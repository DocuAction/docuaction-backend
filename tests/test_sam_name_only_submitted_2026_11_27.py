"""Name-only SAM-extract screening on SUBMITTED values for a delivered record with NO NPI (owner, 2026-11-27).

Identity-first (2026-11-25) refuses an edition screen when the registry entity carries an NPI the delivery did not
submit. For a record that submitted NO NPI that refusal also blocked the normalized-NAME screen methodology §SAM
requires. With the explicit opt-in `name_only_submitted` (edition-pinned scope only) such a record is screened on its
delivered name alone, and the result is RECORD-SCOPED (owner authorization after the independent review of #258):
  * eligibility is the classifier's exactly: `arc_pipeline._submitted_npi(record) == ""` and the delivery's
    assessment date on/after `BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE`; any non-blank submitted value ("N/A", another
    NPI) keeps its refusal, and an earlier delivery is unchanged;
  * every other identity check still holds (queued entity, exactly one delivered record, readable record, a name);
  * no NPI is sent to the extract;
  * the result is one `rce_record_sam_screenings` row bound to the delivery, the record, the basis, the pinned edition
    and the screening time/actor; NOTHING is written to the entity evidence store, so the entity is not covered and
    no sibling record's NPI-based screening is satisfied or replaced;
  * a candidate is a pending SAM_EXTRACT_CANDIDATE finding on THAT record, never absorbed by a sibling's finding;
    entity status is untouched;
  * every name-only call is audited (ids and counts only);
  * without the flag nothing changes.
Synthetic data only; every source call is mocked.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.Tefca.connectors import SourceResult
from app.Tefca.models import TEFCADimensionEvidence
from app.tefca_registry import models as reg
from app.tefca_registry.rce import assessment_context
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import verification_findings as vf
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_entity, seed_intake  # noqa: F401
from test_npi_outcome_record_anchoring_2026_11_16 import NPI, T0
from test_recheck_identity_validation_2026_11_16 import OTHER_NPI, _one_delivery, _resolved, _state, _untouched

pytestmark = pytest.mark.asyncio

EXTRACT = av.EXTRACT_SOURCE
PIN = "a" * 64
EDITION = {"publication_date": "2026-11-26", "extract_sha256": PIN, "extract_filename": "SAM_Exclusions_Public_Extract_V2_26330.ZIP"}


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")
    # A delivery assessed on/after the blank-NPI effective date unless a test says otherwise (deterministic: never
    # the wall clock).
    monkeypatch.setattr(assessment_context, "resolve_assessment_date",
                        AsyncMock(return_value=SimpleNamespace(value=date(2026, 11, 26), source="run_metadata")))


def _screen(candidate: bool) -> SourceResult:
    return SourceResult(source_name="SAM_EXTRACT", success=True,
                        data={"candidate_match": candidate, "candidate_count": 1 if candidate else 0, "candidates": [],
                              "match_methods": ["name"], "match_methods_matched": ["name"] if candidate else [],
                              **EDITION},
                        query_params={"npi": None, "org": "x"}, api_version="GSA-Public-Extract-V2")


async def _attempt(db, *, ent, intake, ref, resolved, flag=True, candidate=True, pinned=PIN, actor="pm@example.test"):
    screen = AsyncMock(return_value=_screen(candidate))
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=screen):
        out = await av.record_sam_extract_attempt(db, ref=ref, canonical_entity_id=ent, intake_id=intake,
                                                  required_edition_sha256=pinned, name_only_submitted=flag,
                                                  actor=actor)
    await db.flush()
    return out, screen


async def _entity_rows(db, ent):
    return (await db.execute(select(TEFCADimensionEvidence).where(
        TEFCADimensionEvidence.entity_id == str(ent)))).scalars().all()


async def _screenings(db, intake):
    return (await db.execute(select(m.RceRecordSamScreening).where(
        m.RceRecordSamScreening.source_intake_id == intake))).scalars().all()


async def _candidates(db, intake):
    return (await db.execute(select(m.RceIssue).where(
        m.RceIssue.source_intake_id == intake, m.RceIssue.issue_type == vf.SAM_EXTRACT_CANDIDATE))).scalars().all()


async def _covered_for_pin(db, intake):
    return (await av.coverage_progress(db, intake, scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=PIN))


async def _seed(db, arc, delivered):
    """ONE entity carrying NPI, linked by one delivered reference per (npi, name) in `delivered` (name None: the
    synthetic default). Delivered records are immutable, so every variant is seeded, never edited."""
    ent = await seed_entity(db, oid=f"{SYN}-NO-{uuid.uuid4().hex[:8]}", name=f"{SYN} name-only entity", npi=NPI)
    rows = make_rows(len(delivered), arc=arc)
    for row, (npi, name) in zip(rows, delivered):
        row["NPI"] = npi
        if name is not None:
            row["name"] = name
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    for r in recs:
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=r.id,
                                  record_status="CLEAN", rce_org_oid=r.source_rce_id, transformation_version="test",
                                  canonical_entity_id=ent, promoted_at=T0))
    await db.flush()
    return ent, intake_id, recs


async def _siblings(db, arc):
    """ONE entity (carrying NPI) linked by TWO delivered references in one delivery: R1 submitted that NPI, R2
    submitted none. The cross-record isolation case the review raised."""
    ent = await seed_entity(db, oid=f"{SYN}-SIB-{uuid.uuid4().hex[:8]}", name=f"{SYN} sibling entity", npi=NPI)
    rows = make_rows(2, arc=arc)
    rows[0]["NPI"], rows[1]["NPI"] = NPI, ""
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    for r in recs:
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=r.id,
                                  record_status="CLEAN", rce_org_oid=r.source_rce_id, transformation_version="test",
                                  canonical_entity_id=ent, promoted_at=T0))
    await db.flush()
    r1 = next(r for r in recs if (r.parsed or {}).get("NPI") == NPI)
    r2 = next(r for r in recs if r is not r1)
    return ent, intake_id, r1, r2


# ── the correction: record-scoped ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("candidate", [True, False])
async def test_blank_npi_record_is_screened_on_its_submitted_name_and_recorded_on_the_record(rolled_back_db,
                                                                                            candidate):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.778.10", entity_npi=NPI, delivered_npi="")
    before = await _covered_for_pin(db, intake)
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=rec.source_rce_id, resolved=_resolved(ent),
                                 candidate=candidate)
    npi_arg, name_arg = screen.await_args.args
    assert npi_arg is None                                         # no NPI at all is sent - not the registry's
    assert name_arg == rec.parsed["name"]                          # the delivered record's ORIGINAL submitted name
    assert out["outcome"] == av.OUTCOME_NAME_ONLY_RECORDED and out["identity_confirmed"] is False
    assert out["name_only_outcome"] == (av.NAME_ONLY_CANDIDATE if candidate else av.NAME_ONLY_NO_CANDIDATE)
    assert out["source_record_id"] == str(rec.id)

    (row,) = await _screenings(db, intake)                         # bound to delivery + record + basis + edition
    assert (row.source_intake_id, row.source_record_id, row.canonical_entity_id) == (intake, rec.id, ent)
    assert row.screen_basis == av.SCREEN_BASIS_NAME_ONLY_SUBMITTED and row.identity_confirmed is False
    assert (row.extract_sha256, row.publication_date, row.extract_filename) == (
        PIN, EDITION["publication_date"], EDITION["extract_filename"])
    assert row.screened_at is not None and row.screened_by == "pm@example.test"
    assert row.outcome == out["name_only_outcome"]

    assert await _entity_rows(db, ent) == []                       # NOTHING in the entity evidence store
    assert await _covered_for_pin(db, intake) == before            # the entity is NOT counted as covered
    issues = await _candidates(db, intake)
    if candidate:
        (issue,) = issues                                          # pending, on THIS record, linked from the row
        assert issue.source_record_id == rec.id and issue.resolution == "OPEN" and row.issue_id == issue.id
        assert "NAME-ONLY" in issue.description and "identity NOT confirmed" in issue.description
    else:
        assert issues == [] and row.issue_id is None
    assert (await db.get(reg.TefcaRegEntity, ent)).verification_status == "not_verified"  # status untouched


async def test_without_the_flag_the_blank_npi_record_is_still_refused(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.11", entity_npi=NPI, delivered_npi="")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent),
                                 flag=False)
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))
    assert await _screenings(db, intake) == []


async def test_the_flag_needs_a_pinned_edition(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.12", entity_npi=NPI, delivered_npi="")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent),
                                 pinned=None)
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))
    assert await _screenings(db, intake) == []


# ── the classifier's blank-NPI definition and effective date ────────────────────────────────────────────────────

@pytest.mark.parametrize("delivered", ["N/A", "PENDING", "TBD", "0", OTHER_NPI])
async def test_any_non_blank_submitted_value_keeps_its_refusal(rolled_back_db, delivered):
    """The classifier treats every non-blank value as a SUBMITTED NPI (`_submitted_npi` strips only). Digits-only
    normalisation would have made "N/A" look blank; it must not."""
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.13", entity_npi=NPI, delivered_npi=delivered)
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))
    assert await _screenings(db, intake) == []


async def test_a_whitespace_only_npi_is_blank_as_the_classifier_reads_it(rolled_back_db):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.778.14", entity_npi=NPI, delivered_npi="   ")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=rec.source_rce_id, resolved=_resolved(ent))
    assert screen.await_args.args[0] is None
    assert out["outcome"] == av.OUTCOME_NAME_ONLY_RECORDED
    assert len(await _screenings(db, intake)) == 1


async def test_a_delivery_assessed_before_the_effective_date_is_unchanged(rolled_back_db, monkeypatch):
    db = rolled_back_db
    monkeypatch.setattr(assessment_context, "resolve_assessment_date",
                        AsyncMock(return_value=SimpleNamespace(value=date(2026, 11, 8), source="run_metadata")))   # the October delivery
    ent, intake, recs = await _one_delivery(db, "9.99.778.15", entity_npi=NPI, delivered_npi="")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))
    assert await _screenings(db, intake) == []


async def test_a_wall_clock_fallback_assessment_date_is_refused(rolled_back_db, monkeypatch):
    """No completed run: the resolver falls back to UTC today, which would let an earlier delivery pass the guard."""
    db = rolled_back_db
    monkeypatch.setattr(assessment_context, "resolve_assessment_date", AsyncMock(return_value=SimpleNamespace(
        value=date(2026, 11, 27), source=assessment_context.SOURCE_FALLBACK_NO_RUN)))
    ent, intake, recs = await _one_delivery(db, "9.99.778.27", entity_npi=NPI, delivered_npi="")
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    assert await _screenings(db, intake) == []


@pytest.mark.parametrize("name", ["", "   "])
async def test_a_record_with_no_submitted_name_keeps_its_refusal(rolled_back_db, name):
    db = rolled_back_db
    ent, intake, (rec,) = await _seed(db, "9.99.778.16", [("", name)])
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=rec.source_rce_id, resolved=_resolved(ent))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    assert await _screenings(db, intake) == []


# ── identity safeguards that must still refuse ──────────────────────────────────────────────────────────────────

async def test_a_blank_npi_reference_that_resolves_to_another_entity_keeps_its_refusal(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.778.17", entity_npi=NPI, delivered_npi="")
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
    ent, intake, recs = await _one_delivery(db, "9.99.778.18", entity_npi=NPI, delivered_npi="", duplicate=True)
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=recs[0].source_rce_id, resolved=_resolved(ent))
    screen.assert_not_awaited()
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED
    _untouched(await _state(db, ent, *recs))


async def test_a_correctly_identified_record_is_screened_exactly_as_before_with_the_flag_on(rolled_back_db):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.778.19", entity_npi=NPI, delivered_npi=NPI)
    out, screen = await _attempt(db, ent=ent, intake=intake, ref=rec.source_rce_id, resolved=_resolved(ent))
    assert screen.await_args.args[0] == NPI                        # NPI + name, the ordinary screen
    assert "screen_basis" not in out and out["outcome"] != av.OUTCOME_NAME_ONLY_RECORDED
    (row,) = await _entity_rows(db, ent)
    assert "screen_basis" not in (row.original_values or {})
    assert await _screenings(db, intake) == []


# ── cross-record isolation ──────────────────────────────────────────────────────────────────────────────────────

async def test_a_name_only_screen_never_satisfies_a_sibling_records_npi_screen(rolled_back_db):
    """R2 (blank NPI) is name-screened first. The entity must still be NOT covered for the edition, and R1 (which
    submitted the NPI) must still get its own NPI-based screen, written to the entity store as before."""
    db = rolled_back_db
    ent, intake, r1, r2 = await _siblings(db, "9.99.778.20")
    before = await _covered_for_pin(db, intake)
    out2, _ = await _attempt(db, ent=ent, intake=intake, ref=r2.source_rce_id, resolved=_resolved(ent),
                             candidate=False)
    assert out2["outcome"] == av.OUTCOME_NAME_ONLY_RECORDED and out2["source_record_id"] == str(r2.id)
    assert await _entity_rows(db, ent) == []
    assert await _covered_for_pin(db, intake) == before            # R2's PASS stands for nothing but R2

    out1, screen1 = await _attempt(db, ent=ent, intake=intake, ref=r1.source_rce_id, resolved=_resolved(ent),
                                   candidate=True)
    assert screen1.await_args.args[0] == NPI                       # R1 screened on ITS NPI
    assert out1["outcome"] != av.OUTCOME_NAME_ONLY_RECORDED
    (entity_row,) = await _entity_rows(db, ent)
    assert entity_row.original_values["extract_sha256"] == PIN and "screen_basis" not in entity_row.original_values
    (screening,) = await _screenings(db, intake)
    assert screening.source_record_id == r2.id                     # the record-scoped row names R2 only


async def test_a_name_only_candidate_is_not_absorbed_by_a_siblings_open_finding(rolled_back_db):
    """R1's NPI screen opens the entity-level EXC-001 first; R2's name-only candidate still gets ITS OWN finding."""
    db = rolled_back_db
    ent, intake, r1, r2 = await _siblings(db, "9.99.778.21")
    await _attempt(db, ent=ent, intake=intake, ref=r1.source_rce_id, resolved=_resolved(ent), candidate=True)
    await _attempt(db, ent=ent, intake=intake, ref=r2.source_rce_id, resolved=_resolved(ent), candidate=True)
    issues = await _candidates(db, intake)
    assert sorted(i.source_record_id for i in issues) == sorted([r1.id, r2.id])
    (screening,) = await _screenings(db, intake)
    assert screening.issue_id == next(i.id for i in issues if i.source_record_id == r2.id)


async def test_an_npi_candidate_is_not_absorbed_by_a_siblings_name_only_finding(rolled_back_db):
    """The reverse order: R2's NAME-ONLY candidate is opened first; R1's NPI-basis candidate must still get its own
    finding, so ruling out R2's (a different organisation's name) cannot leave the NPI match without a work item."""
    db = rolled_back_db
    ent, intake, r1, r2 = await _siblings(db, "9.99.778.28")
    await _attempt(db, ent=ent, intake=intake, ref=r2.source_rce_id, resolved=_resolved(ent), candidate=True)
    out1, screen1 = await _attempt(db, ent=ent, intake=intake, ref=r1.source_rce_id, resolved=_resolved(ent),
                                   candidate=True)
    assert screen1.await_args.args[0] == NPI and out1["outcome"] != av.OUTCOME_NAME_ONLY_RECORDED
    issues = await _candidates(db, intake)
    assert sorted(i.source_record_id for i in issues) == sorted([r1.id, r2.id])
    (screening,) = await _screenings(db, intake)
    r1_issue = next(i for i in issues if i.source_record_id == r1.id)
    assert screening.issue_id != r1_issue.id and "NAME-ONLY" not in r1_issue.description


async def test_the_record_scoped_candidate_writer_requires_its_record():
    with pytest.raises(ValueError, match="record-scoped"):
        await vf.record_sam_extract_candidate(None, entity_id=uuid.uuid4(), npi=None, org_name="x",
                                              name_only_submitted=True)


# ── the batch (database) ────────────────────────────────────────────────────────────────────────────────────────

async def _batch(db, intake, *, resolved, candidate=False):
    screen = AsyncMock(return_value=_screen(candidate))
    with patch.object(av, "pinned_edition_problem", new=AsyncMock(return_value=None)), \
            patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=resolved)), \
            patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=screen):
        return await av.run_coverage_batch(db, intake, batch_size=10, scope=av.SCOPE_SAM_EXTRACT_EDITION,
                                           edition_sha256=PIN, name_only_submitted=True, actor="pm@example.test")


async def test_the_batch_records_name_only_screens_once_and_never_counts_them_as_covered(rolled_back_db):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.778.22", entity_npi=NPI, delivered_npi="")
    first = await _batch(db, intake, resolved=lambda _db, _ref: _resolved(ent), candidate=True)
    assert first["name_only_screened"] == 1 and first["processed_this_call"] == 1
    assert first["name_only_by_outcome"] == {av.NAME_ONLY_CANDIDATE: 1}
    assert first["name_only_recorded_for_edition"] == {av.NAME_ONLY_CANDIDATE: 1, "total": 1}
    assert first["covered"] == 0 and first["remaining"] == 1       # entity coverage (NPI basis) unchanged
    second = await _batch(db, intake, resolved=lambda _db, _ref: _resolved(ent), candidate=True)
    assert second["name_only_screened"] == 0 and second["processed_this_call"] == 0   # not selected again
    assert len(await _screenings(db, intake)) == 1 and len(await _candidates(db, intake)) == 1


async def test_the_batch_still_screens_a_sibling_reference_on_its_npi(rolled_back_db):
    db = rolled_back_db
    ent, intake, r1, r2 = await _siblings(db, "9.99.778.23")
    await _attempt(db, ent=ent, intake=intake, ref=r2.source_rce_id, resolved=_resolved(ent), candidate=False)
    result = await _batch(db, intake, resolved=lambda _db, _ref: _resolved(ent))
    assert result["covered"] == 1 and result["remaining"] == 0     # R1's NPI screen covered the entity
    (entity_row,) = await _entity_rows(db, ent)
    assert "screen_basis" not in (entity_row.original_values or {})


# ── the store: append-only, anchored, migration-owned ───────────────────────────────────────────────────────────

async def test_the_store_is_append_only_and_refuses_a_row_for_another_delivery(rolled_back_db):
    from sqlalchemy import text
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.778.24", entity_npi=NPI, delivered_npi="")
    await _attempt(db, ent=ent, intake=intake, ref=rec.source_rce_id, resolved=_resolved(ent), candidate=False)
    for stmt in ("update rce_record_sam_screenings set outcome = 'CANDIDATE'", "delete from rce_record_sam_screenings"):
        sp = await db.begin_nested()
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
        assert "append-only" in str(exc.value)
        await sp.rollback()
    _other_ent, other_intake, _recs = await _one_delivery(db, "9.99.778.25", entity_npi=OTHER_NPI, delivered_npi="")
    sp = await db.begin_nested()
    with pytest.raises(Exception) as exc:                          # the record is not in the named delivery
        db.add(m.RceRecordSamScreening(id=uuid.uuid4(), source_intake_id=other_intake, source_record_id=rec.id,
                                       screen_basis=av.SCREEN_BASIS_NAME_ONLY_SUBMITTED, extract_sha256=PIN,
                                       outcome=av.NAME_ONLY_NO_CANDIDATE, screened_by="t"))
        await db.flush()
    assert "does not belong to intake" in str(exc.value)
    await sp.rollback()


async def test_a_name_only_batch_refuses_cleanly_when_the_store_is_not_applied(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, intake, _recs = await _one_delivery(db, "9.99.778.26", entity_npi=NPI, delivered_npi="")

    async def absent(_db):
        raise av.NameOnlyStoreNotApplied("NAME_ONLY_STORE_NOT_APPLIED: not applied")

    monkeypatch.setattr(av, "_assert_name_only_store", absent)
    with pytest.raises(av.NameOnlyStoreNotApplied):
        await _batch(db, intake, resolved=lambda _db, _ref: _resolved(ent))
    assert await _screenings(db, intake) == [] and await _entity_rows(db, ent) == []


async def test_the_store_is_migration_owned():
    from app.tefca_registry.rce.traceability_models import MIGRATION_OWNED_TABLES
    assert "rce_record_sam_screenings" in MIGRATION_OWNED_TABLES


# ── wiring and audit ────────────────────────────────────────────────────────────────────────────────────────────

async def test_the_flag_is_refused_outside_the_edition_scope():
    with pytest.raises(ValueError, match="name_only_submitted"):
        await av.run_coverage_batch(None, uuid.uuid4(), scope=av.SCOPE_FULL, name_only_submitted=True)


async def test_the_route_takes_the_flag_defaults_it_off_passes_it_through_and_audits_every_call():
    from app.tefca_registry.rce import delivery_routes as dr
    param = inspect.signature(dr.run_verification_coverage).parameters["name_only_submitted"]
    assert param.default.default is False
    source = inspect.getsource(dr.run_verification_coverage)
    assert "name_only_submitted=name_only_submitted" in source
    assert 'require_role("program_manager")' in source
    assert '"verification_coverage_name_only_screening"' in source
    assert "if name_only_submitted:" in source


async def test_the_audit_carries_ids_counts_and_edition_never_a_name():
    rec_id = str(uuid.uuid4())
    result = {"scope": av.SCOPE_SAM_EXTRACT_EDITION, "identity_refused": 2,
              "by_outcome": {av.OUTCOME_NAME_ONLY_RECORDED: 1, av.OUTCOME_RETRY_PENDING: 1},
              "name_only_by_outcome": {av.NAME_ONLY_NO_CANDIDATE: 1},
              "name_only_recorded_for_edition": {av.NAME_ONLY_NO_CANDIDATE: 7, "total": 7},
              "outcomes": [{"outcome": av.OUTCOME_NAME_ONLY_RECORDED, "ref": "SECRET-REF",
                            "source_record_id": rec_id, "name_only_outcome": av.NAME_ONLY_NO_CANDIDATE},
                           {"outcome": av.OUTCOME_RETRY_PENDING, "ref": "r2"}]}
    from app.tefca_registry.rce import delivery_routes as dr
    meta = dr.name_only_audit_metadata("intake-1", result, PIN)
    assert meta["screen_basis"] == av.SCREEN_BASIS_NAME_ONLY_SUBMITTED and meta["identity_confirmed"] is False
    assert meta["edition_sha256"] == PIN and meta["name_only_screened"] == 1 and meta["retry_pending"] == 1
    assert meta["screened_records"] == [{"source_record_id": rec_id, "outcome": av.NAME_ONLY_NO_CANDIDATE}]
    assert meta["entity_evidence_written_for_name_only"] is False
    assert "SECRET-REF" not in repr(meta)
