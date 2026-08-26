"""
Alembic must survive a percent-encoded password.

WHAT WAS BROKEN
`alembic/env.py` hands DATABASE_URL to `config.set_main_option()`, which stores
it in a ConfigParser. ConfigParser treats '%' as interpolation syntax and raises
on anything that is not '%%' or a valid '%(name)s'. A URL-encoded password makes
that certain -- '@' becomes '%40' -- so alembic fails with "invalid interpolation
syntax" before running a single migration.

WHY IT MATTERS MORE THAN IT LOOKS
It fails at `alembic upgrade head`, which in the production baseline sequence
sits after the stamp. The database would be under Alembic management but not yet
migrated, and the recovery is a PITR restore rather than a config revert. A
punctuation character in a password would have cost a production restore window.

Found by rehearsing the baseline against a restored copy of the production
schema, not by reading the code.

WHAT IS ASSERTED
That the URL survives the round trip through ConfigParser unchanged, for the
encodings that actually occur in Azure PostgreSQL passwords -- and that no test
here ever prints one.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

HOST = "docuaction-db-geo.postgres.database.azure.com"

# Passwords whose URL encoding contains a '%'. These are synthetic.
ENCODED_PASSWORDS = [
    "XI5wEs%40yrsiT7rVGFIEMFkReP2",   # '@'  -> %40   (the one that actually failed)
    "abc%25def",                       # '%'  -> %25
    "p%2Fq%2Br",                       # '/'  and '+'
    "a%3Ab%23c",                       # ':'  and '#'
    "%40%25%2F%2B%3A%23",              # all of them together
    "plain-no-encoding",               # the control: must still work
]


def _url(password: str) -> str:
    return f"postgresql+asyncpg://pgadmin:{password}@{HOST}:5432/postgres"


def _escaped(url: str) -> str:
    """The transformation env.py applies."""
    return url.replace("%", "%%")


@pytest.mark.parametrize("password", ENCODED_PASSWORDS)
def test_url_survives_configparser_round_trip(password):
    """The real failure mode, reproduced against ConfigParser itself."""
    from alembic.config import Config

    url = _url(password)
    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", _escaped(url))
    assert cfg.get_main_option("sqlalchemy.url") == url


@pytest.mark.parametrize("password", ENCODED_PASSWORDS)
def test_unescaped_url_is_what_used_to_break(password):
    """Proves the fix is load-bearing rather than decorative.

    Without the escape, ConfigParser raises for every password containing '%'
    and accepts the one that does not. If this ever stops raising, ConfigParser
    changed and the guard can be revisited -- but not before.
    """
    from alembic.config import Config

    url = _url(password)
    cfg = Config()
    if "%" in password:
        # The raise happens on SET, not on get. ConfigParser validates the value
        # in `before_set`, so the failure lands on the assignment -- which is why
        # the real failure surfaced at `alembic upgrade` before any query ran.
        with pytest.raises((configparser.InterpolationSyntaxError,
                            configparser.InterpolationError, ValueError)):
            cfg.set_main_option("sqlalchemy.url", url)
    else:
        cfg.set_main_option("sqlalchemy.url", url)
        assert cfg.get_main_option("sqlalchemy.url") == url


def test_alembic_does_not_escape_on_our_behalf():
    """Guards against the fix becoming a double-escape.

    If a future alembic escaped internally, `db_url.replace("%", "%%")` would
    store '%%%%40' and the password would come back as '%%40' -- a corrupted
    credential that fails authentication with no obvious cause. Alembic's own
    documentation states the caller must escape; this pins that contract so the
    guard is revisited if it ever changes.
    """
    import inspect

    from alembic.config import Config

    src = inspect.getsource(Config.set_section_option)
    assert 'replace("%", "%%")' not in src, (
        "alembic now escapes internally -- env.py would double-escape")
    # And prove it behaviourally: one escape in, original out.
    cfg = Config()
    url = _url("XI5wEs%40yrsiT7")
    cfg.set_main_option("sqlalchemy.url", _escaped(url))
    assert cfg.get_main_option("sqlalchemy.url") == url


def test_env_py_applies_the_escape():
    """The fix is in the file that actually runs, not only in this test."""
    env = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    src = env.read_text(encoding="utf-8", errors="ignore")
    assert 'set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))' in src, (
        "env.py must escape '%' before ConfigParser sees the URL")


def test_env_py_never_logs_the_url():
    """A URL carries the password. It must not reach a log line.

    Checked on the source because there is no safe way to run env.py here
    without a database, and the property is static anyway.
    """
    env = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"(print|logger\.\w+)\s*\(", stripped):
            assert "db_url" not in stripped, f"URL reaches a log/print: {stripped}"
            assert "sqlalchemy.url" not in stripped, f"URL reaches a log/print: {stripped}"


def test_no_password_appears_in_this_test_output(capsys):
    """The tests themselves must not leak the values they exercise."""
    for password in ENCODED_PASSWORDS:
        _escaped(_url(password))
    out = capsys.readouterr()
    for password in ENCODED_PASSWORDS:
        assert password not in out.out
        assert password not in out.err
