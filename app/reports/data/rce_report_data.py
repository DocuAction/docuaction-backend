"""
Report Data Service extensions for the RCE pipeline — Reports 2 and 4.

READ-ONLY AND DETERMINISTIC, like the rest of the report layer. These queries
read frozen Area 1 / Issue Ledger / Area 2 state. Nothing here triggers a
quality run, a curation pass, a promotion, or an external lookup: a report that
re-ran the rules would produce different numbers next week and would no longer
describe the delivery it claims to.

Every value is counted. Where a denominator is zero the answer is
INSUFFICIENT_DATA — never 0%, because "no records had issues" and "there were no
records" are different statements.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.reports.data.report_data_service import (
    INSUFFICIENT_DATA,
    ChartData,
    ChartSeries,
    percentage,
)

logger = logging.getLogger(__name__)

RCE_REPORT_DATA_SERVICE_VERSION = "1.0.0"

SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")

#: Severity -> indicator. Shape + text carry the meaning; colour is redundant.
SEVERITY_INDICATORS = {
    "CRITICAL": {"glyph": "✕", "token": "--report-error", "text": "CRITICAL"},
    "HIGH": {"glyph": "■", "token": "--report-error", "text": "HIGH"},
    "MEDIUM": {"glyph": "▲", "token": "--report-warning", "text": "MEDIUM"},
    "LOW": {"glyph": "▲", "token": "--report-warning", "text": "LOW"},
    "INFORMATIONAL": {"glyph": "●", "token": "--report-primary",
                      "text": "INFORMATIONAL"},
}

SOURCE_LINE = (
    "Source: DocuAction RCE ingestion pipeline (Area 1 immutable intake, "
    "versioned data-quality rule set, Issue Ledger). Values are read from "
    "frozen ingestion results; no rule is re-executed during report generation."
)


class RceReportDataService:
    """Canonical read-only queries behind the Data Quality and Source Intake
    reports."""

    version = RCE_REPORT_DATA_SERVICE_VERSION

    def __init__(self, db, intake_id=None):
        self.db = db
        self.intake_id = intake_id

    async def _intake(self):
        from app.tefca_registry.rce import models as m

        if self.intake_id:
            return await self.db.get(m.RceSourceIntake, self.intake_id)
        return (await self.db.execute(
            select(m.RceSourceIntake)
            .order_by(m.RceSourceIntake.received_at.desc()).limit(1)
        )).scalar_one_or_none()

    async def get_intake_summary(self) -> Dict[str, Any]:
        from app.tefca_registry.rce import models as m

        intake = await self._intake()
        if intake is None:
            # EVERY key present, even with no delivery. The templates render
            # under StrictUndefined, so a missing key is a hard render failure
            # rather than a blank — which is the right default, but it means the
            # empty case must return the same SHAPE as the populated one, not a
            # smaller dict.
            return {
                "insufficient_data": True, "intake_id": None,
                "delivery_label": None, "filename": None, "sha256": None,
                "file_size_bytes": 0, "delimiter": None, "delimiter_name": None,
                "encoding": None, "encoding_anomaly": False,
                "line_terminator": None, "field_count": 0, "headers": [],
                "schema_fingerprint": None, "schema_drift": False,
                "record_count": 0, "received_at": None, "received_by": None,
                "status": None, "duplicate_content": False,
                "duplicate_of_intake_id": None, "parse_status_counts": {},
                "mojibake_cells": 0, "embedded_tab_cells": 0,
                "field_map_version": None,
            }
        metadata = intake.source_metadata or {}
        counts = dict((status, int(n)) for status, n in (await self.db.execute(
            select(m.RceSourceRecord.parse_status, func.count())
            .where(m.RceSourceRecord.source_intake_id == intake.id)
            .group_by(m.RceSourceRecord.parse_status))).all())
        return {
            "insufficient_data": False,
            "intake_id": str(intake.id),
            "delivery_label": intake.delivery_label,
            "filename": intake.original_filename,
            "sha256": intake.sha256,
            "file_size_bytes": intake.file_size_bytes,
            "delimiter": intake.delimiter,
            "delimiter_name": {"|": "pipe", ",": "comma",
                               "\t": "tab"}.get(intake.delimiter, intake.delimiter),
            "encoding": intake.encoding,
            "encoding_anomaly": bool(intake.encoding_anomaly),
            "line_terminator": intake.line_terminator,
            "field_count": len(intake.headers or []),
            "headers": list(intake.headers or []),
            "schema_fingerprint": intake.schema_fingerprint,
            "schema_drift": bool(metadata.get("schema_drift")),
            "record_count": intake.record_count,
            "received_at": intake.received_at,
            "received_by": intake.received_by,
            "status": intake.status,
            "duplicate_content": bool(intake.duplicate_content),
            "duplicate_of_intake_id": (str(intake.duplicate_of_intake_id)
                                       if intake.duplicate_of_intake_id else None),
            "parse_status_counts": counts,
            "mojibake_cells": metadata.get("mojibake_cells", 0),
            "embedded_tab_cells": metadata.get("embedded_tab_cells", 0),
            "field_map_version": metadata.get("field_map_version"),
        }

    async def get_issue_summary(self) -> Dict[str, Any]:
        # Figures come from the CURRENT quality run only. A delivery may be
        # quality-run more than once and each run writes a full set of issues,
        # so filtering on the intake alone would report one population twice.
        # See `app.tefca_registry.rce.run_selection`.
        from app.tefca_registry.rce import models as m
        from app.tefca_registry.rce import run_selection

        intake = await self._intake()
        if intake is None:
            return {"insufficient_data": True, "total": 0,
                    "by_severity": {s: 0 for s in SEVERITY_ORDER},
                    "by_rule": {}, "by_type": {}, "by_field": {},
                    "by_authority": {}, "by_resolution": {},
                    "records_affected": 0, "records_total": 0,
                    "records_affected_pct": INSUFFICIENT_DATA,
                    "indicators": dict(SEVERITY_INDICATORS)}

        scope = run_selection.current_issues_filter(intake.id)

        async def grouped(column):
            return dict((k or "(none)", int(v)) for k, v in (await self.db.execute(
                select(column, func.count())
                .where(scope)
                .group_by(column))).all())

        by_severity = await grouped(m.RceIssue.severity)
        by_rule = await grouped(m.RceIssue.rule_id)
        by_field = await grouped(m.RceIssue.field_name)
        by_authority = await grouped(m.RceIssue.correction_authority)
        by_resolution = await grouped(m.RceIssue.resolution)
        by_type = await grouped(m.RceIssue.issue_type)
        total = sum(by_severity.values())
        records = intake.record_count or 0
        affected = int((await self.db.execute(
            select(func.count(func.distinct(m.RceIssue.source_record_id)))
            .where(scope,
                   m.RceIssue.source_record_id.isnot(None)))).scalar() or 0)
        return {
            "insufficient_data": total == 0,
            "total": total,
            "by_severity": {s: by_severity.get(s, 0) for s in SEVERITY_ORDER},
            "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
            "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
            "by_field": dict(sorted(by_field.items(), key=lambda kv: -kv[1])),
            "by_authority": by_authority,
            "by_resolution": by_resolution,
            "records_affected": affected,
            "records_total": records,
            "records_affected_pct": percentage(affected, records),
            "indicators": dict(SEVERITY_INDICATORS),
        }

    async def get_remediation_status(self) -> Dict[str, Any]:
        from app.tefca_registry.rce import models as m
        from app.tefca_registry.rce import run_selection

        intake = await self._intake()
        if intake is None:
            return {"insufficient_data": True, "by_resolution": {}, "open": 0,
                    "resolved": 0, "total_issues": 0, "corrections_applied": 0,
                    "corrections_by_authority": {},
                    "resolved_pct": INSUFFICIENT_DATA}
        by_resolution = dict((k, int(v)) for k, v in (await self.db.execute(
            select(m.RceIssue.resolution, func.count())
            .where(run_selection.current_issues_filter(intake.id))
            .group_by(m.RceIssue.resolution))).all())
        corrections = int((await self.db.execute(
            select(func.count()).select_from(m.RceCorrectionDetail)
            .join(m.RceCuratedRecord,
                  m.RceCuratedRecord.id == m.RceCorrectionDetail.curated_record_id)
            .where(m.RceCuratedRecord.source_intake_id == intake.id))).scalar() or 0)
        by_authority = dict((k, int(v)) for k, v in (await self.db.execute(
            select(m.RceCorrectionDetail.correction_authority, func.count())
            .join(m.RceCuratedRecord,
                  m.RceCuratedRecord.id == m.RceCorrectionDetail.curated_record_id)
            .where(m.RceCuratedRecord.source_intake_id == intake.id)
            .group_by(m.RceCorrectionDetail.correction_authority))).all())
        total = sum(by_resolution.values())
        return {
            "insufficient_data": total == 0,
            "by_resolution": by_resolution,
            "open": by_resolution.get("OPEN", 0),
            "resolved": by_resolution.get("RESOLVED", 0),
            "total_issues": total,
            "corrections_applied": corrections,
            "corrections_by_authority": by_authority,
            "resolved_pct": percentage(by_resolution.get("RESOLVED", 0), total),
        }

    async def get_curation_summary(self) -> Dict[str, Any]:
        from app.tefca_registry.rce import models as m

        intake = await self._intake()
        if intake is None:
            return {"insufficient_data": True, "status_counts": {}, "total": 0,
                    "promoted": 0, "promoted_pct": INSUFFICIENT_DATA}
        status_counts = dict((k, int(v)) for k, v in (await self.db.execute(
            select(m.RceCuratedRecord.record_status, func.count())
            .where(m.RceCuratedRecord.source_intake_id == intake.id)
            .group_by(m.RceCuratedRecord.record_status))).all())
        promoted = int((await self.db.execute(
            select(func.count()).select_from(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_intake_id == intake.id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None)))).scalar() or 0)
        total = sum(status_counts.values())
        return {
            "insufficient_data": total == 0,
            "status_counts": status_counts,
            "total": total,
            "promoted": promoted,
            "promoted_pct": percentage(promoted, total),
        }

    async def get_delta_from_previous(self) -> Dict[str, Any]:
        """Change against the previous delivery, or a stated absence of one.

        Returns `comparable: False` when this is the first delivery. That is
        reported explicitly rather than as zeroes, because "nothing changed" and
        "there is nothing to compare against" are different facts and only one
        of them is true of a first delivery.
        """
        from app.tefca_registry.rce import models as m

        intake = await self._intake()
        if intake is None:
            return {"comparable": False, "reason": "No delivery on record."}
        previous = (await self.db.execute(
            select(m.RceSourceIntake)
            .where(m.RceSourceIntake.received_at < intake.received_at,
                   m.RceSourceIntake.status != "FAILED")
            .order_by(m.RceSourceIntake.received_at.desc()).limit(1)
        )).scalar_one_or_none()
        if previous is None:
            return {
                "comparable": False,
                "reason": ("This is the first delivery on record. There is no "
                           "prior delivery to compare against, which is not the "
                           "same as no change having occurred."),
            }

        current_ids = set((await self.db.execute(
            select(m.RceSourceRecord.source_rce_id)
            .where(m.RceSourceRecord.source_intake_id == intake.id,
                   m.RceSourceRecord.source_rce_id.isnot(None)))).scalars().all())
        previous_ids = set((await self.db.execute(
            select(m.RceSourceRecord.source_rce_id)
            .where(m.RceSourceRecord.source_intake_id == previous.id,
                   m.RceSourceRecord.source_rce_id.isnot(None)))).scalars().all())
        return {
            "comparable": True,
            "previous_intake_id": str(previous.id),
            "previous_filename": previous.original_filename,
            "previous_received_at": previous.received_at,
            "previous_record_count": previous.record_count,
            "current_record_count": intake.record_count,
            "record_delta": (intake.record_count or 0) - (previous.record_count or 0),
            "new_ids": len(current_ids - previous_ids),
            "removed_ids": len(previous_ids - current_ids),
            "common_ids": len(current_ids & previous_ids),
            "identical_bytes": intake.sha256 == previous.sha256,
            "schema_changed": intake.schema_fingerprint != previous.schema_fingerprint,
        }

    async def get_field_coverage(self) -> Dict[str, Any]:
        """Per-field population from the locked map's profiled counts.

        Read from `field_map`, which records what the profiling pass counted, so
        the report cites the same numbers the mapping decisions were made on.
        """
        from app.tefca_registry.rce import field_map as fm

        intake = await self._intake()
        return {
            "insufficient_data": intake is None,
            "field_map_version": fm.FIELD_MAP_VERSION,
            "profiled_file": fm.PROFILED_FILE,
            "profiled_sha256": fm.PROFILED_SHA256,
            "profiled_record_count": fm.PROFILED_RECORD_COUNT,
            "fields": [{
                "name": s.name, "ordinal": s.ordinal, "populated": s.populated,
                "empty": s.empty, "coverage_pct": s.coverage_pct,
                "distinct": s.distinct, "necessity": s.necessity,
                "role": s.role, "target": s.target,
            } for s in fm.FIELD_SPECS],
            "empty_columns": fm.empty_in_delivery(),
        }

    async def build_data_quality_dataset(self) -> Dict[str, Any]:
        issues = await self.get_issue_summary()
        remediation = await self.get_remediation_status()
        curation = await self.get_curation_summary()
        intake = await self.get_intake_summary()
        charts = build_data_quality_charts(issues, remediation, curation)
        return {
            "service_version": self.version,
            "intake": intake, "issues": issues, "remediation": remediation,
            "curation": curation,
            "charts": {c.chart_id: c for c in charts}, "chart_list": charts,
        }

    async def build_source_intake_dataset(self) -> Dict[str, Any]:
        intake = await self.get_intake_summary()
        coverage = await self.get_field_coverage()
        issues = await self.get_issue_summary()
        delta = await self.get_delta_from_previous()
        curation = await self.get_curation_summary()
        charts = build_source_intake_charts(coverage, intake, curation)
        return {
            "service_version": self.version,
            "intake": intake, "coverage": coverage, "issues": issues,
            "delta": delta, "curation": curation,
            "charts": {c.chart_id: c for c in charts}, "chart_list": charts,
        }


# -- charts -------------------------------------------------------------------

def _insufficient(chart_id: str, number: int, title: str, kind: str,
                  y_label: str = "Issues") -> ChartData:
    return ChartData(
        chart_id=chart_id, figure_number=number, title=title, kind=kind,
        categories=[], series=[], y_label=y_label,
        alt_text=(f"{title}: insufficient data for this delivery. No records "
                  f"were available to chart."),
        source=SOURCE_LINE,
        notes=("No data was available, so no values are shown. This is an "
               "absence of measurement, not a measured zero."),
        insufficient_data=True)


def build_data_quality_charts(issues, remediation, curation) -> List[ChartData]:
    charts: List[ChartData] = []

    if issues.get("insufficient_data"):
        charts.append(_insufficient("issues_by_severity", 1,
                                    "Data-Quality Issues by Severity",
                                    "bar_vertical"))
    else:
        severities = [s for s in SEVERITY_ORDER if issues["by_severity"].get(s)]
        values = [issues["by_severity"][s] for s in severities]
        top = severities[values.index(max(values))] if values else "none"
        charts.append(ChartData(
            chart_id="issues_by_severity", figure_number=1,
            title="Data-Quality Issues by Severity", kind="bar_vertical",
            categories=severities,
            series=[ChartSeries("Issues", values, "--report-primary")],
            alt_text=(f"Vertical bar chart of {issues['total']} data-quality "
                      f"issues by severity. "
                      + "; ".join(f"{s}: {issues['by_severity'][s]}"
                                  for s in severities)
                      + f". The largest group is {top}."),
            source=SOURCE_LINE,
            notes=("Severity is set by the versioned rule set. A missing NPI is "
                   "INFORMATIONAL, not a failure: a large share of delivered "
                   "records legitimately carry none, and Medicare applicability "
                   "is decided in D2 rather than here."),
        ))

        top_rules = list(issues["by_rule"].items())[:10]
        charts.append(ChartData(
            chart_id="issues_by_rule", figure_number=2,
            title="Top Data-Quality Rules by Issue Count", kind="bar_horizontal",
            categories=[r for r, _ in top_rules],
            series=[ChartSeries("Issues", [n for _, n in top_rules],
                                "--report-primary")],
            alt_text=("Horizontal bar chart of the rules that fired most often. "
                      + "; ".join(f"{r}: {n}" for r, n in top_rules) + "."),
            source=SOURCE_LINE,
            notes=("Every rule evaluated every record. A rule with no issues "
                   "still executed - the ingestion run's rule-execution history "
                   "distinguishes 'found nothing' from 'did not run'."),
        ))

        authority = issues.get("by_authority") or {}
        order = ["AUTO_SAFE", "HUMAN_REQUIRED", "QA_REQUIRED", "NO_CORRECTION"]
        present = [a for a in order if authority.get(a)]
        if present:
            charts.append(ChartData(
                chart_id="issues_by_authority", figure_number=3,
                title="Issues by Correction Authority", kind="bar_horizontal",
                categories=present,
                series=[ChartSeries("Issues", [authority[a] for a in present],
                                    "--report-primary")],
                alt_text=("Horizontal bar chart of issues by who may act on "
                          "them. "
                          + "; ".join(f"{a}: {authority[a]}" for a in present)
                          + "."),
                source=SOURCE_LINE,
                notes=("Correction authority is independent of confidence. A "
                       "high-confidence identity correction is still "
                       "HUMAN_REQUIRED; AUTO_SAFE covers only deterministic, "
                       "non-substantive normalisation."),
            ))

    if curation.get("insufficient_data"):
        charts.append(_insufficient("curation_status", 4,
                                    "Curated Record Status", "bar_vertical",
                                    "Records"))
    else:
        statuses = [s for s in ("CLEAN", "CORRECTED", "HELD", "REJECTED")
                    if curation["status_counts"].get(s)]
        charts.append(ChartData(
            chart_id="curation_status", figure_number=4,
            title="Curated Record Status", kind="bar_vertical",
            categories=statuses,
            series=[ChartSeries("Records",
                                [curation["status_counts"][s] for s in statuses],
                                "--report-primary")],
            y_label="Records",
            alt_text=(f"Vertical bar chart of {curation['total']} curated "
                      f"records by status. "
                      + "; ".join(f"{s}: {curation['status_counts'][s]}"
                                  for s in statuses) + "."),
            source=SOURCE_LINE,
            notes=("HELD records carry an unresolved issue at holding severity "
                   "and are excluded from verification until it is resolved. "
                   "They are never dropped."),
        ))
    return charts


def build_source_intake_charts(coverage, intake, curation) -> List[ChartData]:
    charts: List[ChartData] = []
    if coverage.get("insufficient_data"):
        charts.append(_insufficient("field_coverage", 1,
                                    "Field Population Across the Delivery",
                                    "bar_horizontal", "Records"))
        return charts

    fields = sorted(coverage["fields"], key=lambda f: -f["populated"])[:15]
    charts.append(ChartData(
        chart_id="field_coverage", figure_number=1,
        title="Field Population Across the Delivery", kind="bar_horizontal",
        categories=[f["name"] for f in fields],
        series=[ChartSeries("Populated", [f["populated"] for f in fields],
                            "--report-success"),
                ChartSeries("Empty", [f["empty"] for f in fields],
                            "--report-muted")],
        y_label="Records",
        alt_text=(f"Horizontal bar chart of how many of "
                  f"{coverage['profiled_record_count']} records populate each "
                  f"field, for the 15 most-populated fields. "
                  + "; ".join(f"{f['name']}: {f['populated']}"
                              for f in fields[:6]) + "."),
        source=SOURCE_LINE,
        notes=(f"{len(coverage['empty_columns'])} columns are present in the "
               f"header and empty on every record: "
               f"{', '.join(coverage['empty_columns'])}. Structurally "
               f"delivered, semantically absent."),
    ))

    parse_counts = intake.get("parse_status_counts") or {}
    if parse_counts:
        keys = list(parse_counts)
        charts.append(ChartData(
            chart_id="parse_status", figure_number=2,
            title="Parse Outcome by Delivered Line", kind="bar_vertical",
            categories=keys,
            series=[ChartSeries("Lines", [parse_counts[k] for k in keys],
                                "--report-primary")],
            y_label="Lines",
            alt_text=("Vertical bar chart of parse outcomes across "
                      f"{sum(parse_counts.values())} delivered lines. "
                      + "; ".join(f"{k}: {v}" for k, v in parse_counts.items())
                      + "."),
            source=SOURCE_LINE,
            notes=("Every delivered line is stored in Area 1 regardless of parse "
                   "outcome. A line that could not be split into the expected "
                   "field count is preserved verbatim rather than dropped."),
        ))
    return charts
