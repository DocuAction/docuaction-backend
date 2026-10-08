"""Issue history, track A6 (2026-10-08): account / role access control with REAL
users, real signed tokens, the real auth dependency and the real database.

Both directions for every role in the hierarchy, account-level narrowing via
`feed:<TAG>` entries in users.allowed_modules, auditable denial, no leakage of
hidden deliveries through gaps, and no entity data in the logs.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

import pytest

from support_delivery_api import TAG, headers_for, run

pytestmark = pytest.mark.usefixtures("db_required")

ONC = f"ONCA6-{TAG}"
SYN = f"SYNA6-{TAG}"
OID_B = f"A6-B-{TAG}"          # in both feeds
OID_R = f"A6-R-{TAG}"          # only in the reviewer-only feed
URL = "/api/tefca/rce/entities/by-oid/{}/issue-history"
ROLES = ["viewer", "contributor", "manager", "reviewer", "senior_analyst",
         "qalead", "program_manager", "admin"]
REVIEWER_AND_ABOVE = {"reviewer", "senior_analyst", "qalead", "program_manager", "admin"}

_IDS = {"intakes": [], "users": []}


async def _seed():
    from app.core.config import settings
    from app.core.database import async_session_maker

    from issue_history_support_2026_10_07 import (BAD_LEN_NPI, entity_row, filler_row,
                                                  run_engine, seed_delivery)

    settings.ENABLE_RECORD_CHECK_RESULTS = True
    out = {}
    try:
        async with async_session_maker() as db:
            async def one(key, month, day, feed, oid, status="PARSED", engine=True):
                rows = [entity_row(oid, npi=BAD_LEN_NPI), filler_row(f"{TAG}{key}")]
                iid = await seed_delivery(db, rows, received_at=datetime(2026, month, day),
                                          feed=feed, status=status)
                if engine:
                    await run_engine(db, iid)
                _IDS["intakes"].append(iid)
                out[key] = str(iid)

            await one("onc_jul", 7, 5, ONC, OID_B)
            await one("syn_aug", 8, 5, SYN, OID_B)
            await one("syn_failed", 8, 20, SYN, OID_B, status="FAILED", engine=False)
            await one("onc_sep", 9, 5, ONC, OID_B)
            await one("syn_r", 8, 4, SYN, OID_R)
    finally:
        settings.ENABLE_RECORD_CHECK_RESULTS = False
    return out


async def _cleanup():
    from sqlalchemy import text

    from app.core.database import async_session_maker

    ids = [str(i) for i in _IDS["intakes"]]
    async with async_session_maker() as db:
        p = {"ids": ids}
        for sql in (
            "delete from rce_record_check_results where run_id in (select id from "
            "rce_ingestion_runs where source_intake_id = any(cast(:ids as uuid[])))",
            "delete from rce_issues where source_intake_id = any(cast(:ids as uuid[]))",
            "delete from rce_rule_execution_history where run_id in (select id from "
            "rce_ingestion_runs where source_intake_id = any(cast(:ids as uuid[])))",
            "delete from rce_ingestion_runs where source_intake_id = any(cast(:ids as uuid[]))",
            "delete from rce_source_records where source_intake_id = any(cast(:ids as uuid[]))",
            "delete from rce_source_intakes where id = any(cast(:ids as uuid[]))",
        ):
            await db.execute(text(sql), p)
        await db.execute(text("delete from tefca_reg_audit_log where action = "
                              "'issue_history_read' and metadata->>'oid' like :l"),
                         {"l": f"A6-%-{TAG}"})
        await db.execute(text("delete from audit_logs where resource_type = "
                              "'issue_history' and resource_id like :l"),
                         {"l": f"%A6-%-{TAG}%"})
        if _IDS["users"]:
            await db.execute(text("delete from users where id = any(cast(:u as uuid[]))"),
                             {"u": _IDS["users"]})
        await db.commit()


@pytest.fixture(scope="module")
def seeded():
    data = run(_seed())
    yield data
    run(_cleanup())


@pytest.fixture
def conf(monkeypatch):
    from app.core.config import settings

    def set_(viewer=ONC, reviewer=SYN, enabled=True):
        monkeypatch.setattr(settings, "ENABLE_ISSUE_HISTORY", enabled)
        monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_VIEWER", viewer)
        monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_REVIEWER", reviewer)

    set_()
    return set_


def get(client, oid, role=None, headers=None, path=None):
    h = headers if headers is not None else (headers_for(role) if role else {})
    return client.get(path or URL.format(oid), headers=h)


async def _make_user(role, modules):
    from app.core.database import async_session_maker
    from app.models.database import User

    uid = uuid.uuid4()
    async with async_session_maker() as db:
        db.add(User(id=uid, email=f"a6-{role}-{uid.hex[:8]}-{TAG}@test.local",
                    password_hash="x" * 60, full_name="A6", company="test", role=role,
                    plan="enterprise", allowed_modules=modules, is_active=True,
                    is_verified=True, status="active"))
        await db.commit()
    _IDS["users"].append(str(uid))
    return uid


def headers_with_modules(role, modules):
    from app.core.security import create_access_token

    uid = run(_make_user(role, modules))
    token = create_access_token({"sub": str(uid), "role": role}, is_admin=(role == "admin"))
    return {"Authorization": f"Bearer {token}"}


def feeds_of(resp):
    return sorted({d["feed"] for d in resp.json()["deliveries"]})


# -- every role, both directions -----------------------------------------------

@pytest.mark.parametrize("role", ROLES)
def test_every_role_reaches_exactly_the_feeds_and_fields_its_level_allows(
        client, seeded, conf, role):
    r = get(client, OID_B, role)
    assert r.status_code == 200, role
    body = r.json()
    reviewer = role in REVIEWER_AND_ABOVE
    assert feeds_of(r) == (sorted([ONC, SYN]) if reviewer else [ONC])
    text = r.text
    has_values = '"original_value"' in text
    assert has_values == reviewer, role
    if not reviewer:
        from app.tefca_registry.rce import issue_history_core as core
        assert core.forbidden_keys_present(body) == []
    # the reviewer-only feed: visible only from the reviewer level up
    rr = get(client, OID_R, role)
    assert rr.status_code == (200 if reviewer else 404), role


@pytest.mark.parametrize("role", ROLES)
def test_a_role_with_no_configured_feed_sees_nothing_at_any_level(client, seeded, conf, role):
    conf(viewer="", reviewer="")
    assert get(client, OID_B, role).status_code == 404


def test_below_the_floor_is_refused_and_the_denial_is_audited(client, seeded, conf):
    from sqlalchemy import text

    from app.core.database import async_session_maker

    async def denials():
        async with async_session_maker() as db:
            return (await db.execute(text(
                "select count(*) from audit_logs where resource_type='issue_history' "
                "and outcome='blocked' and resource_id like :l"),
                {"l": f"%{OID_B}%"})).scalar()

    before = run(denials())
    r = get(client, OID_B, "nobody")
    assert r.status_code == 403
    assert run(denials()) == before + 1


def test_unauthenticated_and_forged_tokens_never_reach_the_data(client, seeded, conf):
    assert get(client, OID_B).status_code in (401, 403)
    r = client.get(URL.format(OID_B), headers={"Authorization": "Bearer forged"})
    assert r.status_code in (401, 403)
    assert OID_B not in r.text or r.status_code in (401, 403)


def test_a_hidden_feed_denial_is_audited_with_zero_visible_and_no_value(client, seeded, conf):
    from sqlalchemy import text

    from app.core.database import async_session_maker

    async def rows():
        async with async_session_maker() as db:
            return (await db.execute(text(
                "select metadata from tefca_reg_audit_log where action='issue_history_read' "
                "and metadata->>'oid' = :o order by created_at"), {"o": OID_R})).all()

    before = len(run(rows()))
    assert get(client, OID_R, "viewer").status_code == 404
    after = run(rows())
    assert len(after) == before + 1
    assert after[-1][0] == {"oid": OID_R, "role": "viewer", "visible_deliveries": 0}


# -- gaps never leak a hidden feed -----------------------------------------------

def test_a_failed_intake_in_a_hidden_feed_is_not_visible_to_the_viewer(client, seeded, conf):
    viewer = get(client, OID_B, "viewer").json()
    assert viewer["sequence_gaps"] == []
    assert seeded["syn_failed"] not in json.dumps(viewer)
    reviewer = get(client, OID_B, "reviewer").json()
    kinds = [g["kind"] for g in reviewer["sequence_gaps"]]
    assert kinds == ["DELIVERY_NOT_PROCESSED"]


# -- account-level narrowing --------------------------------------------------------

def test_reviewer_account_narrowed_to_one_feed_sees_only_that_feed(client, seeded, conf):
    h = headers_with_modules("reviewer", [f"feed:{ONC}"])
    r = get(client, OID_B, headers=h)
    assert r.status_code == 200 and feeds_of(r) == [ONC]
    assert r.json()["sequence_gaps"] == []
    assert get(client, OID_R, headers=h).status_code == 404
    # the same role without entries still sees both: narrowing is per account
    assert feeds_of(get(client, OID_B, "reviewer")) == sorted([ONC, SYN])


def test_account_entries_can_never_widen_beyond_the_role(client, seeded, conf):
    h = headers_with_modules("viewer", [f"feed:{SYN}", f"feed:{ONC}"])
    r = get(client, OID_B, headers=h)
    assert feeds_of(r) == [ONC]                     # SYN is reviewer-only
    assert get(client, OID_R, headers=h).status_code == 404
    admin = headers_with_modules("admin", [f"feed:{SYN}"])
    assert get(client, OID_B, headers=admin).status_code == 200
    assert feeds_of(get(client, OID_B, headers=admin)) == [SYN]
    assert get(client, OID_R, headers=admin).status_code == 200


def test_an_account_narrowed_to_an_unrelated_feed_sees_nothing(client, seeded, conf):
    h = headers_with_modules("admin", ["feed:SOMETHING-ELSE"])
    assert get(client, OID_B, headers=h).status_code == 404
    assert get(client, OID_R, headers=h).status_code == 404


def test_unrelated_module_entries_do_not_narrow_anything(client, seeded, conf):
    h = headers_with_modules("reviewer", ["dashboard", "reports"])
    assert feeds_of(get(client, OID_B, headers=h)) == sorted([ONC, SYN])


# -- list / count / export ------------------------------------------------------------

@pytest.mark.parametrize("suffix", ["/count", "/export", "/list", ".csv"])
def test_there_is_no_list_count_or_export_surface(client, seeded, conf, suffix):
    r = get(client, OID_B, "admin", path=URL.format(OID_B) + suffix)
    assert r.status_code in (404, 405)
    assert OID_B not in r.text
    r = get(client, OID_B, "admin", path="/api/tefca/rce/issue-history" + suffix)
    assert r.status_code in (404, 405)


def test_hidden_and_unknown_answer_identically_for_every_role(client, seeded, conf):
    for role in ("viewer", "contributor", "manager"):
        a, b = get(client, OID_R, role), get(client, f"A6-NOPE-{TAG}", role)
        assert a.status_code == b.status_code == 404
        pa, pb = a.json(), b.json()
        pa.pop("request_id", None), pb.pop("request_id", None)
        assert pa == pb


# -- logs -------------------------------------------------------------------------------

def test_logs_carry_no_entity_identifier_account_email_or_value(client, seeded, conf, caplog):
    caplog.set_level(logging.DEBUG)
    h_email = headers_for("viewer")
    from support_delivery_api import user_for

    email = user_for("viewer")["email"]
    for oid, who in ((OID_B, "viewer"), (OID_R, "viewer"), (OID_B, "reviewer"),
                     (OID_B, "nobody"), (f"A6-NOPE-{TAG}", "admin")):
        get(client, oid, who)
    conf(enabled=False)
    get(client, OID_B, "admin")
    blob = "\n".join(f"{r.getMessage()} {r.args}" for r in caplog.records)
    for secret in (OID_B, OID_R, email, "12345", ONC, SYN):
        assert secret not in blob, secret
    assert h_email
