"""Per-source coverage STATES (attempted / answered / unavailable / failed / potential match
/ confirmed / not applicable / not attempted) and the CMS-enrollment aggregation rule.

Additive read-time labelling only: the headline `sources.*` counts are not asserted
differently here (they are pinned by test_coverage_sql_aggregation.py). Two layers:

* pure tests (no database): the row -> state mapping, the display names, the rule text;
* database tests (skipped with a reason when no database is reachable): the SQL twin is
  equal to the Python mapping, the NPPES-backed legacy `pecos` key is never counted as
  CMS enrollment, and `not_attempted` lists exactly the promoted entities with no row.

Entirely synthetic ids; every row is removed afterwards.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.tefca_registry.rce import verification_coverage as vc


# ---------------------------------------------------------------- pure
@pytest.mark.parametrize("table,value,detail,expected", [
    ("verification", "verified", None, "found"),
    ("verification", "Matched", None, "found"),
    ("verification", "no_match", None, "not_found"),
    ("verification", "source_unavailable", None, "unavailable"),
    ("verification", "error", None, "failed"),
    ("verification", "active", "provider is DEACTIVATED", "deactivated"),
    ("verification", "wizard", None, "other"),
    ("dimension", "PASS", None, "found"),
    ("dimension", "REVIEW", None, "potential_match"),   # possible match: NOT confirmed
    ("dimension", "NOT_FOUND", None, "not_found"),
    ("dimension", "UNAVAILABLE", None, "unavailable"),
    ("dimension", "CONFLICT", None, "failed"),
    ("dimension", "NOT_APPLICABLE", None, "not_applicable"),
    ("dimension", "MYSTERY", None, "other"),
])
def test_row_state_mapping(table, value, detail, expected):
    assert vc.evidence_state(table, value, detail) == expected


def test_potential_match_is_not_a_confirmed_result_and_unavailable_is_not_an_answer():
    assert vc.evidence_state("dimension", "REVIEW") not in vc._STATE_CONFIRMED
    for state in ("unavailable", "failed", "not_applicable", "other"):
        assert state not in vc._STATE_ANSWERING


def test_legacy_pecos_is_labelled_as_the_nppes_proxy_never_as_cms_enrollment():
    name = vc.SOURCE_DISPLAY_NAMES["pecos"]
    assert "Legacy PECOS Proxy" in name and "not CMS enrollment" in name
    assert vc.SOURCE_DISPLAY_NAMES["cms_enrollment"] != name
    assert "PPEF" in vc.SOURCE_DISPLAY_NAMES["cms_enrollment"]
    assert "not exclusion clearance" in vc.SOURCE_DISPLAY_NAMES["sam"]


def test_aggregation_rule_text_states_the_load_bearing_rules():
    rule = vc.AGGREGATION_RULE
    for phrase in ("not additive", "held, rejected", "never counted as CMS enrollment",
                   "CMS_PPEF_ENROLLMENT", "not exclusion clearance",
                   "screening leg not recorded", "potential match is not a confirmed result"):
        assert phrase in rule, phrase


# ---------------------------------------------------------------- database
pytestmark_db = pytest.mark.asyncio


@pytest.fixture
async def seeded(db_required):
    """Ten promoted entities + one extra promoted entity that was never looked up."""
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    intake_id = uuid.uuid4()
    ent = [uuid.uuid4() for _ in range(12)]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text("""
            INSERT INTO rce_source_intakes (id, original_filename, storage_path, sha256,
                file_size_bytes, headers, schema_fingerprint)
            VALUES (:i, 'syn.csv', '/dev/null', :h, 1, '["h"]'::jsonb, 'fp')"""),
            {"i": intake_id, "h": "0" * 64})
        for k, e in enumerate(ent):
            src = uuid.uuid4()
            await db.execute(text("""
                INSERT INTO tefca_reg_entities (id, name, entity_level, entity_type)
                VALUES (:id, :n, 'participant', 'provider')"""), {"id": e, "n": f"SYN {k}"})
            await db.execute(text("""
                INSERT INTO rce_source_records (id, source_intake_id, line_number, raw_line,
                    record_sha256, field_count)
                VALUES (:id, :i, :ln, :raw, :sha, 3)"""),
                {"id": src, "i": intake_id, "ln": k + 1, "raw": f"SYN|{k}", "sha": f"{k:064d}"})
            await db.execute(text("""
                INSERT INTO rce_curated_records (id, source_intake_id, source_record_id,
                    record_status, transformation_version, canonical_entity_id, name)
                VALUES (:id, :i, :src, 'CLEAN', 'v1', :e, :n)"""),
                {"id": uuid.uuid4(), "i": intake_id, "src": src, "e": e, "n": f"SYN {k}"})
        v = [  # (source, status, entity index)
            ("nppes", "verified", 0), ("nppes", "unavailable", 0),  # answered AND unavailable
            ("nppes", "not_found", 1),
            ("pecos", "verified", 2), ("pecos", "verified", 3),     # legacy proxy rows
            ("sam", "unavailable", 4), ("sam", "unavailable", 5),
        ]
        for s, st, k in v:
            await db.execute(text("""
                INSERT INTO tefca_verifications (id, source, verification_status, entity_id)
                VALUES (:id, :s, :st, :e)"""),
                {"id": uuid.uuid4(), "s": s, "st": st, "e": str(ent[k])})
        d = [  # (source, disposition, entity index)
            ("CMS_PPEF_ENROLLMENT", "PASS", 6),
            ("CMS_PPEF_ENROLLMENT", "NOT_APPLICABLE", 7),
            ("OIG_LEIE", "REVIEW", 8),          # potential match, not confirmed
            ("OIG_LEIE", "NOT_FOUND", 9),       # confirmed not found
            ("SAM_GOV", "UNAVAILABLE", 4),
        ]
        for s, disp, k in d:
            await db.execute(text("""
                INSERT INTO tefca_dimension_evidence (id, entity_id, evidence_dimension, source, disposition)
                VALUES (:id, :e, 'DIM', :s, :disp)"""),
                {"id": uuid.uuid4(), "e": str(ent[k]), "s": s, "disp": disp})
        await db.commit()
    try:
        yield engine, intake_id, ent
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            ids = [str(x) for x in ent]
            await db.execute(text("DELETE FROM tefca_dimension_evidence WHERE entity_id = ANY(:e)"), {"e": ids})
            await db.execute(text("DELETE FROM tefca_verifications WHERE entity_id = ANY(:e)"), {"e": ids})
            await db.execute(text("DELETE FROM rce_curated_records WHERE source_intake_id = :i"), {"i": intake_id})
            await db.execute(text("DELETE FROM rce_source_records WHERE source_intake_id = :i"), {"i": intake_id})
            await db.execute(text("DELETE FROM tefca_reg_entities WHERE id = ANY(:e)"), {"e": ent})
            await db.execute(text("DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake_id})
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_states_are_separate_and_legacy_pecos_is_not_cms_enrollment(seeded):
    engine, intake_id, ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        out = await vc.source_states(db, intake_id, eligible=12)
    s = out["sources"]
    # NPPES: entity 0 was answered AND unavailable; entity 1 not found -> answered 2, unavailable 1
    assert (s["nppes"]["attempted"], s["nppes"]["answered"], s["nppes"]["unavailable"]) == (2, 2, 1)
    assert s["nppes"]["confirmed_result"] == 2 and s["nppes"]["not_attempted"] == 10
    # The two legacy pecos rows stay under the proxy key ...
    assert s["pecos"]["attempted"] == 2 and s["pecos"]["is_independent_cms_enrollment"] is False
    assert s["pecos"]["backing"] == "nppes_proxy"
    # ... and genuine CMS enrollment is only the CMS_PPEF_ENROLLMENT rows (1 found + 1 N/A)
    assert s["cms_enrollment"]["attempted"] == 2
    assert s["cms_enrollment"]["answered"] == 1 and s["cms_enrollment"]["not_applicable"] == 1
    assert out["independent_cms_enrollment_entities"] == 2   # never 2 + 2
    # SAM: attempted twice (entity 4 has a verification AND a dimension row), never an answer
    assert s["sam"]["attempted"] == 2 and s["sam"]["answered"] == 0
    assert s["sam"]["attempted_not_answered"] == 2 and s["sam"]["is_exclusion_clearance"] is False
    # LEIE: a potential match is answered but NOT confirmed; a NOT_FOUND is confirmed
    assert s["leie"]["potential_match"] == 1
    assert (s["leie"]["answered"], s["leie"]["confirmed_result"]) == (2, 1)
    assert "not additive" in out["aggregation_rule"]


@pytest.mark.asyncio
async def test_sql_states_equal_the_python_mapping(seeded):
    """The SQL CASE and `evidence_state` agree for every row in the seed."""
    engine, intake_id, ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        vrows = (await db.execute(text(
            "SELECT verification_status, detail FROM tefca_verifications WHERE entity_id = ANY(:e)"),
            {"e": [str(x) for x in ent]})).all()
        drows = (await db.execute(text(
            "SELECT disposition FROM tefca_dimension_evidence WHERE entity_id = ANY(:e)"),
            {"e": [str(x) for x in ent]})).all()
        sql_v = (await db.execute(text(
            "SELECT count(*) FROM tefca_verifications WHERE entity_id = ANY(:e) AND verification_status='unavailable'"),
            {"e": [str(x) for x in ent]})).scalar()
    assert sql_v == sum(1 for st, det in vrows if vc.evidence_state("verification", st, det) == "unavailable")
    assert {vc.evidence_state("dimension", d) for (d,) in drows} == {
        "found", "not_applicable", "potential_match", "not_found", "unavailable"}


@pytest.mark.asyncio
async def test_not_attempted_lists_exactly_the_promoted_entities_without_a_row(seeded):
    engine, intake_id, ent = seeded
    async with AsyncSession(engine, expire_on_commit=False) as db:
        got = await vc.list_not_attempted(db, intake_id, "leie")
        refused = None
        try:
            await vc.list_not_attempted(db, intake_id, "nonsense")
        except ValueError as exc:
            refused = str(exc)
    names = {i["entity_name"] for i in got["items"]}
    assert got["count"] == 10 and names == {f"SYN {k}" for k in range(12) if k not in (8, 9)}
    assert [i["line_number"] for i in got["items"]] == sorted(i["line_number"] for i in got["items"])
    assert refused and "unknown source" in refused
