"""SAM public-extract supplementary screen -- D3 wiring (2026-10-28).

Proves the additive-only contract promised in evidence_service.py and
evidence_assembly.py: the `sam_extract_candidate` key, when absent, changes
nothing about D3 (baseline behaviour is untouched); when present with a
candidate match, it escalates the dimension to REVIEW (real information an
analyst should see); when present and clean or unavailable, it never
substitutes for, or clears, the primary api.sam.gov control.

Uses the same direct `_dimension_exclusion` call pattern as
tests/test_sam_b4_path_2026_10_07.py. No rule, mapping or disposition of the
three existing controls is changed by this file.
"""
from __future__ import annotations

from app.Tefca.applicability import build_profile
from app.Tefca.evidence_assembly import _dimension_exclusion
from app.Tefca.evidence_dimensions import Disposition
from app.Tefca.mock_data import MOCK_ENTITY_INDEX


def entity(entity_id):
    return MOCK_ENTITY_INDEX[entity_id]


class _R:
    def __init__(self, success, data=None, error=None):
        self.success, self.data, self.error = success, data, error
        self.query_timestamp, self.api_version = "t", "v"


def _clean_sources(**extra):
    base = {
        "leie_org": _R(True, {"excluded": False, "exclusion_found": False}),
        "sam_name": _R(True, {"found": False, "excluded": False, "ambiguous": False}),
        "cms_revocation": _R(True, {"matches": [], "result": "NONE"}),
    }
    base.update(extra)
    return base


def _d3(ent_id="rce-org-rp-006", **extra):
    ent = entity(ent_id)
    return _dimension_exclusion(ent, build_profile(ent), _clean_sources(**extra))


#: Whatever the three primary controls alone produce for this fixture (an
#: org-name/no-NPI entity, so OIG/SAM are both on their weaker name-search
#: fallback -- REVIEW, per test_sam_b4_path_2026_10_07.py's own finding that
#: a name-only screen is reported as REVIEW, not PASS). The absolute value
#: doesn't matter here; what matters is that NONE of the extract scenarios
#: below move it.
_BASELINE = _d3().disposition


def test_absent_key_changes_nothing_about_d3():
    """Flag off (or not yet wired for this entity) -- no sam_extract_candidate
    key at all. D3 must look exactly as it did before this feature existed:
    no SAM_EXTRACT_SUPPLEMENTARY item at all."""
    d3 = _d3()
    assert not any(i.source == "SAM_EXTRACT_SUPPLEMENTARY" for i in d3.items)
    assert d3.disposition == _BASELINE


def test_candidate_match_escalates_the_dimension_to_review():
    d3 = _d3(sam_extract_candidate=_R(True, {"candidate_match": True, "candidate_count": 1}))
    item = next(i for i in d3.items if i.source == "SAM_EXTRACT_SUPPLEMENTARY")
    assert item.disposition == Disposition.REVIEW.value
    assert d3.disposition == Disposition.REVIEW.value
    assert d3.requires_analyst is True


def test_clean_extract_result_does_not_itself_change_the_dimension():
    """A clean supplementary result is disclosed but is not a fourth vote of
    clearance -- the dimension's own PASS/REVIEW logic is driven only by the
    three primary controls, exactly as before this feature existed. Compared
    against the baseline (no key at all), not a hardcoded PASS, since this
    fixture's three primary controls alone already land on REVIEW (name-only
    screening) -- that pre-existing behaviour is not this file's to re-assert."""
    d3 = _d3(sam_extract_candidate=_R(True, {"candidate_match": False, "candidate_count": 0}))
    item = next(i for i in d3.items if i.source == "SAM_EXTRACT_SUPPLEMENTARY")
    assert item.disposition == Disposition.PASS.value
    assert d3.disposition == _BASELINE


def test_unavailable_extract_is_disclosed_but_never_poisons_the_primary_controls():
    """Flag-off, stale, malformed, or no-identifier -- whatever the reason,
    this supplementary check being unavailable must never be read as the
    primary api.sam.gov check also being unavailable, and must never, by
    itself, move the dimension's own disposition away from the baseline."""
    d3 = _d3(sam_extract_candidate=_R(False, error="SAM_EXTRACT unavailable: ENABLE_SAM_EXTRACT_SCREENING not enabled"))
    item = next(i for i in d3.items if i.source == "SAM_EXTRACT_SUPPLEMENTARY")
    assert item.disposition == Disposition.UNAVAILABLE.value
    assert d3.disposition == _BASELINE


def test_candidate_match_still_escalates_even_if_primary_sam_is_unreachable():
    """The supplementary check's own hit must surface even when the primary
    api.sam.gov control itself is down -- this is exactly the scenario the
    October-readiness directive is worried about: a SAM outage must not look
    like a clean pass if there is independent candidate evidence available."""
    d3 = _d3(
        sam_name=_R(False, error="SAM.gov unavailable"),
        sam_extract_candidate=_R(True, {"candidate_match": True, "candidate_count": 1}),
    )
    assert d3.disposition == Disposition.REVIEW.value
    sources_seen = {i.source for i in d3.items}
    assert {"SAM_GOV", "SAM_EXTRACT_SUPPLEMENTARY"} <= sources_seen


# ── the October BULK path's consumption point (2026-10-29) ───────────────────
#
# arc_pipeline._resolve_and_gather_evidence -> EvidenceService.build_evidence
# -> gather_sources is the ONE chain both the bulk October run and the
# single-entity review share. These tests drive gather_sources itself (all
# other connectors stubbed, no network), proving the bulk path consumes the
# extract screen in all three outcomes -- candidate, no-candidate,
# unavailable -- and that flag-off means the key is ABSENT, not a silent
# clean result.

import time as _time_mod

import pytest as _pytest

from app.Tefca.evidence_service import EvidenceService


class _StubManager:
    class _L:
        async def lookup_by_name(self, **kw):
            return _R(True, {"excluded": False, "exclusion_found": False})

    class _S:
        async def lookup_by_name(self, *a, **kw):
            return _R(True, {"found": False, "excluded": False, "ambiguous": False})

    leie, sam = _L(), _S()

    async def query_all_sources(self, entity):
        return {}


class _StubPPEF:
    async def lookup_by_npi(self, npi):
        return _R(True, {"found": False, "records": [], "enrollment_ids": []})


async def _gather(monkeypatch, cache_over=None, flag="true", npi=None):
    from app.Tefca import sam_extract as s
    from app.Tefca.connectors import normalize_org_name

    if flag is None:
        monkeypatch.delenv(s.ENV_FLAG, raising=False)
    else:
        monkeypatch.setenv(s.ENV_FLAG, flag)
    base = {
        "loaded_at": _time_mod.time(), "row_count": 1,
        "by_npi": {}, "by_name": {normalize_org_name("Bulk Path Test Org"):
                                   [{"Name": "Bulk Path Test Org", "NPI": ""}]},
        "filename": "SAM_Exclusions_Public_Extract_V2_26301.ZIP",
        "publication_date": __import__("datetime").date.today(),
        "date_modified": "Oct 28,2026",
        "http_last_modified": "Wed, 28 Oct 2026 09:39:36 GMT",
        "sha256": "cafe", "source_url": None,
    }
    base.update(cache_over or {})
    monkeypatch.setattr(s, "_SAM_CACHE", base)
    monkeypatch.setattr(s, "_SAM_LOAD_LOCK", None)

    svc = EvidenceService(manager=_StubManager())
    svc.ppef = _StubPPEF()
    svc.revocation = _StubPPEF()

    class _Rel:
        async def practice_locations(self, ids):
            return _R(False, error="unavailable")
        reassignments = practice_locations
        additional_npis = practice_locations
    svc.relational = _Rel()

    ent = {"id": "bulk-test", "name": "Bulk Path Test Org"}
    if npi:
        ent["identifier"] = [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi}]
    return await svc.gather_sources(ent)


@_pytest.mark.asyncio
async def test_bulk_gather_carries_a_candidate_result(monkeypatch):
    sources = await _gather(monkeypatch)
    r = sources["sam_extract_candidate"]
    assert r.success and r.data["candidate_match"] is True
    assert r.data["extract_sha256"] == "cafe"  # edition/hash provenance rides along


@_pytest.mark.asyncio
async def test_bulk_gather_carries_a_no_candidate_result(monkeypatch):
    sources = await _gather(monkeypatch, cache_over={"by_name": {}})
    r = sources["sam_extract_candidate"]
    assert r.success and r.data["candidate_match"] is False


@_pytest.mark.asyncio
async def test_bulk_gather_carries_an_unavailable_result_distinct_from_clean(monkeypatch):
    import datetime as _dt
    stale = _dt.date.today() - _dt.timedelta(days=9)
    sources = await _gather(monkeypatch, cache_over={"publication_date": stale})
    r = sources["sam_extract_candidate"]
    assert r.success is False and "stale_source" in r.error


@_pytest.mark.asyncio
async def test_bulk_gather_flag_off_means_no_key_at_all(monkeypatch):
    sources = await _gather(monkeypatch, flag=None)
    assert "sam_extract_candidate" not in sources
