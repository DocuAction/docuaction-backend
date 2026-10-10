"""Track A3 - QA independence controls, deadline helpers, inactive notifications.

DB-free. Every control under test is default OFF; these tests pin both the OFF
behaviour (unchanged) and the ON behaviour (PROPOSAL flags).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.tefca_registry import deadlines as dl
from app.tefca_registry import models as reg
from app.tefca_registry import notifications as nt
from app.tefca_registry import qa_controls as qc
from app.tefca_registry import qa_gate

E = reg.ReviewDecisionEvent


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- defaults --

@pytest.mark.parametrize("name", [
    "ENABLE_SOD_GRANTOR_VERIFICATION", "ENABLE_RELEASE_GENERATOR_SEPARATION",
    "ENABLE_RELEASE_READ_ACK", "ENABLE_DENIAL_AUDIT", "ENABLE_DEADLINE_DRY_RUN",
    "ENABLE_NOTIFICATIONS"])
def test_every_new_flag_defaults_off(name):
    assert getattr(settings, name) is False
    assert settings.DEADLINE_CONFIG_JSON == ""
    assert settings.NOTIFICATION_TRANSPORT == ""


# ----------------------------------------------------------- grantor checks --

def _user(role="admin", active=True, status="active"):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=active, status=status)


def test_grantor_denials_have_machine_codes():
    actor, analyst = uuid.uuid4(), uuid.uuid4()
    adm = _user()
    ok = dict(grantor=adm, grantor_id=adm.id, actor_id=actor, analyst_id=analyst)
    assert qc.grantor_denial(**ok) is None
    assert qc.grantor_denial(**{**ok, "grantor_id": None}) == qc.SOD_GRANTOR_MISSING
    assert qc.grantor_denial(**{**ok, "grantor_id": actor}) == qc.SOD_GRANTOR_IS_ACTOR
    assert qc.grantor_denial(**{**ok, "grantor_id": analyst}) == qc.SOD_GRANTOR_IS_ANALYST
    assert qc.grantor_denial(**{**ok, "grantor": None}) == qc.SOD_GRANTOR_NOT_FOUND
    assert qc.grantor_denial(**{**ok, "grantor": _user(active=False)}) == qc.SOD_GRANTOR_INACTIVE
    assert qc.grantor_denial(**{**ok, "grantor": _user(status="disabled")}) == qc.SOD_GRANTOR_INACTIVE
    for role in ("qalead", "program_manager", "viewer", "nonsense"):
        assert qc.grantor_denial(**{**ok, "grantor": _user(role=role)}) == qc.SOD_GRANTOR_NOT_ADMIN


# --------------------------------------- submit_qa_review with a fake session --

class FakeDb:
    def __init__(self, grantor=None):
        self.grantor, self.added = grantor, []

    async def get(self, model, key):
        return self.grantor

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


def _setup(monkeypatch, analyst_id):
    det = E(id=uuid.uuid4(), review_id="REV-1", sequence_number=1,
            event_type=E.ANALYST_DETERMINATION, actor_user_id=analyst_id,
            actor_email="a@example.invalid", actor_role="reviewer",
            determination="CONFIRM", occurred_at=datetime(2026, 10, 1))
    review = SimpleNamespace(review_id="REV-1", entity_id=None, reportable_at=None)

    async def _rev(db, rid):
        return review

    async def _ev(db, rid):
        return [det]

    async def _seq(db, rid):
        return 2
    monkeypatch.setattr(qa_gate, "_review_or_refuse", _rev)
    monkeypatch.setattr(qa_gate, "_events", _ev)
    monkeypatch.setattr(qa_gate, "_next_sequence", _seq)


def _qa(db, actor, grantor_id, **kw):
    return qa_gate.submit_qa_review(
        db, "REV-1", user=SimpleNamespace(id=actor, email="q@example.invalid", role="qalead"),
        qa_action="RETURN", qa_reason="long enough reason",
        sod_exception_granted_by=grantor_id, sod_exception_reason="long enough reason", **kw)


def test_flag_off_keeps_legacy_self_attestable_behaviour(monkeypatch):
    """OFF = unchanged: any UUID != actor is accepted (the documented gap)."""
    actor = uuid.uuid4()
    _setup(monkeypatch, analyst_id=actor)
    monkeypatch.setattr(settings, "ENABLE_SOD_GRANTOR_VERIFICATION", False)
    out = run(_qa(FakeDb(grantor=None), actor, uuid.uuid4()))
    assert out["qa_action"] == "RETURN"


def test_flag_on_refuses_unverified_grantor_with_code(monkeypatch):
    actor = uuid.uuid4()
    _setup(monkeypatch, analyst_id=actor)
    monkeypatch.setattr(settings, "ENABLE_SOD_GRANTOR_VERIFICATION", True)
    with pytest.raises(qa_gate.QaGateRefused) as e:
        run(_qa(FakeDb(grantor=None), actor, uuid.uuid4()))
    assert e.value.code == qc.SOD_GRANTOR_NOT_FOUND
    with pytest.raises(qa_gate.QaGateRefused) as e:
        g = _user(role="qalead")
        run(_qa(FakeDb(grantor=g), actor, g.id))
    assert e.value.code == qc.SOD_GRANTOR_NOT_ADMIN


def test_flag_on_accepts_real_admin_grantor(monkeypatch):
    actor = uuid.uuid4()
    _setup(monkeypatch, analyst_id=actor)
    monkeypatch.setattr(settings, "ENABLE_SOD_GRANTOR_VERIFICATION", True)
    g = _user()
    out = run(_qa(FakeDb(grantor=g), actor, g.id))
    assert out["sequence_number"] == 2


def test_self_qa_without_exception_carries_code(monkeypatch):
    actor = uuid.uuid4()
    _setup(monkeypatch, analyst_id=actor)
    with pytest.raises(qa_gate.QaGateRefused) as e:
        run(qa_gate.submit_qa_review(
            FakeDb(), "REV-1",
            user=SimpleNamespace(id=actor, email="q@example.invalid", role="qalead"),
            qa_action="RETURN", qa_reason="long enough reason"))
    assert e.value.code == "SOD_SELF_QA"
    assert qa_gate.QaGateRefused("x").code == "QA_REFUSED"  # legacy raise sites


# --------------------------------------------------------- release controls --

def test_release_denial_pure():
    g = uuid.uuid4()
    base = dict(generator_id=g, generator_email="g@example.invalid",
                actor_id=uuid.uuid4(), actor_email="p@example.invalid",
                acknowledge_read=False, separation_enabled=True, read_ack_enabled=True)
    assert qc.release_denial(action="PM_REVIEWED", **base) is None
    assert qc.release_denial(action="READY_FOR_DELIVERY", **base) == qc.RELEASE_READ_ACK_REQUIRED
    assert qc.release_denial(action="READY_FOR_DELIVERY", **{**base, "acknowledge_read": True}) is None
    assert qc.release_denial(action="PM_REVIEWED", **{**base, "actor_id": g}) == qc.RELEASE_GENERATOR_IS_RELEASER
    assert qc.release_denial(action="PM_REVIEWED", **{**base, "actor_email": "G@Example.invalid"}) \
        == qc.RELEASE_GENERATOR_IS_RELEASER
    assert qc.release_denial(action="PM_REVIEWED", **{**base, "generator_id": None,
                                                      "generator_email": None}) == qc.RELEASE_GENERATOR_UNKNOWN
    # returning to draft is never blocked
    assert qc.release_denial(action="RETURNED_TO_DRAFT", **{**base, "actor_id": g}) is None
    # both off: never denies
    assert qc.release_denial(action="READY_FOR_DELIVERY", **{**base, "actor_id": g,
                             "separation_enabled": False, "read_ack_enabled": False}) is None


class RelDb:
    def __init__(self):
        self.added, self.commits = [], 0

    def add(self, o):
        self.added.append(o)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _release(monkeypatch, user, row, body):
    from app.reports import routes
    async def _stored(db, rid, *a, **k):
        return row
    monkeypatch.setattr(routes, "_stored", _stored)
    import sqlalchemy.orm.attributes as _attrs
    monkeypatch.setattr(_attrs, "flag_modified", lambda *a, **k: None)
    db = RelDb()
    req = routes.ReleaseRequest(**body)
    return db, run(routes.post_release("R1", req, db=db, user=user))


def test_release_route_unchanged_when_flags_off(monkeypatch):
    uid = uuid.uuid4()
    row = SimpleNamespace(report_data={"snapshot": {}}, generated_by=uid, report_type="x")
    user = SimpleNamespace(id=uid, email="same@example.invalid")
    _, out = _release(monkeypatch, user, row, {"action": "PM_REVIEWED"})
    assert out["release"]["status"] == "PM_REVIEWED"


def test_release_route_refuses_generator_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RELEASE_GENERATOR_SEPARATION", True)
    uid = uuid.uuid4()
    row = SimpleNamespace(report_data={"snapshot": {}}, generated_by=uid, report_type="x")
    user = SimpleNamespace(id=uid, email="same@example.invalid")
    with pytest.raises(HTTPException) as e:
        _release(monkeypatch, user, row, {"action": "PM_REVIEWED"})
    assert e.value.status_code == 409
    assert e.value.headers["X-Denial-Code"] == qc.RELEASE_GENERATOR_IS_RELEASER
    other = SimpleNamespace(id=uuid.uuid4(), email="other@example.invalid")
    _, out = _release(monkeypatch, other, row, {"action": "PM_REVIEWED"})
    assert out["release"]["status"] == "PM_REVIEWED"


def test_release_read_ack_recorded(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RELEASE_READ_ACK", True)
    row = SimpleNamespace(report_data={"snapshot": {}, "release": {
        "status": "PM_REVIEWED", "history": []}}, generated_by=uuid.uuid4(), report_type="x")
    user = SimpleNamespace(id=uuid.uuid4(), email="p@example.invalid")
    with pytest.raises(HTTPException) as e:
        _release(monkeypatch, user, row, {"action": "READY_FOR_DELIVERY"})
    assert e.value.headers["X-Denial-Code"] == qc.RELEASE_READ_ACK_REQUIRED
    _, out = _release(monkeypatch, user, row,
                      {"action": "READY_FOR_DELIVERY", "acknowledge_read": True})
    assert out["release"]["history"][-1]["read_acknowledged"] is True


def test_invalid_transition_carries_code(monkeypatch):
    row = SimpleNamespace(report_data={}, generated_by=None, report_type="x")
    user = SimpleNamespace(id=uuid.uuid4(), email="p@example.invalid")
    with pytest.raises(HTTPException) as e:
        _release(monkeypatch, user, row, {"action": "READY_FOR_DELIVERY"})
    assert e.value.headers["X-Denial-Code"] == qc.RELEASE_TRANSITION_REFUSED


def test_denial_audit_off_writes_nothing():
    db = RelDb()
    run(qc.audit_denial(db, action="x", code="C", user=SimpleNamespace(id=uuid.uuid4())))
    assert db.added == [] and db.commits == 0


def test_denial_audit_on_writes_row_with_code(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_DENIAL_AUDIT", True)
    db = RelDb()
    run(qc.audit_denial(db, action="qa_denied_qa", code="SOD_SELF_QA",
                        user=SimpleNamespace(id=uuid.uuid4(), email="q@example.invalid"),
                        metadata={"review_id": "REV-1"}))
    assert len(db.added) == 1 and db.commits == 1
    assert "SOD_SELF_QA" in str(db.added[0].__dict__.get("metadata_json")
                                or db.added[0].__dict__)


# ------------------------------------------------------------ business days --

def test_business_day_math():
    fri = datetime(2026, 10, 9, 15, 0)            # Friday
    assert dl.add_business_days(fri, 1) == datetime(2026, 10, 12, 15, 0)   # Monday
    assert dl.add_business_days(fri, 5) == datetime(2026, 10, 16, 15, 0)
    assert dl.add_business_days(datetime(2026, 10, 10, 9), 1) == datetime(2026, 10, 13, 9)  # Sat start rolls
    assert dl.add_business_days(fri, 0) == fri
    hol = frozenset({date(2026, 10, 12)})
    assert dl.add_business_days(fri, 1, hol) == datetime(2026, 10, 13, 15, 0)
    assert dl.business_days_between(date(2026, 10, 9), date(2026, 10, 12)) == 1
    assert dl.business_days_between(date(2026, 10, 12), date(2026, 10, 9)) == 0
    with pytest.raises(ValueError):
        dl.add_business_days(fri, -1)


# ------------------------------------------------------- deadline register ---

def test_empty_config_resolves_nothing_and_chooses_no_defaults():
    cfg = dl.parse_config("")
    r = dl.compute_due("B4_NOTIFY_ONC", cfg, {"FINDING_CONFIRMED": datetime(2026, 10, 9)})
    assert r["status"] == dl.STATUS_UNCONFIGURED and r["due_at"] is None
    assert any("unit" in x for x in r["reasons"]) and any("clock-start" in x for x in r["reasons"])
    # a rule with a documented unit still has NO clock start by default
    r2 = dl.compute_due("B3_AGT_RESEARCH", cfg, {"ENTITY_CLASSIFIED": datetime(2026, 10, 9)})
    assert r2["status"] == dl.STATUS_UNCONFIGURED and r2["unit"] == "business_days"
    assert len(dl.unresolved_questions(cfg)) == 6 + 1  # 6 clock-starts + B4 unit


def test_configured_rules_compute():
    cfg = dl.parse_config('{"holidays": ["2026-10-12"], "rules": {'
                          '"B4_NOTIFY_ONC": {"unit": "clock_hours", "clock_start_event": "FINDING_CONFIRMED"},'
                          '"B3_ONC_DATA_RESPONSE": {"clock_start_event": "NOTIFICATION_SENT"}}}')
    t = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
    b4 = dl.compute_due("B4_NOTIFY_ONC", cfg, {"FINDING_CONFIRMED": t})
    assert b4["due_at"] == "2026-10-10T15:00:00+00:00"            # clock hours: weekend counts
    b3 = dl.compute_due("B3_ONC_DATA_RESPONSE", cfg, {"NOTIFICATION_SENT": t})
    assert b3["due_at"] == "2026-10-22T15:00:00+00:00"            # 8 bd, Oct 12 holiday
    assert dl.compute_due("B3_ONC_DATA_RESPONSE", cfg, {})["status"] == dl.STATUS_WAITING


def test_b4_business_day_alternative_is_explicit():
    cfg = dl.parse_config('{"rules": {"B4_NOTIFY_ONC": {"unit": "business_days", '
                          '"amount": 1, "clock_start_event": "FINDING_CONFIRMED"}}}')
    t = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
    assert dl.compute_due("B4_NOTIFY_ONC", cfg, {"FINDING_CONFIRMED": t})["due_at"] \
        == "2026-10-12T15:00:00+00:00"


@pytest.mark.parametrize("bad", [
    "not json", "[]", '{"rules": {"NOPE": {}}}',
    '{"rules": {"B4_NOTIFY_ONC": {"unit": "weeks"}}}',
    '{"rules": {"B4_NOTIFY_ONC": {"clock_start_event": "WHENEVER"}}}',
    '{"rules": {"B4_NOTIFY_ONC": {"amount": -1}}}',
    '{"holidays": ["Thanksgiving"]}'])
def test_bad_config_is_refused(bad):
    with pytest.raises(dl.DeadlineConfigError):
        dl.parse_config(bad)


def test_register_dry_run_sends_nothing():
    cfg = dl.parse_config('{"rules": {"INDETERMINATE_RE_REVIEW": {"clock_start_event": "SOURCE_RESTORED"}}}')
    reg_out = dl.build_register([
        {"item_id": "i1", "rule": "INDETERMINATE_RE_REVIEW",
         "events": {"SOURCE_RESTORED": datetime(2026, 10, 1, 9)}},
        {"item_id": "i2", "rule": "B4_NOTIFY_ONC", "events": {}}],
        cfg, now=datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert reg_out["dry_run"] is True and reg_out["notifications_sent"] == 0
    assert reg_out["rows"][0]["overdue"] is True
    assert reg_out["summary"] == {"COMPUTED": 1, "UNCONFIGURED": 1}


def test_conflicts_reuse_live_constants():
    from app.tefca_registry.sla import REVIEW_SLA_DAYS
    c = dl.conflicts_with_existing_display_code()
    assert c[0]["values"] == REVIEW_SLA_DAYS
    assert {x["source"].split()[0] for x in c} >= {"app/tefca_registry/sla.py"}


def test_dry_run_route_is_flag_gated(monkeypatch):
    from app.tefca_registry import review_routes as rr
    req = rr.DeadlineDryRun(items=[])
    with pytest.raises(HTTPException) as e:
        run(rr.deadlines_dry_run(req, user=None))
    assert e.value.headers["X-Denial-Code"] == "FEATURE_DISABLED"
    monkeypatch.setattr(settings, "ENABLE_DEADLINE_DRY_RUN", True)
    out = run(rr.deadlines_dry_run(rr.DeadlineDryRun(
        items=[rr.DeadlineItem(item_id="a", rule="B4_NOTIFY_ONC")]), user=None))
    assert out["notifications_sent"] == 0 and out["rows"][0]["status"] == "UNCONFIGURED"


# ----------------------------------------------------------- notifications ---

def test_templates_render_and_require_values():
    r = nt.render("B4_ONC_NOTIFICATION", {"entity_ref": "ENT-1", "condition": "c",
                                          "top_provision": "p", "sources": "s"})
    assert r["subject"].startswith("[DRAFT]")
    with pytest.raises(nt.TemplateError):
        nt.render("B4_ONC_NOTIFICATION", {"entity_ref": "ENT-1"})
    with pytest.raises(nt.TemplateError):
        nt.render("NOPE", {})


def test_dispatch_is_suppressed_by_default_and_never_under_test():
    r = nt.render("OUTAGE_ESCALATION_TO_COR", {"source": "S", "since": "t", "alternative": "a"})
    assert nt.dispatch(r, ["x@example.invalid"])["status"] == nt.SUPPRESSED_DISABLED
    assert nt.dispatch(r, [], enabled=True, transport_name="")["status"] == nt.SUPPRESSED_NO_TRANSPORT
    assert nt.dispatch(r, [], enabled=True, transport_name="ghost")["status"] == nt.SUPPRESSED_UNREGISTERED
    t = nt.NullTransport()
    nt.register_transport(t)
    out = nt.dispatch(r, ["x@example.invalid"], enabled=True, transport_name="null")
    assert out == {"status": nt.SUPPRESSED_UNDER_TEST, "sent": False}
    assert t.recorded == []
