"""Run-pinned Treatment assessment date (B2 of the 2026-11-11 code update).

The two Treatment producer calls take the assessment date from the delivery's
OWN run (validated against its intake), never the wall clock and never an
unrelated run. 2026-11-08 below is test data only; nothing is hardcoded.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.tefca_registry.rce import assessment_context as ac
from app.tefca_registry.rce import source_policy as sp

OCT_ASSESSMENT = date(2026, 11, 8)


class _Scalar:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalar(self):
        return self._v


class _FakeDB:
    def __init__(self, npi="1234567893", source_row=None):
        self.npi, self.source_row, self.added = npi, source_row, []

    async def execute(self, *_a, **_k):
        return _Scalar(self.npi)

    async def get(self, _model, _id):
        return self.source_row


def _entity(source_record_id=None):
    return SimpleNamespace(id=uuid.uuid4(), source_record_id=source_record_id)


# ── 3. assessment date from the delivery's own run ───────────────────────────

def _run(intake, *, date_str="2026-11-08", status="COMPLETE", started=None,
         meta=True):
    return SimpleNamespace(
        id=uuid.uuid4(), source_intake_id=intake, run_status=status,
        started_at=started or datetime(2026, 11, 9, 8, 0, 0),
        assessment_metadata=({"assessment_date": date_str} if meta else None))


class _RunDB:
    def __init__(self, runs):
        self.runs = {r.id: r for r in runs}

    async def get(self, _model, rid):
        return self.runs.get(rid)


async def test_current_run_of_the_same_intake_supplies_the_pinned_date(monkeypatch):
    from app.tefca_registry.rce import run_selection
    intake = uuid.uuid4()
    run = _run(intake, date_str="2026-11-08")
    seen = {}

    async def _current(_db, intake_id):
        seen["intake"] = intake_id
        return run

    monkeypatch.setattr(run_selection, "current_run", _current)
    got = await ac.resolve_assessment_date(object(), str(intake))
    assert got.value == OCT_ASSESSMENT
    assert got.source == ac.SOURCE_RUN_METADATA and got.run_id == str(run.id)
    assert seen["intake"] == intake                      # scoped to THAT intake


def test_run_selection_is_scoped_to_the_intake_in_sql():
    from app.tefca_registry.rce import run_selection
    intake = uuid.uuid4()
    sql = str(run_selection._ordered_runs(intake).compile(
        compile_kwargs={"literal_binds": False}))
    assert "source_intake_id" in sql and "run_status" in sql


async def test_explicit_run_of_another_intake_is_refused():
    other = _run(uuid.uuid4())
    with pytest.raises(ValueError, match="unrelated run"):
        await ac.resolve_assessment_date(
            _RunDB([other]), uuid.uuid4(), run_id=other.id)


async def test_explicit_run_without_intake_is_refused():
    r = _run(uuid.uuid4())
    with pytest.raises(ValueError, match="linkage cannot be validated"):
        await ac.resolve_assessment_date(_RunDB([r]), None, run_id=r.id)


async def test_explicit_run_must_exist_and_be_complete():
    intake = uuid.uuid4()
    with pytest.raises(ValueError, match="No ingestion run"):
        await ac.resolve_assessment_date(_RunDB([]), intake, run_id=uuid.uuid4())
    failed = _run(intake, status="FAILED")
    with pytest.raises(ValueError, match="not COMPLETE"):
        await ac.resolve_assessment_date(_RunDB([failed]), intake, run_id=failed.id)


async def test_explicit_run_of_the_same_intake_is_accepted():
    intake = uuid.uuid4()
    r = _run(intake, date_str="2026-11-08")
    got = await ac.resolve_assessment_date(_RunDB([r]), intake, run_id=r.id)
    assert got.value == OCT_ASSESSMENT and got.intake_id == str(intake)


async def test_unannotated_run_uses_its_own_start_date_and_says_so(monkeypatch):
    from app.tefca_registry.rce import run_selection
    intake = uuid.uuid4()
    r = _run(intake, meta=False, started=datetime(2026, 10, 3, 23, 59))

    async def _current(_db, _i):
        return r

    monkeypatch.setattr(run_selection, "current_run", _current)
    got = await ac.resolve_assessment_date(object(), intake)
    assert got.value == date(2026, 10, 3) and got.source == ac.SOURCE_RUN_STARTED_AT


async def test_unparseable_metadata_is_not_trusted(monkeypatch):
    from app.tefca_registry.rce import run_selection
    intake = uuid.uuid4()
    r = _run(intake, date_str="not-a-date")

    async def _current(_db, _i):
        return r

    monkeypatch.setattr(run_selection, "current_run", _current)
    got = await ac.resolve_assessment_date(object(), intake)
    assert got.source == ac.SOURCE_RUN_STARTED_AT


async def test_no_intake_or_no_completed_run_falls_back_labelled(monkeypatch):
    from app.tefca_registry.rce import run_selection
    got = await ac.resolve_assessment_date(object(), None)
    assert got.source == ac.SOURCE_FALLBACK_NO_INTAKE and not got.from_run

    async def _none(_db, _i):
        return None

    monkeypatch.setattr(run_selection, "current_run", _none)
    got = await ac.resolve_assessment_date(object(), uuid.uuid4())
    assert got.source == ac.SOURCE_FALLBACK_NO_RUN and got.run_id is None


async def test_future_delivery_gets_its_own_date_nothing_is_hardcoded(monkeypatch):
    from app.tefca_registry.rce import run_selection
    intake = uuid.uuid4()
    r = _run(intake, date_str="2027-03-02")

    async def _current(_db, _i):
        return r

    monkeypatch.setattr(run_selection, "current_run", _current)
    got = await ac.resolve_assessment_date(object(), intake)
    assert got.value == date(2027, 3, 2)


# ── 4. both Treatment producer calls use the resolved date ───────────────────

async def test_single_record_treatment_signals_use_the_run_date(monkeypatch):
    from app.tefca_registry import review_service as rs
    from app.tefca_registry.rce import npi_applicability as npa
    intake = uuid.uuid4()
    row = SimpleNamespace(source_intake_id=intake,
                          parsed={"purposesofuse": "T-TRTMNT", "NPI": "",
                                  "active": "1", "sequoiaorgtype": "participant"})
    captured = {}

    async def _resolve(_db, intake_id, **_k):
        captured["intake"] = intake_id
        return ac.AssessmentDate(OCT_ASSESSMENT, ac.SOURCE_RUN_METADATA,
                                 "run-1", str(intake_id))

    def _signals(parsed, *, assessment_date, requirement_effective):
        captured["date"] = assessment_date
        return {"sig": True}

    monkeypatch.setattr(ac, "resolve_assessment_date", _resolve)
    monkeypatch.setattr(npa, "treatment_signals_from_parsed", _signals)
    sigs, assessment = await rs._treatment_signals_and_assessment(
        _FakeDB(source_row=row), _entity(uuid.uuid4()))
    assert sigs == {"sig": True}
    assert captured["date"] == OCT_ASSESSMENT
    assert captured["intake"] == intake          # the SOURCE RECORD's intake
    assert assessment["run_id"] == "run-1"
    # the thin wrapper keeps its old contract
    assert await rs._treatment_field_signals(
        _FakeDB(source_row=row), _entity(uuid.uuid4())) == {"sig": True}


async def test_predating_entity_has_no_treatment_evidence_and_no_assessment():
    from app.tefca_registry import review_service as rs
    assert await rs._treatment_signals_and_assessment(_FakeDB(), _entity(None)) == ({}, None)


def test_bulk_path_no_longer_reads_the_wall_clock_for_treatment():
    from app.tefca_registry.rce import arc_pipeline
    src = inspect.getsource(arc_pipeline.verify_and_classify)
    assert "_dt.now(_tz.utc).date()" not in src
    assert "assessment_date=_assessment.value" in src
    assert "resolve_assessment_date" in src
    assert "assessment_run_id" in inspect.signature(
        arc_pipeline.verify_and_classify).parameters


def test_single_path_no_longer_reads_the_wall_clock_for_treatment():
    from app.tefca_registry import review_service as rs
    src = inspect.getsource(rs._treatment_signals_and_assessment)
    assert "now(" not in src and "resolve_assessment_date" in src


# -- producers refuse a missing assessment date (no wall-clock default) ------

@pytest.mark.parametrize("bad", [None, "2026-11-08", 20261108])
def test_every_treatment_producer_refuses_a_missing_or_non_date(bad):
    from app.tefca_registry.rce import npi_applicability as npa
    eff = date(2026, 9, 3)
    with pytest.raises(npa.AssessmentDateRequired):
        npa.treatment_field_signals("T-TRTMNT", "", "1", "Participant", None,
                                    assessment_date=bad, requirement_effective=eff)
    with pytest.raises(npa.AssessmentDateRequired):
        npa.treatment_signals_from_parsed({"purposesofuse": "T-TRTMNT"},
                                          assessment_date=bad, requirement_effective=eff)
    with pytest.raises(npa.AssessmentDateRequired):
        npa.active_treatment_missing_required_npi("T-TRTMNT", "", "1",
                                                  assessment_date=bad, requirement_effective=eff)


def test_a_real_date_still_works():
    from app.tefca_registry.rce import npi_applicability as npa
    out = npa.treatment_field_signals("T-TRTMNT", "", "1", "Participant", None,
                                      assessment_date=OCT_ASSESSMENT,
                                      requirement_effective=date(2026, 9, 3))
    assert isinstance(out, dict)
