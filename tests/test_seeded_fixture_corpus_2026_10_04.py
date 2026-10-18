"""Proves tests/fixtures/seeded/manifest.json against the real reader and
the real preflight engine -- Round 22, Part A, step 4.

Every fixture is synthetic (see the manifest's own note: 9.99.777 arc,
SYNTHETIC-TRACE names, no delivered Government row, no live source, no
confidential identifier). Each row is read through the application's own
`ingest_delivery` (the same reader a real delivery uses) so parsing, not a
hand-built RecordContext, is what preflight actually evaluates.

Six of the eight seeds assert an exact expected outcome. Two (SEED-07
unknown enum, SEED-08 identifier checksum failure) are marked EMPIRICAL in
the manifest -- this test records what `run_preflight` actually returns for
them rather than asserting a guessed code, and the checkpoint document
(REVIEW-PACKAGE-PREFLIGHT-A-2026-10-04.md) states the observed result
rather than a predicted one.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest

_SEEDED_DIR = pathlib.Path(__file__).parent / "fixtures" / "seeded"
_MANIFEST = json.loads((_SEEDED_DIR / "manifest.json").read_text(encoding="utf-8"))


async def _ingest_fixture(seed: dict) -> uuid.UUID:
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.intake import ingest_delivery

    raw = (_SEEDED_DIR / seed["file"]).read_bytes()
    async with async_session_maker() as db:
        result = await ingest_delivery(
            db, raw, filename=seed["file"], declared_delimiter="|",
            delivery_label=f"SYNTHETIC-SEEDED-{seed['seed_id']}",
            received_by="pytest-seeded-corpus@synthetic-test.docuaction.invalid")
    return uuid.UUID(str(result["intake_id"]))


async def _run_preflight_on(intake_id: uuid.UUID):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight as pf

    async with async_session_maker() as db:
        run = await pf.run_preflight(db, intake_id, actor="pytest-seeded-corpus")
        findings = await pf.list_findings(db, uuid.UUID(run["run_id"]), limit=500)
    return run, findings


def _by(seed_id):
    return next(s for s in _MANIFEST["seeds"] if s["seed_id"] == seed_id)


@pytest.mark.asyncio
async def test_manifest_lists_every_fixture_file_and_nothing_extra(db_required):
    on_disk = {p.name for p in _SEEDED_DIR.glob("*.psv")}
    in_manifest = {s["file"] for s in _MANIFEST["seeds"]}
    assert on_disk == in_manifest, (on_disk, in_manifest)
    assert len(_MANIFEST["seeds"]) == 8


@pytest.mark.asyncio
async def test_seed_01_clean_delivery_is_never_blocked(db_required):
    seed = _by("SEED-01")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["classification_gate"] != "BLOCKED"
    codes = {f["code"] for f in findings["items"]}
    assert "PF-SCH-001" not in codes and "PF-SCH-008" not in codes


@pytest.mark.asyncio
async def test_seed_02_missing_identity_field_blocks(db_required):
    seed = _by("SEED-02")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["classification_gate"] == "BLOCKED"
    codes = {f["code"] for f in findings["items"]}
    for expected in seed["expected_findings"]:
        assert expected in codes, (expected, codes)


@pytest.mark.asyncio
async def test_seed_03_duplicate_header_blocks(db_required):
    seed = _by("SEED-03")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["classification_gate"] == "BLOCKED"
    codes = {f["code"] for f in findings["items"]}
    for expected in seed["expected_findings"]:
        assert expected in codes, (expected, codes)


@pytest.mark.asyncio
async def test_seed_04_renamed_header_is_open_not_blocked(db_required):
    seed = _by("SEED-04")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["classification_gate"] != "BLOCKED"
    codes = {f["code"] for f in findings["items"]}
    for expected in seed["expected_findings"]:
        assert expected in codes, (expected, codes)


@pytest.mark.asyncio
async def test_seed_05_reordered_header_is_open_not_blocked(db_required):
    seed = _by("SEED-05")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["classification_gate"] != "BLOCKED"
    codes = {f["code"] for f in findings["items"]}
    for expected in seed["expected_findings"]:
        assert expected in codes, (expected, codes)


@pytest.mark.asyncio
async def test_seed_06_extra_column_is_open_not_blocked(db_required):
    seed = _by("SEED-06")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["classification_gate"] != "BLOCKED"
    codes = {f["code"] for f in findings["items"]}
    for expected in seed["expected_findings"]:
        assert expected in codes, (expected, codes)


@pytest.mark.asyncio
async def test_seed_07_unknown_enum_matches_the_observed_manifest_outcome(db_required):
    """Observed once (2026-10-04, local disposable DB) and pinned in the
    manifest rather than guessed in advance -- see manifest.json's own
    notes for SEED-07 and docs/review/DELTA-2026-10-04.md."""
    seed = _by("SEED-07")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["status"] == "COMPLETE"
    assert run["classification_gate"] == seed["expected_gate"]
    codes = {f["code"] for f in findings["items"]}
    for expected in seed["expected_findings"]:
        assert expected in codes, (expected, sorted(codes))


@pytest.mark.asyncio
async def test_seed_08_checksum_failure_matches_the_observed_manifest_outcome(db_required):
    """Observed once (2026-10-04, local disposable DB) and pinned in the
    manifest rather than guessed in advance -- see manifest.json's own
    notes for SEED-08."""
    seed = _by("SEED-08")
    intake_id = await _ingest_fixture(seed)
    run, findings = await _run_preflight_on(intake_id)
    assert run["status"] == "COMPLETE"
    assert run["classification_gate"] == seed["expected_gate"]
    codes = {f["code"] for f in findings["items"]}
    for expected in seed["expected_findings"]:
        assert expected in codes, (expected, sorted(codes))
