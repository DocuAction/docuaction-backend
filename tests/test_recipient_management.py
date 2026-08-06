"""Recipient management (Task 3.5).

These cover the parts that hold without a live Postgres: the endpoint contract,
input validation, and — most importantly — that an unreachable store is
reported rather than silently treated as an empty distribution list.
"""
import pytest

from app.main import app
from app.bulletin_intelligence import bulletin_store
from app.bulletin_intelligence.routes import _EMAIL_RE

# Uses the shared `client` fixture from conftest (which sets ALLOWED_HOSTS and
# skips lifespan startup); no module-level client here.
BASE = "/api/v1/bulletin/recipients"


def _paths():
    return set(app.openapi()["paths"])


# ── Contract ──────────────────────────────────────────────────────────────────

def test_the_endpoints_exist_with_the_expected_methods():
    paths = app.openapi()["paths"]
    assert BASE in paths
    assert {"get", "post"} <= set(paths[BASE])
    assert "delete" in paths[f"{BASE}/{{email}}"]


def test_removal_is_a_deactivation_not_a_delete():
    """Who received a federal deliverable is part of the delivery record, so
    the DELETE verb must not translate into a SQL DELETE."""
    import inspect
    import re
    src = inspect.getsource(bulletin_store.deactivate_recipient)
    assert "UPDATE bulletin_recipients" in src
    assert "SET active = FALSE" in src
    # Match the SQL statement, not the word — the docstring says "Never deletes".
    assert not re.search(r"\bDELETE\s+FROM\b", src, re.I)


def test_the_table_is_created_on_startup():
    ddl = "\n".join(bulletin_store._DDL)
    assert "CREATE TABLE IF NOT EXISTS bulletin_recipients" in ddl
    # Case-folded uniqueness: A@fcc.gov and a@fcc.gov are one recipient, or the
    # same person is emailed twice.
    assert "LOWER(email)" in ddl


# ── Validation ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("email", [
    "imran@agtbi.com", "first.last@fcc.gov", "a+tag@sub.example.co.uk",
])
def test_valid_addresses_are_accepted_by_the_pattern(email):
    assert _EMAIL_RE.match(email)


@pytest.mark.parametrize("email", [
    "", "no-at-sign", "@fcc.gov", "imran@", "imran@fcc", "two@@fcc.gov",
    "has space@fcc.gov", "a@fcc.gov,b@fcc.gov", "a@fcc.gov;b@fcc.gov",
])
def test_invalid_addresses_are_rejected_by_the_pattern(email):
    """The comma and semicolon cases matter: pasting a whole distribution list
    into one field must not create a single unusable 'recipient'."""
    assert not _EMAIL_RE.match(email)


def test_a_bad_address_is_rejected_before_it_reaches_the_store(client):
    r = client.post(BASE, json={"email": "not-an-email"})
    # 401/403 if auth is on; the point is that it is never accepted.
    assert r.status_code in (400, 401, 403), r.text
    if r.status_code == 400:
        # The app's error handler reshapes detail -> error/code.
        assert "valid email" in r.json()["error"]


# ── Availability ──────────────────────────────────────────────────────────────

def test_an_unreachable_store_is_reported_not_reported_as_empty(client, monkeypatch):
    """`fetch_recipients` returns [] both when the list is empty and when the
    database is down. Serving that as an empty distribution list would read as
    'nobody is subscribed' during an outage.

    The 5xx body is deliberately scrubbed by the app's error handler, so the
    machine-readable code is what this asserts on — not the prose."""
    monkeypatch.setattr(bulletin_store, "store_enabled", lambda: False)
    r = client.get(BASE)
    assert r.status_code in (401, 403, 503), r.text
    if r.status_code == 503:
        assert r.json()["code"] == "SERVICE_UNAVAILABLE"
        assert r.json().get("recipients") is None, "must not return a list at all"


def test_writes_are_rejected_rather_than_dropped_when_the_store_is_down(
        client, monkeypatch):
    monkeypatch.setattr(bulletin_store, "store_enabled", lambda: False)
    r = client.post(BASE, json={"email": "imran@agtbi.com"})
    assert r.status_code in (401, 403, 503), r.text


def test_store_helpers_degrade_without_raising(monkeypatch):
    """Persistence is best-effort everywhere else in this module; these must
    not become the one place a DB outage raises into a request."""
    import asyncio
    monkeypatch.setattr(bulletin_store, "_enabled", False)
    assert asyncio.run(bulletin_store.fetch_recipients("fcc")) == []
    assert asyncio.run(bulletin_store.upsert_recipient({})) == "unavailable"
    assert asyncio.run(bulletin_store.deactivate_recipient("fcc", "a@b.com")) is False


# ── The send path reads this table ────────────────────────────────────────────
# resolve_recipients() decides who a briefing is addressed to. Each case below
# is a way that decision can go wrong and mail the wrong people — or nobody.

import asyncio

from app.bulletin_intelligence import engine


def _agency():
    ag = engine.get_agency("fcc")
    assert ag is not None
    return ag


def test_active_table_entries_win_over_the_config_list(monkeypatch):
    monkeypatch.setattr(bulletin_store, "store_enabled", lambda: True)

    async def _rows(agency_id, active_only=True):
        return [{"email": "ops@fcc.gov"}, {"email": "press@fcc.gov"}]

    monkeypatch.setattr(bulletin_store, "fetch_recipients", _rows)
    to_list, source = asyncio.run(engine.resolve_recipients(_agency()))
    assert to_list == ["ops@fcc.gov", "press@fcc.gov"]
    assert source == "recipients_table"


def test_an_empty_table_falls_back_to_the_config_list(monkeypatch):
    monkeypatch.setattr(bulletin_store, "store_enabled", lambda: True)

    async def _none(agency_id, active_only=True):
        return []

    monkeypatch.setattr(bulletin_store, "fetch_recipients", _none)
    to_list, source = asyncio.run(engine.resolve_recipients(_agency()))
    assert to_list == list(_agency().distribution_list)
    assert source == "agency_config_empty"


def test_an_unreachable_store_falls_back_rather_than_mailing_nobody(monkeypatch):
    """fetch_recipients() returns [] for both "empty" and "database down". If the
    send path trusted that, one outage would silently deliver to no one."""
    monkeypatch.setattr(bulletin_store, "store_enabled", lambda: False)
    to_list, source = asyncio.run(engine.resolve_recipients(_agency()))
    assert to_list == list(_agency().distribution_list)
    assert source == "agency_config_store_down"


def test_a_raising_store_falls_back_rather_than_propagating(monkeypatch):
    monkeypatch.setattr(bulletin_store, "store_enabled", lambda: True)

    async def _boom(agency_id, active_only=True):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(bulletin_store, "fetch_recipients", _boom)
    to_list, source = asyncio.run(engine.resolve_recipients(_agency()))
    assert to_list == list(_agency().distribution_list)
    assert source == "agency_config_store_error"


def test_addresses_differing_only_in_case_are_sent_to_once(monkeypatch):
    """The unique index is case-folded, but a legacy row could predate it. Two
    rows for one person must not become two copies of the same briefing."""
    monkeypatch.setattr(bulletin_store, "store_enabled", lambda: True)

    async def _dupes(agency_id, active_only=True):
        return [{"email": "Ops@fcc.gov"}, {"email": "ops@fcc.gov"}]

    monkeypatch.setattr(bulletin_store, "fetch_recipients", _dupes)
    to_list, _ = asyncio.run(engine.resolve_recipients(_agency()))
    assert to_list == ["Ops@fcc.gov"]
