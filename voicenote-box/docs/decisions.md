# Where this departs from the plan

The plan is in [`../../voicenote-box-plan.md`](../../voicenote-box-plan.md).
Four things were built differently, and one of its open questions was
answered in the code rather than left for later.

## The Wyoming protocol is implemented here, not imported

The plan says "Python + `wyoming` lib". The wire format is one JSON line,
optional extra JSON, optional binary payload — about seventy lines in
[`wyoming_protocol.py`](../voicenote_box/wyoming_protocol.py).

Implementing it means the proxy can be tested over real sockets against a
fake recogniser with no container, no Whisper and no third-party package,
which is what `tests/test_wyoming_proxy.py` does. The repository's guidance
is to prefer the standard library and not add dependencies without a
concrete need.

The cost is protocol drift if upstream adds event types. The format has been
stable and is append-only, and unknown events are relayed untouched.

## Transports are synchronous

The plan sketches `async def send_voice`. They are plain HTTP calls, so they
use `urllib` and run on the courier's worker thread. Making them async would
mean adding `aiohttp` to send one multipart POST.

## The add-on files sit at `voicenote-box/`, not `voicenote-box/addon/`

Docker cannot `COPY` from outside its build context, and the Supervisor
builds an add-on with the add-on's own directory as the context. A
`Dockerfile` in `addon/` therefore cannot reach `../voicenote_box/`.

Putting `config.yaml`, `Dockerfile`, `build.yaml` and `run.sh` at the top of
this directory makes it a directory you can copy straight into `/addons/`
with no staging step.

## The add-on owns the recipient select, and plays notes itself

The plan's automation reads `input_select.box_recipient` and passes the
recipient with the arm request. The add-on publishes a `select` entity over
MQTT discovery instead, with options generated from the allowlist plus a
`geen` option.

That means the list cannot drift out of step with who is actually enrolled,
and fail-closed routing lives in one place. Passing an explicit recipient
still works — `POST /api/mode` accepts one, and falls back to the selection.

For the same reason the add-on calls `assist_satellite.announce` itself
through the Supervisor's core API proxy, rather than returning a URL for an
automation to announce. A note is marked read only once Home Assistant
accepted the announcement, so a satellite that is offline leaves the ring
pulsing.

## Open question 3 was answered by building it the better way

The plan asks whether the extra hop slows normal Assist, and says to stream
to Whisper concurrently rather than after buffering *if* it turns out to be
noticeable. The proxy forwards every chunk upstream as it arrives and writes
the WAV on the way past, so there is nothing to change if it is.
`tests/test_wyoming_proxy.py` asserts the recogniser has the audio before
the utterance ends. The wall-clock measurement is still
[a hardware task](verification.md).

## Open questions left open

- **Voice PE fork vs a dedicated second satellite** (1) — a hardware and
  maintenance decision, discussed in [hardware.md](hardware.md).
- **Whether the API audio stream survives a long recording** (2) — only
  measurable on the board.
- **Transcoding location** (4) — ffmpeg runs in the add-on. Home Assistant's
  ffmpeg integration would add a round trip for no benefit, since the audio
  is already in the add-on.
- **Inbound webhook exposure** (5) — not needed. Telegram and Signal both
  poll, and WhatsApp Cloud inbound is not implemented.
