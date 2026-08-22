"""What an operator needs to answer "what did that run do?" without a payload.

Counts and identifiers only. No record contents, no artefact bytes, no URL query
string that has not been through `redact()`. An operations dashboard should be
readable by someone who is not cleared to read the delivery.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.ingestion.security import redact
from app.core.ingestion.states import IngestionState


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class IngestionTelemetry:
    """One run, from the operator's point of view."""

    program: str
    source_name: str
    correlation_id: str

    state: IngestionState = IngestionState.QUEUED
    started_at: datetime = field(default_factory=_now)
    finished_at: Optional[datetime] = None

    #: What made this artefact this artefact. See AcquisitionResult.identity().
    artifact_identity: Optional[str] = None
    source_version: Optional[str] = None
    artifact_sha256: Optional[str] = None
    #: True when this run recognised an artefact already ingested and stopped.
    duplicate_of_run: Optional[str] = None

    records_received: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    observations_created: int = 0
    duplicates_suppressed: int = 0
    issues_created: int = 0
    issues_by_severity: Dict[str, int] = field(default_factory=dict)

    attempt: int = 1
    retry_count: int = 0
    retryable: bool = False
    error_reason: Optional[str] = None

    #: Rules that did not run because a COR decision is outstanding. Present so
    #: "no findings" is never mistaken for "nothing to find".
    blocked_rules: List[Dict[str, str]] = field(default_factory=list)
    #: Set when the delivery's columns differ from the locked map.
    schema_drift: bool = False
    stage_durations_ms: Dict[str, int] = field(default_factory=dict)

    def mark(self, state: IngestionState) -> None:
        self.state = state
        if state.name in ("COMPLETED", "COMPLETED_WITH_ISSUES",
                          "RETRYABLE_FAILURE", "PERMANENT_FAILURE"):
            self.finished_at = _now()

    def fail(self, reason: str, *, retryable: bool) -> None:
        """Record a failure. The reason is redacted before it is stored."""
        self.error_reason = redact(reason)[:2000]
        self.retryable = retryable
        self.mark(IngestionState.RETRYABLE_FAILURE if retryable
                  else IngestionState.PERMANENT_FAILURE)

    @property
    def duration_ms(self) -> Optional[int]:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = (self.finished_at.isoformat()
                               if self.finished_at else None)
        data["duration_ms"] = self.duration_ms
        return data

    def summary_line(self) -> str:
        """One line for a log. Deliberately contains no record content."""
        return (
            f"{self.program}/{self.source_name} {self.state.value} "
            f"received={self.records_received} accepted={self.records_accepted} "
            f"rejected={self.records_rejected} issues={self.issues_created} "
            f"duplicates_suppressed={self.duplicates_suppressed} "
            f"attempt={self.attempt} duration_ms={self.duration_ms}")
