"""Route-level tests for `advisory_comparison` (review finding 2026-11-08).

Moved out of `test_iqvia_routes.py` into this self-contained file so these
tests run in the `isolation-postgres` CI lane: `test_iqvia_routes.py` is wired
into no CI job and its older classes carry 21 pre-existing local failures
(app/core/config.py declares ENABLE_IQVIA_SOURCES as a settings FIELD, which
shadows the `monkeypatch.setenv` mechanism those older tests use — tracked as
its own follow-up change). Nothing here exempts those failures: that file's
tests are exactly as runnable (and as broken) as before this move.

Same direct route-callable convention as `test_iqvia_routes.py`. Covers, for
BOTH advisory source types (AFFILIATION and HCP/ADDR): the HCO 422, the
licensed master-flag 403, the per-source-flag 409 CONSUMPTION_DISABLED with
its blocked audit, the 409 SNAPSHOT_NOT_APPROVED with its blocked audit, and
the success audit carrying outcome counts and NO licensed row content.
Entirely synthetic data; no real IQVIA content anywhere.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, text

from app.tefca_registry.rce import snapshot_models as sm

pytestmark = pytest.mark.regression


#: Ids created by the current test, cleaned up by the `db` fixture after each
#: test. The route's audit path COMMITS, so savepoint rollback cannot cover
#: these rows — they are deleted by id instead (same convention as
#: `test_iqvia_routes.py`, which this file's tests originally lived in).
_created_snapshot_ids: list = []
_created_entity_ids: list = []


@pytest.fixture
async def db(db_required):
    """A REAL, committed session — not the savepoint-isolated
    `rolled_back_db`: the route commits its audit rows, so cleanup is manual,
    by id."""
    from app.core.database import async_session_maker

    _created_snapshot_ids.clear()
    _created_entity_ids.clear()
    async with async_session_maker() as session:
        yield session
    async with async_session_maker() as cleanup:
        from app.models.database import AuditLog
        from app.tefca_registry import models as reg

        audited = [str(i) for i in _created_snapshot_ids] or ["none"]
        await cleanup.execute(delete(AuditLog).where(AuditLog.resource_id.in_(audited)))
        await cleanup.execute(delete(sm.EntitySourceMatch).where(
            sm.EntitySourceMatch.source_snapshot_id.in_(_created_snapshot_ids or [uuid.uuid4()])))
        await cleanup.execute(delete(reg.TefcaRegEntity).where(
            reg.TefcaRegEntity.id.in_(_created_entity_ids or [uuid.uuid4()])))
        # Teardown of synthetic rows only. source_snapshot is guarded by
        # trg_source_snapshot_guard (migration 20261006_snapshot_bookkeeping),
        # which refuses to delete a non-PENDING snapshot; the APPROVED rows
        # these tests create are exactly that. `replica` skips ordinary
        # triggers for THIS cleanup transaction only (SET LOCAL;
        # superuser-only, which the test role is). Not a production path —
        # the application and the runtime role have no such privilege.
        await cleanup.execute(text("SET LOCAL session_replication_role = replica"))
        await cleanup.execute(delete(sm.SourceSnapshot).where(
            sm.SourceSnapshot.id.in_(_created_snapshot_ids or [uuid.uuid4()])))
        await cleanup.commit()
    _created_snapshot_ids.clear()
    _created_entity_ids.clear()


class _User:
    def __init__(self, role, email=None):
        """A minimal user object with the given role and a role-specific
        synthetic email."""
        self.role = role
        self.email = email or f"iqvia-advroute-test-{role}@synthetic-test.docuaction.invalid"
        self.id = None


class TestAdvisoryComparisonRoute:
    from app.core.config import settings  # class-level: shared by the tests

    @staticmethod
    def _snapshot(db, source_system, *, status="PENDING"):
        """A synthetic snapshot row (APPROVED status carries the approval
        triple), registered for cleanup."""
        import hashlib
        from datetime import datetime, timezone

        nonce = uuid.uuid4().hex
        snap = sm.SourceSnapshot(
            id=uuid.uuid4(), source_system=source_system,
            snapshot_label=f"SYNTHETIC-ADVROUTE-{source_system}-{nonce[:8]}",
            sha256=hashlib.sha256(nonce.encode()).hexdigest(),
            record_count=0, received_at=datetime.now(timezone.utc),
            status=status, created_by="synthetic-route-test@invalid",
            correlation_id=str(uuid.uuid4()),
            approved_by=("qa@synthetic.invalid" if status == "APPROVED" else None),
            approved_at=(datetime.now(timezone.utc) if status == "APPROVED" else None),
            approved_role=("qalead" if status == "APPROVED" else None))
        db.add(snap)
        # The route's audit commit makes these rows durable — register them
        # with the cleanup fixture so nothing leaks into the shared DB.
        _created_snapshot_ids.append(snap.id)
        return snap

    @staticmethod
    def _body():
        """A minimal request body naming one random entity id."""
        from app.tefca_registry.rce.iqvia_routes import AdvisoryComparisonRequest
        return AdvisoryComparisonRequest(entity_ids=[uuid.uuid4()])

    @staticmethod
    async def _last_audit(db, resource_id, action="IQVIA_ADVISORY_COMPARISON"):
        """The audit row for THIS test's snapshot (scoped by resource_id so
        committed rows from sibling tests can never be picked up)."""
        from sqlalchemy import select

        from app.models.database import AuditLog
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.action == action,
                                   AuditLog.resource_id == str(resource_id))
            .order_by(AuditLog.created_at.desc()))).scalars().all()
        return rows[0] if rows else None

    @pytest.mark.asyncio
    async def test_master_flag_off_is_403_not_configured(self, db, monkeypatch):
        """Master flag off -> 403 with availability=not_configured."""
        from app.tefca_registry.rce import iqvia_routes as routes
        monkeypatch.setattr(self.settings, "ENABLE_IQVIA_SOURCES", False, raising=False)
        snap = self._snapshot(db, "IQVIA_AFFILIATION"); await db.flush()
        with pytest.raises(HTTPException) as e:
            await routes.advisory_comparison(snap.id, self._body(), db,
                                             user=_User("reviewer"))
        assert e.value.status_code == 403
        assert e.value.detail["availability"] == "not_configured"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source_system,flag", [
        ("IQVIA_AFFILIATION", "ENABLE_IQVIA_AFFILIATION_CONSUMPTION"),
        ("IQVIA_HCP", "ENABLE_IQVIA_ADDRESS_CONSUMPTION"),
    ])
    async def test_per_source_flag_off_is_409_with_blocked_audit(
            self, db, monkeypatch, source_system, flag):
        """Per-source flag off -> 409 CONSUMPTION_DISABLED with a blocked
        audit row naming the source."""
        from app.tefca_registry.rce import iqvia_routes as routes
        monkeypatch.setattr(self.settings, "ENABLE_IQVIA_SOURCES", True, raising=False)
        monkeypatch.setattr(self.settings, flag, False, raising=False)
        snap = self._snapshot(db, source_system, status="APPROVED"); await db.flush()
        with pytest.raises(HTTPException) as e:
            await routes.advisory_comparison(snap.id, self._body(), db,
                                             user=_User("reviewer"))
        assert e.value.status_code == 409
        assert e.value.detail["code"] == "CONSUMPTION_DISABLED"
        audit = await self._last_audit(db, snap.id)
        assert audit is not None and audit.outcome == "blocked"
        assert audit.details["code"] == "CONSUMPTION_DISABLED"
        assert audit.details["source_system"] == source_system

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source_system,flag", [
        ("IQVIA_AFFILIATION", "ENABLE_IQVIA_AFFILIATION_CONSUMPTION"),
        ("IQVIA_HCP", "ENABLE_IQVIA_ADDRESS_CONSUMPTION"),
    ])
    async def test_unapproved_snapshot_is_409_with_blocked_audit(
            self, db, monkeypatch, source_system, flag):
        """PENDING snapshot -> 409 SNAPSHOT_NOT_APPROVED with a blocked audit
        row."""
        from app.tefca_registry.rce import iqvia_routes as routes
        monkeypatch.setattr(self.settings, "ENABLE_IQVIA_SOURCES", True, raising=False)
        monkeypatch.setattr(self.settings, flag, True, raising=False)
        snap = self._snapshot(db, source_system, status="PENDING"); await db.flush()
        with pytest.raises(HTTPException) as e:
            await routes.advisory_comparison(snap.id, self._body(), db,
                                             user=_User("reviewer"))
        assert e.value.status_code == 409
        assert e.value.detail["code"] == "SNAPSHOT_NOT_APPROVED"
        audit = await self._last_audit(db, snap.id)
        assert audit is not None and audit.outcome == "blocked"
        assert audit.details["code"] == "SNAPSHOT_NOT_APPROVED"

    @pytest.mark.asyncio
    async def test_hco_snapshot_is_422_not_comparable(self, db, monkeypatch):
        """An HCO snapshot is 422 NOT_COMPARABLE on this route."""
        from app.tefca_registry.rce import iqvia_routes as routes
        monkeypatch.setattr(self.settings, "ENABLE_IQVIA_SOURCES", True, raising=False)
        snap = self._snapshot(db, "IQVIA_HCO", status="APPROVED"); await db.flush()
        with pytest.raises(HTTPException) as e:
            await routes.advisory_comparison(snap.id, self._body(), db,
                                             user=_User("reviewer"))
        assert e.value.status_code == 422
        assert e.value.detail["code"] == "NOT_COMPARABLE"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source_system,flag", [
        ("IQVIA_AFFILIATION", "ENABLE_IQVIA_AFFILIATION_CONSUMPTION"),
        ("IQVIA_HCP", "ENABLE_IQVIA_ADDRESS_CONSUMPTION"),
    ])
    async def test_success_audits_outcome_counts_without_licensed_content(
            self, db, monkeypatch, source_system, flag):
        """A successful comparison audits outcome counts and carries NO
        licensed row content."""
        import json as _json

        from app.tefca_registry import models as reg
        from app.tefca_registry.rce import iqvia_routes as routes
        from app.tefca_registry.rce.iqvia_routes import AdvisoryComparisonRequest
        monkeypatch.setattr(self.settings, "ENABLE_IQVIA_SOURCES", True, raising=False)
        monkeypatch.setattr(self.settings, flag, True, raising=False)
        snap = self._snapshot(db, source_system, status="APPROVED")
        licensed_marker = "LICENSED-ROW-CONTENT-NEVER-IN-AUDIT"
        entity = reg.TefcaRegEntity(
            id=uuid.uuid4(), name=f"SYNTHETIC-ADV-{licensed_marker}",
            display_name="SYNTHETIC-ADV", entity_level="participant",
            entity_type="healthcare_provider", operational_status="active",
            verification_status="not_verified", current_version=1,
            is_active=True)
        db.add(entity)
        _created_entity_ids.append(entity.id)
        await db.flush()

        out = await routes.advisory_comparison(
            snap.id, AdvisoryComparisonRequest(entity_ids=[entity.id]), db,
            user=_User("reviewer"))
        assert out["entities_requested"] == 1
        assert "summary" in out

        audit = await self._last_audit(db, snap.id)
        assert audit is not None and audit.outcome == "success"
        assert audit.details["entities_requested"] == 1
        assert "summary" in audit.details, "outcome counts are audited"
        dumped = _json.dumps(audit.details)
        assert licensed_marker not in dumped, \
            "audit details must never carry licensed/entity row content"
        assert "address" not in {k.lower() for k in audit.details}, \
            "no per-row address content in the audit"
