"""Record-specific review of an NPI-less record: the name screen and the
persisted name evidence use ONE identity, and an unestablished identity never
passes (W4 / W5, 2026-11-14).

Owner decision 2026-11-14 (COR decision register): names are compared after
Unicode NFC, casefold and collapsed whitespace. Punctuation, word order and
legal suffixes are preserved. No fuzzy matching. Blank never establishes
identity.

Database-backed (rolled-back transaction). Synthetic rows only; the exclusion
connector is replaced at its edge, nothing leaves the process.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry import review_service as rs
from app.tefca_registry.rce import models as m
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, seed_entity, seed_intake,
)

HIT = "SYNTHETIC-NAME-SCREEN-HIT"


# ── pure: the one normalization ─────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("Synthetic Health Clinic", "SYNTHETIC HEALTH CLINIC"),            # case
    ("Synthetic  Health\tClinic ", " synthetic health clinic"),        # whitespace
    ("Straße Clinic", "STRASSE CLINIC"),                           # casefold, not lower()
    ("Café Clinic", "Café Clinic"),                          # NFC: same text, two encodings
])
def test_names_that_differ_only_by_case_whitespace_or_encoding_are_one_identity(a, b):
    assert rs.record_name_identity(a) == rs.record_name_identity(b) != ""


@pytest.mark.parametrize("a,b", [
    ("Synthetic Health Clinic", "Synthetic Health Clinc"),             # no fuzzy matching
    ("Synthetic Health Clinic", "Synthetic Health Clinic LLC"),        # legal suffix preserved
    ("Synthetic Health Clinic, Inc.", "Synthetic Health Clinic Inc"),  # punctuation preserved
    ("A.B. Clinic", "AB Clinic"),
    ("Smith-Jones Clinic", "Smith Jones Clinic"),
    ("Health Synthetic Clinic", "Synthetic Health Clinic"),            # word order preserved
    ("Synthetic Health Clinic", "Synthetic Health"),                   # no substring matching
    ("Cafe Clinic", "Café Clinic"),                                # no accent folding
])
def test_anything_else_is_a_different_identity(a, b):
    assert rs.record_name_identity(a) != rs.record_name_identity(b)


@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_a_blank_name_never_establishes_identity(blank):
    assert rs.record_name_identity(blank) == ""
    assert rs.names_establish_one_identity(blank, blank) is False
    assert rs.names_establish_one_identity(blank, "Synthetic Health Clinic") is False


@pytest.mark.parametrize("stored,source,expected", [
    ("org=Synthetic Health Clinic", "OIG_LEIE", "Synthetic Health Clinic"),
    ("legal_name=Synthetic Health Clinic", "SAM_GOV", "Synthetic Health Clinic"),
    ("Synthetic Health Clinic", "OIG_LEIE", "Synthetic Health Clinic"),      # bare form
    ("legal_name=Synthetic Health Clinic", "OIG_LEIE", None),              # the other source's prefix
    ("org=Synthetic Health Clinic", "SAM_GOV", None),
    ("uei=ABC123", "SAM_GOV", None),                                       # another key entirely
    ("org=", "OIG_LEIE", None),
    (None, "OIG_LEIE", None),
])
def test_the_name_prefix_is_parsed_explicitly_per_source(stored, source, expected):
    assert rs.evidence_query_name(source, stored) == expected


# ── database: the single-record writer ──────────────────────────────────────

async def _npi_less_record(db, arc, *, entity_name=None):
    """One delivery, one NPI-less record, promoted onto one NPI-less entity."""
    row = {**make_rows(1, arc=arc)[0], "NPI": ""}
    intake_id, _ = await seed_intake(db, [row], with_job=False)
    db.add(m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="test",
        rule_config_hash="0" * 64, started_at=datetime(2026, 11, 7, 8), completed_at=datetime(2026, 11, 8, 9),
        run_status="COMPLETE",
        assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"}))
    rec = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().one()
    delivered = (rec.parsed or {}).get("name")
    assert delivered and not (rec.parsed or {}).get("NPI")
    ent_id = await seed_entity(db, oid=f"{SYN}-NAME-{uuid.uuid4().hex[:8]}",
                               name=entity_name(delivered) if entity_name else f"{SYN} a different organisation")
    db.add(m.RceCuratedRecord(
        id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
        record_status="CLEAN", rce_org_oid=rec.source_rce_id,
        transformation_version="test", canonical_entity_id=ent_id))
    await db.flush()
    return await db.get(reg.TefcaRegEntity, ent_id), rec, delivered


def _patch_edges(monkeypatch, *, screen_status="not_found", persisted=None):
    """Replace only the LEIE name-search edge and the persisted-evidence read."""
    calls = []

    async def screen(_db, _entity_id, *args, **kwargs):
        name = kwargs.get("name", args[0] if args else None)
        calls.append(name)
        return {"status": screen_status, "matched_by": rs.MATCHED_BY_ORG_NAME,
                "potential_hit": True, "reason": HIT, "label": "l",
                "lookup_identifier": f"org={name}"[:50] if name else "org=<entity name>"}

    async def none(*_a, **_k):
        return None

    async def stored(_db, _entity_id):
        return dict(persisted or {})

    monkeypatch.setattr(rs, "_leie_org_name_screen", screen)
    monkeypatch.setattr(rs, "latest_persisted_exclusion_evidence", stored)
    monkeypatch.setattr(rs, "_resolve_entity", none)
    monkeypatch.setattr(rs, "_compare_addresses", none)
    return calls


def _evidence(source, query_identifier, disposition="REVIEW"):
    return SimpleNamespace(id=uuid.uuid4(), source=source, disposition=disposition,
                           generation_timestamp="2026-11-08T12:00:00+00:00",
                           review_id="REV-2026-000001", rule_applied="TEST",
                           query_identifier=query_identifier, normalized_values=None)


async def _stored_review(db, review_id):
    return (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == review_id))).scalars().one()


async def test_a_record_whose_name_is_not_the_entitys_is_never_screened_as_the_entity(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, rec, delivered = await _npi_less_record(db, "9.99.780.01")
    calls = _patch_edges(monkeypatch, persisted={
        "OIG_LEIE": _evidence("OIG_LEIE", f"org={ent.name}"),
        "SAM_GOV": _evidence("SAM_GOV", f"legal_name={delivered}")})
    out = await rs.run_review(db, ent, source_record_id=rec.id)

    assert calls == []                                    # the entity-name screen did not run
    leie = out["verification"]["oig_leie"]
    assert leie["status"] == rs.NOT_CHECKED and HIT not in str(out["verification"])
    assert "not established" in leie["reason"]
    assert out["verification"]["sam_gov"]["status"] == rs.NOT_CHECKED
    stored = await _stored_review(db, out["review_id"])
    assert stored.source_record_id == rec.id
    vr = stored.verification_results
    assert vr["persisted_evidence"] == []                 # no name evidence under this record
    assert vr["sources"]["oig_leie"]["status"] == rs.NOT_CHECKED
    assert vr["record_context"]["name_identity"]["established"] is False
    assert stored.classification_bucket == "B3"           # a person decides; never B1/B2
    rows = (await db.execute(select(reg.TefcaVerification).where(
        reg.TefcaVerification.review_id == out["review_id"],
        reg.TefcaVerification.source == "oig_leie"))).scalars().all()
    assert [r.verification_status for r in rows] == [rs.NOT_CHECKED]
    assert all(not r.lookup_identifier for r in rows)


@pytest.mark.parametrize("vary", [str.lower, lambda n: "  " + n.replace(" ", "   ") + " "])
async def test_an_established_name_is_screened_by_the_delivered_name(rolled_back_db, monkeypatch, vary):
    db = rolled_back_db
    ent, rec, delivered = await _npi_less_record(db, "9.99.780.02", entity_name=vary)
    assert ent.name != delivered
    calls = _patch_edges(monkeypatch)
    out = await rs.run_review(db, ent, source_record_id=rec.id)
    assert calls == [delivered.strip()]                   # the RECORD's name, not the entity's
    assert out["verification"]["oig_leie"]["status"] == "not_found"
    vr = (await _stored_review(db, out["review_id"])).verification_results
    assert vr["record_context"]["name_identity"]["established"] is True


@pytest.mark.parametrize("suffix", [" LLC", ".", ", Inc."])
async def test_a_legal_suffix_or_punctuation_difference_is_not_an_established_name(
        rolled_back_db, monkeypatch, suffix):
    db = rolled_back_db
    ent, rec, _ = await _npi_less_record(db, "9.99.780.03", entity_name=lambda n: n + suffix)
    calls = _patch_edges(monkeypatch)
    out = await rs.run_review(db, ent, source_record_id=rec.id)
    assert calls == [] and out["verification"]["oig_leie"]["status"] == rs.NOT_CHECKED
    assert (await _stored_review(db, out["review_id"])).classification_bucket == "B3"


async def test_a_blank_delivered_name_is_not_an_established_name(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, rec, _ = await _npi_less_record(db, "9.99.780.04")
    rec.parsed = {**rec.parsed, "name": "  "}
    ent.name = "  "
    await db.flush()
    calls = _patch_edges(monkeypatch)
    out = await rs.run_review(db, ent, source_record_id=rec.id)
    assert calls == [] and out["verification"]["oig_leie"]["status"] == rs.NOT_CHECKED


async def test_prefixed_name_evidence_for_this_record_is_kept_and_another_names_is_dropped(
        rolled_back_db, monkeypatch):
    """W5. The live list is unreachable; the retained evidence is what is left."""
    db = rolled_back_db
    ent, rec, delivered = await _npi_less_record(db, "9.99.780.05", entity_name=str.lower)
    _patch_edges(monkeypatch, screen_status=rs.UNAVAILABLE, persisted={
        "OIG_LEIE": _evidence("OIG_LEIE", f"org={delivered}"),
        "SAM_GOV": _evidence("SAM_GOV", f"legal_name={delivered.lower()}")})
    out = await rs.run_review(db, ent, source_record_id=rec.id)
    used = (await _stored_review(db, out["review_id"])).verification_results["persisted_evidence"]
    assert sorted(u["source"] for u in used) == ["OIG_LEIE", "SAM_GOV"]
    assert out["verification"]["oig_leie"]["status"] == rs.NOT_FOUND      # the retained candidate hit
    assert out["verification"]["sam_gov"]["status"] == rs.NOT_FOUND

    ent2, rec2, delivered2 = await _npi_less_record(db, "9.99.780.06", entity_name=str.lower)
    _patch_edges(monkeypatch, screen_status=rs.UNAVAILABLE, persisted={
        "OIG_LEIE": _evidence("OIG_LEIE", f"org={delivered2} OTHER"),      # another record's name
        "SAM_GOV": _evidence("SAM_GOV", f"org={delivered2}")})             # LEIE's prefix on a SAM row
    out2 = await rs.run_review(db, ent2, source_record_id=rec2.id)
    assert (await _stored_review(db, out2["review_id"])).verification_results["persisted_evidence"] == []
    assert out2["verification"]["oig_leie"]["status"] == rs.UNAVAILABLE
    assert out2["verification"]["sam_gov"]["status"] == rs.NOT_CHECKED


async def test_an_entity_level_review_is_unchanged(rolled_back_db, monkeypatch):
    """No selected record: the entity is screened by its own name, as before."""
    db = rolled_back_db
    ent, _rec, _ = await _npi_less_record(db, "9.99.780.07")
    calls = _patch_edges(monkeypatch)
    out = await rs.run_review(db, ent)
    assert len(calls) == 1 and out["source_record_id"] is None
    assert out["verification"]["oig_leie"]["status"] == "not_found"
