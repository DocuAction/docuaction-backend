"""Read-only Government-data verification for the automated DEV release
pipeline. Runs AFTER deploy.py has already swapped the image and (maybe) run
a migration.

Re-reads the same per-delivery digest gov_integrity_snapshot.py captured
before the deploy, and asserts every delivery that existed BEFORE still
carries the identical digest. A delivery that appears in the AFTER read but
not the BEFORE one is a new intake_id and is logged, not failed - a normal
DEV release must never fabricate a new Government delivery mid-deploy, but
this workflow does not run any ingestion itself, so in practice any new id
seen here means a genuinely concurrent, independently-authorised delivery
landed during the deploy window, which is worth recording, not blocking.

Any delivery present in BOTH reads whose digest changed is an unexplained
mutation of existing Government-source evidence, and this script exits
non-zero - which fails the gov-verify job and, by extension, the release.
It never attempts to repair or overwrite anything.
"""
import json
import os
import sys

import psycopg2

PGHOST = os.environ["PGHOST"]
PGDATABASE = os.environ["PGDATABASE"]
PG_PRINCIPAL = os.environ["PG_PRINCIPAL"]
PGTOKEN = os.environ["PGTOKEN"]
BASELINE_JSON = os.environ["BASELINE_JSON"]

SNAPSHOT_SQL = """
    SELECT source_intake_id::text,
           encode(
               sha256(convert_to(string_agg(record_sha256, '' ORDER BY line_number), 'UTF8')),
               'hex'
           ) AS digest,
           count(*) AS record_count
    FROM rce_source_records
    GROUP BY source_intake_id
"""


def main() -> None:
    baseline = json.loads(BASELINE_JSON) if BASELINE_JSON.strip() else {}

    # Unlike gov_integrity_snapshot.py, an unreachable DB HERE is not treated
    # as routine: this step only ever runs after gov-baseline successfully
    # captured a real (non-empty-because-unreadable) baseline and deploy
    # actually happened, so DEV Postgres was reachable moments ago. Losing
    # that reachability now means Government-data integrity cannot be
    # confirmed post-deploy - which is itself something to stop and look at,
    # not something to silently pass over.
    try:
        conn = psycopg2.connect(
            host=PGHOST, dbname=PGDATABASE, user=PG_PRINCIPAL, password=PGTOKEN,
            sslmode="require", connect_timeout=20,
        )
    except psycopg2.OperationalError as exc:
        print(f"FAIL: DEV Postgres became unreachable from this runner during "
              f"the deploy window - cannot confirm Government-data integrity "
              f"post-deploy: {exc}", file=sys.stderr)
        sys.exit(1)

    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute("set role docuaction_app")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not SET ROLE docuaction_app as {PG_PRINCIPAL}: {exc}",
              file=sys.stderr)
        conn.close()
        sys.exit(1)

    cur.execute(SNAPSHOT_SQL)
    rows = cur.fetchall()
    conn.close()

    after = {intake_id: {"digest": digest, "record_count": count}
             for intake_id, digest, count in rows}

    mutated = []
    for intake_id, before_entry in baseline.items():
        after_entry = after.get(intake_id)
        if after_entry is None:
            mutated.append((intake_id, "PRESENT BEFORE, MISSING AFTER", before_entry, after_entry))
            continue
        if after_entry["digest"] != before_entry["digest"]:
            mutated.append((intake_id, "DIGEST CHANGED", before_entry, after_entry))

    new_since_baseline = sorted(set(after) - set(baseline))

    print(f"deliveries before deploy: {len(baseline)}")
    print(f"deliveries after deploy:  {len(after)}")
    if new_since_baseline:
        print(f"new delivery ids since baseline ({len(new_since_baseline)}), not a failure by itself:")
        for intake_id in new_since_baseline:
            print(f"  + {intake_id} ({after[intake_id]['record_count']} records)")

    if mutated:
        print("", file=sys.stderr)
        print("CERTIFICATION FAIL: unexplained mutation of existing Government "
              "source evidence detected during this deploy:", file=sys.stderr)
        for intake_id, reason, before_entry, after_entry in mutated:
            print(f"  - {intake_id}: {reason}", file=sys.stderr)
            print(f"      before: {before_entry}", file=sys.stderr)
            print(f"      after:  {after_entry}", file=sys.stderr)
        sys.exit(1)

    print("PASS: every pre-existing delivery's digest is unchanged after this deploy")


if __name__ == "__main__":
    main()
