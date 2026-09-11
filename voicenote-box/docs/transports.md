# Transports

One adapter interface, four implementations. Swapping services is an add-on
option; nothing else in the box changes.

```python
class Transport(Protocol):
    name: str
    def send_voice(self, address: str, ogg_path: Path) -> str: ...
    def poll(self, cursor: str | None) -> tuple[list[IncomingMessage], str | None]: ...
    def download(self, message: IncomingMessage, destination: Path) -> Path: ...
```

Adapters are synchronous and run on the courier's worker thread. They raise
`TransportError` and nothing else; `permanent=True` means the service will
never accept this request, and the queue stops retrying instead of hammering
it forever.

## Telegram — the v1 choice

A voice note is one `sendVoice` call. Inbound arrives through `getUpdates`
long polling, so no public webhook and no port forwarding. Groups are free,
there is no verification, and no phone number is tied to the box.

Addresses are numeric chat ids, negative for groups. Get one by messaging
the bot and reading `getUpdates`, or with `@userinfobot`.

The bot token appears in every request URL, so the adapter scrubs it from
any error before it reaches a log line or the queue's stored error.

## Signal — the good second

Talks to a local [`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api)
container, which holds a session for a dedicated number linked as a device.
Attachments go out base64-encoded in the send body.

Needs a number the box can own. Do not link a personal one.

`signal-cli` deletes envelopes as it hands them over, so the server holds the
read position and the local cursor goes unused. One consequence is worth
knowing: if downloading an attachment fails after the envelope was handed
over, that reply is gone — there is nothing to rewind to. The poller
reports it and carries on with the rest of the batch rather than dropping
those too. Telegram rewinds its offset and recovers on the next poll; wacli
re-lists recent messages and so recovers as well.

## WhatsApp Cloud API — optional, and probably not what you want

Included because the question always comes up. It is the official API, and
it is the wrong shape for a box that sends spontaneously:

- Free-form audio is only accepted inside a **24-hour window** that the
  *recipient* opens by messaging the business number. A note sent outside
  that window is rejected. The adapter reports Meta's code 131047 as a
  permanent failure with that explanation rather than retrying.
- Message templates — the only way to reach someone outside the window —
  cannot carry a voice note as payload.
- It requires Meta business verification, an Official Business Account for
  groups, and a **public HTTPS webhook** for inbound. That is more infra
  than the unofficial clients, not less. `poll()` returns nothing, so
  inbound does not work at all without that webhook.
- The business number cannot also be used in the normal WhatsApp app.
- Templates are priced per message.

It becomes viable only for a reply-driven flow: grandma messages first, the
box replies within 24 hours. Keep it as an adapter, not a foundation.

## wacli — real WhatsApp, unofficially

The closest thing to the original Button Box, and what the Raspberry Pi
build in [`messagebox/`](../../messagebox/) uses. It shells out to a local
`wacli` (whatsmeow) binary holding a linked session.

**This is an unofficial client.** Meta can ban the number, and it breaks
whenever the protocol changes. Give the box a dedicated SIM or eSIM and
never link a personal number.

Addresses are WhatsApp JIDs: `31612345678@s.whatsapp.net` for a person,
`120363123456789@g.us` for a group. A plain phone number is rejected.

`wacli` has no cursor, so the poller lists recent messages every cycle and
the queue's de-duplication on message id is what stops a reply playing
twice. Errors report which subcommand failed and nothing about whom: wacli's
output carries chat identifiers and message ids.

## Adding an adapter

1. Implement the three methods in `transports/<name>.py`.
2. Register it in `build()` in `transports/__init__.py`.
3. Add its address pattern to `_clean_address` in `contacts.py` — an
   address that does not validate cannot be enrolled, which is the point.
4. Add its options to `config.py` and `config.yaml`.
5. Test it the way `tests/test_transports.py` tests Telegram: assert the
   request shape and that credentials never reach an error message.
