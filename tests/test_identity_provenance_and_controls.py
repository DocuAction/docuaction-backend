"""QA-010, 023–029, 040, 042, 043, 052, 057, 058, 059, 061 — identity,
provenance, timestamps and decision controls on the review surfaces.

Reuses the supervisor-operations fixtures (synthetic intake, promoted org,
DQ case, analyst -> QA walk-through) so every assertion runs against a real
database in a rolled-back transaction.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from test_supervisor_operations import (  # noqa: F401  (fixtures registered by import)
    ANALYST_A, QA, SUPERVISOR, _dq_case, _intake, _org, _qhin, _through_qa,
    rolled_back_db)


# ── timestamps (QA-010) ───────────────────────────────────────────────────────

def test_naive_datetimes_are_serialised_as_utc_with_an_offset():
    from app.tefca_registry.rce import delivery_routes, exception_ledger

    naive = datetime(2026, 9, 19, 22, 26, 32)
    assert delivery_routes._iso(naive) == "2026-09-19T22:26:32+00:00"
    assert exception_ledger._iso(naive) == "2026-09-19T22:26:32+00:00"
    assert exception_ledger._iso(naive.date()) == "2026-09-19"
    assert delivery_routes._iso(None) is None


# ── decision controls (QA-057, QA-058) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_reclassify_to_the_current_bucket_is_refused(rolled_back_db):
    from app.tefca_registry import models as reg
    from app.tefca_registry.qa_gate import QaGateRefused, record_analyst_determination
    from sqlalchemy import select

    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "R1")
    review_id = await _dq_case(db, org)
    record = (await db.execute(select(reg.ReviewRecord)
                               .where(reg.ReviewRecord.review_id == review_id))).scalar_one()
    record.classification_bucket = "B3"
    await db.flush()

    with pytest.raises(QaGateRefused, match="no-op"):
        await record_analyst_determination(
            db, review_id, user=ANALYST_A, determination="RECLASSIFY",
            determined_bucket="B3", rationale="Trying to reclassify to the same bucket.")
    out = await record_analyst_determination(
        db, review_id, user=ANALYST_A, determination="RECLASSIFY",
        determined_bucket="B2", rationale="A genuinely different bucket applies here.")
    assert out


def test_qa_eligibility_states_maker_checker_for_the_current_user():
    from app.tefca_registry.workflow_routes import qa_eligibility

    maker = SimpleNamespace(id=uuid.uuid4(), email="maker@synthetic.test", role="qalead")
    other_lead = SimpleNamespace(id=uuid.uuid4(), email="lead@synthetic.test", role="qalead")
    analyst = SimpleNamespace(id=uuid.uuid4(), email="analyst@synthetic.test", role="reviewer")
    result = {"recommendation": {"determination": {
        "actor_user_id": str(maker.id), "actor_email": maker.email}}}

    mine = qa_eligibility(result, maker)
    assert mine["is_maker"] is True and mine["can_qa"] is False
    assert "segregation" in mine["reason"].lower()
    theirs = qa_eligibility(result, other_lead)
    assert theirs["can_qa"] is True and theirs["is_maker"] is False
    junior = qa_eligibility(result, analyst)
    assert junior["can_qa"] is False and "QA lead" in junior["reason"]
    none_yet = qa_eligibility({"recommendation": {"determination": None}}, other_lead)
    assert none_yet["can_qa"] is False


# ── identity and provenance (QA-040, 042, 043, 052, 061) ──────────────────────

@pytest.mark.asyncio
async def test_case_dto_carries_final_classification_and_delivery_context(rolled_back_db):
    from app.tefca_registry import case_assignment as assignment
    from app.tefca_registry import models as reg
    from sqlalchemy import select

    db = rolled_back_db
    intake_id = await _intake(db, label="QA 50-Record Workflow Test")
    org = await _org(db, intake_id, "D1")
    review_id = await _dq_case(db, org)
    record = (await db.execute(select(reg.ReviewRecord)
                               .where(reg.ReviewRecord.review_id == review_id))).scalar_one()
    # A review-cycle shaped payload: no case_classification / severity keys.
    record.verification_results = {"source_intake_id": str(intake_id),
                                   "queue_source": "review_cycle", "sample_id": "s-1"}
    record.classification_bucket = "B3"
    record.classification_rule = "RULE-004"
    record.classification_rule_version = 2
    await db.flush()

    dto = await assignment._dto(db, record)
    assert dto["case_classification"] == "B3"
    assert dto["final_classification"] == "B3" and dto["classification_bucket"] == "B3"
    assert dto["classification_rule"] == "RULE-004" and dto["classification_rule_version"] == 2
    assert dto["source_intake_id"] == str(intake_id)
    assert dto["delivery_label"] == "QA 50-Record Workflow Test"
    assert dto["queue_source"] == "review_cycle" and dto["sample_id"] == "s-1"
    assert dto["entity_name"]


@pytest.mark.asyncio
async def test_supervisor_items_resolve_holder_identity_and_delivery(rolled_back_db):
    from app.models.database import User
    from app.tefca_registry import case_assignment as assignment
    from app.tefca_registry import models as reg
    from app.tefca_registry import supervisor_ops as so
    from sqlalchemy import select

    db = rolled_back_db
    analyst = User(id=uuid.uuid4(), email=f"analyst-{uuid.uuid4().hex[:6]}@synthetic.test",
                   password_hash="x", full_name="Synthetic Analyst", role="reviewer")
    db.add(analyst)
    await db.flush()
    intake_id = await _intake(db, label="QA 50-Record Workflow Test")
    org = await _org(db, intake_id, "H1")
    review_id = await _dq_case(db, org)
    record = (await db.execute(select(reg.ReviewRecord)
                               .where(reg.ReviewRecord.review_id == review_id))).scalar_one()
    record.verification_results = {**(record.verification_results or {}),
                                   "source_intake_id": str(intake_id)}
    record.assigned_to_user_id = analyst.id
    record.assigned_at = datetime.utcnow()
    await db.flush()

    detail = await so.case_detail(db, review_id)
    assert detail["assigned_to"]["email"] == analyst.email
    assert detail["assigned_to"]["display_name"] == "Synthetic Analyst"
    assert detail["assigned_to"]["user_id"] == str(analyst.id)
    assert detail["delivery"]["intake_id"] == str(intake_id)
    assert detail["delivery"]["delivery_label"] == "QA 50-Record Workflow Test"

    workload = await so.analyst_workload(db)
    mine = next(b for b in workload["analysts"] if b["assigned_to_user_id"] == str(analyst.id))
    assert mine["principal"]["email"] == analyst.email and mine["principal"]["resolved"] is True


@pytest.mark.asyncio
async def test_case_timeline_events_are_identified_timed_and_labelled(rolled_back_db):
    from app.tefca_registry import supervisor_ops as so

    db = rolled_back_db
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "T1")
    review_id = await _dq_case(db, org)
    await _through_qa(db, review_id, action="APPROVE")

    timeline = await so.audit_timeline(db, review_id)
    assert any(e["event"] == "case_created" for e in timeline)
    for entry in timeline:
        assert entry["event_id"] and entry["source"] and entry["kind"] in ("transition", "decision_record")
        assert entry["at"] and entry["at"].endswith("+00:00"), entry
        assert entry["label"]
        assert "correlation_id" in entry and "actor_role" in entry
    kinds = {(e["event"], e["kind"]) for e in timeline}
    # The registry transition and the decision record of the same act are
    # both present and distinguished, not duplicated under one name.
    assert ("analyst_determination_recorded", "transition") in kinds
    assert ("analyst_determination", "decision_record") in kinds
    assert ("became_reportable", "transition") in kinds
    ids = [e["event_id"] for e in timeline]
    assert len(ids) == len(set(ids))


# ── delivery audit history (QA-023..029) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_delivery_audit_entries_expose_governed_fields_first_class(rolled_back_db):
    from app.tefca_registry import audit as reg_audit
    from app.tefca_registry.rce import delivery_routes

    db = rolled_back_db
    intake_id = await _intake(db)
    reg_audit.record(db, "entity_created", None, actor_id=None,
                     actor_email="uploader@synthetic.test",
                     metadata={"intake_id": str(intake_id), "correlation_id": "corr-1"})
    await db.flush()

    out = await delivery_routes.delivery_audit_route(
        str(intake_id), limit=50, offset=0, job_id="11111111-1111-1111-1111-111111111111",
        db=db, user=SimpleNamespace(role="reviewer"))
    assert out["identifiers"]["intake_id"] == str(intake_id)
    assert out["identifiers"]["requested_job_id"] == "11111111-1111-1111-1111-111111111111"
    assert "latest_job_id" in out["identifiers"]
    item = next(i for i in out["items"] if i["action"] == "entity_created")
    assert item["at"].endswith("+00:00")
    assert item["event_type"] and item["resource_type"] and item["resource_id"]
    assert item["correlation_id"] == "corr-1"
    assert item["actor_class"] == "human" and item["human_initiator"] == "uploader@synthetic.test"
    assert item["label"] == "Entity created"
    assert delivery_routes._audit_label("PROMOTION:COMPLETED") == "Promotion completed"


# ── QA monitor (QA-059) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_endpoint_check_probes_only_public_endpoints(monkeypatch):
    import httpx

    from app.Tefca import qa_engine

    seen = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            seen.append(url)
            return _Resp(200 if url.endswith(("/health", "/api/tefca/status")) else 401)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("QA_BASE_URL", "https://example.invalid")
    check = qa_engine.PlatformReadinessCheck()
    out = await check.check_api_endpoints()
    assert out["passed"] is True, out
    assert all("dashboard/summary" not in u for u in seen)
    assert any(u.endswith("/api/tefca/status") for u in seen)


# ── analyst directory (QA-050, QA-053) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyst_directory_lists_verified_assignable_accounts_with_workload(rolled_back_db):
    from app.models.database import User
    from app.tefca_registry import models as reg
    from app.tefca_registry.workflow_routes import analyst_directory
    from sqlalchemy import select

    db = rolled_back_db
    tag = uuid.uuid4().hex[:6]
    analyst = User(id=uuid.uuid4(), email=f"dir-{tag}@synthetic.test", password_hash="x",
                   full_name="Directory Analyst", role="reviewer")
    viewer = User(id=uuid.uuid4(), email=f"viewer-{tag}@synthetic.test", password_hash="x",
                  full_name="", role="viewer")
    inactive = User(id=uuid.uuid4(), email=f"gone-{tag}@synthetic.test", password_hash="x",
                    full_name="Gone", role="reviewer", is_active=False)
    db.add_all([analyst, viewer, inactive])
    await db.flush()
    intake_id = await _intake(db)
    org = await _org(db, intake_id, "A1")
    review_id = await _dq_case(db, org)
    record = (await db.execute(select(reg.ReviewRecord)
                               .where(reg.ReviewRecord.review_id == review_id))).scalar_one()
    record.assigned_to_user_id = analyst.id
    await db.flush()

    out = await analyst_directory(db=db, user=SUPERVISOR)
    by_email = {i["email"]: i for i in out["items"]}
    assert by_email[analyst.email]["open_cases"] == 1
    assert by_email[analyst.email]["display_name"] == "Directory Analyst"
    assert by_email[analyst.email]["role"] == "reviewer"
    assert viewer.email not in by_email and inactive.email not in by_email
    assert "eligible_roles" in out
    # Below the supervisor floor: no accounts, a stated reason (LOGIN-013).
    below = await analyst_directory(db=db, user=SimpleNamespace(id=viewer.id, email=viewer.email, role="viewer"))
    assert below["items"] == [] and below["availability"] == "requires_role:senior_analyst"
