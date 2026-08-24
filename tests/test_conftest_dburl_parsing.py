"""The test-harness database probe must decode percent-encoded credentials.

REGRESSION GUARD - 2026-08-24
-----------------------------
tests/conftest.py::_database_reachable() parsed DATABASE_URL with urlparse and
passed parsed.username / parsed.password straight to asyncpg. urlparse does not
percent-decode userinfo, so a credential containing any URL-reserved character
arrived still encoded, authentication failed, DB_AVAILABLE became False, and
roughly 41 database-backed tests skipped with "No database reachable" - against
a database that was reachable.

That is the dangerous shape: the suite reports green while silently covering
less. SQLAlchemy decodes correctly, so the application itself was fine and only
the probe misread the URL, which is why it went unnoticed for so long. It
surfaced when the DEV runtime credential was rotated to one containing '+'.

Every password in this file is synthetic.
"""
import pathlib
import re
from urllib.parse import quote, unquote, urlparse

import pytest

SYNTHETIC = [
    "p+w",            # '+' -> %2B
    "p@w",            # '@' -> %40, also the userinfo delimiter
    "a/b",            # '/' -> %2F, also the path delimiter
    "a:b",            # ':' -> %3A, also the user/password delimiter
    "a%b",            # '%' -> %25
    "a?b#c",          # query and fragment delimiters
    "Tr0ub4dor&3",    # '&'
    "s p a c e",      # ' ' -> %20
]


def _decode_like_conftest(url: str):
    """The decoding conftest performs, isolated for assertion."""
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    return (unquote(parsed.username or ""),
            unquote(parsed.password or ""),
            unquote((parsed.path or "/").lstrip("/")) or "postgres")


@pytest.mark.parametrize("password", SYNTHETIC)
def test_percent_encoded_password_round_trips(password):
    url = (f"postgresql+asyncpg://{quote('app_user', safe='')}:"
           f"{quote(password, safe='')}@db.example.invalid:5432/postgres")
    user, decoded, database = _decode_like_conftest(url)
    assert user == "app_user"
    assert decoded == password, "probe would authenticate with the encoded form"
    assert database == "postgres"


def test_raw_urlparse_is_insufficient():
    """Pin the actual defect, so nobody 'simplifies' the unquote away."""
    password = "p+w@rd"
    url = (f"postgresql+asyncpg://app_user:{quote(password, safe='')}"
           f"@db.example.invalid:5432/postgres")
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
    assert parsed.password != password, "if this fails the URL was not encoded"
    assert unquote(parsed.password) == password


def test_conftest_probe_decodes_credentials():
    """The shipped helper, not a copy of it, must do the decoding.

    Read from disk rather than imported: tests/ is not a package, so
    `import tests.conftest` fails, and importing conftest directly would
    re-execute its module-level database probe.
    """
    source = pathlib.Path(__file__).with_name("conftest.py").read_text(
        encoding="utf-8", errors="replace")
    match = re.search(r"def _database_reachable\(.*?\n(?=\S)", source, re.S)
    assert match, "could not locate _database_reachable in conftest.py"
    probe = match.group(0)
    assert "unquote(" in probe, (
        "_database_reachable must percent-decode credentials before handing "
        "them to asyncpg, or DB-backed tests silently skip")
    assert probe.count("unquote(") >= 3, "user, password and database all decode"
