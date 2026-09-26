"""
Bounded PECOS-only retry — DRY-RUN PLANNER ONLY (Fix 6).

This module selects candidate entities for a possible future bounded retry
against the GENUINE CMS PECOS-derived sources — CMS_PPEF_ENROLLMENT and
CMS_REVOCATION — and NEVER the legacy NPPES/PECOS proxy (`source_registry.
LEGACY_PECOS_KEY`), which is excluded by construction.

IT NEVER EXECUTES A RETRY. `plan_pecos_retry()` makes no upstream HTTP call
and no database write. It only reads `tefca_dimension_evidence`, the existing
append-only evidence table (see app.Tefca.models.TEFCADimensionEvidence and
app.Tefca.source_registry.CANONICAL_EVIDENCE_SOURCES), and returns a sanitized
report for a human to act on or discard.

WHY THIS SCOPE
The RCE/dimension-evidence entity population is served from one of several
configured sources (mock fixtures or a database-backed registry — see
app.Tefca.entity_resolution / routes._evidence_population), so there is no
single authoritative "list every entity" query that is honest across every
deployment. What IS a single source of truth, regardless of population
source, is the evidence this system has already recorded. This planner
therefore draws candidates from entities that already have at least one
NPPES identity-evidence row on file (so an NPI is available to validate and
mask) and checks THOSE for missing CMS_PPEF_ENROLLMENT / CMS_REVOCATION
coverage. It does not claim to enumerate the full entity population, and says
so in its own output (`population_scope`).

BOUNDED, NOT A FULL SCAN
The delivery this was investigated against holds 24,589 records. A dry-run
planner has no business doing an unbounded table scan to propose 25
candidates, so the NPPES-evidence lookup is windowed (`SCAN_WINDOW`, most
recent generation first) rather than scanning every row. This is a documented
heuristic, not a claim of exhaustive coverage — `entities_scanned` in the
output says exactly how many rows were examined.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.services.npi_validator import npi_rejection_reason
from app.Tefca.source_registry import LEGACY_PECOS_KEY

logger = logging.getLogger(__name__)

#: Hard ceiling this planner will ever return, regardless of the `limit` a
#: caller passes. Fix 6 specifies "at most 25".
MAX_CANDIDATES = 25

#: The only sources a future retry built from this plan may target. The
#: legacy key is never in this tuple — excluded by construction, not by a
#: runtime filter that could be forgotten later.
TARGET_SOURCES = ("CMS_PPEF_ENROLLMENT", "CMS_REVOCATION")

#: How many recent NPPES evidence rows to examine while looking for
#: `limit` qualifying candidates. Bounds the read; see module docstring.
SCAN_WINDOW = 500

assert LEGACY_PECOS_KEY not in TARGET_SOURCES  # excluded by construction, not by luck


def _mask_npi(npi: Optional[str]) -> Optional[str]:
    """Last 4 digits only. Enough for an operator to cross-check a specific
    candidate against a source system; never enough to identify the provider
    from this report alone."""
    if not npi:
        return None
    digits = str(npi)
    return f"...{digits[-4:]}" if len(digits) >= 4 else "...."


@dataclass(frozen=True)
class RetryCandidate:
    entity_id: str
    npi_masked: Optional[str]
    missing_sources: tuple
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "npi_masked": self.npi_masked,
            "missing_sources": list(self.missing_sources),
            "reason": self.reason,
        }


async def plan_pecos_retry(db, limit: int = MAX_CANDIDATES) -> Dict[str, Any]:
    """Dry run only. Reads `tefca_dimension_evidence`; writes nothing; calls no
    upstream connector. Deterministic for a fixed database state: the NPPES
    scan is ordered by (entity_id, generation_timestamp DESC), so two runs
    against the same data return the same candidates in the same order.

    Returns a sanitized report — see module docstring for what is and is not
    included.
    """
    limit = min(max(int(limit), 0), MAX_CANDIDATES)
    from app.Tefca.models import TEFCADimensionEvidence

    # Most recent NPPES identity row per entity, within the scan window. NPPES
    # is the primary NPI identity authority (D1), so its `original_values.npi`
    # is the right place to read a candidate's NPI from — never a stored
    # CMS_PPEF/CMS_REVOCATION row's own NPI field, which would presuppose the
    # very evidence this planner is checking for absence.
    rows = (await db.execute(
        select(TEFCADimensionEvidence.entity_id, TEFCADimensionEvidence.original_values)
        .where(TEFCADimensionEvidence.source == "NPPES")
        .order_by(TEFCADimensionEvidence.entity_id,
                  TEFCADimensionEvidence.generation_timestamp.desc())
        .limit(SCAN_WINDOW)
    )).all()

    seen_entities: set = set()
    scanned = 0
    excluded_invalid_npi = 0
    excluded_has_evidence = 0
    candidates: List[RetryCandidate] = []

    for entity_id, original_values in rows:
        entity_id = str(entity_id)
        if entity_id in seen_entities:
            continue  # keep only the newest NPPES generation per entity
        seen_entities.add(entity_id)
        scanned += 1
        if len(candidates) >= limit:
            continue  # keep scanning only to report accurate exclusion counts

        npi = (original_values or {}).get("npi")
        if npi_rejection_reason(npi):
            excluded_invalid_npi += 1
            continue

        existing = (await db.execute(
            select(TEFCADimensionEvidence.source)
            .where(TEFCADimensionEvidence.entity_id == entity_id,
                   TEFCADimensionEvidence.source.in_(TARGET_SOURCES))
        )).scalars().all()
        missing = tuple(s for s in TARGET_SOURCES if s not in set(existing))
        if not missing:
            excluded_has_evidence += 1
            continue

        candidates.append(RetryCandidate(
            entity_id=entity_id,
            npi_masked=_mask_npi(npi),
            missing_sources=missing,
            reason=f"no current-generation evidence on file for {', '.join(missing)}",
        ))

    return {
        "dry_run": True,
        "executed_retry": False,
        "upstream_calls_made": 0,
        "database_writes_made": 0,
        "target_sources": list(TARGET_SOURCES),
        "excludes_legacy_pecos_proxy": True,
        "population_scope": (
            "Candidates are drawn from entities with an existing NPPES identity-"
            "evidence row on file (within the most recent "
            f"{SCAN_WINDOW}-row window), not from a full entity-population scan. "
            "See module docstring."
        ),
        "candidates": [c.to_dict() for c in candidates],
        "candidate_count": len(candidates),
        "max_candidates": limit,
        "entities_scanned": scanned,
        "excluded_invalid_npi": excluded_invalid_npi,
        "excluded_already_has_evidence": excluded_has_evidence,
    }
