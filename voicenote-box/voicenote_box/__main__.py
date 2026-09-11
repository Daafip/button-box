"""Add-on entry point.

Starts, in one process:

* the Wyoming ASR proxy on its own asyncio loop;
* the control HTTP server and the ingress HTTP server, each on a thread;
* one thread that sends queued notes and another that polls for replies;
* a housekeeping thread that deletes audio the box no longer needs.

Any one of them failing is logged and survivable. Losing the proxy is not:
that would break normal Assist on the satellite, so it runs on the main
thread and its exit ends the process.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time

from voicenote_box import api
from voicenote_box.activity import Activity
from voicenote_box.app import Application
from voicenote_box.config import Config
from voicenote_box.contacts import ContactStore
from voicenote_box.courier import Courier
from voicenote_box.errors import VoicenoteError
from voicenote_box.mode import ModeStore
from voicenote_box.mqtt import NO_RECIPIENT, Publisher
from voicenote_box.paths import RECORDINGS_DIR
from voicenote_box.queue import MessageQueue
from voicenote_box.selection import Selection
from voicenote_box.sink import NoteSink
from voicenote_box.transports import build as build_transport
from voicenote_box.wyoming_proxy import RECORDING, AsrProxy


log = logging.getLogger("voicenote_box")

HOUSEKEEPING_INTERVAL = 3600

# Sending is checked on its own short timer. Polling for replies is a long
# poll -- Telegram holds the request open for 25 seconds -- so sharing one
# loop would leave a just-recorded note waiting that long before it went out.
SEND_INTERVAL_SECONDS = 2.0


def sender_loop(courier: Courier, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            courier.send_due()
        except Exception:
            log.exception("send pass failed")
        stop.wait(SEND_INTERVAL_SECONDS)


def poller_loop(courier: Courier, interval: float, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            courier.fetch_inbound()
        except Exception:
            log.exception("poll pass failed")
        stop.wait(interval)


def housekeeping_loop(application: Application, config: Config, stop: threading.Event) -> None:
    retention = config.assist_recording_retention_seconds
    while not stop.is_set():
        try:
            removed = application.queue.purge_settled(config.settled_retention_days * 86400)
            if retention > 0:
                cutoff = time.time() - retention
                for path in RECORDINGS_DIR.glob("*.wav"):
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                        removed += 1
            if removed:
                log.info("housekeeping removed %s items", removed)
        except Exception:
            log.exception("housekeeping failed")
        stop.wait(HOUSEKEEPING_INTERVAL)


def play_next_handler(application: Application):
    """Handle a press of the play button entity."""

    def play() -> None:
        try:
            application.play_next()
        except VoicenoteError as error:
            log.warning("play next refused: %s", error)

    return play


def recipient_chooser(application: Application):
    """Map an option on the select entity back onto the allowlist."""

    def choose(label: str) -> None:
        # "geen" is the select's fail-closed option, not a contact's name,
        # so it clears the selection instead of being looked up and refused.
        wanted = None if label.strip().casefold() == NO_RECIPIENT else label
        try:
            application.select(label=wanted or None)
        except VoicenoteError as error:
            log.warning("recipient select refused: %s", error)

    return choose


def build(config: Config) -> tuple[Application, AsrProxy, Courier | None]:
    queue = MessageQueue()
    contacts = ContactStore()
    selection = Selection(contacts)
    modes = ModeStore()

    publisher = Publisher(config)
    activity = Activity(publish=publisher.publish_activity)
    application = Application(
        config, contacts, selection, modes, queue, activity, publisher=publisher
    )
    publisher.on_play_next = play_next_handler(application)
    publisher.on_recipient = recipient_chooser(application)

    proxy = AsrProxy(
        config,
        NoteSink(config, queue, contacts),
        modes,
        status=lambda state: activity.set_recording(state == RECORDING),
    )

    courier = None
    try:
        courier = Courier(config, queue, contacts, build_transport(config), activity)
    except Exception as error:  # transport missing or misconfigured
        log.error("transport unavailable, sending is disabled: %s", error)

    if publisher.connect():
        application.republish_entities()
    return application, proxy, courier


async def run(config: Config) -> None:
    application, proxy, courier = build(config)
    stop = threading.Event()
    threads = []

    control = api.serve(application, config.api_port, trusted=False)
    ingress = api.serve(application, config.ingress_port, trusted=True)
    for server in (control, ingress):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)

    if courier is not None:
        threads.append(_spawn(sender_loop, courier, stop))
        threads.append(_spawn(poller_loop, courier, config.poll_interval_seconds, stop))
    threads.append(_spawn(housekeeping_loop, application, config, stop))

    server = await proxy.serve()
    loop = asyncio.get_running_loop()
    closing = asyncio.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, closing.set)

    async with server:
        await closing.wait()

    log.info("shutting down")
    stop.set()
    for http_server in (control, ingress):
        http_server.shutdown()
    if application.publisher is not None:
        application.publisher.close()


def _spawn(target, *arguments) -> threading.Thread:
    thread = threading.Thread(target=target, args=arguments, daemon=True)
    thread.start()
    return thread


def main() -> None:
    config = Config.load()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if config.unknown_options:
        log.warning("ignoring unknown options: %s", ", ".join(config.unknown_options))
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
