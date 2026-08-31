"""Environment, data identity and authorization are three different things.

    ENVIRONMENT      where this deployment runs — DEV or PROD
    DATA IDENTITY    what the dataset IS — mock, Government-delivered, nothing
    AUTHORIZATION    whether an authority has approved that dataset for official
                     Government operational and reporting use

They are routinely spoken about as one thing, and every way of collapsing them
is a different untruth:

  * identity → authorization says "the SHA matches, so this is official". It is
    the one this gate exists to make impossible. Data that looks exactly like
    the Government delivery is not authorised by looking right.
  * environment → identity says "we are in DEV, so this must be test data". A
    development deployment can hold a genuine Government delivery, and calling
    it test data licenses treating it carelessly.
  * environment → authorization says "we are in PROD, so this is official".

WHY EVERY FIXTURE HERE IS SYNTHETIC
    Scenarios 3 and 7 are the authorised ones, and manufacturing them against
    the real intake would mean writing the authorization marker — which
    engineering may not do under any circumstances. They are built from fake
    intake objects that never touch the database.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.Tefca.data_state import (GOVERNMENT_AUTHORIZED_KEY, DataIdentity,
                                  DatasetState, Environment, resolve_data_state)
from app.reports.data.source_provenance import (CLASSIFICATION_DEVELOPMENT,
                                                CLASSIFICATION_GOVERNMENT,
                                                CLASSIFICATION_NONE,
                                                resolve_classification)

#: The delivered file's real digest. Used ONLY to build a fixture that looks
#: exactly like the Government delivery, which is the point of scenarios 2 and 6.
DELIVERED_SHA = ("689472073480b1cc4faf604527eda47e4e"
                 "59928f7a6128d84b2f28bb6e9e9e8d")


class _Intake:
    """A synthetic intake row. Valid by default so a scenario breaks one thing."""

    def __init__(self, **over):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.sha256 = DELIVERED_SHA
        self.status = "PARSED"
        self.record_count = 23_566
        self.schema_fingerprint = "1cd655e9120dc9d0d6a52697ea470519"
        self.received_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
        self.duplicate_of_intake_id = None
        self.source_metadata = {}
        for key, value in over.items():
            setattr(self, key, value)


class _DB:
    """Answers the intake list query and nothing else."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_a, **_k):
        rows = self._rows

        class R:
            def scalars(self_inner):
                class S:
                    def all(self_s):
                        return rows

                    def first(self_s):
                        return rows[0] if rows else None
                return S()
        return R()


AUTHORISED = {GOVERNMENT_AUTHORIZED_KEY: True}


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("APP_ENV", raising=False)


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("APP_ENV", raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# scenario, rows, expected identity, expected classification, findings allowed
# ─────────────────────────────────────────────────────────────────────────────

DEV_SCENARIOS = [
    ("1  DEV + mock dataset",
     [_Intake(sha256="0" * 64, record_count=12, original_filename="mock.csv")],
     DataIdentity.MOCK_TEST, CLASSIFICATION_DEVELOPMENT, False),

    ("2  DEV + Government-identical + no authorization",
     [_Intake()],
     DataIdentity.MOCK_TEST, CLASSIFICATION_DEVELOPMENT, False),

    ("3  DEV + authorized Government fixture",
     [_Intake(source_metadata=dict(AUTHORISED))],
     DataIdentity.GOVERNMENT, CLASSIFICATION_GOVERNMENT, True),
]

PROD_SCENARIOS = [
    ("4  PROD + no dataset",
     [],
     DataIdentity.NONE, CLASSIFICATION_NONE, False),

    ("5  PROD + mock/test data present",
     [_Intake(sha256="0" * 64, record_count=12)],
     DataIdentity.MOCK_TEST, CLASSIFICATION_DEVELOPMENT, False),

    ("6  PROD + Government-identical + no authorization",
     [_Intake()],
     DataIdentity.MOCK_TEST, CLASSIFICATION_DEVELOPMENT, False),

    ("7  PROD + authorized Government fixture",
     [_Intake(source_metadata=dict(AUTHORISED))],
     DataIdentity.GOVERNMENT, CLASSIFICATION_GOVERNMENT, True),

    ("8  provenance missing",
     [_Intake(source_metadata=dict(AUTHORISED), sha256=None,
              schema_fingerprint=None, received_at=None)],
     DataIdentity.MOCK_TEST, CLASSIFICATION_DEVELOPMENT, False),

    ("9  provenance inconsistent — authorised but never parsed",
     [_Intake(source_metadata=dict(AUTHORISED), status="FAILED")],
     DataIdentity.MOCK_TEST, CLASSIFICATION_DEVELOPMENT, False),

    ("10 hash/schema mismatch — authorised, unusable checksum",
     [_Intake(source_metadata=dict(AUTHORISED), sha256="not-a-sha",
              schema_fingerprint="")],
     DataIdentity.MOCK_TEST, CLASSIFICATION_DEVELOPMENT, False),
]


@pytest.mark.parametrize("label,rows,identity,classification,findings",
                         DEV_SCENARIOS,
                         ids=[s[0].split()[0] for s in DEV_SCENARIOS])
@pytest.mark.asyncio
async def test_development_scenarios(dev, label, rows, identity,
                                     classification, findings):
    state = await resolve_data_state(_DB(rows))
    assert state.environment is Environment.DEVELOPMENT
    assert state.data_identity is identity, label
    assert state.findings_available is findings, label
    assert await resolve_classification(_DB(rows)) == classification, label


@pytest.mark.parametrize("label,rows,identity,classification,findings",
                         PROD_SCENARIOS,
                         ids=[s[0].split()[0] for s in PROD_SCENARIOS])
@pytest.mark.asyncio
async def test_production_scenarios(prod, label, rows, identity,
                                    classification, findings):
    state = await resolve_data_state(_DB(rows))
    assert state.environment is Environment.PRODUCTION
    assert state.data_identity is identity, label
    assert state.findings_available is findings, label
    assert await resolve_classification(_DB(rows)) == classification, label


# ═══ the three collapses, each asserted directly ════════════════════════════

@pytest.mark.asyncio
async def test_looking_like_the_delivery_does_not_authorise_it(prod):
    """Scenario 6 stated as the rule it enforces.

    This fixture is the Government delivery by every visible measure — the real
    SHA-256, the expected schema fingerprint, 23,566 records, parsed, dated. It
    carries no authorisation marker, and that alone decides it.
    """
    rows = [_Intake()]
    assert rows[0].sha256 == DELIVERED_SHA
    assert rows[0].record_count == 23_566

    state = await resolve_data_state(_DB(rows))
    assert state.data_identity is not DataIdentity.GOVERNMENT
    assert state.government_dataset is DatasetState.NOT_LOADED
    assert state.reason == "NO_AUTHORISED_GOVERNMENT_INTAKE"
    assert await resolve_classification(_DB(rows)) != CLASSIFICATION_GOVERNMENT


@pytest.mark.asyncio
async def test_development_does_not_downgrade_authorised_government_data(dev):
    """The opposite collapse. A DEV deployment holding an authorised delivery
    holds authorised data; calling it test data licenses treating it carelessly.
    """
    rows = [_Intake(source_metadata=dict(AUTHORISED))]
    state = await resolve_data_state(_DB(rows))
    assert state.environment is Environment.DEVELOPMENT
    assert state.data_identity is DataIdentity.GOVERNMENT
    assert await resolve_classification(_DB(rows)) == CLASSIFICATION_GOVERNMENT


@pytest.mark.asyncio
async def test_production_alone_does_not_make_anything_official(prod):
    """The third collapse. An empty production deployment is not Government
    data, and mock data in production is still mock data."""
    assert await resolve_classification(_DB([])) == CLASSIFICATION_NONE
    assert await resolve_classification(
        _DB([_Intake(sha256="0" * 64)])) == CLASSIFICATION_DEVELOPMENT


@pytest.mark.asyncio
async def test_a_marker_that_is_not_literally_true_authorises_nothing(prod):
    """`"yes"`, `1` and `"true"` are not True. A truthiness check here would
    make a typo in a metadata blob an authorisation."""
    for value in ("yes", "true", 1, "True", [], {}, None, False):
        rows = [_Intake(source_metadata={GOVERNMENT_AUTHORIZED_KEY: value})]
        state = await resolve_data_state(_DB(rows))
        assert state.data_identity is not DataIdentity.GOVERNMENT, value


def test_no_application_code_sets_the_authorisation_marker():
    """Engineering may determine that data IS the Government delivery. It may
    not manufacture the authorisation that makes it official.

    `test_data_state.py` already asserts this for `app/`. This repeats it for
    the reporting and export code added since, which is the code with the most
    obvious motive to set it — a test would otherwise fail.
    """
    import subprocess

    result = subprocess.run(
        ["git", "grep", "-l", GOVERNMENT_AUTHORIZED_KEY, "--",
         "app/reports/", "app/tefca_registry/"],
        capture_output=True, text=True)
    assert not result.stdout.strip(), (
        f"the authorisation marker is referenced in reporting or registry "
        f"code: {result.stdout.strip()}")
