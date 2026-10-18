"""Exclusion NAME screening -- candidates, never confirmations (Part B).

Synthetic names and a synthetic in-memory LEIE index only; no network, no
real exclusion record, no confidential identifier.

  * `normalize_org_name` is deterministic (no similarity score, no
    threshold) and only removes punctuation / designator spelling.
  * The OIG connector ADDS normalized candidates to exact ones and reports
    what it searched, so a clean screen reads "no candidate found in this
    list using these methods", not a blanket clearance.
  * The MANUAL review path screens an NPI-less entity by organisation name
    (it previously did not screen it at all): a candidate is `not_found`
    (pending, a person decides) and never `excluded`; a clean screen is
    `clear` but is NOT counted among verified sources; an unreachable list
    is UNAVAILABLE, never a clearance.
"""
from __future__ import annotations

import time

import pytest

from rce_traceability_support import rolled_back_db, seed_entity  # noqa: F401

ACTIVE = {"lastname": "", "firstname": "", "busname": "SYNTHETIC ACME HEALTH LLC",
          "exclusion_type": "1128a1", "exclusion_date": "20200101",
          "reinstatement_date": "00000000", "state": "TX", "npi": ""}


def _load_index(monkeypatch, records):
    from app.Tefca import connectors as c

    by_name = {}
    for rec in records:
        by_name.setdefault((rec["busname"].strip().upper(), ""), []).append(rec)
    monkeypatch.setattr(c, "_LEIE_CACHE", {"loaded_at": time.time(), "by_npi": {},
                                           "by_name": by_name, "row_count": len(records)})

    async def loaded():
        return True
    monkeypatch.setattr(c, "_ensure_leie_loaded", loaded)


# ── pure ─────────────────────────────────────────────────────────────────────

def test_normalization_removes_only_punctuation_and_designator_spelling():
    from app.Tefca.connectors import normalize_org_name as n

    assert n("Synthetic Acme Health, L.L.C.") == n("SYNTHETIC ACME HEALTH LLC") == "SYNTHETIC ACME HEALTH"
    assert n("Smith & Jones Clinic, Inc.") == n("SMITH AND JONES CLINIC INCORPORATED")
    # Different organisations stay different: no fuzzy collapse.
    assert n("Synthetic Acme Health") != n("Synthetic Acme Home Health")
    assert n("Synthetic Acme Health East") != n("Synthetic Acme Health")
    assert n("") == "" and n(None) == ""


# ── connector ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_punctuation_variant_is_a_candidate_not_a_clean_screen(monkeypatch):
    from app.Tefca.connectors import OIGLEIEConnector

    _load_index(monkeypatch, [ACTIVE])
    r = await OIGLEIEConnector().lookup_by_name(last="", first="", org="Synthetic Acme Health, L.L.C.")
    assert r.success and r.data["exclusion_found"] is True and r.data["excluded"] is True
    assert r.data["candidates_exact"] == 0 and r.data["candidates_normalized_only"] == 1
    assert r.data["match_methods"] == ["exact_business_name", "normalized_business_name"]


@pytest.mark.asyncio
async def test_a_clean_screen_reports_what_was_searched(monkeypatch):
    from app.Tefca.connectors import OIGLEIEConnector

    _load_index(monkeypatch, [ACTIVE])
    r = await OIGLEIEConnector().lookup_by_name(last="", first="", org="Synthetic Other Clinic")
    assert r.success and r.data["exclusion_found"] is False
    assert r.data["list_rows_searched"] == 1 and r.data["list_loaded_at_epoch"]
    assert r.data["match_methods"] == ["exact_business_name", "normalized_business_name"]


@pytest.mark.asyncio
async def test_exact_and_normalized_matches_are_not_double_counted(monkeypatch):
    from app.Tefca.connectors import OIGLEIEConnector

    _load_index(monkeypatch, [ACTIVE])
    r = await OIGLEIEConnector().lookup_by_name(last="", first="", org="Synthetic Acme Health LLC")
    assert r.data["exclusion_count"] == 1
    assert r.data["candidates_exact"] == 1 and r.data["candidates_normalized_only"] == 0


# ── manual review path ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manual_path_screens_an_npi_less_entity_by_name_and_finds_a_candidate(
        rolled_back_db, monkeypatch):
    from app.tefca_registry import review_service as rs
    from app.tefca_registry.rce.shadow_reassessment import risk_signals

    db = rolled_back_db
    _load_index(monkeypatch, [ACTIVE])
    entity_id = await seed_entity(db, oid="9.99.777.90.1", name="Synthetic Acme Health, L.L.C.")
    sources = await rs.probe_sources(db, entity_id)

    assert sources["nppes"]["status"] == rs.NOT_CHECKED     # NPI-keyed: honestly not checked
    oig = sources["oig_leie"]
    assert oig["status"] == "not_found" and oig["status"] != "excluded"
    assert oig["potential_hit"] is True and oig["disposition"] == "REVIEW"
    assert oig["matched_by"] == rs.MATCHED_BY_ORG_NAME
    assert "does not confirm an exclusion" in oig["reason"]
    # The candidate is a risk signal for the bulk-closure and prior-risk guards.
    assert risk_signals({"sources": sources}) and any(
        s.startswith("EXCLUSION:oig_leie") for s in risk_signals({"sources": sources}))


@pytest.mark.asyncio
async def test_manual_path_clean_name_screen_is_not_counted_as_verified(rolled_back_db, monkeypatch):
    from app.tefca_registry import review_service as rs
    from app.tefca_registry.rce.shadow_reassessment import risk_signals

    db = rolled_back_db
    _load_index(monkeypatch, [ACTIVE])
    entity_id = await seed_entity(db, oid="9.99.777.90.2", name="Synthetic Other Clinic")
    sources = await rs.probe_sources(db, entity_id)

    oig = sources["oig_leie"]
    assert oig["status"] == "clear" and oig["potential_hit"] is False
    assert "not an equivalent clearance" in oig["reason"]
    note = rs.coverage_note(sources)
    assert note["sources_verified"] == 0
    assert note["sources_name_screen_only"] == 1
    assert "organisation name only" in note["coverage_note"]
    assert not [s for s in risk_signals({"sources": sources}) if s.startswith("EXCLUSION")]


@pytest.mark.asyncio
async def test_manual_path_unreachable_list_is_unavailable_never_clear(rolled_back_db, monkeypatch):
    from app.Tefca import connectors as c
    from app.tefca_registry import review_service as rs

    db = rolled_back_db

    async def not_loaded():
        return False
    monkeypatch.setattr(c, "_ensure_leie_loaded", not_loaded)
    entity_id = await seed_entity(db, oid="9.99.777.90.3", name="Synthetic Other Clinic")
    oig = (await rs.probe_sources(db, entity_id))["oig_leie"]
    assert oig["status"] == rs.UNAVAILABLE
