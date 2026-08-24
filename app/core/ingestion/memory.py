"""Reference in-memory implementations of the ingestion ports.

FOR FIXTURES AND TESTS, NOT FOR A DEPLOYMENT
────────────────────────────────────────────
These exist so a program can exercise the whole pipeline — acquisition through
the issue handoff — without a database, and so the framework's own tests prove
the engine rather than a program's schema.

They are also the shortest readable statement of what a real repository has to
do, which is useful to the next program that implements one.

TEFCA's production path is unchanged: `rce/intake.py` still writes Area 1, and
nothing here is wired into it. That is deliberate. Proving a framework must not
mean re-ingesting 23,566 records of live evidence to see whether the abstraction
holds.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence

from app.core.ingestion.contracts import AcquisitionResult, ParsedBatch


class InMemoryArtifactStore:
    """Keeps the bytes in a dict, keyed by hash.

    Writes once per hash. A second preserve of the same bytes returns the same
    uri without overwriting, which is the behaviour a real immutable store must
    also have — re-preserving an artefact must never be able to change it.
    """

    def __init__(self) -> None:
        self.artifacts: Dict[str, bytes] = {}
        self.filenames: Dict[str, str] = {}

    async def preserve(self, raw: bytes, *, sha256: str, filename: str) -> str:
        if sha256 not in self.artifacts:
            self.artifacts[sha256] = raw
            self.filenames[sha256] = filename
        return f"memory://{sha256}"

    async def exists(self, sha256: str) -> bool:
        return sha256 in self.artifacts


class InMemoryIngestionRepository:
    """Records runs, records and findings in memory."""

    def __init__(self) -> None:
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.by_identity: Dict[str, str] = {}
        self.records: Dict[str, List[Any]] = {}
        self.findings: Dict[str, List[Any]] = {}

    async def find_by_identity(self, identity: str) -> Optional[str]:
        return self.by_identity.get(identity)

    async def open_run(self, result: AcquisitionResult, identity: str) -> str:
        run_id = str(uuid.uuid4())
        self.runs[run_id] = {
            "identity": identity,
            "source": result.descriptor.source_name,
            "program": result.descriptor.program,
            "sha256": result.sha256,
            "version": result.version_ref().effective_dataset_version,
            "state": "OPEN",
        }
        # Registered as soon as the run opens, so a concurrent second run sees
        # the artefact as taken rather than racing to ingest it twice.
        self.by_identity[identity] = run_id
        self.records[run_id] = []
        self.findings[run_id] = []
        return run_id

    async def record_records(self, run_id: str, batch: ParsedBatch) -> int:
        self.records[run_id].extend(batch.records)
        return len(batch.records)

    async def record_findings(self, run_id: str,
                              findings: Sequence[Any]) -> int:
        self.findings[run_id].extend(findings)
        return len(findings)

    async def close_run(self, run_id: str, state: str,
                        telemetry: Dict[str, Any]) -> None:
        self.runs[run_id]["state"] = state
        self.runs[run_id]["telemetry"] = telemetry


class RecordingAuditSink:
    """Collects audit events so a test can assert what was emitted."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def emit(self, event: str, detail: Dict[str, Any]) -> None:
        self.events.append({"event": event, **detail})

    def names(self) -> List[str]:
        return [e["event"] for e in self.events]
