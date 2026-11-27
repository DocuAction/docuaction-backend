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

IDENTITY FIRST (2026-11-25)
───────────────────────────
Evidence is stored per ENTITY, and `resolve_entity` resolves a delivered
reference globally. Before either scope asks a source or adds anything to the
session, the record must pass `verification_findings.validated_delivery_context`
(the reference resolves to the queued entity; exactly one delivered record of
this delivery carries it and links that entity; the probes would use the NPI
that record delivered). A refused record gets NOTHING: no evidence row, no
ledger finding, no identifier or entity status change. It is reported
(`IDENTITY_REFUSED` in the batch `outcomes`, counted in `identity_refused`)
and stays not-covered. Because a refusal leaves no row behind, the batch walks
past refused entities instead of being stopped by them, and the scheduler
puts a delivery whose only remaining entities are refusals on a process-local
hold instead of ticking it forever — see `run_coverage_batch` and
`_identity_holds`. No attempt marker is stored; a durable one would need
schema and is not introduced here.

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
import re
import threading
import time
import uuid
from dataclasses import dataclass, replace as _dc_replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

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

#: The attempt was REFUSED at identity validation, before any source was asked
#: (2026-11-25). Nothing is gathered and nothing is persisted for it: no
#: evidence row, no ledger finding, no identifier or entity status change. It
#: is not a statement about the entity and not a terminal classification, so
#: it is kept out of TERMINAL_OUTCOMES; the entity stays not-covered and the
#: refusal is counted in the batch result (`identity_refused`).
OUTCOME_IDENTITY_REFUSED = "IDENTITY_REFUSED"

#: A NAME-ONLY screen of one delivered record's submitted name was recorded RECORD-SCOPED
#: (`rce_record_sam_screenings`; owner, 2026-11-27). Deliberately NOT a terminal ENTITY outcome: nothing was
#: written to the entity evidence store, so the ENTITY stays not-covered for the edition and no other record's
#: NPI-based screening is satisfied. The record's own result is in `name_only_outcome`.
OUTCOME_NAME_ONLY_RECORDED = "NAME_ONLY_RECORDED"

#: How many identity refusals one `run_coverage_batch` call may step over
#: while looking for entities it is allowed to look up. A refusal persists
#: nothing, so a refused entity stays in the not-covered set and is seen again
#: by every call; without stepping over them, `batch_size` refused entities at
#: the front of the list would stop the delivery's coverage for good. The cap
#: bounds the per-call cost (each refusal is a few local reads, no source
#: call); a call that reaches it says so and returns the cursor to continue.
MAX_IDENTITY_REFUSALS_PER_CALL = 1000

#: Minimum rows fetched per candidate query inside one batch call.
CANDIDATE_PAGE_SIZE = 100

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

#: DISTINCT ON (2026-10-30): several curated records can share one canonical
#: entity; without it one batch would look that entity up once per record and
#: persist duplicate generations within a single batch — the within-batch
#: cousin of the cross-batch overlap `_try_advisory_batch_lock` serializes.
_ELIGIBLE_NOT_COVERED_SQL = """
    SELECT DISTINCT ON (r.canonical_entity_id)
           r.rce_org_oid, r.canonical_entity_id
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND r.rce_org_oid IS NOT NULL
      AND (CAST(:after AS uuid) IS NULL OR r.canonical_entity_id > CAST(:after AS uuid))
      AND NOT EXISTS (
          SELECT 1 FROM tefca_dimension_evidence d
          WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
      )
    ORDER BY r.canonical_entity_id, r.rce_org_oid
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


#: Batch scopes. FULL is the existing behavior: entities with no evidence at
#: all get a full all-source build. SAM_EXTRACT covers entities that already
#: HAVE evidence but lack a SAM_EXTRACT_SUPPLEMENTARY row (the extract leg
#: was wired in after most of a delivery's evidence was built), by running
#: ONLY the extract screen and appending ONLY its row — never re-asking the
#: other sources, so no quota is re-spent on an already-covered entity.
SCOPE_FULL = "full"
SCOPE_SAM_EXTRACT = "sam_extract"
#: EDITION-BOUND extract screen (2026-11-26). Methodology v1.4 §55 / IN-04 / step 4: screen against a DATED
#: extract, "run authorized checks against fixed source versions" and "append attributable outcomes". The plain
#: `sam_extract` scope skips any entity that has ANY extract row, so an entity screened against an October edition
#: is never screened again and its October answer is what the policy reads. This scope selects every eligible
#: entity of the delivery that has no extract row FOR ONE PINNED EDITION (the extract's SHA-256), screens it, and
#: APPENDS a row carrying that edition's filename, publication date, hash and matching basis. Older rows are never
#: touched. The run is pinned: a batch refuses to start, and a single screen is not persisted, when the edition in
#: memory is not the pinned one - an edition cannot change part-way through a population.
SCOPE_SAM_EXTRACT_EDITION = "sam_extract_edition"
SCOPES = (SCOPE_FULL, SCOPE_SAM_EXTRACT, SCOPE_SAM_EXTRACT_EDITION)

EXTRACT_SOURCE = "SAM_EXTRACT_SUPPLEMENTARY"

#: DISTINCT ON: several curated records can share one canonical entity, and a
#: non-distinct candidate list would screen that entity once per record in
#: the SAME batch — the within-batch cousin of the cross-batch duplication
#: `_try_advisory_batch_lock` prevents (both observed on the 2026-10
#: delivery, where overlapping batches wrote 2–3 duplicate generations for
#: ~1,007 entities).
_SAM_EXTRACT_NOT_COVERED_SQL = """
    SELECT DISTINCT ON (r.canonical_entity_id)
           r.rce_org_oid, r.canonical_entity_id
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND r.rce_org_oid IS NOT NULL
      AND (CAST(:after AS uuid) IS NULL OR r.canonical_entity_id > CAST(:after AS uuid))
      AND NOT EXISTS (
          SELECT 1 FROM tefca_dimension_evidence d
          WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
            AND d.source = 'SAM_EXTRACT_SUPPLEMENTARY'
      )
    ORDER BY r.canonical_entity_id, r.rce_org_oid
    LIMIT :limit
"""

_SAM_EXTRACT_NOT_COVERED_COUNT_SQL = """
    SELECT count(DISTINCT r.canonical_entity_id)
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM tefca_dimension_evidence d
          WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
            AND d.source = 'SAM_EXTRACT_SUPPLEMENTARY'
      )
"""

#: SCOPE_SAM_EXTRACT_EDITION: not covered = no extract row attributed to THE PINNED edition. The edition is
#: identified by the SHA-256 of the extract CSV the row itself records (`original_values.extract_sha256`), never by
#: a date the cycle or the batch assumes.
_SAM_EXTRACT_EDITION_NOT_COVERED_SQL = """
    SELECT DISTINCT ON (r.canonical_entity_id)
           r.rce_org_oid, r.canonical_entity_id
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND r.rce_org_oid IS NOT NULL
      AND (CAST(:after AS uuid) IS NULL OR r.canonical_entity_id > CAST(:after AS uuid))
      AND NOT EXISTS (
          SELECT 1 FROM tefca_dimension_evidence d
          WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
            AND d.source = 'SAM_EXTRACT_SUPPLEMENTARY'
            AND d.original_values ->> 'extract_sha256' = :edition_sha256
      )
    ORDER BY r.canonical_entity_id, r.rce_org_oid
    LIMIT :limit
"""

_SAM_EXTRACT_EDITION_NOT_COVERED_COUNT_SQL = """
    SELECT count(DISTINCT r.canonical_entity_id)
    FROM rce_curated_records r
    WHERE r.source_intake_id = CAST(:intake_id AS uuid)
      AND r.canonical_entity_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM tefca_dimension_evidence d
          WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT)
            AND d.source = 'SAM_EXTRACT_SUPPLEMENTARY'
            AND d.original_values ->> 'extract_sha256' = :edition_sha256
      )
"""

#: With `name_only_submitted`: the edition candidate set, minus a (reference, entity) whose delivered record already
#: has a record-scoped name-only screen for THIS edition - otherwise such a record would be selected again on every
#: call, because a name-only screen deliberately never marks the ENTITY covered. Only the record that reference
#: anchors is excluded: another reference of the same entity (a sibling record) is still selected and screened on
#: its own basis.
_SAM_EXTRACT_EDITION_NAME_ONLY_NOT_COVERED_SQL = _SAM_EXTRACT_EDITION_NOT_COVERED_SQL.replace(
    "    ORDER BY r.canonical_entity_id, r.rce_org_oid",
    """      AND NOT EXISTS (
          SELECT 1 FROM rce_record_sam_screenings s
          JOIN rce_curated_records r2 ON r2.source_record_id = s.source_record_id
          WHERE s.source_intake_id = r.source_intake_id
            AND s.extract_sha256 = :edition_sha256
            AND r2.source_intake_id = r.source_intake_id
            AND r2.canonical_entity_id = r.canonical_entity_id
            AND r2.rce_org_oid = r.rce_org_oid
      )
    ORDER BY r.canonical_entity_id, r.rce_org_oid""")
assert _SAM_EXTRACT_EDITION_NAME_ONLY_NOT_COVERED_SQL != _SAM_EXTRACT_EDITION_NOT_COVERED_SQL

#: Record-scoped name-only screens of one delivery for one edition, by outcome (counts only).
_NAME_ONLY_RECORDED_COUNT_SQL = """
    SELECT outcome, count(*) FROM rce_record_sam_screenings
    WHERE source_intake_id = CAST(:intake_id AS uuid) AND extract_sha256 = :edition_sha256
    GROUP BY outcome
"""

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: The ONE unsuccessful screen that is a fact about the entity rather than the source:
#: `SAMExtractConnector.screen` found neither a usable NPI nor an organisation name. Only this is recorded as
#: "attempted, not screened" against the pinned edition; every other unsuccessful screen is retried.
NOT_SCREENABLE_ENTITY_PREFIX = "no_usable_identifier"


class SamExtractEditionRefused(RuntimeError):
    """An edition-bound batch cannot run as asked: no valid pinned edition, the extract cannot screen now, or the
    edition in memory is not the pinned one. Refused before anything is selected or written."""


async def pinned_edition_problem(edition_sha256: Optional[str]) -> Optional[str]:
    """Why an edition-bound screen against `edition_sha256` cannot run now, or None. Loads the extract the way
    a screen would (same flag, load and freshness checks), then compares the loaded edition's hash."""
    from app.Tefca import sam_extract as se

    pinned = (edition_sha256 or "").strip().lower()
    if not _SHA256_RE.match(pinned):
        return "edition_sha256 must be the 64-hex SHA-256 of the extract edition to pin"
    not_ready = await se.screening_not_ready_reason()
    if not_ready:
        return f"the SAM public extract cannot screen now: {not_ready}"
    loaded = se.loaded_edition()
    if (loaded.get("extract_sha256") or "").lower() != pinned:
        return (f"the extract edition in memory ({loaded.get('publication_date')}, sha256 "
                f"{(loaded.get('extract_sha256') or 'none')[:12]}...) is not the pinned edition "
                f"({pinned[:12]}...); nothing was screened. Pin the loaded edition or wait for the pinned one.")
    return None


class NameOnlyStoreNotApplied(RuntimeError):
    """A name-only batch was requested but the record-scoped store (`rce_record_sam_screenings`, migration
    20261127_rce_record_sam_screen) does not exist. Refused before anything is selected or written."""


async def _assert_name_only_store(db) -> None:
    if (await db.execute(text("SELECT to_regclass('public.rce_record_sam_screenings')"))).scalar() is None:
        raise NameOnlyStoreNotApplied(
            "NAME_ONLY_STORE_NOT_APPLIED: rce_record_sam_screenings does not exist; the "
            "20261127_rce_record_sam_screen migration has not been applied through the governed release path. "
            "Nothing was screened or written.")


class SamExtractScreeningDisabled(RuntimeError):
    """A sam_extract-scope batch was requested while ENABLE_SAM_EXTRACT_SCREENING
    is off. Refused up front: with the flag off the connector answers
    UNAVAILABLE without looking anything up, so a mass run would append one
    useless UNAVAILABLE row per entity and cover nothing."""


def sam_extract_screening_enabled() -> bool:
    """Env-flag gate for SAM-extract screening (see app.Tefca.sam_extract)."""
    from app.Tefca import sam_extract as se
    return os.environ.get(se.ENV_FLAG, "").strip().lower() in ("1", "true", "yes")


def automated_coverage_enabled() -> bool:
    """Env-flag gate for the automated coverage scheduler."""
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


async def coverage_progress(db, intake_id, *, scope: str = SCOPE_FULL,
                            edition_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Read-only: how many of the delivery's eligible entities still need a
    first coverage attempt (for `scope="sam_extract"`: a first SAM-extract
    row; for `scope="sam_extract_edition"`: a row for the pinned edition). Never writes."""
    params: Dict[str, Any] = {"intake_id": str(intake_id)}
    if scope == SCOPE_SAM_EXTRACT_EDITION:
        count_sql = _SAM_EXTRACT_EDITION_NOT_COVERED_COUNT_SQL
        params["edition_sha256"] = (edition_sha256 or "").strip().lower()
    elif scope == SCOPE_SAM_EXTRACT:
        count_sql = _SAM_EXTRACT_NOT_COVERED_COUNT_SQL
    else:
        count_sql = _NOT_COVERED_COUNT_SQL
    eligible = int((await db.execute(
        text(_ELIGIBLE_COUNT_SQL), {"intake_id": str(intake_id)})).scalar() or 0)
    remaining = int((await db.execute(text(count_sql), params)).scalar() or 0)
    out: Dict[str, Any] = {"eligible": eligible, "remaining": remaining, "covered": eligible - remaining}
    if scope == SCOPE_SAM_EXTRACT_EDITION:
        out["edition_sha256"] = params["edition_sha256"]
    return out


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
    if any(d == "REVIEW" for d in dispositions):
        # A dimension explicitly asking for an analyst (deactivated NPI,
        # NPPES identity name conflict) must reach in_review even when some
        # other dimension is NOT_FOUND — the NOT_FOUND branches below would
        # otherwise swallow it into VERIFIED_WITH_DIFFERENCES.
        return OUTCOME_REVIEW_REQUIRED
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
    """The registry-entity ORM row for a canonical entity id, or None."""
    from app.tefca_registry import models as reg

    return await db.get(reg.TefcaRegEntity, entity_uuid)


def _identity_refused(ref: str, canonical_entity_id, reason: Optional[str]) -> Dict[str, Any]:
    """The result of an attempt refused at identity validation. `entity_id`
    is the QUEUED entity (the one the delivery's curated record links), never
    the entity the reference happens to resolve to now."""
    return {"ref": ref,
            "entity_id": str(canonical_entity_id) if canonical_entity_id else None,
            "outcome": OUTCOME_IDENTITY_REFUSED,
            "reason": reason or "identity validation refused this record",
            "persisted": False, "dimensions_recorded": 0}


def _not_resolved(ref: str, canonical_entity_id) -> Dict[str, Any]:
    """The result for a reference that does not resolve to a registry entity. Outcome and reason are what they
    always were (NOT_ELIGIBLE); `not_resolved` / `persisted` say that no source was asked and nothing was stored,
    so the batch can step over it like a refusal instead of counting it as a lookup that never happened."""
    return {"ref": ref, "entity_id": str(canonical_entity_id) if canonical_entity_id else None,
            "outcome": OUTCOME_NOT_ELIGIBLE, "reason": "entity did not resolve for this reference",
            "not_resolved": True, "persisted": False, "dimensions_recorded": 0}


async def _validate_attempt_identity(db, *, ref: str, canonical_entity_id, intake_id, entity):
    """IDENTITY FIRST, for both coverage scopes: (context, probe_npi, None)
    when the attempt may proceed, (None, None, refusal result) when it may
    not. Runs before any source is asked and before anything is added to the
    session, so a refusal leaves nothing behind.

    `resolve_entity` resolves a reference GLOBALLY and evidence is stored
    under the resolved entity, so all of `validated_delivery_context` must
    hold first: the reference resolves to the QUEUED entity, exactly one
    delivered record of THIS delivery carries it and links that entity, and
    the NPI the probes will be asked about is the NPI that record delivered.
    `resolved_probe_npi` makes the last comparison cover every probe family
    (the RCE NPI and the FHIR NPI identifiers must be the same NPI)."""
    from app.tefca_registry.rce import verification_findings as vf

    probe_npi, conflict = vf.resolved_probe_npi(entity)
    if conflict:
        return None, None, _identity_refused(ref, canonical_entity_id, conflict)
    context, refusal = await vf.validated_delivery_context(
        db, intake_id=intake_id, ref=ref, queued_entity_id=canonical_entity_id,
        entity=entity, probe_npi=probe_npi)
    if context is None:
        return None, None, _identity_refused(ref, canonical_entity_id, refusal)
    return context, probe_npi, None


async def record_coverage_attempt(db, *, ref: str, canonical_entity_id,
                                  intake_id, actor: str = "SYSTEM",
                                  local_store=None) -> Dict[str, Any]:
    """Look up ONE entity across every configured source and persist the
    evidence. Never raises for a connector-level failure — a bad lookup is
    recorded as RETRY_PENDING/FAILED/UNAVAILABLE, never an unhandled
    exception that would stop the rest of the batch (see `run_coverage_batch`,
    which still wraps this in try/except as a second line of defence for a
    genuinely unexpected error, e.g. a programming bug in this function
    itself).

    IDENTITY FIRST (2026-11-25): when the shared identity validation refuses
    the record, no source is asked and NOTHING is persisted — no evidence
    row, no ledger finding, no identifier or entity status change. The result
    is `IDENTITY_REFUSED` with the reason, and `run_coverage_batch` counts
    it. The entity stays not-covered; the batch steps over it rather than
    writing evidence to make it look attempted (see `run_coverage_batch`)."""
    from app.Tefca.entity_resolution import resolve_entity
    from app.Tefca.evidence_service import EvidenceService, evidence_rows_for_persistence
    from app.Tefca.models import TEFCADimensionEvidence
    from app.Tefca.ppef_store import make_local_store
    from app.tefca_registry.rce import verification_findings as vf

    entity = await resolve_entity(db, ref)
    if entity is None:
        return _not_resolved(ref, canonical_entity_id)

    context, probe_npi, refused = await _validate_attempt_identity(
        db, ref=ref, canonical_entity_id=canonical_entity_id, intake_id=intake_id, entity=entity)
    if refused is not None:
        return refused
    # Validated: the reference resolves to the queued entity, so the two ids
    # are the same registry entity.
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

    ledger_failure = None
    if entity_uuid is not None:
        try:
            from app.services.npi_validator import is_valid_npi  # noqa: F401 - availability check only
            # On the delivered record the shared validation established (the writer owns the savepoint around
            # its identifier change and insert).
            await vf.record_from_evidence(db, entity_id=entity_uuid, npi=probe_npi or None, evidence=evidence,
                                          source_record_id=context["source_record_id"], intake_id=intake_id)
        except Exception as exc:  # noqa: BLE001 - the issue ledger must not fail coverage
            ledger_failure = type(exc).__name__
            logger.error("automated coverage: NPI outcome not recorded for %s: %s",
                        entity_uuid, type(exc).__name__, exc_info=True)
        try:
            extract_values = vf.sam_extract_candidate_values(evidence)
            if extract_values is not None:
                from app.Tefca import rce_fields
                # Savepoint: a SQL failure inside the ledger write must roll
                # back to here, not abort the whole batch transaction — the
                # except below can only "not fail coverage" if the session is
                # still usable afterwards.
                async with db.begin_nested():
                    await vf.record_sam_extract_candidate(
                        db, entity_id=entity_uuid, npi=rce_fields.rce_npi(entity),
                        org_name=(rce_fields.rce_value(entity, "name")
                                  or entity.get("name") or "").strip() or None,
                        extract=extract_values, intake_id=intake_id,
                        # The delivered record identity was validated for, not
                        # "the entity's latest record in this delivery".
                        source_record_id=context["source_record_id"])
        except Exception as exc:  # noqa: BLE001 - the issue ledger must not fail coverage
            logger.error("automated coverage: SAM-extract candidate not recorded for %s: %s",
                         entity_uuid, type(exc).__name__, exc_info=True)

    if outcome == OUTCOME_REVIEW_REQUIRED and entity_uuid is not None:
        row = await _entity_row_shape(db, entity_uuid)
        if row is not None and row.verification_status != "in_review":
            row.verification_status = "in_review"
            row.updated_at = datetime.utcnow()

    await db.flush()
    result = {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
              "outcome": outcome, "dimensions_recorded": len(rows)}
    if ledger_failure:
        result["ledger_recorded"] = False                          # class name only, never message text
        result["ledger_error_class"] = ledger_failure
    return result


#: `screen_basis` of a screen of ONLY the delivered record's submitted name (owner, 2026-11-27).
SCREEN_BASIS_NAME_ONLY_SUBMITTED = "SUBMITTED_VALUES_NAME_ONLY"

#: Outcomes of a record-scoped name-only screen (`RceRecordSamScreening.outcome`).
NAME_ONLY_CANDIDATE = "CANDIDATE"
NAME_ONLY_NO_CANDIDATE = "NO_CANDIDATE"
NAME_ONLY_NOT_SCREENED = "NOT_SCREENED"


async def _name_only_submitted_context(db, *, ref: str, canonical_entity_id, intake_id, entity):
    """For an entity identity validation refused: the delivered-record context for a NAME-ONLY screen on
    submitted values, or None (the refusal stands).

    Eligibility is the CLASSIFIER's, exactly (owner, 2026-11-27):
      * blank NPI = `arc_pipeline._submitted_npi(record) == ""` (the delivered value, stripped). A record that
        submitted ANY non-blank value - a different NPI, "N/A", "PENDING" - keeps its refusal: the classifier
        treats it as a submitted NPI, and an NPI question is a person's to resolve;
      * the delivery's assessment date (`assessment_context.resolve_assessment_date`) is on or after
        `arc_pipeline.BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE`, so an earlier delivery stays exactly as it was.
    `validated_delivery_context` is run with NO probe NPI, so every other identity check still holds: the
    reference resolves to the QUEUED entity, exactly one delivered record of this delivery carries it and links that
    entity, and that record is readable. A record with no submitted name keeps its refusal too."""
    from app.tefca_registry.rce import arc_pipeline as ap
    from app.tefca_registry.rce import assessment_context
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import verification_findings as vf

    assessment = await assessment_context.resolve_assessment_date(db, intake_id)
    if assessment.value < ap.BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE:
        return None
    context, _refusal = await vf.validated_delivery_context(
        db, intake_id=intake_id, ref=ref, queued_entity_id=canonical_entity_id, entity=entity, probe_npi="")
    if context is None:
        return None
    record = await db.get(m.RceSourceRecord, context["source_record_id"])
    if record is None or ap._submitted_npi(record) != "":
        return None
    name = str(((getattr(record, "parsed", None) or {}).get("name")) or "").strip()
    if not name:
        return None
    return {**context, "delivered_name": name}


async def _record_name_only_screening(db, *, context, intake_id, entity_uuid, screen, edition: Dict[str, Any],
                                      outcome: str, candidate_count, not_screened_reason, issue_id,
                                      actor: str) -> None:
    """Insert the record-scoped result of one name-only screen. Nothing is written to the entity evidence store."""
    from app.core import request_context
    from app.tefca_registry.rce import models as m

    db.add(m.RceRecordSamScreening(
        id=uuid.uuid4(),
        source_intake_id=intake_id,
        source_record_id=context["source_record_id"],
        canonical_entity_id=entity_uuid,
        screen_basis=SCREEN_BASIS_NAME_ONLY_SUBMITTED,
        identity_confirmed=False,
        extract_sha256=(edition.get("extract_sha256") or "").lower(),
        extract_filename=edition.get("extract_filename"),
        publication_date=(str(edition.get("publication_date"))[:10] if edition.get("publication_date") else None),
        outcome=outcome,
        candidate_count=candidate_count,
        not_screened_reason=(not_screened_reason or None) and str(not_screened_reason)[:300],
        issue_id=issue_id,
        screened_by=(actor or "SYSTEM")[:320],                     # screened_at: the database clock
        correlation_id=request_context.correlation_id()[:64],
    ))
    await db.flush()


async def record_sam_extract_attempt(db, *, ref: str,
                                     canonical_entity_id,
                                     intake_id,
                                     required_edition_sha256: Optional[str] = None,
                                     name_only_submitted: bool = False,
                                     actor: str = "SYSTEM") -> Dict[str, Any]:
    """Run ONLY the SAM public-extract supplementary screen for one entity and
    append ONLY that source's evidence row. Never calls `build_evidence`, so
    no other source is re-asked and no quota is re-spent. The row is built by
    `sam_extract_evidence_items` — the exact code the full build uses — so the
    persisted item cannot drift from what a full generation would have written.

    Like `record_coverage_attempt`, a connector-level failure is recorded as
    an outcome (RETRY_PENDING), never raised out of the batch.

    IDENTITY FIRST (2026-11-25), exactly as in `record_coverage_attempt`: a
    record the shared identity validation refuses is not screened, gets no
    `SAM_EXTRACT_SUPPLEMENTARY` row, no candidate finding and no status
    change; the result is `IDENTITY_REFUSED`. A candidate finding is written
    on the delivered record the validation established."""
    from app.Tefca import rce_fields
    from app.Tefca.connectors import _extract_npi
    from app.Tefca.entity_resolution import resolve_entity
    from app.Tefca.evidence_assembly import sam_extract_evidence_items
    from app.Tefca.evidence_dimensions import Dimension
    from app.Tefca.evidence_service import evidence_rows_for_persistence
    from app.Tefca.models import TEFCADimensionEvidence
    from app.Tefca.sam_extract import SAMExtractConnector

    entity = await resolve_entity(db, ref)
    if entity is None:
        return _not_resolved(ref, canonical_entity_id)

    context, _probe_npi, refused = await _validate_attempt_identity(
        db, ref=ref, canonical_entity_id=canonical_entity_id, intake_id=intake_id, entity=entity)
    name_only = False
    if refused is not None:
        # NAME-ONLY ON SUBMITTED VALUES (owner, 2026-11-27): an explicit opt-in, edition-pinned only. Methodology
        # §SAM screens the normalized name when no NPI is usable; a delivered record with a blank NPI was refused
        # outright because the registry entity's NPI differs from what was delivered.
        if not (name_only_submitted and required_edition_sha256):
            return refused
        context = await _name_only_submitted_context(
            db, ref=ref, canonical_entity_id=canonical_entity_id, intake_id=intake_id, entity=entity)
        if context is None:
            return refused
        name_only = True
    entity_uuid = entity.get("_registry_entity_id") or canonical_entity_id

    if name_only:
        npi = None                                   # the registry NPI is never sent
        org_name = context["delivered_name"]         # the delivered record's ORIGINAL submitted name
    else:
        npi = rce_fields.rce_npi(entity) or _extract_npi(entity)
        org_name = (rce_fields.rce_value(entity, "name")
                    or entity.get("name") or "").strip()
    try:
        screen = await SAMExtractConnector().screen(npi, org_name)
    except Exception as exc:  # noqa: BLE001 - an outage must not crash the batch
        logger.warning("sam-extract coverage: screen failed for %s: %s: %s",
                       ref, type(exc).__name__, exc)
        return {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
                "outcome": OUTCOME_RETRY_PENDING,
                "reason": f"{type(exc).__name__}: {exc}"}

    dim = Dimension.D3_EXCLUSION_REVOCATION.value
    items, candidate = sam_extract_evidence_items(dim, screen)
    if required_edition_sha256:
        pinned = required_edition_sha256.strip().lower()
        if getattr(screen, "success", False):
            screened_against = ((screen.data or {}).get("extract_sha256") or "").lower()
            if screened_against != pinned:
                # The in-memory extract changed between the batch check and this screen. Persist nothing: an
                # answer from another edition must not be recorded as this run's.
                return {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
                        "outcome": OUTCOME_RETRY_PENDING,
                        "reason": "extract edition changed during the run; not persisted"}
        else:
            from app.Tefca import sam_extract as _se
            loaded = _se.loaded_edition()
            # `SourceResult.unavailable` prefixes the reason ("SAM_EXTRACT unavailable: ..."): one shared matcher.
            entity_specific = _se.is_not_screenable_error(getattr(screen, "error", None))
            if not entity_specific or (loaded.get("extract_sha256") or "").lower() != pinned:
                # Flag off, extract unavailable/refused (e.g. a failed 24 h refetch mid-run), stale edition, or a
                # different edition loaded: the attempt against the pinned edition did not happen. Persist
                # nothing, so the entity stays selected and is screened when the source recovers.
                return {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
                        "outcome": OUTCOME_RETRY_PENDING,
                        "reason": "extract not screenable against the pinned edition now; not persisted"}
            # The pinned edition IS loaded and the screen still could not be performed for THIS entity (e.g. no
            # usable NPI and no organisation name). Record that, attributed to the edition it was attempted
            # against, so the entity is accounted for as "attempted, not screenable" - never as clear, and never
            # left to be re-selected forever.
            items = [_dc_replace(item, original_values={
                **(item.original_values or {}),
                "source_dataset": "SAM_Exclusions_Public_Extract_V2",
                "extract_filename": loaded.get("extract_filename"),
                "publication_date": loaded.get("publication_date"),
                "extract_sha256": loaded.get("extract_sha256"),
                "screen_status": "not_screened",
                "not_screened_reason": (getattr(screen, "error", None) or "")[:300],
            }) for item in items]
    if name_only:
        return await _finish_name_only(db, ref=ref, context=context, intake_id=intake_id, entity_uuid=entity_uuid,
                                       screen=screen, candidate=candidate, org_name=org_name, actor=actor)
    # A one-source, one-dimension partial generation. The dimension-level
    # disposition recorded here reflects ONLY this supplementary item — the
    # full D3 determination already persisted for this entity is untouched
    # (rows are only ever inserted; see evidence_rows_for_persistence).
    evidence = {
        "generated_at": datetime.utcnow().isoformat(),
        "dimensions": [{
            "dimension": dim,
            "disposition": item.disposition,
            "applicability": "APPLICABLE",
            "evidence": [item.to_dict()],
        } for item in items],
    }
    outcome = _classify_entity_outcome(evidence)

    rows = evidence_rows_for_persistence(
        str(entity_uuid or entity.get("id")), None, evidence)
    for row in rows:
        row["review_cycle_id"] = None
        db.add(TEFCADimensionEvidence(**row))

    if candidate and entity_uuid is not None:
        # EDITION-BOUND SCREENING DOES NOT TOUCH THE ENTITY'S STATUS (owner, 2026-11-26: resolve the frame
        # interaction before screening the real population). `verification_status = in_review` is what the review
        # frame excludes on (`qhin_sampling.resolve_qhin_strata`, D-08 still undecided), so writing it here would
        # take every candidate entity OUT of the delivery's classification - while methodology §326 routes an
        # unadjudicated extract candidate INTO classification (B3, RULE-008). The candidate is still recorded: the
        # REVIEW evidence row above and the open SAM_EXTRACT_CANDIDATE finding below; classification sets the
        # status itself. The legacy `sam_extract` scope (no pinned edition) keeps its behaviour unchanged.
        if not required_edition_sha256:
            row_shape = await _entity_row_shape(db, entity_uuid)
            if row_shape is not None and row_shape.verification_status != "in_review":
                row_shape.verification_status = "in_review"
                row_shape.updated_at = datetime.utcnow()
        try:
            from app.tefca_registry.rce import verification_findings as vf
            # Savepoint: same reason as in record_coverage_attempt — the
            # ledger write may fail without aborting the batch transaction.
            async with db.begin_nested():
                await vf.record_sam_extract_candidate(
                    db, entity_id=entity_uuid, npi=npi, org_name=org_name or None,
                    extract=dict(screen.data or {}), intake_id=intake_id,
                    source_record_id=context["source_record_id"])
        except Exception as exc:  # noqa: BLE001 - the issue ledger must not fail coverage
            logger.error("sam-extract coverage: candidate finding not recorded for %s: %s",
                         entity_uuid, type(exc).__name__, exc_info=True)

    await db.flush()
    return {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
            "outcome": outcome, "dimensions_recorded": len(rows),
            "candidate_match": candidate}


async def _finish_name_only(db, *, ref, context, intake_id, entity_uuid, screen, candidate, org_name,
                            actor) -> Dict[str, Any]:
    """Persist a name-only screen RECORD-SCOPED (owner, 2026-11-27). The edition was already verified against the
    pin by the caller. Writes NO `tefca_dimension_evidence` row (so the entity is NOT covered for the edition and no
    other record's NPI-based screen is satisfied or replaced), never touches the entity's status, and puts a
    candidate on THIS record's ledger as a pending SAM_EXTRACT_CANDIDATE finding - never a confirmed exclusion."""
    from app.Tefca import sam_extract as _se
    from app.tefca_registry.rce import verification_findings as vf

    succeeded = bool(getattr(screen, "success", False))
    data = dict(screen.data or {}) if succeeded else {}
    edition = data if succeeded else _se.loaded_edition()
    # One savepoint for the finding AND the screening row: for a record-scoped screen the finding IS part of the
    # result, so either both are written or neither (the batch then reports RETRY_PENDING and the record stays
    # selectable). Never a CANDIDATE row without its finding.
    async with db.begin_nested():
        issue_id = None
        if succeeded and candidate:
            issue = await vf.record_sam_extract_candidate(
                db, entity_id=entity_uuid, npi=None, org_name=org_name, extract=data, intake_id=intake_id,
                source_record_id=context["source_record_id"], name_only_submitted=True)
            if issue is None:
                raise RuntimeError("name-only candidate finding could not be anchored to its delivered record")
            issue_id = issue.id
            outcome = NAME_ONLY_CANDIDATE
        elif succeeded:
            outcome = NAME_ONLY_NO_CANDIDATE
        else:
            outcome = NAME_ONLY_NOT_SCREENED
        await _record_name_only_screening(
            db, context=context, intake_id=intake_id, entity_uuid=entity_uuid, screen=screen, edition=edition,
            outcome=outcome, candidate_count=data.get("candidate_count") if succeeded else None,
            not_screened_reason=None if succeeded else getattr(screen, "error", None), issue_id=issue_id,
            actor=actor)
    return {"ref": ref, "entity_id": str(entity_uuid) if entity_uuid else None,
            "outcome": OUTCOME_NAME_ONLY_RECORDED, "name_only_outcome": outcome,
            "source_record_id": str(context["source_record_id"]),
            "candidate_match": bool(succeeded and candidate), "dimensions_recorded": 0,
            "screen_basis": SCREEN_BASIS_NAME_ONLY_SUBMITTED, "identity_confirmed": False}


#: Per-delivery batch single-flight. Two coverage batches running at once for
#: one delivery each read the committed not-covered set, overlap, and write
#: duplicate generations — observed 2026-10-30 on the October delivery
#: (~1,007 entities with 2–3 generations minutes apart). A transaction-level
#: advisory lock serializes batches per intake: the second caller is refused
#: (never queued) and told to retry. int4 pair: a fixed class key plus
#: hashtext(intake). Distinct from COVERAGE_TICK_LOCK_KEY, which single-
#: flights the scheduler TICK across processes; this one covers every caller
#: of `run_coverage_batch`, manual PM batches included.
BATCH_LOCK_CLASS_KEY = 20261030


async def _try_advisory_batch_lock(db, intake_id) -> bool:
    """Transaction-scoped Postgres advisory lock per (class key, intake);
    False when another batch for the same delivery holds it.
    """
    return bool((await db.execute(
        text("SELECT pg_try_advisory_xact_lock(CAST(:k AS int), "
             "hashtext(CAST(:i AS text)))"),
        {"k": BATCH_LOCK_CLASS_KEY, "i": str(intake_id)})).scalar())


def _batch_identity_fields(*, refused: int, exhausted: bool, limit_reached: bool,
                           started_after, last_scanned, remaining: int,
                           looked_up: int = 0, not_resolved: int = 0) -> Dict[str, Any]:
    """The identity-refusal part of a batch result (see `run_coverage_batch`)."""
    return {
        # Entities refused at identity validation in THIS call: nothing was
        # gathered or persisted for them, and they are still in `remaining`.
        "identity_refused": refused,
        # Entities whose delivered reference does not resolve to a registry
        # entity: no source was asked, nothing persisted, still in
        # `remaining`. Stepped over like a refusal; not a lookup.
        "not_resolved": not_resolved,
        # True when this call walked the not-covered list to its end.
        "candidates_exhausted": exhausted,
        # True when the call stopped stepping over refusals at the per-call
        # cap; `next_after_entity_id` then continues the walk.
        "identity_refusal_scan_limit_reached": limit_reached,
        "next_after_entity_id": (str(last_scanned)
                                 if limit_reached and last_scanned is not None else None),
        # The stop signal for a "call again until remaining is 0" caller:
        # the whole not-covered list was walked from its start and every
        # entity left in it was refused. `remaining` stays above 0 on
        # purpose — those entities are NOT covered — but calling again cannot
        # change that until the record's identity is corrected.
        "remaining_are_all_identity_refused": bool(
            refused and exhausted and started_after is None and remaining == refused),
        # THE STOP SIGNAL for a "call again" loop: the walk reached the end of
        # the not-covered list and this call asked no source about anything.
        # True whatever is left that cannot be asked about (identity refusals,
        # references that do not resolve, records with no delivered
        # reference); `remaining` says how many, and `outcomes` /
        # `identity_refused` / `not_resolved` say why. Calling again cannot
        # make progress until the data changes. NOT set while a source was
        # asked and failed (RETRY_PENDING): that entity is worth another call.
        "no_further_lookups_possible": bool(exhausted and looked_up == 0),
    }


def _count_by(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


async def _name_only_recorded(db, intake_id, edition_sha256: Optional[str]) -> Dict[str, int]:
    """Read-only: the delivery's record-scoped name-only screens for one edition, by outcome (counts only)."""
    rows = (await db.execute(text(_NAME_ONLY_RECORDED_COUNT_SQL),
                             {"intake_id": str(intake_id), "edition_sha256": (edition_sha256 or "").lower()})).all()
    out = {str(k): int(n) for k, n in rows}
    out["total"] = sum(out.values())
    return out


async def run_coverage_batch(db, intake_id, *, batch_size: int = DEFAULT_BATCH_SIZE,
                             actor: str = "SYSTEM",
                             scope: str = SCOPE_FULL,
                             after_entity_id=None,
                             edition_sha256: Optional[str] = None,
                             name_only_submitted: bool = False) -> Dict[str, Any]:
    """Process up to `batch_size` not-yet-covered eligible entities of one
    delivery. Commits once at the end of the call (the batch is the unit of
    atomicity, matching `review_cycle.create_review_cycle`'s own batching) and
    returns how many entities remain. Call again until `remaining` is 0 — the
    same "call again until remaining is 0" contract the review cycle uses, so
    a caller (the scheduler tick, or an operator retrying manually) does not
    need two different conventions.

    `scope` selects the candidate set and the per-entity work: SCOPE_FULL is
    the existing all-source first lookup; SCOPE_SAM_EXTRACT appends only the
    SAM-extract leg to entities that have evidence but no extract row.

    Batches for one delivery are SERIALIZED by a transaction-level advisory
    lock (held until this call's own commit): a second concurrent call is
    refused with `skipped_lock: true` and processes nothing, because two
    batches reading the committed not-covered set at once overlap and write
    duplicate generations (observed 2026-10-30).

    IDENTITY REFUSALS (2026-11-25). An entity whose record the identity
    validation refuses gets nothing persisted, so it stays in the
    not-covered set. `batch_size` bounds the entities LOOKED UP per call
    (`processed_this_call`); refusals do not use that budget — the call walks
    on past them (keyset on the entity id, at most
    `MAX_IDENTITY_REFUSALS_PER_CALL` of them) so refused entities at the
    front of the list cannot stop the entities behind them from being
    covered. Refusals are reported, not hidden: each is in `outcomes` with
    its reason, `by_outcome` counts `IDENTITY_REFUSED`, and
    `identity_refused` / `no_further_lookups_possible` /
    `next_after_entity_id` say what is left and why (`_batch_identity_fields`).
    `after_entity_id` continues a walk a previous call stopped at the cap."""
    from app.Tefca.ppef_store import make_local_store

    if scope not in SCOPES:
        raise ValueError(f"unknown coverage scope {scope!r}; one of {SCOPES}")
    if scope in (SCOPE_SAM_EXTRACT, SCOPE_SAM_EXTRACT_EDITION) and not sam_extract_screening_enabled():
        raise SamExtractScreeningDisabled(
            "ENABLE_SAM_EXTRACT_SCREENING is off; a sam_extract-scope batch "
            "would record UNAVAILABLE for every entity and cover nothing")
    pinned_edition = None
    if scope == SCOPE_SAM_EXTRACT_EDITION:
        problem = await pinned_edition_problem(edition_sha256)
        if problem:
            raise SamExtractEditionRefused(problem)
        pinned_edition = edition_sha256.strip().lower()
    elif edition_sha256:
        raise ValueError("edition_sha256 applies only to the sam_extract_edition scope")
    if name_only_submitted and scope != SCOPE_SAM_EXTRACT_EDITION:
        raise ValueError("name_only_submitted applies only to the sam_extract_edition scope")
    if name_only_submitted:
        await _assert_name_only_store(db)

    batch_size = max(1, min(int(batch_size or DEFAULT_BATCH_SIZE), MAX_BATCH_SIZE))
    started_after = uuid.UUID(str(after_entity_id)) if after_entity_id else None

    if not await _try_advisory_batch_lock(db, intake_id):
        # No rollback: the failed try-lock was a plain read, and the caller's
        # session (a route dependency, the scheduler tick, or a test's
        # savepoint-wrapped session) owns its own transaction lifecycle.
        progress = await coverage_progress(db, intake_id, scope=scope, edition_sha256=pinned_edition)
        return {
            "intake_id": str(intake_id), "scope": scope,
            "skipped_lock": True,
            "note": ("another coverage batch for this delivery is already "
                     "running; retry when it finishes"),
            "processed_this_call": 0, "by_outcome": {}, "outcomes": [],
            **_batch_identity_fields(refused=0, exhausted=False, limit_reached=False,
                                     started_after=started_after, last_scanned=None,
                                     remaining=progress["remaining"]),
            **progress,
            "complete": progress["remaining"] == 0,
            "batch_size": batch_size,
        }

    candidate_sql = {SCOPE_SAM_EXTRACT: _SAM_EXTRACT_NOT_COVERED_SQL,
                     SCOPE_SAM_EXTRACT_EDITION: (_SAM_EXTRACT_EDITION_NAME_ONLY_NOT_COVERED_SQL if name_only_submitted
                                                 else _SAM_EXTRACT_EDITION_NOT_COVERED_SQL)}.get(
                         scope, _ELIGIBLE_NOT_COVERED_SQL)
    extra_params = {"edition_sha256": pinned_edition} if pinned_edition else {}

    local_store = make_local_store(db)
    outcomes: List[Dict[str, Any]] = []
    looked_up = refused = unresolved = 0
    cursor = started_after
    exhausted = limit_reached = False
    # Pages have a fixed minimum size, so a run of refusals costs one
    # candidate query per page and not one per refused entity. Rows are
    # consumed one at a time and the walk stops BEFORE a row once the lookup
    # budget or the refusal cap is used up, so neither is ever exceeded and
    # the cursor is always the last row actually consumed.
    stop = False
    while not stop:
        page_limit = max(batch_size - looked_up, CANDIDATE_PAGE_SIZE)
        rows = (await db.execute(
            text(candidate_sql),
            {"intake_id": str(intake_id), "limit": page_limit,
             "after": str(cursor) if cursor is not None else None, **extra_params})).all()
        for ref, canonical_entity_id in rows:
            if looked_up >= batch_size:
                stop = True
                break
            if refused + unresolved >= MAX_IDENTITY_REFUSALS_PER_CALL:
                stop = limit_reached = True
                break
            cursor = canonical_entity_id
            try:
                if scope in (SCOPE_SAM_EXTRACT, SCOPE_SAM_EXTRACT_EDITION):
                    result = await record_sam_extract_attempt(
                        db, ref=ref, canonical_entity_id=canonical_entity_id,
                        intake_id=intake_id, required_edition_sha256=pinned_edition,
                        name_only_submitted=name_only_submitted, actor=actor)
                else:
                    result = await record_coverage_attempt(
                        db, ref=ref, canonical_entity_id=canonical_entity_id,
                        intake_id=intake_id, actor=actor, local_store=local_store)
            except Exception as exc:  # noqa: BLE001 - one entity's bug must not lose the batch
                logger.error("automated coverage: unhandled error for %s: %s",
                            ref, type(exc).__name__, exc_info=True)
                result = {"ref": ref, "outcome": OUTCOME_RETRY_PENDING,
                          "reason": f"unhandled {type(exc).__name__}"}
            outcomes.append(result)
            if result["outcome"] == OUTCOME_IDENTITY_REFUSED:
                refused += 1
            elif result.get("not_resolved"):
                unresolved += 1            # no source was asked: stepped over, not a lookup
            else:
                looked_up += 1
        else:
            # Every fetched row was consumed; a short page is the end of the list.
            if len(rows) < page_limit:
                exhausted = True
                stop = True

    await db.commit()  # also releases the advisory xact lock

    if refused:
        # Visible, never silent: counts only (the per-entity reasons are in
        # the returned `outcomes`).
        logger.warning("automated coverage: %d record(s) of delivery %s (scope %s) were refused at "
                       "identity validation; nothing was gathered or persisted for them",
                       refused, intake_id, scope)

    progress = await coverage_progress(db, intake_id, scope=scope, edition_sha256=pinned_edition)
    if pinned_edition:
        from app.Tefca import sam_extract as _se
        progress["pinned_edition"] = _se.loaded_edition()
    by_outcome: Dict[str, int] = {}
    for o in outcomes:
        by_outcome[o["outcome"]] = by_outcome.get(o["outcome"], 0) + 1

    return {
        "intake_id": str(intake_id),
        "scope": scope,
        "name_only_submitted": bool(name_only_submitted),
        # Record-scoped name-only screens recorded THIS call, by the record's outcome. They never count toward the
        # entity coverage above (`covered` / `remaining` are entity-level and NPI-basis); the delivery's running
        # totals for the edition are in `name_only_recorded_for_edition`.
        "name_only_screened": sum(1 for o in outcomes if o.get("outcome") == OUTCOME_NAME_ONLY_RECORDED),
        "name_only_by_outcome": _count_by(o.get("name_only_outcome") for o in outcomes
                                          if o.get("outcome") == OUTCOME_NAME_ONLY_RECORDED),
        **({"name_only_recorded_for_edition": await _name_only_recorded(db, intake_id, pinned_edition)}
           if name_only_submitted else {}),
        "skipped_lock": False,
        # Entities a source was asked about in this call (never more than
        # `batch_size`). Identity refusals and references that do not resolve
        # are counted separately below; `by_outcome` and `outcomes` include
        # all of them.
        "processed_this_call": looked_up,
        "by_outcome": by_outcome,
        **_batch_identity_fields(refused=refused, exhausted=exhausted, limit_reached=limit_reached,
                                 started_after=started_after, last_scanned=cursor,
                                 remaining=progress["remaining"], looked_up=looked_up,
                                 not_resolved=unresolved),
        "ledger_not_recorded": sum(1 for o in outcomes if o.get("ledger_recorded") is False),
        "outcomes": outcomes,
        **progress,
        "complete": progress["remaining"] == 0,
        "batch_size": batch_size,
    }


# ── scheduler integration ────────────────────────────────────────────────────
#
# LOAD PROTECTION (2026-09-25). On DEV the candidate query below ran for 34
# minutes — a DISTINCT over every curated record of every SUCCEEDED delivery,
# with a correlated NOT EXISTS against the evidence table — and copies of it
# overlapped across container recycles (APScheduler's `max_instances=1` is
# per process), pinning the database at 100% CPU. Three controls:
#
#   1. cross-process single flight: a PostgreSQL session-level advisory lock
#      on a fixed key, tried (never waited for) on the tick's own connection.
#      A second process, or the previous container still draining, finds the
#      lock held and returns without touching anything;
#   2. a bounded candidate query: the newest `CANDIDATE_JOB_LIMIT` succeeded
#      deliveries, each probed with an EXISTS ... LIMIT 1 for one promoted
#      record without evidence, instead of a DISTINCT over the population;
#   3. a statement timeout on that lookup (`SET LOCAL`, transaction-scoped),
#      so a lookup that is still slow is cancelled, logged and retried on a
#      later tick rather than left running;
#   4. ADAPTIVE BACKOFF (2026-09-25, follow-up). Controls 1-3 were not enough
#      on a 1-vCPU burstable server: every 15-second tick's lookup ran into
#      the 15 s timeout and was cancelled, so a bounded query at a fixed
#      cadence still kept the database at ~100% CPU with no burst credits
#      left. A cancelled (or raising) lookup now arms a cooldown — ticks are
#      skipped until `next_allowed_at` — that doubles per consecutive
#      failure from `COVERAGE_BACKOFF_BASE_SECONDS` (default 300) up to
#      `COVERAGE_BACKOFF_MAX_SECONDS` (default 3600) and resets on the first
#      lookup that completes. A duty-cycle guard adds a rest after any tick
#      whose wall time exceeded half the interval, so successive ticks cannot
#      occupy the database back-to-back. The state lives in this process
#      (`_state`, guarded by `_state_lock`); a multi-process deployment
#      would need shared state for the backoff to be global — the same
#      accepted limitation control 1 addresses for overlap, and in that
#      deployment the advisory lock still keeps ticks single-flight.

#: The advisory-lock key. A fixed 64-bit constant, chosen once, never reused
#: for another lock in this codebase (grep before adding one).
COVERAGE_TICK_LOCK_KEY = 20260925000000001

#: How many of the newest succeeded deliveries one tick considers.
CANDIDATE_JOB_LIMIT = 25

#: `SET LOCAL statement_timeout` for the candidate lookup, in milliseconds.
ENV_CANDIDATE_TIMEOUT_MS = "COVERAGE_CANDIDATE_TIMEOUT_MS"
DEFAULT_CANDIDATE_TIMEOUT_MS = 15_000
MIN_CANDIDATE_TIMEOUT_MS = 1_000
MAX_CANDIDATE_TIMEOUT_MS = 600_000

#: PostgreSQL's SQLSTATE for a statement cancelled by statement_timeout.
_QUERY_CANCELED_SQLSTATE = "57014"

#: The scheduler interval (`register_with_scheduler`) and the backoff bounds.
TICK_INTERVAL_SECONDS = 15
ENV_BACKOFF_BASE_SECONDS = "COVERAGE_BACKOFF_BASE_SECONDS"
DEFAULT_BACKOFF_BASE_SECONDS = 300
ENV_BACKOFF_MAX_SECONDS = "COVERAGE_BACKOFF_MAX_SECONDS"
DEFAULT_BACKOFF_MAX_SECONDS = 3600
MIN_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 86_400

#: Duty-cycle guard: a tick whose wall time exceeds this fraction of the
#: interval is followed by a rest of `DUTY_CYCLE_REST_FACTOR` x its wall
#: time (never less than one interval), so the database is idle at least
#: twice as long as the tick kept it busy.
DUTY_CYCLE_FRACTION = 0.5
DUTY_CYCLE_REST_FACTOR = 2

#: `last_outcome` vocabulary reported by `coverage_scheduler_status()`.
TICK_IDLE = "idle"                  # no tick has run yet
TICK_BATCH = "batch"                # a candidate was found and a batch ran
TICK_NO_CANDIDATE = "no_candidate"  # lookup completed, nothing to cover
TICK_TIMEOUT = "timeout"            # lookup cancelled by statement_timeout
TICK_SKIPPED_LOCK = "skipped_lock"  # another connection holds the advisory lock
TICK_BACKOFF = "backoff"            # skipped: cooldown still in force
TICK_ERROR = "error"                # lookup or batch raised (logged)


class CandidateLookupTimeout(Exception):
    """The candidate lookup was cancelled by its statement timeout. Raised
    only when `_next_delivery_needing_coverage(..., raise_on_timeout=True)`
    is asked for it; the default contract (return None) is unchanged."""


@dataclass
class _CoverageSchedulerState:
    """Per-process tick state. Timestamps are epoch seconds (float) from the
    tick's clock so a test can inject a fake `now`; `coverage_scheduler_status`
    renders them as ISO-8601 UTC. Holds no identifiers, SQL or secrets."""
    last_tick_at: Optional[float] = None
    last_outcome: str = TICK_IDLE
    consecutive_timeouts: int = 0
    next_allowed_at: Optional[float] = None
    last_lookup_ms: Optional[int] = None
    last_batch_ms: Optional[int] = None
    ticks_skipped_backoff: int = 0


_state = _CoverageSchedulerState()
_state_lock = threading.Lock()

#: IDENTITY HOLD (2026-11-25). A refused record gets no evidence row, so its
#: delivery never leaves the "needs coverage" candidate set, and the tick
#: takes ONE delivery per run — the newest. Without this, a delivery whose
#: only not-covered entities are identity refusals would be picked on every
#: tick forever (re-validating the same refusals every 15 s) and every older
#: delivery would never be reached. A tick whose batch looked nothing up and
#: only refused therefore puts that delivery on hold: it is left out of the
#: candidate lookup until the hold expires (`backoff_max_seconds()`, default
#: one hour), then tried again, so a corrected record is picked up without a
#: restart. intake id (text) -> epoch seconds the hold ends. Process-local,
#: like the backoff state above and with the same accepted limitation: each
#: process learns its own holds, and a restart forgets them (one extra
#: validation-only batch per held delivery). No schema, nothing persisted.
_identity_holds: Dict[str, float] = {}

#: Where the tick's walk of one delivery stopped at the per-call refusal cap
#: (intake id -> entity id to continue after). Without it every tick would
#: restart at the front, meet the same refusals, and never reach the records
#: behind them. Dropped when a walk reaches the end of the list. Process-local,
#: same limitation as the holds.
_identity_cursors: Dict[str, str] = {}

#: What the tick's current walk of one delivery has done so far, across the
#: ticks it took: intake id -> [refused, looked_up]. The hold is decided on the
#: WHOLE walk, not on its last tick, so a walk that met only refusals is held
#: whichever tick it happened to end on (for instance when the entities behind
#: the cursor were covered by another path between two ticks and the final
#: tick finds nothing at all).
_identity_walks: Dict[str, List[int]] = {}


def reset_coverage_scheduler_state() -> None:
    """Forget all tick state (tests; a process restart does the same)."""
    global _state
    with _state_lock:
        _state = _CoverageSchedulerState()
        _identity_holds.clear()
        _identity_cursors.clear()
        _identity_walks.clear()


def _hold_delivery_for_identity_refusals(intake_id, now: float) -> float:
    """Put a delivery on identity hold; returns when the hold ends."""
    until = now + backoff_max_seconds()
    with _state_lock:
        _identity_holds[str(intake_id)] = until
    return until


def _held_intake_ids(now: float) -> List[str]:
    """The deliveries on identity hold at `now` (expired holds are dropped)."""
    with _state_lock:
        for key in [k for k, until in _identity_holds.items() if until <= now]:
            del _identity_holds[key]
        return sorted(_identity_holds)


def _env_seconds(name: str, default: int) -> int:
    """Integer seconds from an env var, with default and [MIN, MAX] clamping."""
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d s", name, raw, default)
        value = default
    return max(MIN_BACKOFF_SECONDS, min(value, MAX_BACKOFF_SECONDS))


def backoff_base_seconds() -> int:
    """The configured backoff base (clamped env override or default)."""
    return _env_seconds(ENV_BACKOFF_BASE_SECONDS, DEFAULT_BACKOFF_BASE_SECONDS)


def backoff_max_seconds() -> int:
    """The cap; never below the base, so a misconfigured pair still backs off."""
    return max(backoff_base_seconds(), _env_seconds(ENV_BACKOFF_MAX_SECONDS,
                                                    DEFAULT_BACKOFF_MAX_SECONDS))


def backoff_seconds(consecutive_timeouts: int) -> int:
    """Cooldown after the n-th consecutive failed lookup: base * 2**(n-1),
    capped. `consecutive_timeouts` <= 0 means no cooldown."""
    if consecutive_timeouts <= 0:
        return 0
    base, cap = backoff_base_seconds(), backoff_max_seconds()
    exponent = min(consecutive_timeouts - 1, 30)  # 2**30 * base is already past any cap
    return min(base * (2 ** exponent), cap)


def _iso(epoch: Optional[float]) -> Optional[str]:
    """Epoch seconds -> UTC ISO-8601 string (seconds precision), or None."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def coverage_scheduler_status() -> Dict[str, Any]:
    """Safe operational metrics for `/api/admin/health`: flags, timestamps,
    counters and durations only — no identifiers, SQL or secrets."""
    with _state_lock:
        s = _dc_replace(_state)
        held = len(_identity_holds)
    return {
        "enabled": automated_coverage_enabled(),
        # Deliveries this process is not ticking because their only
        # not-covered entities were refused at identity validation (a count;
        # holds that have expired are dropped at the next tick).
        "deliveries_on_identity_hold": held,
        "last_tick_at": _iso(s.last_tick_at),
        "last_outcome": s.last_outcome,
        "consecutive_timeouts": s.consecutive_timeouts,
        "next_allowed_at": _iso(s.next_allowed_at),
        "last_lookup_ms": s.last_lookup_ms,
        "last_batch_ms": s.last_batch_ms,
        "ticks_skipped_backoff": s.ticks_skipped_backoff,
        "backoff_base_seconds": backoff_base_seconds(),
        "backoff_max_seconds": backoff_max_seconds(),
        "tick_interval_seconds": TICK_INTERVAL_SECONDS,
    }


def _skip_for_backoff(now: float) -> bool:
    """True (and counted) when the cooldown is still in force at `now`."""
    with _state_lock:
        if _state.next_allowed_at is not None and now < _state.next_allowed_at:
            _state.ticks_skipped_backoff += 1
            _state.last_tick_at = now
            _state.last_outcome = TICK_BACKOFF
            return True
    return False


def _finish_tick(started: float, ended: float, outcome: str, *,
                 lookup_result: Optional[str], lookup_ms: Optional[int],
                 batch_ms: Optional[int]) -> None:
    """Record a tick that ran and arm or clear the cooldown.

    `lookup_result` is "ok" (a candidate was found or none exists), "timeout",
    "error", or None when the lookup was not attempted (lock not acquired),
    which leaves the backoff state untouched.
    """
    with _state_lock:
        s = _state
        s.last_tick_at = started
        s.last_outcome = outcome
        s.last_lookup_ms = lookup_ms
        s.last_batch_ms = batch_ms

        if lookup_result in (TICK_TIMEOUT, TICK_ERROR):
            s.consecutive_timeouts += 1
            cooldown = backoff_seconds(s.consecutive_timeouts)
            s.next_allowed_at = ended + cooldown
            logger.info("automated coverage: entering backoff after %d consecutive "
                        "failed lookup(s) (%s); next tick allowed in %d s",
                        s.consecutive_timeouts, lookup_result, cooldown)
        elif lookup_result == "ok":
            if s.consecutive_timeouts:
                logger.info("automated coverage: leaving backoff after %d consecutive "
                            "failed lookup(s); lookup completed in %s ms",
                            s.consecutive_timeouts, lookup_ms)
            s.consecutive_timeouts = 0
            s.next_allowed_at = None

        wall = max(0.0, ended - started)
        if wall > DUTY_CYCLE_FRACTION * TICK_INTERVAL_SECONDS:
            rest = max(float(TICK_INTERVAL_SECONDS), DUTY_CYCLE_REST_FACTOR * wall)
            candidate = ended + rest
            if s.next_allowed_at is None or candidate > s.next_allowed_at:
                s.next_allowed_at = candidate
                logger.debug("automated coverage: tick took %.1f s (> %d%% of the %d s "
                             "interval); resting %.0f s", wall,
                             int(DUTY_CYCLE_FRACTION * 100), TICK_INTERVAL_SECONDS, rest)


def candidate_timeout_ms() -> int:
    """The configured lookup timeout, clamped to a sane range. A value that is
    not an integer falls back to the default rather than disabling the bound."""
    raw = os.environ.get(ENV_CANDIDATE_TIMEOUT_MS, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_CANDIDATE_TIMEOUT_MS
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d ms",
                       ENV_CANDIDATE_TIMEOUT_MS, raw, DEFAULT_CANDIDATE_TIMEOUT_MS)
        value = DEFAULT_CANDIDATE_TIMEOUT_MS
    return max(MIN_CANDIDATE_TIMEOUT_MS, min(value, MAX_CANDIDATE_TIMEOUT_MS))


async def _try_acquire_tick_lock(conn) -> bool:
    """Session-level advisory lock, tried on THIS connection. Never blocks."""
    acquired = (await conn.execute(
        text("SELECT pg_try_advisory_lock(CAST(:k AS bigint))"),
        {"k": COVERAGE_TICK_LOCK_KEY})).scalar()
    return bool(acquired)


async def _release_tick_lock(conn) -> None:
    """Release on the SAME connection that acquired it; a session-level
    advisory lock released elsewhere is not released at all."""
    try:
        released = (await conn.execute(
            text("SELECT pg_advisory_unlock(CAST(:k AS bigint))"),
            {"k": COVERAGE_TICK_LOCK_KEY})).scalar()
        if not released:
            logger.warning("automated coverage: advisory lock %s was not held at release",
                           COVERAGE_TICK_LOCK_KEY)
    except Exception as exc:  # noqa: BLE001 - the connection is closed right after anyway
        logger.warning("automated coverage: advisory unlock failed: %s", type(exc).__name__)


async def _coverage_tick(*, now: Optional[Callable[[], float]] = None):
    """One scheduler tick: find ONE delivery with eligible-but-not-covered
    entities and process one bounded batch for it.

    Mirrors `delivery_scheduler._poll_tick`'s shape (own session, own
    try/except so a tick that raises cannot stop the scheduler). Gated behind
    `automated_coverage_enabled()` so importing/registering this module is
    inert until a deployment explicitly turns it on, and behind the backoff
    cooldown (`_skip_for_backoff`) so a database that cannot answer the
    lookup in time is left alone for a growing interval instead of being
    asked again 15 seconds later.

    The session is bound to ONE connection held for the whole tick, because a
    session-level advisory lock belongs to a connection: a pooled session
    hands its connection back on every commit (and `run_coverage_batch`
    commits), so lock and unlock would otherwise land on different
    connections and the lock would leak into the pool, held forever.

    `now` is the clock (epoch seconds); injectable so tests drive the backoff
    with a fake clock instead of sleeping.
    """
    if not automated_coverage_enabled():
        return
    clock = now or time.time
    started = clock()
    if _skip_for_backoff(started):
        return

    from app.core.database import _get_engine

    outcome = TICK_IDLE
    lookup_result: Optional[str] = None
    lookup_ms: Optional[int] = None
    batch_ms: Optional[int] = None
    try:
        async with _get_engine().connect() as conn:
            if not await _try_acquire_tick_lock(conn):
                logger.debug("automated coverage tick skipped: another process holds "
                             "advisory lock %s", COVERAGE_TICK_LOCK_KEY)
                outcome = TICK_SKIPPED_LOCK
                return
            await conn.commit()  # end the autobegun transaction; the lock outlives it
            try:
                async with AsyncSession(bind=conn, expire_on_commit=False) as db:
                    lookup_started = clock()
                    held = _held_intake_ids(lookup_started)
                    try:
                        # The exclusion is passed only when there is one, so
                        # the lookup's ordinary call is exactly what it was.
                        intake_id = await _next_delivery_needing_coverage(
                            db, raise_on_timeout=True,
                            **({"exclude_intake_ids": held} if held else {}))
                    except CandidateLookupTimeout:
                        lookup_result = outcome = TICK_TIMEOUT
                        return
                    except Exception:
                        lookup_result = outcome = TICK_ERROR
                        raise
                    finally:
                        lookup_ms = int((clock() - lookup_started) * 1000)
                    lookup_result = "ok"
                    if intake_id is None:
                        outcome = TICK_NO_CANDIDATE
                        return
                    batch_started = clock()
                    with _state_lock:
                        resume_after = _identity_cursors.get(str(intake_id))
                    # The cursor is passed only when there is one, so the
                    # ordinary call is exactly what it was.
                    result = await run_coverage_batch(
                        db, intake_id,
                        **({"after_entity_id": resume_after} if resume_after else {}))
                    batch_ms = int((clock() - batch_started) * 1000)
                    outcome = TICK_BATCH
                    logger.info("automated coverage tick for %s: %s/%s covered, %s remaining",
                                intake_id, result["covered"], result["eligible"],
                                result["remaining"])
                    facts = result if isinstance(result, dict) else {}
                    key = str(intake_id)
                    with _state_lock:
                        walk = _identity_walks.setdefault(key, [0, 0])
                        walk[0] += (int(facts.get("identity_refused") or 0)
                                    + int(facts.get("not_resolved") or 0))
                        walk[1] += int(facts.get("processed_this_call") or 0)
                        refused, looked_up_in_walk = walk[0], walk[1]
                    if (facts.get("identity_refusal_scan_limit_reached")
                            and facts.get("next_after_entity_id")):
                        # Stopped at the refusal cap: the next tick continues
                        # behind the refusals instead of meeting them again.
                        with _state_lock:
                            _identity_cursors[key] = str(facts["next_after_entity_id"])
                    elif facts.get("candidates_exhausted"):
                        with _state_lock:
                            _identity_cursors.pop(key, None)
                            _identity_walks.pop(key, None)
                        if not looked_up_in_walk and int(facts.get("remaining") or 0) > 0:
                            # The whole walk reached the end of the list and
                            # asked no source about anything, yet entities
                            # remain: identity refusals, references that do
                            # not resolve, or records with no delivered
                            # reference (those are never candidates at all).
                            # Do not spin on it.
                            until = _hold_delivery_for_identity_refusals(intake_id, clock())
                            logger.warning(
                                "automated coverage: the walk of delivery %s ended with %d record(s) "
                                "refused at identity validation or not resolvable and nothing to look up; "
                                "%s entity(ies) remain NOT covered. The delivery is on hold for the "
                                "scheduler until %s; nothing was persisted for those records.",
                                intake_id, refused, facts.get("remaining"), _iso(until))
            finally:
                await _release_tick_lock(conn)
    except Exception as exc:  # noqa: BLE001 - a tick that raises must not stop the scheduler
        outcome = TICK_ERROR
        logger.error("automated coverage tick error: %s", exc, exc_info=True)
    finally:
        _finish_tick(started, clock(), outcome, lookup_result=lookup_result,
                     lookup_ms=lookup_ms, batch_ms=batch_ms)


#: One intake among the newest `:job_limit` succeeded deliveries that still
#: has a promoted record (canonical_entity_id IS NOT NULL) with no
#: tefca_dimension_evidence row for CAST(canonical_entity_id AS TEXT). The
#: candidate set is bounded first; each candidate is then probed with an
#: EXISTS ... LIMIT 1 that stops at the first qualifying record.
_ONE_DELIVERY_NEEDING_COVERAGE_SQL = """
    SELECT c.source_intake_id
    FROM (
        SELECT j.source_intake_id,
               max(coalesce(j.completed_at, j.created_at)) AS finished_at
        FROM rce_delivery_jobs j
        WHERE j.state = 'SUCCEEDED'
          AND j.source_intake_id IS NOT NULL
        GROUP BY j.source_intake_id
        ORDER BY finished_at DESC
        LIMIT :job_limit
    ) c
    WHERE EXISTS (
        SELECT 1 FROM rce_curated_records r
        WHERE r.source_intake_id = c.source_intake_id
          AND r.canonical_entity_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM tefca_dimension_evidence d
              WHERE d.entity_id = CAST(r.canonical_entity_id AS TEXT))
        LIMIT 1
    )
      AND NOT (CAST(c.source_intake_id AS TEXT) = ANY(CAST(:held AS TEXT[])))
    ORDER BY c.finished_at DESC
    LIMIT 1
"""


def _is_statement_timeout(exc: BaseException) -> bool:
    """True when the exception (or its .orig) is a Postgres statement-timeout
    / query-canceled error.
    """
    for candidate in (exc, getattr(exc, "orig", None)):
        if candidate is None:
            continue
        if getattr(candidate, "sqlstate", None) == _QUERY_CANCELED_SQLSTATE:
            return True
        if type(candidate).__name__ == "QueryCanceledError":
            return True
    return False


async def _next_delivery_needing_coverage(db, *, job_limit: int = CANDIDATE_JOB_LIMIT,
                                          timeout_ms: Optional[int] = None,
                                          raise_on_timeout: bool = False,
                                          exclude_intake_ids: Optional[List[str]] = None):
    """The next intake to cover, or None — also None (logged, nothing marked)
    when the lookup exceeds its statement timeout, unless `raise_on_timeout`
    asks for `CandidateLookupTimeout` instead so the caller (the scheduler
    tick) can tell "nothing to do" from "could not find out" and back off.

    `exclude_intake_ids` leaves out deliveries the tick has on identity hold
    (see `_identity_holds`); None or empty excludes nothing.

    `SET LOCAL` is transaction-scoped, and the transaction is ended after the
    lookup, so the timeout never applies to the batch that follows.
    """
    timeout = int(timeout_ms if timeout_ms is not None else candidate_timeout_ms())
    try:
        # SET LOCAL takes no bind parameter; the value is an int by construction.
        await db.execute(text(f"SET LOCAL statement_timeout = {timeout}"))
        row = (await db.execute(text(_ONE_DELIVERY_NEEDING_COVERAGE_SQL),
                                {"job_limit": int(job_limit),
                                 "held": [str(i) for i in (exclude_intake_ids or [])]})).first()
    except Exception as exc:  # noqa: BLE001 - a cancelled lookup is a skipped tick, not a crash
        if not _is_statement_timeout(exc):
            raise
        logger.warning("automated coverage: candidate lookup exceeded %d ms and was "
                       "cancelled; nothing marked, will retry on a later tick", timeout)
        await db.rollback()
        if raise_on_timeout:
            raise CandidateLookupTimeout(timeout) from exc
        return None
    await db.rollback()  # read-only; ends the transaction and the SET LOCAL with it
    return row[0] if row else None


def register_with_scheduler(scheduler) -> None:
    """Add the coverage tick to an already-constructed APScheduler instance.
    Called from `delivery_scheduler.start_delivery_scheduler`; kept as its own
    function so this module has no import-time dependency on APScheduler at
    all (it is perfectly usable — e.g. from a management command or a test —
    without a scheduler in the process)."""
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        _coverage_tick, IntervalTrigger(seconds=TICK_INTERVAL_SECONDS),
        id="rce_automated_verification_coverage",
        name="Automated verification coverage (external sources, all eligible entities)",
        coalesce=True, misfire_grace_time=120, replace_existing=True, max_instances=1)
