"""DEF-004 — delivery originals survive a container recycle when a durable
artifact backend is configured, fail-closed, and the governed restore command
puts a verified byte-identical copy back without touching processed records.

The durable store double is the real LocalFilesystemArtifactStore pointed at a
test directory with a non-"local" backend name: everything above the store
interface (the code under test) is exercised for real; only Azure itself is
absent, and the Azure backend has its own live suite
(test_azure_artifact_store.py).

Proven here:
  1. ingest with a durable backend records `durable_original` on the intake's
     source_metadata, and the stored bytes ARE the delivered bytes;
  2. after a simulated container recycle (local file removed) the
     "Original delivery file unmodified" evidence still verifies, through the
     store, and says so (`verified_by`), including deep re-hash;
  3. a durable-backend write failure REFUSES the delivery (never silently
     ephemeral) and leaves no intake;
  4. with no durable backend configured, behaviour is exactly pre-DEF-004;
  5. the runner can re-read the original from the durable store by content
     hash alone;
  6. the restore command: refuses on hash mismatch and on a missing durable
     backend, restores fail-closed with a post-upload re-hash, appends ONE
     `original_preserved` audit event, never UPDATEs the intake row, and is
     idempotent.
"""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.core.storage.artifact_store import LocalFilesystemArtifactStore
from app.tefca_registry.rce import intake as intake_mod
from app.tefca_registry.rce import repository as repo
from app.tefca_registry.rce.intake import IntakeError, ingest_delivery
from app.tefca_registry.rce.preserve_original_cmd import (
    RestoreRefused, restore_preserved_original)

pytestmark = pytest.mark.asyncio

RAW = b"id|name|NPI\r\n1|SYN DEF004 ALPHA|1234567893\r\n2|SYN DEF004 BETA|1234567804\r\n"
SHA = hashlib.sha256(RAW).hexdigest()


class FakeDurableStore(LocalFilesystemArtifactStore):
    """The real local store, presenting as a durable backend. `backend` is the
    only thing the code under test branches on."""

    backend = "test_durable"


@pytest.fixture
def durable_store(tmp_path, monkeypatch):
    store = FakeDurableStore(str(tmp_path / "durable"))
    monkeypatch.setattr(intake_mod, "durable_artifact_store", lambda: store)
    return store


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
        session.info["def004_created_intakes"] = created
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


async def _ingest(db) -> uuid.UUID:
    result = await ingest_delivery(
        db, RAW, filename="syn-def004.csv", delivery_label="SYN DEF-004",
        received_by="SYN")
    intake_id = uuid.UUID(result["intake_id"])
    db.info["def004_created_intakes"].append(intake_id)
    return intake_id


def _remove_local(path: str) -> None:
    os.chmod(path, 0o666)   # preserve_original marks it read-only
    os.remove(path)


# ═══ 1-2. durable write at ingest + verification after a recycle ═════════════

async def test_ingest_records_durable_original_and_bytes_match(
        db, local_uploads, durable_store):
    intake_id = await _ingest(db)
    intake = await repo.get_intake(db, intake_id)
    durable = (intake.source_metadata or {}).get("durable_original")
    assert durable, "ingest with a durable backend must record durable_original"
    assert durable["backend"] == "test_durable"
    assert durable["sha256"] == SHA
    assert durable["artifact_key"] == intake_mod.durable_original_key(SHA)
    assert durable_store.get(durable["locator"]) == RAW


async def test_verification_survives_a_container_recycle(
        db, local_uploads, durable_store):
    intake_id = await _ingest(db)
    intake = await repo.get_intake(db, intake_id)

    before = await repo.verify_stored_file(db, intake_id)
    assert before["checked"] and before["intact"]
    assert before["verified_by"] == "local_file_rehash"

    _remove_local(intake.storage_path)          # the simulated recycle

    after = await repo.verify_stored_file(db, intake_id)
    assert after["checked"] is True, after
    assert after["intact"] is True
    assert after["verified_by"] == "durable_artifact_record"
    assert after["storage_backend"] == "test_durable"
    assert after["recomputed_sha256"] == intake.sha256

    deep = await repo.verify_stored_file(db, intake_id, deep=True)
    assert deep["intact"] is True
    assert deep["verified_by"] == "durable_artifact_rehash"


async def test_runner_rereads_the_original_from_the_store_by_hash(
        db, local_uploads, durable_store):
    await _ingest(db)
    assert await intake_mod.read_durable_original(SHA) == RAW
    missing = hashlib.sha256(b"never stored").hexdigest()
    assert await intake_mod.read_durable_original(missing) is None


# ═══ 3-4. fail-closed, and unchanged without a backend ═══════════════════════

async def test_a_durable_write_failure_refuses_the_delivery(
        db, local_uploads, durable_store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic: storage unreachable")

    monkeypatch.setattr(durable_store, "put", boom)
    with pytest.raises(IntakeError, match="NOT accepted"):
        await ingest_delivery(db, RAW, filename="syn-def004.csv", received_by="SYN")
    n = (await db.execute(text(
        "SELECT count(*) FROM rce_source_intakes WHERE sha256 = :s"),
        {"s": SHA})).scalar()
    assert n == 0, "a refused delivery must leave no intake row"


async def test_without_a_durable_backend_behaviour_is_unchanged(
        db, local_uploads):
    intake_id = await _ingest(db)     # default: REPORT_ARTIFACT_BACKEND=local
    intake = await repo.get_intake(db, intake_id)
    assert "durable_original" not in (intake.source_metadata or {})

    _remove_local(intake.storage_path)
    after = await repo.verify_stored_file(db, intake_id)
    assert after["checked"] is False
    assert "stored file not found" in after["reason"]


# ═══ 6. the governed restore ═════════════════════════════════════════════════

async def test_restore_refuses_without_a_durable_backend(db, local_uploads, tmp_path):
    intake_id = await _ingest(db)
    copy = tmp_path / "copy.csv"
    copy.write_bytes(RAW)
    with pytest.raises(RestoreRefused, match="No durable artifact backend"):
        await restore_preserved_original(
            db, intake_id=intake_id, file_path=str(copy), expect_sha=SHA,
            actor="operator@example.test")


async def test_restore_refuses_a_hash_mismatch_and_writes_nothing(
        db, local_uploads, durable_store, tmp_path, monkeypatch):
    # ingest WITHOUT the durable backend, so no durable copy exists yet
    monkeypatch.setattr(intake_mod, "durable_artifact_store", lambda: None)
    intake_id = await _ingest(db)
    monkeypatch.setattr(intake_mod, "durable_artifact_store", lambda: durable_store)

    tampered = tmp_path / "tampered.csv"
    tampered.write_bytes(RAW + b"one extra byte")
    with pytest.raises(RestoreRefused, match="does not equal"):
        await restore_preserved_original(
            db, intake_id=intake_id, file_path=str(tampered), expect_sha=SHA,
            actor="operator@example.test")
    assert durable_store.versions(intake_mod.durable_original_key(SHA)) == []
    n = (await db.execute(text(
        "SELECT count(*) FROM tefca_reg_audit_log "
        "WHERE action = 'original_preserved' AND metadata->>'sha256' = :s"),
        {"s": SHA})).scalar()
    assert n == 0, "a refused restore must leave no audit event"


async def test_restore_puts_verifies_audits_and_is_idempotent(
        db, local_uploads, durable_store, tmp_path, monkeypatch):
    # the DEF-004 situation: ingested pre-durable-config, local copy wiped
    monkeypatch.setattr(intake_mod, "durable_artifact_store", lambda: None)
    intake_id = await _ingest(db)
    intake = await repo.get_intake(db, intake_id)
    _remove_local(intake.storage_path)
    monkeypatch.setattr(intake_mod, "durable_artifact_store", lambda: durable_store)

    assert (await repo.verify_stored_file(db, intake_id))["checked"] is False

    copy = tmp_path / "verified-copy.csv"
    copy.write_bytes(RAW)
    result = await restore_preserved_original(
        db, intake_id=intake_id, file_path=str(copy), expect_sha=SHA.upper(),
        actor="operator@example.test")
    assert result["restored"] is True
    assert result["verification"]["intact"] is True
    assert result["verification"]["verified_by"] == "durable_artifact_rehash"

    # the control now passes through the CONTENT-ADDRESSED key — the intake
    # row was never updated (Area 1 immutability)
    intake_after = await repo.get_intake(db, intake_id)
    assert "durable_original" not in (intake_after.source_metadata or {})
    check = await repo.verify_stored_file(db, intake_id)
    assert check["checked"] and check["intact"]
    assert check["verified_by"] == "durable_artifact_record"

    # exactly one append-only audit event
    rows = (await db.execute(text(
        "SELECT actor_email, metadata FROM tefca_reg_audit_log "
        "WHERE action = 'original_preserved' AND metadata->>'sha256' = :s"),
        {"s": SHA})).all()
    assert len(rows) == 1
    assert rows[0][0] == "operator@example.test"

    # idempotent: a second run deduplicates rather than versions, and writes
    # NO second audit row — "already restored" is reported instead of a
    # duplicate record of an action that already happened.
    again = await restore_preserved_original(
        db, intake_id=intake_id, file_path=str(copy), expect_sha=SHA,
        actor="operator@example.test")
    assert again["restored"] is True
    assert again["already_restored"] is True
    assert again["deduplicated"] is True
    assert len(durable_store.versions(intake_mod.durable_original_key(SHA))) == 1
    rows_after = (await db.execute(text(
        "SELECT count(*) FROM tefca_reg_audit_log "
        "WHERE action = 'original_preserved' AND metadata->>'sha256' = :s"),
        {"s": SHA})).scalar()
    assert rows_after == 1, "a deduplicated repeat must not write a second audit event"
