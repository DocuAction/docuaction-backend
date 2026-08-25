"""
Collecting a duplicate PPEF load, and the proofs that must hold before it can.

WHY THIS OPERATION NEEDS ITS OWN GUARDS
The failed-snapshot collector is safe to run unattended because it only ever
touches snapshots that did NOT finish. A duplicate load is `complete`: it
finished, it is internally consistent, and every row under it is real data. The
only thing wrong with it is that the same bytes are already stored under an
earlier snapshot.

That makes "is this really a duplicate?" the entire safety question, and it is
not answerable from metadata. Two snapshots can carry the same file name, the
same version and the same declared count while holding different rows — a
changed file republished under an unchanged name is exactly how that happens,
and treating the second one as redundant would silently discard the newer data.

So the script re-derives the duplication from the CONTENTS every time it runs,
and these tests assert it refuses in each way the proof can fail. Every refusal
below is a case where deleting would destroy real data.

The tests run with no database. What is under test is the proof logic and the
control flow around the delete, and a fake session asserts both exactly.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "collect_duplicate_ppef_snapshot.py"
    spec = importlib.util.spec_from_file_location("collect_duplicate_ppef_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect = _load()

DUP = str(uuid.uuid4())
CAN = str(uuid.uuid4())
EARLIER = datetime(2026, 8, 19, 22, 39)
LATER = datetime(2026, 8, 20, 1, 11)


def _snap(sid, *, status="complete", sha="0ae087f4", ingested=EARLIER,
          component="ADDITIONAL_NPIS", file_name="PPEF_Additional_NPIs_2026.07.17.csv",
          version="2026.07.17", size=3596195, count=128435):
    return {"id": sid, "component": component, "file_name": file_name,
            "resource_version": version, "file_size": size, "sha256": sha,
            "ingest_status": status, "ingested_at": ingested, "record_count": count}


class _Result:
    def __init__(self, value, rowcount=0):
        self._v = value
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._v

    def scalar(self):
        return self._v


class _FakeSession:
    """Serves scripted results in call order and records every statement."""

    def __init__(self, results):
        self._results = deque(results)
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        return self._results.popleft() if self._results else _Result(0)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install(monkeypatch, session):
    import app.core.database as database

    monkeypatch.setattr(database, "async_session_maker", lambda: session)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)


def _proof_results(*, dup=None, can=None, dup_rows=128435, can_rows=128435,
                   only_dup=0, only_can=0, cited=0, live=0):
    """The seven results _prove() consumes, in order."""
    dup = dup or _snap(DUP, ingested=LATER)
    can = can or _snap(CAN, ingested=EARLIER)
    return [_Result([dup, can]), _Result(dup_rows), _Result(can_rows),
            _Result(only_dup), _Result(only_can), _Result(cited), _Result(live)]


def _deletes(session):
    return [s for s in session.statements if s.strip().lower().startswith("delete")]


# ── 1. the happy path ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_proves_but_deletes_nothing(monkeypatch, capsys):
    session = _FakeSession(_proof_results())
    _install(monkeypatch, session)

    assert await collect.run(DUP, CAN, confirm=False, allow_prod=False) == 0
    assert _deletes(session) == []
    assert session.commits == 0
    out = capsys.readouterr().out
    assert "128,435" in out
    assert "DRY RUN" in out


@pytest.mark.asyncio
async def test_confirm_deletes_only_the_duplicates_children(monkeypatch):
    session = _FakeSession(_proof_results() + [
        _Result(128435),              # before_can
        _Result(None, rowcount=128435),  # the delete
        _Result(128435),              # after_can — unchanged
        _Result(1),                   # duplicate snapshot row still present
    ])
    _install(monkeypatch, session)

    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 0
    deletes = _deletes(session)
    assert len(deletes) == 1
    assert "tefca_ppef_records" in deletes[0]
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_no_snapshot_row_is_ever_deleted(monkeypatch):
    """The duplicate snapshot is the record that a second ingestion happened."""
    session = _FakeSession(_proof_results() + [
        _Result(128435), _Result(None, rowcount=128435), _Result(128435), _Result(1)])
    _install(monkeypatch, session)
    await collect.run(DUP, CAN, confirm=True, allow_prod=False)

    for stmt in _deletes(session):
        assert "tefca_ppef_snapshots" not in stmt


# ── 2. every way the proof can fail ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_refuses_when_content_differs(monkeypatch, capsys):
    """THE case this whole script exists to survive.

    Same file name, same version, same declared count — different rows. That is a
    republished file, not a duplicate load, and deleting it would discard the
    newer data while leaving metadata that says everything is fine.
    """
    session = _FakeSession(_proof_results(only_dup=12, only_can=0))
    _install(monkeypatch, session)

    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3
    assert _deletes(session) == []
    assert session.commits == 0
    assert "content differs" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_refuses_when_sha256_differs(monkeypatch):
    session = _FakeSession(_proof_results(
        dup=_snap(DUP, sha="deadbeef", ingested=LATER), can=_snap(CAN, sha="0ae087f4")))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3
    assert _deletes(session) == []


@pytest.mark.asyncio
async def test_refuses_when_sha256_is_null(monkeypatch, capsys):
    """Equal NULLs prove nothing about the bytes that were loaded."""
    session = _FakeSession(_proof_results(
        dup=_snap(DUP, sha=None, ingested=LATER), can=_snap(CAN, sha=None)))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3
    assert "NULL" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_refuses_to_delete_the_earlier_ingestion(monkeypatch, capsys):
    """Arguments swapped. The earlier successful load must always survive."""
    session = _FakeSession(_proof_results(
        dup=_snap(DUP, ingested=EARLIER), can=_snap(CAN, ingested=LATER)))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3
    assert "not strictly later" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_refuses_when_evidence_cites_the_duplicate(monkeypatch, capsys):
    session = _FakeSession(_proof_results(cited=3))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3
    assert "reproducibility" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_refuses_when_a_live_job_claims_the_duplicate(monkeypatch):
    session = _FakeSession(_proof_results(live=1))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3
    assert _deletes(session) == []


@pytest.mark.asyncio
async def test_refuses_when_a_snapshot_is_not_complete(monkeypatch, capsys):
    """A snapshot that did not finish belongs to the other collector."""
    session = _FakeSession(_proof_results(
        dup=_snap(DUP, status="failed", ingested=LATER)))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3
    assert "not 'complete'" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_refuses_when_child_counts_differ(monkeypatch):
    session = _FakeSession(_proof_results(dup_rows=128435, can_rows=100000))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3


@pytest.mark.asyncio
async def test_refuses_when_the_duplicate_is_already_empty(monkeypatch):
    session = _FakeSession(_proof_results(dup_rows=0, can_rows=0))
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3


@pytest.mark.asyncio
async def test_refuses_when_a_snapshot_is_missing(monkeypatch):
    session = _FakeSession([_Result([_snap(DUP, ingested=LATER)])])
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 3


# ── 3. the guards around the delete ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_rolls_back_if_the_canonical_side_changed(monkeypatch, capsys):
    """A cross-snapshot delete would be catastrophic and silent.

    The invariant is checked inside the transaction, so a delete that touched the
    wrong rows can never be committed.
    """
    session = _FakeSession(_proof_results() + [
        _Result(128435),                 # before_can
        _Result(None, rowcount=128435),  # the delete
        _Result(128000),                 # after_can — CHANGED
        _Result(1),
    ])
    _install(monkeypatch, session)

    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 4
    assert session.rollbacks == 1
    assert session.commits == 0
    assert "ROLLED BACK" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_rolls_back_if_the_duplicate_snapshot_row_vanished(monkeypatch):
    session = _FakeSession(_proof_results() + [
        _Result(128435), _Result(None, rowcount=128435), _Result(128435),
        _Result(0),   # snapshot row gone — must never happen
    ])
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 4
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_rolls_back_if_the_delete_count_is_unexpected(monkeypatch):
    session = _FakeSession(_proof_results() + [
        _Result(128435), _Result(None, rowcount=200000), _Result(128435), _Result(1)])
    _install(monkeypatch, session)
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 4
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_refuses_identical_arguments(monkeypatch):
    """Passing the same id twice would delete the canonical rows."""
    session = _FakeSession([])
    _install(monkeypatch, session)
    assert await collect.run(DUP, DUP, confirm=True, allow_prod=False) == 2
    assert session.statements == []


@pytest.mark.asyncio
async def test_production_is_refused_without_allow_prod(monkeypatch):
    session = _FakeSession([])
    _install(monkeypatch, session)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert await collect.run(DUP, CAN, confirm=True, allow_prod=False) == 2
    assert session.statements == [], "it must refuse before querying anything"
