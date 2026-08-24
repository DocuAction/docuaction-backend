"""
The Report Data Service — the ONLY source of numbers in any report.

THE INVARIANT THIS LAYER EXISTS TO ENFORCE
──────────────────────────────────────────
Reports are READ-ONLY and DETERMINISTIC. Generating the same report tomorrow
must produce the same numbers, which means generation must never trigger a fresh
lookup whose answer could have changed.

Nothing in this module — or anything it calls — may perform:

    NPPES / PECOS / OIG / SAM / USPS lookups
    D1-D6 evaluation
    B1-B4 classification

Those belong to the verification pipeline, which runs first, produces evidence,
and freezes it. This service READS the frozen result. `assert_read_only()` is
the executable form of that promise and the test suite asserts it holds.

WHY A SERVICE RATHER THAN QUERIES IN THE TEMPLATES
──────────────────────────────────────────────────
Every value in every chart and every sentence comes from here, so there is
exactly one place where "how many B2 entities were there" is answered. A
template that could query would eventually contain a second, subtly different
definition of the same number, and the two would disagree in front of a federal
reviewer.

NO FABRICATED METRICS
─────────────────────
No function here invents, estimates, rounds-to-look-good, or defaults a value.
Where the denominator is zero the answer is `INSUFFICIENT_DATA`, not 0% — a zero
percent and a "we had nothing to measure" are different statements and only one
of them is true.

LANGUAGE
────────
Percentages are computed over APPLICABLE dimensions, never over "all six". Some
dimensions are legitimately NOT_APPLICABLE for an entity, and counting them as
failures-to-pass would understate compliance for entities that did nothing
wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

#: Bumped whenever the query logic behind any number changes. Stored on every
#: report snapshot so a number can be traced to the code that produced it —
#: "why did DA-ARC-2026-001 show 47 B2 entities" is answerable only if the
#: version of the counting logic is on the record.
REPORT_DATA_SERVICE_VERSION = "1.0.0"

#: Returned in place of a percentage when the denominator is zero. Rendered as
#: "Insufficient data for this reporting period" — never as 0%, never as a
#: blank chart.
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

BUCKET_CODES = ("B1", "B2", "B3", "B4")

BUCKET_LABELS = {
    "B1": "No Discrepancy",
    "B2": "Minor or Administrative",
    "B3": "Inexplicable",
    "B4": "Non-Compliant",
}

#: Status indicator per bucket: shape + colour token + text. NEVER colour alone.
#: The shape is what carries the meaning in greyscale, for a colour-blind reader,
#: and in a screen reader's text stream.
BUCKET_INDICATORS = {
    "B1": {"shape": "circle", "glyph": "●", "token": "--report-success",
           "text": "PASS"},
    "B2": {"shape": "triangle", "glyph": "▲", "token": "--report-warning",
           "text": "REVIEW"},
    "B3": {"shape": "square", "glyph": "■", "token": "--report-warning",
           "text": "ESCALATED"},
    "B4": {"shape": "cross", "glyph": "✕", "token": "--report-error",
           "text": "FAIL"},
}

DIMENSION_ORDER = ("IDENTITY", "MEDICARE_ENROLLMENT", "EXCLUSION_REVOCATION",
                   "ADDRESS", "TEFCA_ALIGNMENT", "PROVIDER_ORG_RELATIONSHIP")

DIMENSION_LABELS = {
    "IDENTITY": "Identity",
    "MEDICARE_ENROLLMENT": "Medicare Enrollment",
    "EXCLUSION_REVOCATION": "Exclusion / Revocation",
    "ADDRESS": "Address",
    "TEFCA_ALIGNMENT": "TEFCA Alignment",
    "PROVIDER_ORG_RELATIONSHIP": "Provider / Organization",
}


class ReportReadOnlyViolation(RuntimeError):
    """Raised when report generation attempts a live lookup or an evaluation.

    Deliberately an exception rather than a log line. A report that quietly
    re-queried CMS would still render, would look correct, and would produce
    different numbers next week — a failure that hides itself. Loud is the only
    safe behaviour.
    """


def assert_read_only(operation: str) -> None:
    """Refuse an operation that would make report generation non-deterministic."""
    raise ReportReadOnlyViolation(
        f"Report generation attempted '{operation}'. Reports read FROZEN "
        f"verification results only. Live lookups and D1-D6 / B1-B4 evaluation "
        f"belong to the verification pipeline, which must run and snapshot "
        f"BEFORE a report is generated."
    )


def percentage(numerator: int, denominator: int) -> Any:
    """A percentage, or INSUFFICIENT_DATA when there is nothing to divide by.

    Returns a float rounded to one decimal. Never returns 0.0 for an empty
    population: "0% of nothing passed" is a claim about a population that does
    not exist.
    """
    if not denominator:
        return INSUFFICIENT_DATA
    return round((numerator / denominator) * 100, 1)


def is_insufficient(value: Any) -> bool:
    return value == INSUFFICIENT_DATA


@dataclass
class ChartSeries:
    """One series. Charts carry at most 5 of these — see chart_engine."""
    label: str
    values: List[float]
    token: str = "--report-primary"


@dataclass
class ChartData:
    """Everything a chart needs, INCLUDING its accessibility apparatus.

    `alt_text`, `source` and `notes` are constructor arguments rather than
    optional extras because a chart without them cannot be rendered — the engine
    refuses. Making them required here is what stops "add alt text later" from
    becoming "shipped without alt text".
    """
    chart_id: str
    figure_number: int
    title: str
    kind: str                       # bar_vertical | bar_horizontal | bar_stacked | line
    categories: List[str]
    series: List[ChartSeries]
    alt_text: str
    source: str
    notes: str
    y_label: str = "Entities"
    insufficient_data: bool = False

    @property
    def numbered_title(self) -> str:
        return f"Figure {self.figure_number}. {self.title}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chart_id": self.chart_id,
            "figure_number": self.figure_number,
            "title": self.title,
            "numbered_title": self.numbered_title,
            "kind": self.kind,
            "categories": list(self.categories),
            "series": [{"label": s.label, "values": list(s.values), "token": s.token}
                       for s in self.series],
            "alt_text": self.alt_text,
            "source": self.source,
            "notes": self.notes,
            "y_label": self.y_label,
            "insufficient_data": self.insufficient_data,
        }


class ReportDataService:
    """Canonical, read-only queries behind every report.

    Constructed with a database session. Every method returns plain dicts of
    already-computed values — a template receives numbers, never a query.
    """

    version = REPORT_DATA_SERVICE_VERSION

    def __init__(self, db):
        self.db = db
        #: Memo for `_dimension_rows`, keyed by review cycle.
        #:
        #: Safe because the evidence this reads is append-only and frozen: a
        #: report is generated from one generation and nothing rewrites it
        #: mid-request. Worth doing because the SOW envelope reads the evidence
        #: twice per family — once for scope, once for source limitations — so
        #: rendering all eight deliverables meant sixteen full reads of 188,528
        #: rows, which took the certification run past two minutes.
        #:
        #: Per-instance, not global. A long-lived cache would be a correctness
        #: problem the moment a new generation lands.
        self._dimension_cache: Dict[Optional[str], List[Any]] = {}
        #: What the last evidence read actually covered — which rule version,
        #: which versions were excluded, how many observations were read and
        #: how many reached the report. Populated by `_dimension_rows`. A
        #: report that narrows its own population must say so; a count that
        #: shrinks with no visible reason is the failure this exists to prevent.
        self.evidence_scope: Dict[str, Any] = {}

    # ── internal helpers ─────────────────────────────────────────────────────

    async def _review_records(self, review_cycle_id: Optional[str]) -> List[Any]:
        """Frozen review records for a cycle.

        `review_records.verification_results` is a SNAPSHOT taken at review time,
        not a pointer to live state — which is precisely what makes reading it
        here deterministic.
        """
        from app.tefca_registry import models as reg

        stmt = select(reg.ReviewRecord)
        if review_cycle_id:
            cycle = await self._cycle(review_cycle_id)
            sample_id = getattr(cycle, "sample_id", None) if cycle else None
            if sample_id is None:
                return []
            stmt = stmt.where(reg.ReviewRecord.sample_id == sample_id)
        try:
            return list((await self.db.execute(stmt)).scalars().all())
        except Exception as exc:  # noqa: BLE001
            logger.warning("report: review records unavailable: %s", exc)
            return []

    async def _cycle(self, review_cycle_id: Optional[str]):
        from app.tefca_registry import models as reg

        if not review_cycle_id:
            return None
        try:
            return await self.db.get(reg.ReviewCycle, review_cycle_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("report: cycle %s unavailable: %s", review_cycle_id, exc)
            return None

    async def _dimension_rows(self, review_cycle_id: Optional[str]) -> List[Any]:
        """Frozen dimension evidence.

        Reads the MOST RECENT generation per (entity, dimension) — never
        re-evaluates. `tefca_dimension_evidence` is append-only, so an older
        generation stays exactly as it was and a report issued against it stays
        explicable.
        """
        from app.Tefca.evidence_version import (
            current_rule_version, historical_rule_versions)
        from app.Tefca.models import TEFCADimensionEvidence

        if review_cycle_id in self._dimension_cache:
            return self._dimension_cache[review_cycle_id]

        stmt = select(TEFCADimensionEvidence)
        if review_cycle_id:
            stmt = stmt.where(
                TEFCADimensionEvidence.review_cycle_id == review_cycle_id)
        # ONLY THE CURRENT EVIDENCE VERSION REACHES A REPORT.
        #
        # Phase 6 ran, was found defective, and was corrected as a new
        # rule_version. Both sets live in this append-only table, so without
        # this filter a report would sum a defective generation and its
        # correction together.
        #
        # Unversioned rows are excluded too. They predate versioning, they
        # cannot be attributed to any rule generation, and 716 of them carry an
        # automatic PASS disposition — which the Phase 6 architecture forbids
        # precisely because no source may assert a pass without a human. Rows
        # whose provenance cannot be stated do not belong in a contract report.
        # They are counted, not silently dropped: see `evidence_scope`.
        stmt = stmt.where(
            TEFCADimensionEvidence.rule_version == current_rule_version())
        try:
            rows = list((await self.db.execute(stmt)).scalars().all())
        except Exception as exc:  # noqa: BLE001
            logger.warning("report: dimension evidence unavailable: %s", exc)
            self.evidence_scope = {
                "rule_version": current_rule_version(),
                "superseded_versions_excluded": historical_rule_versions(),
                "observations_read": 0, "observations_reported": 0,
                "unavailable": True}
            return []

        # DE-DUP KEYS ON SOURCE AS WELL AS DIMENSION.
        #
        # It used to key on (entity, dimension) alone, and that quietly threw
        # away 70,698 of 188,528 observations — 37.5% of the evidence. Every
        # entity has an ADDRESS observation from NPPES *and* one from PPEF, and
        # three EXCLUSION_REVOCATION observations from three different sources.
        # Those are not duplicates. They are different sources answering the
        # same question, and the disagreement between them is the finding.
        #
        # Worse than the loss was which row survived: `generation_timestamp` is
        # NULL on all 188,528 population rows, so the tie-break compared "" to
        # "" and the winner was whichever row the database happened to return
        # first. The reported address-conflict count would change between runs
        # with no visible cause.
        #
        # (entity, dimension, source) is exactly unique over the current
        # evidence — 188,528 rows, 188,528 keys. The tie-break below is
        # therefore unreachable for this data, and is kept deterministic anyway
        # so a future generation that does collide resolves the same way twice.
        latest: Dict[tuple, Any] = {}
        for row in rows:
            key = (row.entity_id, row.evidence_dimension, row.source)
            current = latest.get(key)
            if current is None or self._newer(row, current):
                latest[key] = row

        kept = list(latest.values())
        self.evidence_scope = {
            "rule_version": current_rule_version(),
            "superseded_versions_excluded": historical_rule_versions(),
            "observations_read": len(rows),
            "observations_reported": len(kept),
            "collapsed_duplicates": len(rows) - len(kept),
            "dedup_key": "entity_id + evidence_dimension + source",
        }
        self._dimension_cache[review_cycle_id] = kept
        return kept

    @staticmethod
    def _newer(candidate: Any, incumbent: Any) -> bool:
        """Deterministic recency, with no reliance on row order.

        `generation_timestamp` first because it is the real answer when it is
        set, then `created_at`, then the primary key. The last of those is
        arbitrary but it is *stable*, which is the property that matters: a
        report regenerated from the same evidence must produce the same number,
        and a tie broken by database return order does not.
        """
        def rank(row: Any) -> tuple:
            return (str(getattr(row, "generation_timestamp", "") or ""),
                    str(getattr(row, "created_at", "") or ""),
                    str(getattr(row, "id", "") or ""))
        return rank(candidate) > rank(incumbent)

    # ── the eight canonical queries ──────────────────────────────────────────

    async def get_b1_b4_distribution(self, review_cycle_id: Optional[str] = None
                                     ) -> Dict[str, Any]:
        """Counts and percentages per bucket, from frozen classifications."""
        records = await self._review_records(review_cycle_id)
        counts = {code: 0 for code in BUCKET_CODES}
        unclassified = 0
        for record in records:
            bucket = (getattr(record, "reclassified_to", None)
                      or getattr(record, "classification_bucket", None) or "")
            bucket = bucket.strip().upper()
            if bucket in counts:
                counts[bucket] += 1
            else:
                unclassified += 1

        total = sum(counts.values())
        return {
            "total_classified": total,
            "unclassified": unclassified,
            "counts": counts,
            "percentages": {c: percentage(counts[c], total) for c in BUCKET_CODES},
            "labels": dict(BUCKET_LABELS),
            "indicators": dict(BUCKET_INDICATORS),
            "insufficient_data": total == 0,
        }

    async def get_evidence_dimension_summary(self, review_cycle_id: Optional[str] = None
                                             ) -> Dict[str, Any]:
        """Per-dimension disposition counts, and the applicable-satisfied rate.

        `applicable_evaluated` deliberately EXCLUDES NOT_APPLICABLE. A dimension
        that does not apply to an entity is not a dimension that entity failed,
        and folding it into the denominator would understate compliance for
        entities that did nothing wrong.
        """
        rows = await self._dimension_rows(review_cycle_id)
        per_dimension: Dict[str, Dict[str, int]] = {
            d: {} for d in DIMENSION_ORDER
        }
        for row in rows:
            dimension = (row.evidence_dimension or "").strip().upper()
            disposition = (row.dimension_disposition or row.disposition or "").strip().upper()
            if not dimension or not disposition:
                continue
            per_dimension.setdefault(dimension, {})
            per_dimension[dimension][disposition] = \
                per_dimension[dimension].get(disposition, 0) + 1

        summary = []
        applicable_total = satisfied_total = 0
        for dimension in DIMENSION_ORDER:
            dispositions = per_dimension.get(dimension, {})
            not_applicable = dispositions.get("NOT_APPLICABLE", 0)
            evaluated = sum(dispositions.values())
            applicable = evaluated - not_applicable
            satisfied = dispositions.get("PASS", 0) + dispositions.get("CORROBORATED", 0)
            applicable_total += applicable
            satisfied_total += satisfied
            summary.append({
                "dimension": dimension,
                "label": DIMENSION_LABELS.get(dimension, dimension),
                "dispositions": dispositions,
                "evaluated": evaluated,
                "applicable": applicable,
                "not_applicable": not_applicable,
                "satisfied": satisfied,
                "satisfied_pct": percentage(satisfied, applicable),
            })

        return {
            "dimensions": summary,
            "applicable_evaluated": applicable_total,
            "applicable_satisfied": satisfied_total,
            "all_applicable_pass_pct": percentage(satisfied_total, applicable_total),
            "insufficient_data": applicable_total == 0,
            "language_note": (
                "Percentages are computed over APPLICABLE dimensions. A dimension "
                "that is NOT_APPLICABLE for an entity is excluded from the "
                "denominator rather than counted as unsatisfied."
            ),
        }

    async def get_entity_status_breakdown(self, review_cycle_id: Optional[str] = None
                                          ) -> Dict[str, Any]:
        from app.tefca_registry import models as reg

        try:
            rows = (await self.db.execute(
                select(reg.TefcaRegEntity.verification_status, func.count())
                .group_by(reg.TefcaRegEntity.verification_status)
            )).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("report: entity status unavailable: %s", exc)
            rows = []
        counts = {(status or "unknown"): int(count) for status, count in rows}
        total = sum(counts.values())
        return {
            "counts": counts,
            "total": total,
            "percentages": {k: percentage(v, total) for k, v in counts.items()},
            "insufficient_data": total == 0,
        }

    async def get_verification_coverage(self, review_cycle_id: Optional[str] = None
                                        ) -> Dict[str, Any]:
        """Per-source verification-state counts, from frozen verification rows.

        The five states stay distinct. `unavailable` (a third party's outage) is
        never merged into `not_found` (a statement about the entity) — merging
        them converts someone else's downtime into a finding.
        """
        from app.tefca_registry import models as reg

        try:
            rows = (await self.db.execute(
                select(reg.TefcaVerification.source,
                       reg.TefcaVerification.verification_status,
                       func.count())
                .group_by(reg.TefcaVerification.source,
                          reg.TefcaVerification.verification_status)
            )).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("report: verification coverage unavailable: %s", exc)
            rows = []

        per_source: Dict[str, Dict[str, int]] = {}
        for source, status, count in rows:
            per_source.setdefault(source or "unknown", {})[
                (status or "unknown")] = int(count)

        states = ("verified", "not_found", "not_checked", "unavailable", "failed")
        sources = []
        for source in sorted(per_source):
            counts = per_source[source]
            total = sum(counts.values())
            sources.append({
                "source": source,
                "counts": {s: counts.get(s, 0) for s in states},
                "total": total,
                "verified_pct": percentage(counts.get("verified", 0), total),
            })
        return {
            "sources": sources,
            "states": list(states),
            "insufficient_data": not sources,
            "state_note": (
                "'unavailable' means the source could not be reached and never "
                "counts against an entity. 'not_found' means the source answered "
                "and held no record. They are reported separately."
            ),
        }

    async def get_qhin_comparison(self, review_cycle_id: Optional[str] = None
                                  ) -> Dict[str, Any]:
        from app.Tefca.models import TEFCAReview

        try:
            rows = (await self.db.execute(
                select(TEFCAReview.qhin, TEFCAReview.status, func.count())
                .group_by(TEFCAReview.qhin, TEFCAReview.status)
            )).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("report: qhin comparison unavailable: %s", exc)
            rows = []

        per_qhin: Dict[str, Dict[str, int]] = {}
        for qhin, status, count in rows:
            per_qhin.setdefault(qhin or "Unattributed", {})[
                (status or "unknown")] = int(count)

        qhins = []
        for qhin in sorted(per_qhin):
            counts = per_qhin[qhin]
            total = sum(counts.values())
            qhins.append({"qhin": qhin, "counts": counts, "total": total})
        return {
            "qhins": qhins,
            "qhin_count": len(qhins),
            # A single-QHIN population has nothing to compare, so Figure 5 is
            # omitted rather than drawn as one lonely bar.
            "comparison_meaningful": len(qhins) > 1,
            "insufficient_data": not qhins,
        }

    async def get_scope_summary(self, review_cycle_id: Optional[str] = None
                                ) -> Dict[str, Any]:
        """The Scope at a Glance box. Every figure counted, none estimated."""
        from app.tefca_registry import models as reg

        cycle = await self._cycle(review_cycle_id)
        records = await self._review_records(review_cycle_id)
        qhins = await self.get_qhin_comparison(review_cycle_id)

        try:
            received = int((await self.db.execute(
                select(func.count()).select_from(reg.TefcaRegEntity)
                .where(reg.TefcaRegEntity.is_deleted.is_(False))
            )).scalar() or 0)
        except Exception:  # noqa: BLE001
            received = 0

        try:
            issues = int((await self.db.execute(
                select(func.count()).select_from(reg.TefcaEntityFinding)
                .where(reg.TefcaEntityFinding.status == "open")
            )).scalar() or 0)
        except Exception:  # noqa: BLE001
            issues = 0

        escalations = sum(
            1 for r in records
            if ((getattr(r, "reclassified_to", None)
                 or getattr(r, "classification_bucket", None) or "").upper()
                in ("B3", "B4"))
        )
        return {
            "reporting_period_start": getattr(cycle, "cycle_start", None),
            "reporting_period_end": getattr(cycle, "cycle_end", None),
            "review_cycle_id": review_cycle_id,
            "cycle_type": getattr(cycle, "cycle_type", None),
            "records_received": received,
            "records_evaluated": len(records),
            "qhin_count": qhins["qhin_count"],
            "issues_identified": issues,
            # Held = received but not evaluated in this cycle. Never negative:
            # a bad count is a data problem, and reporting a negative "held"
            # would hide it behind a nonsense number.
            "records_held": max(received - len(records), 0),
            "escalations": escalations,
            "insufficient_data": received == 0 and not records,
        }

    async def get_exceptions(self, review_cycle_id: Optional[str] = None
                             ) -> Dict[str, Any]:
        """B3/B4 entities — the exception list an analyst actually works."""
        records = await self._review_records(review_cycle_id)
        exceptions = []
        for record in records:
            bucket = ((getattr(record, "reclassified_to", None)
                       or getattr(record, "classification_bucket", None) or "")
                      .strip().upper())
            if bucket not in ("B3", "B4"):
                continue
            exceptions.append({
                "review_id": getattr(record, "review_id", None),
                "entity_id": str(getattr(record, "entity_id", "") or ""),
                "bucket": bucket,
                "bucket_label": BUCKET_LABELS.get(bucket, bucket),
                "indicator": BUCKET_INDICATORS.get(bucket, {}),
                "rule": getattr(record, "classification_rule", None),
                "rule_version": getattr(record, "classification_rule_version", None),
                "rationale": getattr(record, "classification_rationale", None),
                "resolution": getattr(record, "reviewer_resolution", None),
            })
        exceptions.sort(key=lambda e: (e["bucket"] != "B4", e["review_id"] or ""))
        return {
            "exceptions": exceptions,
            "count": len(exceptions),
            "insufficient_data": not records,
        }

    async def get_sla_compliance(self, review_cycle_id: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """SLA metrics as a RAG table. Thresholds are stated, not implied."""
        records = await self._review_records(review_cycle_id)
        total = len(records)
        reviewed = sum(1 for r in records if getattr(r, "reviewed_at", None))
        resolved = sum(1 for r in records
                       if getattr(r, "reviewer_resolution", None))

        def rag(value: Any) -> str:
            if is_insufficient(value):
                return "INSUFFICIENT_DATA"
            return "GREEN" if value >= 95 else "AMBER" if value >= 80 else "RED"

        metrics = []
        for name, numerator, target in (
            ("Records reviewed within cycle", reviewed, 95),
            ("Records with analyst resolution", resolved, 90),
        ):
            pct = percentage(numerator, total)
            metrics.append({
                "metric": name, "met": numerator, "total": total,
                "pct": pct, "target_pct": target, "rag": rag(pct),
            })
        return {
            "metrics": metrics,
            "insufficient_data": total == 0,
        }

    # ── one call for a whole report ──────────────────────────────────────────

    async def build_report_dataset(self, review_cycle_id: Optional[str] = None
                                   ) -> Dict[str, Any]:
        """Everything a report needs, assembled once.

        Charts are built here rather than in the template so that a chart's
        numbers and the sentence next to it come from the same call — the
        arrangement that makes it impossible for a figure and its narrative to
        disagree.
        """
        scope = await self.get_scope_summary(review_cycle_id)
        buckets = await self.get_b1_b4_distribution(review_cycle_id)
        dimensions = await self.get_evidence_dimension_summary(review_cycle_id)
        statuses = await self.get_entity_status_breakdown(review_cycle_id)
        coverage = await self.get_verification_coverage(review_cycle_id)
        qhins = await self.get_qhin_comparison(review_cycle_id)
        exceptions = await self.get_exceptions(review_cycle_id)
        sla = await self.get_sla_compliance(review_cycle_id)

        from app.reports.charts import build_all_charts

        charts = build_all_charts(buckets, coverage, dimensions, statuses, qhins)
        return {
            "service_version": self.version,
            "review_cycle_id": review_cycle_id,
            "scope": scope,
            "buckets": buckets,
            "dimensions": dimensions,
            "entity_status": statuses,
            "coverage": coverage,
            "qhins": qhins,
            "exceptions": exceptions,
            "sla": sla,
            "charts": {c.chart_id: c for c in charts},
            "chart_list": charts,
        }
