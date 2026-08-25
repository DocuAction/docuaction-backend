"""
The database engine log line must never carry any part of the credential.

WHAT WAS WRONG
`_get_engine()` logged `db_url[:35]`. A normalized URL starts with
`postgresql+asyncpg://` — 21 characters. Add a short username and a colon and
the slice is already inside the password. With the production username the
prefix `postgresql+asyncpg://pgadmin:` is exactly 29 characters, so the last six
characters of that slice were the first six characters of the database password.

It was logged at INFO on every engine creation, and the production site has
APPLICATIONINSIGHTS_CONNECTION_STRING set, so those characters were shipped to a
telemetry store read by a wider audience than the vault. A partial secret is
still a secret — it shortens the search space for whoever reads it.

WHAT THESE TESTS PIN
Not "the password is truncated to a safe length" — there is no safe length.
Nothing derived from the credential may appear at all. The tests use a sentinel
password and assert that no prefix of it, down to a single character, survives
into the log record, across usernames of every length. A username-length change
must never be able to reintroduce this.
"""

from __future__ import annotations

import logging

import pytest

from app.core.database import _safe_dsn_description

pytestmark = pytest.mark.regression

SENTINEL = "SuperSecretPassw0rd"
USER = "pgadmin"
HOST = "docuaction-db-geo.postgres.database.azure.com"
DB = "postgres"
URL = f"postgresql+asyncpg://{USER}:{SENTINEL}@{HOST}:5432/{DB}"


def test_description_contains_no_part_of_the_password():
    """Every prefix of the sentinel, down to one character, must be absent."""
    out = _safe_dsn_description(URL)
    for length in range(1, len(SENTINEL) + 1):
        assert SENTINEL[:length] not in out, (
            f"the first {length} characters of the password leaked into {out!r}")


def test_description_contains_no_username():
    """The username is not a secret, but it is half of a credential pair.

    Logging it tells a reader exactly which account to attack, and it costs
    nothing to omit.
    """
    assert USER not in _safe_dsn_description(URL)


def test_description_is_not_the_url():
    out = _safe_dsn_description(URL)
    assert "://" not in out
    assert "@" not in out
    assert URL not in out


def test_description_keeps_what_operations_actually_needs():
    """Redaction that removes the operational value would just get reverted."""
    out = _safe_dsn_description(URL)
    assert HOST in out
    assert DB in out
    assert "5432" in out


@pytest.mark.parametrize("username", ["a", "ab", "pg", "pgadmin", "docuaction_app",
                                      "a_very_long_service_account_name_indeed"])
def test_no_username_length_reintroduces_the_leak(username):
    """The original bug was a fixed-width slice meeting a variable-width prefix.

    A shorter username pushed the cut further into the password; a longer one
    hid the bug entirely. Whether a secret leaks must not depend on how long
    somebody's account name happens to be.
    """
    url = f"postgresql+asyncpg://{username}:{SENTINEL}@{HOST}:5432/{DB}"
    out = _safe_dsn_description(url)
    assert SENTINEL[:1] not in out or SENTINEL[:1] not in SENTINEL, "sentinel prefix leaked"
    assert SENTINEL not in out
    for length in range(1, len(SENTINEL) + 1):
        assert SENTINEL[:length] not in out


def test_a_malformed_url_does_not_fall_back_to_printing_it():
    """The failure path is where redaction usually gets lost."""
    for bad in ("", "not a url", "://", "postgresql+asyncpg://", ":::::"):
        out = _safe_dsn_description(bad)
        assert bad not in out or bad == "", f"raw input echoed for {bad!r}"
        assert SENTINEL not in out


def test_engine_creation_logs_no_credential(monkeypatch, caplog):
    """End to end: the actual log record emitted by _get_engine().

    Asserted on the emitted record rather than on the helper, because the defect
    was in the call site's format string, not in any helper — there was none.
    """
    import app.core.database as database

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setattr(database, "create_async_engine",
                        lambda *a, **k: object())

    with caplog.at_level(logging.INFO, logger="docuaction.database"):
        database._get_engine()

    emitted = " ".join(r.getMessage() for r in caplog.records)
    assert emitted, "the engine creation log line disappeared entirely"
    assert SENTINEL not in emitted
    for length in range(1, len(SENTINEL) + 1):
        assert SENTINEL[:length] not in emitted
    assert USER not in emitted
    assert HOST in emitted, "the line must still say which server was reached"

    monkeypatch.setattr(database, "_engine", None)
