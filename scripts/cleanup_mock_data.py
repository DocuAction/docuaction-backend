#!/usr/bin/env python3
"""Delete seeded mock rows from tefca_reviews (and their dependent findings).

WHEN TO RUN THIS
----------------
Not now. As of August 2026 BOTH environments contain only mock reviews and zero
real ones (dev 11/11 mock, prod 50/50 mock), and the system is in pre-production
demonstration mode. The mock rows are what makes a demo possible.

Run this ONCE, immediately BEFORE the first import of ONC-provided entity data,
so the review population is empty when real data lands. Mixing seeded rows with
ONC data is the failure this script exists to prevent: every sample, denominator
and report is drawn from that population, and a contaminated denominator is not
correctable after the fact.

SAFETY MODEL
------------
Four independent gates, all of which must pass:

  1. --confirm is mandatory. Without it the script reports and exits.
  2. Production is refused unless --allow-prod is ALSO given. ENVIRONMENT is read
     from the environment; an unset/unknown value is treated as production
     (fail-closed) rather than assumed safe.
  3. Only rows with is_mock_data = true are touched. A row that is NULL or false
     is never in scope, so a real row cannot be deleted even by mistake.
  4. Every run prints what it will delete and re-counts afterwards. --dry-run
     performs the full inspection inside a transaction that is always rolled back.

This script is MANUAL ONLY. Nothing invokes it; no scheduler, no endpoint, no
deploy step. It is deliberately not importable as a library.

USAGE
-----
  python scripts/cleanup_mock_data.py --dry-run
  python scripts/cleanup_mock_data.py --confirm
  python scripts/cleanup_mock_data.py --confirm --allow-prod     # production

The connection string is read from DATABASE_URL, exactly as the application reads
it. Credentials in a DSN are percent-encoded and are decoded before use.
"""
import argparse
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. pip install psycopg2-binary", file=sys.stderr)
    sys.exit(2)


def parse_dsn(url):
    m = re.match(r"[a-z+]+://([^:]+):([^@]+)@([^:/]+):?(\d*)/([^?]+)", url)
    if not m:
        raise SystemExit("ERROR: DATABASE_URL is not in the expected form")
    return {
        "user": urllib.parse.unquote(m.group(1)),
        "password": urllib.parse.unquote(m.group(2)),
        "host": m.group(3),
        "port": m.group(4) or "5432",
        "dbname": m.group(5),
        "sslmode": "require",
        "connect_timeout": 30,
    }


def main():
    p = argparse.ArgumentParser(
        description="Delete is_mock_data=true rows from tefca_reviews. Manual use only.")
    p.add_argument("--confirm", action="store_true",
                   help="Actually delete. Without this the script only reports.")
    p.add_argument("--allow-prod", action="store_true",
                   help="Required IN ADDITION to --confirm when ENVIRONMENT=production.")
    p.add_argument("--dry-run", action="store_true",
                   help="Run the deletes inside a transaction and roll back, reporting exact counts.")
    args = p.parse_args()

    env = (os.getenv("ENVIRONMENT", "") or "").strip().lower()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("ERROR: DATABASE_URL is not set.")

    dsn = parse_dsn(url)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    print("=" * 70)
    print("MOCK DATA CLEANUP")
    print("=" * 70)
    print("  time        : %s" % stamp)
    print("  ENVIRONMENT : %s" % (env or "(unset)"))
    print("  host        : %s" % dsn["host"])
    print("  database    : %s" % dsn["dbname"])
    print("  mode        : %s" % ("DRY RUN (rollback)" if args.dry_run
                                  else "DELETE" if args.confirm else "REPORT ONLY"))
    print()

    # Gate 2 — fail closed. An unset or unrecognised ENVIRONMENT is treated as
    # production, because the environment we cannot identify is the one we must
    # not damage.
    looks_prod = env == "production" or env == ""
    if looks_prod and (args.confirm and not args.dry_run) and not args.allow_prod:
        print("REFUSING: ENVIRONMENT=%r is treated as production and --allow-prod "
              "was not given." % (env or "(unset)"), file=sys.stderr)
        print("Re-run with --confirm --allow-prod if this is intended.", file=sys.stderr)
        return 3

    conn = psycopg2.connect(**dsn)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE is_mock_data = true) AS mock,
                   COUNT(*) FILTER (WHERE is_mock_data = false OR is_mock_data IS NULL) AS real
            FROM tefca_reviews
        """)
        before = cur.fetchone()
        print("  BEFORE: total=%d  mock=%d  real=%d"
              % (before["total"], before["mock"], before["real"]))

        if before["mock"] == 0:
            print("\nNothing to do — no rows with is_mock_data = true.")
            conn.rollback()
            return 0

        cur.execute("""
            SELECT qhin, COUNT(*) AS n FROM tefca_reviews
            WHERE is_mock_data = true GROUP BY qhin ORDER BY n DESC
        """)
        print("\n  Mock rows by QHIN label:")
        for r in cur.fetchall():
            print("    %-24s %d" % (r["qhin"] or "(null)", r["n"]))

        if not args.confirm and not args.dry_run:
            print("\nREPORT ONLY — nothing deleted. Re-run with --confirm to delete.")
            conn.rollback()
            return 0

        # Gate 3 — dependent findings first (FK), then the reviews themselves.
        # Both restricted to is_mock_data = true; a real row is never in scope.
        cur.execute("""
            DELETE FROM tefca_findings
            WHERE review_id IN (SELECT id FROM tefca_reviews WHERE is_mock_data = true)
        """)
        findings_deleted = cur.rowcount
        cur.execute("DELETE FROM tefca_reviews WHERE is_mock_data = true")
        reviews_deleted = cur.rowcount

        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE is_mock_data = true) AS mock,
                   COUNT(*) FILTER (WHERE is_mock_data = false OR is_mock_data IS NULL) AS real
            FROM tefca_reviews
        """)
        after = cur.fetchone()

        print("\n  DELETED: %d review(s), %d dependent finding(s)"
              % (reviews_deleted, findings_deleted))
        print("  AFTER  : total=%d  mock=%d  real=%d"
              % (after["total"], after["mock"], after["real"]))

        if after["real"] != before["real"]:
            conn.rollback()
            print("\nABORTED AND ROLLED BACK: the count of NON-mock rows changed "
                  "(%d -> %d). This must never happen; nothing was deleted."
                  % (before["real"], after["real"]), file=sys.stderr)
            return 4

        if args.dry_run:
            conn.rollback()
            print("\nDRY RUN — transaction rolled back. Nothing was actually deleted.")
        else:
            conn.commit()
            print("\nCOMMITTED at %s" % stamp)
            print("Record this run in the deployment log.")
        return 0
    except Exception as e:
        conn.rollback()
        print("\nERROR — rolled back, nothing deleted: %s" % e, file=sys.stderr)
        return 5
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
