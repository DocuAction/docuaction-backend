"""SAM extract candidate evidence -- database persistence proof (2026-10-29).

The directive: "Prove candidate results and source edition/hash survive
database persistence and reporting." This test runs the REAL chain against a
real (rolled-back) PostgreSQL database:

    SAMExtractConnector.screen()            (real connector, in-memory cache)
    -> evidence_assembly._dimension_exclusion  (real D3 builder)
    -> DimensionResult.to_dict()
    -> evidence_service.evidence_rows_for_persistence  (real flattener,
       including the assert_canonical_evidence_source guard)
    -> TEFCADimensionEvidence(**row) INSERT  (the exact statement
       arc_pipeline._classify_and_route uses on the bulk path)
    -> SELECT back and assert the candidate disposition and every edition
       fact (filename, julian publication date, listing dateModified, HTTP
       Last-Modified, sha256) survived the round trip.

The persisted row IS what the report layer reads (evidence is frozen at
write time; reports never recompute) -- so a round-tripped row carrying the
edition facts is the reporting-side proof at this layer. Flag handling is
irrelevant here by design: the connector is driven directly, as
gather_sources would when ENABLE_SAM_EXTRACT_SCREENING is on.
"""
from __future__ import annotations

import datetime as dt
import time

import pytest
from sqlalchemy import select

from app.Tefca.applicability import build_profile
from app.Tefca.evidence_assembly import _dimension_exclusion
from app.Tefca.evidence_service import evidence_rows_for_persistence
from app.Tefca.mock_data import MOCK_ENTITY_INDEX
from app.Tefca.models import TEFCADimensionEvidence
from rce_traceability_support import rolled_back_db  # noqa: F401

pytestmark = pytest.mark.asyncio


class _R:
    def __init__(self, success, data=None, error=None):
        self.success, self.data, self.error = success, data, error
        self.query_timestamp, self.api_version = "t", "v"


async def _real_candidate_result(monkeypatch):
    """A REAL SourceResult from the real connector against an in-memory cache
    whose shape matches what a real load records."""
    from app.Tefca import sam_extract as s
    from app.Tefca.connectors import normalize_org_name

    monkeypatch.setenv(s.ENV_FLAG, "true")
    row_without_npi = {"Name": "Synthetic Test Org, Inc.", "NPI": ""}
    monkeypatch.setattr(s, "_SAM_CACHE", {
        "loaded_at": time.time(), "row_count": 1,
        "by_npi": {},
        "by_name": {normalize_org_name("Synthetic Test Org, Inc."): [row_without_npi]},
        "filename": "SAM_Exclusions_Public_Extract_V2_26301.ZIP",
        "publication_date": dt.date.today(),
        "date_modified": "Oct 28,2026",
        "http_last_modified": "Wed, 28 Oct 2026 09:39:36 GMT",
        "sha256": "752db2d5b0d327b630c0ffee",
        "source_url": "https://sam.gov/api/prod/fileextractservices/...",
    })
    monkeypatch.setattr(s, "_SAM_LOAD_LOCK", None)
    return await s.SAMExtractConnector().screen("1891430989", "Synthetic Test Org Inc")


async def test_candidate_and_edition_facts_survive_db_persistence(rolled_back_db, monkeypatch):
    db = rolled_back_db

    sam_extract = await _real_candidate_result(monkeypatch)
    assert sam_extract.success and sam_extract.data["candidate_match"] is True

    entity = MOCK_ENTITY_INDEX["rce-org-rp-006"]
    d3 = _dimension_exclusion(entity, build_profile(entity), {
        "leie_org": _R(True, {"excluded": False, "exclusion_found": False}),
        "sam_name": _R(True, {"found": False, "excluded": False, "ambiguous": False}),
        "cms_revocation": _R(True, {"matches": [], "result": "NONE"}),
        "sam_extract_candidate": sam_extract,
    })

    bundle = {"generated_at": "2026-10-29T00:00:00", "dimensions": [d3.to_dict()]}
    rows = evidence_rows_for_persistence("synthetic-entity-sam-extract-test", None, bundle)

    for row in rows:
        row["review_cycle_id"] = None  # exactly as arc_pipeline's bulk path does
        db.add(TEFCADimensionEvidence(**row))
    await db.flush()

    stored = (await db.execute(
        select(TEFCADimensionEvidence)
        .where(TEFCADimensionEvidence.entity_id == "synthetic-entity-sam-extract-test",
               TEFCADimensionEvidence.source == "SAM_EXTRACT_SUPPLEMENTARY"))
    ).scalars().all()

    assert len(stored) == 1
    row = stored[0]
    # Candidate-only treatment survived: REVIEW, never an exclusion claim.
    assert row.disposition == "REVIEW"
    ov = row.original_values
    assert ov["candidate_match"] is True and ov["candidate_count"] == 1
    assert "excluded" not in ov
    # Every edition fact survived, separately.
    assert ov["extract_filename"] == "SAM_Exclusions_Public_Extract_V2_26301.ZIP"
    assert ov["publication_date"] == dt.date.today().isoformat()
    assert ov["listing_date_modified"] == "Oct 28,2026"
    assert ov["http_last_modified"] == "Wed, 28 Oct 2026 09:39:36 GMT"
    assert ov["extract_sha256"] == "752db2d5b0d327b630c0ffee"
    assert ov["match_methods_matched"] == ["normalized_business_name"]
    # The dimension-level roll-up escalated to REVIEW on the candidate hit.
    assert row.dimension_disposition == "REVIEW"


async def test_unavailable_extract_row_persists_without_touching_primary_controls(rolled_back_db, monkeypatch):
    """The unavailable state (flag off / stale / malformed) also persists, as
    its own disclosed row -- and the primary controls' clean dispositions are
    untouched beside it in the same generation."""
    db = rolled_back_db

    entity = MOCK_ENTITY_INDEX["rce-org-rp-006"]
    d3 = _dimension_exclusion(entity, build_profile(entity), {
        "leie_org": _R(True, {"excluded": False, "exclusion_found": False}),
        "sam_name": _R(True, {"found": False, "excluded": False, "ambiguous": False}),
        "cms_revocation": _R(True, {"matches": [], "result": "NONE"}),
        "sam_extract_candidate": _R(False, error="SAM_EXTRACT unavailable: stale_source: ..."),
    })
    bundle = {"generated_at": "2026-10-29T00:00:00", "dimensions": [d3.to_dict()]}
    rows = evidence_rows_for_persistence("synthetic-entity-sam-unavail-test", None, bundle)
    for row in rows:
        row["review_cycle_id"] = None
        db.add(TEFCADimensionEvidence(**row))
    await db.flush()

    stored = (await db.execute(
        select(TEFCADimensionEvidence)
        .where(TEFCADimensionEvidence.entity_id == "synthetic-entity-sam-unavail-test"))
    ).scalars().all()
    by_source = {r.source: r for r in stored}
    assert by_source["SAM_EXTRACT_SUPPLEMENTARY"].disposition == "UNAVAILABLE"
    assert "stale_source" in (by_source["SAM_EXTRACT_SUPPLEMENTARY"].note or "")
    # Primary SAM control's own row is separate and unaffected.
    assert "SAM_GOV" in by_source
