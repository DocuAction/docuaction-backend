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

    SSRF GUARD
    ──────────
    The URL comes from a delivered Government data file, which is
    attacker-influenced input in the general case. This previously dialled it
    with `follow_redirects=True` and no address checking: a delivered value of
    `http://169.254.169.254/` would have been fetched from inside the
    deployment, and a redirect to it would have been followed silently.

    Now every URL — the first one and every redirect hop — is resolved and
    checked against private, loopback, link-local, reserved, multicast and
    carrier-grade-NAT ranges BEFORE a connection is attempted, and the
    connection is PINNED to the address that passed (`website_evidence
    .pinned_target`) so a DNS answer that changes between check and connect
    cannot redirect it. The body is streamed to a byte cap. A refusal is
    reported as UNAVAILABLE, which is the correct existing state for it: the
    site was not read, and that is a fact about the fetch rather than about
    the entity.

    WHAT IS OBSERVED
    ────────────────
    The result now also carries the domain, whether the connection was HTTPS,
    and any organisation name, address, phone and contact detail the page
    published — `address` was previously always None with a note saying
    extraction was not attempted. Every one of those keys ends in `_observed`
    because that is what they are: what the site says, never what is true.

    The `result` vocabulary, the existing keys and the "never affects the
    determination" contract are UNCHANGED. Corroboration is still decided the
    same way, by the organisation name appearing in the page text.
    """
    from app.tefca_registry import website_evidence as web

    raw = _entity_website(entity)
    if not raw:
        return {
            "result": WEBSITE_NOT_FOUND,
            "url": None,
            "unavailable": True,
            "affects_determination": False,
            "note": ("ONC supplied no official website for this entity, and none is "
                     "inferred. Absence of a website never counts against an entity."),
            "checked_at": datetime.utcnow().isoformat(),
            **web.observation_fields(None),
        }

    url = web.normalize_url(raw)
    if url is None:
        return {
            "result": WEBSITE_UNAVAILABLE, "url": raw, "unavailable": True,
            "affects_determination": False,
            "note": ("The delivered website value is not a usable http/https "
                     "URL. Not a finding against the entity."),
            "checked_at": datetime.utcnow().isoformat(),
            **web.observation_fields(None),
        }

    # ── RESOLVE ONCE, PIN, AND DIAL THE ADDRESS THAT PASSED ──────────────────
    # Independent review found the first guard checked the name and then let
    # httpx resolve it AGAIN to connect — the DNS-rebinding window. The target
    # returned here carries the address that passed; the hostname travels as
    # the Host header and as SNI for certificate verification, and the name is
    # never looked up a second time. Every redirect hop goes through the same
    # gate. The body is STREAMED to a byte cap rather than buffered and trimmed.
    target, refusal = await web.pinned_target(url)
    if target is None:
        return {
            "result": WEBSITE_UNAVAILABLE, "url": url, "unavailable": True,
            "affects_determination": False,
            "note": (f"Refused before connecting: {refusal}. A property of the "
                     f"URL, not a finding against the entity."),
            "checked_at": datetime.utcnow().isoformat(),
            **web.observation_fields(url),
        }

    def _unavailable(note, at_url):
        return {
            "result": WEBSITE_UNAVAILABLE, "url": at_url, "unavailable": True,
            "affects_determination": False, "note": note,
            "checked_at": datetime.utcnow().isoformat(),
            **web.observation_fields(at_url),
        }

    user_agent = {"User-Agent": "DocuAction-TEFCA-Review/1.0"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout),
                                     follow_redirects=False) as client:
            current = url
            status = None
            body = b""
            truncated = False
            for _ in range(web.MAX_REDIRECTS + 1):
                async with client.stream(
                    "GET", target.url,
                    headers={**user_agent, **target.headers},
                    extensions=target.extensions,
                ) as resp:
                    status = resp.status_code
                    if status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location") or ""
                        nxt = web.resolve_redirect(current, location)
                        if nxt is None:
                            return _unavailable(
                                "Redirected to a URL that is not usable. Not a "
                                "finding against the entity.", current)
                        target, refusal = await web.pinned_target(nxt)
                        if target is None:
                            return _unavailable(
                                f"Redirect refused: {refusal}. Not a finding "
                                f"against the entity.", current)
                        current = nxt
                        continue
                    if status < 400:
                        body, truncated = await web.read_capped(resp)
                    break
            else:
                return _unavailable(
                    "Too many redirects. An availability fact about the site, "
                    "not evidence about the entity.", current)
            url = current
    except Exception as exc:
        return _unavailable(
            f"Website unreachable ({type(exc).__name__}). Not a finding "
            f"against the entity.", url)

    if status is None:
        return _unavailable("No response. Not a finding against the entity.", url)

    if status in (403, 429) or status >= 500:
        return {
            "result": WEBSITE_UNAVAILABLE, "url": url, "unavailable": True,
            "affects_determination": False, "http_status": status,
            "note": (f"HTTP {status} — blocked, rate-limited or server error. "
                     "An availability fact about the site, not evidence about the entity."),
            "checked_at": datetime.utcnow().isoformat(),
            **web.observation_fields(url),
        }
    if status >= 400:
        return {
            "result": WEBSITE_NOT_FOUND, "url": url, "unavailable": True,
            "affects_determination": False, "http_status": status,
            "note": f"HTTP {status}. Not a finding against the entity.",
            "checked_at": datetime.utcnow().isoformat(),
            **web.observation_fields(url),
        }

    html = web.decode(body, None)
    text = html[:200_000]
    name = (entity.get("name") or "").strip()
    corroborates_name = bool(name) and name.lower() in text.lower()
    return {
        "result": WEBSITE_CORROBORATED if corroborates_name else WEBSITE_INSUFFICIENT,
        "url": url,
        "unavailable": False,
        "affects_determination": False,
        "http_status": status,
        "body_truncated": truncated,
        "pinned_address": target.address,
        "note": ("Organisation name found on the official site."
                 if corroborates_name else
                 "Site reachable but did not clearly corroborate the organisation name. "
                 "Supplemental only — never forced into PASS/FAIL."),
        "checked_at": datetime.utcnow().isoformat(),
        **web.observation_fields(url, html, reachable=True),
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

        ORGANISATION-LEVEL SCREENING WHEN NO NPI EXISTS
        Many TEFCA entities legitimately hold no NPI. Screening them only by NPI
        made `lookup_by_npi("")` return a clean "no exclusion found" for an
        entity nothing had been searched for — a manufactured negative in a
        federal exclusion check. Where no NPI is available, LEIE and SAM are
        additionally queried by organisation name under the `leie_org` and
        `sam_name` keys, and D3 reports which key answered.
        """
        from app.Tefca.connectors import _extract_npi
        from app.Tefca import rce_fields

        npi = rce_fields.rce_npi(entity) or _extract_npi(entity)
        existing, enrollment, revocation = await asyncio.gather(
            self.manager.query_all_sources(entity),
            self.ppef.lookup_by_npi(npi),
            self.revocation.lookup_by_npi(npi),
            return_exceptions=False,
        )
        sources: Dict[str, SourceResult] = dict(existing)
        sources["cms_ppef_enrollment"] = enrollment
        sources["cms_revocation"] = revocation

        org_name = (rce_fields.rce_value(entity, "name")
                    or entity.get("name") or "").strip()
        if not npi and org_name:
            # LEIE screens individuals by (last, first) and organisations by the
            # business name; `last` is positional and must be passed explicitly
            # even when screening an organisation. Omitting it raised a TypeError
            # that `_safe_lookup` swallowed, so the org-level check silently
            # never ran and D3 fell through to INSUFFICIENT_EVIDENCE for every
            # NPI-less entity — a check that appeared wired up and was not.
            leie_org, sam_name = await asyncio.gather(
                self._safe_lookup(self.manager.leie.lookup_by_name,
                                  last="", first="", org=org_name),
                self._safe_lookup(self.manager.sam.lookup_by_name, org_name),
                return_exceptions=False,
            )
            if leie_org is not None:
                sources["leie_org"] = leie_org
            if sam_name is not None:
                sources["sam_name"] = sam_name

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

    @staticmethod
    async def _safe_lookup(fn, *args, **kwargs) -> Optional[SourceResult]:
        """Run a supplementary lookup, returning None if it raises.

        Supplementary screening must never break a review. A None result means
        the key is simply absent from `sources`, which D3 reports as the check
        not having been performed — never as a clean pass.

        A TypeError is logged at ERROR, not info. Every other exception here is
        an outage — a third party being unreachable — but a TypeError means this
        code called the connector wrongly, and the check has therefore never run
        for anyone. That already happened once: the LEIE organisation lookup was
        wired with a missing positional argument and degraded silently for every
        NPI-less entity. An outage is news about the world; a TypeError is a bug,
        and the two must not share a log level.
        """
        try:
            return await fn(*args, **kwargs)
        except TypeError as exc:
            logger.error(
                "org-level lookup called incorrectly (%s): %s. This check did "
                "NOT run and no screening was performed.",
                getattr(fn, "__qualname__", fn), exc, exc_info=True)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.info("org-level lookup unavailable: %s: %s", type(exc).__name__, exc)
            return None

    async def build_evidence(
        self,
        entity: Dict[str, Any],
        sources: Optional[Dict[str, SourceResult]] = None,
        methodology_requires: Optional[Dict[str, str]] = None,
        parent_resolver: Optional[Any] = None,
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

        dimensions = assemble_dimensions(entity, profile, sources, website,
                                         parent_resolver=parent_resolver)
        from app.Tefca import rce_fields

        return {
            "entity_id": entity.get("id"),
            "entity_name": entity.get("name"),
            "generated_at": datetime.utcnow().isoformat(),
            "applicability": profile.to_dict(),
            # Record-level data-quality signals, surfaced next to the evidence
            # rather than buried inside D5. Detected and reported; never
            # auto-corrected.
            "data_quality_flags": rce_fields.quality_flags(entity),
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
