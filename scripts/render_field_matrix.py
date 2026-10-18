"""Render the 41-field compliance matrix deterministically.

Two inputs, one output:

    app/tefca_registry/rce/field_map.py     the locked map (necessity, role, target,
                                            dimensions, validation rules, observed
                                            population) — the facts every row inherits
    scripts/field_matrix_overlay.json       the hand-maintained six dimensions per field
                                            (meaning / applicability / requiredness +
                                            governing version / source fields + connector
                                            behaviour / comparison + freshness +
                                            limitations / correction authority +
                                            disposition), the per-document preamble and
                                            appendix text, and the measured NPI counts

    docs/TEFCA_41_Field_Compliance_Matrix_<date>.md   the rendered matrix

The renderer REFUSES to render if any of the 41 fields lacks any of the six
dimensions, or if an overlay row's requiredness cell does not name the necessity
`field_map.py` holds for that field — so the committed document can never drift
from the code it describes. Rendering is a pure function of the two inputs: no
timestamps, no environment, so `python scripts/render_field_matrix.py --check`
proves the committed document is current.

Overlay provenance: built once from the hand-written matrix of 2026-10-02
(`--import-markdown FIELD_MATRIX_41.md`), then maintained as JSON.

Usage:
    python scripts/render_field_matrix.py --out docs/TEFCA_41_Field_Compliance_Matrix_2026-10-03.md
    python scripts/render_field_matrix.py --check docs/TEFCA_41_Field_Compliance_Matrix_2026-10-03.md
    python scripts/render_field_matrix.py --import-markdown FIELD_MATRIX_41.md   # rebuild overlay rows
    python scripts/render_field_matrix.py --import-npi-json <inventory_REAL-...json>  # measured counts
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Dict, List

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OVERLAY_PATH = pathlib.Path(__file__).resolve().parent / "field_matrix_overlay.json"

DIMENSIONS = (
    ("meaning", "1 · Meaning / original representation"),
    ("applicability", "2 · Applicability (entity type · purpose · technical role)"),
    ("requiredness", "3 · Requiredness + effective governing version (A = authoritative, P = AGT policy)"),
    ("sources", "4 · Source fields + actual connector behaviour"),
    ("comparison", "5 · Comparison · freshness · limitations"),
    ("correction", "6 · Correction authority · permitted disposition"),
)

_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|(.*)\|\s*$")


# ── overlay maintenance ──────────────────────────────────────────────────────

def load_overlay() -> Dict[str, Any]:
    return json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))


def save_overlay(overlay: Dict[str, Any]) -> None:
    OVERLAY_PATH.write_text(json.dumps(overlay, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def import_markdown(path: pathlib.Path) -> Dict[str, Any]:
    """Build overlay rows + preamble/appendix from the hand-written matrix."""
    text = path.read_text(encoding="utf-8")
    overlay = load_overlay() if OVERLAY_PATH.exists() else {}
    rows: Dict[str, Dict[str, str]] = {}
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(3).split(" | ")]
        if len(cells) != 6:
            raise SystemExit(f"row {m.group(1)} ({m.group(2)}) has {len(cells)} dimension cells, expected 6")
        rows[m.group(2)] = {key: cells[i] for i, (key, _title) in enumerate(DIMENSIONS)}

    def section(start: str, end: str) -> str:
        s = text.find(start)
        e = text.find(end, s + 1) if end else len(text)
        return text[s:e].strip() if s >= 0 else ""

    overlay.update({
        "source_markdown": path.name,
        "source_markdown_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "evaluated_date": overlay.get("evaluated_date", "2026-10-02"),
        "preamble_A": section("## A.", "## B."),
        "appendix_C": section("## C.", "## D."),
        "appendix_D": section("## D.", ""),
        "fields": rows,
    })
    return overlay


def import_npi_json(path: pathlib.Path) -> Dict[str, Any]:
    overlay = load_overlay()
    blob = json.loads(path.read_text(encoding="utf-8"))
    npi = blob.get("npi_assessments") or {}
    if not npi:
        raise SystemExit("no npi_assessments block in that JSON")
    s, t, i, o, r, c = (npi.get(k, {}) for k in ("syntax", "type_fitness", "identity_matching",
                                                  "own_npi_requirement", "representative_evidence",
                                                  "covered_provider_eligibility"))
    p1 = (c.get("prong_1_is_a_health_care_provider") or {}).get("by_record") or {}
    p2 = c.get("prong_2_conducts_standard_transactions (PPEF enrolment as the only affirmative signal)") or {}
    overlay["measured_npi"] = {
        "label": blob.get("label"), "file": npi.get("file"), "sha256": npi.get("sha256"),
        "records": s.get("records"),
        "nppes_edition": (npi.get("nppes_evidence") or {}).get("edition_member"),
        "ppef_file": p2.get("file"),
        "rows": [
            ["1 Syntax", f"{s.get('valid')} valid · {s.get('invalid')} invalid · {s.get('npi_absent')} absent "
                         f"(of {s.get('records')}); {s.get('distinct_valid_npis')} distinct valid; "
                         f"{s.get('npis_shared_by_more_than_one_record')} NPIs shared by >1 record "
                         f"({s.get('records_carrying_a_shared_npi')} records)",
             "every present value"],
            ["2 Type fitness", "distinct valid NPIs resolved in NPPES: "
                               f"{t.get('distinct_valid_npis_resolved_in_nppes')}; by record: "
                               + ", ".join(f"{k} {v}" for k, v in (t.get("entity_type_by_record") or {}).items()),
             "every resolved NPI"],
            ["3 Identity matching", "name bands: " + ", ".join(
                f"{k.split(' (')[0]} {v}" for k, v in
                (i.get("name_similarity_bands (validation_engine thresholds 0.90/0.70/0.50/0.30)") or {}).items())
             + "; practice address: " + ", ".join(
                f"{k} {v}" for k, v in (i.get("practice_address_comparison (address_comparison.compare_to_nppes, "
                                             "LOCATION only; postal code absent from the index -> uncompared)") or {}).items()),
             f"{i.get('records_with_nppes_record_to_compare')} records with an NPPES record"],
            ["4 Own-NPI presence (policy predicate)",
             f"{o.get('records_where_policy_predicate_is_true')} records where npi_required() is true, "
             f"{o.get('of_which_without_npi (NPI-001 NPI_REQUIRED, HIGH)')} of them without an NPI; "
             f"{o.get('blank_role_records_without_npi (NPI-001 NPI_NOT_SUPPLIED, INFO — never held)')} "
             "blank-role records without an NPI (INFO, never held)",
             "all records (policy predicate); legal requirement = assessment 6"],
            ["5 Representative evidence",
             f"{r.get('records_whose_npi_is_an_individual (NPI-1 on an organisation record -> ENTITY_TYPE_MISMATCH, validation_engine.py; EXCEPTION never auto-match, source_matching.py)')} "
             f"records carry an individual's (NPI-1) NPI ({r.get('distinct_individual_npis_involved')} distinct); "
             f"{r.get('records_carrying_a_deactivated_npi')} records carry a deactivated NPI",
             "every resolved NPI"],
            ["6 Covered-provider eligibility",
             "prong 1 by NPPES taxonomy (records): " + ", ".join(f"{k} {v}" for k, v in p1.items())
             + f"; prong 2: {p2.get('distinct_valid_npis_with_ppef_enrolment')} distinct NPIs PPEF-enrolled "
               f"({p2.get('records_with_ppef_enrolment')} records); resolvable-in-principle "
               f"{c.get('resolvable_in_principle_population (records with a valid NPI resolved in NPPES)')} records; "
               f"irreducibly unresolved {c.get('irreducibly_unresolved_from_delivered_fields (records with no valid NPI)')} "
               "records (no valid NPI)",
             "affirmative signals only; absence never a negative finding"],
        ],
    }
    return overlay


# ── rendering ────────────────────────────────────────────────────────────────

def _cell(s: str) -> str:
    return (s or "").replace("\n", " ").replace("|", "\\|").strip()


def render(overlay: Dict[str, Any]) -> str:
    from app.tefca_registry.rce import field_map as fm

    fields = overlay.get("fields") or {}
    problems: List[str] = []
    for spec in fm.FIELD_SPECS:
        row = fields.get(spec.name)
        if row is None:
            problems.append(f"{spec.name}: no overlay row")
            continue
        for key, _title in DIMENSIONS:
            if not (row.get(key) or "").strip():
                problems.append(f"{spec.name}: dimension '{key}' empty")
        if f"`{spec.necessity}`" not in (row.get("requiredness") or ""):
            problems.append(f"{spec.name}: requiredness cell does not name field_map necessity "
                            f"`{spec.necessity}`")
    extra = sorted(set(fields) - {s.name for s in fm.FIELD_SPECS})
    if extra:
        problems.append(f"overlay rows for unknown fields: {extra}")
    if problems:
        raise SystemExit("matrix not renderable:\n  " + "\n  ".join(problems))

    md: List[str] = []
    md.append("# TEFCA ARC — 41-field compliance matrix (six dimensions per field)")
    md.append("")
    md.append(f"Evaluated date: **{overlay.get('evaluated_date')}**. Generated by "
              "`scripts/render_field_matrix.py` from `app/tefca_registry/rce/field_map.py` "
              f"(FIELD_MAP_VERSION {fm.FIELD_MAP_VERSION}; profiled file `{fm.PROFILED_FILE}` "
              f"sha256 `{fm.PROFILED_SHA256}`, {fm.PROFILED_RECORD_COUNT:,} records, {fm.PROFILED_AT}) "
              "and `scripts/field_matrix_overlay.json` (hand-maintained six dimensions; imported from "
              f"`{overlay.get('source_markdown')}` sha256 `{overlay.get('source_markdown_sha256')}`). "
              "`--check` proves this file is current. Aggregates only; no delivered values.")
    md.append("")
    md.append("Relationship to `docs/TEFCA_41_Field_Processing_Matrix_INTERNAL.md` (2026-08-29): that "
              "matrix answers *what DocuAction executes per field* (preservation → DQ rule → canonical "
              "target → verification → human review). This matrix adds the six compliance dimensions the "
              "2026-10 directive asks for and keeps requiredness separate from source verifiability. "
              "Neither duplicates the other; the `field_map` facts column below is the shared anchor.")
    md.append("")
    md.append("| Dimension | Meaning |")
    md.append("|---|---|")
    for _key, title in DIMENSIONS:
        md.append(f"| {title} | " + {
            "meaning": "What the delivered bytes are (type, format, example SHAPE only — never a value).",
            "applicability": "By entity type (Participant / Subparticipant / QHIN referent), by purpose of use, by technical role (node); `applies` / `does not apply` / `unresolved`, with the signal that decides it.",
            "requiredness": "`REQUIRED` / `CONDITIONAL` / `OPTIONAL` / `LEGITIMATELY_NULLABLE` exactly as `field_map.py` holds it, then the authority: **A** = cited external/contract document, **P** = AGT policy. Never blended. A required field can be NOT_VERIFIABLE externally and still yield a required-but-missing finding when blank.",
            "sources": "What each wired connector really returns for this field today, not what it could. V/C/N = verifiable / corroborative only / NOT_VERIFIABLE — kept separate from requiredness.",
            "comparison": "The comparison method actually coded, the only freshness controls that exist, and the honest limits.",
            "correction": "Who may change the value and what the pipeline is allowed to do with it.",
        }[_key] + " |")
    md.append("")
    if overlay.get("preamble_A"):
        md.append(overlay["preamble_A"])
        md.append("")
    md.append("## B. The matrix — all 41 fields")
    md.append("")
    md.append("Column `field_map facts` is read from code: necessity · role · target(key) · evidence "
              "dimensions · DQ rules · observed population (profiled delivery).")
    md.append("")
    header = ["#", "Field", "field_map facts"] + [t for _k, t in DIMENSIONS]
    md.append("| " + " | ".join(header) + " |")
    md.append("|" + "---|" * len(header))
    for spec in fm.FIELD_SPECS:
        row = fields[spec.name]
        facts = (f"`{spec.necessity}` · {spec.role} · {spec.target}"
                 + (f"({spec.target_key})" if spec.target_key else "")
                 + " · dims " + ("/".join(d.split('_', 1)[0] for d in spec.dimensions) or "none")
                 + " · rules " + (", ".join(spec.validation) or "none")
                 + f" · populated {spec.populated:,} ({spec.coverage_pct}%), {spec.distinct:,} distinct")
        md.append("| " + " | ".join([str(spec.ordinal), f"`{spec.name}`", facts]
                                    + [_cell(row[k]) for k, _t in DIMENSIONS]) + " |")
    md.append("")
    md.append(f"Coverage check (enforced by the renderer): {len(fm.FIELD_SPECS)}/41 fields, "
              f"{len(DIMENSIONS)}/6 dimensions populated on every row, requiredness consistent with "
              "`field_map.py` on every row.")
    md.append("")
    if overlay.get("appendix_C"):
        md.append(overlay["appendix_C"])
        md.append("")
    if overlay.get("appendix_D"):
        md.append(overlay["appendix_D"])
        md.append("")
    m = overlay.get("measured_npi")
    if m:
        md.append("## E. NPI — measured counts for the six assessments (real delivery, offline evidence)")
        md.append("")
        md.append(f"Produced by `scripts/exception_inventory.py --real-file` on `{m.get('file')}` "
                  f"(sha256 `{m.get('sha256')}`, {m.get('records'):,} records) against the pinned NPPES "
                  f"Data Dissemination edition `{m.get('nppes_edition')}` (index built by "
                  f"`scripts/phase6_population_enrichment.py`) and `{m.get('ppef_file')}`. These fill the "
                  "counts §D lists as \"not in evidence held\". Each assessment is measured on its own "
                  "population; none decides another. Label: " + str(m.get("label")) + ".")
        md.append("")
        md.append("| Assessment | Measured | Population |")
        md.append("|---|---|---|")
        for a, b, c in m.get("rows", []):
            md.append(f"| {_cell(a)} | {_cell(b)} | {_cell(c)} |")
        md.append("")
        md.append("Reading the measurements against §D: (2)/(5) the NPI-1-on-organisation count is now "
                  "measured; (3) the name-band split is measured with the legacy engine's thresholds "
                  "(Decision D5 still governs the dimension layer) and the address comparison uses "
                  "`address_comparison.compare_to_nppes` against the pinned edition, so it is not the "
                  "same figure as the population run's live-API conflict count; (4) every record where "
                  "`npi_required()` is true lacks an NPI — the predicate is satisfied only by the 56 "
                  "role-populated records, all of which are NPI-less; (6) prong 1 stays UNKNOWN for the "
                  "majority of resolved NPIs because `_taxonomy_category` deliberately recognises only "
                  "unambiguous NUCC prefixes, while prong 2 (PPEF enrolment) is affirmatively present for "
                  "most resolved NPIs — unresolved therefore concentrates in the no-NPI records, as §D says.")
        md.append("")
    return "\n".join(md)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", default=None, help="path of the committed document; exit 1 if stale")
    ap.add_argument("--import-markdown", default=None)
    ap.add_argument("--import-npi-json", default=None)
    args = ap.parse_args()

    if args.import_markdown:
        save_overlay(import_markdown(pathlib.Path(args.import_markdown)))
        print(f"overlay rebuilt from {args.import_markdown}")
    if args.import_npi_json:
        save_overlay(import_npi_json(pathlib.Path(args.import_npi_json)))
        print(f"measured NPI counts imported from {args.import_npi_json}")
    if args.out:
        text = render(load_overlay())
        pathlib.Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"rendered {args.out}")
    if args.check:
        current = pathlib.Path(args.check).read_text(encoding="utf-8")
        if current.replace("\r\n", "\n") != render(load_overlay()):
            print(f"STALE: {args.check} differs from a fresh render")
            return 1
        print(f"current: {args.check}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
