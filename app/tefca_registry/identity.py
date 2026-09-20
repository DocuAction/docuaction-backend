"""Principal resolution for review surfaces (QA-040, QA-061, QA-027).

Every review table stores the immutable principal id (a UUID) and nothing
else — correct for the record, useless on a screen. Supervisor Operations,
QHIN Assignment and the case drawers showed those UUIDs as the primary label.
This module resolves ids to the account facts a human can act on (email,
display name, role) in ONE query per surface and keeps the id beside them.

Nothing here grants anything: it reads `users` and returns facts. An id that
does not resolve (deleted account, service principal, fixture) is returned
with `resolved: false` and the id as its only label — never invented.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select


def _unresolved(user_id: Optional[str]) -> Dict[str, Any]:
    return {"user_id": user_id, "email": None, "display_name": None, "role": None,
            "label": user_id, "resolved": False}


async def resolve_principals(db, ids: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """`{user_id: {user_id, email, display_name, role, label, resolved}}`."""
    from app.models.database import User

    wanted = sorted({str(i) for i in ids if i})
    out: Dict[str, Dict[str, Any]] = {i: _unresolved(i) for i in wanted}
    if not wanted:
        return out
    import uuid as _uuid

    keys = []
    for i in wanted:
        try:
            keys.append(_uuid.UUID(i))
        except ValueError:
            continue
    if not keys:
        return out
    rows = (await db.execute(
        select(User.id, User.email, User.full_name, User.role).where(User.id.in_(keys))
    )).all()
    for user_id, email, full_name, role in rows:
        out[str(user_id)] = {
            "user_id": str(user_id), "email": email,
            "display_name": (full_name or "").strip() or None, "role": role,
            "label": email or (full_name or "").strip() or str(user_id),
            "resolved": True,
        }
    return out


def principal(resolved: Dict[str, Dict[str, Any]], user_id: Any) -> Optional[Dict[str, Any]]:
    """One principal from a `resolve_principals` map; None for no id."""
    if not user_id:
        return None
    return resolved.get(str(user_id)) or _unresolved(str(user_id))


def actor_facts(*, actor_email: Optional[str] = None, actor_id: Any = None,
                service: Optional[str] = None) -> Dict[str, Any]:
    """Actor CLASS beside the actor (QA-027): a human principal, an executing
    service (`host:pid` worker id) or the system, stated separately."""
    if service:
        return {"actor_class": "service", "executing_service": service,
                "human_initiator": actor_email, "actor_id": (str(actor_id) if actor_id else None)}
    if actor_email or actor_id:
        return {"actor_class": "human", "executing_service": None,
                "human_initiator": actor_email, "actor_id": (str(actor_id) if actor_id else None)}
    return {"actor_class": "system", "executing_service": None,
            "human_initiator": None, "actor_id": None}
