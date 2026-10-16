# Voice Note Box

**A fork of [Button Box](https://github.com/button-box/button-box) that runs
on Home Assistant instead of a Raspberry Pi.**

Hold a button, speak, and the voice note goes to an approved contact.
Replies wait on the box: an LED ring shows that one is waiting, and a short
press plays it. The satellite that records stays an ordinary Assist device,
so wake word and spoken commands keep working throughout.

> **Status:** the add-on is written and covered by 208 unit tests, but none
> of it has run on real hardware yet. The [test plan](#test-plan) below is
> the work that is left.

## What this fork adds

Upstream Button Box is a stand-alone Raspberry Pi with a USB microphone and
speaker that talks WhatsApp through `wacli`. This fork adds a second build of
the same idea:

| | Upstream Pi build | This fork's add-on |
| --- | --- | --- |
| Code | [`messagebox/`](messagebox/) | [`voicenote-box/`](voicenote-box/) |
| Runs on | Raspberry Pi 4B or Zero 2 W | A Home Assistant add-on |
| Microphone and speaker | USB, on the Pi | An ESPHome voice satellite |
| Button and light | GPIO on the Pi | A separate ESPHome node with an LED ring |
| Messaging | WhatsApp via `wacli` | Telegram first; Signal, `wacli` and WhatsApp Cloud adapters exist |
| When Home Assistant is down | Keeps working | Does nothing, and the ring says so |

The two share no code. `messagebox/` and the folders around it are
upstream's Raspberry Pi build, unchanged and merged in from upstream. Its
build instructions are in the
[upstream README](https://github.com/button-box/button-box#readme).

## How it works

The add-on sits between Home Assistant and Whisper as a Wyoming
speech-to-text proxy. Every utterance streams through to Whisper as usual.
When the button is held, Home Assistant first arms voice note mode over
REST, then opens the satellite's microphone. The proxy keeps that one
recording, queues it for the selected contact, and returns a fixed sentence
that makes Assist answer "Verzonden".

The diagram, the safety properties and the code layout are in the
[add-on README](voicenote-box/README.md). The original plan, with its
phases, is in [voicenote-box-plan.md](voicenote-box-plan.md).

## What you need

### Home Assistant

- Home Assistant OS or Supervised. Add-ons need the Supervisor.
- The Whisper, Piper, Mosquitto broker and ESPHome add-ons, and the MQTT
  integration.
- A Dutch Assist pipeline that already works on the satellite. The custom
  sentences are in Dutch.

### Hardware

[hardware.md](voicenote-box/docs/hardware.md) has the details.

- A voice satellite whose ESPHome firmware you build yourself. A DIY
  ESP32-S3 fits best. An M5Stack Atom Echo works as microphone and trigger,
  with a separate speaker for playback. A Voice PE is not supported.
- A button node: a plain ESP32, an arcade button between GPIO16 and ground,
  and a 12-LED WS2812 ring on GPIO5.
- For Phase 5: a few NFC tags, and the Home Assistant Companion app on a
  phone to scan them.

### Telegram account

- A Telegram account on your phone to receive notes and reply. A second
  account, or a friend's, for the not-enrolled and second-contact checks.

## Test plan

Work from top to bottom and tick the boxes as you go. The plan's rule
stands: do not start a phase until the one before it passes. The same
acceptance criteria, with more background, are in
[verification.md](voicenote-box/docs/verification.md).
[setup.md](voicenote-box/docs/setup.md) has the details of every install
step.

### Before you start

- [ ] `make check` passes on your machine (see [Development](#development)).
- [ ] The satellite works with your normal pipeline on Whisper: wake word
      plus "zet de lamp aan" switches a light. Without this baseline you
      cannot tell later failures apart.
- [ ] Mosquitto is running and the MQTT integration is set up.

### Telegram bot

- [ ] Create a bot: message **@BotFather** and send `/newbot`. Treat the
      token like a password; it is the bot's only credential.
- [ ] On your phone, open the bot and press **Start**. A bot cannot message
      anyone who has not started it. The first note would fail permanently
      and the box would block recording.
- [ ] Send the bot any message, open
      `https://api.telegram.org/bot<token>/getUpdates` in a browser, and note
      `message.chat.id`. That number is the address you enrol. Do this
      **before** the add-on runs with this token: once it is polling, your
      request competes with it for the same updates. The URL contains the
      token, so do not share or screenshot it.

### Install the add-on

- [ ] Copy `voicenote-box/` to `/addons/voicenote-box` on the Home Assistant
      host. Then go to **Settings → Add-ons → Add-on Store → ⋮ → Check for
      updates** and install *Voice Note Box* from Local add-ons.
- [ ] Set the options: `api_token` (generate one with
      `openssl rand -hex 24`), `transport: telegram`, `telegram_token`, and
      `announce_entity` (your satellite's `assist_satellite.…`). Set
      `media_player_entity` if the satellite's own speaker is too poor, as
      on an Atom Echo. For Phases 0 and 1, set
      `assist_recording_retention_seconds: 300`.
- [ ] Start the add-on. The log shows it running without errors, and the
      bot token appears nowhere in it.

### Phase 0 — the proxy leaves Assist alone

- [ ] Add the **Wyoming Protocol** integration with the Home Assistant
      host's address and port `10400`. In your pipeline, set Speech-to-text
      to *Voice Note Box (…)* and change nothing else.
- [ ] Wake word plus "zet de lamp aan" still works exactly as before. If it
      does not, stop here.
- [ ] The command's WAV is in `/data/recordings/`, ending in `-assist.wav`.
      The add-on's `/data` is not visible in the file editor. From the
      Advanced SSH & Web Terminal add-on, with protection mode off, run
      `docker exec addon_local_voicenote_box ls /data/recordings`.
- [ ] Latency: run the same command five times with the pipeline on Whisper
      directly, and five times through the proxy. Compare the
      speech-to-text times in the pipeline's debug view (**Settings → Voice
      assistants → your pipeline → ⋮ → Debug**). Write the numbers down. A
      clear difference is a finding.

### Phase 1 — button and recording

- [ ] Flash `voicenote-box/esphome/button-node.yaml`. Set the pins in
      `substitutions`, add `voicenote_button_api_key`,
      `voicenote_button_ota_password`, `wifi_ssid` and `wifi_password` to
      your ESPHome secrets, and adopt the node in Home Assistant.
- [ ] Merge the two blocks from `voicenote-box/esphome/satellite-overlay.yaml`
      into your satellite's configuration and reflash it.
- [ ] Install the Home Assistant side as in
      [setup.md §5](voicenote-box/docs/setup.md#5-home-assistant-configuration).
      That means the files in `config/voicenote/`, the sentences in
      `config/custom_sentences/nl/`, the four `voicenote_*` secrets, the
      includes, and every entity id marked `CHANGE ME`. Then restart Home
      Assistant.
- [ ] In the **Voice notes** sidebar panel, enrol yourself with transport
      `telegram` and your chat id as the address. Select yourself as the
      recipient.
- [ ] Hold the button for 8 seconds. The add-on log says
      `queued voice note … (8.0s)`, give or take a little. Telegram is
      already configured, so the note is also sent; that is checked in
      Phase 3.
- [ ] The wake word does not fire during the recording, and works again
      right after.
- [ ] A spoken command straight afterwards still works, and leaves an
      `-assist.wav` rather than a voice note.
- [ ] A tap of under half a second does **not** start a recording.
- [ ] Hold for 10, 30 and 60 seconds. Check that each log line shows the
      full length. On some boards the audio stream may cut out on long
      holds (the plan's open question 2). 60 seconds is the
      `max_recording_seconds` cap.
- [ ] Set `assist_recording_retention_seconds` back to `0`. It keeps
      everything anyone says to the satellite.

### Phase 2 — the spoken confirmation

This build does not play a recorded note back locally. The first playback
check is the Telegram reply in Phase 4.

- [ ] After you release the button, the box says "Verzonden naar …". If it
      says "sorry, dat begreep ik niet", the custom sentence and the
      `sent_sentence` option do not match.
- [ ] The confirmation does not trigger the wake word.

### Phase 3 — sending over Telegram

- [ ] Hold, speak and release. Within a few seconds a playable voice message
      from the bot appears in your Telegram chat.
- [ ] The ring goes from solid red (recording) to a fast orange pulse
      (sending), then back to dim white (idle).
- [ ] Unplug the router while a note is sending. The note stays in the
      *Outbox* sensor instead of disappearing, and arrives once the
      network is back.
- [ ] Stop Home Assistant. The ring changes to a slow red pulse. Start Home
      Assistant again, and the ring returns to idle.

### Phase 4 — replies

- [ ] Reply to the bot from your phone with a voice message. The ring
      pulses blue and *Unread voice notes* shows 1.
- [ ] A short press plays the reply on the box, or on
      `media_player_entity`, and you can understand it. The counter
      returns to 0.
- [ ] With a reply waiting, restart Home Assistant, and separately restart
      the add-on. The reply is still there and still plays.
- [ ] A voice message from a Telegram account that is not enrolled never
      shows up.
- [ ] Let the person the box is for tap and hold a few times. Tap (play)
      and hold (record) must be reliably different for them, not just for
      you.

### Phase 5 — recipients and safety

- [ ] Enrol a second contact. To use a group: add the bot to the group, and
      in @BotFather send `/setprivacy` and choose **Disable**. Otherwise
      the bot only sees replies to its own messages. Stop the add-on while
      you read the group's chat id from `getUpdates`. Group ids are
      negative.
- [ ] Scan each NFC tag with the Companion app, and find its id under
      **Settings → Tags**. Link it to a contact in the Voice notes panel.
      Scanning each mapped tag selects the right contact. Check every tag,
      not one.
- [ ] Scan a mapped tag, then an unmapped one, then hold the button. The
      ring flashes red, and the box must refuse to record. It must not send
      to the first tag's contact.
- [ ] With no recipient selected (`geen`), holding the button flashes the
      ring red and records nothing.
- [ ] Remove a contact while a note to them is still queued. That note
      fails; it is not sent to anyone else.
- [ ] Hold past `max_recording_seconds`. The recording stops at the cap.
- [ ] Set `rate_limit_per_hour` low, for example `2`, and send past it. The
      box refuses out loud instead of silently dropping the note.
- [ ] Enrol a contact with a chat id that never started the bot, and send
      to it. The note fails permanently. Recording is then blocked, and
      the panel says so. **Mislukte berichten wissen** unblocks it.

### Phase 6 — packaging

- [ ] On a clean Home Assistant, the add-on installs with nothing beyond
      [setup.md](voicenote-box/docs/setup.md).
- [ ] The Voice notes panel shows the contacts and both queues.
- [ ] A backup and restore of the add-on keeps the contacts and the queue.
- [ ] Optional, only if you move off Telegram: the Signal adapter works
      against a real `signal-cli-rest-api`. The `wacli` adapter works
      against a real linked session on a dedicated number, never a personal
      one. `wacli` still needs a sync process and a binary in the image;
      see [transports.md](voicenote-box/docs/transports.md).

## Development

```sh
make check
```

This runs upstream's suite and the add-on's 208 tests, which need no
hardware or network. It needs Python 3.13, `uv` and `bun`, the same as CI.
To run only the add-on's tests:

```sh
cd voicenote-box && python3 -m unittest discover -s tests
```

If your umask is `0002`, 21 of upstream's updater tests fail, because the
updater refuses group-writable files. Running `chmod -R g-w .` once in the
checkout fixes it. Git does not track those bits, so it creates no diff.

## Keeping up with upstream

```sh
git remote add upstream https://github.com/button-box/button-box.git   # once per clone
git fetch upstream
git merge upstream/main
```

Upstream edits its README often, but this README belongs to the fork.
Resolve a conflict here with `git checkout --ours README.md`. The other
files both sides touch are `Makefile`, `.github/workflows/ci.yml` and
`AGENTS.md`. Resolve those by keeping both sides' lines.

## Repository map

- `voicenote-box/`: the Home Assistant add-on, with its own
  [README](voicenote-box/README.md), docs, ESPHome and Home Assistant files,
  and tests
- `voicenote-box-plan.md`: the original plan, phases and open questions
- `messagebox/`, `scripts/`, `systemd/`, `config/`, `hardware/`, `sounds/`,
  `docs/`, `tests/`: upstream's Raspberry Pi build
- `Makefile`, `.github/`: the checks for both

## License and acknowledgements

MIT, see [LICENSE](LICENSE). This is a fork of
[button-box/button-box](https://github.com/button-box/button-box), and the
Raspberry Pi build is its contributors' work. Third-party hardware, software
and audio remain subject to their own licenses and terms.

This project is not affiliated with or endorsed by Telegram, WhatsApp, Meta,
or Home Assistant.
