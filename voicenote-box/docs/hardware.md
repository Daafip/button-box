# Hardware

## The satellite

Voice note mode needs two ESPHome API actions on the satellite
(`start_voicenote`, `stop_voicenote`). That means the satellite's firmware
has to be yours to change.

| Satellite | Verdict |
| --- | --- |
| **DIY ESP32-S3** | Best fit. You already own the YAML; merging the overlay is a few lines |
| **M5Stack Atom Echo** | Fine as a trigger and microphone. Its speaker is too tinny for voice notes — set `media_player_entity` to a real speaker for playback |
| **Home Assistant Voice PE** | Not supported. See below |

### Why not a Voice PE

A Voice PE cannot be given these actions without forking
`home-assistant-voice-pe` and rebasing that fork on every upstream release —
a standing maintenance cost for two API actions.

The box does not need one. A dedicated ESP32-S3 satellite with an I2S
microphone costs less than the hours the fork would take, and nothing else
in this project depends on the Voice PE hardware.

If you already own a Voice PE, leave it alone as your normal Assist device
and give the box its own satellite. That is the supported arrangement.

## The button node

`esphome/button-node.yaml` targets a plain ESP32 with:

- an arcade button to a GPIO and ground, using the internal pull-up
  (`button_pin`, default `GPIO16`)
- a WS2812 LED ring on `ring_pin` (default `GPIO5`), 12 LEDs

Both are substitutions at the top of the file. For a plain lamp instead of
an addressable ring, replace the `light:` block with a `monochromatic`
platform on a PWM output and drop the colour arguments from the
`show_state` script.

### Why short and long press are separated on the node

The node decides, not Home Assistant. A tap plays the next reply; a hold
records. Doing that over the network means a slow Wi-Fi moment turns a tap
into a recording, and the box starts listening when someone meant to listen
to a message.

### The ring

One script owns the ring so the states cannot fight:

| State | Ring |
| --- | --- |
| recording | solid red |
| sending | fast orange pulse |
| error | slow dim red pulse |
| unread replies | slow blue pulse |
| idle | dim white |
| Home Assistant unreachable | slow red pulse |

That last one matters: unlike the Raspberry Pi Button Box, this device does
nothing when Home Assistant is down. The ring says so rather than accepting
presses that go nowhere.

## Recording length

Three caps, deliberately: the add-on's `max_recording_seconds` truncates the
file, the automation's `wait_for_trigger` timeout releases the button, and
the satellite overlay stops the voice assistant after 120 seconds if no stop
ever arrives. Any one of them failing still leaves the box recoverable.

Whether the API audio stream survives a recording that long on your board is
one of the things [verification.md](verification.md) asks you to measure.
