# What this device records

A microphone in a room — often a child's or an elderly relative's — is worth
being precise about. This document is meant to be readable by the household,
not only by whoever installed it.

## When audio is captured

The satellite behaves like any Home Assistant Assist device:

- It listens locally for the wake word at all times. That audio never leaves
  the device.
- After the wake word, or while the button is held, audio is streamed to
  Home Assistant and on to the speech recogniser.

The add-on sits in that second path and sees exactly the audio the speech
recogniser already sees. It adds no new listening.

## What is written to disk

| Situation | What is kept |
| --- | --- |
| Ordinary spoken command | Nothing, by default. `assist_recording_retention_seconds` can keep the WAV for debugging; set it back to `0` afterwards |
| Voice note | The recording is transcoded to Opus, queued, and the WAV deleted. The Opus file is deleted `settled_retention_days` after it was sent |
| Received reply | Stored as WAV until played, then deleted after `settled_retention_days` |

Everything lives on the Home Assistant host under the add-on's `/data`. It
is included in Home Assistant backups — a backup of this add-on contains
voice recordings and the contact list.

## What leaves the house

- Audio goes to whichever speech recogniser the pipeline uses. If that is
  the local Whisper add-on, it does not leave the house. If it is a cloud
  service, it does.
- A voice note goes to one messaging service, to one enrolled contact.
- Nothing else. The add-on makes no other outbound connections.

## Who can be sent to

Only contacts enrolled in the add-on. There is no default recipient: with
nothing selected the box refuses to record. An NFC tag that is not assigned
to anyone refuses too.

Inbound audio is filtered by the same list. A voice message from an address
nobody is enrolled under is discarded without being queued or played.

## Turning the microphone down

If the box is for a child, consider disabling the wake word on that
satellite entirely. Voice note mode does not need it: the button drives the
voice assistant directly. Remove the `micro_wake_word` block from the
satellite configuration and the microphone only opens while the button is
held.

That is the strongest privacy setting this design allows, and it costs
nothing except the ability to say "Hey Jarvis" in that room.
