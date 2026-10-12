"""Track A3 - notification templates and transport interface. INACTIVE.

PROPOSAL. Nothing in this module can send a message on its own:

  * No concrete transport (SMTP, SendGrid, ...) is implemented here. Only the
    interface and a recording `NullTransport` exist.
  * `dispatch()` sends only when ALL hold: ENABLE_NOTIFICATIONS is true,
    NOTIFICATION_TRANSPORT names a transport that has been explicitly
    registered, and the process is not running under pytest.
  * Every other case returns a SUPPRESSED result naming the reason; nothing is
    queued, retried or persisted.

The Task 2 document says B4 notifications use "the agreed template". No agreed
template exists in the repository, so these bodies are DRAFTS for owner
review, with placeholders only (no names, addresses or identifiers).
"""
from __future__ import annotations

import os
import string
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

SUPPRESSED_DISABLED = "SUPPRESSED_FLAG_OFF"
SUPPRESSED_NO_TRANSPORT = "SUPPRESSED_NO_TRANSPORT_CONFIGURED"
SUPPRESSED_UNREGISTERED = "SUPPRESSED_TRANSPORT_NOT_REGISTERED"
SUPPRESSED_UNDER_TEST = "SUPPRESSED_UNDER_TEST"
SENT = "SENT"


@dataclass(frozen=True)
class Template:
    code: str
    subject: str
    body: str
    required: tuple


TEMPLATES: Dict[str, Template] = {
    "B4_ONC_NOTIFICATION": Template(
        "B4_ONC_NOTIFICATION",
        "[DRAFT] B4 non-compliant finding - {entity_ref}",
        "DRAFT TEMPLATE - NOT AN AGREED TEMPLATE.\n"
        "A B4 non-compliant finding was confirmed for {entity_ref}.\n"
        "Confirmed condition: {condition}\n"
        "Terms of Participation provision relied on: {top_provision}\n"
        "Sources checked: {sources}\n",
        ("entity_ref", "condition", "top_provision", "sources")),
    "B3_PARTICIPANT_RESPONSE_REQUEST": Template(
        "B3_PARTICIPANT_RESPONSE_REQUEST",
        "[DRAFT] Response requested - {entity_ref}",
        "DRAFT TEMPLATE. AGT recommends that the HHS/ONC DATA investigate and "
        "respond regarding {entity_ref} by {due_at}.\nSources checked: {sources}\n",
        ("entity_ref", "due_at", "sources")),
    "OUTAGE_ESCALATION_TO_COR": Template(
        "OUTAGE_ESCALATION_TO_COR",
        "[DRAFT] Source unavailable beyond escalation threshold - {source}",
        "DRAFT TEMPLATE. Source {source} has been unavailable since {since}. "
        "Affected entities are held as Indeterminate. Proposed alternative "
        "method: {alternative}.\n",
        ("source", "since", "alternative")),
    "INDETERMINATE_RE_REVIEW_REMINDER": Template(
        "INDETERMINATE_RE_REVIEW_REMINDER",
        "[DRAFT] Re-review due - {entity_ref}",
        "DRAFT TEMPLATE. Source {source} is restored; {entity_ref} is due for "
        "re-review by {due_at}.\n",
        ("entity_ref", "source", "due_at")),
}


class TemplateError(ValueError):
    pass


def render(code: str, values: Dict[str, str]) -> Dict[str, str]:
    t = TEMPLATES.get(code)
    if t is None:
        raise TemplateError(f"unknown template {code!r}")
    missing = [k for k in t.required if k not in values]
    if missing:
        raise TemplateError(f"{code}: missing {missing}")
    fmt = string.Formatter()
    return {"template": code,
            "subject": fmt.vformat(t.subject, (), values),
            "body": fmt.vformat(t.body, (), values)}


class Transport(Protocol):
    name: str

    def send(self, to: List[str], subject: str, body: str) -> None: ...


class NullTransport:
    """Records what it would have sent. Delivers nothing."""
    name = "null"

    def __init__(self) -> None:
        self.recorded: List[Dict[str, object]] = []

    def send(self, to, subject, body) -> None:
        self.recorded.append({"to": list(to), "subject": subject, "body": body})


_REGISTRY: Dict[str, Transport] = {}


def register_transport(transport: Transport) -> None:
    _REGISTRY[transport.name] = transport


def dispatch(rendered: Dict[str, str], to: List[str], *, enabled: Optional[bool] = None,
             transport_name: Optional[str] = None) -> Dict[str, object]:
    """Send only if every gate is open; otherwise report why not."""
    if enabled is None or transport_name is None:
        from app.core.config import settings
        enabled = bool(getattr(settings, "ENABLE_NOTIFICATIONS", False)) \
            if enabled is None else enabled
        transport_name = (getattr(settings, "NOTIFICATION_TRANSPORT", "") or "") \
            if transport_name is None else transport_name
    if not enabled:
        return {"status": SUPPRESSED_DISABLED, "sent": False}
    if not transport_name:
        return {"status": SUPPRESSED_NO_TRANSPORT, "sent": False}
    transport = _REGISTRY.get(transport_name)
    if transport is None:
        return {"status": SUPPRESSED_UNREGISTERED, "sent": False}
    if "PYTEST_CURRENT_TEST" in os.environ:
        return {"status": SUPPRESSED_UNDER_TEST, "sent": False}
    transport.send(to, rendered["subject"], rendered["body"])
    return {"status": SENT, "sent": True}
