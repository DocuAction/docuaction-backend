"""D-07 / DEF-12: the `sampling_plan_finalized` audit event must describe the
plan that was actually created.

Defect at PR #200 head aa21de5f: a FULL_POPULATION plan's audit event recorded
`selection_algorithm = cochran_fpc_per_qhin/1.0.0` and the caller's
confidence/margin arguments - describing a statistical draw that never
happened. The audit now takes algorithm, confidence and margin from the plan
itself, for both plan kinds.

Synthetic data. The plans and their audit rows are created in a single
rollback-only transaction (finalize_plan only flushes), so the shared test
database keeps no residue - no committed plans, no orphaned audit rows.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.regression


@pytest.mark.asyncio
async def test_audit_event_matches_plan_metadata_for_both_plan_kinds(db_required, monkeypatch):
    import test_sam_e2e_delivery_path as sam

    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.qhin_sampling import SELECTION_ALGORITHM, finalize_plan

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    intake_id = await sam._seed_promoted_delivery(n=4)

    async def _audit_for(db, sample_id):
        rows = (await db.execute(
            select(reg.TefcaRegAuditLog)
            .where(reg.TefcaRegAuditLog.action == "sampling_plan_finalized"))).scalars().all()
        mine = [a for a in rows if (a.metadata_ or {}).get("sample_id") == sample_id]
        assert len(mine) == 1
        return mine[0].metadata_

    async with async_session_maker() as db:
        try:
            full = await finalize_plan(db, intake_id, full_population=True,
                                       actor="pytest-audit-parity")
            payload = await _audit_for(db, full["sample_id"])
            # The plan is an enumeration; the audit must say so.
            assert payload["selection_algorithm"] == "FULL_POPULATION_ENUMERATION"
            assert payload["confidence_level"] == 1.0
            assert payload["margin_of_error"] == 0.0
            assert payload["sample_size"] == payload["population_size"]
            assert payload["random_seed"] is None

            statistical = await finalize_plan(db, intake_id,
                                              actor="pytest-audit-parity")
            spayload = await _audit_for(db, statistical["sample_id"])
            # The statistical plan keeps its own (unchanged) labels.
            assert spayload["selection_algorithm"] == SELECTION_ALGORITHM
            assert spayload["confidence_level"] == statistical["confidence_level"]
            assert spayload["margin_of_error"] == statistical["margin_of_error"]
        finally:
            # Rollback-only: nothing this test created is ever committed.
            await db.rollback()
