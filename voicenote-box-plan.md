# Voice Note Box — implementation plan

A Button Box–style voice messaging device built on Home Assistant + ESPHome,
where the satellite **keeps working as a normal Assist device** and only
switches to "voice note" mode when an external button is held.

---

## 1. Goal and non-goals

**Goal**
Hold an external button → record a voice note on an existing ESPHome voice
satellite → deliver it to an approved contact over a messaging service →
incoming replies queue locally, LED signals them, press to play.

**Non-goals (v1)**
- Screen-free onboarding portal (HA already handles wifi/config)
- Standalone operation without HA
- Multi-tenant / multiple boxes (design for it, don't build it)

**Hard requirement**
Normal wake-word assist on the same satellite must keep working, unchanged,
at all times except while a note is being recorded or played.

---

## 2. Architecture

```
[external button node]           [voice satellite]
  ESPHome, arcade button   ──►     ESPHome / Voice PE
  + LED ring                       mic + speaker
        │                                │
        │ binary_sensor                  │ audio (ESPHome API)
        ▼                                ▼
  ┌──────────────────────── Home Assistant ────────────────────────┐
  │  automation: hold → set mode flag → start_va → stop on release │
  │  Assist pipeline (single, unchanged)                           │
  └───────────────┬───────────────────────────────┬────────────────┘
                  │ Wyoming (STT)                 │ media_player /
                  ▼                               │ assist_satellite.announce
       ┌──────────────────────────┐               │
       │  voicenote-box add-on    │───────────────┘
       │  ├ Wyoming ASR proxy     │  serves wav/ogg over HTTP
       │  ├ REST control API      │  MQTT discovery entities
       │  ├ transport adapter     │
       │  └ message queue (sqlite)│
       └───────────┬──────────────┘
                   │ proxies audio
                   ▼
            Whisper add-on (real STT)
```

### 2.1 Key decision: STT proxy, not fake STT

The add-on registers as a Wyoming **ASR service**. For every utterance it:

1. accumulates the PCM chunks to a wav
2. forwards them to the real STT (Whisper add-on) over Wyoming
3. returns the real transcript to HA

Consequence: one pipeline, one satellite config, assist behaves exactly as
before. The add-on decides what to do with the wav based on a mode flag:

| mode flag | behaviour |
|---|---|
| `assist` (default) | wav discarded after N seconds, transcript returned |
| `voicenote:<recipient>` | wav kept + queued for send, canned transcript returned |

The canned transcript is a registered custom sentence (e.g.
`stuur spraakbericht`) whose intent script replies "verzonden" — so the
confirmation comes back through the normal TTS path onto the box's own
speaker, with no extra audio plumbing.

**Ordering matters:** the flag must be set *before* the mic starts, so the
button automation calls the add-on's REST endpoint first, then starts the
voice assistant.

### 2.2 Triggering from an external button

The satellite exposes two ESPHome API services:

```yaml
api:
  actions:
    - action: start_voicenote
      then:
        - micro_wake_word.stop:            # don't double-trigger
        - voice_assistant.start:
            silence_detection: false       # hold-to-talk, no VAD cutoff
    - action: stop_voicenote
      then:
        - voice_assistant.stop:
        - delay: 500ms
        - micro_wake_word.start:
```

HA automation:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.box_button
    to: "on"
actions:
  - action: rest_command.voicenote_mode      # flag FIRST
    data: { mode: "voicenote", recipient: "{{ states('input_select.box_recipient') }}" }
  - action: esphome.satellite_start_voicenote
  - wait_for_trigger:
      - trigger: state
        entity_id: binary_sensor.box_button
        to: "off"
    timeout: "00:01:00"
  - action: esphome.satellite_stop_voicenote
```

**Firmware caveat:** this needs custom ESPHome YAML on the satellite.
- Atom Echo / DIY ESP32-S3 → trivial, you own the YAML already.
- Voice PE → requires forking `home-assistant-voice-pe` firmware and
  maintaining that fork across upstream updates. Decide in Phase 1.
- Escape hatch if the fork is unwanted: dedicate a cheap second satellite to
  the box and leave Voice PE untouched.

### 2.3 Playback

`assist_satellite.announce` with a `media_id` pointing at the add-on's HTTP
server (or a file copied into `/media/`). Announce handles ducking and
returns when playback finishes. Falls back to `media_player.play_media`.

Atom Echo's speaker is too tinny for voice notes — usable as trigger/mic
only; pair with a real media player for output.

---

## 3. Transport

| Option | Verdict |
|---|---|
| **Telegram** | v1 choice. Native HA integration, bot API, voice notes are one call, inbound via webhook or long-poll, groups free, no verification. |
| **Signal** | Good second. `signal-cli-rest-api` add-on exists; needs a dedicated number + linked device. |
| **Matrix** | Native HA integration, fully self-hostable, weakest UX for non-technical family. |
| **WhatsApp via wacli/whatsmeow** | Closest to original Button Box. Unofficial client → ban risk on the number, breaks on Meta changes. Needs a dedicated SIM/eSIM. |
| **WhatsApp Cloud API** | Official but wrong shape for this use case, see below. |

### Why WhatsApp Business Platform does not simplify this

- Free-form audio can only be sent inside a **24-hour window** opened by the
  recipient messaging the business number. A spontaneous outbound note
  outside that window is rejected.
- Message templates (the only way out of the window) don't carry a voice
  note as payload.
- Requires Meta business verification, an Official Business Account for
  groups, and a **public HTTPS webhook** for inbound — more infra than wacli,
  not less.
- The business number cannot also be used in the normal WhatsApp app.
- Per-message pricing on templates.

It becomes viable only for a reply-driven flow (grandma messages first, box
replies within 24h). Keep it as an optional adapter, not the foundation.

### Adapter interface

```python
class Transport(Protocol):
    async def send_voice(self, recipient_id: str, ogg_path: Path) -> str: ...
    async def poll(self) -> list[IncomingMessage]: ...   # or webhook push
    def contacts(self) -> list[Contact]: ...
```

---

## 4. Phases

Each phase ends in something demonstrable. Do not start the next until the
acceptance criterion is physically met on real hardware.

### Phase 0 — Wyoming proxy skeleton
- Add-on container, Python + `wyoming` lib, ASR service on :10400
- Proxies to Whisper add-on, writes every utterance to `/data/recordings/`
- REST endpoint `POST /mode` sets the flag
- Registered in HA via Wyoming Protocol integration, selected in the pipeline

**Done when:** wake word + "zet de lamp aan" still works normally, and the
wav of that command appears on disk.

### Phase 1 — Button and capture path
- External ESPHome node: arcade button + LED, `binary_sensor` with hold
- ESPHome API actions on the satellite (`start_voicenote`/`stop_voicenote`)
- HA automation wiring flag → start → release → stop
- Decide the Voice PE fork question here

**Done when:** holding the button for 8 seconds yields an 8-second wav
tagged `voicenote`, the wake word is suppressed during it and works again
after, and a normal assist command is still tagged `assist`.

### Phase 2 — Local loopback
- Transcode PCM → ogg/opus (ffmpeg in the add-on)
- Serve files over HTTP from the add-on
- Play the last note back via `assist_satellite.announce`
- Custom sentence + intent script for the spoken confirmation

**Done when:** record, hear it played back on the box, with a spoken
"verzonden" confirmation. No messaging involved yet.

### Phase 3 — Outbound
- Telegram adapter, single hardcoded recipient from add-on config
- Retry queue for when the network or HA is down
- LED states: idle / recording / sending / error

**Done when:** a held button produces a playable voice message in the
recipient's Telegram within a few seconds, and an unplugged router produces
a retry rather than a silent loss.

### Phase 4 — Inbound queue
- Poll or webhook → download audio → transcode → sqlite queue
- MQTT discovery entities: `sensor.voicenotes_unread`,
  `button.voicenotes_play_next`, `sensor.voicenotes_last_sender`
- LED pulse while unread > 0; short press plays next, marks read
- Short press vs hold disambiguation on the button node

**Done when:** a reply arrives, the LED pulses, a short press plays it, the
counter decrements, and a reboot of HA does not lose the queue.

### Phase 5 — Recipients and safety
- Recipient selection: NFC tags (`tag_scanned`) or a rotary/second button
- **Fail-closed allowlist**: unknown tag or unset recipient blocks sending
  rather than falling back to a default
- Max recording length, rate limit, no send while queue is degraded
- Optional: disable wake word entirely on the box satellite if it's a kid
  device

**Done when:** an unmapped tag refuses to record, and each mapped tag routes
to the right contact.

### Phase 6 — Packaging
- Proper add-on repository: `config.yaml`, options schema, ingress UI for
  contacts + queue inspection
- Signal and wacli adapters behind the transport interface
- README with the pipeline setup and ESPHome snippets
- Backup/restore of contacts + auth state

---

## 5. Repo layout

```
voicenote-box/
├── voicenote_box/              # add-on source
│   ├── wyoming_proxy.py        # ASR proxy + recording sink
│   ├── api.py                  # REST control + media serving
│   ├── queue.py                # sqlite queue
│   ├── mqtt.py                 # discovery entities
│   └── transports/
│       ├── base.py
│       ├── telegram.py
│       ├── signal.py
│       └── whatsapp_cloud.py   # optional, see §3
├── addon/                      # config.yaml, Dockerfile, run.sh
├── esphome/
│   ├── button-node.yaml
│   └── satellite-overlay.yaml  # actions to merge into satellite config
├── homeassistant/              # example automations, rest_command, sentences
└── docs/
```

---

## 6. Open questions

1. Voice PE firmware fork vs dedicated second satellite — resolve in Phase 1.
2. Does `voice_assistant.start` with `silence_detection: false` reliably run
   past 30s on the target board, or does the API audio stream time out?
3. Wyoming proxy latency: does the extra hop measurably slow normal assist?
   Measure in Phase 0; if noticeable, stream to Whisper concurrently rather
   than after buffering.
4. Transcoding location: add-on ffmpeg vs HA's `ffmpeg` integration.
5. Inbound webhook exposure if WhatsApp Cloud API is ever added — Nabu Casa
   cloudhook vs Cloudflare tunnel.

---

## 7. Risks

- **Firmware fork maintenance** on Voice PE across upstream releases.
- **Unofficial WhatsApp clients** get numbers banned; never use a personal
  number.
- **Always-on mic in a kid's room** — document clearly what is recorded,
  when, and where it goes; consider disabling the wake word on that device.
- **HA dependency**: unlike the original Button Box, this dies when HA is
  down. Acceptable, but the LED should show it rather than fail silently.
