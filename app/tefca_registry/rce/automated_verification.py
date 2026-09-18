"""Automated verification COVERAGE — external-source lookups for every eligible
entity of a delivery, run automatically and in batches, separate from the
analyst-review sample.

WHY THIS IS A NEW, SEPARATE MODULE AND NOT A CHANGE TO `arc_pipeline.verify_and_classify`
────────────────────────────────────────────────────────────────────────────
`delivery_runner.py` carries a long "WHY VERIFICATION IS NOT RUN HERE ANY MORE"
section: an earlier version of this system DID auto-verify a "seed" of promoted
entities inside the delivery job, and independent review reverted it for three
reasons:

    1. `verify_and_classify` is not idempotent — every call mints a NEW
       `ReviewRecord`, so a retried/re-run stage doubled the review population.
    2. The auto-verified seed was not the approved statistical sample, so it
       created a second, unofficial review population indistinguishable from
       the real one.
    3. It spent real, rate-limited Government source quota (NPPES/PECOS/SAM/
       LEIE) on entities nobody had decided needed review.

This module deliberately keeps reasons 1 and 2 solved by construction: it never
allocates a `review_id`, never writes a `ReviewRecord`, never runs the B1-B4
classifier, and never touches `qhin_sampling`. It answers a DIFFERENT question
than the review cycle does — "has this delivery's population been looked up in
each source at all" (coverage), not "which entities does an analyst work"
(the sample) — and writes only to the evidence tables `verification_coverage.py`
already reads: `tefca_dimension_evidence` (per source, per entity) via the same
`EvidenceService`/`evidence_rows_for_persistence` pipeline `verify_and_classify`
uses, so no new counting logic is needed on the read side.

Reason 3 is NOT solved by this module and cannot be solved by code alone: an
automatic, unconditional pass over every eligible entity of every delivery,
scaled to a 25,000-record delivery, means 25,000 real calls to NPPES etc. per
delivery, with no human decision point. `ENABLE_AUTOMATED_VERIFICATION_COVERAGE`
(default OFF) exists so that turning this on in any given deployment is an
explicit, reviewable configuration decision, not a silent code change.

IDEMPOTENCY
───────────
An entity counts as already covered the moment it has ANY `tefca_dimension_evidence`
row (from this module, from a review cycle, from anywhere) — the exact
"attempted" definition `verification_coverage.py` already uses. A retried or
repeated batch call for the same delivery therefore only ever processes the
entities that do not have one yet, converges to zero remaining work, and never
re-spends quota on an entity already looked up.

BATCHING AND RESUMABILITY
──────────────────────────
`run_coverage_batch` processes at most `batch_size` not-yet-covered entities
per call and returns how many remain. It commits once per call (the same
pattern `review_cycle.create_review_cycle` uses for its own batches), so a
crash mid-delivery loses at most one in-flight entity's evidence, not the
whole delivery's progress — the next call picks up wherever the "not yet
covered" query says work remains. `delivery_scheduler.py`'s poller shape
(claim one unit of work, commit, return) is reused directly: see
`_coverage_tick` below and its registration in `start_delivery_scheduler`.
A single delivery of 25,000 eligible entities is therefore many ticks, each
bounded, never one long-held transaction or a request the browser waits on.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

logger = logging.getLogger(__name__)

#: Off by default — see the module docstring, reason 3. Set to a truthy value
#: ("1", "true", "yes") to let the scheduler tick actually run lookups.
ENV_FLAG = "ENABLE_AUTOMATED_VERIFICATION_COVERAGE"

#: Entities looked up per call. Configurable so a deployment can trade batch
#: latency against connector burst load; see the module docstring.
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 500

#: The `source` stamped on evidence rows this module writes, so a later
#: reader (or an operator debugging quota usage) can tell automated coverage
#: apart from a review-cycle's sample run. Purely descriptive — it changes
#: nothing about how `verification_coverage.py` counts the row.
COVERAGE_SOURCE_LABEL = "automated_coverage"

#: The controlled terminal-outcome vocabulary this module classifies every
#: attempted (entity, source) pair into. Never left unclassified.
OUTCOME_VERIFIED = "VERIFIED"
OUTCOME_VERIFIED_WITH_DIFFERENCES = "VERIFIED_WITH_DIFFERENCES"
OUTCOME_NOT_FOUND = "NOT_FOUND"
OUTCOME_REVIEW_REQUIRED = "REVIEW_REQUIRED"
OUTCOME_RETRY_PENDING = "RETRY_PENDING"
OUTCOME_FAILED = "FAILED"
OUTCOME_UNAVAILABLE = "UNAVAILABLE"
OUTCOME_NOT_ELIGIBLE = "NOT_ELIGIBLE"

TERMINAL_OUTCOMES = frozenset({
    OUTCOME_VERIFIED, OUTCOME_VERIFIED_WITH_DIFFERENCES, OUTCOME_NOT_FOUND,
    OUTCOME_REVIEW_REQUIRED, OUTCOME_FAILED, OUTCOME_UNAVAILABLE,
    OUTCOME_NOT_ELIGIBLE,
})
#: RETRY_PENDING is the one non-terminal outcome: it means "try again",
#: not "done". Kept out of TERMINAL_OUTCOMES so a caller can tell the two
#: apart without inspecting the string.

#: Dimension-level disposition -> this module's per-source outcome. Mirrors
#: `verification_coverage.py`'s own `_DIMENSION_DISPOSITION`, which this
#: module's evidence rows are read back through — the mapping here decides
#: what to WRITE, that module's mapping decides what a read counts it as, and
#: the two must agree or a source's coverage card would read one outcome
#: while this module believed it recorded another.
_DISPOSITION_TO_OUTCOME = {
    "PASS": OUTCOME_VERIFIED,
    "CORROBORATED": OUTCOME_VERIFIED,
    "NOT_FOUND": OUTCOME_NOT_FOUND,
    "UNAVAILABLE": OUTCOME_UNAVAILABLE,
    "FAIL": OUTCOME_VERIFIED_WITH_DIFFERENCES,
    "CONFLICT": OUTCOME_VERIFIED_WITH_DIFFERENCES,
}

_ELIGIBLE_NOT_COVERED_SQL = """
    SELECT r.rce_org_oid, r.canonical_entity_id
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND r.rce_org_oid IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM tefca_dimension_evidence d
          WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
      )
    ORDER BY r.rce_org_oid
    LIMIT :limit
"""

_ELIGIBLE_COUNT_SQL = """
    SELECT count(DISTINCT r.canonical_entity_id)
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
"""

_NOT_COVERED_COUNT_SQL = """
    SELECT count(DISTINCT r.canonical_entity_id)
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM tefca_dimension_evidence d
          WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
      )
"""


def automated_coverage_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


async def coverage_progress(db, intake_id) -> Dict[str, int]:
    """Read-only: how many of the delivery's eligible entities still need a
    first coverage attempt. Never writes."""
    eligible = int((await db.execute(
        text(_ELIGIBLE_COUNT_SQL), {"intake_id": str(intake_id)})).scalar() or 0)
    remaining = int((await db.execute(
        text(_NOT_COVERED_COUNT_SQL), {"intake_id": str(intake_id)})).scalar() or 0)
    return {"eligible": eligible, "remaining": remaining, "covered": eligible - remaining}


def _classify_entity_outcome(evidence: Dict[str, Any]) -> str:
    """One outcome for the ENTITY as a whole, from its dimension dispositions.

    Per-SOURCE outcomes are recovered later by `verification_coverage.py`
    directly from the `tefca_dimension_evidence` rows this call persists —
    this function only decides the single summary outcome recorded for audit
    / retry purposes (see `record_coverage_attempt`).

    NOT_ELIGIBLE when every dimension came back NOT_APPLICABLE (the entity has
    nothing this pipeline can check — e.g. no NPI and no other checkable
    identifier). UNAVAILABLE when every applicable dimension's evidence could
    not be obtained (a source outage, not a finding against the entity).
    Otherwise the worst applicable disposition wins, in the order a reviewer
    would care about it: a conflict/mismatch outranks a clean not-found, which
    outranks a plain pass.
    """
    dims = evidence.get("dimensions", [])
    applicable = [d for d in dims if (d.get("applicability") or "").upper() != "NOT_APPLICABLE"]
    if not applicable:
        return OUTCOME_NOT_ELIGIBLE

    dispositions = [(d.get("disposition") or "").upper() for d in applicable]
    if dispositions and all(d in ("UNAVAILABLE", "") for d in dispositions):
        return OUTCOME_UNAVAILABLE
    if any(d in ("FAIL", "CONFLICT") for d in dispositions):
        return OUTCOME_VERIFIED_WITH_DIFFERENCES
    if all(d == "NOT_FOUND" for d in dispositions):
        return OUTCOME_NOT_FOUND
    if any(d == "NOT_FOUND" for d in dispositions):
        # Found in some sources, not others — a real difference worth an
        # analyst's eyes, not a silent pass.
        return OUTCOME_VERIFIED_WITH_DIFFERENCES
    if all(d in ("PASS", "CORROBORATED") for d in dispositions):
        return OUTCOME_VERIFIED
    return OUTCOME_REVIEW_REQUIRED  # an unrecognised disposition mix - never unclassified


async def _entity_row_shape(db, entity_uuid) -> Optional[Any]:
    from app.tefca_registry import models as reg

    return await db.get(reg.TefcaRegEntity, entity_uuid)


async def record_coverage_attempt(db, *, ref: str, canonical_entity_id,
                                  intake_id, actor: str = "SYSTEM",
                                  local_store=None) -> Dict[str, Any]:
    """Look up ONE entity across every configured source and persist the
    evidence. Never raises for a connector-level failure — a bad lookup is
    recorded as RETRY_PENDING/FAILED/UNAVAILABLE, never an unhandled
    exception that would stop the rest of the batch (see `run_coverage_batch`,
    which still wraps this in try/except as a second line of defence for a
    genuinely unexpected error, e.g. a programming bug in this function
    itself)."""
    from app.Tefca.entity_resolution import resolve_entity
    from app.Tefca.evidence_service import EvidenceService, evidence_rows_for_persistence
    from app.Tefca.models import TEFCADimensionEvidence
    from app.Tefca.ppef_store import make_local_store
    from app.tefca_registry.rce import verification_findings as vf

    entity = await resolve_entity(db, ref)
    if entity is None:
        return {"ref": ref, "outcome": OUTCOME_NOT_ELIGIBLE,
                "reason": "entity did not resolve for this reference"}

    entity_uuid = entity.get("_registry_entity_id") or canonical_entity_id
    service = EvidenceService(local_store=local_store or make_local_store(db))

    try:
        evidence = await service.build_evidence(entity)
    except Exception as exc:  # noqa: BLE001 - a connector outage must not crash the batch
        logger.warning("automated coverage: build_evidence failed for %s: %s: %s",
                       ref, type(exc).__name__, exc)
        return {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
                "outcome": OUTCOME_RETRY_PENDING, "reason": f"{type(exc).__name__}: {exc}"}

    outcome = _classify_entity_outcome(evidence)

    rows = evidence_rows_for_persistence(str(entity_uuid or entity.get("id")), None, evidence)
    for row in rows:
        row["review_cycle_id"] = None
        db.add(TEFCADimensionEvidence(**row))

    if entity_uuid is not None:
        try:
            from app.services.npi_validator import is_valid_npi  # noqa: F401 - availability check only
            npi = None
            from app.Tefca import rce_fields
            npi = rce_fields.rce_npi(entity)
            await vf.record_from_evidence(db, entity_id=entity_uuid, npi=npi, evidence=evidence)
        except Exception as exc:  # noqa: BLE001 - the issue ledger must not fail coverage
            logger.error("automated coverage: NPI outcome not recorded for %s: %s",
                        entity_uuid, type(exc).__name__, exc_info=True)

    if outcome == OUTCOME_REVIEW_REQUIRED and entity_uuid is not None:
        row = await _entity_row_shape(db, entity_uuid)
        if row is not None and row.verification_status != "in_review":
            row.verification_status = "in_review"
            row.updated_at = datetime.utcnow()

    await db.flush()
    return {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
            "outcome": outcome, "dimensions_recorded": len(rows)}


async def run_coverage_batch(db, intake_id, *, batch_size: int = DEFAULT_BATCH_SIZE,
                             actor: str = "SYSTEM") -> Dict[str, Any]:
    """Process up to `batch_size` not-yet-covered eligible entities of one
    delivery. Commits once at the end of the call (the batch is the unit of
    atomicity, matching `review_cycle.create_review_cycle`'s own batching) and
    returns how many entities remain. Call again until `remaining` is 0 — the
    same "call again until remaining is 0" contract the review cycle uses, so
    a caller (the scheduler tick, or an operator retrying manually) does not
    need two different conventions."""
    from app.Tefca.ppef_store import make_local_store

    batch_size = max(1, min(int(batch_size or DEFAULT_BATCH_SIZE), MAX_BATCH_SIZE))
    rows = (await db.execute(
        text(_ELIGIBLE_NOT_COVERED_SQL),
        {"intake_id": str(intake_id), "limit": batch_size})).all()

    local_store = make_local_store(db)
    outcomes: List[Dict[str, Any]] = []
    for ref, canonical_entity_id in rows:
        try:
            result = await record_coverage_attempt(
                db, ref=ref, canonical_entity_id=canonical_entity_id,
                intake_id=intake_id, actor=actor, local_store=local_store)
        except Exception as exc:  # noqa: BLE001 - one entity's bug must not lose the batch
            logger.error("automated coverage: unhandled error for %s: %s",
                        ref, type(exc).__name__, exc_info=True)
            result = {"ref": ref, "outcome": OUTCOME_RETRY_PENDING,
                      "reason": f"unhandled {type(exc).__name__}"}
        outcomes.append(result)

    await db.commit()

    progress = await coverage_progress(db, intake_id)
    by_outcome: Dict[str, int] = {}
    for o in outcomes:
        by_outcome[o["outcome"]] = by_outcome.get(o["outcome"], 0) + 1

    return {
        "intake_id": str(intake_id),
        "processed_this_call": len(outcomes),
        "by_outcome": by_outcome,
        "outcomes": outcomes,
        **progress,
        "complete": progress["remaining"] == 0,
        "batch_size": batch_size,
    }


# ── scheduler integration ────────────────────────────────────────────────────

async def _coverage_tick():
    """One scheduler tick: find ONE delivery with eligible-but-not-covered
    entities and process one bounded batch for it.

    Mirrors `delivery_scheduler._poll_tick`'s shape exactly (own session, own
    try/except so a tick that raises cannot stop the scheduler) rather than
    inventing a second convention for background work in this codebase.
    Gated behind `automated_coverage_enabled()` so importing/registering this
    module is inert until a deployment explicitly turns it on.
    """
    if not automated_coverage_enabled():
        return
    from app.core.database import async_session_maker

    try:
        async with async_session_maker() as db:
            intake_id = await _next_delivery_needing_coverage(db)
            if intake_id is None:
                return
            result = await run_coverage_batch(db, intake_id)
            logger.info("automated coverage tick for %s: %s/%s covered, %s remaining",
                       intake_id, result["covered"], result["eligible"], result["remaining"])
    except Exception as exc:  # noqa: BLE001 - a tick that raises must not stop the scheduler
        logger.error("automated coverage tick error: %s", exc, exc_info=True)


_ONE_DELIVERY_NEEDING_COVERAGE_SQL = """
    SELECT DISTINCT r.source_intake_id
    FROM rce_curated_records r
    JOIN rce_delivery_jobs j ON j.source_intake_id = r.source_intake_id
    WHERE r.canonical_entity_id IS NOT NULL
      AND j.state = 'SUCCEEDED'
      AND EXISTS (
          SELECT 1 FROM rce_curated_records r2
          WHERE r2.source_intake_id = r.source_intake_id
            AND r2.canonical_entity_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM tefca_dimension_evidence d
                WHERE d.entity_id = CAST(r2.canonical_entity_id AS TEXT))
      )
    ORDER BY r.source_intake_id
    LIMIT 1
"""


async def _next_delivery_needing_coverage(db):
    row = (await db.execute(text(_ONE_DELIVERY_NEEDING_COVERAGE_SQL))).first()
    return row[0] if row else None


def register_with_scheduler(scheduler) -> None:
    """Add the coverage tick to an already-constructed APScheduler instance.
    Called from `delivery_scheduler.start_delivery_scheduler`; kept as its own
    function so this module has no import-time dependency on APScheduler at
    all (it is perfectly usable — e.g. from a management command or a test —
    without a scheduler in the process)."""
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        _coverage_tick, IntervalTrigger(seconds=15),
        id="rce_automated_verification_coverage",
        name="Automated verification coverage (external sources, all eligible entities)",
        coalesce=True, misfire_grace_time=120, replace_existing=True, max_instances=1)
