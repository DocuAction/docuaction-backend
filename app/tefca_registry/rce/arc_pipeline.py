"""
P9 + P10 — run D1-D6, then B1-B4, then tier routing, over promoted RCE entities.

THREE CONCEPTS, KEPT SEPARATE
─────────────────────────────
    VERIFICATION RESULT   what each source said, per dimension. D1-D6.
    ARC DETERMINATION     the B1-B4 discrepancy classification.
    REVIEW TIER           who works it. T1 auto-complete, T2 analyst, T3 SME.

They are stored in three different places and are never collapsed into one
field. A B1 is not "passed"; it is "no discrepancy found against the evidence
gathered", and the evidence is what an auditor reads. A T1 routing is not a
determination either — it says nobody needs to look, which is a workload
statement, not a compliance one.

ONE CLASSIFIER
`bucket_classifier.BucketClassifier` — the DB-driven, versioned one. Every
classification records the rule_code and rule_version that produced it, so a
determination stays explicable after ONC revises the rule set.
`validation_engine.py` is deliberately NOT used for RCE entities: it is the
in-code classifier serving the legacy path, and running two classifiers over one
population would mean two answers with no way to say which was authoritative.

APPLICABILITY BEFORE DISPOSITION
A dimension that does not apply is NOT_APPLICABLE and is excluded from the
satisfied rate. It is never a FAIL, and never counted as an unsatisfied
requirement — an entity with no NPI has not failed Medicare enrollment, it has
no Medicare dimension to fail.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m

# The shared vocabulary registry. `app/core/__init__.py` is empty, so this import
# is side-effect free and cannot cycle back into either domain package — which is
# why the registry lives there rather than under app/Tefca/, whose __init__ eagerly
# imports routes, connectors, validation_engine and mock_data.
from app.core.evidence_vocabulary import (
    CLASSIFIER_SIGNAL_REGISTRY as _SIGNAL_REGISTRY,
    PATH_RCE as _PATH_RCE,
)

logger = logging.getLogger(__name__)

#: B1-B4 → review tier. B1 auto-completes; B3 and B4 both escalate to T3, but
#: they arrive there for different reasons and keep their own bucket.
BUCKET_TO_TIER = {"B1": 1, "B2": 2, "B3": 3, "B4": 3}

TIER_ROLE = {1: "system", 2: "reviewer", 3: "senior_analyst"}

#: Reserved code recorded when NO rule matched. Not a rule in `review_rules` —
#: the documented default path, named so a determination always cites something.
UNMATCHED_RULE_CODE = "DEFAULT-UNMATCHED"
UNMATCHED_RULE_VERSION = 0

#: Evidence source key → the source name the B1-B4 rules evaluate.
#:
#: PER SOURCE, NOT PER DIMENSION. An earlier version mapped each DIMENSION to
#: one classifier source, and it was wrong in a way that mattered: D3 rolls up
#: OIG, SAM and CMS-Revocation into one disposition, so a SAM outage made the
#: whole dimension UNAVAILABLE and the classifier read that as "OIG did not
#: answer" — when OIG had answered and returned clear. Every rule then failed to
#: match and every entity defaulted to B3.
#:
#: The classifier speaks a source vocabulary because its rules are about
#: sources. Feeding it dimension roll-ups discards exactly the per-source
#: detail the rules need.
_EVIDENCE_SOURCE_TO_RULE_SOURCE = {
    "NPPES": "nppes",
    "OIG_LEIE": "oig_leie",
    "SAM_GOV": "sam_gov",
    "CMS_PPEF_ENROLLMENT": "pecos",
    "CMS_REVOCATION": "cms_revocation",
    "CMS_PPEF_PRACTICE_LOCATION": "pecos_practice_location",
    "CMS_PPEF_REASSIGNMENT": "pecos_reassignment",
    "ONC_RCE_DIRECTORY": "rce_directory",
    "ENTRANT_WEBSITE": "website",
}

#: Disposition → the classifier's five verification states.
#:
#: NOT_APPLICABLE maps to `not_checked`, NOT to `verified`. The classifier
#: excludes not_checked from its discrepancy counts, which is exactly right:
#: a dimension that does not apply must neither help nor hurt the entity.
#: Mapping it to `verified` would let inapplicability manufacture a clean result.
#:
#: INVESTIGATED 2026-10-02, RESOLVED (not deferred): two findings from the
#: SAM verification-contract review, both traced to this mapping.
#:
#:   1. REVIEW -> "not_found" collapses "searched, found a potential match,
#:      analyst must confirm" into the same classifier state as "searched,
#:      found nothing," for OIG_LEIE/SAM_GOV exclusion dimensions. Left
#:      UNCHANGED, deliberately: `verification_coverage.py` (the dashboard's
#:      per-source count) reads `tefca_dimension_evidence.disposition`
#:      directly, through its OWN `_DIMENSION_DISPOSITION` mapping, never
#:      through this translator or this classifier — confirmed by reading
#:      both consumers; they do not share state, so changing this mapping
#:      would not touch the "1,298 Failed indicator" dashboard count either
#:      way. It was left alone because RULE-002's own pre-existing condition
#:      already depends on "not_found" meaning exactly this (see point 2),
#:      and changing the literal here without updating that rule would
#:      break a currently-correct guard, not fix anything.
#:      SEPARATELY, `verification_coverage.py`'s OWN `_DIMENSION_DISPOSITION`
#:      dict WAS updated (same session) to map REVIEW -> "not_found" too --
#:      it had no entry for REVIEW at all before, so a pending/confirmed
#:      exclusion was invisible to the coverage dashboard, not merely
#:      mislabeled. That is a different dict from this one; see its own
#:      comment for why mapping it the same way is safe and independent of
#:      the 1,298 question.
#:   2. `bucket_classifier.SEED_RULES_V2`'s RULE-001/003/005 SAM/LEIE
#:      disqualifiers were written against literal source-status strings
#:      "excluded"/"debarred" — never producible by this translator's
#:      five-state vocabulary, so dead on THIS (RCE/delivery) path, while
#:      still correctly reachable on the separate manual single-entity
#:      review path (`review_service.probe_sources` DOES emit "excluded"
#:      literally for OIG_LEIE). FIXED in `bucket_classifier.SEED_RULES_V3`
#:      (`_v3_rules()`): additively adds `{"source": ..., "status":
#:      "not_found"}` conditions — the literal this translator actually
#:      produces for REVIEW, already proven reachable by RULE-002's own
#:      existing condition — to RULE-001/003/005, without touching the
#:      v1/v2 "excluded"/"debarred" conditions those rules still need for
#:      the other path. Confirmed independent of the 1,298 question: this
#:      is about whether the BUCKET CLASSIFIER can disqualify an entity for
#:      a pending exclusion review, not about the dashboard's "failed"
#:      count semantics across sources.
_DISPOSITION_TO_STATE = {
    "PASS": "verified",
    "CORROBORATED": "verified",
    "FAIL": "failed",
    "REVIEW": "not_found",
    "CONFLICT": "not_found",
    "NOT_FOUND": "not_found",
    "INSUFFICIENT_EVIDENCE": "not_checked",
    "UNAVAILABLE": "unavailable",
    "NOT_APPLICABLE": "not_checked",
}

#: CORRECTION 2026-10-03 (peer Lane S finding, PEER-LANE-S.md): on the
#: EXCLUSION_REVOCATION dimension the evidence layer emits NOT_FOUND for a
#: CLEAN name screen — "searched SAM.gov / OIG LEIE by organisation name,
#: nothing listed" (`evidence_assembly._sam_disposition` by_name branch;
#: `_dimension_exclusion`'s no-NPI LEIE branch). The table above maps that to
#: `not_found`, the same state as a REVIEW (potential hit), and SEED_RULES_V3
#: lists `sam_gov/oig_leie == not_found` as a B4 disqualifier. On the bulk
#: path SAM is ALWAYS name-screened (no RCE field carries a UEI) and LEIE is
#: name-screened for every NPI-less record, so with a SAM key configured a
#: clean, not-listed entity would have classified B4. Reproduced without a DB
#: in lanes/S/repro_v3_name_screen_not_found_disqualifies.py.
#:
#: Fixed in the TRANSLATOR, scoped to exclusion dimensions, with no rule
#: change: a NOT_FOUND exclusion-list item becomes the classifier's own
#: exclusion-list literal "clear" ("reached it and the entity is not
#: listed" — `BucketClassifier._match_source` already accepts it for
#: `oig_leie == clear`, and the manual path emits the same literal for a
#: clean LEIE lookup). REVIEW (potential or confirmed hit, or an ambiguous
#: multi-match) stays `not_found` and still disqualifies; UNAVAILABLE and
#: INSUFFICIENT_EVIDENCE are untouched. "clear" is deliberately NOT
#: `verified`: a name-only screen is weaker than a UEI/NPI match and the
#: evidence row's note says so; the classifier treats the two alike for
#: exclusion purposes, which is the truthful reading of an enumerative list.
#: `_DISPOSITION_TO_STATE` itself is unchanged (other dimensions' NOT_FOUND —
#: e.g. PECOS enrolment not found — is a finding and must stay `not_found`).
EXCLUSION_DIMENSION = "EXCLUSION_REVOCATION"
EXCLUSION_CLEAN_SCREEN_STATE = "clear"


def evidence_item_state(dimension: Optional[str], disposition: Optional[str]) -> str:
    """Classifier state for one persisted/assembled evidence item. The single
    translation both real classifier callers use (this translator for the bulk
    path; `review_service.apply_persisted_exclusion_evidence` for persisted
    evidence on the manual path)."""
    if dimension == EXCLUSION_DIMENSION and disposition == "NOT_FOUND":
        return EXCLUSION_CLEAN_SCREEN_STATE
    return _DISPOSITION_TO_STATE.get(disposition, "not_checked")


#: The worst disposition wins when a source appears in several of the dimensions
#: below, so a source that failed somewhere is not reported verified because it
#: also passed elsewhere. "clear" ranks with "verified" (least bad).
_STATE_PRECEDENCE = ("failed", "not_found", "unavailable", "not_checked", "verified",
                     EXCLUSION_CLEAN_SCREEN_STATE)

#: Dimensions whose evidence items set a SOURCE state.
#:
#: ADDRESS is deliberately excluded. The classifier's `nppes` source means "did
#: NPPES confirm this entity" — an identity question. NPPES also appears in D4
#: as one of several addresses being compared, and letting that comparison set
#: the `nppes` source state made an address disagreement read as "NPPES could
#: not confirm the entity". Buffalo Medical Group was the case that exposed it:
#: NPPES confirmed the identity (D1 PASS) and disagreed on the address (D4
#: CONFLICT), and the conflated state pushed it to B3 on identity grounds that
#: did not exist.
#:
#: Address disagreement is a FIELD signal — `address_mismatch` — which is the
#: input the B2 rule is written against. It is counted once, in the place the
#: rules expect it.
_SOURCE_STATE_DIMENSIONS = frozenset({
    "IDENTITY", "MEDICARE_ENROLLMENT", "EXCLUSION_REVOCATION",
    "TEFCA_ALIGNMENT", "PROVIDER_ORG_RELATIONSHIP",
})

#: The D1 evidence field carrying the organisation-name comparison.
#:
#: Named here rather than written inline so the producer and the consumer can be
#: asserted equal by a test instead of agreeing by coincidence — they did not
#: agree for the whole of the first run, and nothing failed to say so.
#: `evidence_assembly._dimension_identity` is the producer.
IDENTITY_NAME_FIELD = "legal_name"

#: Classifier SIGNAL names this translator emits, derived from the shared
#: registry rather than restated here.
#:
#: THE REGISTRY IS THE SINGLE DEFINITION. This dict previously held its own copy
#: of the emitted-signal list while the test file held its own copy of the
#: unproduced-signal list, so producer, consumer and test could each be correct
#: about a different thing. `app.core.evidence_vocabulary` now holds one entry
#: per signal, recording — separately — whether it can be PRODUCED, whether its
#: VALUE DOMAIN is settled, and whether its B1-B4 CONSEQUENCE is decided.
#:
#: Signals not emitted on this path are registered there with a reason and a
#: blocking decision, not omitted. See docs/methodology_decision_package.md.
EMITTED_FIELD_SIGNALS: Dict[str, str] = {
    name: (entry.producers[0].location if entry.producers else "")
    for name, entry in _SIGNAL_REGISTRY.items()
    if any(p.path == _PATH_RCE for p in entry.producers)
}


def dimensions_to_verification_results(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Translate assembled D1-D6 evidence into the classifier's input shape.

    Produces `{"sources": {...}, "fields": {...}, "dimensions": {...}}` — the
    shape `BucketClassifier._source_state` and `._field_value` actually read.
    """
    sources: Dict[str, Dict[str, Any]] = {}
    fields: Dict[str, Any] = {}
    dimension_view: Dict[str, Any] = {}

    for dimension in evidence.get("dimensions", []):
        name = dimension["dimension"]
        dimension_view[name] = {
            "disposition": dimension["disposition"],
            "applicability": dimension["applicability"],
        }
        for item in dimension.get("evidence", []):
            if name not in _SOURCE_STATE_DIMENSIONS:
                continue
            key = _EVIDENCE_SOURCE_TO_RULE_SOURCE.get(item.get("source"))
            if not key:
                continue
            state = evidence_item_state(name, item.get("disposition"))
            current = sources.get(key, {}).get("status")
            if current is None or (
                _STATE_PRECEDENCE.index(state) < _STATE_PRECEDENCE.index(current)
            ):
                sources[key] = {
                    "status": state,
                    "disposition": item.get("disposition"),
                    "dimension": name,
                    "rule_applied": item.get("rule_applied"),
                }
                # Additive (Track A2): which SAM leg produced this answer, so a registration-only lookup is never
                # read as an exclusion screen. Absent for every other source, so their shape is unchanged.
                leg = (item.get("normalized_values") or {}).get("screening_leg")
                if leg:
                    sources[key]["screening_leg"] = leg

        # Field-level signals the B2/B3 rules look for.
        if name == "ADDRESS":
            disposition = dimension["disposition"]
            if disposition in ("REVIEW", "CONFLICT"):
                # PARTIAL_MATCH is the address layer's "differs in form, not in
                # identity" — a minor administrative variance. A hard CONFLICT
                # is not minor and must not be graded as one.
                partial = any(
                    (i.get("disposition") or "").upper() == "PARTIAL_MATCH"
                    for i in dimension.get("evidence", []))
                fields["address_mismatch"] = {
                    "severity": "minor" if partial else "major",
                    "disposition": disposition,
                }
        if name == "IDENTITY":
            # THE EVIDENCE FIELD IS `legal_name`, NOT `name`.
            #
            # This condition read `== "name"` and therefore never matched.
            # `_dimension_identity` writes `{"field": "legal_name", ...}` into
            # field_conflicts, and every other layer agrees with it: D1 declares
            # `fields_evaluated=[..., "legal_name", ...]`, the NPPES and SAM
            # connectors shape their responses under a `legal_name` key, and
            # `TEFCAEntity.legal_name_submitted` is the column. Across the 1,984
            # persisted evidence rows the value `legal_name` occurs 92 times and
            # the value `name` occurs zero times.
            #
            # So the signal `name_mismatch` was never emitted, and RULE-003 —
            # which is written to grade a minor name difference as B2 — could
            # only ever fire on `address_mismatch`. Correcting the key restores
            # the input the approved rule was written to consume; it does not
            # change what the rule does with it.
            #
            # TWO NAMESPACES, DELIBERATELY NOT MERGED. `legal_name` is the
            # EVIDENCE field (what was compared). `name_mismatch` is the
            # CLASSIFIER SIGNAL (what the rules are written against, and what
            # review_rules RULE-003 v2 references by name). Renaming the signal
            # would be a rule change; renaming the evidence field would break
            # the persisted rows. Only the lookup was wrong.
            #
            # SEVERITY IS STILL HARDCODED `minor`, AND THAT IS A KNOWN GAP.
            # Grading which name differences are minor and which are material
            # is a methodology question — `ValidationEngine` uses a five-band
            # similarity model and the dimension layer has none. Deciding the
            # bands here would be inventing methodology, so the existing
            # constant is left exactly as it was and the question is recorded in
            # docs/methodology_decision_package.md (Decision D5).
            conflicts = [c for i in dimension.get("evidence", [])
                         for c in (i.get("field_conflicts") or [])]
            if any((c.get("field") or "") == IDENTITY_NAME_FIELD for c in conflicts):
                fields["name_mismatch"] = {"severity": "minor"}

    quality = evidence.get("data_quality_flags") or []
    if "NPI_MALFORMED" in quality or "NPI_CHECK_DIGIT_FAILED" in quality:
        fields["npi_validation"] = {"status": "flagged"}

    return {"sources": sources, "fields": fields, "dimensions": dimension_view}


def next_review_id(sequence: int, year: Optional[int] = None) -> str:
    return f"REV-{year or datetime.utcnow().year}-{sequence:06d}"


async def _lock_review_id_allocation(db, year: Optional[int] = None) -> None:
    """Serialise review-id allocation for one calendar year across ALL callers.

    FOUND DURING DEV CERTIFICATION, 2026-09-02: `verify_and_classify` computed
    every review id in a batch from ONE `count(*)` read taken before the loop
    started, then formatted `count + offset + 1` locally with no further check.
    Reproduced empirically: two concurrent `verify_and_classify` calls (as
    `review_cycle.create_review_cycle` now legitimately makes possible — two
    Program Managers creating review cycles for two different deliveries at the
    same time) read the same starting count and computed overlapping ids. The
    second caller's batch failed with `IntegrityError` on the unique
    `review_id` column, losing that whole batch's verification work.

    A SELECT-then-check retry (the pattern `dq_review_bridge._next_review_id`
    and `priority_review._next_review_id` already use) does NOT fix this here:
    `verify_and_classify` commits its whole batch in ONE transaction at the
    end, and under READ COMMITTED a SELECT inside one open transaction cannot
    see another still-open transaction's rows no matter how carefully it
    checks — visibility requires the OTHER transaction to have committed
    first, which a same-instant race by definition has not.

    A transaction-scoped advisory lock (`pg_advisory_xact_lock`) fixes this
    correctly: PostgreSQL releases it only at COMMIT or ROLLBACK of the
    holding transaction, so a second caller blocked on this lock is
    guaranteed — once it proceeds — to see the first caller's rows as
    committed. This is the SAME primitive `qhin_sampling.finalize_plan`
    already uses in this codebase for the identical shape of problem ("only
    one transaction may run this critical section at a time"); this is not a
    new mechanism.

    Held for the WHOLE calling batch, not released between entities — the
    numbering must stay contiguous within one call, and `review_cycle
    .create_review_cycle`'s own batch cap (max 1000, default 200) bounds how
    long any other caller can be made to wait.
    """
    await db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"),
                     {"k": f"review_id_alloc:{year or datetime.utcnow().year}"})


def _format_review_id(sequence: int, year: Optional[int] = None) -> str:
    return f"REV-{year or datetime.utcnow().year}-{sequence:06d}"


async def _allocate_review_id(db, year: Optional[int] = None) -> str:
    """REV-YYYY-NNNNNN. Caller MUST hold `_lock_review_id_allocation` first."""
    prefix = f"REV-{year or datetime.utcnow().year}-"
    top = (await db.execute(
        select(func.max(reg.ReviewRecord.review_id))
        .where(reg.ReviewRecord.review_id.like(f"{prefix}%")))).scalar()
    nxt = (int(top.rsplit("-", 1)[1]) + 1) if top else 1
    return f"{prefix}{nxt:06d}"


async def _rule_set(db) -> List[Dict[str, Any]]:
    """Active B1-B4 rules, seeded if the table is empty."""
    from app.tefca_registry.bucket_classifier import (ensure_rules_v2, ensure_rules_v3,
                                                       ensure_seed_rules)

    count = int((await db.execute(
        select(func.count()).select_from(reg.ReviewRule))).scalar() or 0)
    if count == 0:
        # First-time seeding under concurrency: two callers that both read
        # count == 0 would both insert RULE-001 and one would die on the
        # (rule_code, version) unique index. Serialise the seed on a
        # transaction-scoped advisory lock and re-check inside it, exactly as
        # review-id allocation does (found 2026-09-17 when the concurrency
        # tests first ran against an empty rule table).
        await db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"),
                         {"k": "review_rules_seed"})
        count = int((await db.execute(
            select(func.count()).select_from(reg.ReviewRule))).scalar() or 0)
        if count == 0:
            await ensure_seed_rules(db)
            await ensure_rules_v2(db)
            await ensure_rules_v3(db)
        await db.commit()
    rows = (await db.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.is_active.is_(True)))).scalars().all()
    return [{
        "rule_code": r.rule_code, "name": r.name, "bucket": r.bucket,
        "priority": r.priority, "conditions": r.conditions,
        "description": r.description, "version": r.version,
    } for r in rows]


def _entity_npi(entity) -> Optional[str]:
    """The NPI on a resolved (FHIR-shaped) entity, or None."""
    for ident in (entity or {}).get("identifier") or []:
        if "us-npi" in str(ident.get("system") or ""):
            return (ident.get("value") or "").strip() or None
    return None


# PROFILED 2026-10-02: a 200-entity synthetic delivery measured ~1.4s/entity
# (~277s total) for what the pipeline called "classification" but which was
# actually `verify_and_classify`'s whole per-entity loop. `classify()` itself
# is pure CPU (no DB, no network — confirmed by reading it). `_allocate_review_id`
# was suspected (a MAX()+LIKE query) but profiled directly against a 25,000-row
# review_records table: 0.2-2ms, using an Index Only Scan Backward — not the
# cost. The real cost is `EvidenceService.build_evidence()`, which makes REAL
# outbound calls to NPPES/LEIE/SAM/CMS PPEF, and the original loop below
# awaited this, one entity fully, before starting the next.
#
# TWO fixes, found by profiling in sequence, not guessed:
#  1. Entity resolution + evidence gathering (read-only, I/O-bound, nothing in
#     it writes) is split into its own bounded-concurrency phase below, using a
#     throwaway session PER CONCURRENT TASK (concurrent awaits on one
#     asyncpg/SQLAlchemy AsyncSession are not supported — reusing the caller's
#     own `db` across concurrent tasks raises "another operation is in
#     progress"). The serial phase that follows — persist evidence, classify,
#     allocate the review id, write ReviewRecord — is fast (CPU + sub-
#     millisecond DB, per the profiling above) and stays serial.
#  2. Concurrency ALONE barely helped (measured: 40 entities, 55.5s with
#     concurrency=16 vs ~56s fully serial) — per-entity timing showed every
#     one of the first 16 concurrent entities individually taking 19-28s, far
#     more than a single warm external call (~0.3-0.4s). Root cause: every
#     external GET in `app/Tefca/connectors.py` opened a BRAND NEW
#     `httpx.AsyncClient()` — a fresh TCP+TLS handshake per request, even
#     between repeated calls to the SAME host — so 16 entities starting at
#     once meant 16+ simultaneous cold handshakes contending with each other.
#     Fixed at the source (`connectors.py::_shared_http_client`): one
#     process-lifetime, connection-pooled `httpx.AsyncClient`, reused by
#     every connector including the CMS PPEF client (both funnel through the
#     same `_get_with_retry`). Re-measured after both fixes: 40 entities in
#     33.4s, with the SAME "slow first wave, fast rest" shape — entities
#     16-25 (the pool already warm) completed in 0.6-3.4s each, not 19-28s.
#     That first-wave cost is a ONE-TIME connection/DNS warmup, fixed
#     regardless of delivery size — it will not scale with a 24,589-entity
#     delivery. The REAL diagnosis, caveats, and the honest extrapolation this
#     implies are in this fork's FORK-FINDINGS.md, not asserted here as a
#     solved number.
_EVIDENCE_GATHER_CONCURRENCY = 16  # bounded: a reasonable concurrency ceiling
# toward NPPES/LEIE/SAM/CMS PPEF, not an attempt to maximise throughput against
# live government APIs that were not asked to absorb 24,563 simultaneous callers.

# PROFILED 2026-10-02 (24,563-entity full-scale benchmark): `_gather_all_evidence`
# called once over the WHOLE entity_refs list held every entity's resolved-
# entity-plus-evidence dict in memory simultaneously until the ENTIRE gather
# finished -- peak RSS 1.23GB. Chosen chunk size 1000: large enough that the
# one-time connection/DNS warmup (paid once per `verify_and_classify` call
# regardless of chunking -- it is a property of the shared HTTP client, not
# the chunk) is amortised over many chunks' worth of entities rather than
# repeated per chunk; small enough to cut peak "all evidence held at once"
# memory by ~24.5x at the real 24,563-entity delivery scale (24563/1000).
_GATHER_CHUNK_SIZE = 1000
# toward NPPES/LEIE/SAM/CMS PPEF, not an attempt to maximise throughput against
# live government APIs that were not asked to absorb 24,589 simultaneous callers.


async def _resolve_and_gather_evidence(ref: str,
                                       timing_log: Optional[str] = None) -> Dict[str, Any]:
    """Phase 1 of `verify_and_classify`: resolve one entity and build its
    evidence. Read-only; opens its own session (or none, for the mock
    resolver) rather than sharing the caller's.

    `EvidenceService.local_store` (`make_local_store`) closes over whatever
    session it is built with and runs real `await db.execute(...)` reads
    against it (PPEF relational lookups) — so each concurrently-running task
    needs its OWN `EvidenceService` bound to its OWN scratch session, not a
    shared instance built from the caller's `db`. Reusing one session or one
    service across concurrent tasks would race on the same asyncpg
    connection ("another operation is in progress"), not just be slow.
    """
    from app.Tefca.entity_resolution import resolve_entity, resolver_source, SOURCE_MOCK
    from app.Tefca.evidence_service import EvidenceService
    from app.Tefca.ppef_store import make_local_store
    from app.core.database import async_session_maker

    # Optional, diagnostic-only per-entity timing, enabled by an explicit env
    # var (ARC_PIPELINE_TIMING_LOG) so this never runs in production. Lets a
    # real benchmark separate "first semaphore wave, paying one-time
    # connection/DNS warmup" from "steady-state, pool already warm" instead of
    # only ever seeing a blended average.
    import time as _tt
    _t_start = _tt.perf_counter()

    async def _log(phase: str) -> None:
        if not timing_log:
            return
        import json as _j
        with open(timing_log, "a", encoding="utf-8") as _f:
            _f.write(_j.dumps({"ref": ref, "phase": phase,
                              "t": _tt.perf_counter() - _t_start}) + "\n")

    if resolver_source() == SOURCE_MOCK:
        entity = await resolve_entity(None, ref)
        if entity is None:
            return {"ref": ref, "entity": None, "evidence": None}
        async with async_session_maker() as scratch_db:
            service = EvidenceService(local_store=make_local_store(scratch_db))
            evidence = await service.build_evidence(entity)
        await _log("end")
        return {"ref": ref, "entity": entity, "evidence": evidence}

    await _log("start")
    async with async_session_maker() as scratch_db:
        entity = await resolve_entity(scratch_db, ref)
        if entity is None:
            return {"ref": ref, "entity": None, "evidence": None}
        service = EvidenceService(local_store=make_local_store(scratch_db))
        evidence = await service.build_evidence(entity)
    await _log("end")
    return {"ref": ref, "entity": entity, "evidence": evidence}


async def _gather_all_evidence(entity_refs: List[str],
                               timing_log: Optional[str] = None) -> List[Dict[str, Any]]:
    """Runs `_resolve_and_gather_evidence` for every ref, bounded-concurrently,
    preserving `entity_refs` order in the result (asyncio.gather guarantees
    result order matches input order regardless of completion order))."""
    semaphore = asyncio.Semaphore(_EVIDENCE_GATHER_CONCURRENCY)
    import time as _t
    from app.tefca_registry.rce import pipeline_metrics as _pm

    async def _bounded(ref: str) -> Dict[str, Any]:
        # Measurement hook: one `is not None` check when no collector is
        # active (the production case); see pipeline_metrics.py.
        _t_wait = _t.perf_counter() if _pm.active() is not None else None
        async with semaphore:
            if _t_wait is not None:
                _pm.note_semaphore_wait(_t.perf_counter() - _t_wait)
            return await _resolve_and_gather_evidence(ref, timing_log=timing_log)

    return await asyncio.gather(*(_bounded(ref) for ref in entity_refs))


async def verify_and_classify(
    db,
    entity_refs: List[str],
    *,
    intake_id=None,
    actor: str = "SYSTEM",
    persist_evidence: bool = True,
) -> Dict[str, Any]:
    """Run D1-D6 → B1-B4 → tier routing for a list of entity references.

    Each entity produces:
      * one generation of dimension evidence (append-only, never overwritten)
      * one ReviewRecord carrying the bucket, rule_code and rule_version
      * one queue routing decision

    Nothing here re-runs on read. The evidence generation is stamped, and the
    report layer reads the frozen result rather than recomputing it.
    """
    from app.Tefca.evidence_service import evidence_rows_for_persistence
    from app.Tefca.models import TEFCADimensionEvidence
    from app.tefca_registry.bucket_classifier import BucketClassifier

    rules = await _rule_set(db)
    classifier = BucketClassifier()

    outcomes: List[Dict[str, Any]] = []
    buckets: Dict[str, int] = {}
    tiers: Dict[int, int] = {}
    unresolved: List[str] = []

    # Held for the whole batch — see `_lock_review_id_allocation`. Acquired
    # even when entity_refs is empty or every ref is unresolved, which costs
    # nothing (no id is ever allocated) and keeps this call site simple.
    await _lock_review_id_allocation(db)

    # Phase 1: resolve + gather evidence for every entity, bounded-concurrently
    # within each chunk (see `_resolve_and_gather_evidence`'s docstring for why
    # this used to dominate wall-clock time and why it is safe to parallelise).
    # Phase 2 below — persistence, classification, review-id allocation — stays
    # serial, in the same entity_refs order as before, now run per chunk
    # rather than once over the whole delivery. `rules`, the review-id lock,
    # and the final `db.commit()` are all still exactly-once for this whole
    # call — see `_GATHER_CHUNK_SIZE`'s comment above for why that is a
    # deliberate choice, not an oversight.
    #
    # PIPELINING ATTEMPTED AND REVERTED, 2026-10-03: a one-chunk-ahead
    # prefetch (chunk N+1's gather kicked off via `asyncio.create_task`
    # before chunk N's persist/classify loop ran, overlapping the two) was
    # built and measured, expecting to recover the duration this simple
    # chunk-at-a-time loop gives up. It did the opposite: measured at TWO
    # scales (n=2,500: ~559s vs this simple loop's n=2,500 territory;
    # n=24,563: 5,286.46s, 2.67x WORSE than the 1,978.99s unbatched
    # baseline) — confirmed at small scale too, ruling out "only compounds
    # over many chunks". Best-available hypothesis, not fully proven in the
    # time available: each concurrent gather task opens its own scratch DB
    # session (`async_session_maker()`), and under this test environment's
    # connection pooling (confirmed `NullPool` — every scratch session is a
    # brand-new physical connection, not a reused one), overlapping up to
    # 16 of those simultaneously with the long-held, uncommitted main
    # persist transaction plausibly caused real contention this simple
    # serial loop never did (gather and persist never previously overlapped
    # in time at all). Reverted rather than shipped on a hypothesis that
    # could not be fully confirmed within this pass's time budget — a
    # regression proven worse is worse than no fix, however reasoned the
    # intent. The duration-vs-memory tradeoff from chunking itself (without
    # prefetching) remains this function's honest, current state: memory is
    # bounded, full-scale duration is not yet proven to beat the unbatched
    # baseline, and that gap is intentionally left open rather than closed
    # with an unproven fix.
    import time as _dbgtime, os as _dbgos, json as _dbgjson
    _dbg_path = _dbgos.environ.get("ARC_PIPELINE_TIMING_LOG")

    for _chunk_start in range(0, len(entity_refs), _GATHER_CHUNK_SIZE):
        chunk_refs = entity_refs[_chunk_start:_chunk_start + _GATHER_CHUNK_SIZE]

        _dbg_t0 = _dbgtime.perf_counter()
        gathered = await _gather_all_evidence(chunk_refs, timing_log=_dbg_path)
        _dbg_gather_s = _dbgtime.perf_counter() - _dbg_t0
        if _dbg_path:
            with open(_dbg_path, "a", encoding="utf-8") as _f:
                _rec = {"phase": "gather_chunk", "chunk_start": _chunk_start,
                       "n": len(chunk_refs), "seconds": _dbg_gather_s}
                _f.write(_dbgjson.dumps(_rec) + "\n")
        _dbg_serial_t0 = _dbgtime.perf_counter()

        from app.tefca_registry.rce import verification_completeness as _vcomp
        for g in gathered:
            ref = g["ref"]
            entity = g["entity"]
            if entity is None:
                unresolved.append(ref)
                continue

            evidence = g["evidence"]
            entity_uuid = entity.get("_registry_entity_id")

            if persist_evidence:
                rows = evidence_rows_for_persistence(
                    str(entity_uuid or entity.get("id")), None, evidence)
                for row in rows:
                    row["review_cycle_id"] = None
                    db.add(TEFCADimensionEvidence(**row))

            verification_results = dimensions_to_verification_results(evidence)
            classification = classifier.classify(verification_results, rules=rules)

            # NPPES outcome to the delivery's issue ledger (NPI-005/006/009). One
            # open issue per outcome per record; a repeat cycle adds nothing.
            if entity_uuid is not None:
                try:
                    from app.tefca_registry.rce import verification_findings as vf
                    await vf.record_from_evidence(
                        db, entity_id=entity_uuid,
                        npi=_entity_npi(entity), evidence=evidence)
                except Exception as exc:  # noqa: BLE001 — ledger must not fail a cycle
                    logger.error("NPI verification outcome not recorded for %s: %s",
                                 entity_uuid, type(exc).__name__, exc_info=True)

            # A B1 in THIS cycle must not clear a risk signal no person has
            # cleared (2026-10-04; see prior_risk.py). Checked only on the
            # one bucket that would otherwise mark the entity verified, and
            # BEFORE this cycle's own ReviewRecord is added.
            prior_risk = None
            if classification.bucket == "B1" and entity_uuid is not None:
                from app.tefca_registry.rce import prior_risk as _prior_risk
                prior_risk = await _prior_risk.unresolved_prior_risk(db, entity_uuid)

            # Exclusion screening that never completed (see prior_risk.py):
            # recorded on every B1 it affects; withholds `verified` only when
            # ENFORCE_COMPLETE_EXCLUSION_SCREENING is on (default off).
            claim = None
            if classification.bucket == "B1":
                from app.tefca_registry.rce import prior_risk as _prior_risk
                gaps = _prior_risk.exclusion_screening_gaps(verification_results)
                if gaps:
                    claim = _prior_risk.verification_claim("B1", gaps, prior_risk)
            # Track A2 (flags default OFF): screening-state block, and the registration-only proposal.
            screening_block = None
            from app.core.config import settings
            if getattr(settings, "ENABLE_SCREENING_STATE_RECORDING", False):
                from app.tefca_registry.rce import screening_state as _ss
                reg_only = bool(getattr(settings, "SAM_REGISTRATION_ONLY_IS_INCOMPLETE_SCREENING", False))
                screening_block = _ss.derive_screening_state(
                    verification_results, registration_only_is_incomplete=reg_only)
                if reg_only and classification.bucket == "B1":
                    from app.tefca_registry.rce import prior_risk as _prior_risk
                    extra = _ss.registration_only_gaps(verification_results)
                    if extra:
                        claim = _prior_risk.verification_claim(
                            "B1", _prior_risk.exclusion_screening_gaps(verification_results) + extra, prior_risk)
            withhold_verified = bool(prior_risk) or bool(claim and claim["enforced"])

            review_id = await _allocate_review_id(db)
            tier = BUCKET_TO_TIER.get(classification.bucket, 3)
            if withhold_verified:
                # Not Tier-1 auto-complete: a person must look.
                tier = max(tier, 2)

            # THE UNMATCHED PATH STILL HAS TO CITE ITS PROVENANCE.
            #
            # When no rule matches, the classifier returns B3 with rule_code None —
            # an honest default ("the rule set does not describe this"). But a
            # determination stored with a bucket and no rule cannot be explained
            # later: an auditor asking "which rule produced this B3" gets nothing,
            # and reconciliation flags it, correctly, as untraceable.
            #
            # So the default path is recorded under an explicit reserved code rather
            # than as an absence. It is NOT a rule in `review_rules` — it is the
            # documented behaviour when none of them applied, and naming it makes
            # that visible instead of blank.
            rule_code = classification.rule_code or UNMATCHED_RULE_CODE
            rule_version = (classification.rule_version
                            if classification.rule_code else UNMATCHED_RULE_VERSION)
            rationale = classification.rationale
            if not classification.rule_code:
                rationale = (
                    f"[{UNMATCHED_RULE_CODE}] {rationale} Rule set version in force: "
                    f"{len(rules)} active rule(s), evaluated in priority order "
                    f"({', '.join(classification.evaluated_rules) or 'none'}).")

            if prior_risk:
                rationale = (rationale or "") + _prior_risk.rationale_suffix(prior_risk)

            db.add(reg.ReviewRecord(
                id=uuid.uuid4(),
                review_id=review_id,
                entity_id=entity_uuid,
                verification_results={
                    # Present ONLY when a prior risk signal is uncleared, so
                    # the snapshot shape of every other record is unchanged.
                    **({"prior_risk_not_cleared": prior_risk} if prior_risk else {}),
                    **({"verification_claim": claim} if claim else {}),
                    **({"screening_state": screening_block} if screening_block else {}),
                    # A SNAPSHOT, not a pointer. The report issued from this review
                    # must keep saying what it said after the entity is re-verified.
                    "dimensions": evidence.get("dimensions", []),
                    "applicability": evidence.get("applicability", {}),
                    "sufficiency": evidence.get("sufficiency", {}),
                    "data_quality_flags": evidence.get("data_quality_flags", []),
                    "generation_timestamp": evidence.get("generated_at"),
                    "resolution_source": entity.get("_resolution_source"),
                    # Official (POLICY_UNAPPROVED / freshness UNKNOWN) and
                    # inactive proposed policy views for the sources used,
                    # against the same pinned timestamps. Beside the
                    # classifier input; never part of it.
                    "source_policy": evidence.get("source_policy"),
                    "classifier_input": verification_results,
                },
                classification_bucket=classification.bucket,
                classification_rule=rule_code,
                classification_rule_version=rule_version,
                classification_rationale=rationale,
            ))

            db.add(reg.TefcaVerification(
                id=uuid.uuid4(), entity_id=entity_uuid, review_id=review_id,
                source="rce_arc_pipeline",
                lookup_identifier=ref[:50],
                verification_status="verified",
                detail=(f"D1-D6 assembled; classified {classification.bucket} by "
                        f"{rule_code} v{rule_version}; routed to tier {tier}."),
                data_source_label="RCE canonical registry",
            ))

            entity_row = await db.get(reg.TefcaRegEntity, entity_uuid) if entity_uuid else None
            if entity_row is not None:
                # The REVIEW TIER, stored separately from the determination. A
                # verification_status of in_review says who must look; it does not
                # say what was found.
                entity_row.verification_status = (
                    "verified" if classification.bucket == "B1" and not withhold_verified
                    else "in_review")

            buckets[classification.bucket] = buckets.get(classification.bucket, 0) + 1
            tiers[tier] = tiers.get(tier, 0) + 1
            outcomes.append({
                "entity_ref": ref,
                "entity_id": str(entity_uuid) if entity_uuid else None,
                "name": entity.get("name"),
                "review_id": review_id,
                "bucket": classification.bucket,
                "rule_code": rule_code,
                "rule_version": rule_version,
                "rule_matched": bool(classification.rule_code),
                "tier": tier,
                "assigned_role": TIER_ROLE[tier],
                "prior_risk_not_cleared": prior_risk,
                "verification_claim": claim,
                "entity_marked_verified": bool(
                    classification.bucket == "B1" and not withhold_verified),
                "verification_completeness": _vcomp.describe(
                    "verified" if classification.bucket == "B1" and not withhold_verified
                    else "in_review",
                    _vcomp.completeness({"classifier_input": verification_results})),
                "dimensions": {d["dimension"]: d["disposition"]
                               for d in evidence.get("dimensions", [])},
                "applicability": evidence.get("applicability", {}).get("dimensions", {}),
            })

        # Measurement hook (no-op unless a collector is active).
        from app.tefca_registry.rce import pipeline_metrics as _pm
        _pm.note_chunk(_chunk_start, len(chunk_refs), _dbg_gather_s,
                       _dbgtime.perf_counter() - _dbg_serial_t0)

    await db.commit()

    return {
        "requested": len(entity_refs),
        # LEGACY NAME, kept for existing consumers: this is the number of
        # entities the pipeline PROCESSED (every bucket, B4 included). It was
        # never a count of verified entities. Use the three counts below.
        "verified": len(outcomes),
        "processed": len(outcomes),
        "entities_marked_verified": sum(1 for o in outcomes if o["entity_marked_verified"]),
        "entities_marked_verified_checks_incomplete": sum(
            1 for o in outcomes
            if o["verification_completeness"]["overall_status"]
            == "verified_checks_incomplete"),
        "unresolved": unresolved,
        "bucket_counts": buckets,
        "tier_counts": {str(k): v for k, v in sorted(tiers.items())},
        "rule_set_size": len(rules),
        "outcomes": outcomes,
        "separation_note": (
            "Verification result (D1-D6), ARC determination (B1-B4) and review "
            "tier (T1-T3) are stored separately and are not collapsed. A B1 "
            "means no discrepancy was found against the evidence gathered; it "
            "is not a statement that every source passed."
        ),
    }
