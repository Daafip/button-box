# Setting up the Voice Note Box

Do these in order. Each step is checkable on its own, and a later step will
not work if an earlier one silently failed.

## 1. Install the add-on

The add-on directory is `voicenote-box/` itself.

```bash
# On the Home Assistant host
cp -r voicenote-box /addons/voicenote-box
```

Then **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**, and
install *Voice Note Box* from the Local add-ons section.

To publish it as an add-on repository instead, put a `repository.yaml` at the
root of the git repository, alongside this directory.

## 2. Configure it

The speech-to-text add-on it proxies to must already be installed and
running. The defaults assume the official Whisper add-on.

| Option | Meaning |
| --- | --- |
| `upstream_stt_host` / `_port` | The real speech recogniser. `core-whisper:10300` by default |
| `api_token` | Required. Home Assistant sends it as a bearer token; without it every command is refused |
| `announce_entity` | The satellite that plays replies, e.g. `assist_satellite.box` |
| `media_base_url` | How the satellite reaches the add-on, e.g. `http://homeassistant.local:8099`. Derived from the hostname when blank |
| `transport` | `telegram`, `signal`, `wacli`, or `whatsapp_cloud` |
| `max_recording_seconds` | Hard cap on one note. The satellite and the automation have matching caps |
| `rate_limit_per_hour` | Refuses further sends past this many in an hour. `0` disables it |
| `assist_recording_retention_seconds` | Keep ordinary Assist recordings this long for debugging. `0` discards them at once |

Generate the token with something like `openssl rand -hex 24`.

MQTT credentials are not options: the add-on takes them from the Supervisor's
broker service, so the Mosquitto add-on just needs to be running.

## 3. Point the pipeline at the proxy

**Settings → Devices & services → Add integration → Wyoming Protocol**, with
the add-on's host and port `10400`.

Then **Settings → Voice assistants → your pipeline → Speech-to-text**, and
select the entry named *Voice Note Box (…)*. Leave everything else alone.

> At this point wake word and spoken commands must still work exactly as
> before. If they do not, stop here — nothing later will fix it.

## 4. Enrol a contact

Open the add-on's **Voice notes** panel in the sidebar. Add a recipient with
an id, a name, the transport and its address:

- Telegram: the numeric chat id (negative for groups)
- Signal: the number in E.164, or `group.<id>`
- wacli: a WhatsApp JID, `31612345678@s.whatsapp.net` or `…@g.us`
- WhatsApp Cloud: the number in E.164

Nothing can be recorded until at least one contact exists and one is
selected. That is deliberate.

## 4a. When sending breaks

A note that a service permanently refuses is marked failed, and **while any
failed note is in the outbox the box refuses to record a new one**. That is
deliberate: without it, someone keeps recording messages they believe were
delivered.

The ingress page shows the count and a **Mislukte berichten wissen** button
that discards them and unblocks recording. Ordinary spoken commands keep
working throughout.

## 5. Home Assistant configuration

Copy `homeassistant/` into `config/voicenote/` and the custom sentences into
`config/custom_sentences/nl/`:

```bash
cp -r voicenote-box/homeassistant /config/voicenote
mkdir -p /config/custom_sentences/nl
mv /config/voicenote/sentences/nl/voicenote.yaml /config/custom_sentences/nl/
```

Add to `secrets.yaml`:

```yaml
voicenote_mode_url: "http://homeassistant.local:8099/api/mode"
voicenote_recipient_url: "http://homeassistant.local:8099/api/recipient"
voicenote_play_next_url: "http://homeassistant.local:8099/api/play_next"
voicenote_authorization: "Bearer <the api_token you generated>"
```

Merge the includes from `homeassistant/configuration.yaml` into your own, then
edit `automations.yaml` and replace every entity id marked `CHANGE ME`.

The sentences in `custom_sentences/nl/voicenote.yaml` must match the
`sent_sentence` and `failed_sentence` options exactly. If they drift apart,
Assist answers "sorry, dat begreep ik niet" and the caregiver hears that
instead of a confirmation.

## 6. Flash the nodes

`esphome/button-node.yaml` is a complete configuration; set the pins in
`substitutions` and add the secrets it references.

`esphome/satellite-overlay.yaml` is **not** standalone — merge its two blocks
into your existing satellite configuration. Read
[hardware.md](hardware.md) first if your satellite is a Voice PE.

## 7. Check it

Work through [verification.md](verification.md). It is the plan's phase
acceptance criteria, in order, and none of them have been run yet.

## Where things live

Inside the add-on container, everything persistent is under `/data`:

```
/data/voicenote.db      the outbound and inbound queues
/data/contacts.json     the recipient allowlist
/data/state/mode.json   the mode flag
/data/state/media.key   signs media URLs; delete to invalidate every link
/data/recordings/       WAVs being recorded, and kept Assist audio
/data/outbox/           Opus notes waiting to be sent
/data/inbox/            received replies, transcoded for playback
```
