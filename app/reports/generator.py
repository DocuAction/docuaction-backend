"""
Report generation — the one path from frozen data to a delivered document.

ORDER OF OPERATIONS, AND WHY IT IS THIS ORDER
─────────────────────────────────────────────
    1. Read frozen data through the Report Data Service. Nothing else may query.
    2. Render charts from that data. Same objects, so a figure and the sentence
       beside it cannot disagree.
    3. Render HTML.
    4. Validate accessibility on the rendered HTML — after rendering, because
       the checks inspect the actual output rather than an intention.
    5. Build the provenance snapshot, INCLUDING the accessibility result.
    6. Re-render with the snapshot embedded, so the document carries its own
       provenance and accessibility statement.

Step 6 is the reason HTML is rendered twice. The alternative — a snapshot
computed after the fact and stored beside the document — produces a report whose
printed provenance can drift from the record of it. Rendering it into the
document makes them the same artefact.

The second render cannot change any NUMBER: the dataset is already fixed by step
1, and `verify_reproducible()` in the test suite proves the payload hash is
identical across both renders.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TEMPLATES = {
    "verification": "verification_detail.html",
    "verification_brief": "verification_brief.html",
    "executive": "executive_cor.html",
    "data_quality": "data_quality.html",
    "intake": "source_intake.html",
}

AVAILABLE_TYPES = tuple(TEMPLATES)

#: Report types served by the RCE Report Data Service rather than the ARC one.
#: They read Area 1, the Issue Ledger and Area 2, all frozen — the same
#: read-only, deterministic contract as every other report.
RCE_TYPES = ("data_quality", "intake")


class ReportGenerationError(RuntimeError):
    pass


async def generate_report(
    db,
    *,
    report_type: str = "verification",
    review_cycle_id: Optional[str] = None,
    generated_by: str = "SYSTEM",
    persist: bool = True,
    query_parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate one report. Returns HTML, CSV, the dataset and the snapshot.

    PDF is NOT generated here — it is produced on demand from the stored HTML by
    the `/pdf` endpoint. Generating it eagerly would fail the whole request on a
    host without WeasyPrint's native libraries, taking the HTML and CSV down with
    it for no reason.
    """
    from app.reports.data.report_data_service import ReportDataService
    from app.reports.data.report_snapshot import build_snapshot, next_report_id, store_report
    from app.reports.engine import chart_engine
    from app.reports.engine.accessibility import validate_html
    from app.reports.engine.csv_engine import report_to_csv
    from app.reports.engine.template_engine import TEMPLATE_VERSION, render_html

    if report_type not in TEMPLATES:
        raise ReportGenerationError(
            f"Unknown report type {report_type!r}. Available: "
            f"{list(AVAILABLE_TYPES)}.")

    # 1. Frozen data.
    if report_type in RCE_TYPES:
        from app.reports.data.rce_report_data import RceReportDataService

        rce_service = RceReportDataService(
            db, intake_id=(query_parameters or {}).get("intake_id"))
        dataset = (await rce_service.build_data_quality_dataset()
                   if report_type == "data_quality"
                   else await rce_service.build_source_intake_dataset())
        dataset["review_cycle_id"] = review_cycle_id
    else:
        service = ReportDataService(db)
        dataset = await service.build_report_dataset(review_cycle_id)

    # 2. Charts from that same data.
    chart_images = chart_engine.render_all(dataset["chart_list"])

    context = {key: dataset[key] for key in dataset
               if key not in ("chart_list", "service_version", "review_cycle_id")}
    context["chart_images"] = chart_images

    report_id = await next_report_id(db, report_type)
    template = TEMPLATES[report_type]

    # 3-4. Render, then validate what was actually produced.
    provisional_snapshot = _empty_snapshot(report_id, report_type, dataset,
                                           generated_by, TEMPLATE_VERSION)
    first_pass = render_html(template, {**context, "snapshot": provisional_snapshot})
    accessibility = validate_html(first_pass, chart_engine.TOKENS).to_dict()

    # 5. Provenance, carrying the accessibility result.
    snapshot = await build_snapshot(
        db, report_id=report_id, report_type=report_type, dataset=dataset,
        query_parameters=query_parameters or {"review_cycle_id": review_cycle_id},
        generated_by=generated_by, template_version=TEMPLATE_VERSION,
        accessibility=accessibility,
    )

    # 6. Final render, with the snapshot inside the document.
    html = render_html(template, {**context, "snapshot": snapshot.to_dict()})

    # Re-validate the document that is ACTUALLY delivered.
    #
    # Step 4 validated the first pass, whose result had to exist before the
    # snapshot could carry it. But the first pass is not the artefact anyone
    # receives, and validating only it would leave the delivered document
    # unchecked — including the accessibility statement the second render adds.
    # The two renders differ only in the content of the provenance table, so
    # they should agree; when they do not, the DELIVERED document's result wins
    # and the divergence is logged rather than smoothed over.
    final_accessibility = validate_html(html, chart_engine.TOKENS).to_dict()
    if final_accessibility["automated_checks_passed"] != accessibility["automated_checks_passed"]:
        logger.error(
            "report %s: accessibility verdict changed between the validation "
            "render (%s) and the delivered render (%s). The delivered result is "
            "authoritative. Errors: %s",
            report_id, accessibility["automated_checks_passed"],
            final_accessibility["automated_checks_passed"],
            final_accessibility["errors"])
    accessibility = final_accessibility

    csv_text = report_to_csv(dataset, report_id, snapshot.generation_timestamp)

    stored_id = None
    artifact = None
    if persist:
        stored_id = await store_report(db, snapshot, dataset, html)

        # 8. Register the DELIVERED bytes in the durable artifact registry.
        #
        # store_report() above writes the report into `review_reports`, which is
        # a mutable row in the application database. That answers "what do the
        # numbers say now". It does not answer "what did the document we issued
        # actually say", because a row can be updated and carries no content
        # address.
        #
        # The artifact registry exists for the second question — content-hashed,
        # write-once, versioned, with retention metadata — and finalize_artifact
        # was written for exactly this call site but was never wired to it. The
        # result was a registry that stayed empty while reports generated
        # normally: /api/reports/{id}/html served the document and
        # /api/reports/artifacts/{id} answered 404, so the immutable record that
        # D8 retention depends on did not exist for any report ever issued.
        #
        # Every value below comes from the snapshot that is already inside the
        # rendered document. Nothing is invented here, and nothing is recomputed
        # — a provenance field that disagreed with the one in the document would
        # be worse than none.
        try:
            from app.reports.data.artifact_registry import finalize_artifact

            artifact = await finalize_artifact(
                db,
                report_id=report_id,
                report_type=report_type,
                content=html.encode("utf-8"),
                content_type="text/html",
                review_cycle_id=snapshot.review_cycle_id,
                generated_by=generated_by,
                template_version=snapshot.template_version,
                evidence_rule_version=snapshot.b1_b4_rule_version,
                report_data_hash=snapshot.data_payload_hash,
                source_artifact_sha256=snapshot.rce_source_file_sha256,
                data_classification=snapshot.data_classification,
            )
            # finalize_artifact() flushes but does not commit, and the /generate
            # route does not commit either — store_report() above commits its
            # own write, which is why review_reports persisted while the
            # artifact row was discarded at session close. Commit here, for the
            # same reason and in the same place as store_report does: the write
            # that owns the row owns its durability.
            #
            # finalize_artifact returns a plain dict, not an ORM instance, so
            # nothing here is expired by the commit.
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            # Non-fatal, for the reason store_report gives: the analyst holding
            # the document should not lose it because a secondary write failed.
            # But LOUD, and reported to the caller — a silently missing
            # immutable record is the failure this whole call site was added to
            # correct, and swallowing it here would recreate it in a new place.
            logger.error(
                "report %s: durable artifact registration FAILED (%s: %s). The "
                "report was generated and stored, but no immutable content-"
                "addressed record exists for it.",
                report_id, type(exc).__name__, exc)
            artifact = {"registered": False, "error": f"{type(exc).__name__}: {exc}"}

    if not accessibility["automated_checks_passed"]:
        # Loud, and not fatal. The report is still returned — an analyst can see
        # the defect and the errors are on the record — but it must never be
        # issued in this state, and silence here is how it would be.
        logger.error(
            "report %s FAILED automated accessibility checks: %s",
            report_id, accessibility["errors"])

    return {
        "report_id": report_id,
        "report_type": report_type,
        "stored_id": stored_id,
        "artifact": artifact,
        "html": html,
        "csv": csv_text,
        "dataset": dataset,
        "snapshot": snapshot,
        "accessibility": accessibility,
        "chart_images": chart_images,
    }


def _empty_snapshot(report_id: str, report_type: str, dataset: Dict[str, Any],
                    generated_by: str, template_version: str) -> Dict[str, Any]:
    """A placeholder snapshot for the validation-only first render.

    Never returned to a caller and never stored. It exists so the first render
    has the keys the template needs; the accessibility checks inspect structure,
    which the placeholder does not affect.
    """
    from datetime import datetime, timezone

    return {
        "report_id": report_id,
        "report_type": report_type,
        "generation_timestamp": datetime.now(timezone.utc).isoformat(),
        "review_cycle_id": dataset.get("review_cycle_id"),
        "dataset_snapshot_version": None,
        "evidence_generation": None,
        "b1_b4_rule_version": None,
        "rce_source_file_sha256": None,
        "report_data_service_version": dataset.get("service_version"),
        "template_version": template_version,
        "data_payload_hash": None,
        "generated_by": generated_by,
        "pdf_engine": {"engine": "WeasyPrint", "version": None},
        "accessibility": {},
        # Development unless the real snapshot says otherwise. The first pass
        # must render the SAME structure as the final one — the accessibility
        # validator inspects it — and defaulting the other way would make the
        # validation pass render a document without the classification banner.
        "data_classification": "DEVELOPMENT_TEST",
        "source_provenance": {},
    }
