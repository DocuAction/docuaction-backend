"""WP7-A: SAM coverage and report figures separate registration-only, exclusion and unrecorded legs.

READ-TIME LABELLING ONLY. Stored rows are not rewritten; B1-B4 classification, rules and
bucket assignment are untouched (decision C4). The headline SAM counts are unchanged; an
additive `screening_legs` block says which question each SAM row answered:

  registration_only   v3 registration search (NPI-less `sam_name` branch). A miss is "no
                      registration record found", NEVER an exclusion "no hit".
  exclusion           the independent exclusion leg: clear / hit / unavailable.
  leg_not_recorded    historical rows with no `screening_leg` label: "screening leg not
                      recorded" -- never exclusion-clear, never relabelled as registration.
"""
from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.tefca_registry.rce import verification_coverage as vc


# ---------------------------------------------------------------- pure classification
@pytest.mark.parametrize("leg,expected", [
    ("registration_only", "registration_only"),
    (" Registration_Only ", "registration_only"),
    ("exclusion", "exclusion"),
    ("exclusion_and_registration", "exclusion"),
    (None, "leg_not_recorded"),             # historical row, no label
    ("", "leg_not_recorded"),
    ("something_new", "leg_not_recorded"),  # unknown label is an honest unknown
    (7, "leg_not_recorded"),
])
def test_leg_class(leg, expected):
    assert vc.sam_leg_class(leg) == expected


def test_registration_only_miss_is_never_an_exclusion_clear():
    assert vc.sam_leg_outcome("registration_only", "NOT_FOUND") == "no_registration_record_found"
    assert vc.sam_leg_outcome("registration_only", "NOT_FOUND") != "clear"
    assert vc.sam_leg_outcome("registration_only", "PASS") == "registration_record_found"


def test_exclusion_leg_outcomes():
    assert vc.sam_leg_outcome("exclusion", "NOT_FOUND") == "clear"
    assert vc.sam_leg_outcome("exclusion", "PASS") == "clear"
    assert vc.sam_leg_outcome("exclusion", "REVIEW") == "hit"
    assert vc.sam_leg_outcome("exclusion", "FAIL") == "hit"
    assert vc.sam_leg_outcome("exclusion", "UNAVAILABLE") == "unavailable"


def test_historical_row_without_leg_is_not_clear_and_not_registration():
    cls = vc.sam_leg_class(None)
    assert cls == "leg_not_recorded"
    for disp in ("NOT_FOUND", "PASS", "REVIEW", "CORROBORATED"):
        out = vc.sam_leg_outcome(cls, disp)
        assert out == "not_interpreted"
        assert out not in ("clear", "no_registration_record_found", "registration_record_found")
    assert vc.sam_leg_outcome(cls, "UNAVAILABLE") == "unavailable"
    assert vc._empty_sam_legs()["leg_not_recorded"]["label"] == "screening leg not recorded"


# ---------------------------------------------------------------- report layer
def _legs(**over):
    base = vc._empty_sam_legs()
    for k, v in over.items():
        base[k].update(v)
    return base


def test_normalise_coverage_carries_screening_legs_for_sam_only():
    from app.reports.data.delivery_processing_data import _normalise_coverage

    legs = _legs(registration_only={"entities": 3, "no_registration_record_found": 3})
    out = _normalise_coverage({"state": "Partial", "sources": {
        "sam": {"coverage_state": "Complete", "eligible": 3, "attempted": 3, "verified": 0,
                "not_found": 3, "unavailable": 0, "failed": 0, "coverage_pct": 100.0,
                "screening_legs": legs},
        "leie": {"coverage_state": "Complete", "eligible": 3, "attempted": 3, "verified": 3,
                 "not_found": 0, "unavailable": 0, "failed": 0, "coverage_pct": 100.0}}})
    by = {s["name"]: s for s in out["sources"]}
    assert by["sam"]["screening_legs"] == legs
    assert "screening_legs" not in by["leie"]
    # headline columns unchanged
    assert by["sam"]["not_found"] == 3 and by["sam"]["answered"] == 3


def _csv_for(legs):
    from app.reports.engine.csv_engine import delivery_processing_to_csv

    sam = {"name": "sam", "state": "Complete", "eligible": 5, "attempted": 5,
           "verified": 0, "not_found": 5, "unavailable": 0, "failed": 0,
           "coverage_pct": 100.0, "answered": 5, "answered_pct": 100.0}
    if legs:
        sam["screening_legs"] = legs
    ds = {
        "delivery": {"job_id": "j", "intake_id": "i", "delivery_label": "L", "filename": "f", "sha256": "s"},
        "build": {}, "dispositions": {"counts": {}, "rows": []}, "reconciliation": {"available": False},
        "findings": {"available": False},
        "verification": {"sources": [sam]},
        "analyst": {"available": False}, "limitations": [],
    }
    return delivery_processing_to_csv(ds, "DA-ARC-2026-999", "2026-10-12T00:00:00", rule_set_version=3)


def test_csv_annex_b1_separates_the_legs_and_labels_historical_rows():
    legs = _legs(registration_only={"entities": 2, "no_registration_record_found": 2},
                 exclusion={"entities": 2, "clear": 1, "hit": 1},
                 leg_not_recorded={"entities": 1, "not_interpreted": 1})
    text_ = _csv_for(legs)
    assert "## Annex B.1. SAM.gov results by screening leg" in text_
    rows = {r.split(",")[0]: r.split(",") for r in text_.splitlines()
            if r.startswith(("Registration search only", "Exclusion screening", "Screening leg not recorded"))}
    reg, exc, unk = (rows["Registration search only"], rows["Exclusion screening"],
                     rows["Screening leg not recorded"])
    # columns: leg, entities, clear, hit, no-registration-record, record found, unavailable, other
    assert reg[1:5] == ["2", "n/a", "n/a", "2"]            # registration miss is NOT in "Clear"
    assert exc[1:4] == ["2", "1", "1"]
    assert unk[1:5] == ["1", "n/a", "n/a", "n/a"]          # unrecorded never reported as clear
    assert "is not an exclusion 'no hit'" in text_


def test_csv_without_legs_has_no_annex_b1_and_existing_annex_b_is_unchanged():
    text_ = _csv_for(None)
    assert "Annex B.1" not in text_
    assert "## Annex B. Verification coverage per source" in text_


def test_html_template_renders_the_leg_table():
    from app.reports.engine.template_engine import environment

    legs = _legs(registration_only={"entities": 4, "no_registration_record_found": 4},
                 leg_not_recorded={"entities": 2, "not_interpreted": 2})
    path = os.path.join(os.path.dirname(__file__), "..", "app", "reports", "templates",
                        "delivery_processing.html")
    tpl = open(path, encoding="utf-8").read()
    start = tpl.index("{% for s in verification.sources %}{% if s.get('screening_legs') %}")
    end = tpl.index("{% endif %}{% endfor %}", start) + len("{% endif %}{% endfor %}")
    html = environment().from_string(tpl[start:end]).render(
        verification={"sources": [{"name": "sam", "screening_legs": legs}, {"name": "leie"}]})
    assert "Registration search only" in html and "Screening leg not recorded" in html
    assert "not an exclusion" in html
    # `legs.exclusion.clear` would resolve to dict.clear (a bound method) in Jinja: it must be the count
    assert " at 0x" not in html and "built-in method" not in html
    excl_row = next(r for r in html.split("</tr>") if "Exclusion screening" in r)
    assert "<td>0</td><td>0</td>" in "".join(excl_row.split())
    assert html.count("SAM.gov results by screening leg") == 1


def test_html_leg_table_has_the_same_columns_and_counts_as_csv_annex_b1():
    """CodeRabbit (PR #148): the PDF table must carry every Annex B.1 column so the outcome cells add up."""
    import re
    from app.reports.engine.template_engine import environment

    legs = _legs(registration_only={"entities": 7, "no_registration_record_found": 1,
                                    "registration_record_found": 2, "unavailable": 3, "other": 1},
                 exclusion={"entities": 9, "clear": 4, "hit": 2, "unavailable": 1, "other": 2},
                 leg_not_recorded={"entities": 5, "unavailable": 1, "not_interpreted": 4})
    path = os.path.join(os.path.dirname(__file__), "..", "app", "reports", "templates",
                        "delivery_processing.html")
    tpl = open(path, encoding="utf-8").read()
    start = tpl.index("{% for s in verification.sources %}{% if s.get('screening_legs') %}")
    end = tpl.index("{% endif %}{% endfor %}", start) + len("{% endif %}{% endfor %}")
    html = environment().from_string(tpl[start:end]).render(
        verification={"sources": [{"name": "sam", "screening_legs": legs}]})
    headers = re.findall(r'<th scope="col">([^<]+)</th>', html)
    assert headers == ["Screening leg", "Entities", "Clear", "Hit", "No registration record found",
                       "Registration record found", "Unavailable", "Other / not interpreted"]
    rows = {}
    for r in html.split("</tr>"):
        m = re.search(r'<th scope="row">([^<]+)</th>', r)
        if m:
            rows[m.group(1)] = re.findall(r"<td>([^<]*)</td>", r)
    assert all(len(v) == len(headers) - 1 for v in rows.values())
    assert rows["Registration search only"] == ["7", "n/a", "n/a", "1", "2", "3", "1"]
    assert rows["Exclusion screening"] == ["9", "4", "2", "n/a", "n/a", "1", "2"]
    assert rows["Screening leg not recorded"] == ["5", "n/a", "n/a", "n/a", "n/a", "1", "4"]


# ---------------------------------------------------------------- DB: SQL twin parity
@pytest.fixture
async def sam_seed(db_required):
    from app.core.database import _normalize_url

    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    intake_id = uuid.uuid4()
    ent = [uuid.uuid4() for _ in range(8)]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text("""
            INSERT INTO rce_source_intakes (id, original_filename, storage_path, sha256,
                file_size_bytes, headers, schema_fingerprint)
            VALUES (:i, 'syn.csv', '/dev/null', :h, 1, '["h"]'::jsonb, 'fp')"""),
            {"i": intake_id, "h": "1" * 64})
        for k, e in enumerate(ent):
            src = uuid.uuid4()
            await db.execute(text("INSERT INTO tefca_reg_entities (id, name, entity_level, entity_type) "
                                  "VALUES (:id, :n, 'participant', 'provider')"), {"id": e, "n": f"SAMLEG {k}"})
            await db.execute(text("""INSERT INTO rce_source_records (id, source_intake_id, line_number,
                raw_line, record_sha256, field_count) VALUES (:id, :i, :ln, 'x', :sha, 3)"""),
                {"id": src, "i": intake_id, "ln": k + 1, "sha": uuid.uuid4().hex * 2})
            await db.execute(text("""INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                record_status, transformation_version, canonical_entity_id)
                VALUES (:id, :i, :src, 'CLEAN', 'v1', :e)"""),
                {"id": uuid.uuid4(), "i": intake_id, "src": src, "e": e})
        # (entity idx, source spelling, disposition, normalized_values)
        rows = [
            (0, "sam_gov", "NOT_FOUND", {"screening_leg": "registration_only"}),    # registration-only miss
            (1, "sam_gov", "NOT_FOUND", {"screening_leg": "exclusion"}),            # exclusion clear
            (2, "SAM", "REVIEW", {"screening_leg": "exclusion_and_registration"}),  # exclusion hit
            (3, "sam_gov", "UNAVAILABLE", {"screening_leg": "exclusion"}),          # exclusion unavailable
            (4, "sam_gov", "NOT_FOUND", {}),                                        # historical, no leg
            (5, "sam_gov", "PASS", None),                                           # historical, null values
            (6, "sam.gov", "PASS", {"screening_leg": "registration_only"}),         # registration record found
            (1, "sam_gov", "PASS", {"screening_leg": "exclusion"}),                 # same entity, same outcome
            (7, "leie", "NOT_FOUND", {"screening_leg": "exclusion"}),               # not SAM: ignored
        ]
        for idx, src_name, disp, nv in rows:
            await db.execute(text("""INSERT INTO tefca_dimension_evidence
                (id, entity_id, evidence_dimension, source, disposition, normalized_values)
                VALUES (:id, :e, 'EXCLUSION_REVOCATION', :s, :d, CAST(:nv AS jsonb))"""),
                {"id": uuid.uuid4(), "e": str(ent[idx]), "s": src_name, "d": disp,
                 "nv": json.dumps(nv) if nv is not None else None})
        await db.commit()
    try:
        yield engine, intake_id
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM tefca_dimension_evidence WHERE entity_id = ANY(:e)"),
                             {"e": [str(x) for x in ent]})
            await db.execute(text("DELETE FROM rce_curated_records WHERE source_intake_id = :i"), {"i": intake_id})
            await db.execute(text("DELETE FROM rce_source_records WHERE source_intake_id = :i"), {"i": intake_id})
            await db.execute(text("DELETE FROM tefca_reg_entities WHERE id = ANY(:e)"), {"e": ent})
            await db.execute(text("DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake_id})
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_leg_breakdown_registration_exclusion_and_unrecorded(sam_seed):
    engine, intake_id = sam_seed
    async with AsyncSession(engine, expire_on_commit=False) as db:
        legs = await vc.sam_screening_legs(db, intake_id)
        headline = await vc.coverage_counts(db, intake_id)
    reg, exc, unk = legs["registration_only"], legs["exclusion"], legs["leg_not_recorded"]
    # registration-only: entity 0 miss, entity 6 record found -- NOT counted as exclusion clear
    assert (reg["entities"], reg["no_registration_record_found"], reg["registration_record_found"]) == (2, 1, 1)
    # exclusion: entity 1 clear (two rows, one outcome, one entity), 2 hit, 3 unavailable
    assert (exc["entities"], exc["clear"], exc["hit"], exc["unavailable"]) == (3, 1, 1, 1)
    # historical rows 4 and 5: labelled, not clear, not registration
    assert unk["entities"] == 2 and unk["not_interpreted"] == 2
    assert unk["label"] == "screening leg not recorded"
    # headline SAM counts are unchanged by the additive breakdown: 7 SAM entities attempted
    assert headline["sam"]["attempted"] == 7


@pytest.mark.asyncio
async def test_coverage_for_intake_attaches_legs_to_sam_only(sam_seed):
    engine, intake_id = sam_seed
    async with AsyncSession(engine, expire_on_commit=False) as db:
        cov = await vc.coverage_for_intake(db, intake_id)
    assert cov["sources"]["sam"]["screening_legs"]["registration_only"]["entities"] == 2
    assert "screening_legs" not in cov["sources"]["leie"]
    assert cov["sources"]["sam"]["attempted"] == 7


@pytest.mark.asyncio
async def test_sql_matches_python_twin(sam_seed):
    engine, intake_id = sam_seed
    async with AsyncSession(engine, expire_on_commit=False) as db:
        rows = (await db.execute(text(
            "SELECT normalized_values->>'screening_leg', disposition FROM tefca_dimension_evidence "
            "WHERE entity_id IN (SELECT CAST(canonical_entity_id AS text) FROM rce_curated_records "
            "WHERE source_intake_id = CAST(:i AS uuid)) AND lower(source) LIKE 'sam%'"),
            {"i": str(intake_id)})).all()
        legs = await vc.sam_screening_legs(db, intake_id)
    py = defaultdict(int)
    for leg, disp in rows:
        cls = vc.sam_leg_class(leg)
        py[(cls, vc.sam_leg_outcome(cls, disp))] += 1
    for (cls, outcome), n in py.items():
        assert legs[cls][outcome] >= 1, (cls, outcome, n, legs)
