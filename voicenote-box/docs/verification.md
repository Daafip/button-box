# Hardware verification

**Nothing in this list has been run on real hardware.** The software is
complete and unit-tested (`make check` from the repository root), but every
acceptance criterion below involves a microphone, a speaker, a button or a
messaging service, and none of those can be proven from a test suite.

Work through it in order. The plan's rule stands: do not move on until the
current criterion is physically met.

## Phase 0 — the proxy

- [ ] Wake word plus "zet de lamp aan" still works normally through the
      satellite.
- [ ] With `assist_recording_retention_seconds` set above zero, that
      command's WAV appears in `/data/recordings/` tagged `-assist.wav`.
- [ ] **Measure the added latency.** Time the same command with the pipeline
      pointed at Whisper directly, then at the proxy. The proxy streams
      chunks upstream as they arrive rather than buffering, so the
      difference should be within noise. If it is not, that is a real
      finding — record the numbers.

## Phase 1 — button and capture

- [ ] Holding the button for 8 seconds yields an 8-second WAV tagged
      `-voicenote.wav`.
- [ ] The wake word is suppressed during the recording and works again
      afterwards.
- [ ] A normal spoken command immediately after is still tagged `-assist`.
- [ ] **Does `voice_assistant.start` with `silence_detection: false`
      survive past 30 seconds on your board**, or does the API audio stream
      time out? This is the plan's second open question and it can only be
      answered on hardware. Try 10s, 30s, 60s.
- [ ] The Voice PE fork decision is made and written down.

## Phase 2 — local loopback

- [ ] A recorded note plays back on the box.
- [ ] The spoken "verzonden" confirmation comes out of the box's speaker.
- [ ] The confirmation does not retrigger the wake word.
- [ ] Playback is intelligible on the actual speaker. On an Atom Echo it
      will not be; set `media_player_entity` to a real speaker.

## Phase 3 — outbound

- [ ] A held button produces a playable voice message in the recipient's
      Telegram within a few seconds.
- [ ] **Unplug the router mid-send.** The note must retry, not disappear.
      Plug it back in and confirm it arrives.
- [ ] The ring shows idle / recording / sending / error correctly.
- [ ] Stop Home Assistant. The ring must show the offline state.

## Phase 4 — inbound

- [ ] A reply arrives, the ring pulses, a short press plays it, the counter
      decrements.
- [ ] **Restart Home Assistant with an unread reply queued.** The queue must
      survive.
- [ ] A short press and a hold are reliably distinguished by the person who
      will actually use the box, not by you.
- [ ] Audio sent from a number that is *not* enrolled does not appear.

## Phase 5 — recipients and safety

- [ ] An unmapped NFC tag refuses to record, visibly.
- [ ] Each mapped tag routes to the right contact — check every tag, not one.
- [ ] With no recipient selected, holding the button refuses.
- [ ] Removing a contact while a note of theirs is queued fails that note
      rather than sending it elsewhere.
- [ ] The recording cap actually stops the recording.
- [ ] The rate limit refuses audibly rather than silently dropping.
- [ ] A permanently failed note blocks further recording, the ingress page
      says so, and clearing it unblocks the box.

## Phase 6 — packaging

- [ ] The add-on installs from a clean Home Assistant with no manual steps
      beyond [setup.md](setup.md).
- [ ] The ingress panel shows contacts and both queues.
- [ ] Backup and restore of `/data` preserves contacts and the queue.
- [ ] The Signal adapter works against a real `signal-cli-rest-api`.
- [ ] The wacli adapter works against a real linked session, on a dedicated
      number — never a personal one.

## Things the tests cannot tell you

- Whether the Wyoming service description the proxy relays is accepted by
  every Home Assistant version you care about.
- Whether `assist_satellite.announce` returns when playback finishes on your
  satellite, or returns early.
- Whether the ring is visible in the room it will live in.
- Whether the person the box is for can tell the states apart.
