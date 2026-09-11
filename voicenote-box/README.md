# Voice Note Box

A Button Box–style voice messaging device built on Home Assistant and
ESPHome. Hold an external button, speak, and the recording is delivered to
an approved contact over a messaging service. Replies queue locally, the LED
ring signals them, and a short press plays the next one.

The satellite stays a **normal Assist device**. Wake word, pipeline and
satellite configuration are unchanged; voice note mode exists only for the
seconds a button is held.

This is a separate device from the Raspberry Pi Button Box in
[`messagebox/`](../messagebox/) — different hardware, different runtime, no
shared code.

## How it works

```
[button node]                  [voice satellite]
 ESPHome, arcade button  ──►    ESPHome / Voice PE
 + LED ring                     mic + speaker
       │                               │
       │ binary_sensor                 │ audio (ESPHome API)
       ▼                               ▼
 ┌──────────────────── Home Assistant ────────────────────┐
 │ automation: hold → arm flag → start → stop on release  │
 │ Assist pipeline (single, unchanged)                    │
 └──────────┬───────────────────────────────┬─────────────┘
            │ Wyoming (STT)                 │ assist_satellite.announce
            ▼                               │
 ┌──────────────────────────┐               │
 │  voicenote-box add-on    │───────────────┘
 │  ├ Wyoming ASR proxy     │  serves signed media over HTTP
 │  ├ control API + ingress │  MQTT discovery entities
 │  ├ transport adapters    │
 │  └ message queue (sqlite)│
 └──────────┬───────────────┘
            ▼
     Whisper add-on (real speech to text)
```

The add-on registers as a Wyoming **speech-to-text service** and proxies to
the real one. For every utterance it streams the audio to Whisper as it
arrives and writes the same chunks to a WAV. What happens to that WAV is
decided by a mode flag:

| mode flag | behaviour |
| --- | --- |
| `assist` (default) | WAV discarded, the real transcript is returned |
| `voicenote` | WAV queued for a contact, a canned transcript is returned |

The canned transcript is a registered custom sentence whose intent script
replies "verzonden", so the confirmation reaches the box's own speaker
through the ordinary text-to-speech path with no extra audio plumbing.

**Ordering matters.** The flag is armed over REST *before* the microphone
opens. The proxy latches it at the first audio sample and disarms it
immediately, so a stale flag can capture at most one utterance — and it
expires besides.

## Safety properties

These are the behaviours the tests exist to protect:

- **Fail-closed routing.** An unknown recipient, an unmapped NFC tag or no
  selection at all refuses to record. There is no default contact and no
  fallback anywhere in the path.
- **Normal Assist is never broken.** An unreadable mode flag, an
  unconfigured transport, a missing MQTT broker and a dead messaging service
  all leave wake word and spoken commands working.
- **A broken outbox stops the box.** While a note has permanently failed,
  recording is refused rather than piling up messages nobody receives.
  Spoken commands keep working; the ingress page clears the block.
- **Nothing is lost silently.** A note stays queued until a service accepts
  it; an unplugged router produces a retry. A refusal is spoken aloud, not
  swallowed.
- **A voice note is never executed as a command.** Whatever was spoken into
  a note is never relayed to Assist, regardless of whether the recogniser
  answers before or after the button is released, or fails entirely.
- **Only enrolled contacts.** Inbound audio from an address nobody is
  enrolled under is dropped, and the address is kept out of the log.
- **No secrets in logs.** Bot tokens are stripped from errors before they
  reach a log line or the queue.

## Layout

```
voicenote-box/
├── voicenote_box/          add-on source
│   ├── wyoming_proxy.py    ASR proxy: streams upstream, records on the way past
│   ├── wyoming_protocol.py the Wyoming wire format, on stdlib asyncio
│   ├── sink.py             what happens to a finished utterance
│   ├── mode.py             the one-shot, expiring mode flag
│   ├── contacts.py         fail-closed recipient allowlist
│   ├── selection.py        who the box currently sends to
│   ├── queue.py            durable outbound and inbound queues (sqlite)
│   ├── courier.py          sends queued notes, polls for replies
│   ├── api.py / ui.py      control API, signed media, ingress page
│   ├── mqtt.py             Home Assistant entities via discovery
│   ├── app.py              the operations every front end shares
│   └── transports/         telegram, signal, wacli, whatsapp_cloud
├── esphome/                button node and satellite overlay
├── homeassistant/          automations, rest_command, sentences, intents
├── tests/                  208 tests, no hardware or network needed
├── docs/
├── config.yaml             add-on manifest
├── Dockerfile / run.sh     add-on image
└── build.yaml
```

## Getting started

[docs/setup.md](docs/setup.md) has the install order.
[docs/verification.md](docs/verification.md) is the hardware checklist — the
phase acceptance criteria from the plan, none of which have been run on real
hardware yet.

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/setup.md](docs/setup.md) | Installing and configuring the add-on, Home Assistant and the nodes |
| [docs/hardware.md](docs/hardware.md) | Satellite choice, the Voice PE fork question, wiring |
| [docs/transports.md](docs/transports.md) | Telegram, Signal, and why WhatsApp Cloud does not fit |
| [docs/verification.md](docs/verification.md) | What still has to be proven on real hardware |
| [docs/privacy.md](docs/privacy.md) | What is recorded, when, and where it goes |
| [docs/decisions.md](docs/decisions.md) | Where the implementation departs from the plan, and why |
