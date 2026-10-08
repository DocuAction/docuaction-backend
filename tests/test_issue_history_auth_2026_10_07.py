"""Issue history: account-to-feed authorization with REAL authenticated users.

No mocks of the auth layer: real `users` rows, real signed tokens, the real
`require_role` dependency, the real application and the real database. The
deliveries are committed (the application runs in its own session) and removed
again at the end of the module.

What this proves is the per-ROLE feed policy of the scope document. It does not
prove a per-USER mapping, which does not exist (see the PR's deviation list).
"""
from __future__ import annotations

import json
import statistics
import time
import uuid
from datetime import datetime

import pytest

from support_delivery_api import TAG, headers_for, run

pytestmark = pytest.mark.usefixtures("db_required")

ONC = f"ONC-{TAG}"
SYN = f"SYN-{TAG}"
OID_A = f"AUTH-A-{TAG}"
OID_SYN_ONLY = f"AUTH-SYN-{TAG}"
OID_UNTAGGED = f"AUTH-UNT-{TAG}"
OID_UNKNOWN = f"AUTH-NOPE-{TAG}"
URL = "/api/tefca/rce/entities/by-oid/{}/issue-history"

_IDS = {"intakes": [], "users": []}


async def _seed():
    from app.core.config import settings
    from app.core.database import async_session_maker

    from issue_history_support_2026_10_07 import (BAD_LEN_NPI, GOOD_NPI, entity_row,
                                                  filler_row, run_engine, seed_delivery)

    settings.ENABLE_RECORD_CHECK_RESULTS = True
    out = {}
    try:
        async with async_session_maker() as db:
            async def one(key, month, feed, oid, npi, day=5):
                rows = [entity_row(oid, npi=npi), filler_row(f"{TAG}{key}")]
                iid = await seed_delivery(db, rows, received_at=datetime(2026, month, day),
                                          feed=feed)
                await run_engine(db, iid)
                _IDS["intakes"].append(iid)
                out[key] = str(iid)

            await one("onc_jul", 7, ONC, OID_A, BAD_LEN_NPI)
            await one("syn_aug", 8, SYN, OID_A, GOOD_NPI)
            await one("onc_sep", 9, ONC, OID_A, BAD_LEN_NPI)
            await one("unt_oct", 10, None, OID_A, BAD_LEN_NPI)
            # day 4: strictly before syn_aug (day 5) so the SYN-only record can never
            # sort into OID_A's sequence on a random uuid tie-break
            await one("syn_only", 8, SYN, OID_SYN_ONLY, BAD_LEN_NPI, day=4)
            await one("untagged", 9, None, OID_UNTAGGED, BAD_LEN_NPI)
    finally:
        settings.ENABLE_RECORD_CHECK_RESULTS = False
    return out


async def _cleanup():
    from sqlalchemy import text

    from app.core.database import async_session_maker

    ids = [str(i) for i in _IDS["intakes"]]
    if not ids:
        return
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
            "delete from tefca_reg_audit_log where action = 'issue_history_read' "
            "and metadata->>'oid' like :oidlike",
        ):
            params = dict(p)
            if ":oidlike" in sql:
                params = {"oidlike": f"AUTH-%-{TAG}"}
            elif ":ids" not in sql:
                params = {}
            await db.execute(text(sql), params)
        await db.commit()


@pytest.fixture(scope="module")
def seeded():
    data = run(_seed())
    yield data
    run(_cleanup())


@pytest.fixture
def conf(monkeypatch):
    from app.core.config import settings

    def set_(viewer=ONC, reviewer="", enabled=True):
        monkeypatch.setattr(settings, "ENABLE_ISSUE_HISTORY", enabled)
        monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_VIEWER", viewer)
        monkeypatch.setattr(settings, "ISSUE_HISTORY_FEEDS_REVIEWER", reviewer)

    set_()
    return set_


def shape(r):
    """Status and body with the per-request correlation id removed."""
    body = r.json()
    body.pop("request_id", None)
    return r.status_code, body


def get(client, oid, role=None, **kw):
    headers = headers_for(role) if role else {}
    return client.get(URL.format(oid), headers=headers, **kw)


def test_viewer_with_one_feed_sees_only_that_feeds_deliveries(client, seeded, conf):
    r = get(client, OID_A, "viewer")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["delivery_id"] for d in body["deliveries"]] == [seeded["onc_jul"], seeded["onc_sep"]]
    text = r.text
    for hidden in ("syn_aug", "unt_oct", "syn_only", "untagged"):
        assert seeded[hidden] not in text
    assert SYN not in text
    assert body["scope_note"] == f"History for the {ONC} feed"
    # no gap text across a hidden delivery: the only gap is between the two visible ones
    gaps = [g for g in body["gaps"] if g["rule_id"] == "NPI-002"]
    assert len(gaps) == 1
    assert (gaps[0]["from_delivery_id"], gaps[0]["to_delivery_id"]) == \
        (seeded["onc_jul"], seeded["onc_sep"])
    for key in ("hidden", "withheld", "total", "count"):
        assert key not in body
    # the visible Sep failure is NOT recurring through the hidden August pass
    sep = next(d for d in body["deliveries"] if d["delivery_id"] == seeded["onc_sep"])
    npi2 = next(l for l in sep["lanes"] if l["rule_id"] == "NPI-002")
    assert npi2["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    # redaction for a real viewer
    from app.tefca_registry.rce import issue_history_core as core
    assert core.forbidden_keys_present(body) == []


def test_hidden_only_untagged_and_unknown_oids_are_indistinguishable(client, seeded, conf):
    answers = {oid: get(client, oid, "viewer")
               for oid in (OID_SYN_ONLY, OID_UNTAGGED, OID_UNKNOWN)}
    shapes = {(s, json.dumps(bd, sort_keys=True)) for s, bd in map(shape, answers.values())}
    assert len(shapes) == 1, shapes
    assert next(iter(shapes))[0] == 404


def test_hidden_and_unknown_answer_in_the_same_timing_class(client, seeded, conf):
    def median_ms(oid):
        get(client, oid, "viewer")
        samples = []
        for _ in range(15):
            t = time.perf_counter()
            get(client, oid, "viewer")
            samples.append((time.perf_counter() - t) * 1000)
        return statistics.median(samples)

    hidden, unknown = median_ms(OID_SYN_ONLY), median_ms(OID_UNKNOWN)
    # same class: neither is more than 3x the other (plus 40 ms of jitter allowance)
    assert hidden < 3 * unknown + 40 and unknown < 3 * hidden + 40, (hidden, unknown)


def test_viewer_with_no_configured_feed_gets_404_for_every_oid(client, seeded, conf):
    conf(viewer="", reviewer=ONC)        # the reviewer list never applies to a viewer
    for oid in (OID_A, OID_SYN_ONLY, OID_UNTAGGED, OID_UNKNOWN):
        r = get(client, oid, "viewer")
        assert shape(r) == (404, {"error": "NOT_FOUND", "code": "NOT_FOUND"}), oid


def test_reviewer_with_a_second_feed_sees_both_but_never_untagged(client, seeded, conf):
    conf(viewer=ONC, reviewer=SYN)
    r = get(client, OID_A, "reviewer")
    assert r.status_code == 200
    ids = [d["delivery_id"] for d in r.json()["deliveries"]]
    assert ids == [seeded["onc_jul"], seeded["syn_aug"], seeded["onc_sep"]]
    assert seeded["unt_oct"] not in r.text
    first = r.json()["deliveries"][0]
    npi2 = next(l for l in first["lanes"] if l["rule_id"] == "NPI-002")
    assert npi2["finding"]["original_value"]           # reviewers see values
    assert get(client, OID_SYN_ONLY, "reviewer").status_code == 200
    assert get(client, OID_UNTAGGED, "reviewer").status_code == 404
    # the same account sees less as a viewer
    assert get(client, OID_SYN_ONLY, "viewer").status_code == 404


def test_roles_above_reviewer_follow_the_reviewer_list(client, seeded, conf):
    conf(viewer=ONC, reviewer=SYN)
    for role in ("admin",):
        assert get(client, OID_SYN_ONLY, role).status_code == 200
        assert get(client, OID_UNTAGGED, role).status_code == 404


def test_below_viewer_and_unauthenticated_are_refused_by_the_framework(client, seeded, conf):
    assert get(client, OID_A).status_code in (401, 403)
    assert get(client, OID_A, "nobody").status_code == 403
    r = client.get(URL.format(OID_A), headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code in (401, 403)


def test_flag_off_is_404_even_for_an_authorized_viewer(client, seeded, conf):
    conf(enabled=False)
    assert get(client, OID_A, "viewer").status_code == 404


def test_non_get_is_405_for_an_authenticated_user(client, seeded, conf):
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)(URL.format(OID_A), headers=headers_for("admin"))
        assert r.status_code == 405, method


def test_each_read_writes_one_audit_row_with_the_real_user_and_role(client, seeded, conf):
    from sqlalchemy import func, select, text

    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    async def count_and_last():
        async with async_session_maker() as db:
            n = (await db.execute(text(
                "select count(*) from tefca_reg_audit_log where action='issue_history_read' "
                "and metadata->>'oid' = :o"), {"o": OID_A})).scalar()
            last = (await db.execute(
                select(reg.TefcaRegAuditLog).where(
                    reg.TefcaRegAuditLog.action == "issue_history_read",
                    reg.TefcaRegAuditLog.metadata_["oid"].as_string() == OID_A)
                .order_by(reg.TefcaRegAuditLog.created_at.desc()).limit(1))).scalar()
            return n, (last.metadata_, last.actor_email) if last else None

    before, _ = run(count_and_last())
    assert get(client, OID_A, "viewer").status_code == 200
    after, last = run(count_and_last())
    assert after == before + 1
    meta, email = last
    assert meta == {"oid": OID_A, "role": "viewer", "visible_deliveries": 2}
    assert email.startswith("lane-a-viewer-")
    assert not any(v in json.dumps(meta) for v in ("12345", seeded["onc_jul"]))
