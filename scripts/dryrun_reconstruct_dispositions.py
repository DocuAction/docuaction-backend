"""Propose record-level dispositions for a delivery processed BEFORE the
disposition ledger existed - read-only by default, and honest about what is
evidence and what is inference.

WHY THIS IS A SCRIPT AND NOT A MIGRATION OR A BACKGROUND JOB
────────────────────────────────────────────────────────────
Deliveries processed before 2026-09-17 have no rows in
`rce_disposition_events`: what happened to each received line was computed in
memory and discarded. Some of it can be derived afterwards from rows that were
persisted (Area 1 parse status, Area 2 record status and canonical entity,
registry entity creation times, entity versions). Some of it cannot (the
reason text, the material fields that differed, whether a hold came from a
quality issue or an identifier conflict).

Writing derived rows into an append-only evidence table is a records decision,
not schema work. So it does not happen in a migration, unattended. It happens
here, once per delivery, by a named person with an approval reference, after
the dry run has been read - and every row it writes is marked
`reconstructed = true` with a `reconstruction` payload that names the method,
the evidence, the operator, the approval and the confidence, so a report can
never present a reconstructed disposition as one the pipeline recorded.

CLASSIFICATION OF EVERY PROPOSED FIELD
──────────────────────────────────────
    directly_evidenced          a persisted column states the fact
                                (parse_status, record_status, is_test_record,
                                canonical_entity_id)
    deterministically_recomputed  derived by a fixed rule from persisted values
                                (no OID or no name -> MISSING_KEY; entity
                                created inside the job window -> CREATED)
    inferred                    the best-supported reading of indirect evidence
                                (a version row in the window -> UPDATED; no
                                version row -> MATCHED_UNCHANGED; hold reason)
    unavailable                 nothing persisted supports a value (free-text
                                reason; changed_fields when no version exists;
                                a record whose curated row is missing)

SAFETY
──────
  * `--dry-run` is the default. The connection is opened read-only and the
    transaction is `SET TRANSACTION READ ONLY`, so even a defect in this file
    cannot write.
  * `--write` refuses unless `--approved-by` AND `--approval-ref` are given.
  * `--write` refuses against any host that is not local (a shared DEV/PROD
    server) unless `--allow-shared` is also given.
  * `--write` refuses while any proposal is `unavailable`: a ledger that closes
    the equation only by leaving records out is not a reconciliation.
  * Nothing is ever updated or deleted. Reconstruction APPENDS events and ONE
    snapshot with trigger RECONSTRUCTION.

USAGE
─────
    # 1. Read only. Prints JSON: totals, the equation, classifications,
    #    limitations and every proposal.
    python scripts/dryrun_reconstruct_dispositions.py --intake-id <uuid>

    # 2. Write (never run in an unattended session). All three flags required;
    #    --allow-shared additionally required off localhost.
    python scripts/dryrun_reconstruct_dispositions.py --intake-id <uuid> \
        --write --approved-by "Name, Role" --approval-ref TICKET-123

Exit codes: 0 success, 1 refused (precondition failed), 2 error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

METHOD_VERSION = "1.0.0"

DISPOSITIONS = ("CREATED", "UPDATED", "MATCHED_UNCHANGED", "HELD", "REJECTED",
                "MISSING_KEY", "EXCLUDED")

DIRECT = "directly_evidenced"
DETERMINISTIC = "deterministically_recomputed"
INFERRED = "inferred"
UNAVAILABLE = "unavailable"
CLASSIFICATIONS = (DIRECT, DETERMINISTIC, INFERRED, UNAVAILABLE)

REASON = {
    "CREATED": "CREATED_NEW_ENTITY", "UPDATED": "UPDATED_MATERIAL_FIELDS",
    "MATCHED_UNCHANGED": "MATCHED_NO_MATERIAL_CHANGE",
    "HELD_QUALITY": "HELD_QUALITY_ISSUE", "HELD_CONFLICT": "HELD_IDENTIFIER_CONFLICT",
    "HELD_SCHEMA": "HELD_SCHEMA_DRIFT", "REJECTED": "REJECTED_UNPARSEABLE",
    "MISSING_KEY": "MISSING_KEY_NO_OID_OR_NAME", "EXCLUDED": "EXCLUDED_TEST_RECORD",
}

#: Hosts that are NOT a shared environment. Anything else needs --allow-shared.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

#: When no job window exists, an entity created within this many seconds of the
#: curated row's promotion time is read as created BY this delivery (inferred).
PROMOTION_TOLERANCE_SECONDS = 120


# ═══ pure classifier (no database) ═══════════════════════════════════════════

def is_shared_host(database_url: str) -> bool:
    """True unless the URL points at a local server."""
    host = (urlsplit(database_url).hostname or "").lower()
    return host not in LOCAL_HOSTS


def equation(counts: Dict[str, int], received: int) -> Dict[str, Any]:
    """Mirror of app.tefca_registry.rce.dispositions.equation (kept local so the
    tool has no application import at run time)."""
    accounted = sum(int(counts.get(d, 0)) for d in DISPOSITIONS)
    return {"received": int(received), "accounted": accounted,
            **{d.lower(): int(counts.get(d, 0)) for d in DISPOSITIONS},
            "holds": accounted == int(received), "difference": int(received) - accounted}


def _in_window(ts, window: Optional[Tuple[Any, Any]]) -> Optional[bool]:
    if ts is None or window is None or window[0] is None:
        return None
    start, end = window
    if end is None:
        return ts >= start
    return start <= ts <= end


def propose(source: Dict[str, Any], curated: Optional[Dict[str, Any]],
            entity: Optional[Dict[str, Any]], versions: List[Dict[str, Any]],
            job_window: Optional[Tuple[Any, Any]] = None,
            profile_excludes_test_records: bool = True) -> Dict[str, Any]:
    """One proposed disposition for one received line, every field classified.

    `source`   rce_source_records: parse_status, promotion_status, canonical_entity_id
    `curated`  rce_curated_records or None: record_status, canonical_entity_id,
               rce_org_oid, name, is_test_record, status_reason, promoted_at
    `entity`   tefca_reg_entities or None: id, created_at
    `versions` tefca_entity_versions rows for that entity (created_at, snapshot_data)
    `job_window` (started_at, completed_at) of the job, or None
    """
    fields: Dict[str, str] = {}
    basis: List[str] = []
    out: Dict[str, Any] = {
        "source_record_id": str(source.get("id")), "line_number": source.get("line_number"),
        "disposition": None, "reason_code": None, "reason": None, "entity_id": None,
        "changed_fields": [], "curated_record_id": str(curated["id"]) if curated else None,
        "confidence": "none", "fields": fields, "basis": basis,
    }
    # the free-text reason was never persisted for these records
    fields["reason"] = UNAVAILABLE
    fields["changed_fields"] = UNAVAILABLE

    def decide(disposition, reason_key, disp_class, reason_class, confidence, why):
        out["disposition"] = disposition
        out["reason_code"] = REASON[reason_key]
        fields["disposition"] = disp_class
        fields["reason_code"] = reason_class
        out["confidence"] = confidence
        basis.append(why)

    parse_status = (source.get("parse_status") or "").lower()
    if parse_status and parse_status != "ok":
        decide("REJECTED", "REJECTED", DIRECT, DETERMINISTIC, "high",
               f"rce_source_records.parse_status={parse_status!r} states the line did not parse")
        fields["entity_id"] = DIRECT
        return out

    if curated is None:
        fields["disposition"] = UNAVAILABLE
        fields["reason_code"] = UNAVAILABLE
        fields["entity_id"] = UNAVAILABLE
        basis.append("no rce_curated_records row exists for this source record: curation "
                     "never ran for it, or the row was never written")
        return out

    status = (curated.get("record_status") or "").upper()
    if status == "REJECTED":
        decide("REJECTED", "REJECTED", DIRECT, DETERMINISTIC, "high",
               "rce_curated_records.record_status=REJECTED")
        fields["entity_id"] = DIRECT
        return out

    if status == "HELD":
        reason_text = (curated.get("status_reason") or "").lower()
        if "conflict" in reason_text or "npi-008" in reason_text or "existing_value" in reason_text:
            key = "HELD_CONFLICT"
        elif "schema" in reason_text or "drift" in reason_text:
            key = "HELD_SCHEMA"
        else:
            key = "HELD_QUALITY"
        decide("HELD", key, DIRECT, INFERRED, "medium",
               "rce_curated_records.record_status=HELD; the hold reason is read from "
               "status_reason text, which is not a coded field")
        fields["entity_id"] = DIRECT
        return out

    if curated.get("is_test_record") and profile_excludes_test_records:
        decide("EXCLUDED", "EXCLUDED", DIRECT, DETERMINISTIC, "high",
               "rce_curated_records.is_test_record=true and the profile excludes test records")
        fields["entity_id"] = DIRECT
        return out

    if not (curated.get("rce_org_oid") or "").strip() or not (curated.get("name") or "").strip():
        decide("MISSING_KEY", "MISSING_KEY", DETERMINISTIC, DETERMINISTIC, "high",
               "rce_curated_records has no rce_org_oid or no name")
        fields["entity_id"] = DIRECT
        return out

    entity_id = curated.get("canonical_entity_id") or source.get("canonical_entity_id")
    if not entity_id:
        fields["disposition"] = UNAVAILABLE
        fields["reason_code"] = UNAVAILABLE
        fields["entity_id"] = UNAVAILABLE
        basis.append(f"curated row is {status or 'unlabelled'} but carries no canonical_entity_id "
                     f"(promotion_status={source.get('promotion_status')!r}): promotion did "
                     f"not reach this record and no disposition can be evidenced")
        return out

    out["entity_id"] = str(entity_id)
    fields["entity_id"] = DIRECT
    if entity is None:
        fields["disposition"] = UNAVAILABLE
        fields["reason_code"] = UNAVAILABLE
        basis.append("canonical_entity_id names an entity that no longer exists")
        return out

    created_at = entity.get("created_at")
    inside = _in_window(created_at, job_window)
    if inside is None:
        # no job window: fall back on the curated promotion time
        promoted_at = curated.get("promoted_at")
        if created_at is not None and promoted_at is not None:
            near = abs((created_at - promoted_at).total_seconds()) <= PROMOTION_TOLERANCE_SECONDS
            if near:
                decide("CREATED", "CREATED", INFERRED, INFERRED, "low",
                       f"no job window; entity created within {PROMOTION_TOLERANCE_SECONDS}s "
                       f"of the curated row's promoted_at")
                return out
            return _updated_or_unchanged(out, fields, basis, versions,
                                         (promoted_at - timedelta(seconds=PROMOTION_TOLERANCE_SECONDS),
                                          promoted_at + timedelta(seconds=PROMOTION_TOLERANCE_SECONDS)),
                                         "no job window; entity predates promotion")
        fields["disposition"] = UNAVAILABLE
        fields["reason_code"] = UNAVAILABLE
        basis.append("no job window and no promotion time: CREATED cannot be told from a match")
        return out
    if inside:
        decide("CREATED", "CREATED", DETERMINISTIC, DETERMINISTIC, "high",
               "tefca_reg_entities.created_at falls inside the job window")
        return out
    return _updated_or_unchanged(out, fields, basis, versions, job_window,
                                 "entity predates the job window")


def _updated_or_unchanged(out, fields, basis, versions, window, prefix):
    in_window = [v for v in versions or []
                 if _in_window(v.get("created_at"), window)]
    if in_window:
        out["disposition"] = "UPDATED"
        out["reason_code"] = REASON["UPDATED"]
        fields["disposition"] = INFERRED
        fields["reason_code"] = INFERRED
        keys = sorted({k for v in in_window for k in (v.get("snapshot_data") or {}).keys()
                       if isinstance(v.get("snapshot_data"), dict)})
        if keys:
            out["changed_fields"] = keys
            fields["changed_fields"] = INFERRED
        out["confidence"] = "medium"
        basis.append(f"{prefix}; {len(in_window)} tefca_entity_versions row(s) inside the window")
        return out
    out["disposition"] = "MATCHED_UNCHANGED"
    out["reason_code"] = REASON["MATCHED_UNCHANGED"]
    fields["disposition"] = INFERRED
    fields["reason_code"] = INFERRED
    out["confidence"] = "low"
    basis.append(f"{prefix}; no entity version inside the window (absence of evidence "
                 f"read as no material change)")
    return out


def summarise(proposals: List[Dict[str, Any]], received: int) -> Dict[str, Any]:
    counts = {d: 0 for d in DISPOSITIONS}
    unavailable = 0
    by_class: Dict[str, Dict[str, int]] = {}
    for p in proposals:
        if p["disposition"]:
            counts[p["disposition"]] += 1
        else:
            unavailable += 1
        for field, cls in p["fields"].items():
            by_class.setdefault(field, {c: 0 for c in CLASSIFICATIONS})[cls] += 1
    limitations = [
        "reason text is never reconstructed: it was not persisted for these records",
        "HELD reason codes are inferred from free-text status_reason, not from a coded column",
        "UPDATED versus MATCHED_UNCHANGED is inferred from the presence of an entity version "
        "inside the window; a version written by another process in the same window would be "
        "misattributed",
        "identifier conflicts raised before the decision ledger existed are not recoverable",
    ]
    if unavailable:
        limitations.append(f"{unavailable} record(s) have no evidenced disposition; the equation "
                           f"cannot close and --write is refused")
    return {"totals": {**counts, "total": sum(counts.values()), "unavailable": unavailable},
            "equation": equation(counts, received), "classification_summary": by_class,
            "limitations": limitations}


# ═══ database reads ══════════════════════════════════════════════════════════

def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def analyse(conn, intake_id: str, job_id: Optional[str] = None) -> Dict[str, Any]:
    """Read every row the classifier needs and propose. READS ONLY."""
    from sqlalchemy import text

    intake = conn.execute(text(
        "select id, delivery_label, original_filename, record_count from rce_source_intakes "
        "where id = cast(:i as uuid)"), {"i": intake_id}).mappings().first()
    if intake is None:
        return {"intake_id": intake_id, "found": False, "records": 0, "proposals": [],
                **summarise([], 0)}
    if job_id:
        job = conn.execute(text(
            "select id, started_at, completed_at, source_intake_id from rce_delivery_jobs "
            "where id = cast(:j as uuid)"), {"j": job_id}).mappings().first()
        if job is None or str(job["source_intake_id"]) != str(intake["id"]):
            raise SystemExit(f"REFUSED: job {job_id} does not belong to intake {intake_id}")
    else:
        jobs = conn.execute(text(
            "select id, started_at, completed_at from rce_delivery_jobs "
            "where source_intake_id = cast(:i as uuid) order by created_at desc"),
            {"i": intake_id}).mappings().all()
        if len(jobs) > 1:
            raise SystemExit(f"REFUSED: intake {intake_id} has {len(jobs)} jobs; pass --job-id")
        job = jobs[0] if jobs else None
    window = (job["started_at"], job["completed_at"]) if job and job["started_at"] else None

    sources = conn.execute(text(
        "select id, line_number, parse_status, promotion_status, canonical_entity_id "
        "from rce_source_records where source_intake_id = cast(:i as uuid) order by line_number"),
        {"i": intake_id}).mappings().all()
    curated_rows = conn.execute(text(
        "select id, source_record_id, record_status, canonical_entity_id, rce_org_oid, name, "
        "is_test_record, status_reason, promoted_at from rce_curated_records "
        "where source_intake_id = cast(:i as uuid)"), {"i": intake_id}).mappings().all()
    curated = {str(c["source_record_id"]): dict(c) for c in curated_rows}
    entity_ids = sorted({str(c["canonical_entity_id"]) for c in curated_rows if c["canonical_entity_id"]})
    entities: Dict[str, Dict[str, Any]] = {}
    versions: Dict[str, List[Dict[str, Any]]] = {}
    if entity_ids:
        for e in conn.execute(text(
                "select id, created_at from tefca_reg_entities where id = any(cast(:ids as uuid[]))"),
                {"ids": entity_ids}).mappings().all():
            entities[str(e["id"])] = dict(e)
        for v in conn.execute(text(
                "select entity_id, created_at, snapshot_data from tefca_entity_versions "
                "where entity_id = any(cast(:ids as uuid[])) order by created_at"),
                {"ids": entity_ids}).mappings().all():
            versions.setdefault(str(v["entity_id"]), []).append(dict(v))
    existing_events = conn.execute(text(
        "select count(*) from rce_disposition_events where intake_id = cast(:i as uuid)"),
        {"i": intake_id}).scalar()

    proposals = []
    for s in sources:
        c = curated.get(str(s["id"]))
        eid = str((c or {}).get("canonical_entity_id") or s["canonical_entity_id"] or "") or None
        proposals.append(propose(dict(s), c, entities.get(eid) if eid else None,
                                 versions.get(eid, []) if eid else [], window))
    result = {
        "intake_id": str(intake["id"]), "found": True,
        "delivery_label": intake["delivery_label"], "filename": intake["original_filename"],
        "job_id": str(job["id"]) if job else None,
        "job_window": [window[0].isoformat() if window else None,
                       window[1].isoformat() if window and window[1] else None],
        "declared_record_count": intake["record_count"], "records": len(sources),
        "existing_disposition_events": int(existing_events or 0),
        "proposals": proposals, **summarise(proposals, len(sources)),
    }
    if existing_events:
        result["limitations"].insert(0, (
            f"{existing_events} disposition event(s) already exist for this intake; "
            f"reconstruction would append to a ledger that is not empty and is refused"))
    return result


# ═══ the write path (never used unattended) ══════════════════════════════════

def write_reconstruction(conn, analysis: Dict[str, Any], *, approved_by: str,
                         approval_ref: str) -> Dict[str, Any]:
    """Append reconstructed disposition events and ONE RECONSTRUCTION snapshot.

    Refuses when any proposal is unavailable or when the intake already has
    disposition events. Every row carries reconstructed=true and the payload.
    """
    from sqlalchemy import text

    if analysis["totals"]["unavailable"]:
        raise SystemExit("REFUSED: %d proposal(s) are unavailable; the equation cannot close"
                         % analysis["totals"]["unavailable"])
    if analysis["existing_disposition_events"]:
        raise SystemExit("REFUSED: disposition events already exist for this intake")
    if not analysis["job_id"]:
        raise SystemExit("REFUSED: a snapshot needs a job; this intake has none")
    now = datetime.now(timezone.utc)
    build_sha = os.environ.get("GIT_SHA", "unknown")[:40]
    correlation = f"reconstruction:{uuid.uuid4()}"[:64]
    revision = conn.execute(text("select version_num from alembic_version")).scalar() or "unknown"
    payload_base = {"method_version": METHOD_VERSION, "operator": approved_by,
                    "approval_ref": approval_ref, "reconstructed_at": now.isoformat()}
    written = 0
    for p in analysis["proposals"]:
        payload = {**payload_base, "source_evidence": p["basis"], "confidence": p["confidence"],
                   "field_classification": p["fields"]}
        conn.execute(text(
            "insert into rce_disposition_events (id, intake_id, source_record_id, curated_record_id, "
            "job_id, sequence, disposition, reason_code, reason, entity_id, changed_fields, actor, "
            "actor_type, decided_at, correlation_id, build_sha, reconstructed, reconstruction) "
            "values (cast(:id as uuid), cast(:intake as uuid), cast(:src as uuid), "
            "cast(:cur as uuid), cast(:job as uuid), "
            "coalesce((select max(sequence) from rce_disposition_events where source_record_id = "
            "cast(:src as uuid)), 0) + 1, :disp, :code, NULL, cast(:ent as uuid), "
            "cast(:changed as jsonb), :actor, 'HUMAN', :now, :corr, :sha, true, cast(:recon as jsonb))"),
            {"id": str(uuid.uuid4()), "intake": analysis["intake_id"], "src": p["source_record_id"],
             "cur": p["curated_record_id"], "job": analysis["job_id"], "disp": p["disposition"],
             "code": p["reason_code"], "ent": p["entity_id"],
             "changed": json.dumps(p["changed_fields"]), "actor": approved_by[:320], "now": now,
             "corr": correlation, "sha": build_sha, "recon": json.dumps(payload)})
        written += 1
    eq = analysis["equation"]
    seq = conn.execute(text(
        "select coalesce(max(sequence), 0) + 1 from rce_reconciliation_snapshots "
        "where job_id = cast(:j as uuid)"), {"j": analysis["job_id"]}).scalar()
    canonical = json.dumps({"equation": eq, "dimensions": {}, "checks": []},
                           sort_keys=True, separators=(",", ":"))
    conn.execute(text(
        "insert into rce_reconciliation_snapshots (id, job_id, intake_id, sequence, passed, "
        "failure_reason, received, created, updated, matched_unchanged, held, rejected, "
        "missing_key, excluded, dimensions, checks, source_evidence, actor, trigger, created_at, "
        "hash, build_sha, migration_revision, correlation_id, reconstructed) values "
        "(cast(:id as uuid), cast(:job as uuid), cast(:intake as uuid), :seq, :passed, :why, "
        ":received, :created, :updated, :mu, :held, :rejected, :mk, :excluded, '{}'::jsonb, "
        "'[]'::jsonb, cast(:evidence as jsonb), :actor, 'RECONSTRUCTION', :now, :hash, :sha, "
        ":rev, :corr, true)"),
        {"id": str(uuid.uuid4()), "job": analysis["job_id"], "intake": analysis["intake_id"],
         "seq": seq, "passed": bool(eq["holds"]),
         "why": None if eq["holds"] else f"difference {eq['difference']}",
         "received": eq["received"], "created": eq["created"], "updated": eq["updated"],
         "mu": eq["matched_unchanged"], "held": eq["held"], "rejected": eq["rejected"],
         "mk": eq["missing_key"], "excluded": eq["excluded"],
         "evidence": json.dumps({**payload_base, "classification_summary":
                                 analysis["classification_summary"]}),
         "actor": approved_by[:320], "now": now,
         "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "sha": build_sha,
         "rev": revision, "corr": correlation})
    return {"events_written": written, "snapshot_sequence": seq, "correlation_id": correlation}


# ═══ entry point ═════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--intake-id", required=True)
    ap.add_argument("--job-id", default=None)
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="read only (default)")
    mode.add_argument("--write", action="store_true", help="append reconstructed rows")
    ap.add_argument("--approved-by", default=None)
    ap.add_argument("--approval-ref", default=None)
    ap.add_argument("--allow-shared", action="store_true",
                    help="permit --write against a non-local (shared) database host")
    ap.add_argument("--summary-only", action="store_true", help="omit per-record proposals")
    ap.add_argument("--json", dest="json_path", default=None, help="also write the JSON here")
    args = ap.parse_args(argv)

    if not args.database_url:
        print("REFUSED: no DATABASE_URL", file=sys.stderr)
        return 1
    if args.write:
        if not (args.approved_by and args.approval_ref):
            print("REFUSED: --write needs --approved-by <name> and --approval-ref <ticket>",
                  file=sys.stderr)
            return 1
        if is_shared_host(args.database_url) and not args.allow_shared:
            print("REFUSED: --write against a shared (non-local) database host needs "
                  "--allow-shared and a recorded approval", file=sys.stderr)
            return 1

    import sqlalchemy as sa
    from sqlalchemy import text

    engine = sa.create_engine(_sync_url(args.database_url))
    try:
        if not args.write:
            with engine.connect().execution_options(postgresql_readonly=True) as conn:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                result = analyse(conn, args.intake_id, args.job_id)
                conn.rollback()
            result["mode"] = "dry-run"
            result["writes_performed"] = 0
        else:
            with engine.begin() as conn:
                result = analyse(conn, args.intake_id, args.job_id)
                outcome = write_reconstruction(conn, result, approved_by=args.approved_by,
                                               approval_ref=args.approval_ref)
            result["mode"] = "write"
            result["writes_performed"] = outcome["events_written"] + 1
            result["write_outcome"] = outcome
    finally:
        engine.dispose()

    result["method_version"] = METHOD_VERSION
    if args.summary_only:
        result.pop("proposals", None)
    payload = json.dumps(result, indent=2, default=str)
    print(payload)
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            sys.exit(1)
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
