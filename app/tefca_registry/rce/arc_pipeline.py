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
            conflicts = [c for i in dimension.get("evidence", [])
                         for c in (i.get("field_conflicts") or [])]
            if any((c.get("field") or "") == "name" for c in conflicts):
                fields["name_mismatch"] = {"severity": "minor"}

    quality = evidence.get("data_quality_flags") or []
    if "NPI_MALFORMED" in quality or "NPI_CHECK_DIGIT_FAILED" in quality:
        fields["npi_validation"] = {"status": "flagged"}

    return {"sources": sources, "fields": fields, "dimensions": dimension_view}


def next_review_id(sequence: int, year: Optional[int] = None) -> str:
    return f"REV-{year or datetime.utcnow().year}-{sequence:06d}"


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

    existing = int((await db.execute(
        select(func.count()).select_from(reg.ReviewRecord))).scalar() or 0)

    outcomes: List[Dict[str, Any]] = []
    buckets: Dict[str, int] = {}
    tiers: Dict[int, int] = {}
    unresolved: List[str] = []

    for offset, ref in enumerate(entity_refs):
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

        review_id = next_review_id(existing + offset + 1)
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
