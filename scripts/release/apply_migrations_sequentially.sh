#!/usr/bin/env bash
# Apply every pending Alembic revision between DEV's current revision and the repository head,
# ONE revision per `alembic upgrade` (one transaction each), verifying the ACTUAL database
# revision before and after every step, and stopping at the first problem.
#
# Same guarantees as the single-target apply this extends, per revision:
#   * identity: session_user is the dedicated migration identity, current_user is DB_MIGRATION_ROLE
#   * the revision read from the DATABASE (not from a log line) equals what was expected
#   * a second upgrade to the same revision is a true no-op
#   * runtime privileges on rce_delivery_jobs stay S/I/U with no DELETE, owned by the migration role
# Extra for the sequence:
#   * the plan must be a single linear chain from EXPECTED_CURRENT to head (no branch, no gap)
#   * the final revision must equal head, or the script fails
# Required env: EXPECTED_CURRENT PGHOST PGDATABASE PG_PRINCIPAL DB_MIGRATION_ROLE DB_APP_ROLE
# Optional env: MIGRATION_SSL (default require), PGPORT, PYTHON, GITHUB_OUTPUT, GITHUB_STEP_SUMMARY
set -euo pipefail

: "${EXPECTED_CURRENT:?}" "${PGHOST:?}" "${PGDATABASE:?}" "${PG_PRINCIPAL:?}" "${DB_MIGRATION_ROLE:?}" "${DB_APP_ROLE:?}"
SSL="${MIGRATION_SSL:-require}"
PY="${PYTHON:-python3}"
export DB_MIGRATION_ROLE DB_APP_ROLE PGHOST PGDATABASE PG_PRINCIPAL MIGRATION_SSL="$SSL"

summary() { if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then echo "$1" >> "$GITHUB_STEP_SUMMARY"; fi; }
out()     { if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1" >> "$GITHUB_OUTPUT"; fi; }

mint_token() {
  PGTOKEN="$(az account get-access-token --resource https://ossrdbms-aad.database.windows.net --query accessToken -o tsv)"
  test -n "$PGTOKEN" || { echo "::error::no PostgreSQL access token"; exit 1; }
  export PGTOKEN
  export DATABASE_URL="postgresql://${PG_PRINCIPAL}:${PGTOKEN}@${PGHOST}:${PGPORT:-5432}/${PGDATABASE}?ssl=${SSL}"
}

actual_revision() {
  # Read from the database through alembic, as the migration role. Fail unless it is exactly one row.
  local rows
  rows="$(alembic current 2>/dev/null | awk 'NF && $1 !~ /^INFO/ {print $1}')"
  if [ "$(echo "$rows" | grep -c .)" -ne 1 ]; then
    echo "::error::expected exactly one current revision, got: '$rows'" >&2
    return 1
  fi
  echo "$rows"
}

identity_check() {
  "$PY" - <<'PY'
import asyncio, os, asyncpg
async def m():
    c = await asyncpg.connect(host=os.environ["PGHOST"], port=int(os.environ.get("PGPORT", "5432")),
                              database=os.environ["PGDATABASE"], user=os.environ["PG_PRINCIPAL"],
                              password=os.environ["PGTOKEN"], ssl=os.environ["MIGRATION_SSL"],
                              server_settings={"role": os.environ["DB_MIGRATION_ROLE"]})
    r = await c.fetchrow("select session_user, current_user"); await c.close()
    print("session_user =", r[0], "| current_user =", r[1])
    assert r[0] == os.environ["PG_PRINCIPAL"], "session_user is not the dedicated migration identity"
    assert r[1] == os.environ["DB_MIGRATION_ROLE"], "current_user is not the migration role - refusing to run DDL"
asyncio.run(m())
PY
}

privilege_invariant() {
  "$PY" - <<'PY'
import asyncio, os, asyncpg
async def m():
    c = await asyncpg.connect(host=os.environ["PGHOST"], port=int(os.environ.get("PGPORT", "5432")),
                              database=os.environ["PGDATABASE"], user=os.environ["PG_PRINCIPAL"],
                              password=os.environ["PGTOKEN"], ssl=os.environ["MIGRATION_SSL"])
    try:
        exists = await c.fetchval("select to_regclass('public.rce_delivery_jobs') is not null")
        if not exists:
            print("rce_delivery_jobs not present - privilege invariant not applicable"); return
        got = {}
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            got[p] = await c.fetchval("select has_table_privilege($1,'rce_delivery_jobs',$2)", os.environ["DB_APP_ROLE"], p)
        owner = await c.fetchval("select tableowner from pg_tables where tablename='rce_delivery_jobs'")
        print("rce_delivery_jobs privileges:", got, "| owner:", owner)
        assert got == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}, "runtime privileges are not the approved least-privilege set"
        assert owner == os.environ["DB_MIGRATION_ROLE"], "rce_delivery_jobs is not owned by the migration role"
    finally:
        await c.close()
asyncio.run(m())
PY
}

# ---- 1. the plan: a strictly linear chain EXPECTED_CURRENT -> head, from the repository (offline) ----
PLAN="$(EXPECTED_CURRENT="$EXPECTED_CURRENT" "$PY" - <<'PY'
import os, sys
from alembic.config import Config
from alembic.script import ScriptDirectory
sd = ScriptDirectory.from_config(Config("alembic.ini"))
heads = sd.get_heads()
if len(heads) != 1:
    sys.exit("expected exactly one head, found %s" % (heads,))
head, cur = heads[0], os.environ["EXPECTED_CURRENT"]
try:
    known = sd.get_revision(cur) is not None
except Exception:
    known = False
if not known:
    sys.exit("EXPECTED_CURRENT %s is not a revision in this repository" % cur)
chain, rev = [], sd.get_revision(head)
while rev.revision != cur:
    chain.append(rev.revision)
    down = rev.down_revision
    if isinstance(down, tuple):
        if len(down) != 1:
            sys.exit("%s is a merge point %s; refusing to automate it" % (rev.revision, down))
        down = down[0]
    if down is None:
        sys.exit("%s is not an ancestor of head %s" % (cur, head))
    rev = sd.get_revision(down)
chain.reverse()
print(" ".join(chain))
PY
)"

if [ -z "$PLAN" ]; then
  echo "Nothing to apply: the expected revision $EXPECTED_CURRENT is already the repository head."
  out "applied=false"
  exit 0
fi
HEAD_REV="${PLAN##* }"
COUNT="$(echo "$PLAN" | wc -w | tr -d ' ')"
echo "Plan ($EXPECTED_CURRENT -> $HEAD_REV), $COUNT revision(s): $PLAN"
summary "### Sequential migration plan"
summary "$EXPECTED_CURRENT -> $HEAD_REV: $COUNT revision(s): $PLAN"

# ---- 2. the start state, read from the database, must be the operator's expectation ----
mint_token
START="$(actual_revision)"
if [ "$START" != "$EXPECTED_CURRENT" ]; then
  echo "::error::DEV is at $START, operator expected $EXPECTED_CURRENT - nothing executed"
  exit 1
fi

# ---- 3. one revision at a time; any failure stops the whole sequence ----
PREV="$START"; N=0
for TARGET in $PLAN; do
  N=$((N+1)); mint_token
  echo "=== [$N/$COUNT] $PREV -> $TARGET ==="
  BEFORE="$(actual_revision)"
  if [ "$BEFORE" != "$PREV" ]; then echo "::error::before step $N DEV is at $BEFORE, expected $PREV - stopping"; exit 1; fi
  identity_check
  alembic upgrade "$TARGET"
  AFTER="$(actual_revision)"
  if [ "$AFTER" != "$TARGET" ]; then echo "::error::after step $N DEV is at $AFTER, expected $TARGET - stopping"; exit 1; fi
  echo "--- second run must be a true no-op ---"
  alembic upgrade "$TARGET" 2>&1 | tee "${TMPDIR:-/tmp}/second_$N.log"
  if grep -q "Running upgrade" "${TMPDIR:-/tmp}/second_$N.log"; then
    echo "::error::second alembic upgrade $TARGET was not a no-op - stopping"; exit 1
  fi
  privilege_invariant
  summary "- [$N/$COUNT] $PREV -> $TARGET verified (database revision read back, second run a no-op, privilege invariant held)"
  out "dev_revision_after=$TARGET"
  PREV="$TARGET"
done

# ---- 4. only the head is acceptable ----
FINAL="$(actual_revision)"
if [ "$FINAL" != "$HEAD_REV" ]; then echo "::error::finished at $FINAL, repository head is $HEAD_REV"; exit 1; fi
if [ "$(alembic heads | grep -c '(head)')" -ne 1 ]; then echo "::error::multiple heads after upgrade"; exit 1; fi
out "applied=true"; out "dev_revision_after=$FINAL"; out "applied_count=$N"
echo "All $N revision(s) applied; DEV is at head $FINAL."
