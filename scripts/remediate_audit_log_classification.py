"""Classify pre-existing audit_logs rows — a data-remediation operation.

WHY THIS IS NOT A MIGRATION
───────────────────────────
Revision 20260817_audit_fields adds `event_type`, `outcome` and
`correlation_id` to `audit_logs`. It adds the columns and their indexes and
stops there. Rows written before that revision carry NULL in all three.

Assigning a classification to an audit record that already exists is not schema
work. It rewrites the audit trail — the one table whose value depends on nobody
rewriting it. So it does not run inside a migration, unattended, on whatever
database happens to be next in the deployment pipeline. It runs here, once, by
a named person, against a stated row count, leaving a journal that can undo it.

The original migration embedded these UPDATEs. Besides the risk-class problem
they were also unexecutable: the correlation-id statement filtered on
`details ? 'correlation_id'`, and the `?` key-existence operator exists only for
`jsonb`. `audit_logs.details` is `json`, so the statement raised

    operator does not exist: json ? unknown

and aborted the whole revision. This script uses `details ->> 'key'`, which is
defined for both `json` and `jsonb`. The semantics differ in one harmless way:
`?` is true for a key whose value is JSON null, whereas `->>` yields SQL NULL
there. A row like that would have been assigned NULL by the original statement
anyway, so nothing is lost.

WHAT IT DERIVES
───────────────
  correlation_id  lifted from details->>'correlation_id', which the auth routes
                  have written since the enterprise auth work. Those rows
                  already hold the value; it was simply not addressable.
  outcome         from details->>'result', else from the action name.
  event_type      from the action name, using the same buckets as
                  app/services/audit.py::classify_event_type, so the Audit
                  Trail filter shows history and new events under one taxonomy.

Leaving history NULL makes the new filters lie by omission: an empty result for
"failed logins before today" is indistinguishable from "there were none". That
is the argument FOR running this. It is an argument for a records decision, not
for a silent one.

USAGE
─────
    # 1. Report only. Writes nothing. Always start here.
    python scripts/remediate_audit_log_classification.py

    # 2. Apply. Every argument is mandatory. --expect-rows must equal the
    #    number the dry run reported, so the operation cannot run blind
    #    against a table that changed since it was reviewed.
    python scripts/remediate_audit_log_classification.py \
        --apply --authorized-by "Name, Role" --expect-rows 251 \
        --journal docs/rce/audit_classification_journal.json

    # 3. Undo, using the journal written by step 2.
    python scripts/remediate_audit_log_classification.py \
        --revert docs/rce/audit_classification_journal.json \
        --authorized-by "Name, Role"

Exit codes: 0 success, 1 refused (precondition failed), 2 error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import sqlalchemy as sa

TABLE = "audit_logs"
TARGET_COLUMNS = ("event_type", "outcome", "correlation_id")

# ── the derivations ─────────────────────────────────────────────────────────
# Each is read as a SELECT first. The script reads the proposed value for every
# affected row, journals it, and only then writes. That is what makes the
# operation reversible and reviewable rather than a fire-and-forget statement.

CORRELATION_EXPR = "details ->> 'correlation_id'"

OUTCOME_EXPR = r"""
    CASE
      WHEN lower(coalesce(details ->> 'result', '')) IN ('fail', 'failure', 'error')
           THEN 'failure'
      WHEN lower(coalesce(details ->> 'result', '')) = 'rejected' THEN 'rejected'
      WHEN lower(coalesce(details ->> 'result', '')) IN ('blocked', 'denied')
           THEN 'blocked'
      WHEN lower(action) LIKE '%\_failed'    ESCAPE '\' THEN 'failure'
      WHEN lower(action) LIKE '%\_failure'   ESCAPE '\' THEN 'failure'
      WHEN lower(action) LIKE '%\_blocked'   ESCAPE '\' THEN 'blocked'
      WHEN lower(action) LIKE '%\_throttled' ESCAPE '\' THEN 'blocked'
      WHEN lower(action) LIKE '%\_rejected'  ESCAPE '\' THEN 'rejected'
      ELSE 'success'
    END
"""

EVENT_TYPE_EXPR = r"""
    CASE
      WHEN lower(action) IN (
             'login_success', 'login_failed', 'login_failure',
             'login_blocked', 'login_throttled', 'logout', 'signup',
             'signup_rejected', 'signup_throttled', 'password_reset',
             'email_verified')
           THEN 'authentication'
      WHEN lower(action) IN ('file_scan', 'permission_denied') THEN 'security'
      WHEN lower(action) IN (
             'entity_import', 'import_completed', 'fhir_import', 'csv_import')
           THEN 'data_import'
      WHEN lower(action) IN (
             'review_executed', 'review_decision', 'entity_verified',
             'bucket_override', 'verification_started', 'verification_completed')
           THEN 'review'
      WHEN lower(action) IN (
             'entity_created', 'entity_updated', 'status_changed',
             'status_change_refused', 'npi_flagged')
           THEN 'data_change'
      WHEN lower(action) IN (
             'user_approved', 'user_rejected', 'user_disabled',
             'user_role_changed', 'user_invited', 'password_set')
           THEN 'administration'
      WHEN lower(action) LIKE '%report%' THEN 'reporting'
      WHEN lower(action) LIKE '%export%' THEN 'reporting'
      WHEN lower(action) LIKE 'login%'   THEN 'authentication'
      WHEN lower(action) LIKE 'signup%'  THEN 'authentication'
      WHEN lower(action) LIKE 'auth%'    THEN 'authentication'
      WHEN lower(action) LIKE 'user\_%'  ESCAPE '\' THEN 'administration'
      -- classify_event_type()'s residue bucket, which is the label the Audit
      -- Trail filter offers. 'system' would be a bucket the filter omits.
      ELSE 'other'
    END
"""

DERIVATIONS = {
    "correlation_id": CORRELATION_EXPR,
    "outcome": OUTCOME_EXPR,
    "event_type": EVENT_TYPE_EXPR,
}


def _engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        env = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(env):
            for line in open(env, "rb").read().decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    if key.strip() == "DATABASE_URL":
                        url = value.strip()
        if not url:
            sys.exit("DATABASE_URL is not set.")
    return sa.create_engine("postgresql+psycopg2://" + url.split("://", 1)[1])


def _preflight(conn) -> None:
    """Refuse to run against a schema this script was not written for."""
    inspector = sa.inspect(conn)
    columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    missing = [c for c in TARGET_COLUMNS if c not in columns]
    if missing:
        sys.exit(f"REFUSED: {TABLE} is missing {missing}. "
                 f"Run `alembic upgrade 20260817_audit_fields` first.")
    details_type = str(columns["details"]["type"]).lower()
    if "json" not in details_type:
        sys.exit(f"REFUSED: {TABLE}.details is {details_type}, not json/jsonb. "
                 f"The -> operators below do not apply.")


def _proposed(conn) -> dict:
    """Read the proposed value for every row that would change. No writes."""
    plan = {}
    for column, expr in DERIVATIONS.items():
        where = f"{column} IS NULL"
        if column == "correlation_id":
            where += f" AND {CORRELATION_EXPR} IS NOT NULL"
        rows = conn.execute(sa.text(
            f"SELECT id, {column} AS old_value, ({expr}) AS new_value "
            f"FROM {TABLE} WHERE {where} ORDER BY id"
        )).mappings().all()
        plan[column] = [
            {"id": str(r["id"]), "old": r["old_value"], "new": r["new_value"]}
            for r in rows
            if r["new_value"] is not None and r["new_value"] != r["old_value"]
        ]
    return plan


def _report(plan: dict, total_rows: int) -> int:
    print("=" * 78)
    print("AUDIT LOG CLASSIFICATION — PROPOSED CHANGES (nothing has been written)")
    print("=" * 78)
    print(f"  {TABLE} rows in table : {total_rows}")
    affected = set()
    for column in TARGET_COLUMNS:
        entries = plan[column]
        affected.update(e["id"] for e in entries)
        buckets: dict = {}
        for entry in entries:
            buckets[entry["new"]] = buckets.get(entry["new"], 0) + 1
        print(f"\n  {column}: {len(entries)} row(s) would be set"
              f"  ({len(buckets)} distinct value(s))")
        # correlation_id is per-transaction, so its value list is as long as the
        # row list. Show the shape, not a transcript of it.
        ranked = sorted(buckets.items(), key=lambda kv: (-kv[1], str(kv[0])))
        for value, count in ranked[:8]:
            print(f"      {value!r:>18} : {count}")
        if len(ranked) > 8:
            print(f"      ... and {len(ranked) - 8} more distinct value(s)")
        if not entries:
            print("      (none)")
    print(f"\n  distinct rows touched: {len(affected)}")
    print(f"  --expect-rows value  : {len(affected)}")
    return len(affected)


def _apply(conn, plan: dict, args, total_rows: int) -> None:
    affected = {e["id"] for column in TARGET_COLUMNS for e in plan[column]}
    if len(affected) != args.expect_rows:
        sys.exit(f"REFUSED: --expect-rows {args.expect_rows} but {len(affected)} "
                 f"row(s) would change. Re-run the dry run and review the delta.")
    journal = {
        "operation": "audit_log_classification",
        "authorized_by": args.authorized_by,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "database": conn.execute(sa.text("SELECT current_database()")).scalar(),
        "table_rows_at_apply": total_rows,
        "rows_touched": len(affected),
        "changes": plan,
    }
    for column, expr in DERIVATIONS.items():
        ids = [e["id"] for e in plan[column]]
        if not ids:
            continue
        conn.execute(
            sa.text(f"UPDATE {TABLE} SET {column} = ({expr}) "
                    f"WHERE id = ANY(:ids) AND {column} IS NULL"),
            {"ids": ids},
        )
    with open(args.journal, "w", encoding="utf-8") as handle:
        json.dump(journal, handle, indent=1)
    conn.commit()
    print(f"Applied. {len(affected)} row(s) classified. Journal: {args.journal}")


def _revert(conn, args) -> None:
    journal = json.load(open(args.revert, encoding="utf-8"))
    reverted = 0
    for column, entries in journal["changes"].items():
        for entry in entries:
            result = conn.execute(
                sa.text(f"UPDATE {TABLE} SET {column} = :old "
                        f"WHERE id = :id AND {column} IS NOT DISTINCT FROM :new"),
                {"old": entry["old"], "new": entry["new"], "id": entry["id"]},
            )
            reverted += result.rowcount
    conn.commit()
    print(f"Reverted {reverted} column value(s) from {args.revert} "
          f"(authorized by {args.authorized_by}).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (requires --authorized-by, "
                             "--expect-rows and --journal)")
    parser.add_argument("--authorized-by", help="name and role of the approver")
    parser.add_argument("--expect-rows", type=int,
                        help="row count the dry run reported; must match exactly")
    parser.add_argument("--journal", help="path to write the undo journal to")
    parser.add_argument("--revert", help="undo a previous apply, using its journal")
    args = parser.parse_args()

    engine = _engine()
    with engine.connect() as conn:
        _preflight(conn)
        if args.revert:
            if not args.authorized_by:
                sys.exit("REFUSED: --revert requires --authorized-by.")
            _revert(conn, args)
            return
        total_rows = conn.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar()
        plan = _proposed(conn)
        count = _report(plan, total_rows)
        if not args.apply:
            print("\nDry run. Nothing was written. Re-run with --apply "
                  "--authorized-by ... --expect-rows "
                  f"{count} --journal <path> to commit.")
            return
        for flag in ("authorized_by", "expect_rows", "journal"):
            if getattr(args, flag) in (None, ""):
                sys.exit(f"REFUSED: --apply requires --{flag.replace('_', '-')}.")
        _apply(conn, plan, args, total_rows)


if __name__ == "__main__":
    main()
