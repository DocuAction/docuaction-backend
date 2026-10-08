"""Durable original for large licensed uploads: streaming, immutable, fail-closed, explicit when absent."""
from __future__ import annotations

import asyncio
import hashlib
import types
import uuid

import pytest
from azure.core.exceptions import ResourceExistsError

from app.core.storage import original_blob_store as obs


class _Props:
    def __init__(self, size, metadata):
        self.size, self.metadata = size, metadata


class _FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def upload_blob(self, data, *, length=None, overwrite=True, metadata=None, max_concurrency=1,
                    validate_content=False):
        assert not isinstance(data, (bytes, bytearray)), "must stream from a file object, never a bytes copy"
        assert overwrite is False
        if self.name in self.store:
            raise ResourceExistsError("exists")
        body = data.read()
        self.store[self.name] = (body, dict(metadata or {}))
        self.store.setdefault("__calls__", []).append(
            {"length": length, "concurrency": max_concurrency, "validate": validate_content})

    def get_blob_properties(self):
        body, meta = self.store[self.name]
        return _Props(len(body), meta)

    def download_blob(self, max_concurrency=1):
        body, _ = self.store[self.name]
        return types.SimpleNamespace(chunks=lambda: iter([body[i:i + 5] for i in range(0, len(body), 5)]))


class _FakeContainer:
    def __init__(self, store):
        self.store = store

    def get_blob_client(self, name):
        return _FakeBlob(self.store, name)


@pytest.fixture
def azure_on(monkeypatch):
    monkeypatch.setenv(obs.BACKEND_ENV, "azure_blob")
    store = {}
    return store, (lambda: (_FakeContainer(store), "report-artifacts"))


def _file(tmp_path, content=b"HCP_HCE_ID,x\n1,2\n"):
    p = tmp_path / "client-supplied-name.csv"
    p.write_bytes(content)
    return p, hashlib.sha256(content).hexdigest()


def test_unconfigured_is_an_explicit_not_preserved_never_an_implied_success(monkeypatch, tmp_path):
    monkeypatch.delenv(obs.BACKEND_ENV, raising=False)
    p, sha = _file(tmp_path)
    rec = obs.preserve_file(p, sha)
    assert rec["preserved"] is False and "no durable" in rec["reason"]


def test_preserve_streams_records_locator_and_does_not_use_the_client_filename(azure_on, tmp_path):
    store, factory = azure_on
    p, sha = _file(tmp_path)
    rec = obs.preserve_file(p, sha, client_factory=factory)
    assert rec["preserved"] and rec["outcome"] == "written"
    assert rec["locator"] == f"azureblob://report-artifacts/iqvia-original-{sha}/original.csv"
    assert "client-supplied-name" not in str(rec)
    body, meta = store[f"iqvia-original-{sha}/original.csv"]
    assert body == p.read_bytes() and meta["sha256"] == sha and meta["size_bytes"] == str(len(body))
    call = store["__calls__"][0]
    assert call["length"] == len(body) and call["validate"] is True and call["concurrency"] <= 2
    assert rec["end_to_end_sha256_verified"] is False          # stated, not implied


def test_reputting_identical_bytes_is_idempotent(azure_on, tmp_path):
    store, factory = azure_on
    p, sha = _file(tmp_path)
    obs.preserve_file(p, sha, client_factory=factory)
    again = obs.preserve_file(p, sha, client_factory=factory)
    assert again["preserved"] and again["outcome"] == "already_present"
    assert len([k for k in store if k != "__calls__"]) == 1


def test_a_different_file_under_the_same_key_is_refused_never_replaced(azure_on, tmp_path):
    store, factory = azure_on
    p, sha = _file(tmp_path)
    store[f"iqvia-original-{sha}/original.csv"] = (b"other bytes", {"sha256": "0" * 64})
    with pytest.raises(obs.OriginalNotPreserved):
        obs.preserve_file(p, sha, client_factory=factory)
    assert store[f"iqvia-original-{sha}/original.csv"][0] == b"other bytes"


def test_a_size_mismatch_after_write_is_refused(azure_on, tmp_path, monkeypatch):
    store, factory = azure_on
    p, sha = _file(tmp_path)
    monkeypatch.setattr(_FakeBlob, "get_blob_properties", lambda self: _Props(1, {"sha256": sha}))
    with pytest.raises(obs.OriginalNotPreserved, match="size"):
        obs.preserve_file(p, sha, client_factory=factory)


def test_backend_failure_fails_closed_and_does_not_leak_the_sdk_message(azure_on, tmp_path):
    p, sha = _file(tmp_path)

    def boom():
        raise RuntimeError("https://acct.blob.core.windows.net/c?sig=SECRETSAS")
    with pytest.raises(obs.OriginalNotPreserved) as exc:
        obs.preserve_file(p, sha, client_factory=boom)
    assert "SECRETSAS" not in str(exc.value) and "RuntimeError" in str(exc.value)


def test_blob_name_requires_a_real_sha256():
    for bad in ("", "abc", "../x", "Z" * 64):
        with pytest.raises(obs.OriginalStoreError):
            obs.blob_name(bad)


def test_verify_original_is_true_only_for_matching_bytes(azure_on, tmp_path):
    store, factory = azure_on
    p, sha = _file(tmp_path)
    obs.preserve_file(p, sha, client_factory=factory)
    assert obs.verify_original(sha, client_factory=factory) is True
    store[f"iqvia-original-{sha}/original.csv"] = (b"tampered", {"sha256": sha})
    assert obs.verify_original(sha, client_factory=factory) is False


# -- the hook in the import job ------------------------------------------------------------------

class _Snap:
    def __init__(self, sha, meta=None):
        self.sha256, self.metadata_ = sha, meta or {}


class _Db:
    def __init__(self, snap):
        self.snap, self.commits = snap, 0

    async def get(self, model, key):
        return self.snap

    async def commit(self):
        self.commits += 1


class _AsyncNull:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _job(path):
    return types.SimpleNamespace(id=uuid.uuid4(), snapshot_id=uuid.uuid4(), file_path=str(path))


def test_job_records_not_preserved_when_unconfigured(monkeypatch, tmp_path):
    from app.tefca_registry.rce import iqvia_routes as r

    monkeypatch.delenv(obs.BACKEND_ENV, raising=False)
    p, sha = _file(tmp_path)
    db = _Db(_Snap(sha))
    asyncio.run(r._ensure_durable_original(db, _job(p)))
    assert db.snap.metadata_["durable_original"]["preserved"] is False and db.commits == 1


def test_job_fails_before_staging_when_configured_and_preservation_fails(monkeypatch, tmp_path):
    from app.tefca_registry.rce import iqvia_routes as r
    import app.core.database as dbm

    def fail(*a, **k):
        raise obs.OriginalNotPreserved("x")

    async def beat(*a, **k):
        return None
    monkeypatch.setenv(obs.BACKEND_ENV, "azure_blob")
    monkeypatch.setattr(obs, "preserve_file", fail)
    monkeypatch.setattr(r.jobs, "heartbeat", beat)
    monkeypatch.setattr(dbm, "async_session_maker", lambda: _AsyncNull())
    p, sha = _file(tmp_path)
    db = _Db(_Snap(sha))
    with pytest.raises(obs.OriginalNotPreserved):
        asyncio.run(r._ensure_durable_original(db, _job(p)))
    assert "durable_original" not in db.snap.metadata_          # nothing recorded as preserved


def test_job_skips_when_already_preserved(monkeypatch, tmp_path):
    from app.tefca_registry.rce import iqvia_routes as r

    def no(*a, **k):
        raise AssertionError("must not upload twice")
    monkeypatch.setenv(obs.BACKEND_ENV, "azure_blob")
    monkeypatch.setattr(obs, "preserve_file", no)
    p, sha = _file(tmp_path)
    db = _Db(_Snap(sha, {"durable_original": {"preserved": True}}))
    asyncio.run(r._ensure_durable_original(db, _job(p)))
    assert db.commits == 0
