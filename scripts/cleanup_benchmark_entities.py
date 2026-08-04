#!/usr/bin/env python3
"""
Remove the synthetic registry entities left behind by the Block 6 load benchmark.

Background: docs/audit/PERFORMANCE_BASELINE.md (lines 104-125). The benchmark grew
the dev registry from 71 entities to 22,274; 22,172 of those are synthetic `draft`
rows. GET /registry/stats degraded from ~1.8s to 5.38s as a result. The cleanup was
deliberately NOT run automatically -- "bulk deletion against a shared environment is
a deliberate act, not a test teardown".

Targets entities whose TEFCAID starts with one of:
    TID-P100-      TID-P1000-      TID-P10000-      TID-TH
The trailing hyphens make the first three mutually exclusive: "TID-P1000-0001" does
not start with "TID-P100-" because position 8 is "0", not "-".

DRY RUN IS THE DEFAULT. Nothing is deleted without --execute.

Deletion is SOFT (app/tefca_registry/routes.py:356) -- rows are retained with
is_deleted/deleted_at set, so review_records, tefca_verifications and sample_entities
keep their referent. Deleted rows drop out of listings, stats and the sample frame.

Usage
    export DOCUACTION_ADMIN_TOKEN='<admin access_token>'
    python scripts/cleanup_benchmark_entities.py                  # dry run
    python scripts/cleanup_benchmark_entities.py --execute        # perform deletion
    python scripts/cleanup_benchmark_entities.py --execute --max 100   # cautious first batch
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent import futures
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://docuaction-dev.azurewebsites.net"
REGISTRY = "/api/tefca/registry"

# Synthetic-entity TEFCAID prefixes from the Block 6 benchmark.
PREFIXES = ["TID-P100-", "TID-P1000-", "TID-P10000-", "TID-TH"]

PAGE_SIZE = 500  # server caps limit at 500 (routes.py:66)
DELETE_REASON = "Block 6 benchmark cleanup - synthetic load-test entity"


def api(method, url, token=None, payload=None, timeout=60, retries=4):
    """JSON HTTP call with retry on transient network faults.

    Azure intermittently resets or times out connections under concurrency. Those
    surface as raw OSError (ConnectionResetError / WinError 10060) rather than
    urllib.error.URLError, so they must be caught as OSError -- an earlier version
    let ConnectionResetError escape, which killed a 21,800-item run at item 3,050.
    Transient faults are retried with exponential backoff; HTTP status codes are
    returned as-is and never retried (409 in particular is meaningful).
    """
    data = json.dumps(payload).encode() if payload is not None else None
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if token:
            req.add_header("Authorization", "Bearer %s" % token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw) if raw else None
                except ValueError:
                    return resp.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw
        except (urllib.error.URLError, OSError) as e:
            last = getattr(e, "reason", e)
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
    return 0, "connection error after %d retries: %s" % (retries, last)


def detail(body):
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("error") or body)
    return str(body)


def classify(tefcaid):
    """Return the matching prefix, or None. Prefixes are mutually exclusive."""
    if not tefcaid:
        return None
    for p in PREFIXES:
        if tefcaid.startswith(p):
            return p
    return None


def fetch_all_entities(base_url, token):
    """Page through the full entity list, collecting id / name / tefcaid.

    The whole inventory is collected BEFORE any deletion. Deleting while paging
    would shift offsets under us and silently skip rows, since soft-deleted
    entities drop out of the result set (queries.py:112-113).
    """
    collected, offset, total = [], 0, None
    while True:
        url = "%s%s/entities?%s" % (base_url, REGISTRY, urllib.parse.urlencode(
            {"limit": PAGE_SIZE, "offset": offset}))
        status, body = api("GET", url, token=token)
        if status != 200:
            print("ERROR: GET /entities offset=%d returned %s: %s"
                  % (offset, status, detail(body)), file=sys.stderr)
            if status == 401:
                print("  Token invalid or expired.", file=sys.stderr)
            elif status == 403:
                print("  Token lacks the required role.", file=sys.stderr)
            return None, None
        if total is None:
            total = int(body.get("total") or 0)
            print("Registry reports %d live entities." % total)
        items = body.get("items") or []
        if not items:
            break
        for it in items:
            collected.append({"id": it.get("id"), "name": it.get("name"),
                              "tefcaid": it.get("tefcaid")})
        offset += len(items)
        print("\r  fetched %d/%s ..." % (len(collected), total), end="", file=sys.stderr)
        if offset >= total:
            break
    print("", file=sys.stderr)
    return collected, total


def timed_stats(base_url, token, label):
    t = time.time()
    status, body = api("GET", "%s%s/stats" % (base_url, REGISTRY), token=token)
    elapsed = time.time() - t
    if status != 200:
        print("  %s: stats returned %s (%s)" % (label, status, detail(body)))
        return None, elapsed
    count = None
    if isinstance(body, dict):
        for k in ("total_entities", "entities", "total", "entity_count"):
            if isinstance(body.get(k), int):
                count = body[k]
                break
    print("  %s: %.2fs%s" % (label, elapsed,
                             "" if count is None else "  (entities: %d)" % count))
    return count, elapsed


def main():
    p = argparse.ArgumentParser(description="Soft-delete Block 6 benchmark entities.")
    p.add_argument("--base-url", default=os.environ.get("DOCUACTION_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--execute", action="store_true",
                   help="Actually delete. Without this the script only reports.")
    p.add_argument("--max", type=int, default=0, metavar="N",
                   help="Delete at most N entities this run (0 = no cap). Use for a cautious first batch.")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    p.add_argument("--workers", type=int, default=1, metavar="N",
                   help="Concurrent delete requests (default 1). Each DELETE costs ~2.3s "
                        "server-side, so serial throughput is ~0.4/s. Keep this modest: the "
                        "environment is shared with whoever else is using dev.")
    p.add_argument("--allow-nondev", action="store_true",
                   help="Required to target a host whose name does not contain 'dev'.")
    args = p.parse_args()

    base_url = args.base_url.rstrip("/")
    host = urllib.parse.urlparse(base_url).hostname or ""
    if "dev" not in host and not args.allow_nondev:
        print("REFUSING: host %r does not look like a dev environment." % host, file=sys.stderr)
        print("Re-run with --allow-nondev if this is intentional.", file=sys.stderr)
        return 2

    token = os.environ.get("DOCUACTION_ADMIN_TOKEN", "").strip()
    if not token:
        print("ERROR: set DOCUACTION_ADMIN_TOKEN (every registry endpoint requires auth).",
              file=sys.stderr)
        return 2

    print("Environment : %s" % base_url)
    print("Mode        : %s" % ("EXECUTE - will soft-delete" if args.execute
                                else "DRY RUN - nothing will be deleted"))
    print("Patterns    : %s" % ", ".join(p_ + "*" for p_ in PREFIXES))
    print()

    print("Baseline:")
    before_count, before_ms = timed_stats(base_url, token, "GET /registry/stats")
    print()

    print("Inventory (full pagination before any change):")
    entities, total = fetch_all_entities(base_url, token)
    if entities is None:
        return 1

    buckets = {p_: [] for p_ in PREFIXES}
    survivors, no_tefcaid = [], []
    for e in entities:
        pref = classify(e.get("tefcaid"))
        if pref:
            buckets[pref].append(e)
        elif e.get("tefcaid"):
            survivors.append(e)
        else:
            no_tefcaid.append(e)

    doomed = [e for p_ in PREFIXES for e in buckets[p_]]

    print()
    print("Counts per pattern:")
    print("  %-16s %8s" % ("PATTERN", "COUNT"))
    for p_ in PREFIXES:
        print("  %-16s %8d" % (p_ + "*", len(buckets[p_])))
    print("  %-16s %8d" % ("(matched total)", len(doomed)))
    print()
    print("  %-16s %8d   <- kept: TEFCAID matches no pattern" % ("survivors", len(survivors)))
    print("  %-16s %8d   <- kept: no TEFCAID attached" % ("no TEFCAID", len(no_tefcaid)))
    print("  %-16s %8d" % ("scanned total", len(entities)))
    print()
    print("After cleanup the registry would hold %d live entities."
          % (len(survivors) + len(no_tefcaid)))

    for p_ in PREFIXES:
        if buckets[p_]:
            sample = buckets[p_][0]
            print("  e.g. %s -> %s / %s" % (p_ + "*", sample.get("tefcaid"), sample.get("name")))

    if not args.execute:
        print()
        print("DRY RUN complete. Nothing was deleted.")
        print("Re-run with --execute to perform the soft-delete.")
        if doomed:
            print("Estimated wall-clock at ~0.6s/request: %.0f min for %d deletions."
                  % (len(doomed) * 0.6 / 60.0, len(doomed)))
        return 0

    if not doomed:
        print("\nNothing matches. Nothing to do.")
        return 0

    targets = doomed[:args.max] if args.max else doomed
    print()
    print("About to SOFT-DELETE %d entities (of %d matched)." % (len(targets), len(doomed)))
    print("Each becomes is_deleted=True and drops out of listings, stats and the sample frame.")
    print("Rows are retained, so prior classifications keep their referent.")
    if not args.yes:
        try:
            if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1

    counters = {"deleted": 0, "already": 0, "failed": 0, "done": 0}
    lock = threading.Lock()
    abort = threading.Event()
    started = time.time()
    reason = urllib.parse.urlencode({"reason": DELETE_REASON})

    def delete_one(e):
        if abort.is_set():
            return
        url = "%s%s/entities/%s?%s" % (base_url, REGISTRY, e["id"], reason)
        try:
            status, body = api("DELETE", url, token=token)
        except Exception as exc:
            # Belt-and-braces: an exception escaping a worker propagates out of
            # pool.map and tears down the whole run. Never let that happen -- a
            # single bad row must not cost the other 20,000.
            status, body = 0, "unhandled: %r" % (exc,)
        with lock:
            if status == 200:
                counters["deleted"] += 1
            elif status == 409:
                # Endpoint is deliberately not idempotent-silent (routes.py:376-378):
                # 409 means the row was already deleted, which is not an error here.
                counters["already"] += 1
            else:
                counters["failed"] += 1
                print("\n  FAIL %s (%s): %s" % (e.get("tefcaid"), status, detail(body)))
                if status in (401, 403):
                    print("  Token rejected - stopping.", file=sys.stderr)
                    abort.set()
                elif counters["failed"] >= 50:
                    print("  50 failures - stopping to avoid a runaway.", file=sys.stderr)
                    abort.set()
            counters["done"] += 1
            n = counters["done"]
            if n % 50 == 0 or n == len(targets):
                rate = n / max(time.time() - started, 0.001)
                eta = (len(targets) - n) / rate / 60.0 if rate else 0
                print("\r  %d/%d  deleted=%d already=%d failed=%d  (%.1f/s, ETA %.0f min)"
                      % (n, len(targets), counters["deleted"], counters["already"],
                         counters["failed"], rate, eta), end="", file=sys.stderr)

    if args.workers > 1:
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(delete_one, targets))
    else:
        for e in targets:
            if abort.is_set():
                break
            delete_one(e)
    print("", file=sys.stderr)
    deleted, already, failed = counters["deleted"], counters["already"], counters["failed"]

    print()
    print("Deletion complete in %.1f min: deleted=%d already-deleted=%d failed=%d"
          % ((time.time() - started) / 60.0, deleted, already, failed))
    if args.max and len(doomed) > len(targets):
        print("%d matched entities remain (--max cap). Re-run to continue."
              % (len(doomed) - len(targets)))

    print()
    print("Verification:")
    after_count, after_ms = timed_stats(base_url, token, "GET /registry/stats")
    if before_ms and after_ms:
        print("  latency: %.2fs -> %.2fs" % (before_ms, after_ms))
    if before_count is not None and after_count is not None:
        print("  entities: %d -> %d" % (before_count, after_count))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
