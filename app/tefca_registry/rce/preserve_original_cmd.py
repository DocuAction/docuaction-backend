"""Governed restore of a delivery's preserved original (DEF-004).

    python -m app.tefca_registry.rce.preserve_original_cmd \
        --intake <uuid> --file <path> --expect-sha <sha256> --actor <email>

WHAT THIS IS FOR
────────────────
A delivery's original file was preserved on the container filesystem, a
redeploy discarded it, and a byte-identical copy exists outside the platform
(verified by hash against the recorded intake). This command puts that copy
into the DURABLE write-once artifact store so the "Original delivery file
unmodified" control verifies again — and does nothing else. It never touches
`rce_source_records`, `rce_curated_records`, dispositions, snapshots or any
processed record; there is no reprocessing and no re-ingestion.

WHY IT NEVER UPDATES THE INTAKE ROW
───────────────────────────────────
Area 1 is immutable: the application role holds no UPDATE on
`rce_source_intakes`, and a restore must not need one. The durable copy is
stored under the CONTENT-ADDRESSED key `delivery-original-<sha256>`, and
`verify_stored_file` finds it from the intake's own recorded sha256 — the
pointer IS the hash the intake has carried since arrival. The only database
write is one append-only `original_preserved` audit event.

FAIL-CLOSED, at every step (RESTORE-PROCEDURE.md):
  1. the local file is read unchanged — no open/resave/normalise;
  2. its SHA-256 must equal BOTH --expect-sha and the intake's recorded sha256
     (and the size must match the recorded size) or nothing is written;
  3. the put is write-once: identical bytes deduplicate, different bytes under
     the key are impossible by construction (the key is the hash);
  4. the stored bytes are read back and re-hashed before anything is recorded;
  5. the preservation control is re-run (deep) and must PASS;
  6. any deviation aborts with the store untouched beyond the idempotent put
     and no audit row committed. A preserved original is never fabricated
     from database rows.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RestoreRefused(RuntimeError):
    """The restore did not happen, and the reason is the message."""


async def restore_preserved_original(
    db, *, intake_id, file_path: str, expect_sha: str, actor: str,
    store=None,
) -> Dict[str, Any]:
    """The CLI's entry point: read a LOCAL file, then run the shared core.

    Kept as its own function — with its own name in every operator runbook —
    but it is now the file-based special case of `restore_preserved_original_bytes`,
    which also backs the admin-endpoint restore (DEF-004 governed original-
    artifact restoration: a pre-staged blob, not a local path). Steps 1-7 of
    RESTORE-PROCEDURE.md; raises RestoreRefused on ANY deviation, leaving the
    intake exactly as it was.
    """
    with open(file_path, "rb") as handle:
        raw = handle.read()
    return await restore_preserved_original_bytes(
        db, intake_id=intake_id, raw=raw, expect_sha=expect_sha, actor=actor,
        provenance="operator restore from verified local copy", store=store)


async def restore_preserved_original_bytes(
    db, *, intake_id, raw: bytes, expect_sha: str, actor: str,
    provenance: str, store=None,
) -> Dict[str, Any]:
    """The shared restore core: bytes in hand, everything after that is
    identical regardless of where the bytes came from (a local file for the
    CLI, a pre-staged private blob for the admin endpoint). Raises
    RestoreRefused on any deviation, leaving the intake exactly as it was.

    IDEMPOTENCY (stricter than the original CLI-only version): the underlying
    store's `put` already deduplicates identical bytes under a key — this
    function additionally skips the audit write whenever that happens, so a
    repeat call writes NOTHING (no second blob write, no second audit row)
    and reports `already_restored: True` instead of quietly appending a
    duplicate record of an action that already happened.
    """
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import repository as repo
    from app.tefca_registry.rce.intake import (durable_artifact_store,
                                               durable_original_key)

    store = store or durable_artifact_store()
    if store is None:
        raise RestoreRefused(
            "No durable artifact backend is configured "
            "(REPORT_ARTIFACT_BACKEND is local). Restoring into the container "
            "filesystem would be discarded by the next redeploy — configure "
            "the durable backend first.")

    intake = await repo.get_intake(db, intake_id)
    if intake is None:
        raise RestoreRefused(f"intake {intake_id} does not exist")

    # 1-2. hash the bytes in hand, compare against BOTH the caller's expected
    # hash and the intake's recorded one.
    local_sha = hashlib.sha256(raw).hexdigest()
    expect = (expect_sha or "").strip().lower()
    if local_sha != expect:
        raise RestoreRefused(
            f"the bytes' SHA-256 ({local_sha[:16]}…) does not equal the "
            f"expected SHA-256 ({expect[:16]}…); nothing was written")
    if local_sha != (intake.sha256 or "").lower():
        raise RestoreRefused(
            f"the bytes' SHA-256 ({local_sha[:16]}…) does not equal the "
            f"intake's recorded sha256 ({(intake.sha256 or '')[:16]}…); "
            f"nothing was written")
    if intake.file_size_bytes is not None and len(raw) != intake.file_size_bytes:
        raise RestoreRefused(
            f"the bytes are {len(raw)} long but the intake recorded "
            f"{intake.file_size_bytes}; nothing was written")

    # 3. write-once put under the content-addressed key. Identical bytes
    # deduplicate (a second run is a no-op), and the store cannot overwrite.
    record = await asyncio.to_thread(
        store.put, durable_original_key(local_sha), raw,
        content_type="text/csv",
        metadata={"kind": "delivery_original",
                  "original_filename": intake.original_filename,
                  "source_sha256": local_sha,
                  "provenance": provenance,
                  "restored_by": actor,
                  "intake_id": str(intake.id)})

    # 4. post-upload verification: the stored bytes, re-read and re-hashed.
    stored_raw = await asyncio.to_thread(store.get, record.locator)
    stored_sha = hashlib.sha256(stored_raw).hexdigest()
    if stored_sha != local_sha:
        raise RestoreRefused(
            f"post-upload verification FAILED: the stored bytes hash to "
            f"{stored_sha[:16]}…, not {local_sha[:16]}…. The intake remains "
            f"marked unavailable; do not trust the stored copy.")

    # 5-6. one append-only audit event, ONLY on a genuinely new write — never
    # on a deduplicated repeat. No intake UPDATE — the durable copy is found
    # through the content-addressed key.
    if not record.deduplicated:
        db.add(reg.TefcaRegAuditLog(
            entity_id=None,
            action="original_preserved",
            actor_email=actor,
            metadata_={
                "intake_id": str(intake.id),
                "original_filename": intake.original_filename,
                "size_bytes": len(raw),
                "sha256": local_sha,
                "storage_backend": store.backend,
                "storage_key": record.key,
                "storage_locator": record.locator,
                "provenance": provenance,
            }))
        await db.commit()

    # 7. rerun ONLY the preservation control, deep, and require PASS.
    verification = await repo.verify_stored_file(db, intake.id, deep=True)
    if not (verification.get("checked") and verification.get("intact")):
        raise RestoreRefused(
            f"the preservation control did not PASS after the restore: "
            f"{json.dumps(verification, default=str)}")

    return {
        "restored": True,
        "already_restored": bool(record.deduplicated),
        "intake_id": str(intake.id),
        "sha256": local_sha,
        "size_bytes": len(raw),
        "storage_backend": store.backend,
        "storage_locator": record.locator,
        "deduplicated": bool(record.deduplicated),
        "verification": verification,
    }


def _parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="preserve_original",
        description="Restore a delivery's preserved original into the durable "
                    "artifact store (DEF-004). Refuses on any hash mismatch.")
    parser.add_argument("--intake", required=True, help="intake UUID")
    parser.add_argument("--file", required=True, help="path to the verified local copy")
    parser.add_argument("--expect-sha", required=True,
                        help="the SHA-256 the file MUST have (from the intake record)")
    parser.add_argument("--actor", required=True,
                        help="the operator performing the restore (goes to the audit event)")
    return parser.parse_args(argv)


async def _amain(argv: Optional[list] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        intake_id = uuid.UUID(args.intake)
    except ValueError:
        print(f"REFUSED: {args.intake!r} is not a UUID", file=sys.stderr)
        return 2

    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        try:
            result = await restore_preserved_original(
                db, intake_id=intake_id, file_path=args.file,
                expect_sha=args.expect_sha, actor=args.actor)
        except RestoreRefused as exc:
            await db.rollback()
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin wrapper over _amain
    raise SystemExit(asyncio.run(_amain()))
