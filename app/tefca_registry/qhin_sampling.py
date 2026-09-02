"""Draw an official per-QHIN sample for one delivery.

WHAT THIS ORCHESTRATES — AND DELIBERATELY DOES NOT REIMPLEMENT
──────────────────────────────────────────────────────────────
    current delivery
        -> eligible population
        -> QHIN resolver (canonical managed_by_qhin edge)
        -> sampling_engine.draw_per_stratum   <- the statistics live THERE
        -> frozen ReviewSample + SampleEntity <- the tables already existed
        -> existing review_records workflow

No formula appears in this module. `calculate_sample_size` is called once per
QHIN by `draw_per_stratum`, and this module's job is to decide WHICH records are
eligible, WHICH QHIN each belongs to, and to freeze the result.

WHY NOT `draw_sample`
─────────────────────
`CochranSampler.draw_sample` with strata computes ONE sample size from the whole
population and allocates it proportionally. Measured on the delivered population
that gives the smallest QHIN (3 records) ZERO selected records while the total
still reads as a 95% sample. The contract requires a representative sample FROM
EACH QHIN at >=95%, so the official path must size each stratum against its own
population. `draw_sample` remains for the national/proportional question it was
written for; it must never serve this one, and a test pins that.

FREEZING
────────
A random selection recomputed on each request is not a sample — it is a new
sample every time nobody looks. So membership is written once to
`SampleEntity` and read back thereafter. `uq_sample_entity (sample_id,
entity_id)` is the database's guarantee that one record cannot be enrolled
twice, and a transaction-scoped advisory lock on the plan key stops two
concurrent finalisers both deciding no plan exists yet.

WHAT REMAINS GOVERNANCE, NOT ENGINEERING
────────────────────────────────────────
Margin of error, stratification confirmation, Task 3/Task 4 cadence, HELD
eligibility and repeat-selection policy are unresolved with the COR. They are
carried as EXPLICIT parameters recorded on the plan, never as silent defaults —
so when a decision arrives, the plans drawn before it can still say what they
assumed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, text

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.sampling_engine import CochranSampler

#: Bump when eligibility or resolution changes, so a plan can be traced to the
#: rules that produced it rather than to whatever this file says today.
POPULATION_VERSION = "1.0.0"
SELECTION_ALGORITHM = "cochran_fpc_per_qhin/1.0.0"

#: Stamped on every plan this module creates, so a per-QHIN ARC sample is
#: distinguishable from a legacy proportional draw through the same tables.
PLAN_SOURCE = "TEFCA_ARC_PER_QHIN"

#: A record whose QHIN cannot be determined from exactly one canonical edge.
#: Never silently folded into another stratum.
UNRESOLVED_QHIN = "UNRESOLVED"


class SamplingRefused(RuntimeError):
    """A sampling act was refused, and the reason is stated."""


def plan_key(intake_id: Any, review_type: str, *, confidence: float,
             margin: float, proportion: float, use_fpc: bool,
             include_held: bool) -> str:
    """Identity of a logical sampling plan.

    Every parameter that changes WHAT THE SAMPLE MEANS is in the key. Two plans
    that differ in margin or in HELD treatment are different plans and may
    legitimately coexist; asking twice for the SAME plan must find the one
    already drawn rather than draw a second.
    """
    return (f"{PLAN_SOURCE}:{intake_id}:{review_type}:{confidence}:{margin}:"
            f"{proportion}:{int(use_fpc)}:held={int(include_held)}")


async def resolve_qhin_strata(db, intake_id, *, include_held: bool = False
                              ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(eligible units, unresolved units) for one delivery.

    QHIN comes from the CANONICAL relationship — the `managed_by_qhin` edge
    written at promotion — not from a convenient column and never from state,
    entity level, name, NPI or TEFCAID. A record with no edge, or with more than
    one, is returned as UNRESOLVED rather than being placed somewhere plausible.

    HELD is excluded by default and its treatment is an explicit parameter,
    because whether a held record belongs in the frame is an open ONC question
    and a silent default would answer it.

    A curated record that was never promoted has no canonical entity, and
    `sample_entities.entity_id` is a NOT NULL foreign key — so it CANNOT be
    enrolled in a sample whatever `include_held` says. That is a real limit of
    the sampling unit, not a judgement about the record, so such records are
    RETURNED AS UNRESOLVED rather than filtered away in the query. A record
    dropped before anyone counts it is the one nobody asks about.
    """
    rows = (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id,
               m.RceCuratedRecord.rce_org_oid,
               m.RceCuratedRecord.record_status)
        .where(m.RceCuratedRecord.source_intake_id == intake_id))).all()

    if not rows:
        return [], []

    # One query for every QHIN edge, then counted per child: two edges is an
    # ambiguity to report, not a coin to toss.
    #
    # A SUBQUERY, NOT `.in_(entity_ids)`. The delivered population is 23,562
    # promoted entities and a 100K delivery is expected; asyncpg refuses a
    # statement with more than 32,767 bind parameters, so an expanded IN list
    # would work on today's file and raise on the next size up. The database
    # already holds the id set — it is asked for the join rather than handed
    # the list back.
    promoted = (select(m.RceCuratedRecord.canonical_entity_id)
                .where(m.RceCuratedRecord.source_intake_id == intake_id,
                       m.RceCuratedRecord.canonical_entity_id.isnot(None)))
    edges = (await db.execute(
        select(reg.TefcaEntityRelationship.child_entity_id,
               reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id.in_(promoted),
               reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
               reg.TefcaEntityRelationship.status == "active"))).all()
    by_child: Dict[Any, List[Any]] = {}
    for child, parent in edges:
        by_child.setdefault(child, []).append(parent)

    eligible: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    for entity_id, org_oid, record_status in rows:
        parents = by_child.get(entity_id, [])
        unit = {"entity_id": entity_id, "rce_org_oid": org_oid,
                "record_status": record_status}
        if entity_id is None:
            unresolved.append({**unit, "qhin": UNRESOLVED_QHIN,
                               "reason": ("not promoted; no canonical entity, "
                                          "so it cannot be a sampling unit")})
            continue
        if len(parents) != 1:
            unresolved.append({**unit, "qhin": UNRESOLVED_QHIN,
                               "reason": ("no canonical managed_by_qhin edge"
                                          if not parents else
                                          f"{len(parents)} managing QHINs")})
            continue
        if record_status == "HELD" and not include_held:
            unresolved.append({**unit, "qhin": str(parents[0]),
                               "reason": "HELD; eligibility is an open ONC "
                                         "question and is excluded by default"})
            continue
        eligible.append({**unit, "qhin": str(parents[0])})
    return eligible, unresolved


async def preview_plan(db, intake_id, *, review_type: str = "quarterly",
                       confidence: float = 0.95, margin: float = 0.05,
                       proportion: float = 0.5, use_fpc: bool = True,
                       include_held: bool = False) -> Dict[str, Any]:
    """Per-QHIN N and n WITHOUT drawing or persisting anything.

    Sizing only — no selection, so a preview cannot leak which records would be
    drawn and cannot be used to shop for a favourable sample.
    """
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise SamplingRefused(f"No delivery {intake_id}")

    eligible, unresolved = await resolve_qhin_strata(
        db, intake_id, include_held=include_held)
    sampler = CochranSampler()

    strata: Dict[str, Dict[str, Any]] = {}
    for unit in eligible:
        strata.setdefault(unit["qhin"], {"population_size": 0})
        strata[unit["qhin"]]["population_size"] += 1
    for key, info in strata.items():
        info["sample_size"] = sampler.calculate_sample_size(
            info["population_size"], confidence=confidence, margin=margin,
            proportion=proportion, use_fpc=use_fpc)
        info["census"] = info["sample_size"] == info["population_size"]

    return {
        "intake_id": str(intake_id),
        "delivery_received_at": intake.received_at,
        "schema_fingerprint": intake.schema_fingerprint,
        "population_version": POPULATION_VERSION,
        "review_type": review_type,
        "confidence_level": confidence, "margin_of_error": margin,
        "proportion": proportion, "use_fpc": use_fpc,
        "include_held": include_held,
        "qhin_strata": len(strata),
        "eligible_population": len(eligible),
        "unresolved_units": len(unresolved),
        "total_sample_size": sum(s["sample_size"] for s in strata.values()),
        "per_qhin": dict(sorted(strata.items())),
        "note": ("Margin of error and stratification remain subject to COR "
                 "confirmation. Recorded explicitly on any plan drawn."),
    }


async def _existing_plan(db, key: str) -> Optional[reg.ReviewSample]:
    return (await db.execute(
        select(reg.ReviewSample)
        .where(reg.ReviewSample.strata_config["plan_key"].astext == key)
        .limit(1))).scalars().first()


async def finalize_plan(db, intake_id, *, review_type: str = "quarterly",
                        confidence: float = 0.95, margin: float = 0.05,
                        proportion: float = 0.5, use_fpc: bool = True,
                        include_held: bool = False,
                        seed: Optional[int] = None,
                        actor: Optional[str] = None,
                        actor_id: Optional[uuid.UUID] = None,
                        sample_name: Optional[str] = None) -> Dict[str, Any]:
    """Draw and FREEZE one official per-QHIN sample. Idempotent.

    Asking twice for the same plan returns the plan already drawn — it never
    redraws. That is the whole point of an official sample: the selection has to
    be the same tomorrow as it was when it was made.
    """
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise SamplingRefused(f"No delivery {intake_id}")

    key = plan_key(intake_id, review_type, confidence=confidence,
                   margin=margin, proportion=proportion, use_fpc=use_fpc,
                   include_held=include_held)

    # One finaliser at a time per plan. Transaction-scoped, so a crash cannot
    # strand it. Without this two concurrent callers could both find no plan and
    # both draw — with different seeds, giving two official samples.
    await db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"),
                     {"k": key})

    existing = await _existing_plan(db, key)
    if existing is not None:
        return {**await get_plan(db, existing.id), "already_finalized": True}

    eligible, unresolved = await resolve_qhin_strata(
        db, intake_id, include_held=include_held)
    if not eligible:
        raise SamplingRefused(
            f"Delivery {intake_id} has no eligible population to sample under "
            f"the stated parameters; refusing to create an empty plan.")

    # The statistics are the engine's. This module only says what to sample.
    result = CochranSampler().draw_per_stratum(
        eligible, stratum_of=lambda unit: unit["qhin"], seed=seed,
        confidence=confidence, margin=margin, proportion=proportion,
        use_fpc=use_fpc,
        strata_config={"stratify_by": "managed_by_qhin",
                       "plan_key": key,
                       "plan_source": PLAN_SOURCE,
                       "population_version": POPULATION_VERSION,
                       "selection_algorithm": SELECTION_ALGORITHM,
                       "source_intake_id": str(intake_id),
                       "include_held": include_held,
                       "unresolved_units": len(unresolved)})

    sample = reg.ReviewSample(
        id=uuid.uuid4(),
        sample_name=sample_name or f"{PLAN_SOURCE} {review_type} "
                                   f"{date.today().isoformat()}",
        review_type=review_type,
        population_size=result.population_size,
        sample_size=result.sample_size,
        confidence_level=result.confidence_level,
        margin_of_error=result.margin_of_error,
        proportion=result.proportion,
        use_fpc=result.use_fpc,
        random_seed=result.random_seed,
        strata_config=result.strata_config,
        # Per-QHIN N and n, so a reviewer can check each stratum's own
        # calculation rather than only the total.
        strata_distribution={"selected": result.strata_distribution,
                             "sizing": result.stratum_sizing},
        status="drawn",
        created_by=actor_id)
    db.add(sample)
    await db.flush()

    for unit in result.selected:
        db.add(reg.SampleEntity(
            id=uuid.uuid4(), sample_id=sample.id, entity_id=unit["entity_id"],
            review_status="pending", stratum=unit["qhin"]))
    await db.flush()

    reg_audit.record(
        db, "sampling_plan_finalized", None,
        actor_id=actor_id, actor_email=actor,
        metadata={"sample_id": str(sample.id), "plan_key": key,
                  "source_intake_id": str(intake_id),
                  "review_type": review_type,
                  "confidence_level": confidence, "margin_of_error": margin,
                  "population_size": result.population_size,
                  "sample_size": result.sample_size,
                  "qhin_strata": len(result.strata_distribution),
                  "selection_algorithm": SELECTION_ALGORITHM,
                  "random_seed": result.random_seed,
                  "unresolved_units": len(unresolved)})

    return {**await get_plan(db, sample.id), "already_finalized": False,
            "unresolved_units": len(unresolved)}


async def get_plan(db, sample_id) -> Dict[str, Any]:
    """A finalised plan, read from storage. NEVER redraws."""
    sample = await db.get(reg.ReviewSample, sample_id)
    if sample is None:
        raise SamplingRefused(f"No sampling plan {sample_id}")

    members = (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == sample.id))).scalars().all()
    per_stratum: Dict[str, int] = {}
    for member in members:
        per_stratum[member.stratum] = per_stratum.get(member.stratum, 0) + 1

    config = sample.strata_config or {}
    distribution = sample.strata_distribution or {}
    return {
        "sample_id": str(sample.id),
        "sample_name": sample.sample_name,
        "review_type": sample.review_type,
        "plan_key": config.get("plan_key"),
        "plan_source": config.get("plan_source"),
        "source_intake_id": config.get("source_intake_id"),
        "population_version": config.get("population_version"),
        "selection_algorithm": config.get("selection_algorithm"),
        "stratify_by": config.get("stratify_by"),
        "include_held": config.get("include_held"),
        "population_size": sample.population_size,
        "sample_size": sample.sample_size,
        "confidence_level": sample.confidence_level,
        "margin_of_error": sample.margin_of_error,
        "proportion": sample.proportion,
        "use_fpc": sample.use_fpc,
        "random_seed": sample.random_seed,
        "per_qhin_sizing": distribution.get("sizing", {}),
        "per_qhin_selected": per_stratum,
        "status": sample.status,
        "drawn_at": sample.drawn_at,
        "created_by": str(sample.created_by) if sample.created_by else None,
        "membership_count": len(members),
    }


async def plan_completion(db, sample_id) -> Dict[str, Any]:
    """How much of the sample has actually been REVIEWED.

    Selection is not completion. A plan is complete when its members have been
    determined and independently QA-approved — read from the review events that
    already own those facts, not from a status this module keeps in parallel.
    """
    from app.tefca_registry.qa_gate import _events, is_reportable

    sample = await db.get(reg.ReviewSample, sample_id)
    if sample is None:
        raise SamplingRefused(f"No sampling plan {sample_id}")

    members = (await db.execute(
        select(reg.SampleEntity)
        .where(reg.SampleEntity.sample_id == sample.id))).scalars().all()

    counts = {"selected": len(members), "no_review_case": 0,
              "review_pending": 0, "submitted_for_qa": 0, "qa_returned": 0,
              "qa_escalated": 0, "qa_approved": 0}
    for member in members:
        if not member.review_id:
            counts["no_review_case"] += 1
            continue
        events = await _events(db, member.review_id)
        if not events:
            counts["review_pending"] += 1
            continue
        if is_reportable(events):
            counts["qa_approved"] += 1
            continue
        qa = [e for e in events if e.event_type == "QA_REVIEW"]
        if qa and qa[-1].qa_action == "RETURN":
            counts["qa_returned"] += 1
        elif qa and qa[-1].qa_action == "ESCALATE":
            counts["qa_escalated"] += 1
        else:
            counts["submitted_for_qa"] += 1

    return {
        "sample_id": str(sample.id),
        "counts": counts,
        "complete": counts["qa_approved"] == counts["selected"] > 0,
        "note": ("A plan is not complete because records were selected. "
                 "Completion is QA-approved review of its members. RETURN and "
                 "ESCALATE leave a member in the sample; a member is never "
                 "swapped for an easier record."),
    }
