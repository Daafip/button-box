"""The ingress page: contacts, queue, and current state.

Server-rendered plain HTML. It is reached only through Home Assistant's
ingress, which has already authenticated the user, and it is the one place an
operator can see why a send failed without reading the add-on log.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from voicenote_box.app import Application
from voicenote_box.errors import VoicenoteError


STYLE = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem; max-width: 52rem; }
h1 { font-size: 1.3rem; margin: 0 0 1rem; }
h2 { font-size: 1rem; margin: 2rem 0 .5rem; text-transform: uppercase;
     letter-spacing: .06em; opacity: .7; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8884; }
th { font-weight: 600; opacity: .7; font-size: .85rem; }
form { display: flex; flex-wrap: wrap; gap: .5rem; align-items: flex-end; margin: .5rem 0; }
label { display: flex; flex-direction: column; font-size: .8rem; opacity: .8; gap: .2rem; }
input, select, button { font: inherit; padding: .35rem .5rem; }
.state { display: flex; flex-wrap: wrap; gap: 1.5rem; padding: .8rem 0; }
.state div { min-width: 7rem; }
.state span { display: block; font-size: .75rem; text-transform: uppercase;
              letter-spacing: .06em; opacity: .6; }
.state strong { font-size: 1.2rem; font-weight: 600; }
.notice { padding: .6rem .8rem; border-radius: .4rem; background: #2e7d3222; }
.error { background: #c6282822; }
.muted { opacity: .6; }
"""


def _escape(value) -> str:
    return html.escape("" if value is None else str(value))


def _moment(timestamp: float | None) -> str:
    if not timestamp:
        return "-"
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d-%m %H:%M")


def _state_panel(state: dict) -> str:
    cells = [
        ("Status", state["activity"]),
        ("Ongelezen", state["unread"]),
        ("Laatste afzender", state["last_sender"] or "-"),
        ("Ontvanger", state["recipient_label"] or "geen"),
        ("Wachtrij", state["outbox_pending"]),
        ("Mislukt", state["outbox_failed"]),
    ]
    body = "".join(
        f"<div><span>{_escape(name)}</span><strong>{_escape(value)}</strong></div>"
        for name, value in cells
    )
    return f'<div class="state">{body}</div>'


def _recipients(application: Application) -> dict:
    """Load the allowlist, or nothing if the store cannot be read.

    A broken store must not make this page unrenderable: it is where an
    operator goes to see what is wrong.
    """
    try:
        return application.contacts.load()
    except VoicenoteError:
        return {}


def _store_problem(application: Application) -> str:
    try:
        application.contacts.load()
    except VoicenoteError as error:
        return (
            f'<p class="notice error">De contactenlijst kan niet worden gelezen:'
            f" {_escape(error)}. De box weigert op te nemen en te versturen"
            " totdat dit is hersteld.</p>"
        )
    return ""


def _recipients_table(application: Application) -> str:
    recipients = _recipients(application)
    if not recipients:
        return (
            '<p class="muted">Nog geen contacten. Zonder contact weigert de box'
            " op te nemen.</p>"
        )
    rows = "".join(
        "<tr>"
        f"<td>{_escape(recipient.label)}</td>"
        f"<td><code>{_escape(recipient.id)}</code></td>"
        f"<td>{_escape(recipient.transport)}</td>"
        f"<td>{_escape(', '.join(recipient.tags) or '-')}</td>"
        f'<td><form method="post" action="ui/recipients/{_escape(recipient.id)}/delete">'
        '<button type="submit">Verwijderen</button></form></td>'
        "</tr>"
        for recipient in sorted(recipients.values(), key=lambda item: item.label)
    )
    return (
        "<table><tr><th>Naam</th><th>Id</th><th>Dienst</th><th>Tags</th><th></th></tr>"
        f"{rows}</table>"
    )


def _recipient_options(application: Application) -> str:
    return "".join(
        f'<option value="{_escape(recipient.id)}">{_escape(recipient.label)}</option>'
        for recipient in sorted(_recipients(application).values(), key=lambda i: i.label)
    )


def _duration(seconds) -> str:
    return f"{seconds:.0f}s" if seconds else "-"


def _inbound_table(application: Application) -> str:
    messages = application.queue.recent_inbound()
    if not messages:
        return '<p class="muted">Nog niets ontvangen.</p>'
    rows = "".join(
        "<tr>"
        f"<td>{_moment(message.received_at)}</td>"
        f"<td>{_escape(message.sender_label)}</td>"
        f"<td>{_duration(message.seconds)}</td>"
        f"<td>{'gelezen' if message.read_at else '<b>nieuw</b>'}</td>"
        "</tr>"
        for message in messages
    )
    return f"<table><tr><th>Tijd</th><th>Van</th><th>Duur</th><th></th></tr>{rows}</table>"


def _outbound_table(application: Application) -> str:
    messages = [message for message in application.queue.recent_outbound() if message]
    if not messages:
        return '<p class="muted">Nog niets verstuurd.</p>'
    rows = "".join(
        "<tr>"
        f"<td>{_moment(message.created_at)}</td>"
        f"<td>{_escape(message.recipient_id)}</td>"
        f"<td>{message.attempts}</td>"
        f"<td>{_escape(message.last_error or '-')}</td>"
        "</tr>"
        for message in messages
    )
    return (
        "<table><tr><th>Tijd</th><th>Naar</th><th>Pogingen</th><th>Laatste fout</th></tr>"
        f"{rows}</table>"
    )


def _clear_failed_form(state: dict) -> str:
    """Offer the way out of the block that failed notes put the box in."""
    if not state["outbox_failed"]:
        return ""
    return (
        '<p class="notice error">'
        f'{state["outbox_failed"]} bericht(en) zijn niet verzonden. '
        "Zolang die er zijn, weigert de box nieuwe opnames."
        "</p>"
        '<form method="post" action="ui/failed/clear">'
        '<button type="submit">Mislukte berichten wissen</button></form>'
    )


def page(application: Application, ingress_path: str = "", notice: str = "",
         error: str = "") -> str:
    state = application.state()
    banner = ""
    if error:
        banner = f'<p class="notice error">{_escape(error)}</p>'
    elif notice:
        banner = f'<p class="notice">{_escape(notice)}</p>'
    base = (ingress_path.rstrip("/") + "/") if ingress_path else ""
    return f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Note Box</title><base href="{_escape(base)}"><style>{STYLE}</style></head>
<body>
<h1>Voice Note Box</h1>
{_store_problem(application)}
{banner}
{_state_panel(state)}

<h2>Ontvanger kiezen</h2>
<form method="post" action="ui/select">
  <label>Contact<select name="recipient">
    <option value="">— geen —</option>{_recipient_options(application)}
  </select></label>
  <button type="submit">Instellen</button>
</form>

<h2>Contacten</h2>
{_recipients_table(application)}
<form method="post" action="ui/recipients">
  <label>Id<input name="id" pattern="[a-z0-9][a-z0-9_-]*" required></label>
  <label>Naam<input name="label" required></label>
  <label>Dienst<select name="transport">
    <option value="telegram">telegram</option>
    <option value="signal">signal</option>
    <option value="wacli">wacli</option>
    <option value="whatsapp_cloud">whatsapp_cloud</option>
  </select></label>
  <label>Adres<input name="address" required></label>
  <button type="submit">Toevoegen</button>
</form>

<h2>NFC-tag koppelen</h2>
<form method="post" action="ui/tags">
  <label>Tag-id<input name="tag" required></label>
  <label>Contact<select name="recipient">{_recipient_options(application)}</select></label>
  <button type="submit">Koppelen</button>
</form>

<h2>Ontvangen</h2>
{_inbound_table(application)}

<h2>Verzonden</h2>
{_outbound_table(application)}
{_clear_failed_form(state)}
</body></html>
"""
