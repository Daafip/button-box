"""Wyoming ASR service that proxies to the real speech recogniser.

Home Assistant's pipeline talks to this service instead of Whisper directly.
Every utterance is forwarded upstream chunk by chunk as it arrives -- never
buffered first -- so normal Assist keeps the latency it had before, and the
same chunks are written to a WAV on the way past.

When the mode flag is armed, the utterance is queued as a voice note and a
canned transcript is returned in place of the real one. Otherwise the real
transcript is relayed untouched and Assist behaves exactly as it always did.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections import deque
from dataclasses import dataclass, field

from voicenote_box.config import Config
from voicenote_box.mode import Mode, ModeStore
from voicenote_box.sink import NoteSink
from voicenote_box.wyoming_protocol import (
    AUDIO_CHUNK,
    AUDIO_START,
    AUDIO_STOP,
    INFO,
    TRANSCRIPT,
    Event,
    audio_format,
    read_event,
    write_event,
)


log = logging.getLogger(__name__)

RECORDING = "recording"
IDLE = "idle"

# How long a transcript waits for its utterance to finish being queued.
# Transcoding a minute of speech takes about a second; this is only here so
# a lost audio-stop cannot hang the connection forever.
OUTCOME_TIMEOUT = 30


@dataclass
class _Utterance:
    """One utterance and the answer that must go back for it."""

    mode: Mode
    recording: object
    outcome: asyncio.Future = field(repr=False)


class _Session:
    """One Home Assistant connection and its paired upstream connection.

    A connection can carry more than one utterance, so each keeps its own
    outcome and they are answered in order. Sharing a single slot would let
    one utterance consume another's answer -- and a voice note whose answer
    went missing would have its real transcript relayed to Assist.
    """

    def __init__(self, proxy: "AsrProxy") -> None:
        self.proxy = proxy
        self.recording: _Utterance | None = None
        self.awaiting: deque[_Utterance] = deque()

    async def from_client(self, event: Event, upstream: asyncio.StreamWriter) -> None:
        if event.type == AUDIO_START:
            self._begin(event)
        elif event.type == AUDIO_CHUNK and self.recording is not None and event.payload:
            self.proxy.sink.write(self.recording.recording, event.payload)
        elif event.type == AUDIO_STOP:
            self._end()
        await write_event(upstream, event)

    async def from_upstream(self, event: Event, client: asyncio.StreamWriter) -> None:
        if event.type == INFO:
            event = self.proxy.label_info(event)
        elif event.type == TRANSCRIPT:
            replacement = await self._replacement_transcript()
            if replacement is not None:
                event = Event(TRANSCRIPT, {**event.data, "text": replacement})
        await write_event(client, event)

    async def _replacement_transcript(self) -> str | None:
        """The text to return instead of the recogniser's, or None to relay it.

        A voice note never relays the real transcript. Doing so would hand
        Assist whatever the speaker said as a command to execute, which is
        the opposite of what someone recording a private message expects --
        so every failure here still answers with the spoken refusal.
        """
        if not self.awaiting:
            return None
        utterance = self.awaiting.popleft()
        refusal = (
            self.proxy.config.failed_sentence if utterance.mode.is_voicenote else None
        )
        try:
            outcome = await asyncio.wait_for(
                asyncio.shield(utterance.outcome), timeout=OUTCOME_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.error("timed out preparing the utterance")
            return refusal
        except Exception:
            log.exception("preparing the utterance failed")
            return refusal
        return outcome.transcript

    def _begin(self, event: Event) -> None:
        # A second audio-start without a stop means the previous recording
        # will never be finished; drop it rather than leaking its open file.
        self._abandon()
        # Latching here, at the first sample rather than at the transcript, is
        # what limits a stale flag to capturing at most one utterance.
        mode = self.proxy.modes.consume()
        rate, width, channels = audio_format(event.data)
        self.recording = _Utterance(
            mode=mode,
            recording=self.proxy.sink.start(mode, rate, width, channels),
            outcome=asyncio.get_running_loop().create_future(),
        )
        if mode.is_voicenote:
            self.proxy.report(RECORDING)

    def _end(self) -> None:
        utterance, self.recording = self.recording, None
        if utterance is None:
            return
        if utterance.mode.is_voicenote:
            self.proxy.report(IDLE)
        self.awaiting.append(utterance)
        # Transcoding and queueing run off the event loop so the connection
        # stays responsive while the note is prepared.
        task = asyncio.create_task(
            asyncio.to_thread(
                self.proxy.sink.complete, utterance.recording, utterance.mode
            )
        )
        task.add_done_callback(lambda finished: self._settle(utterance, finished))

    @staticmethod
    def _settle(utterance: _Utterance, task: asyncio.Task) -> None:
        """Hand this task's result to the utterance it belongs to."""
        if utterance.outcome.done():
            return
        if task.cancelled():
            utterance.outcome.cancel()
            return
        error = task.exception()
        if error is not None:
            utterance.outcome.set_exception(error)
        else:
            utterance.outcome.set_result(task.result())

    def _abandon(self) -> None:
        if self.recording is None:
            return
        self.proxy.sink.abandon(self.recording.recording)
        self.recording.outcome.cancel()
        self.recording = None
        self.proxy.report(IDLE)

    def cleanup(self) -> None:
        """Drop anything whose connection died before it was answered."""
        self._abandon()
        for utterance in self.awaiting:
            if not utterance.outcome.done():
                utterance.outcome.cancel()
        self.awaiting.clear()


async def _close(*writers: asyncio.StreamWriter) -> None:
    for writer in writers:
        writer.close()
    await asyncio.gather(
        *(writer.wait_closed() for writer in writers), return_exceptions=True
    )


class AsrProxy:
    def __init__(self, config: Config, sink: NoteSink, modes: ModeStore, status=None) -> None:
        self.config = config
        self.sink = sink
        self.modes = modes
        self._status = status

    def report(self, state: str) -> None:
        if self._status is None:
            return
        try:
            self._status(state)
        except Exception:  # a status display must never break the pipeline
            log.exception("status callback failed")

    def label_info(self, event: Event) -> Event:
        """Name the proxy in the upstream service description.

        The models stay exactly as the real recogniser reported them; only the
        service label changes, so the Wyoming integration does not show two
        indistinguishable speech-to-text entries.
        """
        data = copy.deepcopy(event.data)
        for service in data.get("asr") or []:
            if isinstance(service, dict):
                service["name"] = f"{self.config.device_name} ({service.get('name', 'asr')})"
        return Event(INFO, data)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.config.upstream_stt_host, self.config.upstream_stt_port
            )
        except OSError as error:
            log.error("speech recogniser unreachable: %s", error)
            await _close(writer)
            return

        session = _Session(self)
        relay = asyncio.create_task(self._relay(session, upstream_reader, writer))
        try:
            while True:
                event = await read_event(reader)
                if event is None:
                    break
                await session.from_client(event, upstream_writer)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception:
            log.exception("proxy connection failed")
        finally:
            relay.cancel()
            session.cleanup()
            await _close(upstream_writer, writer)

    async def _relay(
        self, session: _Session, upstream: asyncio.StreamReader, client: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                event = await read_event(upstream)
                if event is None:
                    break
                await session.from_upstream(event, client)
        except (asyncio.CancelledError, asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception:
            log.exception("upstream relay failed")

    async def serve(self) -> asyncio.Server:
        server = await asyncio.start_server(self.handle, "0.0.0.0", self.config.wyoming_port)
        log.info(
            "wyoming asr proxy on :%s -> %s:%s",
            self.config.wyoming_port,
            self.config.upstream_stt_host,
            self.config.upstream_stt_port,
        )
        return server
