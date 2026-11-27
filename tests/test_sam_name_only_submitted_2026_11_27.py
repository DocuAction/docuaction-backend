"""Name-only SAM-extract screening on SUBMITTED values for a delivered record with NO NPI (owner, 2026-11-27).

Identity-first (2026-11-25) refuses an edition screen when the registry entity carries an NPI the delivery did not
submit. For a record that submitted NO NPI that refusal also blocked the normalized-NAME screen methodology §SAM
requires. With the explicit opt-in `name_only_submitted` (edition-pinned scope only) such a record is screened on its
delivered name alone:
  * every other identity check still holds (queued entity, exactly one delivered record, readable record);
  * no NPI is sent to the extract - neither the registry's nor any other;
  * the row is attributed to the pinned edition and marked screen_basis=SUBMITTED_VALUES_NAME_ONLY,
    identity_confirmed=false (screening is recorded separately from confirmed identity);
  * a candidate is a pending SAM_EXTRACT_CANDIDATE finding on the delivered record; entity status is untouched;
  * a record that DID submit an NPI (an NPI mismatch) keeps its refusal, as does every other refusal;
  * without the flag nothing changes.
Synthetic data only; every source call is mocked.
"""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.Tefca.connectors import SourceResult
from app.Tefca.models import TEFCADimensionEvidence
from app.tefca_registry import models as reg
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import verification_findings as vf
from rce_traceability_support import SYN, rolled_back_db, seed_entity  # noqa: F401
from test_npi_outcome_record_anchoring_2026_11_16 import NPI
from test_recheck_identity_validation_2026_11_16 import OTHER_NPI, _one_delivery, _resolved, _state, _untouched

pytestmark = pytest.mark.asyncio

EXTRACT = av.EXTRACT_SOURCE
PIN = "a" * 64
EDITION = {"publication_date": "2026-11-26", "extract_sha256": PIN, "extract_filename": "SAM_Exclusions_Public_Extract_V2_26330.ZIP"}


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")


def _screen(candidate: bool) -> SourceResult:
    return SourceResult(source_name="SAM_EXTRACT", success=True,
                        data={"candidate_match": candidate, "candidate_count": 1 if candidate else 0, "candidates": [],
                              "match_methods": ["name"], "match_methods_matched": ["name"] if candidate else [],
                              **EDITION},
                        query_params={"npi": None, "org": "x"}, api_version="GSA-Public-Extract-V2")


async def _attempt(db, *, ent, intake, ref, resolved, flag=True, candidate=True, pinned=PIN):
    screen = AsyncMock(return_value=_screen(candidate))
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=screen):
        out = await av.record_sam_extract_attempt(db, ref=ref, canonical_entity_id=ent, intake_id=intake,
                                                  required_edition_sha256=pinned, name_only_submitted=flag)
    await db.flush()
    return out, screen


async def _rows(db, ent):
    return (await db.execute(select(TEFCADimensionEvidence).where(
        TEFCADimensionEvidence.entity_id == str(ent)))).scalars().all()


async def _candidates(db, intake):
    return (await db.execute(select(m.RceIssue).where(
        m.RceIssue.source_intake_id == intake, m.RceIssue.issue_type == vf.SAM_EXTRACT_CANDIDATE))).scalars().all()


# ── the correction ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("candidate", [True, False])
async def test_blank_npi_record_is_screened_on_its_submitted_name_only(rolled_back_db, candidate):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.778.10", entity_npi=NPI, delivered_npi="")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=rec.source_rce_id, resolved=_resolved(ent),
                                 candidate=candidate)
    npi_arg, name_arg = screen.await_args.args
    assert npi_arg is None                                         # no NPI at all is sent - not the registry's
    assert name_arg == rec.parsed["name"]                          # the delivered record's ORIGINAL submitted name
    assert out["screen_basis"] == av.SCREEN_BASIS_NAME_ONLY_SUBMITTED
    assert out["outcome"] != av.OUTCOME_IDENTITY_REFUSED and out["candidate_match"] is candidate
    rows = await _rows(db, ent)
    assert len(rows) == 1 and rows[0].source == EXTRACT
    ov = rows[0].original_values
    assert ov["extract_sha256"] == PIN                             # attributed to the pinned edition
    assert ov["screen_basis"] == av.SCREEN_BASIS_NAME_ONLY_SUBMITTED
    assert ov["registry_npi_not_used"] is True and ov["identity_confirmed"] is False
    assert rows[0].disposition == ("REVIEW" if candidate else "PASS")
    issues = await _candidates(db, intake)
    assert [(i.source_record_id,) for i in issues] == ([(rec.id,)] if candidate else [])   # pending, on the record
    assert (await db.get(reg.TefcaRegEntity, ent)).verification_status == "not_verified"  # status untouched


async def test_without_the_flag_the_blank_npi_record_is_still_refused(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.11", entity_npi=NPI, delivered_npi="")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent),
                                 flag=False)
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))


async def test_the_flag_needs_a_pinned_edition(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.12", entity_npi=NPI, delivered_npi="")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent),
                                 pinned=None)
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))


# ── identity safeguards that must still refuse ──────────────────────────────────────────────────────────────────

async def test_a_record_that_submitted_a_different_npi_keeps_its_refusal(rolled_back_db):
    """The NPI-mismatch record is a person's question, never a name-only screen."""
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.13", entity_npi=OTHER_NPI, delivered_npi=NPI)
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id,
                                 resolved=_resolved(ent, OTHER_NPI))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))


async def test_a_blank_npi_reference_that_resolves_to_another_entity_keeps_its_refusal(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.14", entity_npi=NPI, delivered_npi="")
    # Seeded WITHOUT an NPI, as in test_coverage_identity_gate's same case: seeding a second entity with the same NPI
    # would move the first entity's NPI identifier and break the "untouched" baseline, not test the refusal.
    other = await seed_entity(db, oid=f"{SYN}-NAMEONLY-{uuid.uuid4().hex[:8]}", name=f"{SYN} other")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id,
                                 resolved=_resolved(other, NPI))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))


async def test_a_blank_npi_reference_delivered_twice_keeps_its_refusal(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.15", entity_npi=NPI, delivered_npi="", duplicate=True)
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))


async def test_a_correctly_identified_record_is_screened_exactly_as_before_with_the_flag_on(rolled_back_db):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.778.16", entity_npi=NPI, delivered_npi=NPI)
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=rec.source_rce_id, resolved=_resolved(ent))
    assert screen.await_args.args[0] == NPI                        # NPI + name, the ordinary screen
    assert "screen_basis" not in out
    (row,) = await _rows(db, ent)
    assert "screen_basis" not in (row.original_values or {})


# ── wiring (pure) ───────────────────────────────────────────────────────────────────────────────────────────────

async def test_the_flag_is_refused_outside_the_edition_scope():
    with pytest.raises(ValueError, match="name_only_submitted"):
        await av.run_coverage_batch(None, uuid.uuid4(), scope=av.SCOPE_FULL, name_only_submitted=True)


async def test_the_route_takes_the_flag_defaults_it_off_and_passes_it_through():
    from app.tefca_registry.rce import delivery_routes as dr
    param = inspect.signature(dr.run_verification_coverage).parameters["name_only_submitted"]
    assert param.default.default is False
    source = inspect.getsource(dr.run_verification_coverage)
    assert "name_only_submitted=name_only_submitted" in source
    assert 'require_role("program_manager")' in source
