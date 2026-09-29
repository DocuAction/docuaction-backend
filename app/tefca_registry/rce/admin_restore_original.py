"""DEF-005 follow-up — governed DEV-only restore of a delivery's preserved
original via a pre-staged private blob, driven through the RUNNING
application's own DATABASE_URL and managed identity.

WHY THIS EXISTS
────────────────
The governed restore (RESTORE-PROCEDURE.md, `preserve_original_cmd.py`)
needs a live database session. On App Service `DATABASE_URL` is an App
Service Key Vault reference that only resolves INSIDE the running
container — there is no way to run the CLI from an operator's own machine
without materializing that secret (correctly refused as credential
exposure) or SSH-ing into a container image that has no SSH server. This
module is the smallest thing that runs inside the app instead: an
admin-only, DEV-only, feature-flagged HTTP endpoint that takes a delivery
job id and the NAME of a file an operator has already staged into the
platform's own approved private blob container, re-verifies everything
independently, and calls the exact same restore core the CLI uses
(`restore_preserved_original_bytes`) — the CLI's own guarantees (write-once,
post-write re-hash, one audit event, no intake UPDATE) are inherited
unchanged, not reimplemented.

WHAT IT NEVER ACCEPTS
──────────────────────
No database URL. No storage account name, container name, account key or
SAS token. No arbitrary filesystem path. No arbitrary URL. The only "where"
the caller supplies is a blob NAME that must fall under `STAGING_PREFIX`,
inside the SAME already-approved account/container the durable store
already uses (`REPORT_ARTIFACT_AZURE_ACCOUNT` / `_CONTAINER` — this
process's own configuration, never request input).

WHAT IT VERIFIES BEFORE WRITING ANYTHING
──────────────────────────────────────────
1. this deployment is DEV (`settings.is_development`) — checked again here
   even though the HTTP route is only ever REGISTERED under the same
   condition, so this function refuses correctly if it is ever called
   directly (a test, or a future caller);
2. the delivery job resolves to a real intake (identity check);
3. the staged blob name is exactly under the approved prefix, validated
   BEFORE any network call;
4. the staged bytes are downloaded and hashed INDEPENDENTLY here — never
   trusting any length/hash metadata Azure itself reports for the blob —
   and must equal the caller-supplied expected size/hash;
5. the shared restore core then re-checks the bytes against the INTAKE's
   own recorded sha256/size a second time, redundantly, before writing.
Any mismatch at any step raises RestoreRefused and writes nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any, Dict

from app.tefca_registry.rce.preserve_original_cmd import RestoreRefused

#: The ONLY location this endpoint will ever read from. An operator stages a
#: verified file there (see the operator staging command in the restore
#: runbook) using their OWN Azure identity against the SAME already-approved
#: private DEV storage account/container the durable store uses — this
#: module never names, and never accepts, the account or container.
STAGING_PREFIX = "operator-restore-staging/"

_SAFE_STAGED_NAME = re.compile(
    r"^operator-restore-staging/[A-Za-z0-9][A-Za-z0-9._-]{0,190}$")


def validate_staged_blob_name(name: str) -> str:
    """The ONLY gate between a caller-supplied string and a blob read. A name
    that does not match this exact shape never reaches the storage backend —
    no `..`, no leading slash, no other prefix, no characters outside the
    safe set."""
    if not isinstance(name, str) or not _SAFE_STAGED_NAME.match(name):
        raise RestoreRefused(
            f"{name!r} is not a permitted staging blob name; it must start "
            f"with {STAGING_PREFIX!r} and contain only letters, digits, dot, "
            f"underscore or hyphen after that")
    return name


async def admin_restore_original_from_staged_blob(
    db, *, delivery_job, staged_blob_name: str,
    expected_size_bytes: int, expected_sha256: str, actor: str,
) -> Dict[str, Any]:
    """The full governed check-then-restore. `delivery_job` is an already
    fetched, already-existence-checked `RceDeliveryJob` row (the HTTP route
    owns id parsing and the sanitized-404 convention for a malformed or
    missing job — this function is pure business logic on a row it trusts
    exists). Raises RestoreRefused on any deviation; writes nothing until
    every check above has passed."""
    from app.core.config import settings
    from app.tefca_registry.rce.intake import durable_artifact_store
    from app.tefca_registry.rce.preserve_original_cmd import \
        restore_preserved_original_bytes

    # 1. verify DEV environment — see module docstring.
    if not settings.is_development:
        raise RestoreRefused("this operation is only available in a development environment")

    # 2. verify delivery identity: the job must name a real intake.
    intake_id = getattr(delivery_job, "source_intake_id", None)
    if intake_id is None:
        raise RestoreRefused(
            f"delivery job {delivery_job.id} has no recorded intake to restore an original for")

    # 3. restrict the source to the approved prefix — before ANY network call.
    validate_staged_blob_name(staged_blob_name)

    expected_sha256 = (expected_sha256 or "").strip().lower()
    if len(expected_sha256) != 64 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise RestoreRefused("expected_sha256 must be exactly 64 hex characters")
    if not isinstance(expected_size_bytes, int) or expected_size_bytes <= 0:
        raise RestoreRefused("expected_size_bytes must be a positive integer")

    store = durable_artifact_store()
    if store is None:
        raise RestoreRefused(
            "no durable artifact backend is configured "
            "(REPORT_ARTIFACT_BACKEND is local)")

    # 4. download the STAGED bytes — the account/container are the store's
    # own configuration, never caller input; only the relative name is —
    # then verify the source blob hash INDEPENDENTLY, against what the
    # CALLER claimed, before the shared restore core even looks at the
    # intake row.
    staged = await _read_staged(store, staged_blob_name)
    staged_sha = hashlib.sha256(staged).hexdigest()
    if len(staged) != expected_size_bytes:
        raise RestoreRefused(
            f"the staged blob is {len(staged)} bytes, not the expected "
            f"{expected_size_bytes}; nothing was written")
    if staged_sha != expected_sha256:
        raise RestoreRefused(
            f"the staged blob's SHA-256 ({staged_sha[:16]}…) does not equal "
            f"the expected SHA-256 ({expected_sha256[:16]}…); nothing was written")

    result = await restore_preserved_original_bytes(
        db, intake_id=intake_id, raw=staged, expect_sha=expected_sha256,
        actor=actor, provenance="admin endpoint restore from pre-staged DEV blob",
        store=store)
    result["delivery_job_id"] = str(delivery_job.id)
    return result


async def _read_staged(store, blob_name: str) -> bytes:
    from app.core.storage.artifact_store import ArtifactNotFound

    try:
        return await asyncio.to_thread(store.read_staged_blob, blob_name)
    except ArtifactNotFound as exc:
        raise RestoreRefused(f"the staged blob could not be found: {exc}") from exc
