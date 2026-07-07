"""FCC Bulletin — audit logging (Phase 3). Additive + flag-gated.

BULLETIN_AUDIT_ENABLED=false (default): audit() is a no-op -> no DB writes, no
behavior change. =true: appends an immutable row to bulletin_audit_log for
collections, exports, deliveries, QA approvals, retries, manual actions, and
failures. Best-effort: audit() NEVER raises and never blocks a request.
"""
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

BULLETIN_AUDIT_ENABLED = os.getenv("BULLETIN_AUDIT_ENABLED", "false").strip().lower() == "true"


async def audit(event_type: str, *, actor: str = "api", entity_type: str = "",
                entity_id: str = "", action: str = "",
                details: Optional[Dict[str, Any]] = None, result: str = "ok") -> None:
    """Record one audit event. No-op unless BULLETIN_AUDIT_ENABLED=true."""
    if not BULLETIN_AUDIT_ENABLED:
        return
    try:
        from app.bulletin_intelligence import bulletin_store
        await bulletin_store.save_audit({
            "id": uuid.uuid4().hex,
            "ts": datetime.utcnow().isoformat(),
            "actor": actor, "event_type": event_type,
            "entity_type": entity_type, "entity_id": str(entity_id or ""),
            "action": action, "details": details or {}, "result": result,
        })
    except Exception:
        pass  # never break a request over audit
