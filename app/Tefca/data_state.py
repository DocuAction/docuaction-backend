"""
Three questions the application used to answer with one flag.

THE DEFECT THIS CORRECTS
────────────────────────
`is_running_mock()` returned True whenever no entity-data key was configured,
and every dashboard, report and status response was stamped from it. In
production, before the first Government intake, that produced:

    "data_source": "MOCK — demonstration data only"
    "mock_data_warning": "This report uses synthetic demonstration data."

on a system holding no demonstration data at all. A clean production
environment announced itself as a mock one. That is not a cosmetic problem: it
tells an operator that development evidence exists when none does, and it would
teach people to ignore the mock warning in exactly the environment where the
warning matters.

The cause is that one boolean was carrying three independent facts:

    ENVIRONMENT          development or production
    GOVERNMENT DATASET   not loaded or loaded
    DATA IDENTITY        none, mock/test, or Government

"No Government data" and "mock environment" are different statements. A
production system before its first intake is empty, not fake.

WHAT ACTIVATES GOVERNMENT STATE
───────────────────────────────
Not an environment variable, and NOT an API key. A key says AGT can reach a
source; it says nothing about whether authorised Government data has been
received. Government state requires an actual controlled intake carrying
complete provenance — an intake id, a real SHA-256, a record count, a schema
fingerprint, a receipt timestamp, a successful parse, and an explicit
authorisation marker set only by the authorised import path.

The explicit marker matters more than it looks. The development artefact in this
system carries `source_metadata.origin = "ONC/RCE delivery"` — it is a copy of a
real ONC snapshot used as development data. Anything inferring Government status
from origin text, filename or record count would classify development evidence
as Government. Nothing here reads those fields for that purpose.

WHAT THIS MODULE DOES NOT DO
────────────────────────────
It does not weaken a single existing safeguard. Development keeps its mock
labelling exactly as before. Nothing here can turn an observation into a
finding, and no state it reports makes findings available on its own — the
analyst, QA and reportability gates are untouched and still apply.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DATA_STATE_VERSION = "1.0.0"

#: A real SHA-256 and nothing else. Same rule the artifact registry enforces.
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: The only intake status that means the delivery was read successfully.
_PARSED = "PARSED"

#: Set exclusively by the authorised Government import path. Absent from every
#: existing record, which is why development data cannot satisfy the condition.
GOVERNMENT_AUTHORIZED_KEY = "government_authorized"


class Environment(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class DatasetState(str, Enum):
    NOT_LOADED = "NOT_LOADED"
    LOADED = "LOADED"


class DataIdentity(str, Enum):
    #: No dataset of any kind. The expected clean production state.
    NONE = "NONE"
    #: Development or demonstration data.
    MOCK_TEST = "MOCK_TEST"
    #: Authorised Government data, received through a controlled intake.
    GOVERNMENT = "GOVERNMENT"


def current_environment() -> Environment:
    """The deployment environment, from configuration.

    Anything unrecognised is treated as development. Defaulting the other way
    would let a misconfigured host suppress the mock warning, which is the more
    dangerous mistake of the two.
    """
    raw = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "").strip().lower()
    return Environment.PRODUCTION if raw in ("production", "prod") else Environment.DEVELOPMENT


@dataclass(frozen=True)
class DataState:
    """What data this deployment holds, and what may be said about it."""

    environment: Environment
    government_dataset: DatasetState
    data_identity: DataIdentity
    mock_data_present: bool
    #: Why the dataset is not loaded, when it is not. Never a guess.
    reason: Optional[str] = None
    #: The authorised intake behind GOVERNMENT state, when there is one.
    intake_id: Optional[str] = None
    source_sha256: Optional[str] = None

    # ── what surfaces are allowed to say ─────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def shows_mock_warning(self) -> bool:
        """Only ever true when mock or test data is actually present.

        Not "when Government data is absent". That was the defect.
        """
        return self.data_identity is DataIdentity.MOCK_TEST

    @property
    def findings_available(self) -> bool:
        """Whether operational review results can exist at all.

        A necessary condition, never a sufficient one. Even with Government data
        loaded, a result still needs an analyst determination and an independent
        QA approval before it is reportable. This gate sits in front of those; it
        does not replace them.
        """
        return self.government_dataset is DatasetState.LOADED

    @property
    def status_message(self) -> str:
        if self.data_identity is DataIdentity.GOVERNMENT:
            return "Government dataset loaded."
        if self.data_identity is DataIdentity.MOCK_TEST:
            return ("Development and test data only. Not for operational "
                    "decisions.")
        return "Government dataset not yet loaded."

    @property
    def availability_message(self) -> str:
        if self.findings_available:
            return ("Review results are available subject to analyst "
                    "determination and independent QA approval.")
        if self.data_identity is DataIdentity.MOCK_TEST:
            return ("No operational review results are available. This "
                    "deployment holds development data only.")
        return "No operational review results are available."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "environment": self.environment.value,
            "government_dataset": self.government_dataset.value,
            "data_identity": self.data_identity.value,
            "mock_data_present": self.mock_data_present,
            "shows_mock_warning": self.shows_mock_warning,
            "findings_available": self.findings_available,
            "status_message": self.status_message,
            "availability_message": self.availability_message,
            "reason": self.reason,
            "intake_id": self.intake_id,
            "source_sha256": self.source_sha256,
            "data_state_version": DATA_STATE_VERSION,
        }


# ── determination ────────────────────────────────────────────────────────────

def _authorised_government_intake(row: Any) -> bool:
    """Whether one intake row is an authorised Government delivery.

    Every condition must hold. Each exists because its absence has a plausible
    innocent explanation that must not be mistaken for authorisation:

      * the explicit marker — set only by the authorised import path;
      * a real SHA-256 — a placeholder has reached a provenance field before;
      * a successful parse — a failed delivery is not a loaded dataset;
      * a positive record count — an empty file is not a population;
      * a schema fingerprint — an unfingerprinted file cannot be reconciled;
      * a receipt timestamp — an intake nobody can date cannot be audited.
    """
    if row is None:
        return False
    metadata = getattr(row, "source_metadata", None) or {}
    if metadata.get(GOVERNMENT_AUTHORIZED_KEY) is not True:
        return False
    digest = (getattr(row, "sha256", "") or "").strip().lower()
    if not _SHA256.match(digest):
        return False
    if (getattr(row, "status", "") or "").strip().upper() != _PARSED:
        return False
    if not (getattr(row, "record_count", 0) or 0) > 0:
        return False
    if not (getattr(row, "schema_fingerprint", "") or "").strip():
        return False
    if getattr(row, "received_at", None) is None:
        return False
    return True


async def resolve_data_state(db) -> DataState:
    """The authoritative state, determined from actual intake provenance.

    Government status is never inferred from configuration. It requires a
    controlled intake that satisfies every condition in
    `_authorised_government_intake`.
    """
    environment = current_environment()

    intakes = []
    try:
        from sqlalchemy import select

        from app.tefca_registry.rce.models import RceSourceIntake

        intakes = list((await db.execute(
            select(RceSourceIntake)
            .where(RceSourceIntake.duplicate_of_intake_id.is_(None))
            .order_by(RceSourceIntake.received_at.desc())
        )).scalars().all())
    except Exception as exc:  # noqa: BLE001
        # Cannot see the intake record, so cannot claim anything is loaded.
        logger.warning("data state: intake records unavailable: %s", exc)
        return DataState(
            environment=environment,
            government_dataset=DatasetState.NOT_LOADED,
            data_identity=DataIdentity.NONE,
            mock_data_present=False,
            reason="INTAKE_RECORDS_UNAVAILABLE")

    authorised = next((i for i in intakes if _authorised_government_intake(i)), None)
    if authorised is not None:
        return DataState(
            environment=environment,
            government_dataset=DatasetState.LOADED,
            data_identity=DataIdentity.GOVERNMENT,
            mock_data_present=False,
            intake_id=str(getattr(authorised, "id", "") or "") or None,
            source_sha256=(getattr(authorised, "sha256", "") or "").lower() or None)

    # Not loaded. Is anything else here?
    if intakes:
        return DataState(
            environment=environment,
            government_dataset=DatasetState.NOT_LOADED,
            data_identity=DataIdentity.MOCK_TEST,
            mock_data_present=True,
            reason="NO_AUTHORISED_GOVERNMENT_INTAKE")

    return DataState(
        environment=environment,
        government_dataset=DatasetState.NOT_LOADED,
        data_identity=DataIdentity.NONE,
        mock_data_present=False,
        reason="NO_INTAKE_RECORDED")


def data_state_sync() -> DataState:
    """Conservative state for surfaces with no database session.

    It cannot verify an intake, so it never claims GOVERNMENT and never claims a
    production deployment is loaded. In development it reports MOCK_TEST, which
    preserves the existing warning behaviour exactly.

    Understating is the safe direction: a surface that says "not loaded" when
    data is in fact loaded is merely unhelpful, whereas one that says "loaded"
    or "mock" without checking is misleading.
    """
    environment = current_environment()
    if environment is Environment.PRODUCTION:
        return DataState(
            environment=environment,
            government_dataset=DatasetState.NOT_LOADED,
            data_identity=DataIdentity.NONE,
            mock_data_present=False,
            reason="NOT_VERIFIED_WITHOUT_DATABASE_SESSION")
    return DataState(
        environment=environment,
        government_dataset=DatasetState.NOT_LOADED,
        data_identity=DataIdentity.MOCK_TEST,
        mock_data_present=True,
        reason="DEVELOPMENT_ENVIRONMENT")


def labels_for(state: DataState) -> Dict[str, Any]:
    """Provenance fields stamped on a dashboard, report or status response.

    `mock_data_warning` is populated only when mock or test data is genuinely
    present. In a clean production deployment it is None, and `data_source`
    states the truth: the Government dataset has not been loaded.
    """
    if state.data_identity is DataIdentity.MOCK_TEST:
        return {
            "data_source": "MOCK — demonstration data only",
            "mock_data_warning": ("This report uses synthetic demonstration data. "
                                  "Do not use for operational decisions."),
            "environment": state.environment.value,
            "government_dataset": state.government_dataset.value,
            "data_identity": state.data_identity.value,
            "findings_available": state.findings_available,
        }
    if state.data_identity is DataIdentity.GOVERNMENT:
        return {
            "data_source": "GOVERNMENT",
            "mock_data_warning": None,
            "environment": state.environment.value,
            "government_dataset": state.government_dataset.value,
            "data_identity": state.data_identity.value,
            "findings_available": state.findings_available,
        }
    return {
        "data_source": "Government dataset not yet loaded",
        "mock_data_warning": None,
        "environment": state.environment.value,
        "government_dataset": state.government_dataset.value,
        "data_identity": state.data_identity.value,
        "findings_available": state.findings_available,
        "availability_note": state.availability_message,
    }
