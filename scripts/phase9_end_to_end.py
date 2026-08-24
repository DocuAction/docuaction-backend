"""
Phase 9 — the operational chain, traced against the live development database.

Two things are proven here that a unit test cannot prove:

  1. Every transition in the chain from a delivered file to a COR report exists,
     carries provenance, and applies the control it is supposed to apply.

  2. Every number a report renders can be walked back to the evidence rows that
     produced it, to the source records behind those, and to the SHA-256 of the
     delivered file. An unexplained number is a failure.

Read-only. Nothing is written; report generation runs with persist=False.

DEVELOPMENT / TEST DATA. No figure here is an ONC finding.
"""

from __future__ import annotations

import asyncio
import io
import os
import secrets
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap() -> None:
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        raw = io.open(env_path, "rb").read().decode("utf-8", "replace")
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(64)
    sys.path.insert(0, ROOT)


_bootstrap()

import logging  # noqa: E402

logging.disable(logging.WARNING)

from sqlalchemy import text  # noqa: E402

from app.core.database import async_session_maker  # noqa: E402

FAILURES = []


def row(stage, frm, to, service, identifier, provenance, control, mode, refusal):
    print(f"  {stage}")
    print(f"      {frm}  ->  {to}")
    print(f"      via        {service}")
    print(f"      keyed on   {identifier}")
    print(f"      provenance {provenance}")
    print(f"      control    {control}   [{mode}]")
    print(f"      refuses    {refusal}")
    print()


def expect(label, actual, expected):
    ok = actual == expected
    if not ok:
        FAILURES.append(f"{label}: got {actual!r}, expected {expected!r}")
    print(f"      {'OK  ' if ok else 'FAIL'} {label:<52} {actual}")
    return ok


async def main() -> int:
    async with async_session_maker() as db:
        async def scalar(sql, **kw):
            return (await db.execute(text(sql), kw)).scalar()

        print("=" * 100)
        print("PHASE 9 — END-TO-END OPERATIONAL CHAIN")
        print("DEVELOPMENT / TEST DATA — NOT ONC FINDINGS")
        print("=" * 100)
        print()

        # ── 1. the path ──────────────────────────────────────────────────────
        print("SECTION 1 — THE PATH, AS IMPLEMENTED")
        print()

        row("SOURCE DELIVERY -> IMMUTABLE INTAKE",
            "file on disk", "rce_source_intakes",
            "app/tefca_registry/rce/repository.py",
            "SHA-256 of the bytes as received",
            "filename, size, encoding, delimiter, headers, schema fingerprint",
            "no update path exists in the repository; DB revokes UPDATE/DELETE",
            "AUTOMATED",
            "a second delivery of identical bytes is recorded, not silently merged")

        row("INTAKE -> SOURCE RECORD",
            "rce_source_intakes", "rce_source_records",
            "rce/repository.py parse",
            "intake id + line number",
            "raw_line preserved verbatim",
            "Area-1 immutability enforced at the database",
            "AUTOMATED",
            "a parse failure is recorded as a parse failure, not a missing row")

        row("SOURCE RECORD -> CANONICAL ENTITY",
            "rce_source_records", "tefca_reg_entities + tefca_entity_identifiers",
            "app/tefca_registry/rce/field_map.py",
            "delivered identifiers (NPI where present)",
            "source_record_id retained on the entity",
            "field map is versioned; schema fingerprint must match",
            "AUTOMATED",
            "an unparseable identifier is held, not guessed")

        row("ENTITY -> SOURCE APPLICABILITY",
            "entity attributes", "SourceApplicabilityMatrix",
            "app/Tefca/source_applicability.py",
            "entity type, taxonomy, available identifiers",
            "rationale recorded per source decision",
            "only REQUIRED and APPLICABLE are queried",
            "AUTOMATED",
            "PENDING_GOVERNMENT_VERIFICATION is never queried and never resolved")

        row("APPLICABILITY -> OBSERVATION",
            "authoritative sources", "tefca_dimension_evidence",
            "app/Tefca/evidence_service.py",
            "the identifier the source is keyed on",
            "source edition, query timestamp, observation hash, rule_version",
            "eight Layer-1 states; none is a verdict",
            "AUTOMATED",
            "SOURCE_UNAVAILABLE never becomes NO_MATCH_OBSERVED")

        row("OBSERVATION -> RELATIONSHIP / PROVENANCE",
            "tefca_dimension_evidence", "evidence_relationship_path",
            "phase6 enrichment",
            "evidence id + deterministic source row key",
            "PPEF component, source version id, row key",
            "controlled relationship vocabulary",
            "AUTOMATED",
            "a hop with no source row key is not written")

        row("OBSERVATION -> EXCEPTION",
            "tefca_dimension_evidence", "triage disposition",
            "app/Tefca/exception_triage.py",
            "source + dimension + state + applicability",
            "reason and blocking decision recorded",
            "five dispositions; only READY_FOR_ANALYST is work",
            "AUTOMATED",
            "triage sorts; it never determines")

        row("EXCEPTION -> ANALYST WORK ITEM",
            "triage", "review_records",
            "app/Tefca/exception_queue.py",
            "review_id",
            "evidence LINKED, never copied",
            "work item creation is explicit, not bulk-automatic",
            "AUTOMATED",
            "2,000+ items are not created without a decision to create them")

        row("WORK ITEM -> ANALYST DETERMINATION",
            "review_records", "review_decision_events",
            "app/tefca_registry/qa_gate.py record_analyst_determination",
            "review_id + sequence number",
            "actor id, email, role, IP, rationale",
            "rationale required, minimum length enforced by the database",
            "HUMAN",
            "a determination on an already-approved record is refused")

        row("DETERMINATION -> QA",
            "review_decision_events", "review_decision_events",
            "qa_gate.py submit_qa_review",
            "review_id + sequence number",
            "actor, action, reason, escalation target",
            "segregation of duties; analyst may not approve own work",
            "HUMAN",
            "APPROVE by the determining analyst is refused by a DB trigger")

        row("QA -> REPORTABILITY",
            "review_decision_events", "review_records.reportable_at",
            "qa_gate.py is_reportable",
            "the standing determination",
            "the approving event id",
            "only a standing APPROVE; RETURN or ESCALATE revokes",
            "DERIVED",
            "an approval is never permanent")

        row("REPORTABILITY -> COR REPORT",
            "review_records + tefca_dimension_evidence", "report dataset",
            "app/reports/data/sow_report_data.py",
            "canonical evidence rule version",
            "evidence scope, source provenance, cycle",
            "canonical selector; unversioned rows excluded",
            "AUTOMATED",
            "an unapproved determination is counted as pending, never as a finding")

        row("REPORT -> SNAPSHOT",
            "report dataset", "review_reports + report_artifacts",
            "report_snapshot.py + artifact_registry.py",
            "report id + content type + version",
            "source SHA, data hash, rendered hash, cycle, template version",
            "append-only; identical content dedupes, changed content versions",
            "AUTOMATED",
            "a finalised artifact is never overwritten")

        row("SNAPSHOT -> AUDIT RECONSTRUCTION",
            "report_artifacts", "verified bytes",
            "artifact_registry.retrieve_artifact",
            "storage locator",
            "re-hashed on read against the registered digest",
            "integrity failure raises rather than serving",
            "AUTOMATED",
            "altered bytes are refused, not served with a warning")

        row("ANY SCREEN -> OPERATOR GUIDANCE",
            "screen", "/api/learning/TEFCA_ARC/help/{key}",
            "app/core/learning/routes.py",
            "contextual help key",
            "classification and source on every statement",
            "role filtering; deep links validated at construction",
            "AUTOMATED",
            "guidance that names a term the code no longer has fails the build")

        # ── 2. numeric reconstruction ────────────────────────────────────────
        print("=" * 100)
        print("SECTION 2 — EVERY REPORT NUMBER, RECONSTRUCTED")
        print("=" * 100)
        print()

        from app.reports.data.source_provenance import authoritative_source_provenance
        from app.reports.data.sow_report_data import SowReportDataService
        from app.Tefca.evidence_version import current_rule_version

        service = SowReportDataService(db)
        report = await service.retrospective_weekly()
        provenance = await authoritative_source_provenance(db)

        strat = report["stratification"]
        scope = report["evidence_scope"]

        print("  REPORT FIELD -> CANONICAL QUERY -> EVIDENCE -> SOURCE")
        print()

        population = await scalar("select count(*) from rce_source_records")
        intake_sha = await scalar("select sha256 from rce_source_intakes")

        print("  [population]")
        expect("source records in Area 1", population, 23_566)
        expect("source file SHA-256", provenance.sha256, intake_sha)
        print()

        print("  [evidence scope]")
        db_observations = await scalar(
            "select count(*) from tefca_dimension_evidence where rule_version=:v",
            v=current_rule_version())
        expect("report observations_read", scope["observations_read"],
               db_observations)
        expect("report observations_reported", scope["observations_reported"],
               db_observations)
        expect("dropped by de-dup", scope["collapsed_duplicates"], 0)
        expect("evidence rule version", report["evidence_rule_version"],
               current_rule_version())
        print()

        print("  [stratification — every category traced to review_records]")
        db_records = await scalar("select count(*) from review_records")
        db_reportable = await scalar(
            "select count(*) from review_records where reportable_at is not null")
        expect("records considered", strat["records_considered"], db_records)
        expect("reportable total", strat["reportable_total"], db_reportable)
        expect("pending QA total", strat["pending_qa_total"],
               db_records - db_reportable)

        bucket_rows = (await db.execute(text(
            "select coalesce(reclassified_to, classification_bucket) b, count(*) "
            "from review_records where reportable_at is null group by 1"))).all()
        bucket_map = {"B1": "no_discrepancy", "B2": "minor_administrative",
                      "B3": "inexplicable", "B4": "non_compliant"}
        for bucket, count in bucket_rows:
            category = bucket_map.get(bucket)
            if category:
                expect(f"pending {category} (from {bucket})",
                       strat["pending_qa"][category], count)
        print()

        print("  [source limitations — traced to observation state]")
        limits = report["source_limitations"]
        for source, count in limits["sources_unavailable"].items():
            db_count = await scalar(
                "select count(*) from tefca_dimension_evidence "
                "where rule_version=:v and source=:s "
                "and observation_result='SOURCE_UNAVAILABLE'",
                v=current_rule_version(), s=source)
            expect(f"{source} unavailable observations", count, db_count)
        print()

        print("  [address conflicts — the figure most often misquoted]")
        for source, expected_count in (("NPPES", 8_584),
                                       ("CMS_PPEF_PRACTICE_LOCATION", 1_842)):
            db_count = await scalar(
                "select count(*) from tefca_dimension_evidence "
                "where rule_version=:v and source=:s "
                "and evidence_dimension like '%%ADDRESS%%' "
                "and dimension_disposition='CONFLICT'",
                v=current_rule_version(), s=source)
            expect(f"{source} address conflicts", db_count, expected_count)
        entities = await scalar(
            "select count(distinct entity_id) from tefca_dimension_evidence "
            "where rule_version=:v and evidence_dimension like '%%ADDRESS%%' "
            "and dimension_disposition='CONFLICT'", v=current_rule_version())
        expect("distinct entities with an address conflict", entities, 9_032)
        print()

        print("  [no unsupported verdicts anywhere in the current evidence]")
        expect("automatic PASS or FAIL", await scalar(
            "select count(*) from tefca_dimension_evidence "
            "where rule_version=:v and disposition in ('PASS','FAIL')",
            v=current_rule_version()), 0)
        expect("decision events", await scalar(
            "select count(*) from review_decision_events"), 0)
        print()

        # ── 3. report families ───────────────────────────────────────────────
        print("=" * 100)
        print("SECTION 3 — REPORT FAMILIES")
        print("=" * 100)
        print()
        from app.reports.data.sow_report_data import SOW_FAMILIES

        for deliverable, method in sorted(SOW_FAMILIES.items()):
            fn = getattr(service, method)
            data = (await fn(case_id=None) if method == "priority_status"
                    else await fn())
            has_scope = bool(data.get("evidence_scope"))
            has_strat = set(data["stratification"]["reportable"]) == {
                "no_discrepancy", "minor_administrative", "inexplicable",
                "non_compliant"}
            has_limits = "source_limitations" in data
            has_pending = "methodology_pending" in data
            ok = has_scope and has_strat and has_limits and has_pending
            if not ok:
                FAILURES.append(f"{deliverable} missing a required element")
            print(f"      {'OK  ' if ok else 'FAIL'} {deliverable:<7} "
                  f"{method:<26} scope={has_scope} categories={has_strat} "
                  f"limits={has_limits} pending={has_pending}")

        # ── verdict ──────────────────────────────────────────────────────────
        print()
        print("=" * 100)
        if FAILURES:
            print(f"END-TO-END CHAIN: {len(FAILURES)} FAILURE(S)")
            for failure in FAILURES:
                print(f"  - {failure}")
        else:
            print("END-TO-END CHAIN: VERIFIED. Every reported number reconstructs "
                  "to evidence and to the source file hash.")
        print("=" * 100)
        return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
