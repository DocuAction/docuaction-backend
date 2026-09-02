"""alembic/env.py must be able to assume the owner role before any DDL.

docuaction_owner has no LOGIN, so migrations connect as a member and, unless
the role is assumed first, create objects owned by the connecting principal.
That happened to rce_delivery_jobs in Azure DEV on 2026-09-02 and needed a
recorded ALTER OWNER afterwards. These pin the opt-in mechanism:
DB_MIGRATION_ROLE -> asyncpg server_settings.role, applied in the startup
packet, and a no-op when unset.
"""
import io
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(REPO, "alembic", "env.py")
GRANT_MIGRATION = os.path.join(REPO, "alembic", "versions",
                               "20260903_delivery_jobs_grants.py")


def _src(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def test_env_reads_db_migration_role_and_passes_it_as_a_server_setting():
    src = _src(ENV)
    assert 'os.getenv("DB_MIGRATION_ROLE"' in src
    assert 'connect_args["server_settings"] = {"role": migration_role}' in src
    call = src.index("async_engine_from_config(")
    assert "connect_args=connect_args," in src[call:call + 400], (
        "connect_args must be passed to async_engine_from_config")


def test_the_mechanism_is_opt_in():
    """Unset must leave every existing local/CI invocation unchanged."""
    src = _src(ENV)
    assert "connect_args = {}" in src
    assert "if migration_role:" in src


def test_delivery_jobs_grant_migration_asserts_ownership_and_names_its_role():
    src = _src(GRANT_MIGRATION)
    assert 'down_revision = "20260902_delivery_jobs"' in src
    assert 'PRIVILEGES = ("SELECT", "INSERT", "UPDATE")' in src
    assert "if owner != OWNER" in src, "must refuse to grant on a mis-owned table"
    assert "DB_APP_ROLE is not set" in src, "must fail closed without a named role"
