"""A delivered NPI must reach the screening layer even when promotion withheld its
identifier row because the NPI is shared within the delivery (2026-10-25).

Background (September intake 4417b334, DEV, read-only inspection): 804 entities /
488 distinct NPIs carried an NPI on their curated record but had no
`tefca_entity_identifiers` row of type `npi`, because the same NPI appeared on
more than one record of the delivery and `promotion._add_missing_identifiers`
withholds shared values. `tefca_reg_entities` has no `npi` column, so
`registry_entity_to_evidence_shape` read an empty NPI and NPPES / PECOS / OIG LEIE
screened those organisations by name (NOT_FOUND) or recorded UNAVAILABLE
("entity has no NPI identifier to look up").
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.Tefca import entity_resolution as er
from app.tefca_registry import models as reg
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

SHARED_NPI = "1234567893"  # Luhn-valid test NPI


# ── pure ────────────────────────────────────────────────────────────────────

def _entity_row(**over):
    base = dict(id=uuid.uuid4(), name="Shared NPI Clinic", entity_level="participant",
                rce_org_oid="2.999.1", rce_tefcaid="TEFCA-1", rce_hcid="urn:oid:2.999.1",
                rce_aaid=None, sequoia_org_type="Participant", hl7_org_role="",
                org_managing_org="2.999", org_node_type="", exchange_purposes={"purposes": ["T-TRTMNT"]},
                is_active=True, city="Testville", state="MA", zip="02101", address="1 Synthetic Way",
                rce_attributes=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_curated_fallback_identifier_is_carried_into_the_evidence_shape():
    shape = er.registry_entity_to_evidence_shape(
        _entity_row(), [er.CuratedNpiIdentifier(SHARED_NPI)], parent_tefcaid=None)
    assert shape["_rce"]["NPI"] == SHARED_NPI
    assert {"system": "http://hl7.org/fhir/sid/us-npi", "value": SHARED_NPI} in shape["identifier"]


def test_without_any_npi_the_shape_still_reads_blank():
    shape = er.registry_entity_to_evidence_shape(_entity_row(), [], parent_tefcaid=None)
    assert shape["_rce"]["NPI"] == ""
    assert all("us-npi" not in i["system"] for i in shape["identifier"])


def test_fallback_object_looks_like_an_identifier_row_but_is_marked_as_a_fallback():
    ident = er.CuratedNpiIdentifier(SHARED_NPI)
    assert ident.identifier_type == "npi"
    assert ident.identifier_value == SHARED_NPI
    assert ident.is_primary is False
    assert ident.identifier_status == "curated_record_fallback"


# ── database ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _resolve_from_the_registry(monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")


@pytest.fixture
async def two_records_sharing_one_npi(rolled_back_db):
    """Two promoted entities from one intake, both delivering SHARED_NPI."""
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.777.61", NPI=SHARED_NPI)
    intake_id, _job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    entity_ids = [r[0] for r in (await db.execute(text(
        "SELECT DISTINCT canonical_entity_id FROM rce_curated_records "
        "WHERE source_intake_id = CAST(:iid AS uuid) AND canonical_entity_id IS NOT NULL "
        "ORDER BY 1"), {"iid": str(intake_id)})).all()]
    return db, intake_id, entity_ids


async def test_promotion_withholds_the_shared_npi_identifier_row(two_records_sharing_one_npi):
    """Pins the behaviour this fix compensates for. If promotion ever starts
    writing shared NPIs, this test tells the reader the fallback is now dormant."""
    db, _intake_id, entity_ids = two_records_sharing_one_npi
    assert len(entity_ids) == 2
    rows = (await db.execute(
        select(reg.TefcaEntityIdentifier)
        .where(reg.TefcaEntityIdentifier.entity_id.in_(entity_ids))
        .where(reg.TefcaEntityIdentifier.identifier_type == "npi"))).scalars().all()
    assert rows == []


async def test_resolver_supplies_the_curated_npi_when_no_identifier_row_exists(two_records_sharing_one_npi):
    db, _intake_id, entity_ids = two_records_sharing_one_npi
    for entity_id in entity_ids:
        entity = await er.resolve_entity(db, str(entity_id))
        assert entity is not None
        assert entity["_rce"]["NPI"] == SHARED_NPI
        assert {"system": "http://hl7.org/fhir/sid/us-npi", "value": SHARED_NPI} in entity["identifier"]


async def test_coverage_runner_hands_the_shared_npi_to_the_evidence_service(two_records_sharing_one_npi):
    db, intake_id, _entity_ids = two_records_sharing_one_npi
    seen = []

    async def capture(self, entity):
        seen.append(entity["_rce"]["NPI"])
        return {"dimensions": [{"dimension": "D1_IDENTITY", "disposition": "PASS",
                                "applicability": "APPLICABLE",
                                "evidence": [{"source": "NPPES", "disposition": "PASS"}]}]}

    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=capture):
        result = await av.run_coverage_batch(db, intake_id, batch_size=10)
    assert result["processed_this_call"] == 2
    assert seen == [SHARED_NPI, SHARED_NPI]
