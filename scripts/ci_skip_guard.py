"""Fail a database-backed CI job when a test was SKIPPED and the skip is not explicitly permitted.

WHY THIS EXISTS
    The isolation-postgres job used `grep -q 'No database reachable'` on the JUnit
    file. That is case-sensitive, so skips worded "no database reachable ..." or
    "no sandbox database ..." slipped through and the job reported green with three
    tests never run (2026-11-12, run 43176884516). A green job must mean the
    database-required tests RAN.

POLICY
    In a job that provides a database, every skip is a failure UNLESS the exact test
    id appears in PERMITTED_SKIPS below, each with the recorded reason. The list is
    deliberately explicit and short; adding to it is a reviewed change, and an entry
    whose test no longer skips is reported (not failed) so the list can be pruned.

    Matching is by test id (classname + name), not by message, so rewording a skip
    message can neither hide a new skip nor invalidate a permitted one.

USAGE
    python scripts/ci_skip_guard.py isolation-results.xml
    exit 0 = no un-permitted skips; exit 1 = un-permitted skips (listed).
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, List, Tuple

#: test id (as "<module path>::<test name>") -> why this skip is currently accepted.
#: These are KNOWN ENVIRONMENT GAPS in the CI job, not tests that are designed to skip.
PERMITTED_SKIPS: Dict[str, str] = {
    "tests/test_qhin_sampling_operational.py::test_two_concurrent_finalizations_produce_one_official_sample":
        "sandbox fixture connects to a database named 'docuaction' that the CI cluster does not "
        "create; the concurrency property is NOT verified in this job",
    "tests/test_iqvia_affil_org_indexes_2026_10_08.py::test_org_indexes_exist_are_partial_owned_and_used":
        "throwaway-database migration test needs psycopg2, which the CI environment lacks; "
        "NOT verified in this job",
    "tests/test_iqvia_affil_org_indexes_2026_10_08.py::test_interrupted_build_leaves_an_invalid_index_that_is_detected_and_repaired":
        "same psycopg2 gap as above; NOT verified in this job",
}


def _test_id(case: ET.Element) -> str:
    classname = case.get("classname", "")
    name = case.get("name", "")
    # junit classname is dotted module path (tests.test_x) possibly with a class suffix.
    parts = classname.split(".")
    module_parts: List[str] = []
    cls_parts: List[str] = []
    for part in parts:
        (cls_parts if (module_parts and part[:1].isupper()) else module_parts).append(part)
    module = "/".join(module_parts) + ".py"
    return "::".join([module, *cls_parts, name])


def find_skips(root: ET.Element) -> List[Tuple[str, str]]:
    out = []
    for case in root.iter("testcase"):
        sk = case.find("skipped")
        if sk is not None:
            out.append((_test_id(case), sk.get("message") or (sk.text or "").strip()))
    return out


def evaluate(skips: Iterable[Tuple[str, str]],
             permitted: Dict[str, str] = PERMITTED_SKIPS) -> Tuple[List[Tuple[str, str]], List[str]]:
    """(unpermitted skips, permitted entries that did not skip)."""
    skips = list(skips)
    seen = {tid for tid, _ in skips}
    bad = [(tid, msg) for tid, msg in skips if tid not in permitted]
    stale = sorted(t for t in permitted if t not in seen)
    return bad, stale


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("usage: ci_skip_guard.py <junit.xml>", file=sys.stderr)
        return 2
    skips = find_skips(ET.parse(argv[1]).getroot())
    bad, stale = evaluate(skips)
    for tid, msg in skips:
        if tid in PERMITTED_SKIPS:
            print(f"::warning::permitted skip (NOT verified in this job): {tid} -- {PERMITTED_SKIPS[tid]}")
    for tid in stale:
        print(f"::notice::permitted skip no longer skips; remove it from PERMITTED_SKIPS: {tid}")
    if bad:
        for tid, msg in bad:
            print(f"::error::un-permitted skip in a database-backed job: {tid} -- {msg}")
        return 1
    print(f"ok: {len(skips)} skip(s), all explicitly permitted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
