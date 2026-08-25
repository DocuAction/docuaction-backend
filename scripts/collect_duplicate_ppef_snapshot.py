#!/usr/bin/env python
"""
Collect the child records of a PPEF snapshot that re-ingested an identical file.

WHY THIS IS A SEPARATE SCRIPT
`cleanup_stuck_ppef_snapshots.py` collects rows under snapshots that FAILED. Its
central invariant — asserted by its tests — is that a `complete` snapshot is
evidence and is never rewritten or emptied by either of its passes. A duplicate
load is `complete`: it finished, it is internally consistent, and its rows are
real. Teaching that script to delete from `complete` snapshots would destroy the
one property that makes it safe to run unattended.

So this is a different operation with a different guarantee, and it is deliberately
not automatic. It names both snapshots explicitly, it re-proves the duplication
from the data every time it runs, and it refuses if the proof does not hold.

WHAT IT PROVES BEFORE IT DELETES ANYTHING
Being "obviously a duplicate" to a human reading two rows is not evidence. Every
one of these must hold or the script exits without writing:

  1. both snapshots exist and are `complete`;
  2. same component, file name, resource version, file size;
  3. same SHA-256, and it is not NULL — a NULL hash proves nothing;
  4. the duplicate was ingested STRICTLY LATER than the canonical one, so the
     earlier successful ingestion is the one that survives;
  5. both hold the same number of child rows;
  6. every business key in the duplicate appears in the canonical snapshot, and
     the reverse — zero rows unique to either side. This is the check that
     actually matters: identical metadata with different contents would mean the
     second load caught a changed file under an unchanged name;
  7. nothing in `tefca_dimension_evidence` cites the duplicate;
  8. no non-terminal ingestion job claims the duplicate.

WHAT IT DELIBERATELY DOES NOT DO
It never deletes a SNAPSHOT row — not the duplicate's and not the canonical's.
The duplicate snapshot is the record that a second ingestion happened, and that
history is the reason anyone can later explain the row counts. It is left in
place with its counts, its hash and its timestamps, holding zero children, in
exactly the same shape as a collected failed snapshot.

It also never touches the canonical snapshot's rows, and it verifies afterwards
that it did not.

USAGE
    python scripts/collect_duplicate_ppef_snapshot.py \
        --duplicate <uuid> --canonical <uuid>              # dry run
    python scripts/collect_duplicate_ppef_snapshot.py \
        --duplicate <uuid> --canonical <uuid> --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Fields that must agree exactly for two snapshots to describe the same file.
IDENTITY_FIELDS = ("component", "file_name", "resource_version", "file_size", "sha256")


class DuplicateProofError(RuntimeError):
    """The duplication could not be proven. Nothing was deleted."""


async def _prove(db, duplicate: str, canonical: str) -> dict:
    """Re-derive the duplication from the data. Raises if it does not hold."""
    from sqlalchemy import text

    rows = (await db.execute(text(
        "select id, component, file_name, resource_version, file_size, sha256, "
        "       ingest_status, ingested_at, record_count "
        "from tefca_ppef_snapshots where id = any(cast(:ids as uuid[]))"
    ), {"ids": [duplicate, canonical]})).mappings().all()
    found = {str(r["id"]): r for r in rows}

    for label, sid in (("duplicate", duplicate), ("canonical", canonical)):
        if sid not in found:
            raise DuplicateProofError(f"{label} snapshot {sid} does not exist")
        if found[sid]["ingest_status"] != "complete":
            raise DuplicateProofError(
                f"{label} snapshot {sid} is {found[sid]['ingest_status']!r}, not 'complete'. "
                f"A snapshot that did not finish is the other script's job.")

    dup, can = found[duplicate], found[canonical]

    for field in IDENTITY_FIELDS:
        if dup[field] != can[field]:
            raise DuplicateProofError(
                f"{field} differs: duplicate={dup[field]!r} canonical={can[field]!r}. "
                f"These snapshots do not describe the same file.")
    if not dup["sha256"]:
        raise DuplicateProofError(
            "sha256 is NULL on both snapshots. Equal NULLs prove nothing about "
            "the bytes that were loaded.")

    if not (dup["ingested_at"] and can["ingested_at"] and dup["ingested_at"] > can["ingested_at"]):
        raise DuplicateProofError(
            f"the duplicate ({dup['ingested_at']}) is not strictly later than the "
            f"canonical ({can['ingested_at']}). The earlier successful ingestion "
            f"is the one that must survive.")

    counts = {}
    for label, sid in (("duplicate", duplicate), ("canonical", canonical)):
        counts[label] = (await db.execute(text(
            "select count(*) from tefca_ppef_records where snapshot_id = cast(:s as uuid)"
        ), {"s": sid})).scalar()
    if counts["duplicate"] != counts["canonical"]:
        raise DuplicateProofError(
            f"child row counts differ: duplicate={counts['duplicate']:,} "
            f"canonical={counts['canonical']:,}")
    if not counts["duplicate"]:
        raise DuplicateProofError("the duplicate snapshot already holds no rows")

    # The check that matters: identical metadata with different contents would
    # mean the second load caught a changed file under an unchanged name.
    only_dup = (await db.execute(text(
        "select count(*) from ("
        "  select enrollment_id, npi from tefca_ppef_records where snapshot_id = cast(:d as uuid)"
        "  except"
        "  select enrollment_id, npi from tefca_ppef_records where snapshot_id = cast(:c as uuid)"
        ") x"), {"d": duplicate, "c": canonical})).scalar()
    only_can = (await db.execute(text(
        "select count(*) from ("
        "  select enrollment_id, npi from tefca_ppef_records where snapshot_id = cast(:c as uuid)"
        "  except"
        "  select enrollment_id, npi from tefca_ppef_records where snapshot_id = cast(:d as uuid)"
        ") x"), {"d": duplicate, "c": canonical})).scalar()
    if only_dup or only_can:
        raise DuplicateProofError(
            f"content differs: {only_dup:,} keys unique to the duplicate and "
            f"{only_can:,} unique to the canonical snapshot. The later load is "
            f"NOT a re-ingestion of the same data.")

    cited = (await db.execute(text(
        "select count(*) from tefca_dimension_evidence where source_dataset = :d"
    ), {"d": duplicate})).scalar()
    if cited:
        raise DuplicateProofError(
            f"{cited} evidence row(s) cite the duplicate snapshot as their source "
            f"dataset. Deleting its records would break reproducibility.")

    live = (await db.execute(text(
        "select count(*) from tefca_ppef_ingest_jobs "
        "where snapshot_id = cast(:d as uuid) and state not in ('COMPLETE','FAILED')"
    ), {"d": duplicate})).scalar()
    if live:
        raise DuplicateProofError(
            f"{live} non-terminal ingestion job(s) claim the duplicate snapshot")

    return {"rows": counts["duplicate"], "sha256": dup["sha256"],
            "component": dup["component"], "file_name": dup["file_name"],
            "duplicate_ingested_at": dup["ingested_at"],
            "canonical_ingested_at": can["ingested_at"]}


async def run(duplicate: str, canonical: str, confirm: bool, allow_prod: bool) -> int:
    from sqlalchemy import text

    from app.core.database import async_session_maker

    environment = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").lower()
    if environment in {"production", "prod"} and not allow_prod:
        print("REFUSING: ENVIRONMENT is production and --allow-prod was not given.")
        return 2
    if duplicate == canonical:
        print("REFUSING: --duplicate and --canonical are the same snapshot.")
        return 2

    print(f"duplicate PPEF snapshot collection — {'APPLY' if confirm else 'DRY RUN'}")
    print(f"environment: {environment or '(unset)'}")
    print(f"  duplicate (records deleted) : {duplicate}")
    print(f"  canonical (records kept)    : {canonical}")
    print("-" * 78)

    async with async_session_maker() as db:
        try:
            proof = await _prove(db, duplicate, canonical)
        except DuplicateProofError as exc:
            print(f"PROOF FAILED — nothing was deleted.\n  {exc}")
            return 3

        print("proof holds:")
        print(f"  component      : {proof['component']}")
        print(f"  file_name      : {proof['file_name']}")
        print(f"  sha256         : {proof['sha256']}")
        print(f"  canonical at   : {proof['canonical_ingested_at']}")
        print(f"  duplicate at   : {proof['duplicate_ingested_at']}  (later)")
        print(f"  identical rows : {proof['rows']:,}  (zero unique to either side)")
        print(f"  evidence citing the duplicate: 0")

        if not confirm:
            print("-" * 78)
            print(f"rows that WOULD be deleted: {proof['rows']:,}")
            print("the duplicate SNAPSHOT row would be retained as ingestion evidence.")
            print("\nDRY RUN — re-run with --confirm to apply.")
            return 0

        before_can = (await db.execute(text(
            "select count(*) from tefca_ppef_records where snapshot_id = cast(:c as uuid)"
        ), {"c": canonical})).scalar()

        result = await db.execute(text(
            "delete from tefca_ppef_records where snapshot_id = cast(:d as uuid)"
        ), {"d": duplicate})
        deleted = result.rowcount

        # Prove, in the same transaction, that the canonical side was untouched
        # and the duplicate snapshot row survived. A cross-snapshot delete would
        # be catastrophic and silent; this makes it impossible to commit one.
        after_can = (await db.execute(text(
            "select count(*) from tefca_ppef_records where snapshot_id = cast(:c as uuid)"
        ), {"c": canonical})).scalar()
        snap_alive = (await db.execute(text(
            "select count(*) from tefca_ppef_snapshots where id = cast(:d as uuid)"
        ), {"d": duplicate})).scalar()

        if after_can != before_can or deleted != proof["rows"] or snap_alive != 1:
            await db.rollback()
            print("ROLLED BACK — post-delete invariants did not hold:")
            print(f"  canonical rows {before_can:,} -> {after_can:,} (must not change)")
            print(f"  deleted {deleted:,}, expected {proof['rows']:,}")
            print(f"  duplicate snapshot rows surviving: {snap_alive} (must be 1)")
            return 4

        await db.commit()

    print("-" * 78)
    print(f"duplicate child records deleted : {deleted:,}")
    print(f"canonical records untouched     : {after_can:,}")
    print("duplicate SNAPSHOT row retained as ingestion evidence.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duplicate", required=True,
                        help="snapshot whose CHILD RECORDS are deleted (row is kept)")
    parser.add_argument("--canonical", required=True,
                        help="snapshot that must survive intact")
    parser.add_argument("--confirm", action="store_true",
                        help="actually write changes (default is a dry run)")
    parser.add_argument("--allow-prod", action="store_true",
                        help="required when ENVIRONMENT=production")
    args = parser.parse_args()
    return asyncio.run(run(args.duplicate, args.canonical, args.confirm, args.allow_prod))


if __name__ == "__main__":
    raise SystemExit(main())
