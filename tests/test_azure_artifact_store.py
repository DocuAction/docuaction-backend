"""The Azure Blob artifact backend, exercised against a real container.

Until Step #18B this class was a declared seam whose every method raised, which
made the local filesystem the only working backend — and on App Service that is
the container's own writable layer, which does not survive a restart. These
tests are what turn "implemented" into "exercised".

They SKIP unless `REPORT_ARTIFACT_AZURE_ACCOUNT` and
`REPORT_ARTIFACT_AZURE_CONTAINER` are set and a credential resolves, so the
suite stays runnable on a machine with no Azure access. A skip is reported as a
skip and never as a pass.

NO GOVERNMENT DATA. Every object written here is synthetic, keyed under a
per-run prefix, and the account is a DEV account with shared-key access
disabled — the only way in is a managed identity or a signed-in principal.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.core.storage.artifact_store import (ArtifactNotFound,
                                             ArtifactStoreError,
                                             ArtifactStoreUnconfigured,
                                             AzureBlobArtifactStore,
                                             InvalidArtifactKey,
                                             RetentionPolicy, content_sha256)

ACCOUNT = os.getenv("REPORT_ARTIFACT_AZURE_ACCOUNT", "")
CONTAINER = os.getenv("REPORT_ARTIFACT_AZURE_CONTAINER", "")

pytestmark = pytest.mark.skipif(
    not (ACCOUNT and CONTAINER),
    reason="REPORT_ARTIFACT_AZURE_ACCOUNT / _CONTAINER not set; "
           "the Azure artifact backend is not exercised on this host")


@pytest.fixture(scope="module")
def store():
    try:
        backend = AzureBlobArtifactStore()
        backend._client().get_container_properties()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Azure blob container unreachable: "
                    f"{type(exc).__name__}")
    return backend


@pytest.fixture
def key():
    """A fresh key per test, so tests cannot see each other's versions."""
    return f"t18b-{uuid.uuid4().hex[:12]}"


# ═══ write and read ═════════════════════════════════════════════════════════

def test_bytes_written_are_the_bytes_read_back(store, key):
    content = b"the registered bytes \x00\x01 with binary in them"
    record = store.put(key, content, content_type="text/csv",
                       retention=RetentionPolicy())

    assert record.version == 1
    assert record.content_sha256 == content_sha256(content)
    assert record.size_bytes == len(content)
    assert record.locator.startswith("azureblob://")
    assert store.get(record.locator) == content


def test_the_record_survives_a_new_client(store, key):
    """Durability is the whole point: a fresh client, and therefore a fresh
    connection, must still find the object. This is what the local backend
    could not offer on App Service."""
    original = store.put(key, b"durable", content_type="text/csv")

    fresh = AzureBlobArtifactStore()
    got = fresh.head(original.locator)

    assert got.content_sha256 == original.content_sha256
    assert got.version == original.version
    assert fresh.get(original.locator) == b"durable"


def test_head_returns_the_record_without_the_bytes(store, key):
    record = store.put(key, b"x" * 5000, content_type="application/pdf")
    got = store.head(record.locator)

    assert got.artifact_id == record.artifact_id
    assert got.size_bytes == 5000
    assert got.content_type == "application/pdf"
    assert got.retention.classification == record.retention.classification


# ═══ immutability and versioning ════════════════════════════════════════════

def test_identical_content_is_deduplicated_not_versioned(store, key):
    """Regenerating an unchanged report is normal. Treating it as a collision
    would make the safe case look like the dangerous one."""
    first = store.put(key, b"same", content_type="text/csv")
    second = store.put(key, b"same", content_type="text/csv")

    assert second.version == first.version
    assert second.deduplicated is True
    assert second.artifact_id == first.artifact_id
    assert len(store.versions(key)) == 1


def test_different_content_adds_a_version_and_never_replaces(store, key):
    first = store.put(key, b"version one", content_type="text/csv")
    second = store.put(key, b"version two", content_type="text/csv")

    assert second.version == first.version + 1
    assert store.get(first.locator) == b"version one", (
        "the first version was overwritten")
    assert store.get(second.locator) == b"version two"
    assert [r.version for r in store.versions(key)] == [1, 2]


def test_a_blob_cannot_be_overwritten(store, key):
    """The guarantee is enforced by the SERVICE, not by a prior check.

    `upload_blob(overwrite=False)` is refused by Azure. A check-then-write would
    have a window between its two calls; this has none.
    """
    from azure.core.exceptions import ResourceExistsError

    record = store.put(key, b"original", content_type="text/csv")
    name = store._parse(record.locator)

    with pytest.raises(ResourceExistsError):
        store._client().upload_blob(name=name, data=b"tampered",
                                    overwrite=False)

    assert store.get(record.locator) == b"original"


# ═══ refusals ═══════════════════════════════════════════════════════════════

def test_a_missing_object_is_not_found_rather_than_empty(store, key):
    with pytest.raises(ArtifactNotFound):
        store.get(f"azureblob://{CONTAINER}/{key}/9/artifact.csv")


@pytest.mark.parametrize("hostile", [
    "local://a/1/artifact.csv",
    "https://example.com/a/1/artifact.csv",
    "azureblob://other-container/a/1/artifact.csv",
    "azureblob://{c}/../../secrets/1/artifact.csv",
    "azureblob://{c}/a/1/.hidden",
    "azureblob://{c}/a/notanumber/artifact.csv",
    "azureblob://{c}/a/1",
    "azureblob://{c}/a/1/x/y",
])
def test_a_locator_cannot_escape_the_container(store, hostile):
    """No caller supplies a locator — they come from the registry row — but one
    that tried to escape is refused regardless. A control that relies on nobody
    passing the wrong thing is not a control."""
    with pytest.raises((ArtifactNotFound, InvalidArtifactKey)):
        store.get(hostile.replace("{c}", CONTAINER))


def test_a_bad_key_is_refused_before_any_network_call(store):
    for bad in ("../escape", "with/slash", "", "x" * 300, ".leading"):
        with pytest.raises(InvalidArtifactKey):
            store.put(bad, b"content")


def test_content_must_be_bytes(store, key):
    with pytest.raises(ArtifactStoreError):
        store.put(key, "a string, not bytes")  # type: ignore[arg-type]


def test_the_backend_refuses_to_start_unconfigured(monkeypatch):
    """Selecting Azure without configuration must raise rather than silently
    falling back to local — a report the operator believes is in Azure and is
    actually on a container's ephemeral disk is worse than an error."""
    monkeypatch.delenv("REPORT_ARTIFACT_AZURE_ACCOUNT", raising=False)
    monkeypatch.delenv("REPORT_ARTIFACT_AZURE_CONTAINER", raising=False)
    with pytest.raises(ArtifactStoreUnconfigured):
        AzureBlobArtifactStore()


# ═══ concurrency ════════════════════════════════════════════════════════════

def test_concurrent_writers_do_not_lose_a_version(store, key):
    """Two writers racing on the same key must produce two versions, not one
    overwritten one. The loser of the `overwrite=False` race takes the next
    version rather than replacing what the winner wrote."""
    from concurrent.futures import ThreadPoolExecutor

    payloads = [f"writer {i}".encode() for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(
            lambda body: store.put(key, body, content_type="text/csv"),
            payloads))

    assert sorted(r.version for r in records) == [1, 2, 3, 4]
    for record, body in zip(records, payloads):
        assert store.get(record.locator) == body
    assert len(store.versions(key)) == 4


# ═══ the store is selectable by configuration ═══════════════════════════════

def test_configuration_selects_this_backend(monkeypatch):
    from app.core.storage.artifact_store import build_artifact_store

    monkeypatch.setenv("REPORT_ARTIFACT_BACKEND", "azure")
    monkeypatch.setenv("REPORT_ARTIFACT_AZURE_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("REPORT_ARTIFACT_AZURE_CONTAINER", CONTAINER)
    built = build_artifact_store()
    assert built.backend == "azure_blob"


def test_no_account_key_or_connection_string_is_accepted():
    """Shared-key access is disabled on the account, and the code must not offer
    a way to use one anyway — a connection string is the form of this
    configuration most likely to end up in a log or a commit."""
    import inspect

    source = inspect.getsource(AzureBlobArtifactStore)
    for forbidden in ("connection_string", "from_connection_string",
                      "account_key", "AccountKey", "generate_blob_sas"):
        assert forbidden not in source, f"the backend references {forbidden}"
    assert "DefaultAzureCredential" in source
