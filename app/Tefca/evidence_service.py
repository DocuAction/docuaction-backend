"""
Evidence orchestration: query the sources, build the six dimensions, persist.

WHAT THIS ADDS, AND WHAT IT DELIBERATELY LEAVES ALONE
─────────────────────────────────────────────────────
This is an additive evidence layer. It does NOT touch ValidationEngine, the
B1–B4 bucket rules, the five-element evidence record, or any existing route's
response shape. The approved methodology is unchanged; what is new is a
dimension-organised body of evidence sitting alongside it, with provenance
detailed enough to reproduce every determination.

The existing SourceConnectorManager is used as-is for NPPES / OIG / SAM, so
there is one definition of those lookups in the codebase, not two.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.Tefca.applicability import build_profile
from app.Tefca.cms_ppef import (
    CMSDataAPIClient,
    CMSRevocationConnector,
    PPEFEnrollmentConnector,
    PPEFRelationalConnector,
    cms_capability_health,
)
from app.Tefca.connectors import SourceConnectorManager, SourceResult
from app.Tefca.evidence_assembly import assemble_dimensions
from app.Tefca.evidence_dimensions import (
    DIMENSION_ORDER,
    DimensionResult,
    Disposition,
    sufficiency_summary,
)

logger = logging.getLogger(__name__)


# ── Website corroboration (supplemental evidence only) ───────────────────────

WEBSITE_CORROBORATED = "CORROBORATED"
WEBSITE_CONFLICT = "CONFLICT"
WEBSITE_NOT_FOUND = "NOT_FOUND"
WEBSITE_UNAVAILABLE = "UNAVAILABLE"
WEBSITE_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
WEBSITE_NOT_APPLICABLE = "NOT_APPLICABLE"

#: Every one of these is a fact about the internet, not about the entity.
_UNAVAILABLE_CONDITIONS = ("dns", "timeout", "403", "429", "5xx", "ssl", "anti-bot")


def _entity_website(entity: Dict[str, Any]) -> Optional[str]:
    for t in entity.get("telecom") or []:
        if (t.get("system") or "").lower() == "url":
            return (t.get("value") or "").strip() or None
    return None


async def website_corroboration(entity: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
    """Fetch the entrant's official website, if ONC supplied one.

    Hard rule, implemented as an early return rather than as a caveat further
    down: an unreachable site — DNS failure, timeout, 403, 429, 5xx, SSL error,
    anti-bot challenge — produces UNAVAILABLE and CANNOT affect the entity's
    determination. Neither can the absence of a website. A small business with
    no web presence is not less compliant than one with a CDN.

    No website is derived from an email domain or guessed from a name. Guessing
    at a URL and then treating what comes back as evidence about this entity is
    how an unrelated third party's site ends up in a federal audit trail.
    """
    url = _entity_website(entity)
    if not url:
        return {
            "result": WEBSITE_NOT_FOUND,
            "url": None,
            "unavailable": True,
            "affects_determination": False,
            "note": ("ONC supplied no official website for this entity, and none is "
                     "inferred. Absence of a website never counts against an entity."),
            "checked_at": datetime.utcnow().isoformat(),
        }
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "DocuAction-TEFCA-Review/1.0"})
    except Exception as exc:
        return {
            "result": WEBSITE_UNAVAILABLE, "url": url, "unavailable": True,
            "affects_determination": False,
            "note": f"Website unreachable ({type(exc).__name__}). Not a finding against the entity.",
            "checked_at": datetime.utcnow().isoformat(),
        }

    if resp.status_code in (403, 429) or resp.status_code >= 500:
        return {
            "result": WEBSITE_UNAVAILABLE, "url": url, "unavailable": True,
            "affects_determination": False, "http_status": resp.status_code,
            "note": (f"HTTP {resp.status_code} — blocked, rate-limited or server error. "
                     "An availability fact about the site, not evidence about the entity."),
            "checked_at": datetime.utcnow().isoformat(),
        }
    if resp.status_code >= 400:
        return {
            "result": WEBSITE_NOT_FOUND, "url": url, "unavailable": True,
            "affects_determination": False, "http_status": resp.status_code,
            "note": f"HTTP {resp.status_code}. Not a finding against the entity.",
            "checked_at": datetime.utcnow().isoformat(),
        }

    text = (resp.text or "")[:200_000]
    name = (entity.get("name") or "").strip()
    corroborates_name = bool(name) and name.lower() in text.lower()
    return {
        "result": WEBSITE_CORROBORATED if corroborates_name else WEBSITE_INSUFFICIENT,
        "url": url,
        "unavailable": False,
        "affects_determination": False,
        "http_status": resp.status_code,
        "address": None,  # Address extraction from free HTML is not attempted; see note.
        "note": ("Organisation name found on the official site."
                 if corroborates_name else
                 "Site reachable but did not clearly corroborate the organisation name. "
                 "Supplemental only — never forced into PASS/FAIL."),
        "checked_at": datetime.utcnow().isoformat(),
    }


# ── Orchestration ────────────────────────────────────────────────────────────

class EvidenceService:
    """Query every source for one entity and assemble the six dimensions."""

    def __init__(
        self,
        manager: Optional[SourceConnectorManager] = None,
        cms_client: Optional[CMSDataAPIClient] = None,
        enable_website: bool = False,
        local_store=None,
    ):
        self.manager = manager or SourceConnectorManager()
        client = cms_client or CMSDataAPIClient()
        self.ppef = PPEFEnrollmentConnector(client)
        self.revocation = CMSRevocationConnector(client)
        # local_store lets the four download-only components resolve from an
        # ingested quarterly snapshot. Without one they report UNAVAILABLE with
        # a reason — which is honest, and never a finding against an entity.
        self.relational = PPEFRelationalConnector(client, local_store=local_store)
        # Off by default: website corroboration reaches out to a third-party host
        # and is supplemental evidence only. It is opt-in per review.
        self.enable_website = enable_website

    async def gather_sources(self, entity: Dict[str, Any]) -> Dict[str, SourceResult]:
        """All source lookups for one entity, concurrently.

        The existing five keys (nppes, leie_npi, sam_entity, sam_exclusion,
        pecos) are preserved untouched so ValidationEngine keeps working exactly
        as before; the CMS results are added under new keys.
        """
        from app.Tefca.connectors import _extract_npi

        npi = _extract_npi(entity)
        existing, enrollment, revocation = await asyncio.gather(
            self.manager.query_all_sources(entity),
            self.ppef.lookup_by_npi(npi),
            self.revocation.lookup_by_npi(npi),
            return_exceptions=False,
        )
        sources: Dict[str, SourceResult] = dict(existing)
        sources["cms_ppef_enrollment"] = enrollment
        sources["cms_revocation"] = revocation

        # Relational components are keyed on the enrolment ids the Enrollment
        # extract just gave us — the documented ENRLMT_ID linkage. They resolve
        # to UNAVAILABLE while CMS does not publish them, which the dimension
        # layer handles without ever converting it into a failure.
        enrollment_ids = (enrollment.data or {}).get("enrollment_ids") or [] if enrollment.success else []
        practice_location, reassignment, additional_npis = await asyncio.gather(
            self.relational.practice_locations(enrollment_ids),
            self.relational.reassignments(enrollment_ids),
            self.relational.additional_npis(enrollment_ids),
            return_exceptions=False,
        )
        sources["cms_ppef_practice_location"] = practice_location
        sources["cms_ppef_reassignment"] = reassignment
        sources["cms_ppef_additional_npis"] = additional_npis
        return sources

    async def build_evidence(
        self,
        entity: Dict[str, Any],
        sources: Optional[Dict[str, SourceResult]] = None,
        methodology_requires: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Full dimension-organised evidence for one entity."""
        sources = sources if sources is not None else await self.gather_sources(entity)

        nppes = sources.get("nppes")
        nppes_data = (nppes.data or {}) if nppes and nppes.success else {}
        enrollment = sources.get("cms_ppef_enrollment")
        pecos_found = (
            bool((enrollment.data or {}).get("found"))
            if enrollment is not None and enrollment.success else None
        )

        profile = build_profile(
            entity, nppes_data=nppes_data, pecos_found=pecos_found,
            methodology_requires=methodology_requires,
        )

        website = None
        if self.enable_website:
            try:
                website = await website_corroboration(entity)
            except Exception as exc:  # never let supplemental evidence break a review
                logger.info("website corroboration skipped: %s", exc)
                website = {"result": WEBSITE_UNAVAILABLE, "unavailable": True,
                           "affects_determination": False, "note": str(exc)}

        dimensions = assemble_dimensions(entity, profile, sources, website)
        return {
            "entity_id": entity.get("id"),
            "entity_name": entity.get("name"),
            "generated_at": datetime.utcnow().isoformat(),
            "applicability": profile.to_dict(),
            "dimensions": [d.to_dict() for d in dimensions],
            "dimension_order": [d.value for d in DIMENSION_ORDER],
            "sufficiency": sufficiency_summary(dimensions),
            "website_corroboration": website,
            "evidence_model_note": (
                "Evidence is organised by verification dimension, not by API. No score, "
                "percentage or source count is derived; correlated CMS components are one "
                "body of evidence."
            ),
        }

    async def health(self) -> Dict[str, Any]:
        return await cms_capability_health()


def evidence_rows_for_persistence(
    entity_id: str,
    review_id: Optional[str],
    evidence: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Flatten assembled evidence into insert-ready rows.

    One row per (dimension, source) evidence item. Rows are only ever INSERTED —
    a re-run adds a new generation, it never updates or deletes a previous one,
    because the determination that cited the old evidence has to stay
    explicable after CMS publishes a newer dataset.
    """
    from app.Tefca.source_registry import assert_canonical_evidence_source

    rows: List[Dict[str, Any]] = []
    generated_at = evidence.get("generated_at")
    for dim in evidence.get("dimensions", []):
        for item in dim.get("evidence", []):
            # Refuse to persist the ambiguous legacy key. Genuine PECOS evidence
            # is CMS_PPEF_ENROLLMENT; identity is NPPES. Letting "pecos" into the
            # new store would put two different meanings behind one word in the
            # same audit trail.
            assert_canonical_evidence_source(item.get("source"))
            rows.append({
                "entity_id": entity_id,
                "review_id": review_id,
                "evidence_dimension": dim["dimension"],
                "dimension_disposition": dim["disposition"],
                "dimension_applicability": dim["applicability"],
                "source": item.get("source"),
                "source_dataset": item.get("source_dataset"),
                "ppef_component": item.get("ppef_component"),
                "source_record_identifier": item.get("source_record_identifier"),
                "query_identifier": item.get("query_identifier"),
                "query_timestamp": item.get("query_timestamp"),
                "dataset_version_anchor": item.get("dataset_version_anchor"),
                "http_last_modified": item.get("http_last_modified"),
                "disposition": item.get("disposition"),
                "fields_evaluated": item.get("fields_evaluated"),
                "field_matches": item.get("field_matches"),
                "field_conflicts": item.get("field_conflicts"),
                "original_values": item.get("original_values"),
                "normalized_values": item.get("normalized_values"),
                "rule_applied": item.get("rule_applied"),
                "note": item.get("note"),
                "retrieved_at": item.get("retrieved_at") or generated_at,
                "generation_timestamp": generated_at,
            })
    return rows
