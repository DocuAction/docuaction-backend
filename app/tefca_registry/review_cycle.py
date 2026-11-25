"""Create the official review cycle for a delivery — the link that was missing.

WHAT INDEPENDENT REVIEW FOUND
─────────────────────────────
The workflow is stated as

    delivery → ready → REVIEW CYCLE → QHIN assignment → analyst → QA

and GO 1 wrote "Review Cycles (existing)". It was not. Three facts, each
verified against source:

  * `qhin_sampling.finalize_plan` — the approved per-QHIN methodology, the only
    thing that may draw an official sample — was called by NO route. The
    frontend's "Create review cycle" linked to a page that posts to the legacy
    `/api/v1/tefca/cycles`, which knows nothing about a delivery, a
    reconciliation verdict or a QHIN stratum.
  * `SampleEntity.review_id` was written by NOTHING. A drawn plan's members
    never acquired a review case, so `plan_completion` reported every member as
    `no_review_case` forever, and the analyst queue never saw the sample.
  * `POST /samples` — the generic Cochran sampler over the whole registry, at
    contributor — was the only reachable sampler, and it has no reconciliation
    gate and no QHIN strata.

This module is the missing transition, and only that. It introduces no
sampling, no classification and no decision vocabulary. It calls the three
functions that already own those and writes the two links that nothing wrote.

THE GATE
────────
Reconciliation must have PASSED. Not run — passed. A delivery whose A–F
populations do not close is not a population, and drawing a sample from it
would make the sample exactly as unaccountable as the delivery. The check is
performed here, server-side, on every call; the dashboard's disabled button is
a courtesy, not the control.

IDEMPOTENT, AND SERIALISED PER DELIVERY
───────────────────────────────────────
`finalize_plan` returns the plan already drawn rather than redrawing, so asking
twice yields one sample. Verification is then run ONLY for members whose
`review_id` is still NULL, so asking twice yields one review case per member.
The work is bounded per call (`batch`) because verification calls external
Government sources per entity and a 3,000-member sample is minutes of upstream
traffic; the caller repeats until `remaining` is zero.

`verify_and_classify` commits internally, which would release a
transaction-scoped advisory lock mid-way and let a second caller verify the
same members before the first had linked them. So the lock here is
SESSION-scoped (`pg_advisory_lock`), held across that commit and released in
`finally`. One cycle-creation per delivery at a time, no matter how many
Program Managers click.

WHAT EVERY REVIEW CASE CREATED HERE CARRIES
───────────────────────────────────────────
    ReviewRecord.sample_id                       → the plan it belongs to
    verification_results.source_intake_id        → the delivery
    verification_results.queue_source            → RCE_SAMPLE_REVIEW
    verification_results.sample_id               → the plan (again, for filters)
    SampleEntity.review_id                       → back-link, so completion works

`source_intake_id` is the key `case_assignment._queue` and `qhin_workload`
scope by. A review case without it is invisible to both, which is precisely
what was wrong before.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m

#: D-15 PROPOSED, INACTIVE by default: when set truthy, review-cycle
#: verification SKIPS members whose curated record is currently HELD
#: (including promoted-then-re-HELD), recording VERIFY_SKIPPED_HELD per member.
#: HELD is a processing state, never a bucket. May only be set under the
#: recorded D-15 decision.
PROPOSED_VERIFY_SKIP_HELD_ENV = "RCE_PROPOSED_VERIFY_SKIP_HELD"


def _proposed_verify_skip_held() -> bool:
    """True when the D-15 proposed HELD/re-HELD verification skip is enabled
    via PROPOSED_VERIFY_SKIP_HELD_ENV (default OFF; gated on the recorded
    decision). Read fresh each call so tests can set it without reimport."""
    import os
    return (os.getenv(PROPOSED_VERIFY_SKIP_HELD_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on")


#: Member status for a D-15 skip. A member in this status stays UNLINKED
#: (review_id is None — no review case was invented for it) but is no longer
#: selectable, so repeated cycle calls converge; the sample row itself is the
#: durable record of why.
SKIPPED_HELD_STATUS = "skipped_held"


async def _unlinked_members(db, sample_id):
    """The sample members still needing verification: review_id unset AND not
    marked `skipped_held` (D-15). Deterministic order. Factored out so the
    selection semantics are directly testable."""
    from sqlalchemy import func as safunc, select as saselect
    return (await db.execute(
        saselect(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == sample_id,
               reg.SampleEntity.review_id.is_(None),
               safunc.coalesce(reg.SampleEntity.review_status, "")
               != SKIPPED_HELD_STATUS)
        .order_by(reg.SampleEntity.stratum, reg.SampleEntity.entity_id)
    )).scalars().all()


async def _held_entity_ids(db, intake_id, entity_ids) -> set:
    """Entity ids among `entity_ids` whose curated record in THIS delivery is
    CURRENTLY HELD — the D-15 skip set. Reads the live `record_status`, so a
    promoted-then-re-HELD record (post-promotion verification rewrote the
    disposition after the plan was drawn) is caught exactly like an original
    hold. Factored out so the proposed semantics are testable against the
    database directly."""
    from sqlalchemy import select as _select
    if not entity_ids:
        return set()
    # Chunked IN list (review finding 2026-11-08): with full_population the
    # sample is the whole delivery, and asyncpg caps one statement at 32,767
    # bind parameters — a single IN over a very large delivery would fail the
    # cycle before any member is verified, on every retry.
    ids = list(entity_ids)
    held: set = set()
    for i in range(0, len(ids), 5000):
        held.update((await db.execute(
            _select(m.RceCuratedRecord.canonical_entity_id)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.in_(ids[i:i + 5000]),
                   m.RceCuratedRecord.record_status == "HELD")
        )).scalars().all())
    return held

logger = logging.getLogger(__name__)

#: The queue_source stamped on every review case created from an official
#: sample. Distinct from `dq_review_bridge.QUEUE_SOURCE` (RCE_DQ_HUMAN_REQUIRED)
#: so the two populations can be told apart in the same queue.
QUEUE_SOURCE = "RCE_SAMPLE_REVIEW"

#: Members verified per call. Bounded because each one is several upstream
#: Government-source calls; see the module docstring.
DEFAULT_BATCH = 200
MAX_BATCH = 1000


class CycleRefused(RuntimeError):
    """The cycle could not be created, and the reason is stated."""


#: Members one call may find NOT reviewable for identity reasons before it stops walking. Each costs local reads
#: only (no source is asked). A call that reaches it says so and returns `next_after_member_id` to continue.
MAX_NOT_REVIEWABLE_PER_CALL = 2000

#: Members whose delivered references are read per query while walking.
_REF_CHUNK = 500

NOT_REVIEWABLE_NO_REFERENCE = ("this delivery's curated records carry no delivered reference for the sample "
                               "member; nothing can be resolved or verified")
NOT_REVIEWABLE_UNRESOLVED = "the delivered reference does not resolve to a registry entity"
NOT_REVIEWABLE_OTHER_ENTITY = ("the delivered reference resolves to a different registry entity than the sample "
                               "member; a review of that entity is not a review of this member")
NOT_REVIEWABLE_NO_REVIEW = "verification produced no review for this member"
NOT_REVIEWABLE_CHANGED = ("the delivered reference resolved to the sample member when it was checked and to a "
                          "different registry entity when it was verified; the review written is not this "
                          "member's and was not linked")

#: Why a member was set aside, as far as this call can know.
#:   identity  - decided from the delivery's own stored data by a deterministic rule (the reference resolves to
#:               another entity, an NPI gate refuses the anchored record, the member has no delivered reference).
#:               The same call tomorrow gives the same answer until the data is corrected.
#:   transient - NOT established as an identity fact: the resolver returned nothing (it also returns nothing on a
#:               database fault - `entity_resolution.resolve_from_db` never raises), verification produced no
#:               review, or the resolution changed during the call. Another call may succeed.
#: Only `identity` members can justify the stop signal.
KIND_IDENTITY = "identity"
KIND_TRANSIENT = "transient"


async def _identity_precheck(db, intake_uuid, member_entity_id, ref, *, assessment_date=None) -> Optional[str]:
    """Why a sample member cannot be reviewed for identity reasons, decided BEFORE any source is asked - or None.

    Local reads only. It answers with a reason only on positive knowledge, using the rules the bulk writer itself
    applies, so it never withholds a member the writer would have reviewed as that member:
      * the reference resolves to a registry entity that is NOT the sample member (the writer would review that
        other entity; linking it to this member is the defect this function exists to prevent);
      * the reference is anchored to one delivered record of this delivery and `arc_pipeline.anchored_npi_refusal`
        refuses it (the writer gathers evidence first and then refuses the same record, every call).
    Anything it cannot establish - a reference that does not resolve here, a resolver that supplies no registry
    identity (the fixture resolver) - is left to the writer, exactly as before."""
    return (await _identity_precheck_detail(db, intake_uuid, member_entity_id, ref,
                                            assessment_date=assessment_date))["reason"]


async def _identity_precheck_detail(db, intake_uuid, member_entity_id, ref, *,
                                    assessment_date=None) -> Dict[str, Any]:
    """`_identity_precheck` with the facts it decided on (it IS the pre-check: the function above returns this
    function's `reason`). Local reads only; writes nothing. No delivered value is returned - only what was
    established:

      reason                None, or why the member cannot be reviewed for identity reasons
      resolved              the reference resolved to a registry entity with a registry identity
      lineage               how the delivery anchors the reference (`arc_pipeline.LINEAGE_*`), when resolved to
                            the member's own entity
      submitted_npi_blank   for a refused ANCHORED record: whether the record delivered no NPI at all
      classified_on_submitted_values
                            the record submitted no NPI, the registry holds one for the entity, and the blank-NPI
                            rule applies to this delivery: it is reviewable and will be classified on its own
                            delivered values (`arc_pipeline.record_identity`)

    `assessment_date` is the delivery's own assessment date; resolved here when not given.
    """
    from app.Tefca.entity_resolution import resolve_entity
    from app.tefca_registry.rce import arc_pipeline as ap

    detail: Dict[str, Any] = {"reason": None, "resolved": False, "lineage": None, "submitted_npi_blank": None,
                              "classified_on_submitted_values": False}
    entity = await resolve_entity(db, ref)
    if entity is None:
        return detail
    resolved_id = entity.get("_registry_entity_id")
    if resolved_id is None:
        return detail
    detail["resolved"] = True
    if str(resolved_id) != str(member_entity_id):
        detail["reason"] = NOT_REVIEWABLE_OTHER_ENTITY
        return detail
    anchors = await ap._source_anchors(db, intake_uuid, [ref])
    source_record_id, lineage = ap._source_anchor(anchors, ref, resolved_id)
    detail["lineage"] = lineage
    selected = await db.get(m.RceSourceRecord, source_record_id) if source_record_id else None
    # The writer's own decision (`arc_pipeline.record_identity`), with the delivery's assessment date: a record
    # that submitted no NPI is not refused for an NPI the registry holds when the blank-NPI rule applies to this
    # delivery - it will be classified on its submitted values.
    if assessment_date is None:
        from app.tefca_registry.rce.assessment_context import resolve_assessment_date
        assessment_date = (await resolve_assessment_date(db, intake_uuid)).value
    refusal, _entity_to_use, withheld = ap.record_identity(entity, selected, assessment_date=assessment_date)
    detail["reason"] = refusal
    detail["classified_on_submitted_values"] = bool(withheld and not refusal)
    if refusal and selected is not None:
        delivered = selected.parsed or {}
        detail["submitted_npi_blank"] = not str(delivered.get("NPI", selected.npi) or "").strip()
    return detail


class IdentityPrecheckUnavailable(RuntimeError):
    """The identity pre-check cannot predict anything in this configuration. Routes turn it into a 409."""


#: Not-reviewable members listed per page of the preview; the counts are always exact.
PRECHECK_LIST_CAP = 500
PRECHECK_DEFAULT_LIMIT = 2000
PRECHECK_MAX_LIMIT = 5000

OUTCOME_REVIEWABLE = "reviewable"
OUTCOME_NOT_REVIEWABLE = "not_reviewable"
OUTCOME_REFERENCE_NOT_RESOLVED = "reference_not_resolved"

PRECHECK_NOTE = (
    "A PREDICTION, not a result: what the review cycle's identity pre-check would decide for each member from "
    "the local registry and this delivery's stored records at the time of this call. No source was asked and "
    "nothing was written. Promotion, corrections or analyst decisions made after this call can change it. A "
    "member listed as reviewable may still be set aside by verification itself; a reference that does not "
    "resolve here is reported as such (kind transient) and is not counted as refused.")


async def preview_identity_precheck(db, intake_id, *, after_entity_id=None,
                                    limit: int = PRECHECK_DEFAULT_LIMIT) -> Dict[str, Any]:
    """Count, BEFORE any review cycle exists, the members the cycle would not be able to review for identity
    reasons. READ-ONLY: selects only, no flush, no commit, no audit row, no source call.

    Members are the sampling frame `finalize_plan` uses for this delivery
    (`qhin_sampling.resolve_qhin_strata(include_held=False)`: one member per distinct promoted entity of an
    eligible unit) - with `full_population` that frame IS the sample. Each member's delivered reference is read
    as the cycle reads it (`_refs_for`) and given to the cycle's own `_identity_precheck_detail`. One page of
    members per call, ordered by entity id; `next_after_entity_id` continues.

    Never returns an NPI or any other delivered value: entity ids, delivered references (the identifier the
    cycle itself reports) and reasons only."""
    from app.Tefca.entity_resolution import SOURCE_DB, resolver_source
    from app.tefca_registry.qhin_sampling import resolve_qhin_strata
    from app.tefca_registry.rce import arc_pipeline as ap

    source = resolver_source()
    if source != SOURCE_DB:
        # With the fixture resolver no reference carries a registry identity, the pre-check can establish
        # nothing, and every member would be reported "reviewable". That would be a false all-clear.
        raise IdentityPrecheckUnavailable(
            f"the identity pre-check needs the registry resolver (ENTITY_RESOLVER_SOURCE=db); it is "
            f"{source!r} here, so no member's identity can be predicted and nothing is reported")

    intake_uuid = intake_id if isinstance(intake_id, uuid.UUID) else uuid.UUID(str(intake_id))
    limit = max(1, min(int(limit or PRECHECK_DEFAULT_LIMIT), PRECHECK_MAX_LIMIT))
    after = uuid.UUID(str(after_entity_id)) if after_entity_id else None

    # The delivery's own assessment date, read once with the resolver the cycle and the writer use: it decides
    # whether the blank-NPI rule applies to this delivery at all.
    from app.tefca_registry.rce.assessment_context import resolve_assessment_date
    assessment = await resolve_assessment_date(db, intake_uuid)
    rule_applies = assessment.value >= ap.BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE

    eligible, unresolved = await resolve_qhin_strata(db, intake_uuid, include_held=False)
    member_ids = sorted({unit["entity_id"] for unit in eligible})
    not_in_frame: Dict[str, int] = {}
    for unit in unresolved:
        not_in_frame[unit["reason"]] = not_in_frame.get(unit["reason"], 0) + 1
    held_out = sum(1 for unit in unresolved if unit.get("record_status") == "HELD"
                   and str(unit.get("reason", "")).startswith("HELD"))
    # Members with ANOTHER curated record of this delivery currently HELD: the D-15 skip set
    # (`_held_entity_ids`), verified today and skipped when RCE_PROPOSED_VERIFY_SKIP_HELD is on.
    held_members = await _held_entity_ids(db, intake_uuid, member_ids)

    page_ids = [e for e in member_ids if after is None or e > after][:limit]
    remaining_after_page = sum(1 for e in member_ids if after is None or e > after) - len(page_ids)

    counts = {OUTCOME_REVIEWABLE: 0, OUTCOME_NOT_REVIEWABLE: 0, OUTCOME_REFERENCE_NOT_RESOLVED: 0}
    by_reason: Dict[Any, int] = {}
    gate_one = {"submitted_npi_blank": 0, "submitted_npi_present_and_different": 0}
    listed: List[Dict[str, Any]] = []
    listed_total = 0
    held_in_page = held_not_reviewable = held_reviewable = 0
    on_submitted_values: List[Dict[str, Any]] = []
    on_submitted_values_total = 0

    def _count(entity_id, ref, reason, kind, outcome):
        nonlocal listed_total, held_not_reviewable
        counts[outcome] += 1
        if entity_id in held_members:
            held_not_reviewable += 1
        by_reason[(reason, kind)] = by_reason.get((reason, kind), 0) + 1
        listed_total += 1
        if len(listed) < PRECHECK_LIST_CAP:
            listed.append({"entity_id": str(entity_id), "entity_ref": ref, "reason": reason, "kind": kind,
                           "currently_held": entity_id in held_members})

    for start in range(0, len(page_ids), _REF_CHUNK):
        chunk = page_ids[start:start + _REF_CHUNK]
        refs = await _refs_for(db, intake_uuid, chunk)
        for entity_id in chunk:
            if entity_id in held_members:
                held_in_page += 1
            ref = refs.get(entity_id)
            if not ref:
                _count(entity_id, None, NOT_REVIEWABLE_NO_REFERENCE, KIND_IDENTITY, OUTCOME_NOT_REVIEWABLE)
                continue
            detail = await _identity_precheck_detail(db, intake_uuid, entity_id, ref,
                                                     assessment_date=assessment.value)
            if detail["reason"]:
                _count(entity_id, ref, detail["reason"], KIND_IDENTITY, OUTCOME_NOT_REVIEWABLE)
                if detail["reason"] == ap.REFUSAL_SUBMITTED_NPI_DIFFERS:
                    gate_one["submitted_npi_blank" if detail["submitted_npi_blank"]
                             else "submitted_npi_present_and_different"] += 1
            elif not detail["resolved"]:
                _count(entity_id, ref, NOT_REVIEWABLE_UNRESOLVED, KIND_TRANSIENT, OUTCOME_REFERENCE_NOT_RESOLVED)
            else:
                counts[OUTCOME_REVIEWABLE] += 1
                if entity_id in held_members:
                    held_reviewable += 1
                if detail["classified_on_submitted_values"]:
                    on_submitted_values_total += 1
                    if len(on_submitted_values) < PRECHECK_LIST_CAP:
                        on_submitted_values.append({"entity_id": str(entity_id), "entity_ref": ref,
                                                    "currently_held": entity_id in held_members})

    return {
        "intake_id": str(intake_uuid),
        "read_only": True,
        "note": PRECHECK_NOTE,
        "resolver_source": source,
        # Whether records that submitted NO NPI, on an entity the registry holds an NPI for, will be CLASSIFIED on
        # their submitted values (rule applies) or REFUSED as before (it does not) - decided by this delivery's own
        # assessment date against the effective date.
        "blank_npi_rule": {
            "effective_date": ap.BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE.isoformat(),
            "delivery_assessment_date": assessment.value.isoformat(),
            "delivery_assessment_date_source": assessment.source,
            "applies_to_this_delivery": rule_applies,
            "effect": ("blank-NPI records on an entity with a registry NPI are reviewable and will be classified "
                       "on their submitted values (counted under counts.classified_on_submitted_values)"
                       if rule_applies else
                       "blank-NPI records on an entity with a registry NPI are refused as before (counted under "
                       "counts.submitted_npi_gate.submitted_npi_blank)"),
        },
        # The whole delivery, the same on every page.
        "frame": {
            "members_total": len(member_ids),
            # One eligible unit per member is what the cycle assumes. False means some entity has more than one
            # eligible record in this delivery: DO NOT START THE CYCLE until that is understood.
            "frame_consistent": len(eligible) == len(member_ids),
            "eligible_units": len(eligible),
            "not_in_frame_total": len(unresolved),
            "not_in_frame_by_reason": [{"reason": reason, "count": n}
                                       for reason, n in sorted(not_in_frame.items())],
            # HELD records are outside the frame by default (include_held=False): never members.
            "held_records_excluded_from_frame": held_out,
            # Members that also have a curated record of this delivery currently HELD.
            "members_currently_held": len(held_members),
            "verify_skip_held_flag_on": _proposed_verify_skip_held(),
        },
        "page": {
            "after_entity_id": str(after) if after else None,
            "limit": limit,
            "examined": len(page_ids),
            "next_after_entity_id": (str(page_ids[-1]) if page_ids and remaining_after_page > 0 else None),
            "members_after_this_page": remaining_after_page,
            "complete": remaining_after_page == 0,
        },
        # This page only. Add the pages up for the delivery.
        "counts": {
            **counts,
            "by_reason": [{"reason": reason, "kind": kind, "count": n}
                          for (reason, kind), n in sorted(by_reason.items())],
            # Of the members refused because the registered NPI is not the submitted one: how many records
            # delivered NO NPI (they are refused before a missing-NPI rule could see them) and how many
            # delivered a different one.
            "submitted_npi_gate": gate_one,
            # Of this page's members: currently HELD (still pre-checked above; verification skips them only
            # when RCE_PROPOSED_VERIFY_SKIP_HELD is on).
            "members_currently_held": held_in_page,
            "held_and_reviewable": held_reviewable,
            "held_and_not_reviewable": held_not_reviewable,
            # Of `reviewable`: members whose record submitted no NPI while the registry holds one for the entity.
            # They WILL BE CLASSIFIED ON THEIR SUBMITTED VALUES (the registry NPI withheld, NPI-keyed sources not
            # asked). Always 0 when blank_npi_rule.applies_to_this_delivery is false.
            "classified_on_submitted_values": on_submitted_values_total,
        },
        # The members counted under counts.classified_on_submitted_values (bounded like the list below).
        "classified_on_submitted_values": on_submitted_values,
        "classified_on_submitted_values_truncated": on_submitted_values_total > len(on_submitted_values),
        "not_reviewable": listed,
        "not_reviewable_listed": len(listed),
        "not_reviewable_total": listed_total,
        "not_reviewable_truncated": listed_total > len(listed),
    }


def _merge_verification(total: Dict[str, Any], part: Dict[str, Any]) -> Dict[str, Any]:
    """Fold one `verify_and_classify` summary into the call's summary (one call may now verify in several steps)."""
    if not total.get("requested"):
        return dict(part)
    out = dict(total)
    for key in ("requested", "verified"):
        out[key] = int(total.get(key) or 0) + int(part.get(key) or 0)
    out["unresolved"] = list(total.get("unresolved") or []) + list(part.get("unresolved") or [])
    counts = dict(total.get("bucket_counts") or {})
    for bucket, n in (part.get("bucket_counts") or {}).items():
        counts[bucket] = counts.get(bucket, 0) + n
    out["bucket_counts"] = counts
    lineage = dict(total.get("lineage") or {})
    for outcome, n in (part.get("lineage") or {}).items():
        lineage[outcome] = lineage.get(outcome, 0) + n
    out["lineage"] = lineage
    for key in ("evidence_not_stored_unanchored", "refused_before_gather",
                "classified_on_submitted_values", "evidence_snapshot_only_submitted_values"):
        out[key] = list(total.get(key) or []) + list(part.get(key) or [])
    out["execution_status"] = ("PARTIAL" if out["unresolved"] else part.get("execution_status"))
    return out


#: Entries of a per-reference list kept in the cycle response and its audit row; the counts are always exact.
CYCLE_LIST_CAP = 200


def _empty_verification_summary():
    from app.Tefca.entity_resolution import resolver_configuration_warnings, resolver_source

    return {"requested": 0, "verified": 0, "unresolved": [], "bucket_counts": {},
            "resolver_source": resolver_source(),
            "configuration_warnings": resolver_configuration_warnings(),
            "execution_status": "NO_RECORDS_REQUESTED"}


@asynccontextmanager
async def _cycle_session(db):
    """Keep one physical connection for the session-scoped delivery lock.

    A normal engine-bound AsyncSession returns its connection to the pool on
    commit. Unlocking through that session can therefore use a DIFFERENT
    backend, leaving the original lock held in the pool. This operation already
    owns its commits; finish the caller's current unit of work before borrowing
    one connection for the entire cycle. Externally bound sessions (including
    rolled-back test transactions) already retain their connection.
    """
    bind = getattr(db, "bind", None)
    if not isinstance(bind, AsyncEngine):
        yield db
        return
    await db.commit()
    async with bind.connect() as connection:
        async with AsyncSession(bind=connection, expire_on_commit=False) as pinned:
            yield pinned


async def create_review_cycle(
    db, intake_id, *, user,
    review_type: str = "quarterly",
    confidence: float = 0.95, margin: float = 0.05, proportion: float = 0.5,
    use_fpc: bool = True, include_held: bool = False,
    seed: Optional[int] = None, sample_name: Optional[str] = None,
    batch: int = DEFAULT_BATCH,
    ip_address: Optional[str] = None,
    full_population: bool = False,
    after_member_id=None,
) -> Dict[str, Any]:
    # Capture the authenticated actor before commit could expire an ORM user.
    actor_id, actor_email = reg_audit.actor_of(user)
    actor = SimpleNamespace(id=actor_id, email=actor_email)
    async with _cycle_session(db) as pinned:
        return await _create_review_cycle(
            pinned, intake_id, user=actor, review_type=review_type,
            confidence=confidence, margin=margin, proportion=proportion,
            use_fpc=use_fpc, include_held=include_held, seed=seed,
            sample_name=sample_name, batch=batch, ip_address=ip_address,
            full_population=full_population, after_member_id=after_member_id)


async def _create_review_cycle(
    db, intake_id, *, user,
    review_type: str = "quarterly",
    confidence: float = 0.95, margin: float = 0.05, proportion: float = 0.5,
    use_fpc: bool = True, include_held: bool = False,
    seed: Optional[int] = None, sample_name: Optional[str] = None,
    batch: int = DEFAULT_BATCH,
    ip_address: Optional[str] = None,
    full_population: bool = False,
    after_member_id=None,
) -> Dict[str, Any]:
    """Gate → draw (or reuse) the plan → verify unlinked members → link.

    Returns the plan, how many members are linked, how many remain, and the
    verification summary for this call. Call again until `remaining` is 0 or
    `no_further_reviews_possible` is true (see the return value for exactly
    when that is set; a walk continued from a cursor never sets it).

    MEMBERS THAT CANNOT BE REVIEWED FOR IDENTITY REASONS (2026-11-25). A member
    is linked only to a review of ITS OWN entity. A member whose delivered
    reference resolves to another entity, whose anchored record the NPI gates
    refuse, or for which verification produced no review, is NOT linked and is
    NOT given a status: it is reported (`not_reviewable_for_identity`, with
    the reason) and the call walks on to the members behind it, so it cannot
    hold them up. Where the refusal can be known from local data it is decided
    BEFORE the sources are asked, so repeating the call does not ask them
    about that member again. `remaining` keeps its meaning (members with no
    linked review) and `complete` stays false while any member is unreviewed -
    a sample is never reported complete because members were walked past.
    """
    from app.tefca_registry.qhin_sampling import SamplingRefused, finalize_plan
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.tefca_registry.rce.reconciliation import reconcile_delivery

    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise CycleRefused(f"No delivery {intake_id}")

    # Release-prerequisite barrier check (2026-11-08) at the CYCLE's own top,
    # before the pre-verification member-status writes this function commits
    # (plan finalization, HELD skips). `verify_and_classify` re-checks per
    # batch transaction — this early check only makes the refusal land before
    # any cycle bookkeeping is written. The commits below end this
    # transaction (releasing the shared in-flight lock), which is fine: the
    # drain guarantee protects classification writes, and those happen inside
    # verify_and_classify's own guarded transaction.
    from app.tefca_registry.processing_controls import enter_processing
    await enter_processing(db)

    actor_id, actor_email = reg_audit.actor_of(user)
    batch = max(1, min(int(batch or DEFAULT_BATCH), MAX_BATCH))

    # ── the gate ─────────────────────────────────────────────────────────────
    recon = await reconcile_delivery(db, intake_id)
    if not recon.get("passed"):
        failed = [c["check"] for c in recon.get("checks", []) if not c["passed"]]
        reg_audit.record(
            db, "review_cycle_refused", None, actor_id=actor_id,
            actor_email=actor_email, ip_address=ip_address,
            metadata={"source_intake_id": str(intake_id),
                      "reason": "reconciliation_not_passed",
                      "failed_checks": failed[:10]})
        await db.commit()
        raise CycleRefused(
            f"Delivery {intake_id} has not passed reconciliation; a review "
            f"cycle cannot be drawn from a population that does not close. "
            f"Failed: {', '.join(failed[:5]) or 'see reconciliation'}.")

    # ── one creator per delivery at a time ───────────────────────────────────
    lock_key = f"review_cycle:{intake_id}"
    await db.execute(text("select pg_advisory_lock(hashtext(:k))"), {"k": lock_key})
    try:
        try:
            plan = await finalize_plan(
                db, intake_id, review_type=review_type, confidence=confidence,
                margin=margin, proportion=proportion, use_fpc=use_fpc,
                include_held=include_held, seed=seed, actor=actor_email,
                actor_id=actor_id, sample_name=sample_name,
                full_population=full_population)
        except SamplingRefused as exc:
            await db.rollback()
            raise CycleRefused(str(exc))
        sample_id = uuid.UUID(plan["sample_id"])
        # finalize_plan flushes but does not commit; the plan is made durable
        # here, BEFORE verification, so a verification failure part-way cannot
        # roll the official sample back and let the next attempt redraw it.
        await db.commit()

        # THE MISSING LINK (2026-09-17): `tefca_registry.models.ReviewCycle`
        # existed as a table but nothing ever wrote a row to it, so no code
        # path could name a delivery's review cycle by id at all — a report
        # asked to scope to "this delivery's cycle" had no id to be given,
        # and the only working default was the unscoped, registry-wide one.
        # One row per drawn sample, deterministically (sample_id is already
        # idempotent per `finalize_plan`'s plan_key, so re-entering this
        # function for the same delivery/parameters never creates a second
        # cycle for the same sample).
        cycle = (await db.execute(select(reg.ReviewCycle)
                                  .where(reg.ReviewCycle.sample_id == sample_id))).scalar_one_or_none()
        if cycle is None:
            cycle = reg.ReviewCycle(
                id=uuid.uuid4(), cycle_type=review_type, sample_id=sample_id,
                cycle_start=None, cycle_end=None, status="open")
            db.add(cycle)
            await db.commit()
            reg_audit.record(
                db, "review_cycle_row_created", None, actor_id=actor_id,
                actor_email=actor_email, ip_address=ip_address,
                metadata={"source_intake_id": str(intake_id), "sample_id": str(sample_id),
                          "review_cycle_id": str(cycle.id)})
            await db.commit()
        review_cycle_id = cycle.id

        unlinked = await _unlinked_members(db, sample_id)
        total_unlinked = len(unlinked)

        # D-15 PROPOSED, INACTIVE by default: a record whose curated row is
        # currently HELD — including promoted-then-re-HELD after the plan was
        # drawn — is a processing state, not a classifiable member. When the
        # flag is on, held members are resolved over the WHOLE unlinked set
        # BEFORE the batch slice, marked `skipped_held` so they are never
        # re-selected, and removed from `remaining` so repeated calls still
        # converge (review finding 2026-11-08: the earlier in-batch filter
        # left them unlinked and selectable forever, so `remaining` could
        # never reach 0 and a batch of held-first members stalled the cycle).
        # The ids are recorded in the summary AND the audit trail. Default-off
        # keeps today's behavior byte-identical. May only be enabled under
        # the recorded D-15 decision; no hold is released.
        skipped_held: List[str] = []
        if _proposed_verify_skip_held() and unlinked:
            held_ids = await _held_entity_ids(
                db, intake_id, [member.entity_id for member in unlinked])
            if held_ids:
                skipped_held = sorted(str(e) for e in held_ids)
                for member in unlinked:
                    if member.entity_id in held_ids:
                        member.review_status = SKIPPED_HELD_STATUS
                await db.commit()
                unlinked = [member for member in unlinked
                            if member.entity_id not in held_ids]
                total_unlinked = len(unlinked)
                logger.info(
                    "review cycle %s: %d member(s) skipped — curated record "
                    "currently HELD (VERIFY_SKIPPED_HELD)",
                    intake_id, len(skipped_held))
                reg_audit.record(
                    db, "review_cycle_members_skipped_held", None,
                    actor_id=actor_id, actor_email=actor_email,
                    ip_address=ip_address,
                    metadata={"source_intake_id": str(intake_id),
                              "sample_id": str(sample_id),
                              "reason": "VERIFY_SKIPPED_HELD",
                              "skipped_held": skipped_held})
                await db.commit()

        verified: Dict[str, Any] = _empty_verification_summary()
        linked = 0
        intake_uuid = intake_id if isinstance(intake_id, uuid.UUID) else uuid.UUID(str(intake_id))
        # Plain values, read once: the commits inside the loop must never be
        # able to expire what the walk is iterating over.
        members = [(member, member.id, member.entity_id) for member in unlinked]
        position = 0
        started_at_front = True
        if after_member_id is not None:
            wanted = str(after_member_id)
            for index, (_member, member_id, _entity_id) in enumerate(members):
                if str(member_id) == wanted:
                    position = index + 1          # continue behind it; an unknown id starts at the front
                    started_at_front = False
                    break
        not_reviewable: List[Dict[str, Any]] = []
        cycle_assessment_date = None       # this delivery's own assessment date, read once when first needed
        limit_reached = False
        last_examined = None
        refs: Dict[Any, str] = {}
        refs_loaded_to = position

        def _set_aside(entity_id, ref, reason, kind):
            not_reviewable.append({"entity_id": str(entity_id), "entity_ref": ref, "reason": reason,
                                   "kind": kind})

        while linked < batch and position < len(members) and not limit_reached:
            # ── choose the next members to verify, walking past the ones that
            #    are known - without asking any source - not to be reviewable ──
            step: List[Any] = []
            while position < len(members) and len(step) < batch - linked:
                if len(not_reviewable) >= MAX_NOT_REVIEWABLE_PER_CALL:
                    limit_reached = True
                    break
                if position >= refs_loaded_to:
                    chunk = members[position:position + _REF_CHUNK]
                    refs.update(await _refs_for(db, intake_id, [e for _m, _i, e in chunk]))
                    refs_loaded_to = position + len(chunk)
                member, member_id, entity_id = members[position]
                position += 1
                last_examined = member_id
                ref = refs.get(entity_id)
                if not ref:
                    _set_aside(entity_id, None, NOT_REVIEWABLE_NO_REFERENCE, KIND_IDENTITY)
                    continue
                if cycle_assessment_date is None:
                    from app.tefca_registry.rce.assessment_context import resolve_assessment_date
                    cycle_assessment_date = (await resolve_assessment_date(db, intake_uuid)).value
                reason = await _identity_precheck(db, intake_uuid, entity_id, ref,
                                                  assessment_date=cycle_assessment_date)
                if reason:
                    _set_aside(entity_id, ref, reason, KIND_IDENTITY)
                    continue
                step.append((member, entity_id, ref))
            if not step:
                break

            result = await verify_and_classify(
                db, [ref for _member, _entity_id, ref in step], intake_id=intake_id,
                actor=actor_email or "SYSTEM")
            # `lineage` and the references whose evidence was not stored (unanchored) used to be dropped here,
            # so on the official path - this one - neither was visible to the caller or in the audit trail.
            verified = _merge_verification(verified, {k: result.get(k) for k in
                        ("requested", "verified", "unresolved", "bucket_counts",
                         "resolver_source", "configuration_warnings", "execution_status",
                         "lineage", "evidence_not_stored_unanchored", "refused_before_gather",
                         "classified_on_submitted_values", "evidence_snapshot_only_submitted_values")})
            outcome_by_ref = {o.get("entity_ref"): o for o in result.get("outcomes", [])}
            failure_by_ref = {f.get("entity_ref"): f.get("reason")
                              for f in result.get("resolution_failures") or []}
            unresolved_refs = set(result.get("unresolved") or [])

            # verify_and_classify has committed its ReviewRecords. Link them -
            # each only to the member whose own entity it reviewed.
            for member, entity_id, ref in step:
                outcome = outcome_by_ref.get(ref)
                review_id = outcome.get("review_id") if outcome else None
                if not review_id:
                    if failure_by_ref.get(ref):
                        # The writer's own identity gate refused the record (its reason, verbatim).
                        _set_aside(entity_id, ref, failure_by_ref[ref], KIND_IDENTITY)
                    else:
                        # Not resolved, or no review came back: not established as an identity fact.
                        _set_aside(entity_id, ref, (NOT_REVIEWABLE_UNRESOLVED if ref in unresolved_refs
                                                    else NOT_REVIEWABLE_NO_REVIEW), KIND_TRANSIENT)
                    continue
                reviewed = outcome.get("entity_id")
                if reviewed is not None and str(reviewed) != str(entity_id):
                    # The review exists and is about ANOTHER entity (the
                    # resolution changed between the check above and the
                    # writer's own, or the writer resolved differently). It is
                    # never recorded as this member's review.
                    _set_aside(entity_id, ref, NOT_REVIEWABLE_CHANGED, KIND_TRANSIENT)
                    not_reviewable[-1]["review_not_linked"] = review_id
                    continue
                record = (await db.execute(
                    select(reg.ReviewRecord)
                    .where(reg.ReviewRecord.review_id == review_id))).scalars().first()
                if record is None:
                    _set_aside(entity_id, ref, NOT_REVIEWABLE_NO_REVIEW, KIND_TRANSIENT)
                    continue
                record.sample_id = sample_id
                # Replaced, not mutated: SQLAlchemy does not see an in-place
                # change to a JSONB dict.
                record.verification_results = {
                    **(record.verification_results or {}),
                    "source_intake_id": str(intake_id),
                    "queue_source": QUEUE_SOURCE,
                    "sample_id": str(sample_id),
                }
                member.review_id = review_id
                member.review_status = "in_review"
                linked += 1
            await db.commit()

        if skipped_held:
            verified["skipped_held"] = skipped_held
            verified["skipped_held_reason"] = "VERIFY_SKIPPED_HELD"
        not_stored = list(verified.get("evidence_not_stored_unanchored") or [])
        verified["evidence_not_stored_unanchored_count"] = len(not_stored)
        verified["evidence_not_stored_unanchored"] = not_stored[:CYCLE_LIST_CAP]
        verified["evidence_not_stored_unanchored_truncated"] = len(not_stored) > CYCLE_LIST_CAP
        verified.setdefault("lineage", {})
        on_submitted = list(verified.get("classified_on_submitted_values") or [])
        verified["classified_on_submitted_values_count"] = len(on_submitted)
        verified["classified_on_submitted_values"] = on_submitted[:CYCLE_LIST_CAP]
        verified["evidence_snapshot_only_submitted_values"] = list(
            verified.get("evidence_snapshot_only_submitted_values") or [])[:CYCLE_LIST_CAP]
        walk_exhausted = position >= len(members)
        if verified.get("requested") or not_reviewable:
            reg_audit.record(
                db, "review_cycle_members_verified", None, actor_id=actor_id,
                actor_email=actor_email, ip_address=ip_address,
                metadata={"source_intake_id": str(intake_id),
                          "sample_id": str(sample_id),
                          "requested": verified.get("requested"),
                          "verified": verified.get("verified"),
                          "linked": linked,
                          "unresolved": len(verified.get("unresolved") or []),
                          "skipped_held": skipped_held,
                          # Counted here, listed in the response: members walked
                          # past for identity reasons. None was linked or marked.
                          "not_reviewable_for_identity": len(not_reviewable),
                          "not_reviewable_reasons": sorted({n["reason"] for n in not_reviewable}),
                          "not_reviewable_members": [
                              {"entity_id": n["entity_id"], "reason": n["reason"], "kind": n["kind"]}
                              for n in not_reviewable[:CYCLE_LIST_CAP]],
                          "lineage": verified.get("lineage"),
                          "evidence_not_stored_unanchored": verified["evidence_not_stored_unanchored_count"],
                          "evidence_not_stored_unanchored_refs": verified["evidence_not_stored_unanchored"],
                          # Records that submitted no NPI and were classified on their submitted values (the
                          # registry NPI withheld; evidence in the review snapshot only).
                          "classified_on_submitted_values": verified["classified_on_submitted_values_count"],
                          "classified_on_submitted_values_refs": verified["classified_on_submitted_values"]})
            await db.commit()
        if not_reviewable:
            logger.warning(
                "review cycle %s: %d sample member(s) were not reviewable for identity reasons and "
                "were walked past; none was linked or given a status", intake_id, len(not_reviewable))

        if not plan.get("already_finalized"):
            reg_audit.record(
                db, "review_cycle_created", None, actor_id=actor_id,
                actor_email=actor_email, ip_address=ip_address,
                metadata={"source_intake_id": str(intake_id),
                          "sample_id": str(sample_id),
                          "review_type": review_type,
                          "sample_size": plan.get("sample_size"),
                          "population_size": plan.get("population_size"),
                          "reconciliation_passed": True})
            await db.commit()
    finally:
        try:
            # A failed statement must be rolled back before unlocking; otherwise
            # the failed transaction rejects the unlock and the pool retains it.
            # ONLY when a failure is propagating: on a normal exit the work is
            # already committed, and an unconditional rollback here ends the
            # CALLER's outer transaction when the session is bound to a
            # connection that already holds one (SQLAlchemy "rollback_only"
            # join mode), discarding their uncommitted work while the call
            # reports success. After a failure that rollback is unavoidable: a
            # failed statement poisons the transaction it ran in.
            if sys.exc_info()[0] is not None:
                await db.rollback()
            await db.execute(text("select pg_advisory_unlock(hashtext(:k))"),
                             {"k": lock_key})
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("advisory unlock failed for %s: %s", lock_key,
                           type(exc).__name__)

    remaining = max(total_unlinked - linked, 0)
    identity_count = sum(1 for n in not_reviewable if n["kind"] == KIND_IDENTITY)
    return {
        "intake_id": str(intake_id),
        "review_cycle_id": str(review_cycle_id),
        "plan": plan,
        "reconciliation_passed": True,
        "members": plan.get("membership_count"),
        "linked_this_call": linked,
        # Members with no linked review. Unchanged meaning: it includes the
        # members this call could not review for identity reasons.
        "remaining": remaining,
        # False while ANY member is unreviewed - never true because members
        # were walked past.
        "complete": remaining == 0,
        # Members this call examined and could not review for identity
        # reasons, each with its reason. None was linked; none was given a
        # status; each is still counted in `remaining`.
        "not_reviewable_for_identity": not_reviewable,
        "not_reviewable_for_identity_count": len(not_reviewable),
        # Split by `kind` (see KIND_IDENTITY / KIND_TRANSIENT): only the first is a settled identity fact.
        "not_reviewable_identity_count": identity_count,
        "not_reviewable_transient_count": len(not_reviewable) - identity_count,
        # Whether this call's walk began at the first unlinked member (no cursor, or a cursor it did not find).
        "walk_started_at_front": started_at_front,
        # Members this call did not get to (its `batch` was used up, or the
        # limit below was reached). Their reviewability is not known yet, so
        # this is the most that can still be reviewable; 0 when the walk
        # reached the end of the sample.
        "members_not_examined": len(members) - position,
        "reviewable_remaining_at_most": len(members) - position,
        "walk_exhausted": walk_exhausted,
        "identity_check_limit_reached": limit_reached,
        "next_after_member_id": (str(last_examined)
                                 if limit_reached and last_examined is not None else None),
        # THE STOP SIGNAL. True only when ALL of this holds: the walk began at
        # the FIRST unlinked member and reached the end in this one call (so
        # no member was left unexamined in front of a cursor or behind the
        # per-call limit), nothing was linked, members remain, and every one
        # of them was set aside for a settled identity reason. A member set
        # aside for a reason that may be transient (the reference did not
        # resolve, no review came back, the resolution changed mid-call)
        # keeps it false: calling again may review that member. The sample is
        # NOT complete either way.
        "no_further_reviews_possible": bool(
            started_at_front and walk_exhausted and linked == 0 and remaining > 0
            and identity_count == len(not_reviewable) == remaining),
        "verification": verified,
        "batch": batch,
        "note": ("The sample is frozen; asking again never redraws it. "
                 "Verification runs only for members not yet linked to a "
                 "review case, so repeated calls converge and never duplicate. "
                 "Call again until remaining is 0, or until "
                 "no_further_reviews_possible is true (members remain that "
                 "cannot be reviewed for identity reasons; the sample is not "
                 "complete)."),
    }


async def read_review_cycle(db, intake_id) -> Dict[str, Any]:
    """The plans drawn for this delivery and how complete each one is."""
    from app.tefca_registry.qhin_sampling import get_plan, plan_completion

    samples = (await db.execute(
        select(reg.ReviewSample)
        .where(reg.ReviewSample.strata_config["source_intake_id"].astext
               == str(intake_id))
        .order_by(reg.ReviewSample.drawn_at.desc()))).scalars().all()

    plans: List[Dict[str, Any]] = []
    for sample in samples:
        plan = await get_plan(db, sample.id)
        unlinked = (await db.execute(
            select(reg.SampleEntity.id)
            .where(reg.SampleEntity.sample_id == sample.id,
                   reg.SampleEntity.review_id.is_(None)))).all()
        plan["unlinked_members"] = len(unlinked)
        cycle = (await db.execute(select(reg.ReviewCycle.id)
                                  .where(reg.ReviewCycle.sample_id == sample.id))).scalar_one_or_none()
        # None here means a sample was drawn by some OTHER, older path than
        # `create_review_cycle` (or before this fix existed) and never got its
        # row — a real, visible gap, not hidden behind a fabricated id.
        plan["review_cycle_id"] = str(cycle) if cycle is not None else None
        plan["completion"] = await plan_completion(db, sample.id)
        plans.append(plan)

    return {"intake_id": str(intake_id), "plans": plans, "count": len(plans)}


async def _refs_for(db, intake_id, entity_ids: List[Any]) -> Dict[Any, str]:
    """entity_id -> rce_org_oid, the reference `resolve_entity` matches on.

    Read from THIS delivery's curated records so the reference is the one the
    delivery actually carried for that organisation.
    """
    if not entity_ids:
        return {}
    rows = (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id,
               m.RceCuratedRecord.rce_org_oid)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.in_(entity_ids),
               m.RceCuratedRecord.rce_org_oid.isnot(None)))).all()
    out: Dict[Any, str] = {}
    for entity_id, ref in rows:
        out.setdefault(entity_id, ref)
    return out
