"""OFFLINE dry run: how many records would the SAM daily-extract path flag, before anyone enables it.

Reads an RCE/ONC snapshot CSV and a GSA public exclusions extract ZIP from local paths. Writes AGGREGATE COUNTS ONLY:
no names, NPIs or other identifiers appear in the output. Touches no database, network, flag or application path.

  python -I scripts/sam_extract_dry_run.py --snapshot X.csv --extract Y.ZIP [--out report.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.Tefca.sam_exclusions_extract import load_extract  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--extract", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    idx = load_extract(Path(a.extract))
    outcome, reason, by, ids_tried = Counter(), Counter(), Counter(), Counter()
    rows = actions_total = ambiguous = multi_action_single_identity = 0
    npi_present = npi_hit_rows = 0
    flagged_identities: set = set()
    with open(a.snapshot, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows += 1
            npi = (r.get("NPI") or "").strip()
            npi_present += bool(npi)
            res = idx.screen(npi=npi, name=(r.get("name") or "").strip(),
                             state=(r.get("address_state") or "").strip(),
                             zip5=(r.get("address_postalCode") or "").strip()[:5])
            outcome[res.outcome] += 1
            reason[res.reason] += 1
            by[res.matched_by] += 1
            ids_tried["+".join(res.provenance.get("identifiers_tried", [])) or "none"] += 1
            if res.action_count:
                actions_total += res.action_count
                ambiguous += res.distinct_identities > 1
                multi_action_single_identity += res.distinct_identities == 1 and res.action_count > 1
                npi_hit_rows += res.matched_by == "npi"
                flagged_identities.update(i.identity_key for i in res.identities)
    rep = {"extract": idx.meta.anchor(), "snapshot_rows": rows, "snapshot_rows_with_npi": npi_present,
           "outcome": dict(outcome), "reason": dict(reason), "matched_by": dict(by),
           "identifiers_tried": dict(ids_tried),
           "rows_with_any_hit": outcome["POTENTIAL_MATCH"] + outcome["IDENTIFIER_MATCH"],
           "exclusion_actions_attached": actions_total,
           "distinct_excluded_identities_touched": len(flagged_identities),
           "rows_matching_one_identity_with_several_actions": multi_action_single_identity,
           "rows_ambiguous_between_identities": ambiguous, "rows_identifier_matched_by_npi_pending_adjudication": npi_hit_rows}
    txt = json.dumps(rep, indent=2, sort_keys=True)
    print(txt)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
