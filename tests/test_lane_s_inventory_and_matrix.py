"""Guards for the two Lane-S artefact generators (2026-10-03):

  * scripts/render_field_matrix.py — the committed 41-field compliance matrix
    must be a pure function of field_map.py + the overlay, every field must
    carry all six dimensions, and the overlay's requiredness must name the
    necessity field_map holds (the renderer refuses otherwise);
  * scripts/exception_inventory.py — the NPI six-assessment section must read
    the delivered file with the application's own reader, count syntax
    outcomes correctly, keep absence out of the syntax outcome, and never emit
    a delivered value.

No database, no network. The real delivery is never read here; a 3-row
synthetic pipe-delimited file with SYNTHETIC-TRACE values is generated inline.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── matrix ───────────────────────────────────────────────────────────────────

def test_committed_matrix_is_a_fresh_render():
    rfm = _load("render_field_matrix")
    committed = (ROOT / "docs" / "TEFCA_41_Field_Compliance_Matrix_2026-10-03.md").read_text(encoding="utf-8")
    assert committed.replace("\r\n", "\n") == rfm.render(rfm.load_overlay()), (
        "docs/TEFCA_41_Field_Compliance_Matrix_2026-10-03.md is stale; re-run "
        "`python scripts/render_field_matrix.py --out docs/TEFCA_41_Field_Compliance_Matrix_2026-10-03.md`")


def test_overlay_covers_all_41_fields_with_six_dimensions():
    from app.tefca_registry.rce.field_map import FIELD_SPECS

    rfm = _load("render_field_matrix")
    overlay = rfm.load_overlay()
    assert set(overlay["fields"]) == {s.name for s in FIELD_SPECS}
    for spec in FIELD_SPECS:
        row = overlay["fields"][spec.name]
        for key, _title in rfm.DIMENSIONS:
            assert row[key].strip(), f"{spec.name} lacks dimension {key}"
        assert f"`{spec.necessity}`" in row["requiredness"], (
            f"{spec.name}: overlay requiredness must name field_map necessity {spec.necessity}")


def test_renderer_refuses_a_missing_dimension():
    rfm = _load("render_field_matrix")
    overlay = json.loads(json.dumps(rfm.load_overlay()))
    overlay["fields"]["NPI"]["comparison"] = ""
    with pytest.raises(SystemExit, match="dimension 'comparison' empty"):
        rfm.render(overlay)


def test_renderer_refuses_requiredness_that_contradicts_field_map():
    rfm = _load("render_field_matrix")
    overlay = json.loads(json.dumps(rfm.load_overlay()))
    overlay["fields"]["name"]["requiredness"] = "`OPTIONAL`. A: none."   # field_map says REQUIRED
    with pytest.raises(SystemExit, match="does not name field_map necessity"):
        rfm.render(overlay)


# ── inventory: NPI assessments on a synthetic file ───────────────────────────

def _synthetic_delivery(tmp_path: pathlib.Path) -> pathlib.Path:
    from app.services.npi_validator import CMS_PREFIX, _luhn_total
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    def valid_npi(seed: int) -> str:
        base = f"{1_000_000_000 + seed:09d}"[-9:]
        for d in range(10):
            if _luhn_total(CMS_PREFIX + base + str(d)) % 10 == 0:
                return base + str(d)
        raise AssertionError("fixture bug")

    rows = []
    for i, (npi, role) in enumerate([(valid_npi(11), ""), ("123", "provider"), ("", "payer"),
                                     (valid_npi(11), "provider")]):
        values = {f: "" for f in RCE_FIELDS}
        values.update({"id": f"2.16.840.1.113883.3.99999.{i}", "sequoiaorgtype": "Participant",
                       "hl7orgrole": role, "NPI": npi, "name": f"SYNTHETIC-TRACE Org {i}",
                       "partOf": "2.16.840.1.113883.4.391.1000", "orgManagingOrg": "2.16.840.1.113883.4.391.1000"})
        rows.append("|".join(values[f] for f in RCE_FIELDS))
    path = tmp_path / "SYNTHETIC-lane-s.psv"
    path.write_text("|".join(RCE_FIELDS) + "\r\n" + "\r\n".join(rows) + "\r\n", encoding="utf-8")
    return path


def test_npi_assessments_count_syntax_absence_and_policy_predicate_separately(tmp_path):
    inv = _load("exception_inventory")
    res = inv.npi_assessments(str(_synthetic_delivery(tmp_path)), nppes_index=None,
                              ppef_enrollment=None, delimiter="|")
    assert res["parsed_ok"] == 4 and res["parse_field_count_mismatch"] == 0
    syn = res["syntax"]
    assert (syn["npi_present"], syn["npi_absent"], syn["valid"], syn["invalid"]) == (3, 1, 2, 1)
    assert syn["distinct_valid_npis"] == 1 and syn["npis_shared_by_more_than_one_record"] == 1
    assert syn["records_carrying_a_shared_npi"] == 2
    own = res["own_npi_requirement"]
    # provider-role records are 2 (one with an invalid NPI -> still "has an NPI value",
    # one with a valid NPI); payer relaxes; blank role never imposes.
    assert own["records_where_policy_predicate_is_true"] == 2
    assert own["of_which_without_npi (NPI-001 NPI_REQUIRED, HIGH)"] == 0
    assert own["by_hl7orgrole"]["payer"] == {"records": 1, "without_npi": 1}
    assert own["by_hl7orgrole"]["<blank>"] == {"records": 1, "without_npi": 0}
    # Without an NPPES index the evidence-dependent assessments say so instead of guessing.
    assert res["nppes_evidence"]["available"] is False
    assert "type_fitness" not in res


def test_inventory_output_never_carries_delivered_values(tmp_path):
    inv = _load("exception_inventory")
    path = _synthetic_delivery(tmp_path)
    res = inv.npi_assessments(str(path), None, None, delimiter="|")
    text = inv.render_markdown("TEST", None, res, {})
    assert "SYNTHETIC-TRACE Org" not in text
    assert "2.16.840.1.113883.3.99999" not in text
    for line in path.read_text(encoding="utf-8", newline="").splitlines()[1:]:
        if not line.strip():
            continue
        npi = line.split("|")[10]
        if len(npi) == 10:
            assert npi not in text


def test_inventory_markdown_renders_three_separate_populations():
    inv = _load("exception_inventory")
    db = {"database": "x", "intake_filter": None,
          "context": {"intakes": [], "review_rules": [], "population_entities": 0},
          "processing": {"stage_events_by_stage_status": [], "failure_classes": [],
                         "jobs_by_state_stage": [], "rule_execution_by_status": []},
          "findings": {"by_rule": [], "by_delivery": [], "by_severity": [],
                       "unique_records_any_finding": 0, "records_by_finding_count": []},
          "verification": {"dimension_evidence": [], "verifications": [],
                           "per_source_outcome_entities": []},
          "cases": {"by_bucket_rule_version": [], "entities_with_multiple_cases": 0,
                    "decision_events": [], "sample_entities_by_status": []}}
    text = inv.render_markdown("EMPTY", db, None, {})
    for heading in ("### 1. Processing failures", "### 2. Data-quality findings",
                    "### 3. Verification outcomes", "### 4. Analyst / review cases"):
        assert heading in text
