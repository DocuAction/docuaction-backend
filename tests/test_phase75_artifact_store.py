"""Phase 7.5A — durable artifact storage.

Finalised reports previously lived only in a Postgres column, which can be
updated. There was no content address for the delivered bytes and no way to ask
"is this still the document that was issued".

The tests here pin the properties that make the answer trustworthy: bytes are
written once, identical content is idempotent rather than an error, different
content becomes a new version without disturbing the old one, and a recorded
hash is one that gets recomputed.

They also pin two refusals — the core store must not learn what a report is, and
retention must not become irreversible while D8 is open.

DEVELOPMENT/TEST DATA. Nothing here is an ONC finding.
"""
from __future__ import annotations

import os

import pytest

from app.core.storage.artifact_store import (
    RETENTION_PENDING, ArtifactNotFound, ArtifactStoreUnconfigured,
    AzureBlobArtifactStore, InvalidArtifactKey, LocalFilesystemArtifactStore,
    ReportArtifactStore, RetentionPolicy, build_artifact_store, content_sha256,
    validate_key)

HTML = b"<html><body>development report</body></html>"
OTHER = b"<html><body>a different report</body></html>"


@pytest.fixture
def store(tmp_path):
    return LocalFilesystemArtifactStore(str(tmp_path / "artifacts"))


class TestWriteOnce:

    def test_storing_returns_a_content_address(self, store):
        a = store.put("DA-ARC-2026-001-html", HTML)
        assert a.content_sha256 == content_sha256(HTML)
        assert a.size_bytes == len(HTML)
        assert a.version == 1

    def test_identical_content_is_idempotent_not_an_error(self, store):
        """Regenerating an unchanged report is normal and safe.

        Treating it as a collision would make the safe case look like the
        dangerous one, and callers would learn to ignore the error.
        """
        first = store.put("DA-ARC-2026-001-html", HTML)
        again = store.put("DA-ARC-2026-001-html", HTML)
        assert again.deduplicated is True
        assert again.version == first.version
        assert again.content_sha256 == first.content_sha256

    def test_different_content_becomes_a_new_version(self, store):
        first = store.put("DA-ARC-2026-001-html", HTML)
        second = store.put("DA-ARC-2026-001-html", OTHER)
        assert second.version == first.version + 1
        assert second.deduplicated is False

    def test_the_earlier_version_is_still_retrievable(self, store):
        """The whole point: history is not overwritten."""
        first = store.put("DA-ARC-2026-001-html", HTML)
        store.put("DA-ARC-2026-001-html", OTHER)
        assert store.get(first.locator) == HTML

    def test_versions_are_listed_oldest_first(self, store):
        store.put("DA-ARC-2026-001-html", HTML)
        store.put("DA-ARC-2026-001-html", OTHER)
        assert [v.version for v in store.versions("DA-ARC-2026-001-html")] == [1, 2]

    def test_the_store_has_no_way_to_delete_or_overwrite(self):
        """Absence of a capability, asserted deliberately.

        Retention cleanup, when it is eventually approved, happens out of band —
        not down an application path a bug could reach.
        """
        for forbidden in ("delete", "remove", "overwrite", "update", "replace"):
            assert not hasattr(ReportArtifactStore, forbidden)


class TestIntegrityIsRecomputed:

    def test_verify_rehashes_the_stored_bytes(self, store):
        a = store.put("DA-ARC-2026-001-html", HTML)
        assert store.verify(a.locator) is True

    def test_verify_catches_tampering(self, store):
        """A recorded hash nobody recomputes is a claim, not evidence."""
        a = store.put("DA-ARC-2026-001-html", HTML)
        path = store._resolve(a.locator)
        with open(path, "wb") as fh:
            fh.write(b"<html>tampered</html>")
        assert store.verify(a.locator) is False

    def test_an_unknown_locator_is_not_found(self, store):
        with pytest.raises(ArtifactNotFound):
            store.get("local://DA-ARC-9999-999-html/1/artifact.html")


class TestKeysCannotEscapeTheStore:

    @pytest.mark.parametrize("bad", [
        "../etc/passwd", "a/b", "..", ".hidden", "", "x" * 200,
        "with space", "semi;colon", None, 5,
    ])
    def test_unsafe_keys_are_refused(self, bad):
        with pytest.raises(InvalidArtifactKey):
            validate_key(bad)

    def test_a_traversal_locator_is_refused(self, store):
        with pytest.raises(ArtifactNotFound):
            store.get("local://../../../etc/passwd/1/artifact.html")

    def test_a_normal_report_id_is_a_valid_key(self):
        assert validate_key("DA-ARC-2026-001-html")


class TestRetentionStaysReversible:
    """D8 is open. Nothing here may become irreversible before it is answered."""

    def test_a_new_artifact_has_no_retention_period(self, store):
        a = store.put("DA-ARC-2026-001-html", HTML)
        assert a.retention.classification == RETENTION_PENDING
        assert a.retention.period_days is None
        assert a.retention.is_pending is True

    def test_worm_is_not_locked(self, store):
        a = store.put("DA-ARC-2026-001-html", HTML)
        assert a.retention.worm_locked is False

    def test_an_approved_period_can_be_applied_later(self):
        """The transition the design exists to make possible."""
        approved = RetentionPolicy().with_approved_period(
            2555, basis="COR decision D8")
        assert approved.period_days == 2555
        assert approved.classification == "APPROVED"
        assert approved.is_pending is False
        # still not locked unless explicitly asked
        assert approved.worm_locked is False

    def test_locking_is_a_separate_opt_in(self):
        locked = RetentionPolicy().with_approved_period(
            2555, basis="COR decision D8", lock=True)
        assert locked.worm_locked is True

    def test_an_approved_period_needs_a_basis(self):
        with pytest.raises(ValueError):
            RetentionPolicy().with_approved_period(2555, basis="")

    def test_a_nonsense_period_is_refused(self):
        with pytest.raises(ValueError):
            RetentionPolicy().with_approved_period(0, basis="x")

    def test_applying_retention_does_not_change_report_semantics(self, store):
        """Same bytes, same hash, same locator — only the policy differs."""
        plain = store.put("R1-html", HTML)
        approved = store.put(
            "R2-html", HTML,
            retention=RetentionPolicy().with_approved_period(90, basis="test"))
        assert plain.content_sha256 == approved.content_sha256
        assert store.get(plain.locator) == store.get(approved.locator)


class TestTheCoreStoreKnowsNothingAboutPrograms:
    """If this file learns what a review cycle is, the next program cannot
    use it."""

    def test_no_program_vocabulary_in_the_core_module(self):
        import inspect

        from app.core.storage import artifact_store
        source = inspect.getsource(artifact_store)
        # Prose in the module docstring explains the motivation; the code must
        # not reference program concepts.
        code = source.split('"""', 2)[-1]
        for term in ("TEFCA", "review_cycle", "evidence_rule", "QHIN",
                     "analyst", "qa_event"):
            assert term not in code, f"core store leaked program concept {term!r}"

    def test_program_facts_travel_as_opaque_metadata(self, store):
        a = store.put("DA-ARC-2026-001-html", HTML,
                      metadata={"review_cycle_id": "DEV-CYCLE-x",
                                "report_type": "executive"})
        assert a.metadata["review_cycle_id"] == "DEV-CYCLE-x"
        assert store.head(a.locator).metadata["report_type"] == "executive"


class TestBackendSelection:

    def test_local_is_the_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("REPORT_ARTIFACT_BACKEND", raising=False)
        assert build_artifact_store(root=str(tmp_path)).backend == "local"

    def test_an_unknown_backend_is_refused(self):
        with pytest.raises(ArtifactStoreUnconfigured):
            build_artifact_store("s3")

    def test_azure_without_configuration_refuses_rather_than_falling_back(
            self, monkeypatch):
        """Silently falling back to local would leave an operator believing a
        report is in Azure when it is on a container's ephemeral disk."""
        monkeypatch.delenv("REPORT_ARTIFACT_AZURE_ACCOUNT", raising=False)
        monkeypatch.delenv("REPORT_ARTIFACT_AZURE_CONTAINER", raising=False)
        with pytest.raises(ArtifactStoreUnconfigured):
            build_artifact_store("azure")

    def test_the_azure_backend_never_accepts_a_connection_string(self):
        """The configuration form most likely to end up in a log or a commit."""
        import inspect
        source = inspect.getsource(AzureBlobArtifactStore)
        assert "connection_string" not in source
        assert "AccountKey" not in source

    def test_the_azure_backend_uses_managed_identity(self):
        import inspect
        assert "DefaultAzureCredential" in inspect.getsource(AzureBlobArtifactStore)

    def test_no_credential_literal_lives_in_the_module(self):
        import inspect

        from app.core.storage import artifact_store
        source = inspect.getsource(artifact_store)
        for leak in ("AccountKey=", "SharedAccessSignature", "sig=",
                     "password=", "client_secret"):
            assert leak not in source


class TestArtifactKeyNaming:

    def test_html_and_pdf_of_one_report_are_separate_objects(self):
        """A PDF that appeared to supersede its own HTML would be nonsense."""
        from app.reports.data.artifact_registry import artifact_key

        assert (artifact_key("DA-ARC-2026-001", "text/html")
                != artifact_key("DA-ARC-2026-001", "application/pdf"))

    def test_the_key_is_a_valid_store_key(self):
        from app.reports.data.artifact_registry import artifact_key

        assert validate_key(artifact_key("DA-ARC-2026-001", "text/html"))
