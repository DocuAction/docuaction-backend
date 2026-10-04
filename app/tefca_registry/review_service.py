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
from typing import Any, Dict, List, Optional

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import (
    BucketClassifier, FAILED, NOT_CHECKED, NOT_FOUND, UNAVAILABLE, VERIFIED,
    ensure_seed_rules, ensure_rules_v2, ensure_rules_v3)
from app.services.npi_validator import npi_rejection_reason
from app.Tefca.connectors import PECOS_UI_LABEL, PECOS_UI_SUBTITLE

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

#: Fix 3: "pecos" is the legacy NPPES-proxy connector (PECOS_BACKING =
#: "nppes_proxy" in app.Tefca.connectors) — it does not check Medicare
#: enrolment, and the label must say so. Reads PECOS_UI_LABEL/PECOS_UI_SUBTITLE
#: directly (imported above) rather than duplicating the text, so the two can
#: never drift apart.
SOURCE_LABELS = {
    "nppes": "NPI Registry — CMS/HHS",
    "pecos": PECOS_UI_LABEL,
    "oig_leie": "Exclusion List — OIG/HHS",
    "sam_gov": "Federal Registration — GSA",
    "state_registry": "State licensure registry",
    "irs": "IRS Exempt Organizations",
}
SOURCE_SUBTITLES = {
    "pecos": PECOS_UI_SUBTITLE,
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
        k: {"status": NOT_CHECKED, "reason": why, "label": SOURCE_LABELS.get(k),
            "subtitle": SOURCE_SUBTITLES.get(k)}
        for k, why in NO_CONNECTOR.items()
    }

    if not npi:
        for key in ("nppes", "pecos", "oig_leie"):
            out[key] = {"status": NOT_CHECKED,
                        "reason": "entity has no NPI identifier to look up",
                        "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key)}
        # A missing NPI must not remove the entity from exclusion NAME
        # screening (2026-10-04, Part B). NPPES/PECOS are NPI-keyed and stay
        # NOT_CHECKED; the OIG LEIE is also searchable by organisation name,
        # and the delivery path already does that (`evidence_service.
        # gather_sources`, `leie_org`). Before this, a manual review of an
        # NPI-less entity never screened the exclusion list at all.
        out["oig_leie"] = await _leie_org_name_screen(db, entity_id)
        return out

    # Centralised gate (Fix 1), ahead of the connector loop: a present but
    # malformed/checksum-invalid NPI is reported NOT_CHECKED — never sent
    # upstream, never NOT_FOUND, never an adverse finding — and NOT_CHECKED is
    # exactly the status this module's own docs already define as "needs a
    # decision", i.e. analyst review, never a silent pass. The connectors
    # below (NPPESConnector/PECOSConnector) apply the identical validator as a
    # second, independent gate — this early check only avoids the wasted call.
    npi_rejection = npi_rejection_reason(npi)
    if npi_rejection:
        for key in ("nppes", "pecos", "oig_leie"):
            out[key] = {"status": NOT_CHECKED, "reason": npi_rejection,
                        "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key)}
        return out

    try:
        from app.Tefca.connectors import SourceConnectorManager
        mgr = SourceConnectorManager()
    except Exception as exc:  # pragma: no cover
        logger.warning("TEFCA connectors unavailable: %s", exc)
        for key in ("nppes", "pecos", "oig_leie"):
            out[key] = {"status": UNAVAILABLE, "reason": f"connector import failed: {exc}",
                        "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key)}
        return out

    for key, attr in (("nppes", "nppes"), ("pecos", "pecos"), ("oig_leie", "leie")):
        conn = getattr(mgr, attr, None)
        fn = getattr(conn, "lookup_by_npi", None) if conn else None
        if fn is None:
            out[key] = {"status": NOT_CHECKED, "reason": "connector not available",
                        "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key)}
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
                            "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key)}
                if key == "nppes":
                    out[key]["npi_outcome"] = "NPI_VERIFICATION_UNAVAILABLE"
                    out[key]["npi_outcome_detail"] = out[key]["reason"]
            elif key == "oig_leie":
                # Exclusion list: a hit is bad news, absence is the good outcome.
                # `excluded` counts only ACTIVE exclusions — a reinstated
                # provider is not currently excluded.
                out[key] = {"status": "excluded" if data.get("excluded") else "clear",
                            "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key),
                            "exclusion_count": data.get("exclusion_count", 0)}
            else:
                # NPPES/PECOS return ok() for BOTH found and not-found; `found`
                # is what distinguishes them.
                out[key] = {"status": VERIFIED if data.get("found", False) else NOT_FOUND,
                            "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key)}
                if key == "nppes":
                    # Found + active, found + DEACTIVATED, not found, unavailable
                    # are four different statements. `status` keeps the
                    # five-state vocabulary the classifier reads; `npi_outcome`
                    # carries the finer distinction to the issue ledger.
                    from app.tefca_registry.rce import verification_findings as vf
                    derived = vf.npi_outcome_from_nppes(data, ok=True)
                    out[key]["npi_outcome"] = derived["outcome"]
                    out[key]["npi_outcome_detail"] = derived.get("detail")
                    if derived["outcome"] == vf.NPI_DEACTIVATED:
                        out[key]["npi_status"] = "DEACTIVATED"
                        out[key]["deactivation_date"] = derived.get("deactivation_date")
                        out[key]["reason"] = derived.get("detail")
                # Carry the authoritative record forward.
                #
                # Without this, `data` never left this function, and
                # _resolve_entity — which reads info["data"] and skips a source
                # that has none — found nothing to compare against on every
                # single run. The effect was silent: entity resolution reported
                # "no_authoritative_record" and steps 6 and 7 of the documented
                # pipeline (Jaro-Winkler name matching, address comparison) never
                # executed, while NPPES itself reported "verified".
                #
                # Only the comparison fields are copied, not the whole payload:
                # this dict is persisted as the review's verification_results
                # snapshot, and snapshots are kept for the life of the contract.
                if data.get("found"):
                    out[key]["data"] = {
                        "organization_name": (data.get("organization_name")
                                              or data.get("legal_name")
                                              or data.get("name")),
                        "practice_address": _practice_address(data),
                        "npi": data.get("npi") or npi,
                        "entity_type": data.get("enumeration_type")
                        or data.get("entity_type"),
                    }
        except Exception as exc:  # noqa: BLE001 — one source must not sink the run
            out[key] = {"status": FAILED, "reason": f"{type(exc).__name__}: {exc}"[:200],
                        "label": SOURCE_LABELS.get(key), "subtitle": SOURCE_SUBTITLES.get(key)}
            if key == "nppes":
                out[key]["npi_outcome"] = "NPI_VERIFICATION_UNAVAILABLE"
                out[key]["npi_outcome_detail"] = out[key]["reason"]
        out[key]["verified_at"] = datetime.utcnow().isoformat() + "Z"
        out[key]["lookup_identifier"] = npi
    return out


#: Marker carried by an exclusion result that came from a NAME search rather
#: than an identifier match. Weaker evidence: never counted as a verified
#: source, and a hit is a CANDIDATE for an analyst, never a confirmed exclusion.
MATCHED_BY_ORG_NAME = "organisation_name"


async def _leie_org_name_screen(db, entity_id) -> dict:
    """OIG LEIE screened by organisation name for an entity with no NPI.

    Uses the SAME two classifier states the delivery path produces for the
    same evidence (`arc_pipeline.evidence_item_state` on the exclusion
    dimension), so the two paths cannot disagree about what a name screen
    means:
        candidate found  REVIEW    -> "not_found"  potential hit; a person
                                                   decides; never "excluded"
        nothing listed   NOT_FOUND -> "clear"      screened by name only
        list unreachable           -> UNAVAILABLE  never a clearance
        no name either             -> NOT_CHECKED  screening did not happen
    Never raises.
    """
    from sqlalchemy import select
    from app.tefca_registry.rce.arc_pipeline import EXCLUSION_DIMENSION, evidence_item_state

    base = {"label": SOURCE_LABELS.get("oig_leie"), "subtitle": SOURCE_SUBTITLES.get("oig_leie"),
            "matched_by": MATCHED_BY_ORG_NAME}
    name = ((await db.execute(
        select(reg.TefcaRegEntity.name).where(reg.TefcaRegEntity.id == entity_id))
    ).scalar_one_or_none() or "").strip()
    if not name:
        return {**base, "status": NOT_CHECKED, "matched_by": None,
                "reason": ("entity has neither an NPI nor an organisation name; no "
                           "exclusion screening could be performed -- this is NOT a "
                           "clearance")}
    try:
        from app.Tefca.connectors import SourceConnectorManager
        r = await SourceConnectorManager().leie.lookup_by_name(last="", first="", org=name)
    except Exception as exc:  # noqa: BLE001 -- one source must not sink the run
        return {**base, "status": FAILED, "reason": f"{type(exc).__name__}: {exc}"[:200]}
    if getattr(r, "error", None) or not getattr(r, "success", False):
        return {**base, "status": UNAVAILABLE,
                "reason": str(getattr(r, "error", None) or "source did not complete")[:200]}
    data = getattr(r, "data", None) or {}
    hit = bool(data.get("excluded") or data.get("exclusion_found"))
    disposition = "REVIEW" if hit else "NOT_FOUND"
    return {**base,
            "status": evidence_item_state(EXCLUSION_DIMENSION, disposition),
            "disposition": disposition,
            "potential_hit": hit,
            "exclusion_count": data.get("exclusion_count", 0),
            "lookup_identifier": f"org={name}"[:50],
            "verified_at": datetime.utcnow().isoformat() + "Z",
            "reason": (
                "No NPI. OIG LEIE screened by organisation name: a CANDIDATE match "
                "was found. A name match alone does not confirm an exclusion and "
                "does not clear one -- analyst determination required."
                if hit else
                "No NPI. OIG LEIE screened by organisation name: no candidate found "
                "in the current list using an exact organisation-name search. Weaker "
                "than an identifier match; not an equivalent clearance.")}


#: Persisted exclusion/revocation evidence the manual path CONSUMES (2026-10-03).
#:
#: `probe_sources` only ever queries the live NPPES/PECOS/LEIE connectors and
#: stubs `sam_gov` as a permanent NOT_CHECKED. The bulk path
#: (`arc_pipeline.verify_and_classify`) does query SAM.gov and persists the
#: answer as `tefca_dimension_evidence` rows. Until this helper existed the
#: manual path ignored those rows entirely — a confirmed SAM exclusion
#: persisted at B4 by the bulk path was followed by a NEWER manual
#: ReviewRecord at B1 (proven in tests/test_sam_manual_review_asymmetry.py).
#:
#: Rules, fail-closed:
#:   * If persisted evidence exists for a source, the classifier sees its real
#:     state, translated through the SAME `_DISPOSITION_TO_STATE` the bulk
#:     path uses (REVIEW -> not_found, PASS -> verified, ...). A persisted
#:     exclusion can never be classified B1 here.
#:   * A live answer is kept when it is at least as bad as the persisted one
#:     (a live "excluded" beats a persisted "not_found"); persisted evidence
#:     overrides a live "clear"/verified only when it is worse. The worse
#:     state always wins, mirroring `arc_pipeline._STATE_PRECEDENCE`.
#:   * With NO persisted evidence the existing NOT_CHECKED stub stands, with
#:     its reason; nothing is invented.
#:   * No network call; `IMPLEMENTED_SOURCES` (the live-probe set) is unchanged.
#:   * The evidence generation timestamp is carried into the review's
#:     rationale so a reviewer can see how old the SAM/LEIE evidence is. No
#:     freshness cutoff is applied — that is a policy decision (proposed, not
#:     applied, in SAM_MANUAL_REVIEW_ASYMMETRY.md).
PERSISTED_EXCLUSION_DIMENSION = "EXCLUSION_REVOCATION"
PERSISTED_EVIDENCE_SOURCES = {"SAM_GOV": "sam_gov", "OIG_LEIE": "oig_leie",
                              "CMS_REVOCATION": "cms_revocation"}
#: Worst first. "excluded"/"clear" are the manual path's own literals for the
#: exclusion list; the five canonical states sit between them.
_PERSISTED_PRECEDENCE = {"excluded": 0, FAILED: 1, NOT_FOUND: 2, UNAVAILABLE: 3,
                         NOT_CHECKED: 4, VERIFIED: 5, "clear": 5}


def _precedence(status: Optional[str]) -> int:
    # An unknown literal never outranks evidence.
    return _PERSISTED_PRECEDENCE.get(status, len(_PERSISTED_PRECEDENCE))


async def latest_persisted_exclusion_evidence(db, entity_id) -> Dict[str, Any]:
    """Most recent `tefca_dimension_evidence` row per source for the
    EXCLUSION_REVOCATION dimension. Rows are append-only, so the newest
    `created_at` is the newest generation."""
    from sqlalchemy import select
    from app.Tefca.models import TEFCADimensionEvidence as DE

    rows = (await db.execute(
        select(DE).where(DE.entity_id == str(entity_id),
                         DE.evidence_dimension == PERSISTED_EXCLUSION_DIMENSION,
                         DE.source.in_(list(PERSISTED_EVIDENCE_SOURCES)))
        .order_by(DE.created_at.desc(), DE.generation_timestamp.desc()))).scalars().all()
    latest: Dict[str, Any] = {}
    for row in rows:
        latest.setdefault(row.source, row)
    return latest


def _evidence_age_days(generation_timestamp: Optional[str]) -> Optional[float]:
    if not generation_timestamp:
        return None
    try:
        stamp = datetime.fromisoformat(str(generation_timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.replace(tzinfo=None)
    return round((datetime.utcnow() - stamp).total_seconds() / 86400, 2)


def apply_persisted_exclusion_evidence(sources: Dict[str, dict],
                                       latest_rows: Dict[str, Any]) -> List[dict]:
    """Fold persisted exclusion/revocation evidence into `sources`, in place.
    Returns one provenance record per source whose state came from persisted
    evidence — carried into the rationale and the review snapshot."""
    # The SAME translation the bulk path's classifier input uses — including
    # the 2026-10-03 correction that a clean exclusion-list name screen
    # (NOT_FOUND on this dimension) is "clear", not a disqualifying not_found.
    from app.tefca_registry.rce.arc_pipeline import evidence_item_state

    used: List[dict] = []
    for source_name, row in latest_rows.items():
        key = PERSISTED_EVIDENCE_SOURCES[source_name]
        state = evidence_item_state(PERSISTED_EXCLUSION_DIMENSION, row.disposition)
        live = sources.get(key) or {}
        live_status = live.get("status")
        live_answered = live_status in ("excluded", "clear", VERIFIED, NOT_FOUND)
        if live_answered and _precedence(live_status) <= _precedence(state):
            # The live probe already said something at least as bad. Keep it;
            # note that the persisted row was seen and not needed.
            live["persisted_evidence_seen"] = {
                "source": source_name, "disposition": row.disposition,
                "generation_timestamp": row.generation_timestamp}
            continue
        provenance = {
            "source": source_name, "disposition": row.disposition,
            "state": state, "generation_timestamp": row.generation_timestamp,
            "age_days": _evidence_age_days(row.generation_timestamp),
            "evidence_id": str(row.id), "review_id": row.review_id,
            "rule_applied": row.rule_applied,
            "superseded_live_status": live_status,
        }
        sources[key] = {
            "status": state,
            "label": SOURCE_LABELS.get(key, source_name),
            "subtitle": SOURCE_SUBTITLES.get(key),
            "reason": (f"persisted {source_name} evidence ({PERSISTED_EXCLUSION_DIMENSION}) "
                       f"disposition {row.disposition}, generated "
                       f"{row.generation_timestamp or 'unknown'}"
                       + (f"; replaces live {live_status}" if live_status else "")),
            "verified_at": row.generation_timestamp,
            "lookup_identifier": row.query_identifier,
            "persisted_evidence": provenance,
        }
        used.append(provenance)
    return used


def persisted_evidence_rationale(used: List[dict]) -> str:
    if not used:
        return ""
    parts = [f"{u['source']} {u['disposition']} -> {u['state']} (generated "
             f"{u['generation_timestamp'] or 'unknown'}"
             + (f", {u['age_days']} days old" if u.get("age_days") is not None else "")
             + ")" for u in used]
    return " Persisted exclusion/revocation evidence consumed: " + "; ".join(parts) + "."


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
    # A name-only exclusion screen is weaker than an identifier match: it is
    # CHECKED, but never counted among the VERIFIED sources (2026-10-04).
    name_only = sorted(k for k, v in impl.items()
                       if v.get("matched_by") == MATCHED_BY_ORG_NAME
                       and v.get("status") in ("clear", NOT_FOUND))
    verified = [k for k, v in impl.items()
                if v.get("status") in (VERIFIED, "clear") and k not in name_only]

    parts = [f"{len(checked)} of {len(impl)} implemented sources checked."]
    if name_only:
        parts.append(f"Screened by organisation name only (not counted as verified): "
                     f"{', '.join(name_only)}.")
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
        "sources_name_screen_only": len(name_only),
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

    # No AI client is injected. AI reaches this pipeline only through
    # TEFCAAIOrchestrator (app/tefca_registry/ai/), which applies policy, the
    # egress allowlist, output validation, evidence scoring, the human gate, and
    # audit before any recommendation comes back. resolve_with_orchestrator()
    # runs the deterministic steps first and consults AI only for a genuinely
    # inconclusive pair; it never raises, so an unavailable or denied control
    # plane yields the deterministic result unchanged.
    resolver = EntityResolver()
    result = await resolver.resolve_with_orchestrator(
        ours, authoritative,
        evidence_signals={
            # The one objective signal this function can see and the resolver
            # cannot: whether the upstream sources corroborate each other. The
            # resolver folds it into no_conflicting_fields alongside its own
            # identifier-conflict check.
            "source_agreement": not detect_source_conflict(sources),
        },
    )

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
        # Objective evidence quality, not the model's self-assessment. Present
        # whenever the control plane ran (including when it declined to call
        # AI), absent when steps 1-3 settled the pair without it.
        "evidence_quality_score": result.details.get("evidence_quality_score"),
        # Surfaced explicitly rather than left implicit in requires_manual_review
        # so a consumer of this payload cannot read an AI recommendation without
        # also reading that a human must sign it off.
        "human_review_required": bool(result.requires_manual_review),
        "details": result.details,
    }


def _practice_address(data: dict) -> str:
    """One-line practice address from an NPPES/PECOS payload.

    NPPES returns `addresses` as a list, each tagged LOCATION or MAILING. The
    LOCATION entry is the practice address and the one worth comparing; a
    mailing address is frequently a lockbox or a corporate office in another
    state, so comparing against it would manufacture mismatches for entities
    that are perfectly consistent.
    """
    addresses = data.get("addresses") or []
    chosen = next((a for a in addresses
                   if str(a.get("address_purpose", "")).upper() == "LOCATION"), None)
    # No LOCATION row means NO practice address is known, not "use whatever
    # row NPPES listed first". That first row is a MAILING address whenever
    # LOCATION is absent, and comparing the submitted practice address against
    # a lockbox manufactured a mismatch no one could act on. Fail closed: an
    # empty string makes `_compare_addresses` record `not_compared`, which is
    # neither a match nor a finding. (2026-10-03, safeguard 5.)
    if chosen is None:
        if addresses:
            return ""
        return data.get("practice_address") or data.get("address") or ""
    parts = [chosen.get("address_1"), chosen.get("address_2"),
             chosen.get("city"), chosen.get("state"),
             # NPPES pads ZIP to 9 digits with no hyphen; the normalizer reads a
             # 5-digit ZIP, and "212870010" would not match "21287".
             (chosen.get("postal_code") or "")[:5]]
    return " ".join(str(p).strip() for p in parts if p and str(p).strip())


def _address_meta(results: dict, key: str):
    """Read one address-comparison field for the audit row.

    Tolerant by design: the comparison is wrapped in its own try/except above, so
    `address_match` may be the skipped-shape dict instead of a full AddressMatch.
    An audit row must still be written in that case."""
    return (results.get("address_match") or {}).get(key)


async def _compare_addresses(entity, sources: Dict[str, dict]) -> dict:
    """Compare the registry address against what NPPES (or PECOS) reports.

    Returns the AddressMatch as a dict, with the two audit fields the caller
    needs folded in. `usps_latency_ms` is measured here rather than read off the
    result because AddressMatch carries no latency field — only USPSAddressResult
    does, and by the time the comparison is built those have been consumed.
    It is None whenever USPS was not called, which is the common case.
    """
    import time as _time
    from app.tefca_registry.address_normalizer import ThreeLayerAddressNormalizer

    authoritative = ""
    compared_against = None
    for src in ("nppes", "pecos"):
        info = sources.get(src) or {}
        data = info.get("data") or {}
        if info.get("status") in (None, "error") or not data:
            continue
        authoritative = data.get("practice_address") or data.get("address") or ""
        if authoritative:
            compared_against = src
            break

    submitted = getattr(entity, "address", "") or ""
    if not submitted or not authoritative:
        # Not a mismatch — there was nothing to compare. Saying "no match" here
        # would put a discrepancy in the record that no one can act on.
        return {"match": False, "confidence": 0.0, "method": "not_compared",
                "reason": "registry or source address missing",
                "compared_against": compared_against,
                "usps_used": False, "usps_latency_ms": None}

    started = _time.perf_counter()
    result = await ThreeLayerAddressNormalizer().standardize_and_compare(
        submitted, authoritative)
    elapsed_ms = round((_time.perf_counter() - started) * 1000, 2)

    usps_used = result.method.startswith("usps")
    payload = result.model_dump()
    payload["compared_against"] = compared_against
    payload["usps_used"] = usps_used
    payload["usps_latency_ms"] = elapsed_ms if usps_used else None
    return payload


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
    # v3 makes v2's SAM/LEIE disqualifier reachable on the RCE path too
    # (2026-10-02; see bucket_classifier._v3_rules's docstring). Purely
    # additive over v2, same no-op-when-silent guarantee. Idempotent.
    await ensure_rules_v3(db)
    actor_id, actor_email = reg_audit.actor_of(user)

    reg_audit.record(db, reg_audit.VERIFICATION_STARTED, entity.id,
                     actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address, metadata={"trigger": trigger})

    sources = await probe_sources(db, entity.id)

    # Persisted exclusion/revocation evidence from the bulk path (2026-10-03).
    # Read-only, no network; see PERSISTED_EVIDENCE_SOURCES. A query failure
    # here must not fail the review, but it must not pass silently either:
    # the stub stays NOT_CHECKED and the failure is logged, never "clear".
    persisted_used: List[dict] = []
    try:
        persisted_rows = await latest_persisted_exclusion_evidence(db, entity.id)
        persisted_used = apply_persisted_exclusion_evidence(sources, persisted_rows)
    except Exception as exc:  # noqa: BLE001 — evidence read must not sink the review
        logger.error("Persisted exclusion evidence not consumed for %s: %s",
                     entity.id, type(exc).__name__, exc_info=True)

    # The NPPES outcome goes to the delivery's issue ledger too (NPI-005/006/
    # 009), so the exception view shows every NPI question in one place. A
    # repeat verification with the same outcome writes nothing new.
    try:
        from app.tefca_registry.rce import verification_findings as vf
        await vf.record_from_sources(db, entity_id=entity.id, sources=sources)
    except Exception as exc:  # noqa: BLE001 — the ledger must not fail a review
        logger.error("NPI verification outcome not recorded for %s: %s",
                     entity.id, type(exc).__name__, exc_info=True)

    npi = (await db.execute(
        select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.entity_id == entity.id,
            reg.TefcaEntityIdentifier.identifier_type == "npi").limit(1))
    ).scalar_one_or_none()
    npi_flagged = bool(npi) and not validate_npi(npi)[0]

    results = {"sources": sources, "fields": _derived_fields(sources, npi_flagged),
               "confidence_score": None,
               # Provenance of every source state taken from persisted evidence
               # rather than a live probe — part of the review snapshot.
               "persisted_evidence": persisted_used}

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

    # ── Address comparison (three-layer: code → USPS → code-only) ────────────
    # Like entity resolution above, this is recorded alongside the review and
    # feeds nothing into classification — the B1-B4 rules engine stays the sole
    # classifier, so wiring an external API in here cannot move a bucket. With
    # no USPS credentials it is pure code normalization: no network, no cost.
    try:
        results["address_match"] = await _compare_addresses(entity, sources)
    except Exception as e:  # noqa: BLE001 — an address check must never fail a review
        logger.warning("Address comparison skipped for %s: %s", entity.id, e)
        results["address_match"] = {"method": "skipped", "reason": str(e)[:200]}

    classification = await _classifier.classify_with_db(db, results)
    # The rule set and version path are unchanged; only the rationale text
    # gains the evidence provenance, so a reviewer can see how old the
    # SAM/LEIE evidence behind this determination is.
    rationale = classification.rationale + persisted_evidence_rationale(persisted_used)
    review_id = await generate_review_id(db)

    # One audit row per source — the minimal record an auditor needs to retrace
    # the decision, without storing full provenance.
    for src, info in sources.items():
        lookup = info.get("lookup_identifier")
        db.add(reg.TefcaVerification(
            entity_id=entity.id, review_id=review_id, source=src,
            # String(50); a persisted-evidence query identifier can be longer
            # than a bare NPI (the bulk path truncates the same way).
            lookup_identifier=(str(lookup)[:50] if lookup else None),
            verification_status=("verified" if info.get("status") == "clear"
                                 else info.get("status")),
            detail=info.get("reason"), data_source_label=info.get("label")))

    db.add(reg.ReviewRecord(
        review_id=review_id, entity_id=entity.id, sample_id=sample_id,
        verification_results=results,          # snapshot, not a live pointer
        classification_bucket=classification.bucket,
        classification_rule=classification.rule_code,
        classification_rule_version=classification.rule_version,
        classification_rationale=rationale,
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
                               "rule_version": classification.rule_version,
                               # Which address path decided this review, and what
                               # it cost. An auditor asking "was an external API
                               # consulted for this record" needs to answer it
                               # from the audit row, not by re-running anything.
                               "address_method": _address_meta(results, "method"),
                               "usps_used": bool(_address_meta(results, "usps_used")),
                               "usps_latency_ms": _address_meta(results,
                                                                "usps_latency_ms")})
    await db.commit()

    return {
        "entity_id": str(entity.id),
        "review_id": review_id,
        "verification": sources,
        "classification": {
            **classification.as_dict(),
            "rationale": rationale,
            "classified_at": datetime.utcnow().isoformat() + "Z",
        },
        "persisted_evidence": persisted_used,
        "confidence": coverage_note(sources),
        # Steps 6-7 of the documented pipeline. Both were already computed and
        # persisted into verification_results, but neither was returned — so a
        # caller who had just run a verification could not see what the name and
        # address comparison concluded without re-reading the review record.
        # They are the two fields a reviewer looks at first when a bucket looks
        # wrong.
        "entity_resolution": results.get("entity_resolution"),
        "address_match": results.get("address_match"),
    }
