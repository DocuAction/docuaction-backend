"""Production data-state correction — the three concepts, kept apart.

`is_running_mock()` used to answer three questions with one boolean, and got the
production case wrong: a clean production deployment holding no data was
labelled "MOCK — demonstration data only" on every dashboard, report and status
response.

These tests pin the six cases from the correction matrix, and the two directions
the fix could have failed in — production-empty being called mock (the original
defect), and production-empty being called Government (the inverted defect the
fix would have introduced if the flag had simply been negated).

No Government data is imported by any test here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.Tefca.data_state import (GOVERNMENT_AUTHORIZED_KEY, DataIdentity,
                                  DataState, DatasetState, Environment,
                                  current_environment, data_state_sync,
                                  labels_for, resolve_data_state)

REAL_SHA = "689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d"


class _Intake:
    """An intake row, valid by default so each test can break one thing."""

    def __init__(self, **over):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.sha256 = REAL_SHA
        self.status = "PARSED"
        self.record_count = 23_566
        self.schema_fingerprint = "abc123"
        self.received_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
        self.duplicate_of_intake_id = None
        self.source_metadata = {GOVERNMENT_AUTHORIZED_KEY: True}
        for key, value in over.items():
            setattr(self, key, value)


class _DB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_a, **_k):
        rows = self._rows

        class R:
            def scalars(self_inner):
                class S:
                    def all(self_s): return rows
                return S()
        return R()


class _BrokenDB:
    async def execute(self, *_a, **_k):
        raise RuntimeError("no connection")


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")


# ═══ environment classification ══════════════════════════════════════════════

class TestEnvironmentClassification:

    @pytest.mark.parametrize("value", ["production", "PRODUCTION", "prod", " Prod "])
    def test_production_is_recognised(self, monkeypatch, value):
        monkeypatch.setenv("ENVIRONMENT", value)
        assert current_environment() is Environment.PRODUCTION

    @pytest.mark.parametrize("value", ["development", "dev", "staging", "", "banana"])
    def test_anything_else_is_development(self, monkeypatch, value):
        monkeypatch.setenv("ENVIRONMENT", value)
        assert current_environment() is Environment.DEVELOPMENT

    def test_unset_defaults_to_development(self, monkeypatch):
        """A misconfigured host must not silently suppress the mock warning."""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        assert current_environment() is Environment.DEVELOPMENT


# ═══ 1. DEV + mock/test data ═════════════════════════════════════════════════

class TestCase1DevelopmentWithMockData:

    @pytest.mark.asyncio
    async def test_identity_is_mock_test(self, dev):
        state = await resolve_data_state(_DB([_Intake(source_metadata={})]))
        assert state.data_identity is DataIdentity.MOCK_TEST
        assert state.mock_data_present is True

    @pytest.mark.asyncio
    async def test_the_development_warning_is_visible(self, dev):
        state = await resolve_data_state(_DB([_Intake(source_metadata={})]))
        assert state.shows_mock_warning is True
        labels = labels_for(state)
        assert labels["data_source"].startswith("MOCK")
        assert labels["mock_data_warning"]

    def test_the_sync_path_preserves_development_behaviour(self, dev):
        """Unchanged from before the correction."""
        state = data_state_sync()
        assert state.data_identity is DataIdentity.MOCK_TEST
        assert state.shows_mock_warning is True

    def test_is_running_mock_is_still_true_in_development(self, dev):
        from app.Tefca.connectors import is_running_mock

        assert is_running_mock() is True


# ═══ 2. PROD + zero Government intake ════════════════════════════════════════

class TestCase2ProductionEmpty:
    """The state the correction exists to represent."""

    @pytest.mark.asyncio
    async def test_dataset_is_not_loaded_and_identity_is_none(self, prod):
        state = await resolve_data_state(_DB([]))
        assert state.government_dataset is DatasetState.NOT_LOADED
        assert state.data_identity is DataIdentity.NONE
        assert state.mock_data_present is False

    @pytest.mark.asyncio
    async def test_no_mock_warning(self, prod):
        """The defect. A clean production system is empty, not fake."""
        state = await resolve_data_state(_DB([]))
        assert state.shows_mock_warning is False
        labels = labels_for(state)
        assert labels["mock_data_warning"] is None
        assert "MOCK" not in labels["data_source"].upper()
        assert "demonstration" not in labels["data_source"].lower()
        assert "synthetic" not in labels["data_source"].lower()

    @pytest.mark.asyncio
    async def test_it_says_the_dataset_is_not_loaded(self, prod):
        state = await resolve_data_state(_DB([]))
        assert state.status_message == "Government dataset not yet loaded."
        assert labels_for(state)["data_source"] == "Government dataset not yet loaded"

    @pytest.mark.asyncio
    async def test_findings_are_unavailable(self, prod):
        state = await resolve_data_state(_DB([]))
        assert state.findings_available is False
        assert "No operational review results are available" in state.availability_message

    def test_the_sync_path_agrees(self, prod):
        state = data_state_sync()
        assert state.data_identity is DataIdentity.NONE
        assert state.shows_mock_warning is False

    def test_is_running_mock_is_false_in_clean_production(self, prod):
        from app.Tefca.connectors import is_running_mock

        assert is_running_mock() is False

    def test_the_report_classification_is_not_development(self, prod):
        """It must not claim development evidence exists either."""
        from app.reports.data.source_provenance import (CLASSIFICATION_NONE,
                                                        _classification)
        assert _classification() == CLASSIFICATION_NONE


# ═══ 3. PROD + valid Government intake ═══════════════════════════════════════

class TestCase3ProductionGovernment:

    @pytest.mark.asyncio
    async def test_identity_is_government(self, prod):
        state = await resolve_data_state(_DB([_Intake()]))
        assert state.government_dataset is DatasetState.LOADED
        assert state.data_identity is DataIdentity.GOVERNMENT

    @pytest.mark.asyncio
    async def test_no_mock_warning(self, prod):
        state = await resolve_data_state(_DB([_Intake()]))
        assert state.shows_mock_warning is False
        assert labels_for(state)["mock_data_warning"] is None
        assert labels_for(state)["data_source"] == "GOVERNMENT"

    @pytest.mark.asyncio
    async def test_the_intake_provenance_travels_with_the_state(self, prod):
        state = await resolve_data_state(_DB([_Intake()]))
        assert state.intake_id
        assert state.source_sha256 == REAL_SHA

    @pytest.mark.asyncio
    async def test_findings_become_possible_but_are_still_gated(self, prod):
        """`findings_available` is necessary, never sufficient. Analyst
        determination and independent QA approval still apply."""
        state = await resolve_data_state(_DB([_Intake()]))
        assert state.findings_available is True
        assert "analyst determination" in state.availability_message
        assert "QA approval" in state.availability_message


# ═══ 4. PROD + incomplete or invalid intake ══════════════════════════════════

class TestCase4IncompleteIntakeNeverBecomesGovernment:
    """Each condition removed one at a time."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("broken,label", [
        ({"source_metadata": {}}, "no authorisation marker"),
        ({"source_metadata": {GOVERNMENT_AUTHORIZED_KEY: "yes"}}, "marker not literally True"),
        ({"source_metadata": {GOVERNMENT_AUTHORIZED_KEY: False}}, "marker false"),
        ({"sha256": "cafe"}, "placeholder hash"),
        ({"sha256": ""}, "no hash"),
        ({"sha256": REAL_SHA[:-1] + "g"}, "non-hex hash"),
        ({"status": "RECEIVED"}, "not parsed"),
        ({"status": "FAILED"}, "parse failed"),
        ({"record_count": 0}, "empty file"),
        ({"record_count": None}, "no record count"),
        ({"schema_fingerprint": ""}, "no schema fingerprint"),
        ({"received_at": None}, "no receipt timestamp"),
    ])
    async def test_it_stays_not_loaded(self, prod, broken, label):
        state = await resolve_data_state(_DB([_Intake(**broken)]))
        assert state.government_dataset is DatasetState.NOT_LOADED, label
        assert state.data_identity is not DataIdentity.GOVERNMENT, label

    @pytest.mark.asyncio
    async def test_an_unreadable_intake_table_claims_nothing(self, prod):
        state = await resolve_data_state(_BrokenDB())
        assert state.government_dataset is DatasetState.NOT_LOADED
        assert state.data_identity is DataIdentity.NONE
        assert state.reason == "INTAKE_RECORDS_UNAVAILABLE"


# ═══ 5. PROD + API credentials but zero Government intake ════════════════════

class TestCase5CredentialsAreNotData:

    @pytest.mark.asyncio
    async def test_an_entity_data_key_does_not_load_a_dataset(self, prod, monkeypatch):
        """A credential says a source is reachable. It says nothing about which
        dataset was received."""
        monkeypatch.setenv("TEFCA_ENTITY_DATA_KEY", "a-real-looking-key-value")
        state = await resolve_data_state(_DB([]))
        assert state.government_dataset is DatasetState.NOT_LOADED
        assert state.data_identity is DataIdentity.NONE

    @pytest.mark.asyncio
    async def test_the_legacy_key_does_not_either(self, prod, monkeypatch):
        monkeypatch.setenv("RCE_DIRECTORY_API_KEY", "a-real-looking-key-value")
        state = await resolve_data_state(_DB([]))
        assert state.government_dataset is DatasetState.NOT_LOADED

    def test_the_sync_path_is_not_swayed_by_a_key(self, prod, monkeypatch):
        monkeypatch.setenv("TEFCA_ENTITY_DATA_KEY", "a-real-looking-key-value")
        assert data_state_sync().data_identity is DataIdentity.NONE

    def test_the_determination_never_reads_a_credential(self):
        """Asserted against the source, so the coupling cannot come back."""
        import inspect

        from app.Tefca import data_state

        source = inspect.getsource(data_state._authorised_government_intake)
        for credential in ("TEFCA_ENTITY_DATA_KEY", "RCE_DIRECTORY_API_KEY",
                           "getenv", "environ"):
            assert credential not in source


# ═══ 6. Development evidence never satisfies the Government condition ════════

class TestCase6DevelopmentEvidenceIsNeverGovernment:

    @pytest.mark.asyncio
    async def test_government_sounding_metadata_is_not_enough(self, prod):
        """The real trap. The development artefact in this system carries
        `origin: "ONC/RCE delivery"` — it is a copy of a real ONC snapshot used
        as development data. Nothing may infer Government status from it."""
        development = _Intake(source_metadata={
            "origin": "ONC/RCE delivery",
            "profiled": "2026-08-21",
            "field_map_version": "1.0.0",
        })
        state = await resolve_data_state(_DB([development]))
        assert state.data_identity is DataIdentity.MOCK_TEST
        assert state.government_dataset is DatasetState.NOT_LOADED

    @pytest.mark.asyncio
    async def test_a_government_looking_filename_is_not_enough(self, prod):
        development = _Intake(source_metadata={})
        development.original_filename = "onc-snapshot-20260720.csv"
        state = await resolve_data_state(_DB([development]))
        assert state.data_identity is not DataIdentity.GOVERNMENT

    @pytest.mark.asyncio
    async def test_development_data_in_production_is_surfaced_not_hidden(self, prod):
        """If development evidence ever reached a production database, that is
        an anomaly the operator should see."""
        state = await resolve_data_state(_DB([_Intake(source_metadata={})]))
        assert state.mock_data_present is True
        assert state.is_production is True
        assert state.shows_mock_warning is True

    def test_the_marker_is_not_set_on_any_existing_record(self):
        """No code path outside an authorised import may set it."""
        import subprocess
        result = subprocess.run(
            ["git", "grep", "-n", GOVERNMENT_AUTHORIZED_KEY, "--", "app/"],
            capture_output=True, text=True)
        # Only the definition and the check in data_state.py may mention it.
        files = {line.split(":", 1)[0] for line in result.stdout.splitlines() if line}
        assert files <= {"app/Tefca/data_state.py"}, files


# ═══ the two ways this fix could have failed ═════════════════════════════════

class TestNeitherDirectionOfTheDefect:

    def test_production_empty_is_not_called_mock(self, prod):
        """The original defect."""
        labels = labels_for(data_state_sync())
        assert labels["mock_data_warning"] is None
        assert "MOCK" not in labels["data_source"].upper()

    def test_production_empty_is_not_called_government(self, prod):
        """The inverted defect — what negating the old flag would have produced."""
        state = data_state_sync()
        assert state.data_identity is not DataIdentity.GOVERNMENT
        assert labels_for(state)["data_source"] != "GOVERNMENT"

    def test_no_safeguard_was_weakened(self, dev):
        """Development still warns, exactly as before."""
        labels = labels_for(data_state_sync())
        assert labels["data_source"] == "MOCK — demonstration data only"
        assert "synthetic demonstration data" in labels["mock_data_warning"]

    def test_findings_availability_is_not_a_reportability_gate(self):
        """It gates whether results can exist, never whether they may be
        reported. That remains analyst plus QA."""
        state = DataState(environment=Environment.PRODUCTION,
                          government_dataset=DatasetState.LOADED,
                          data_identity=DataIdentity.GOVERNMENT,
                          mock_data_present=False)
        assert state.findings_available is True
        assert "subject to" in state.availability_message
