"""Edition-bound SAM public-extract screening (`scope="sam_extract_edition"`, 2026-11-26).

Methodology v1.4: screen against a DATED extract, run checks against fixed source versions and append
attributable outcomes. The plain `sam_extract` scope skips an entity that has ANY extract row, so an entity
screened against an October edition kept its October answer. These tests pin that the edition scope:

* selects every eligible entity with no row for the PINNED edition, whatever older rows it has;
* appends a new row carrying the edition (filename, publication date, sha256) and leaves older rows untouched;
* refuses to start when the loaded edition is not the pinned one, and never persists an answer from another
  edition if the extract changes mid-run;
* records an entity the pinned edition could not screen (no usable identifier) as attempted-not-screened,
  attributed to that edition - never as clear, never re-selected forever.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.Tefca.connectors import SourceResult
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

OLD = "a" * 64
NEW = "b" * 64


@pytest.fixture(autouse=True)
def _resolve_entities_from_the_registry(monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")


def _edition(sha, pub):
    return {"extract_filename": f"SAM_Exclusions_Public_Extract_V2_{pub}.ZIP", "publication_date": pub,
            "extract_sha256": sha, "list_rows_searched": 10}


def _answer(sha, pub, candidate=False):
    return SourceResult(source_name="SAM_EXTRACT", success=True, api_version="GSA-Public-Extract-V2",
                        data={"candidate_match": candidate, "candidate_count": int(candidate),
                              "match_methods": ["npi", "normalized_business_name"],
                              "match_methods_matched": ["npi"] if candidate else [],
                              **_edition(sha, pub)})


def _loaded(sha, pub):
    return (patch("app.Tefca.sam_extract.screening_not_ready_reason", new=AsyncMock(return_value=None)),
            patch("app.Tefca.sam_extract.loaded_edition", return_value=_edition(sha, pub)))


async def _rows(db, intake_id):
    return (await db.execute(text(
        "SELECT d.entity_id, d.disposition, d.original_values->>'extract_sha256' AS sha, "
        "       d.original_values->>'publication_date' AS pub, d.original_values->>'screen_status' AS st "
        "FROM tefca_dimension_evidence d WHERE d.source = 'SAM_EXTRACT_SUPPLEMENTARY' "
        "  AND d.entity_id IN (SELECT CAST(r.canonical_entity_id AS TEXT) FROM rce_curated_records r "
        "                      WHERE r.source_intake_id = CAST(:i AS uuid))"), {"i": str(intake_id)})).all()


@pytest.fixture
async def screened_on_old_edition(rolled_back_db):
    """Three promoted entities, each already carrying an extract row from an OLDER edition."""
    db = rolled_back_db
    intake_id, _job = await seed_intake(db, make_rows(3, arc="9.99.777.71"))
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(return_value=_answer(OLD, "2026-10-30"))):
        first = await av.run_coverage_batch(db, intake_id, batch_size=100, scope=av.SCOPE_SAM_EXTRACT)
    assert first["processed_this_call"] == 3
    return db, intake_id


async def test_older_edition_rows_do_not_cover_the_pinned_edition(screened_on_old_edition):
    db, intake_id = screened_on_old_edition
    # The plain scope considers them covered; the edition scope does not.
    assert (await av.coverage_progress(db, intake_id, scope=av.SCOPE_SAM_EXTRACT))["remaining"] == 0
    before = await av.coverage_progress(db, intake_id, scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    assert (before["eligible"], before["remaining"]) == (3, 3)

    a, b = _loaded(NEW, "2026-11-26")
    with a, b, patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
                     new=AsyncMock(return_value=_answer(NEW, "2026-11-26"))):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100,
                                             scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    assert result["processed_this_call"] == 3 and result["remaining"] == 0 and result["complete"] is True
    assert result["edition_sha256"] == NEW and result["pinned_edition"]["publication_date"] == "2026-11-26"

    rows = await _rows(db, intake_id)
    assert sorted((r.sha, r.pub) for r in rows) == sorted([(OLD, "2026-10-30")] * 3 + [(NEW, "2026-11-26")] * 3)

    # Idempotent for the same edition.
    with a, b, patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
                     new=AsyncMock(return_value=_answer(NEW, "2026-11-26"))):
        again = await av.run_coverage_batch(db, intake_id, batch_size=100,
                                            scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    assert again["processed_this_call"] == 0 and len(await _rows(db, intake_id)) == 6


async def test_refused_when_the_loaded_edition_is_not_the_pinned_one(screened_on_old_edition):
    db, intake_id = screened_on_old_edition
    a, b = _loaded(OLD, "2026-10-30")
    screen = AsyncMock(return_value=_answer(OLD, "2026-10-30"))
    with a, b, patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=screen):
        with pytest.raises(av.SamExtractEditionRefused, match="not the pinned edition"):
            await av.run_coverage_batch(db, intake_id, scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    screen.assert_not_awaited()
    assert len(await _rows(db, intake_id)) == 3


async def test_refused_when_the_extract_cannot_screen(screened_on_old_edition):
    db, intake_id = screened_on_old_edition
    with patch("app.Tefca.sam_extract.screening_not_ready_reason",
               new=AsyncMock(return_value="stale_source: 3.0 day(s) old")):
        with pytest.raises(av.SamExtractEditionRefused, match="stale_source"):
            await av.run_coverage_batch(db, intake_id, scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)


@pytest.mark.parametrize("bad", [None, "", "b" * 63, "g" * 64, "not-a-hash"])
async def test_refused_without_a_valid_pinned_hash(screened_on_old_edition, bad):
    db, intake_id = screened_on_old_edition
    with pytest.raises(av.SamExtractEditionRefused, match="64-hex"):
        await av.run_coverage_batch(db, intake_id, scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=bad)


async def test_edition_hash_is_refused_on_other_scopes(screened_on_old_edition):
    db, intake_id = screened_on_old_edition
    with pytest.raises(ValueError):
        await av.run_coverage_batch(db, intake_id, scope=av.SCOPE_SAM_EXTRACT, edition_sha256=NEW)


async def test_an_answer_from_another_edition_mid_run_is_not_persisted(screened_on_old_edition):
    db, intake_id = screened_on_old_edition
    a, b = _loaded(NEW, "2026-11-26")
    # The batch check sees NEW, but the screen answers from a reloaded edition.
    with a, b, patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
                     new=AsyncMock(return_value=_answer("c" * 64, "2026-11-27"))):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100,
                                             scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    assert result["by_outcome"] == {av.OUTCOME_RETRY_PENDING: 3}
    assert result["remaining"] == 3
    assert {r.sha for r in await _rows(db, intake_id)} == {OLD}


async def test_not_screenable_is_recorded_against_the_pinned_edition_never_as_clear(screened_on_old_edition):
    db, intake_id = screened_on_old_edition
    a, b = _loaded(NEW, "2026-11-26")
    not_screenable = SourceResult(source_name="SAM_EXTRACT", success=False,
                                  error="no_usable_identifier: neither a valid NPI nor an organisation name")
    with a, b, patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=AsyncMock(return_value=not_screenable)):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100,
                                             scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    assert result["remaining"] == 0
    new_rows = [r for r in await _rows(db, intake_id) if r.sha == NEW]
    assert len(new_rows) == 3
    assert {(r.disposition, r.pub, r.st) for r in new_rows} == {("UNAVAILABLE", "2026-11-26", "not_screened")}


@pytest.mark.parametrize("source_failure", [
    "extract unavailable or refused",
    "stale_source: extract edition 2026-11-26 is 2.1 day(s) old",
    "ENABLE_SAM_EXTRACT_SCREENING not enabled",
])
async def test_a_source_failure_mid_run_is_retried_never_recorded_as_screened(screened_on_old_edition,
                                                                              source_failure):
    """E.g. the 24 h cache refetch fails part-way through a long run while loaded_edition() still names the pinned
    hash: the attempt did not happen, so nothing is persisted and the entity stays selected."""
    db, intake_id = screened_on_old_edition
    a, b = _loaded(NEW, "2026-11-26")
    failed = SourceResult(source_name="SAM_EXTRACT", success=False, error=source_failure)
    with a, b, patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=AsyncMock(return_value=failed)):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100,
                                             scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    assert result["by_outcome"] == {av.OUTCOME_RETRY_PENDING: 3}
    assert result["remaining"] == 3
    assert {r.sha for r in await _rows(db, intake_id)} == {OLD}


async def _statuses(db, intake_id):
    return sorted((await db.execute(text(
        "SELECT e.verification_status FROM tefca_reg_entities e WHERE e.id IN ("
        "  SELECT r.canonical_entity_id FROM rce_curated_records r WHERE r.source_intake_id = CAST(:i AS uuid))"),
        {"i": str(intake_id)})).scalars().all())


async def test_candidate_on_the_pinned_edition_is_review_never_exclusion(screened_on_old_edition):
    db, intake_id = screened_on_old_edition
    statuses_before = await _statuses(db, intake_id)
    a, b = _loaded(NEW, "2026-11-26")
    with a, b, patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
                     new=AsyncMock(return_value=_answer(NEW, "2026-11-26", candidate=True))):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100,
                                             scope=av.SCOPE_SAM_EXTRACT_EDITION, edition_sha256=NEW)
    assert result["by_outcome"] == {av.OUTCOME_REVIEW_REQUIRED: 3}
    assert {r.disposition for r in await _rows(db, intake_id) if r.sha == NEW} == {"REVIEW"}
    # The candidate is a work item, not only an evidence row: one OPEN SAM_EXTRACT_CANDIDATE finding per entity on
    # this delivery's records (the finding writer's failures are caught and logged, so assert it actually ran).
    from app.tefca_registry.rce.verification_findings import SAM_EXTRACT_CANDIDATE
    findings = (await db.execute(text(
        "SELECT count(DISTINCT r.canonical_entity_id) FROM rce_issues i "
        "JOIN rce_curated_records r ON r.source_record_id = i.source_record_id "
        "WHERE r.source_intake_id = CAST(:i AS uuid) AND i.issue_type = :t AND i.resolution = 'OPEN'"),
        {"i": str(intake_id), "t": SAM_EXTRACT_CANDIDATE})).scalar()
    assert findings == 3
    # ...and it does NOT write the entity's verification_status: `in_review` is what the review frame excludes on
    # (D-08 undecided), and methodology §326 routes an unadjudicated candidate INTO classification (B3, RULE-008).
    statuses_after = await _statuses(db, intake_id)
    assert statuses_after == statuses_before and "in_review" not in statuses_after
    from app.tefca_registry.qhin_sampling import resolve_qhin_strata, review_required_exclusions
    _eligible, unresolved = await resolve_qhin_strata(db, intake_id)
    assert review_required_exclusions(unresolved) == []


async def test_legacy_scope_candidate_still_sets_in_review(rolled_back_db):
    """The unpinned `sam_extract` scope is deliberately unchanged by the edition-scope change."""
    db = rolled_back_db
    intake_id, _job = await seed_intake(db, make_rows(2, arc="9.99.777.72"))
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(return_value=_answer(OLD, "2026-10-30", candidate=True))):
        await av.run_coverage_batch(db, intake_id, batch_size=100, scope=av.SCOPE_SAM_EXTRACT)
    assert await _statuses(db, intake_id) == ["in_review", "in_review"]


def test_edition_candidate_query_matches_on_the_recorded_hash_and_deduplicates():
    assert "original_values ->> 'extract_sha256' = :edition_sha256" in av._SAM_EXTRACT_EDITION_NOT_COVERED_SQL
    assert "original_values ->> 'extract_sha256' = :edition_sha256" in av._SAM_EXTRACT_EDITION_NOT_COVERED_COUNT_SQL
    assert "DISTINCT ON (r.canonical_entity_id)" in av._SAM_EXTRACT_EDITION_NOT_COVERED_SQL
