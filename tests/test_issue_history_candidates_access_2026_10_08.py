"""Issue history: candidate associations are reviewer-level only and BOTH sides of
an association obey the caller's role feeds and account feeds. Real users, real
tokens, real auth dependency, real database; synthetic data.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

import pytest

from support_delivery_api import TAG, headers_for, run

pytestmark = pytest.mark.usefixtures("db_required")

F1 = f"CAF1-{TAG}"
F2 = f"CAF2-{TAG}"
OID_P = f"CA-P-{TAG}"      # feed F1, NPI X
OID_S = f"CA-S-{TAG}"      # feed F1, NPI X  (same-feed candidate for P)
OID_Q = f"CA-Q-{TAG}"      # feed F2, NPI X  (cross-feed candidate for P)
OID_N = f"CA-N-{TAG}"      # feed F1, its own NPI (no association at all)
NPI_X = "1234567893"
NPI_Y = "1245319599"
URL = "/api/tefca/rce/entities/by-oid/{}/issue-history"
_IDS = {"intakes": [], "users": []}


async def _seed():
    from app.core.config import settings
    from app.core.database import async_session_maker

    from issue_history_support_2026_10_07 import (entity_row, filler_row, run_engine,
                                                  seed_delivery)

    settings.ENABLE_RECORD_CHECK_RESULTS = True
    try:
        async with async_session_maker() as db:
            async def one(feed, rows, month):
                iid = await seed_delivery(db, rows + [filler_row(f"{TAG}{feed}{month}")],
                                          received_at=datetime(2026, month, 5), feed=feed)
                await run_engine(db, iid)
                _IDS["intakes"].append(iid)

            for month in (7, 9):
                await one(F1, [entity_row(OID_P, npi=NPI_X), entity_row(OID_S, npi=NPI_X),
                               entity_row(OID_N, npi=NPI_Y)], month)
            await one(F2, [entity_row(OID_Q, npi=NPI_X)], 8)
    finally:
        settings.ENABLE_RECORD_CHECK_RESULTS = False


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
                         {"l": f"CA-%-{TAG}"})
        await db.execute(text("delete from audit_logs where resource_type = "
                              "'issue_history' and resource_id like :l"),
                         {"l": f"%CA-%-{TAG}%"})
        if _IDS["users"]:
            await db.execute(text("delete from users where id = any(cast(:u as uuid[]))"),
                             {"u": _IDS["users"]})
        await db.commit()


@pytest.fixture(scope="module")
def seeded():
    run(_seed())
    yield
    run(_cleanup())


@pytest.fixture
def conf(monkeypatch):
    from app.core.config import settings

    def set_(viewer=f"{F1},{F2}", reviewer="", enabled=True):
        monkeypatch.setattr(settings, "ENABLE_ISSUE_HISTORY", enabled)
        monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_VIEWER", viewer)
        monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_REVIEWER", reviewer)

    set_()
    return set_


async def _make_user(role, modules):
    from app.core.database import async_session_maker
    from app.models.database import User

    uid = uuid.uuid4()
    async with async_session_maker() as db:
        db.add(User(id=uid, email=f"ca-{role}-{uid.hex[:8]}-{TAG}@test.local",
                    password_hash="x" * 60, full_name="CA", company="test", role=role,
                    plan="enterprise", allowed_modules=modules, is_active=True,
                    is_verified=True, status="active"))
        await db.commit()
    _IDS["users"].append(str(uid))
    return uid


def modules_headers(role, modules):
    from app.core.security import create_access_token

    uid = run(_make_user(role, modules))
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": str(uid), "role": role}, is_admin=(role == "admin"))}


def get(client, oid, role=None, headers=None):
    return client.get(URL.format(oid), headers=headers or (headers_for(role) if role else {}))


def cands(r):
    return {c["record_id"]: c for c in r.json()["candidate_associations"]}


@pytest.mark.parametrize("role", ["viewer", "contributor", "manager"])
def test_below_reviewer_has_no_association_block_count_or_hint(client, seeded, conf, role):
    with_assoc, without = get(client, OID_P, role), get(client, OID_N, role)
    assert with_assoc.status_code == without.status_code == 200
    assert "candidate_associations" not in with_assoc.json()
    # identical response shape whether or not associations exist
    assert set(with_assoc.json()) == set(without.json())
    assert [set(d) for d in with_assoc.json()["deliveries"]] == \
        [set(d) for d in with_assoc.json()["deliveries"]]
    for other in (OID_S, OID_Q):
        assert other not in with_assoc.text
    assert NPI_X not in with_assoc.text and "UNCONFIRMED" not in with_assoc.text.upper()


def test_reviewer_with_both_feeds_sees_the_same_feed_and_the_cross_feed_association(client, seeded, conf):
    conf(viewer=F1, reviewer=F2)
    got = cands(get(client, OID_P, "reviewer"))
    assert set(got) == {OID_S, OID_Q}
    assert all(c["status"] == "UNCONFIRMED" for c in got.values())
    assert got[OID_Q]["shared_npi"] == [NPI_X]


def test_reviewer_without_the_other_feed_gets_no_trace_of_the_cross_feed_side(client, seeded, conf):
    conf(viewer=F1, reviewer="")                 # F2 is not authorized for anyone
    r = get(client, OID_P, "reviewer")
    assert set(cands(r)) == {OID_S}              # same-feed association still shown
    assert OID_Q not in r.text
    q = get(client, OID_Q, "reviewer")           # and the F2 record itself is not found
    assert q.status_code == 404 and OID_Q not in q.text and OID_P not in q.text


def test_account_level_narrowing_removes_the_other_side_too(client, seeded, conf):
    conf(viewer=F1, reviewer=F2)
    h = modules_headers("reviewer", [f"feed:{F1}"])
    r = get(client, OID_P, headers=h)
    assert r.status_code == 200 and set(cands(r)) == {OID_S}
    assert OID_Q not in r.text
    assert F2 not in r.text
    h2 = modules_headers("reviewer", [f"feed:{F2}"])
    r2 = get(client, OID_Q, headers=h2)
    assert r2.status_code == 200
    assert r2.json()["candidate_associations"] == []   # F1 side not authorized
    assert OID_P not in r2.text and OID_S not in r2.text


def test_same_feed_pair_is_a_reviewer_only_unconfirmed_candidate(client, seeded, conf):
    conf(viewer=F1, reviewer="")
    rev = get(client, OID_P, "reviewer")
    (c,) = [x for x in rev.json()["candidate_associations"]]
    assert c["record_id"] == OID_S and c["status"] == "UNCONFIRMED"
    assert get(client, OID_N, "reviewer").json()["candidate_associations"] == []
    assert "candidate_associations" not in get(client, OID_P, "viewer").json()


def test_a_hidden_association_never_reaches_logs_errors_or_other_surfaces(client, seeded, conf, caplog):
    conf(viewer=F1, reviewer="")
    caplog.set_level(logging.DEBUG)
    bodies = [get(client, OID_P, "reviewer").text, get(client, OID_P, "viewer").text,
              get(client, OID_Q, "reviewer").text, get(client, OID_Q, "viewer").text,
              get(client, OID_P, "nobody").text,
              client.get(URL.format(OID_P) + "/export", headers=headers_for("admin")).text,
              client.get(URL.format(OID_P) + "/count", headers=headers_for("admin")).text]
    conf(enabled=False)
    bodies.append(get(client, OID_P, "reviewer").text)
    assert caplog.records
    logs = "\n".join(f"{r.getMessage()} {r.args}" for r in caplog.records)
    for text in bodies + [logs]:
        assert OID_Q not in text and F2 not in text
    # the audit trail stores only the requested oid, role and a count
    from sqlalchemy import text as sql

    from app.core.database import async_session_maker

    async def rows():
        async with async_session_maker() as db:
            return [r[0] for r in (await db.execute(sql(
                "select metadata::text from tefca_reg_audit_log where action="
                "'issue_history_read' and metadata->>'oid' like :l"), {"l": f"CA-%-{TAG}"})).all()]

    audit = run(rows())
    assert audit and not any(OID_S in a or NPI_X in a for a in audit)
