"""Synthetic demonstration cases A-D (+ identity limitations) for issue history.

Not a test module. Used by test_issue_history_cases_2026_10_08.py (rolled-back
database) and by scripts/dump_issue_history_cases.py (a LOCAL database only) so
the screenshots show exactly what the tests assert. All data is synthetic: OIDs
under the unassigned 9.99.777 arc, NPIs are test values.

Every case has its OWN feed tag so that one case's deliveries never appear as
"record absent" gaps in another's history.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict

from issue_history_support_2026_10_07 import (BAD_LEN_NPI, GOOD_NPI, entity_row,
                                              filler_row, run_engine, seed_delivery)

GOOD_NPI_2 = "1245319599"        # Luhn-valid, different from GOOD_NPI

FEEDS = {"A": "SYN-CASE-A", "B": "SYN-CASE-B", "C": "SYN-CASE-C", "D": "SYN-CASE-D",
         "E": "SYN-CASE-E"}
OIDS = {"A": "9.99.777.100.1", "B": "9.99.777.100.2", "C": "9.99.777.100.3",
        "D1": "9.99.777.100.41", "D2": "9.99.777.100.42", "E": "9.99.777.100.5"}


async def _delivery(db, month: int, feed: str, rows, day: int = 5, process: bool = True):
    tag = f"{feed}-{month}"
    iid = await seed_delivery(db, list(rows) + [filler_row(tag)],
                              received_at=datetime(2026, month, day), feed=feed)
    if process:
        await run_engine(db, iid)
    return iid


async def seed_case_a(db) -> Dict[str, str]:
    """July invalid NPI -> August comparable pass -> September invalid NPI: RECURRING."""
    f, o = FEEDS["A"], OIDS["A"]
    jul = await _delivery(db, 7, f, [entity_row(o, npi=BAD_LEN_NPI)])
    aug = await _delivery(db, 8, f, [entity_row(o, npi=GOOD_NPI)])
    sep = await _delivery(db, 9, f, [entity_row(o, npi=BAD_LEN_NPI)])
    return {"jul": jul, "aug": aug, "sep": sep}


async def seed_case_b(db) -> Dict[str, str]:
    """July invalid NPI -> NO August delivery -> September invalid NPI."""
    f, o = FEEDS["B"], OIDS["B"]
    jul = await _delivery(db, 7, f, [entity_row(o, npi=BAD_LEN_NPI)])
    sep = await _delivery(db, 9, f, [entity_row(o, npi=BAD_LEN_NPI)])
    return {"jul": jul, "sep": sep}


async def seed_case_c(db) -> Dict[str, str]:
    """July NPI A -> September NPI B under the SAME record id."""
    f, o = FEEDS["C"], OIDS["C"]
    jul = await _delivery(db, 7, f, [entity_row(o, npi=GOOD_NPI)])
    sep = await _delivery(db, 9, f, [entity_row(o, npi=GOOD_NPI_2)])
    return {"jul": jul, "sep": sep}


async def seed_case_d(db) -> Dict[str, str]:
    """The SAME NPI under two DIFFERENT record ids: separate histories."""
    f = FEEDS["D"]
    rows = [entity_row(OIDS["D1"], npi=GOOD_NPI), entity_row(OIDS["D2"], npi=GOOD_NPI)]
    jul = await _delivery(db, 7, f, rows)
    sep = await _delivery(db, 9, f, rows)
    return {"jul": jul, "sep": sep}


async def seed_case_e(db) -> Dict[str, str]:
    """Identity limitations: a duplicate record id (July), a row with no record id
    (September), and a clean August between them."""
    f, o = FEEDS["E"], OIDS["E"]
    dup = entity_row(o, npi=BAD_LEN_NPI)
    jul = await _delivery(db, 7, f, [entity_row(o, npi=BAD_LEN_NPI), dup])
    aug = await _delivery(db, 8, f, [entity_row(o, npi=GOOD_NPI)])
    noid = entity_row("x", npi=GOOD_NPI)   # shares the August NPI
    noid["id"] = ""
    sep = await _delivery(db, 9, f, [entity_row(o, npi=BAD_LEN_NPI), noid])
    return {"jul": jul, "aug": aug, "sep": sep}


FEEDS["F1"], FEEDS["F2"] = "SYN-CASE-F1", "SYN-CASE-F2"
OIDS["F1"], OIDS["F2"] = "9.99.777.100.71", "9.99.777.100.72"


async def seed_case_f(db) -> Dict[str, str]:
    """The SAME NPI under record ids in two DIFFERENT feeds (cross-feed association)."""
    a = await _delivery(db, 7, FEEDS["F1"], [entity_row(OIDS["F1"], npi=GOOD_NPI)])
    b = await _delivery(db, 8, FEEDS["F2"], [entity_row(OIDS["F2"], npi=GOOD_NPI)])
    return {"a": a, "b": b}


SEEDERS = {"A": seed_case_a, "B": seed_case_b, "C": seed_case_c, "D": seed_case_d,
           "E": seed_case_e, "F": seed_case_f}
