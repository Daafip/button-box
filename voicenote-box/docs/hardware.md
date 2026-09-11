# Hardware

## The satellite

Voice note mode needs two ESPHome API actions on the satellite
(`start_voicenote`, `stop_voicenote`). That means the satellite's firmware
has to be yours to change.

| Satellite | Verdict |
| --- | --- |
| **DIY ESP32-S3** | Best fit. You already own the YAML; merging the overlay is a few lines |
| **M5Stack Atom Echo** | Fine as a trigger and microphone. Its speaker is too tinny for voice notes — set `media_player_entity` to a real speaker for playback |
| **Home Assistant Voice PE** | Needs a fork of `home-assistant-voice-pe`, maintained across upstream releases. See below |

### The Voice PE question

Adding the actions to a Voice PE means forking its firmware repository and
rebasing that fork every time upstream releases. That is a standing
maintenance cost for two API actions.

The escape hatch is to leave the Voice PE untouched and dedicate a cheap
second satellite to the box. An ESP32-S3 with an I2S microphone costs less
than the hours the fork will take, and the Voice PE keeps getting updates.

**Decide this before flashing anything.** It is the one choice in this
project that is expensive to reverse.

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
