"""One place to write a TEFCA registry audit entry.

The table and its action vocabulary already existed; what was missing was a
single call site. fhir_import.py wrote rows inline, csv_import wrote none, and
nothing recorded a status change or a verification run — so the trail had holes
exactly where a reviewer would look first.

Design notes worth keeping:

* Never raises. An audit write failing must not fail the operation being
  audited; a lost row is recoverable, a 500 on a status change is not. Failures
  are logged so a silent gap is still visible in the application log.
* Does NOT commit. The caller owns the transaction, so the audit row lands in
  the same commit as the change it describes. Committing here would let the two
  diverge if the caller later rolled back.
* Records refusals as well as successes. An attempt to move an entity straight
  from draft to active is precisely the event worth seeing, and a trail of only
  successful transitions hides it.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.tefca_registry import models as reg

logger = logging.getLogger(__name__)

# Vocabulary already documented on the model; repeated here so callers get a
# name rather than a bare string literal at each site.
ENTITY_CREATED = "entity_created"
ENTITY_UPDATED = "entity_updated"
STATUS_CHANGED = "status_changed"
STATUS_CHANGE_REFUSED = "status_change_refused"
VERIFICATION_STARTED = "verification_started"
VERIFICATION_COMPLETED = "verification_completed"
IMPORT_COMPLETED = "import_completed"
NPI_FLAGGED = "npi_flagged"


def record(session,
           action: str,
           entity_id: Optional[Any] = None,
           *,
           actor_id: Optional[Any] = None,
           actor_email: Optional[str] = None,
           ip_address: Optional[str] = None,
           metadata: Optional[dict] = None) -> None:
    """Stage one audit row on `session`. Never raises; never commits."""
    try:
        session.add(reg.TefcaRegAuditLog(
            entity_id=entity_id,
            action=action,
            actor_id=actor_id,
            actor_email=(actor_email or None),
            ip_address=(ip_address or None),
            metadata_=(metadata or {}),
        ))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("TEFCA audit write failed (action=%s entity=%s): %s",
                       action, entity_id, exc)


def actor_of(user) -> tuple:
    """(actor_id, actor_email) from whatever the auth dependency handed us.

    Tolerates None and objects missing either attribute, so an audit call never
    becomes the reason a request fails.
    """
    return getattr(user, "id", None), getattr(user, "email", None)
