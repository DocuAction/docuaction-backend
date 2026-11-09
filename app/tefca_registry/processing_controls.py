"""EX-3 configuration-consistency guard and the release-transition processing
barrier (release prerequisite, 2026-11-08).

WHY THE GUARD EXISTS (verified against the real classifier at the 2026-11-08
release checkpoint): the EX-3 producer flag (RCE_PROPOSED_EXCLUSION_STATES,
read by `arc_pipeline.evidence_item_state`) and its consumer rule component
(`exclusion_candidate_b3`, which activates RULE-008) are only safe TOGETHER.
The four flag x component combinations behave as follows under current code
(tests/test_ex3_consistency_guard_2026_11_08.py asserts every row against the
real compose/classifier/translator):

    flag OFF, component OFF   BASELINE. A candidate (producer REVIEW)
                              collapses to not_found; RULE-005 v3 lists that
                              as a disqualifier -> B4. Today's deployed
                              behavior; the guard changes nothing here.
    flag ON,  component OFF   MISMATCH. A SAM candidate with otherwise-clean
                              sources classifies B1 via RULE-001 (the baseline
                              has no positive sam_gov requirement and
                              possible_match trips none of its none_of
                              guards); a LEIE candidate falls to B3-default
                              (B1/B2 require oig_leie clear). A silent clean
                              pass is possible -> REFUSE.
    flag OFF, component ON    MISMATCH. Candidates collapse to not_found;
                              the component removed RULE-005's not_found
                              clauses, and the B1/B2 none_of guards catch the
                              state -> B3-default. No silent pass, but the
                              approved configuration is NOT in effect
                              (RULE-008 is unreachable) -> REFUSE rather than
                              classify under a half-applied configuration.
    flag ON,  component ON    APPROVED CONFIGURATION. Candidate -> B3 via
                              RULE-008; confirmed exclusion -> B4 via
                              RULE-005; clean -> B1.

THE GUARD refuses classification on a mismatch. It NEVER silently changes a
bucket to conceal the configuration error — refusal is its only action. It
checks the EFFECTIVE rule configuration: the exact rule list the calling path
is about to classify with (bulk: `arc_pipeline._rule_set`; manual: the module
classifier's `load_rules`, cache included — if that cache is stale relative to
a just-run activation the guard sees the same stale rules classification would
use, so the stale-cache window produces refusals, never wrong buckets, until
the worker restarts or the cache expires). Historical or retired RULE-008 rows
play no part: only a rule in the effective list with a POSITIVE possible_match
clause counts as the consumer being active (a none_of guard alone does not —
without RULE-008's positive routing the approved B3 outcome is unreachable).

THE BARRIER is the enforceable release-transition stop:

    STATE         the latest `processing_barrier_set` governance audit row
                  (metadata.state on/off) — durable, auditable, append-only,
                  no schema change.
    ENFORCEMENT   every supported classification entry point calls
                  `enter_processing` BEFORE any classification write: it takes
                  a SHARED advisory transaction lock on BARRIER_LOCK_KEY, then
                  reads the barrier state in the same transaction and refuses
                  when the barrier is on.
    DRAIN PROOF   `barrier_status` attempts the EXCLUSIVE form of the same
                  lock. The exclusive lock can only be acquired when no
                  in-flight classification transaction still holds the shared
                  lock, so "barrier on and drained" is a verified lock
                  acquisition — never "no job happens to be visible".

SUPPORTED ENTRY POINTS that can initiate relevant (classification-writing)
processing, each wired to `enter_processing` + the guard:
    * `arc_pipeline.verify_and_classify` — the bulk path (covers its callers:
      the RCE verify route and `review_cycle.create_review_cycle`'s batches)
    * `review_service.run_review` — the manual path (covers the ordinary
      verify endpoint and the priority review)
    * `review_cycle.create_review_cycle` — checked again at its own top so a
      cycle refuses BEFORE its pre-verification member-status writes
Not wired, deliberately: `automated_verification` and `delivery_runner` never
run the B1-B4 classifier (asserted by their own docstrings and tests), and
`shadow_reassessment` / the classification-preview route are non-persisting
diagnostics that MUST keep working while the barrier is on — they are the
transition's verification vehicle.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Audit action recording a barrier transition. The newest row wins.
BARRIER_EVENT = "processing_barrier_set"

#: Advisory-lock key string (hashed via hashtext, the codebase's existing
#: pattern) marking an in-flight classification transaction.
BARRIER_LOCK_KEY = "rce_classification_processing"

#: Sources whose possible_match state the EX-3 component consumes.
EXCLUSION_CONSUMER_SOURCES = ("sam_gov", "oig_leie")


class ExclusionConfigurationMismatch(RuntimeError):
    """The EX-3 producer flag and the exclusion_candidate_b3 rule component
    disagree; classification is refused rather than run half-configured."""


class ProcessingBarrierActive(RuntimeError):
    """The release-transition processing barrier is on; new classification
    processing is refused until it is lifted."""


# ── EX-3 configuration-consistency guard ─────────────────────────────────────

def exclusion_states_consumer_active(rules: Optional[List[dict]]) -> bool:
    """True when the EFFECTIVE rule list consumes the possible_match state
    through a POSITIVE clause (any_of/all_of) on an exclusion source — the
    behavioral signature of the `exclusion_candidate_b3` component (RULE-008).
    A none_of guard alone is not consumption: it can only exclude, so the
    approved B3 routing would still be unreachable."""
    for rule in rules or []:
        conditions = (rule.get("conditions") or {})
        for clause in ("any_of", "all_of"):
            for cond in conditions.get(clause) or []:
                if (isinstance(cond, dict)
                        and cond.get("status") == "possible_match"
                        and cond.get("source") in EXCLUSION_CONSUMER_SOURCES):
                    return True
    return False


def assert_exclusion_configuration_consistent(rules: Optional[List[dict]]) -> None:
    """Refuse classification when the EX-3 producer flag and the effective
    rule configuration disagree. Pure decision over the rule list the caller
    is about to classify with; raises `ExclusionConfigurationMismatch` on a
    mismatch and returns silently otherwise (both-off baseline preserved)."""
    # Deferred import: arc_pipeline imports widely and this module is imported
    # by low-level callers; the flag reader is a fresh os.getenv each call.
    from app.tefca_registry.rce.arc_pipeline import (
        PROPOSED_EXCLUSION_STATES_ENV, _proposed_exclusion_states_on)

    producer_on = _proposed_exclusion_states_on()
    consumer_on = exclusion_states_consumer_active(rules)
    if producer_on == consumer_on:
        return
    if producer_on:
        raise ExclusionConfigurationMismatch(
            f"{PROPOSED_EXCLUSION_STATES_ENV} is set but the effective rule "
            f"configuration has no rule consuming possible_match (the "
            f"exclusion_candidate_b3 component / RULE-008 is not in effect). "
            f"Under this half-configuration an otherwise-clean SAM exclusion "
            f"candidate would classify B1. Classification is refused; no "
            f"bucket is rerouted. Either activate exclusion_candidate_b3 "
            f"through the governed activation with its recorded approval, or "
            f"unset the flag.")
    raise ExclusionConfigurationMismatch(
        f"The effective rule configuration consumes possible_match (the "
        f"exclusion_candidate_b3 component / RULE-008 is active) but "
        f"{PROPOSED_EXCLUSION_STATES_ENV} is not set, so no producer ever "
        f"emits that state and RULE-008 is unreachable. The approved EX-3 "
        f"configuration is not in effect. Classification is refused; no "
        f"bucket is rerouted. Either set the flag (covered by the same "
        f"recorded approval as the component) or retire the component "
        f"through the governed activation.")


# ── release-transition processing barrier ────────────────────────────────────

async def barrier_record(session):
    """The newest `processing_barrier_set` audit row, or None.

    `created_at` is server-defaulted to now(), which Postgres fixes at
    TRANSACTION start — two transitions written in one transaction would tie.
    `set_barrier` therefore also stamps a wall-clock `at` into the metadata,
    used as the tiebreaker."""
    from sqlalchemy import select
    from app.tefca_registry import models as reg

    return (await session.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action == BARRIER_EVENT)
        .order_by(reg.TefcaRegAuditLog.created_at.desc(),
                  reg.TefcaRegAuditLog.metadata_["at"].astext.desc())
        .limit(1))).scalars().first()


async def barrier_is_on(session) -> bool:
    row = await barrier_record(session)
    return bool(row is not None
                and ((row.metadata_ or {}).get("state") == "on"))


async def enter_processing(session) -> None:
    """Mark this transaction as in-flight relevant processing and enforce the
    barrier. MUST be called before any classification write, on the same
    session/transaction that will perform the writes.

    Order is deliberate: the SHARED advisory transaction lock is taken FIRST,
    then the barrier state is read in the same transaction. A worker that read
    "off" just before the barrier turned on still holds the shared lock, so
    `barrier_status`'s exclusive acquisition waits for it — the race closes at
    the lock, not at the flag read."""
    from sqlalchemy import text

    await session.execute(
        text("select pg_advisory_xact_lock_shared(hashtext(:k))"),
        {"k": BARRIER_LOCK_KEY})
    if await barrier_is_on(session):
        raise ProcessingBarrierActive(
            "The release-transition processing barrier is ON: new "
            "classification processing is refused before any write. Lift the "
            "barrier through the governed admin route once the transition's "
            "configuration readback and non-persisting checks have passed.")


async def set_barrier(session, *, on: bool, actor_id=None,
                      actor_email: Optional[str] = None,
                      ip_address: Optional[str] = None,
                      reason: str = "") -> dict:
    """Stage the barrier transition as a governance audit row. Does NOT
    commit — the caller owns the transaction (one commit, event included)."""
    from datetime import datetime, timezone

    from app.tefca_registry import audit as reg_audit

    state = "on" if on else "off"
    reg_audit.record(session, BARRIER_EVENT, None,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"state": state, "reason": reason,
                               # Wall-clock tiebreaker: created_at is fixed at
                               # transaction start, so same-transaction
                               # transitions would otherwise tie.
                               "at": datetime.now(timezone.utc).isoformat()})
    return {"state": state, "reason": reason}


async def barrier_status(session) -> Dict[str, Any]:
    """Barrier state plus a VERIFIED drain check.

    `in_flight_drained` is established by attempting the EXCLUSIVE advisory
    transaction lock that every classification transaction holds in SHARED
    mode: acquisition succeeding means no relevant processing transaction is
    in flight right now. With the barrier on, no new one can start (every
    entry point checks the flag after taking its shared lock), so
    state=on + drained=True is a durable "barrier on and verified", not a
    point-in-time absence of visible jobs. The probe lock is transaction-
    scoped and released when the caller's transaction ends."""
    from sqlalchemy import text

    row = await barrier_record(session)
    state = ((row.metadata_ or {}).get("state") if row is not None else "off") or "off"
    drained = bool((await session.execute(
        text("select pg_try_advisory_xact_lock(hashtext(:k))"),
        {"k": BARRIER_LOCK_KEY})).scalar())
    return {
        "state": state,
        "set_at": (row.created_at.isoformat() if row is not None and row.created_at
                   else None),
        "set_by": (row.actor_email if row is not None else None),
        "reason": ((row.metadata_ or {}).get("reason") if row is not None else None),
        "in_flight_drained": drained,
        "verified": state == "on" and drained,
        "note": ("Drain is verified by acquiring the exclusive form of the "
                 "advisory lock every classification transaction holds in "
                 "shared mode — not by the absence of visible jobs."),
    }


# ── configuration readback (step 5 of the release sequence) ─────────────────

async def effective_configuration(session) -> Dict[str, Any]:
    """Fresh, authoritative readback of what classification on THIS worker
    would use: the effective rule set (loaded with the classifier's own
    filter — newest active version per code, UTC effective/retired dates,
    positive-clause requirement), the EX-3 producer flag, the consistency
    verdict, and the barrier state. Read-only; usable while the barrier is on
    (it is the transition's verification vehicle)."""
    import os

    from app.tefca_registry.bucket_classifier import (CACHE_TTL_SECONDS,
                                                      BucketClassifier)
    from app.tefca_registry.rce.arc_pipeline import (
        PROPOSED_EXCLUSION_STATES_ENV, _proposed_exclusion_states_on)

    loader = BucketClassifier()            # fresh instance: no cached rules
    rules = await loader.load_rules(session)
    producer_on = _proposed_exclusion_states_on()
    consumer_on = exclusion_states_consumer_active(rules)
    return {
        "rules": [{"rule_code": r["rule_code"], "bucket": r["bucket"],
                   "priority": r["priority"], "version": r.get("version")}
                  for r in rules],
        "rejected_rule_count": len(loader.rejected_rules),
        "exclusion_states_flag": {
            "env": PROPOSED_EXCLUSION_STATES_ENV,
            "raw": os.getenv(PROPOSED_EXCLUSION_STATES_ENV),
            "enabled": producer_on,
        },
        "exclusion_candidate_component_effective": consumer_on,
        "configuration_consistent": producer_on == consumer_on,
        "barrier": await barrier_status(session),
        "cache_note": (
            f"This readback loads fresh from the database. Request-path "
            f"classifiers cache rules per process for up to "
            f"{CACHE_TTL_SECONDS}s; the release sequence's worker restart "
            f"(after the flag is set) empties those caches, and the "
            f"consistency guard refuses — never misclassifies — inside any "
            f"remaining stale-cache window."),
    }
