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

#: The worst disposition wins when a source appears in several of the dimensions
#: below, so a source that failed somewhere is not reported verified because it
#: also passed elsewhere.
_STATE_PRECEDENCE = ("failed", "not_found", "unavailable", "not_checked", "verified")

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
            state = _DISPOSITION_TO_STATE.get(item.get("disposition"), "not_checked")
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
    from app.tefca_registry.bucket_classifier import ensure_rules_v2, ensure_seed_rules

    count = int((await db.execute(
        select(func.count()).select_from(reg.ReviewRule))).scalar() or 0)
    if count == 0:
        await ensure_seed_rules(db)
        await ensure_rules_v2(db)
        await db.commit()
    rows = (await db.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.is_active.is_(True)))).scalars().all()
    return [{
        "rule_code": r.rule_code, "name": r.name, "bucket": r.bucket,
        "priority": r.priority, "conditions": r.conditions,
        "description": r.description, "version": r.version,
    } for r in rows]


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
    from app.Tefca.entity_resolution import resolve_entity
    from app.Tefca.evidence_service import EvidenceService, evidence_rows_for_persistence
    from app.Tefca.models import TEFCADimensionEvidence
    from app.Tefca.ppef_store import make_local_store
    from app.tefca_registry.bucket_classifier import BucketClassifier

    rules = await _rule_set(db)
    classifier = BucketClassifier()
    service = EvidenceService(local_store=make_local_store(db))

    outcomes: List[Dict[str, Any]] = []
    buckets: Dict[str, int] = {}
    tiers: Dict[int, int] = {}
    unresolved: List[str] = []

    # Held for the whole batch — see `_lock_review_id_allocation`. Acquired
    # even when entity_refs is empty or every ref is unresolved, which costs
    # nothing (no id is ever allocated) and keeps this call site simple.
    await _lock_review_id_allocation(db)

    for ref in entity_refs:
        entity = await resolve_entity(db, ref)
        if entity is None:
            unresolved.append(ref)
            continue

        evidence = await service.build_evidence(entity)
        entity_uuid = entity.get("_registry_entity_id")

        if persist_evidence:
            rows = evidence_rows_for_persistence(
                str(entity_uuid or entity.get("id")), None, evidence)
            for row in rows:
                row["review_cycle_id"] = None
                db.add(TEFCADimensionEvidence(**row))

        verification_results = dimensions_to_verification_results(evidence)
        classification = classifier.classify(verification_results, rules=rules)

        review_id = await _allocate_review_id(db)
        tier = BUCKET_TO_TIER.get(classification.bucket, 3)

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

        db.add(reg.ReviewRecord(
            id=uuid.uuid4(),
            review_id=review_id,
            entity_id=entity_uuid,
            verification_results={
                # A SNAPSHOT, not a pointer. The report issued from this review
                # must keep saying what it said after the entity is re-verified.
                "dimensions": evidence.get("dimensions", []),
                "applicability": evidence.get("applicability", {}),
                "sufficiency": evidence.get("sufficiency", {}),
                "data_quality_flags": evidence.get("data_quality_flags", []),
                "generation_timestamp": evidence.get("generated_at"),
                "resolution_source": entity.get("_resolution_source"),
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
                "verified" if classification.bucket == "B1" else "in_review")

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
            "dimensions": {d["dimension"]: d["disposition"]
                           for d in evidence.get("dimensions", [])},
            "applicability": evidence.get("applicability", {}).get("dimensions", {}),
        })

    await db.commit()

    return {
        "requested": len(entity_refs),
        "verified": len(outcomes),
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
