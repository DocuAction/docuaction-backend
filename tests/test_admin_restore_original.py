"""DEF-004 governed original-artifact restoration — the DEV-only, admin-only,
feature-flagged governed restore of a delivery's preserved original from a
pre-staged private blob.

WHY THIS EXISTS
────────────────
`preserve_original_cmd.py`'s CLI restore needs a live DATABASE_URL, and on
App Service that value is a Key Vault reference resolvable only INSIDE the
running container — an operator's own machine cannot use it without
materializing the secret (a real exposure) or SSH-ing into a container image
that carries no SSH server. `admin_restore_original.py` runs the restore
inside the app itself instead, reusing the app's own resolved DATABASE_URL
and managed identity, reachable only through
`admin_restore_routes.py`'s route — which does not exist in the running
application AT ALL unless BOTH `ENVIRONMENT=development` and
`ENABLE_DEV_RESTORE_ORIGINAL=true` were set before the app started.

WHAT IS TESTED HERE, AND HOW
───────────────────────────────
- Route registration is proven with the SAME technique the codebase already
  uses for the dev-only demo router (`test_rbac_roles.py`'s
  `test_demo_router_is_absent_in_production`): a source-position pin AND a
  live reload of the router module under monkeypatched settings, checked for
  an actual route count — not by hitting a live client under whatever
  ENVIRONMENT the CI process happens to have.
- The admin-role requirement is pinned the same way (source position of the
  `require_role("admin")` dependency).
- Every other requirement (source allowlist, hash mismatch, write-once,
  idempotency, audit count, immutable intake row, reconciliation after a
  simulated restart) is proven by calling the SERVICE function directly
  against a real disposable database and a real (local-backend, real-code)
  artifact store — the same philosophy `test_def004_durable_originals.py`
  already uses, and not a mock of either.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.core.storage.artifact_store import (ArtifactNotFound,
                                             LocalFilesystemArtifactStore)
from app.tefca_registry.rce import admin_restore_original as aro
from app.tefca_registry.rce import intake as intake_mod
from app.tefca_registry.rce import repository as repo
from app.tefca_registry.rce.intake import ingest_delivery
from app.tefca_registry.rce.preserve_original_cmd import RestoreRefused

pytestmark = pytest.mark.asyncio

RAW = b"id|name|NPI\r\n1|SYN ADMIN-RESTORE ALPHA|1234567893\r\n2|SYN ADMIN-RESTORE BETA|1234567804\r\n"
SHA = hashlib.sha256(RAW).hexdigest()
STAGED_NAME = f"{aro.STAGING_PREFIX}onc-snapshot-syn.csv"


class FakeDurableStore(LocalFilesystemArtifactStore):
    """The real local store, presenting as a durable backend. `backend` is
    the only thing the code under test branches on — `read_staged_blob` is
    inherited unchanged from the real class, so staging reads are exercised
    for real against a real filesystem, not mocked."""

    backend = "test_durable"


class _FakeJob:
    """The one attribute `admin_restore_original_from_staged_blob` reads off
    a delivery job — a stand-in so these tests do not need a real
    `RceDeliveryJob` row (the route, not this service function, owns job
    lookup)."""

    def __init__(self, job_id, intake_id):
        self.id = job_id
        self.source_intake_id = intake_id


@pytest.fixture(autouse=True)
def dev_environment(monkeypatch):
    """Every test in this file exercises DEV-only business logic; the one
    test proving the environment check itself (`test_dev_environment_check_
    is_enforced_even_when_called_directly`) overrides this locally to
    "production" for its own duration."""
    import app.core.config as config_mod

    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "development")


@pytest.fixture
def durable_store(tmp_path, monkeypatch):
    store = FakeDurableStore(str(tmp_path / "durable"))
    monkeypatch.setattr(intake_mod, "durable_artifact_store", lambda: store)
    return store


@pytest.fixture
def staging_area(tmp_path, durable_store):
    """Stage RAW under the approved prefix inside the SAME store's root —
    the exact shape `read_staged_blob` reads from."""
    staged_dir = os.path.join(durable_store.root, aro.STAGING_PREFIX)
    os.makedirs(staged_dir, exist_ok=True)
    path = os.path.join(durable_store.root, STAGED_NAME)
    with open(path, "wb") as fh:
        fh.write(RAW)
    return STAGED_NAME


@pytest.fixture
def local_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    return tmp_path / "uploads"


@pytest.fixture
async def db(db_required):
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]),
                                 poolclass=NullPool)
    created: list = []
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.info["created_intakes"] = created
        yield session
        await session.rollback()
        for intake_id in created:
            await session.execute(text(
                "DELETE FROM rce_source_records WHERE source_intake_id = :i"),
                {"i": intake_id})
            await session.execute(text(
                "DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake_id})
        await session.execute(text(
            "DELETE FROM tefca_reg_audit_log WHERE action = 'original_preserved' "
            "AND metadata->>'sha256' = :s"), {"s": SHA})
        await session.commit()
    await engine.dispose()


async def _ingest_without_durable(db, monkeypatch) -> uuid.UUID:
    """Ingest with NO durable backend visible (the pre-DEF-004 situation
    every real intake predates this feature in) — `ingest_delivery` itself
    now writes the original to whatever durable store IS configured
    (DEF-004), so this must suppress it during the ingest call itself and
    let the surrounding `durable_store` fixture's patch resume immediately
    after, exactly like `test_def004_durable_originals.py`'s own `_ingest`."""
    from app.tefca_registry.rce import intake as _im

    resume = _im.durable_artifact_store
    monkeypatch.setattr(_im, "durable_artifact_store", lambda: None)
    try:
        result = await ingest_delivery(
            db, RAW, filename="syn-admin-restore.csv",
            delivery_label="SYN ADMIN-RESTORE", received_by="SYN")
    finally:
        monkeypatch.setattr(_im, "durable_artifact_store", resume)
    intake_id = uuid.UUID(result["intake_id"])
    db.info["created_intakes"].append(intake_id)
    return intake_id


def _remove_local(path: str) -> None:
    os.chmod(path, 0o666)
    os.remove(path)


async def _simulate_recycle(db, intake_id) -> None:
    """Delete the local original — the exact 'restart discarded it' state
    this feature exists to recover from."""
    intake = await repo.get_intake(db, intake_id)
    _remove_local(intake.storage_path)


# ═══ 1. DEV-only enforcement — source pin + live registration check ═════════

def test_route_is_registered_only_when_both_dev_flags_are_true(monkeypatch):
    """The functional proof: reload the route module under every combination
    of the two flags and count what actually got attached to the router."""
    import app.core.config as config_mod
    import app.tefca_registry.rce.admin_restore_routes as route_mod

    def reload_with(environment, enable_flag):
        monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", environment)
        monkeypatch.setattr(config_mod.settings, "ENABLE_DEV_RESTORE_ORIGINAL", enable_flag)
        return importlib.reload(route_mod)

    try:
        assert len(reload_with("production", True).router.routes) == 0
        assert len(reload_with("development", False).router.routes) == 0
        assert len(reload_with("production", False).router.routes) == 0
        assert len(reload_with("development", True).router.routes) == 1
    finally:
        # Leave the cached module in the default (absent) shape for any
        # later test that imports it, regardless of where this test ended.
        reload_with("production", False)


def test_source_guard_wraps_both_conditions_before_the_route_decorator():
    """A structural pin, independent of any runtime import: the guard names
    BOTH settings, and the decorator line is textually inside it — the same
    technique test_rbac_roles.py uses for the demo router."""
    import app.tefca_registry.rce.admin_restore_routes as route_mod

    source = inspect.getsource(route_mod)
    marker = "if settings.is_development and settings.ENABLE_DEV_RESTORE_ORIGINAL:"
    assert marker in source, (
        "the admin restore route's dual DEV-only guard has been removed, "
        "weakened or renamed")
    guard_at = source.index(marker)
    route_at = source.index('"/restore-original"')
    assert guard_at < route_at, (
        "/restore-original is no longer inside the dual dev-only guard — "
        "it could be registered outside DEV or without the feature flag")


def test_route_requires_admin_role():
    import app.tefca_registry.rce.admin_restore_routes as route_mod

    source = inspect.getsource(route_mod)
    assert 'require_role("admin")' in source, (
        "the restore route no longer requires the admin role")


# ═══ 2. source allowlist ═════════════════════════════════════════════════════

@pytest.mark.parametrize("bad_name", [
    "onc-snapshot-syn.csv",                       # missing the prefix entirely
    "../operator-restore-staging/x.csv",          # traversal before the prefix
    "operator-restore-staging/../secrets.csv",    # traversal after the prefix
    "operator-restore-staging/",                  # empty filename
    "operator-restore-staging/../../etc/passwd",  # classic traversal
    "/operator-restore-staging/x.csv",             # leading slash
    "OPERATOR-RESTORE-STAGING/x.csv",             # wrong case, not an alias
    "operator-restore-staging/x/y.csv",           # nested path, not a flat name
    "operator-restore-staging/x csv",             # disallowed character (space)
    "report-artifacts/x.csv",                     # a DIFFERENT, real container path
    "",
])
def test_staged_blob_name_allowlist_rejects_everything_but_the_exact_prefix(bad_name):
    with pytest.raises(RestoreRefused):
        aro.validate_staged_blob_name(bad_name)


def test_staged_blob_name_allowlist_accepts_the_exact_shape():
    assert aro.validate_staged_blob_name(STAGED_NAME) == STAGED_NAME
    assert aro.validate_staged_blob_name(
        "operator-restore-staging/a.b_c-9.csv") == "operator-restore-staging/a.b_c-9.csv"


async def test_a_disallowed_name_is_refused_before_any_store_read(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    """The allowlist gate must fire BEFORE `read_staged_blob` is ever called
    — proven by pointing at a name that, if read, would return real bytes
    from elsewhere in the same store root, and confirming no restore happens."""
    intake_id = await _ingest_without_durable(db, monkeypatch)
    job = _FakeJob(uuid.uuid4(), intake_id)
    with pytest.raises(RestoreRefused, match="not a permitted staging blob name"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job, staged_blob_name="report-artifacts/../" + STAGED_NAME,
            expected_size_bytes=len(RAW), expected_sha256=SHA, actor="admin@example.test")
    assert durable_store.versions(intake_mod.durable_original_key(SHA)) == []


# ═══ 3. hash / size mismatch ══════════════════════════════════════════════════

async def test_size_mismatch_against_caller_claim_refuses_and_writes_nothing(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    intake_id = await _ingest_without_durable(db, monkeypatch)
    job = _FakeJob(uuid.uuid4(), intake_id)
    with pytest.raises(RestoreRefused, match="not the expected"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job, staged_blob_name=staging_area,
            expected_size_bytes=len(RAW) + 1, expected_sha256=SHA,
            actor="admin@example.test")
    assert durable_store.versions(intake_mod.durable_original_key(SHA)) == []


async def test_sha_mismatch_against_caller_claim_refuses_and_writes_nothing(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    intake_id = await _ingest_without_durable(db, monkeypatch)
    job = _FakeJob(uuid.uuid4(), intake_id)
    wrong_sha = "0" * 64
    with pytest.raises(RestoreRefused, match="does not equal the expected SHA-256"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job, staged_blob_name=staging_area,
            expected_size_bytes=len(RAW), expected_sha256=wrong_sha,
            actor="admin@example.test")
    assert durable_store.versions(intake_mod.durable_original_key(SHA)) == []


async def test_hash_matching_caller_claim_but_not_the_intakes_own_record_refuses(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    """The staged bytes and the caller's claim agree with EACH OTHER but not
    with what the intake actually recorded at arrival — the shared restore
    core's own second, redundant check must still fire."""
    other_raw = b"completely different synthetic content, still 10-digit-safe"
    other_sha = hashlib.sha256(other_raw).hexdigest()
    other_name = f"{aro.STAGING_PREFIX}other.csv"
    with open(os.path.join(durable_store.root, other_name), "wb") as fh:
        fh.write(other_raw)

    intake_id = await _ingest_without_durable(db, monkeypatch)  # recorded sha is SHA, not other_sha
    job = _FakeJob(uuid.uuid4(), intake_id)
    with pytest.raises(RestoreRefused, match="intake's recorded sha256"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job, staged_blob_name=other_name,
            expected_size_bytes=len(other_raw), expected_sha256=other_sha,
            actor="admin@example.test")


async def test_malformed_expected_sha256_is_refused_before_any_read(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    intake_id = await _ingest_without_durable(db, monkeypatch)
    job = _FakeJob(uuid.uuid4(), intake_id)
    with pytest.raises(RestoreRefused, match="64 hex characters"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job, staged_blob_name=staging_area,
            expected_size_bytes=len(RAW), expected_sha256="not-hex",
            actor="admin@example.test")


async def test_missing_staged_blob_is_refused_not_a_stack_trace(
        db, local_uploads, durable_store, monkeypatch):
    intake_id = await _ingest_without_durable(db, monkeypatch)
    job = _FakeJob(uuid.uuid4(), intake_id)
    with pytest.raises(RestoreRefused, match="could not be found"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job,
            staged_blob_name=f"{aro.STAGING_PREFIX}never-staged.csv",
            expected_size_bytes=len(RAW), expected_sha256=SHA,
            actor="admin@example.test")


async def test_delivery_job_with_no_intake_is_refused(db, local_uploads, durable_store):
    job = _FakeJob(uuid.uuid4(), None)
    with pytest.raises(RestoreRefused, match="no recorded intake"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job, staged_blob_name=STAGED_NAME,
            expected_size_bytes=len(RAW), expected_sha256=SHA,
            actor="admin@example.test")


async def test_dev_environment_check_is_enforced_even_when_called_directly(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    import app.core.config as config_mod

    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "production")
    intake_id = await _ingest_without_durable(db, monkeypatch)
    job = _FakeJob(uuid.uuid4(), intake_id)
    with pytest.raises(RestoreRefused, match="only available in a development environment"):
        await aro.admin_restore_original_from_staged_blob(
            db, delivery_job=job, staged_blob_name=staging_area,
            expected_size_bytes=len(RAW), expected_sha256=SHA,
            actor="admin@example.test")


# ═══ 4. write-once, idempotency, audit count, immutable intake row ═════════

async def test_successful_restore_is_write_once_audited_once_and_never_touches_the_intake_row(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    intake_id = await _ingest_without_durable(db, monkeypatch)
    await _simulate_recycle(db, intake_id)  # the local original is gone
    assert (await repo.verify_stored_file(db, intake_id))["checked"] is False

    job = _FakeJob(uuid.uuid4(), intake_id)
    result = await aro.admin_restore_original_from_staged_blob(
        db, delivery_job=job, staged_blob_name=staging_area,
        expected_size_bytes=len(RAW), expected_sha256=SHA, actor="admin@example.test")

    assert result["restored"] is True
    assert result["already_restored"] is False
    assert result["sha256"] == SHA
    assert result["delivery_job_id"] == str(job.id)

    # write-once: exactly one version under the content-addressed key.
    versions = durable_store.versions(intake_mod.durable_original_key(SHA))
    assert len(versions) == 1

    # exactly one append-only audit event.
    rows = (await db.execute(text(
        "SELECT actor_email, metadata FROM tefca_reg_audit_log "
        "WHERE action = 'original_preserved' AND metadata->>'sha256' = :s"),
        {"s": SHA})).all()
    assert len(rows) == 1
    assert rows[0][0] == "admin@example.test"
    assert rows[0][1]["provenance"] == "admin endpoint restore from pre-staged DEV blob"

    # the intake row itself was never UPDATEd (Area 1 immutability) — the
    # durable copy is found through the content-addressed key, not a pointer
    # written onto the intake.
    intake_after = await repo.get_intake(db, intake_id)
    assert "durable_original" not in (intake_after.source_metadata or {})

    # reconciliation now PASSES through the durable store, with no local
    # file at all — the "survives a restart" proof.
    check = await repo.verify_stored_file(db, intake_id)
    assert check["checked"] and check["intact"]
    assert check["verified_by"] == "durable_artifact_record"


async def test_repeat_restore_is_idempotent_no_second_write_no_second_audit_event(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    intake_id = await _ingest_without_durable(db, monkeypatch)
    await _simulate_recycle(db, intake_id)
    job = _FakeJob(uuid.uuid4(), intake_id)

    first = await aro.admin_restore_original_from_staged_blob(
        db, delivery_job=job, staged_blob_name=staging_area,
        expected_size_bytes=len(RAW), expected_sha256=SHA, actor="admin@example.test")
    assert first["already_restored"] is False

    again = await aro.admin_restore_original_from_staged_blob(
        db, delivery_job=job, staged_blob_name=staging_area,
        expected_size_bytes=len(RAW), expected_sha256=SHA, actor="admin@example.test")
    assert again["restored"] is True
    assert again["already_restored"] is True

    assert len(durable_store.versions(intake_mod.durable_original_key(SHA))) == 1
    rows_after = (await db.execute(text(
        "SELECT count(*) FROM tefca_reg_audit_log "
        "WHERE action = 'original_preserved' AND metadata->>'sha256' = :s"),
        {"s": SHA})).scalar()
    assert rows_after == 1, "a repeat restore must not write a second audit event"


# ═══ 5. reconciliation verification survives a simulated restart ═══════════

async def test_reconciliation_control_passes_after_restore_and_a_further_recycle(
        db, local_uploads, durable_store, staging_area, monkeypatch):
    """The durable copy — not the local file — is what a subsequent restart
    finds: remove the local file (already done before the restore, standing
    in for the pre-restore recycle) and confirm a SECOND, independent
    verification call (simulating a later app restart re-checking
    reconciliation) still PASSES with no local file present at all."""
    intake_id = await _ingest_without_durable(db, monkeypatch)
    await _simulate_recycle(db, intake_id)
    job = _FakeJob(uuid.uuid4(), intake_id)

    await aro.admin_restore_original_from_staged_blob(
        db, delivery_job=job, staged_blob_name=staging_area,
        expected_size_bytes=len(RAW), expected_sha256=SHA, actor="admin@example.test")

    # a later, independent check (standing in for the NEXT app restart's own
    # reconciliation run) still finds and verifies the durable copy.
    later_check = await repo.verify_stored_file(db, intake_id, deep=True)
    assert later_check["checked"] and later_check["intact"]
    assert later_check["verified_by"] == "durable_artifact_rehash"


# ═══ 6. sanitized-response hygiene (what the route is allowed to return) ═══

def test_route_response_shape_excludes_sensitive_fields():
    """A structural pin on the route module's response dict literal: it must
    never key on anything that could carry a name, address, NPI, database
    detail or secret. The response is built from a fixed, reviewed set of
    keys — this asserts that set has not grown to include a forbidden one."""
    import app.tefca_registry.rce.admin_restore_routes as route_mod

    source = inspect.getsource(route_mod)
    forbidden = ("npi", "database_url", "storage_key", "sas_token",
                "password", "secret", "traceback", "stack")
    # Only scan the response-construction region, not the whole file (whose
    # docstrings legitimately discuss these words to explain why they are
    # excluded).
    start = source.index("return {")
    end = source.find("\n\n", start)
    if end == -1:
        end = len(source)
    body = source[start:end].lower()
    for word in forbidden:
        assert word not in body, f"the response body references {word!r}"
