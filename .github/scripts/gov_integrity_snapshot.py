"""Read-only Government-data baseline for the automated DEV release pipeline.

Runs BEFORE deploy.py swaps the image or runs a migration. For every existing
delivery (`source_intake_id`), computes a digest over its source records'
own `record_sha256` values (never the raw records themselves) in a fixed
order, so the output proves "this delivery's bytes are unchanged" without the
CI job ever holding or logging the underlying Government data.

Connects with the token minted by the workflow's own `az account
get-access-token` step (passed in via PGTOKEN, never written to a file or
echoed) and assumes `docuaction_app` - the same role the running application
already reads this table as. This does not request a new grant: if the CI
OIDC principal cannot SET ROLE docuaction_app, that is a real gap to close
(grant docuaction_app to `github-actions-docuaction-backend-dev`) rather than
something this script should route around by reading as a broader role.
"""
import json
import os
import sys

import psycopg2

PGHOST = os.environ["PGHOST"]
PGDATABASE = os.environ["PGDATABASE"]
PG_PRINCIPAL = os.environ["PG_PRINCIPAL"]
PGTOKEN = os.environ["PGTOKEN"]

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
    conn = psycopg2.connect(
        host=PGHOST, dbname=PGDATABASE, user=PG_PRINCIPAL, password=PGTOKEN,
        sslmode="require", connect_timeout=20,
    )
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute("set role docuaction_app")
    except Exception as exc:  # noqa: BLE001
        print(
            f"FAIL: could not SET ROLE docuaction_app as {PG_PRINCIPAL}: {exc}\n"
            f"This is a permission gap, not a code defect - grant docuaction_app "
            f"to {PG_PRINCIPAL} before this check can run. Refusing to read "
            f"rce_source_records under any broader role instead.",
            file=sys.stderr,
        )
        conn.close()
        sys.exit(1)

    cur.execute(SNAPSHOT_SQL)
    rows = cur.fetchall()
    conn.close()

    baseline = {intake_id: {"digest": digest, "record_count": count}
                for intake_id, digest, count in rows}

    print(f"baseline captured: {len(baseline)} existing deliveries", file=sys.stderr)

    print("baseline_json<<GOV_INTEGRITY_EOF")
    print(json.dumps(baseline, separators=(",", ":")))
    print("GOV_INTEGRITY_EOF")
    print(f"intake_count={len(baseline)}")


if __name__ == "__main__":
    main()
