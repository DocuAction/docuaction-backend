"""One review: verify against sources, classify, persist, return the envelope.

The single place where a verification becomes a reviewable record. Kept separate
from the route so the same path serves the ordinary verify endpoint, the
priority review, and any future scheduled run — three call sites producing
review records by three slightly different routes is how audit trails develop
holes.

The five verification states are preserved end to end. Nothing here collapses
`unavailable` into `not_found`: one is a third party's outage and must not count
against the entity, the other is a statement about the entity and must.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import (
    BucketClassifier, FAILED, NOT_CHECKED, NOT_FOUND, UNAVAILABLE, VERIFIED,
    ensure_seed_rules, ensure_rules_v2)

logger = logging.getLogger(__name__)

_classifier = BucketClassifier()

# Sources the model expects. Those without a connector are reported as
# not_checked with a reason rather than omitted — a source missing from the
# response reads as an oversight, while "not_checked: no connector" is a
# disclosed gap.
# Reported as not_checked WITH A REASON — never "unavailable". The distinction
# is load-bearing: "unavailable" implies a source that normally answers is
# temporarily down and will recover, which invites someone to retry and wait.
# "not_checked — connector not implemented" says the work has not been built,
# which is a roadmap item and needs a decision, not a retry.
NO_CONNECTOR = {
    # Every reason here must signal "this needs a decision", never "retry later".
    # "under investigation" carries that as plainly as "not operational" did, and
    # test_unimplemented_are_not_checked_never_unavailable accepts it for exactly
    # that reason. Do not reword this into something that reads like a transient
    # outage.
    "sam_gov": "API key configured. Entity lookup endpoints returning 404 — "
               "API version under investigation.",
    "state_registry": "Connector not implemented",
    # NOT "not implemented" — that implies a roadmap item. There is no public
    # IRS API for verifying a for-profit entity at all; TEOS covers only
    # tax-exempt organisations. This will never be built, and saying so is more
    # useful than leaving a reader waiting for it.
    "irs": "Not applicable — no public IRS API exists for for-profit entity "
           "verification. IRS TEOS covers only tax-exempt organizations "
           "(501(c)(3)), and IRS data is keyed on EIN, which the registry does "
           "not hold.",
}

SOURCE_LABELS = {
    "nppes": "NPI Registry — CMS/HHS",
    "pecos": "Provider Enrollment — CMS",
    "oig_leie": "Exclusion List — OIG/HHS",
    "sam_gov": "Federal Registration — GSA",
    "state_registry": "State licensure registry",
    "irs": "IRS Exempt Organizations",
}


async def probe_sources(db, entity_id) -> Dict[str, dict]:
    """Query each connector for this entity's NPI, in five-state form.

    Never raises. A verification that returns partial results is far more useful
    than one that 500s because a third-party API had a bad minute.
    """
    from sqlalchemy import select

    npi = (await db.execute(
        select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.entity_id == entity_id,
            reg.TefcaEntityIdentifier.identifier_type == "npi").limit(1))
    ).scalar_one_or_none()

    out: Dict[str, dict] = {
        k: {"status": NOT_CHECKED, "reason": why, "label": SOURCE_LABELS.get(k)}
        for k, why in NO_CONNECTOR.items()
    }

    if not npi:
        for key in ("nppes", "pecos", "oig_leie"):
            out[key] = {"status": NOT_CHECKED,
                        "reason": "entity has no NPI identifier to look up",
                        "label": SOURCE_LABELS.get(key)}
        return out

    try:
        from app.Tefca.connectors import SourceConnectorManager
        mgr = SourceConnectorManager()
    except Exception as exc:  # pragma: no cover
        logger.warning("TEFCA connectors unavailable: %s", exc)
        for key in ("nppes", "pecos", "oig_leie"):
            out[key] = {"status": UNAVAILABLE, "reason": f"connector import failed: {exc}",
                        "label": SOURCE_LABELS.get(key)}
        return out

    for key, attr in (("nppes", "nppes"), ("pecos", "pecos"), ("oig_leie", "leie")):
        conn = getattr(mgr, attr, None)
        fn = getattr(conn, "lookup_by_npi", None) if conn else None
        if fn is None:
            out[key] = {"status": NOT_CHECKED, "reason": "connector not available",
                        "label": SOURCE_LABELS.get(key)}
            continue
        try:
            r = await fn(npi)
            err = getattr(r, "error", None)
            ok = bool(getattr(r, "success", False))
            data = getattr(r, "data", None) or {}

            # CRITICAL: SourceResult.success means THE QUERY SUCCEEDED, not that
            # the entity was found or excluded. The finding lives in .data. An
            # earlier version read success as the answer, which reported every
            # entity whose LEIE lookup merely completed as EXCLUDED — the single
            # most damaging misclassification available here, since B4 is
            # disqualifying. The answer is always taken from the payload now.
            if err or not ok:
                # Reached-and-errored is UNAVAILABLE, not a finding. Scoring an
                # outage against the entity would be an accusation, not a result.
                out[key] = {"status": UNAVAILABLE,
                            "reason": str(err or "source did not complete")[:200],
                            "label": SOURCE_LABELS.get(key)}
            elif key == "oig_leie":
                # Exclusion list: a hit is bad news, absence is the good outcome.
                # `excluded` counts only ACTIVE exclusions — a reinstated
                # provider is not currently excluded.
                out[key] = {"status": "excluded" if data.get("excluded") else "clear",
                            "label": SOURCE_LABELS.get(key),
                            "exclusion_count": data.get("exclusion_count", 0)}
            else:
                # NPPES/PECOS return ok() for BOTH found and not-found; `found`
                # is what distinguishes them.
                out[key] = {"status": VERIFIED if data.get("found", False) else NOT_FOUND,
                            "label": SOURCE_LABELS.get(key)}
        except Exception as exc:  # noqa: BLE001 — one source must not sink the run
            out[key] = {"status": FAILED, "reason": f"{type(exc).__name__}: {exc}"[:200],
                        "label": SOURCE_LABELS.get(key)}
        out[key]["verified_at"] = datetime.utcnow().isoformat() + "Z"
        out[key]["lookup_identifier"] = npi
    return out


#: Connectors that EXIST and are queried on every verification. Coverage is
#: measured against this set, not against every source the model can name.
#: Counting an unbuilt connector as a missing source would report permanently
#: degraded coverage for work that was never scheduled — it makes the platform
#: look broken rather than incomplete, and no verification could ever reach
#: full coverage no matter how healthy the live sources were.
IMPLEMENTED_SOURCES = ("nppes", "pecos", "oig_leie")


def coverage_note(sources: Dict[str, dict]) -> dict:
    """Plain-language coverage over the connectors that actually exist."""
    impl = {k: v for k, v in sources.items() if k in IMPLEMENTED_SOURCES}
    unimplemented = sorted(k for k in sources if k not in IMPLEMENTED_SOURCES)

    checked = [k for k, v in impl.items()
               if v.get("status") in (VERIFIED, NOT_FOUND, "clear", "excluded")]
    unavailable = [k for k, v in impl.items() if v.get("status") == UNAVAILABLE]
    not_checked = [k for k, v in impl.items() if v.get("status") == NOT_CHECKED]
    failed = [k for k, v in impl.items() if v.get("status") == FAILED]
    verified = [k for k, v in impl.items() if v.get("status") in (VERIFIED, "clear")]

    parts = [f"{len(checked)} of {len(impl)} implemented sources checked."]
    if unavailable:
        parts.append(f"Unavailable: {', '.join(sorted(unavailable))}.")
    if not_checked:
        parts.append(f"Not checked: {', '.join(sorted(not_checked))}.")
    if failed:
        parts.append(f"Errored: {', '.join(sorted(failed))}.")
    if unimplemented:
        # Reported separately and explicitly. These are a roadmap item, not a
        # coverage failure, and conflating the two misstates both.
        parts.append(f"Not implemented (excluded from coverage): "
                     f"{', '.join(unimplemented)}.")

    return {
        "sources_checked": len(checked),
        "sources_available": len(impl),          # implemented connectors only
        "sources_verified": len(verified),
        "sources_unavailable": len(unavailable),
        "sources_not_checked": len(not_checked),
        "sources_failed": len(failed),
        "sources_not_implemented": len(unimplemented),
        "not_implemented": unimplemented,
        "coverage_note": " ".join(parts),
    }


def detect_source_conflict(sources: Dict[str, dict]) -> bool:
    """Do two sources that BOTH answered contradict each other?

    Only sources that actually responded can conflict. If one is unavailable or
    unimplemented there is a gap, not a disagreement, and calling that a
    conflict would manufacture a B3 out of an outage.

    Two contradictions are recognised:
      * NPPES has the provider, PECOS does not — an enrolment inconsistency.
      * PECOS shows the provider enrolled while OIG lists them as excluded —
        the more serious pairing, since an excluded provider should not be
        actively enrolled.
    """
    def st(name: str) -> Optional[str]:
        return (sources.get(name) or {}).get("status")

    nppes, pecos, oig = st("nppes"), st("pecos"), st("oig_leie")

    if nppes == VERIFIED and pecos == NOT_FOUND:
        return True
    if pecos == VERIFIED and oig == "excluded":
        return True
    return False


def _derived_fields(sources: Dict[str, dict], npi_flagged: bool) -> dict:
    """Signals the rules reference but the connectors do not emit directly."""
    nppes = (sources.get("nppes") or {}).get("status")
    pecos = (sources.get("pecos") or {}).get("status")
    return {
        "npi_validation": "invalid" if npi_flagged else "valid",
        # Conflict means both answered and disagreed. If either is unavailable
        # there is no conflict to see — only a gap.
        "nppes_pecos_conflict": (
            nppes in (VERIFIED, NOT_FOUND) and pecos in (VERIFIED, NOT_FOUND)
            and nppes != pecos),
        "multiple_source_conflict": detect_source_conflict(sources),
    }


async def _resolve_entity(db, entity, sources: Dict[str, dict]) -> dict:
    """Steps 2-4: compare the registry record against what the sources returned.

    Step 2 normalises both addresses to USPS Publication 28 form, step 3 scores
    the organisation names with Jaro-Winkler, and step 4 asks an AI to adjudicate
    ONLY when those two disagree and AI resolution is enabled.

    Returns a plain dict for the review snapshot. Never raises — the caller wraps
    this too, but failing closed here keeps the reason attached to the review
    rather than only in a log line.
    """
    from app.tefca_registry.entity_resolver import EntityResolver, resolution_mode

    # The authoritative record as the sources describe it. NPPES is the registry
    # of record for provider name and practice address, so it is preferred; PECOS
    # is the fallback. A source that errored contributes nothing.
    authoritative = {}
    for src in ("nppes", "pecos"):
        info = sources.get(src) or {}
        data = info.get("data") or {}
        if info.get("status") in (None, "error") or not data:
            continue
        authoritative = {
            "name": data.get("organization_name") or data.get("name") or "",
            "address": data.get("practice_address") or data.get("address") or "",
            "npi": data.get("npi") or "",
            "entity_type": data.get("entity_type") or "",
        }
        if authoritative.get("name") or authoritative.get("address"):
            authoritative["_source"] = src
            break

    if not authoritative:
        return {"status": "no_authoritative_record",
                "note": "no source returned comparable name/address data",
                "mode": resolution_mode()}

    ours = {
        "name": getattr(entity, "name", "") or "",
        "address": getattr(entity, "address", "") or "",
        "entity_type": getattr(entity, "entity_type", "") or "",
    }

    ai_client = None
    try:
        from app.tefca_registry.ai_client import build_ai_client
        ai_client = build_ai_client()
    except Exception as e:  # noqa: BLE001 — deterministic path must still run
        logger.debug("AI client unavailable, deterministic resolution only: %s", e)

    resolver = EntityResolver(ai_client=ai_client)
    result = resolver.resolve(ours, authoritative)

    # Step 7: every AI call is audit-logged with model, prompt version, input,
    # output, confidence, threshold, latency and software version.
    for record in resolver.audit_records:
        reg_audit.record(db, "ai_entity_resolution", entity.id,
                         metadata=record)

    return {
        "status": "resolved",
        "mode": resolver.mode,
        "compared_against": authoritative.get("_source"),
        "is_match": result.is_match,
        "confidence": result.confidence,
        "method": result.method,
        "reasoning": result.reasoning,
        "requires_manual_review": result.requires_manual_review,
        "ai_consulted": result.ai_consulted,
        "threshold_applied": result.threshold_applied,
        "details": result.details,
    }


async def run_review(db, entity, *, user=None, ip_address: Optional[str] = None,
                     sample_id=None, trigger: str = "manual") -> dict:
    """Verify, classify, persist a ReviewRecord, return the response envelope."""
    from app.services.npi_validator import validate_npi
    from sqlalchemy import select
    from app.tefca_registry.review_routes import generate_review_id

    await ensure_seed_rules(db)
    # v2 wires SAM.gov into classification. Every SAM condition fires only
    # on a positive finding, so with no SAM key this is a no-op on bucketing
    # (test_v2_is_identical_to_v1_when_sam_is_silent). Idempotent.
    await ensure_rules_v2(db)
    actor_id, actor_email = reg_audit.actor_of(user)

    reg_audit.record(db, reg_audit.VERIFICATION_STARTED, entity.id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address, metadata={"trigger": trigger})

    sources = await probe_sources(db, entity.id)

    npi = (await db.execute(
        select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.entity_id == entity.id,
            reg.TefcaEntityIdentifier.identifier_type == "npi").limit(1))
    ).scalar_one_or_none()
    npi_flagged = bool(npi) and not validate_npi(npi)[0]

    results = {"sources": sources, "fields": _derived_fields(sources, npi_flagged),
               "confidence_score": None}

    # ── Steps 2-4: entity resolution (USPS -> Jaro-Winkler -> AI) ────────────
    # Runs BEFORE classification and contributes nothing to the bucket: the B1-B4
    # rules engine remains the sole classifier, so wiring this in cannot change
    # any existing classification outcome. What it produces is a resolution
    # opinion recorded alongside the review for a human to act on.
    #
    # AI is reached only when the deterministic steps disagree AND
    # AI_ENTITY_RESOLUTION is not "disabled" (the default). With AI off this is
    # pure USPS normalisation plus name similarity, costs nothing, and calls
    # nothing external.
    try:
        results["entity_resolution"] = await _resolve_entity(db, entity, sources)
    except Exception as e:  # noqa: BLE001 — resolution must never fail a review
        logger.warning("Entity resolution skipped for %s: %s", entity.id, e)
        results["entity_resolution"] = {"status": "skipped", "reason": str(e)[:200]}

    classification = await _classifier.classify_with_db(db, results)
    review_id = await generate_review_id(db)

    # One audit row per source — the minimal record an auditor needs to retrace
    # the decision, without storing full provenance.
    for src, info in sources.items():
        db.add(reg.TefcaVerification(
            entity_id=entity.id, review_id=review_id, source=src,
            lookup_identifier=info.get("lookup_identifier"),
            verification_status=("verified" if info.get("status") == "clear"
                                 else info.get("status")),
            detail=info.get("reason"), data_source_label=info.get("label")))

    db.add(reg.ReviewRecord(
        review_id=review_id, entity_id=entity.id, sample_id=sample_id,
        verification_results=results,          # snapshot, not a live pointer
        classification_bucket=classification.bucket,
        classification_rule=classification.rule_code,
        classification_rule_version=classification.rule_version,
        classification_rationale=classification.rationale,
        reviewed_at=datetime.utcnow()))

    if sample_id:
        await db.execute(
            reg.SampleEntity.__table__.update()
            .where(reg.SampleEntity.sample_id == sample_id,
                   reg.SampleEntity.entity_id == entity.id)
            .values(review_id=review_id, review_status="reviewed",
                    discrepancy_bucket=classification.bucket,
                    reviewed_at=datetime.utcnow()))

    reg_audit.record(db, reg_audit.VERIFICATION_COMPLETED, entity.id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"review_id": review_id,
                               "bucket": classification.bucket,
                               "rule": classification.rule_code,
                               "rule_version": classification.rule_version})
    await db.commit()

    return {
        "entity_id": str(entity.id),
        "review_id": review_id,
        "verification": sources,
        "classification": {
            **classification.as_dict(),
            "classified_at": datetime.utcnow().isoformat() + "Z",
        },
        "confidence": coverage_note(sources),
    }
