"""Synthetic performance run for the entity-intelligence foundation.

    python scripts/ei_perf.py --sizes 2000 5000 10000 25000 50000 --json out.json

Builds a synthetic NPPES V2 bundle (main file at full 330-column width, Other
Name and Practice Location reference files) of N organisations, then for every
organisation builds a synthetic program delivery and runs the full pipeline:
parse → bundle → adapter observations → comparison → delta (vs a prior
delivery) → System Evidence Assessment.

NO real data: every NPI is in the reserved synthetic range 9999xxxxxx, every
name is "SYNTHETIC ORG n", every address is on "n SYNTHETIC WAY". Feature
flags are set on the in-process settings object only. Nothing is written to
any database or network.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import sys
import time
import tracemalloc
from collections import Counter


def rss_mb() -> float:
    """Resident set size in MB without psutil: Windows via psapi, POSIX via /proc."""
    try:
        import ctypes, ctypes.wintypes  # noqa
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        h = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / 1e6, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1000, 1)
    except OSError:
        pass
    return -1.0

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("ENTITY_INTELLIGENCE_ENABLED", "true")
os.environ.setdefault("NPPES_IDENTITY_CORROBORATION_ENABLED", "true")

from app.core.config import settings  # noqa: E402
settings.ENTITY_INTELLIGENCE_ENABLED = True
settings.NPPES_IDENTITY_CORROBORATION_ENABLED = True

from app.core.entity_intelligence.service import EntityIntelligenceService  # noqa: E402
from app.evidence_sources.nppes_v2.adapter import NppesV2Adapter, NppesV2Bundle, SOURCE_ID  # noqa: E402
from ei_fixtures import MAIN_HEADER, OTHER_NAME_HEADER, PL_HEADER, delivered, main_row, other_name_row, pl_row  # noqa: E402

SUFFIXES = ["LLC", "INC", "CORP", "PLLC", "LTD"]


def _addr(i: int, city="BALTIMORE", zip5="21201"):
    return {"line1": f"{i} SYNTHETIC WAY", "line2": "STE 100" if i % 3 == 0 else "", "city": city,
            "state": "MD", "postal_code": f"{zip5}0000"}


def build(n: int, seed: int = 7):
    rng = random.Random(seed)  # nosec B311 — synthetic test-data mix, not security
    main_rows, on_rows, pl_rows = [], [], []
    deliveries = {}
    kinds = Counter()
    for i in range(n):
        npi = f"9999{i:06d}"
        legal = f"SYNTHETIC ORG {i} {SUFFIXES[i % len(SUFFIXES)]}"
        primary = _addr(i)
        # Type 1 noise rows (skipped by the parser) at ~10%
        if i % 10 == 9:
            main_rows.append(main_row(f"8888{i:06d}", "", entity_type="1"))
        main_rows.append(main_row(npi, legal, practice=primary, mailing=_addr(i, "ROCKVILLE", "20850")))
        r = rng.random()
        if r < 0.70:                       # exact delivery
            d = delivered(legal, primary, npi, entity_id=npi); kinds["exact"] += 1
        elif r < 0.85:                     # delivered under a DBA recorded in NPPES
            dba = f"SYNTHETIC CLINIC {i}"
            on_rows.append(other_name_row(npi, dba, "3"))
            d = delivered(dba, primary, npi, entity_id=npi); kinds["dba"] += 1
        elif r < 0.95:                     # delivered at an additional practice location
            extra = _addr(i + 700000, "FREDERICK", "21701")
            pl_rows.append(pl_row(npi, extra))
            d = delivered(legal, extra, npi, entity_id=npi); kinds["additional_location"] += 1
        else:                              # conflicting name and address
            d = delivered(f"UNRELATED ENTITY {i}", _addr(i + 900000, "DOVER", "19901"), npi, entity_id=npi)
            kinds["conflict"] += 1
        deliveries[npi] = d
    return main_rows, on_rows, pl_rows, deliveries, kinds


def to_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def run(n: int) -> dict:
    from app.core.entity_intelligence.evidence_plan import SourceRecord, plan_evidence
    t0 = time.perf_counter()
    main_rows, on_rows, pl_rows, deliveries, kinds = build(n)
    # 25K SOURCE RECORDS != 25K REVIEW CASES: 30% duplicate source records resolve to the same candidates
    records = [SourceRecord(f"r{i}", npi=npi, name=d[0].observed_value["name"], address=next((o.observed_value for o in d if o.observation_type.value == "LOCATION"), None))
               for i, (npi, d) in enumerate(deliveries.items())]
    records += [SourceRecord(f"dup{i}", npi=r.npi, name=r.name, address=r.address) for i, r in enumerate(records[: int(n * 0.3)])]
    tp = time.perf_counter(); plan = plan_evidence(records); plan_s = time.perf_counter() - tp
    rss_before = rss_mb()
    main_text, on_text, pl_text = to_csv(MAIN_HEADER, main_rows), to_csv(OTHER_NAME_HEADER, on_rows), to_csv(PL_HEADER, pl_rows)
    t1 = time.perf_counter()
    tracemalloc.start()
    bundle = NppesV2Bundle.from_texts(main_text=main_text, other_name_text=on_text,
                                      practice_location_text=pl_text, dataset_version=f"SYNTHETIC_{n}")
    t2 = time.perf_counter()
    adapter = NppesV2Adapter(bundle)
    svc = EntityIntelligenceService()
    obs_count = 0
    assessments = Counter()
    for npi, d in deliveries.items():
        obs = adapter.observations_for(canonical_entity_id=npi, identifier=npi)
        obs_count += len(obs)
        current = d + obs
        # prior delivery = the same delivery (a stable entity) → deltas mostly UNCHANGED
        run_ = svc.evaluate(canonical_entity_id=npi, current=current, prior=current, source_ids=[SOURCE_ID])
        assessments[run_.assessment.assessment.value] += 1
    t3 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = rss_mb()
    review_candidates = n - assessments.get("EVIDENCE_CORROBORATES", 0)
    comparisons = sum(3 for _ in deliveries)   # identifier + name + location per entity per source
    return {"n": n, "main_file_mb": round(len(main_text) / 1e6, 1),
            "source_records": plan.source_record_count, "canonical_candidates": plan.canonical_candidate_count,
            "duplicates_removed": plan.duplicate_record_count, "plan_s": round(plan_s, 3),
            "deduplicated_lookups": dict(plan.lookups_by_source), "comparisons": comparisons,
            "entities_per_sec": round(n / max(1e-9, (t3 - t2)), 1), "rss_mb_before": rss_before, "rss_mb_after": rss_after,
            "rows_read": bundle.reports[0].rows_read, "rows_skipped_type1": bundle.reports[0].rows_skipped,
            "parse_status": [r.status for r in bundle.reports],
            "build_synthetic_s": round(t1 - t0, 2), "parse_bundle_s": round(t2 - t1, 2),
            "evaluate_all_s": round(t3 - t2, 2), "evaluate_per_entity_ms": round((t3 - t2) / n * 1000, 3),
            "observations": obs_count, "peak_mb": round(peak / 1e6, 1),
            "delivery_mix": dict(kinds), "assessments": dict(assessments),
            "human_review_candidates": review_candidates,
            "human_review_pct": round(review_candidates / n * 100, 1)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--sizes", nargs="+", type=int, default=[2000, 5000, 10000, 25000])
    p.add_argument("--json")
    a = p.parse_args(argv)
    results = [run(n) for n in a.sizes]
    print("Domain processing performance — synthetic data only. External source acquisition performance is not represented.")
    print(f"{'N':>7} {'records':>8} {'canon':>7} {'plan s':>7} {'parse s':>8} {'eval s':>8} {'ms/ent':>7} {'ent/s':>8} {'peak MB':>8} {'RSS MB':>7} {'review%':>8}")
    for r in results:
        print(f"{r['n']:>7} {r['source_records']:>8} {r['canonical_candidates']:>7} {r['plan_s']:>7} {r['parse_bundle_s']:>8} "
              f"{r['evaluate_all_s']:>8} {r['evaluate_per_entity_ms']:>7} {r['entities_per_sec']:>8} {r['peak_mb']:>8} "
              f"{r['rss_mb_after']:>7} {r['human_review_pct']:>8}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
