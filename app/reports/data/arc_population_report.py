"""Population figures for ARC reporting, each one carrying its own definition.

THE MISTAKE THIS MODULE IS BUILT TO PREVENT
    "10,426 address conflicts" and "10,426 entities with address problems" are
    different statements, and only the first is true. 10,426 is a count of
    OBSERVATIONS across two sources; the distinct entities behind them number
    9,032, because 1,394 entities disagree with both NPPES and PPEF and were
    counted twice. Every figure below therefore reports `observations` and
    `entities` as separate fields, and `Metric` refuses to exist without a
    denominator and a stated calculation.

EVERY QUERY GOES THROUGH THE CANONICAL SELECTOR
    `app.Tefca.evidence_version.current_filter` decides which evidence version
    is current. This module never writes a `rule_version ==` literal of its own —
    that is how two versions get mixed and a population silently doubles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import distinct, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.Tefca.evidence_version import current_filter, current_rule_version

#: Bump when a metric definition changes, so a printed figure can be traced to
#: the definition in force when it was printed.
REPORT_DATA_VERSION = "1.0.0"

_EV = "tefca_dimension_evidence"


@dataclass(frozen=True)
class Metric:
    """One number, and everything needed to defend it.

    A bare integer in a federal deliverable is unusable: the reader cannot tell
    what it counts, out of what, or how it was derived. All four are mandatory.
    """

    label: str
    observations: int
    entities: int
    denominator: int
    denominator_label: str
    calculation: str
    evidence_version: str = field(default_factory=current_rule_version)

    @property
    def entity_pct(self) -> Optional[float]:
        if not self.denominator:
            return None
        return round(self.entities / self.denominator * 100, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "observations": self.observations,
                "entities": self.entities, "denominator": self.denominator,
                "denominator_label": self.denominator_label,
                "entity_pct": self.entity_pct, "calculation": self.calculation,
                "evidence_version": self.evidence_version,
                "report_data_version": REPORT_DATA_VERSION}


class ArcPopulationReport:
    """Read-only population figures for the current evidence version."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _scalar(self, sql: str, **params) -> int:
        return int((await self.db.execute(
            text(sql), dict(v=current_rule_version(), **params))).scalar() or 0)

    async def denominator(self) -> Dict[str, Any]:
        """The population every percentage in the report divides by.

        Area-1 SOURCE RECORDS, not registry entities. The registry also holds
        seed and QHIN-placeholder rows that were never part of the delivery, and
        dividing by those would understate every rate in the report.
        """
        records = await self._scalar("select count(*) from rce_source_records")
        with_evidence = await self._scalar(
            f"select count(distinct entity_id) from {_EV} where rule_version = :v")
        intake = (await self.db.execute(text(
            "select id, sha256, schema_fingerprint, record_count, original_filename "
            "from rce_source_intakes"))).mappings().first()
        return {
            "delivery_records": records,
            "entities_with_evidence": with_evidence,
            "coverage_complete": records == with_evidence,
            "source_intake_id": str(intake["id"]) if intake else None,
            "source_file_sha256": intake["sha256"] if intake else None,
            "schema_fingerprint": intake["schema_fingerprint"] if intake else None,
            "excluded": ("Registry seed and QHIN-placeholder entities are excluded; "
                         "the denominator is delivered Area-1 source records."),
            "evidence_version": current_rule_version(),
        }

    async def address_comparison(self) -> Dict[str, Any]:
        """Address verdicts per source, plus the de-duplicated entity total.

        `any_conflict_entities` is NOT the sum of the per-source conflicts. An
        entity that disagrees with both sources produces two observations and is
        one entity, and reporting the sum would overstate the affected population
        by exactly the overlap.
        """
        rows = (await self.db.execute(text(
            f"select source, dimension_disposition, count(*) obs, "
            f"count(distinct entity_id) ents from {_EV} "
            f"where rule_version = :v and dimension_disposition is not null "
            f"group by 1, 2"), {"v": current_rule_version()})).mappings().all()
        by_source: Dict[str, Dict[str, Dict[str, int]]] = {}
        for r in rows:
            by_source.setdefault(r["source"], {})[r["dimension_disposition"]] = {
                "observations": r["obs"], "entities": r["ents"]}
        total = await self.denominator()
        n = total["delivery_records"]
        any_conflict = await self._scalar(
            f"select count(distinct entity_id) from {_EV} "
            f"where rule_version = :v and dimension_disposition = 'CONFLICT'")
        conflict_obs = await self._scalar(
            f"select count(*) from {_EV} "
            f"where rule_version = :v and dimension_disposition = 'CONFLICT'")
        both = await self._scalar(
            f"select count(*) from (select entity_id from {_EV} "
            f"where rule_version = :v and dimension_disposition = 'CONFLICT' "
            f"group by entity_id having count(distinct source) > 1) t")
        return {
            "by_source": by_source,
            "conflict": Metric(
                label="Address conflict after normalisation",
                observations=conflict_obs, entities=any_conflict, denominator=n,
                denominator_label="delivered Area-1 source records",
                calculation=(
                    "Observations = one row per (entity, source) whose normalised "
                    "address fields disagree. Entities = distinct entities across "
                    "both sources; the two numbers differ because an entity may "
                    "conflict with more than one source."),
            ).to_dict(),
            "conflict_on_both_sources": both,
            "ppef_scope_note": (
                "The PPEF practice-location extract publishes ENRLMT_ID, city, "
                "state and ZIP and NO street line. PPEF agreement is therefore "
                "city/state/ZIP agreement and is never reported as complete "
                "street-address agreement."),
            "materiality_note": (
                "An observed conflict is not a compliance conclusion. The RCE "
                "delivers a registered address while NPPES and PPEF publish "
                "practice locations; whether a difference between them is "
                "material is PENDING COR DECISION (D4_ADDRESS_MATERIALITY)."),
        }

    async def exceptions(self) -> Dict[str, Any]:
        """Adverse and ambiguous observations, as observations and as entities."""
        rows = (await self.db.execute(text(
            f"select source, observation_result, count(*) obs, "
            f"count(distinct entity_id) ents from {_EV} "
            f"where rule_version = :v and evidence_dimension not like '%ADDRESS%' "
            f"and observation_result in "
            f"('MATCH_OBSERVED','AMBIGUOUS','MULTIPLE_MATCHES','NO_MATCH_OBSERVED') "
            f"and source in ('OIG_LEIE','CMS_REVOCATION','NPPES') "
            f"group by 1, 2 order by 1, 2"), {"v": current_rule_version()})).mappings().all()
        return {"rows": [dict(r) for r in rows],
                "note": ("A match is an observation from an authoritative source. "
                         "It becomes a finding only after analyst determination and "
                         "QA approval.")}

    async def applicability(self) -> Dict[str, int]:
        rows = (await self.db.execute(text(
            f"select dimension_applicability, count(*) from {_EV} "
            f"where rule_version = :v group by 1"), {"v": current_rule_version()})).all()
        return {r[0]: r[1] for r in rows}

    async def qa_status(self) -> Dict[str, Any]:
        """What humans have actually decided. Zero is a legitimate answer."""
        total = await self._scalar("select count(*) from review_records")
        reportable = await self._scalar(
            "select count(*) from review_records where reportable_at is not null")
        events = await self._scalar("select count(*) from review_decision_events")
        return {"review_records": total, "qa_approved_reportable": reportable,
                "decision_events": events,
                "note": ("reportable_at is set only by a QA APPROVE event. Zero "
                         "means no human has resolved any determination — which "
                         "is a true statement about the programme, not a gap in "
                         "the report.")}

    async def source_versions(self) -> List[Dict[str, Any]]:
        rows = (await self.db.execute(text(
            "select source, version_label, source_file_hash, record_count, "
            "retrieved_at, is_point_in_time from source_version_snapshots "
            "order by source"))).mappings().all()
        return [dict(r) for r in rows]

    async def build(self) -> Dict[str, Any]:
        """Everything a population report needs, in one reproducible payload."""
        return {
            "report_data_version": REPORT_DATA_VERSION,
            "evidence_version": current_rule_version(),
            "denominator": await self.denominator(),
            "applicability": await self.applicability(),
            "address": await self.address_comparison(),
            "exceptions": await self.exceptions(),
            "qa": await self.qa_status(),
            "source_versions": await self.source_versions(),
        }
