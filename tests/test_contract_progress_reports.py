"""The three Task 3 progress deliverables — D3.1 Weekly, D3.1 Monthly,
D3.2 120-Day Final — generated INSIDE the report engine from a controlled,
delivery-scoped review population (ONC demo implementation, 2026-09-30).

Every figure asserted here is recomputed from the seeded rows, never typed
in: the tests seed N cases with known buckets, states, QHIN edges and decision
events, then check the report says exactly what the rows say.

Database-backed (isolated PostgreSQL, rolled back per test; listed in the CI
no-skip isolation job).
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    SYN, VALID_NPI, artifact_root, rolled_back_db, seed_delivery)

PROGRESS_TYPES = ("retrospective_weekly", "retro_monthly", "retrospective_final")
DEV_MARKING = "DRAFT — FOR CLIENT REVIEW"
SYNTHETIC_NOTE = "Synthetic sample data: the figures in this draft are computed from a synthetic review population"
ANNEX_COLUMNS = [
    "Participant/Subparticipant reference", "Participant type", "QHIN", "Source category",
    "Sample-selection reason", "B1–B4 classification", "Discrepancy summary",
    "Assignment status", "Reviewer status", "QA status", "Recommended action",
    "Evidence reference", "Review date", "Workflow interval (days)"]

#: The controlled population. (bucket, state) per case; the seeder derives the
#: decision events and assignment from the state.
#:   AVAILABLE       no assignment, no determination (Unassigned)
#:   CLAIMED         assigned, no determination (In review)
#:   SUBMITTED_FOR_QA assigned + determination, no QA (Awaiting QA)
#:   RETURNED        assigned + determination + QA RETURN (In review)
#:   APPROVED        assigned + determination + QA APPROVE (Completed)
#:   OPEN            no bucket at all (unclassified, never in B1–B4)
POPULATION = [
    ("B1", "APPROVED", "QHIN-ALPHA"), ("B1", "APPROVED", "QHIN-ALPHA"), ("B1", "SUBMITTED_FOR_QA", "QHIN-BRAVO"),
    ("B1", "CLAIMED", "QHIN-BRAVO"), ("B1", "AVAILABLE", "QHIN-ALPHA"),
    ("B2", "APPROVED", "QHIN-ALPHA"), ("B2", "RETURNED", "QHIN-BRAVO"), ("B2", "AVAILABLE", "QHIN-BRAVO"),
    ("B3", "APPROVED", "QHIN-BRAVO"), ("B3", "SUBMITTED_FOR_QA", "QHIN-ALPHA"),
    ("B4", "APPROVED", "QHIN-ALPHA"),
    (None, "OPEN", "QHIN-BRAVO"),
]
# One RECLASSIFY: the system said B1, the analyst determined B3 and QA approved
# it — the report must count it as B3 (event chain wins).
RECLASSIFIED_INDEX = 8

ANALYST = SimpleNamespace(id=uuid.uuid4(), email="analyst@synthetic.invalid", role="senior_analyst")
QA = SimpleNamespace(id=uuid.uuid4(), email="qa@synthetic.invalid", role="qalead")


async def seed_scope(db, *, label: str, period_anchor: date):
    """A delivery (from the shared fixture) plus QHINs, entities, a drawn
    sample, a review cycle and the controlled case population."""
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    d = await seed_delivery(db, label=label)
    intake_id = d["intake_id"]
    t0 = datetime.combine(period_anchor, datetime.min.time()) - timedelta(days=20)

    qhins = {}
    for name in ("QHIN-ALPHA", "QHIN-BRAVO"):
        q = reg.TefcaRegEntity(id=uuid.uuid4(), name=f"{label} {name}", entity_level="qhin",
                               entity_type="qhin", created_at=t0)
        db.add(q)
        qhins[name] = q
    await db.flush()

    sample = reg.ReviewSample(
        id=uuid.uuid4(), sample_name=f"{label} sample", review_type="weekly",
        population_size=len(POPULATION), sample_size=len(POPULATION) - 1,
        confidence_level=0.95, margin_of_error=0.05, proportion=0.5, use_fpc=True,
        strata_config={"stratify_by": "qhin", "source_intake_id": str(intake_id)},
        strata_distribution={"selected": {}, "sizing": {
            str(qhins["QHIN-ALPHA"].id): {"population_size": 7, "sample_size": 6, "census": False},
            str(qhins["QHIN-BRAVO"].id): {"population_size": 5, "sample_size": 5, "census": True}}},
        status="drawn", drawn_at=t0)
    db.add(sample)
    await db.flush()
    cycle = reg.ReviewCycle(id=uuid.uuid4(), cycle_type="retrospective", cycle_number=1,
                            sample_id=sample.id, status="open")
    db.add(cycle)
    await db.flush()

    stem = uuid.uuid4().hex[:6].upper()
    cases = []
    for i, (bucket, state, qhin) in enumerate(POPULATION, start=1):
        entity = reg.TefcaRegEntity(
            id=uuid.uuid4(), name=f"{label} ENTITY {i:02d}", entity_level="participant",
            entity_type="provider", created_at=t0)
        db.add(entity)
        await db.flush()
        db.add(reg.TefcaEntityRelationship(
            parent_entity_id=qhins[qhin].id, child_entity_id=entity.id,
            relationship_type="managed_by_qhin", effective_date=t0.date(), status="active"))
        # A delivered line and its promoted curated row, so the delivery's
        # QHIN population counts the entity.
        raw = f"9.99.777.{i}|{entity.name}|{VALID_NPI}|Testville"
        src = m.RceSourceRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, line_number=100 + i, raw_line=raw,
            parsed={"id": f"9.99.777.{i}", "name": entity.name, "NPI": VALID_NPI,
                    "address_city": "Testville"},
            record_sha256=uuid.uuid4().hex + uuid.uuid4().hex, source_rce_id=f"9.99.777.{i}",
            npi=VALID_NPI, field_count=4, parse_status="ok", promotion_status="promoted",
            canonical_entity_id=entity.id)
        db.add(src)
        await db.flush()
        db.add(m.RceCuratedRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=src.id,
            record_status="CLEAN", rce_org_oid=f"9.99.777.{i}", npi=VALID_NPI,
            name=entity.name, entity_level="participant", transformation_version="1.0.0",
            canonical_entity_id=entity.id, promoted_at=t0))
        system_bucket = "B1" if i - 1 == RECLASSIFIED_INDEX else bucket
        assigned = state not in ("AVAILABLE", "OPEN")
        assigned_at = t0 + timedelta(days=1, hours=i)
        record = reg.ReviewRecord(
            review_id=f"REV-{stem}-{i:06d}", entity_id=entity.id,
            sample_id=sample.id, classification_bucket=system_bucket,
            classification_rule="R-SYN" if system_bucket else None,
            assigned_to_user_id=ANALYST.id if assigned else None,
            assigned_at=assigned_at if assigned else None,
            verification_results={"source_intake_id": str(intake_id), "sample_id": str(sample.id),
                                  "queue_source": "RCE_SAMPLE_REVIEW",
                                  "issue_codes": ["ADDR-FMT"] if bucket in ("B2", "B3") else []},
            created_at=t0)
        db.add(record)
        await db.flush()
        db.add(reg.TefcaVerification(entity_id=entity.id, review_id=record.review_id,
                                     source="NPPES", verification_status="verified",
                                     data_source_label="NPPES (public)", verified_at=t0))
        if state in ("SUBMITTED_FOR_QA", "RETURNED", "APPROVED"):
            determined_at = period_anchor - timedelta(days=(i % 5) + 1)
            det = reg.ReviewDecisionEvent(
                id=uuid.uuid4(), review_id=record.review_id, sequence_number=1,
                event_type="ANALYST_DETERMINATION", actor_user_id=ANALYST.id,
                actor_email=ANALYST.email, actor_role=ANALYST.role,
                occurred_at=datetime.combine(determined_at, datetime.min.time()) + timedelta(hours=9),
                determination="RECLASSIFY" if i - 1 == RECLASSIFIED_INDEX else "CONFIRM",
                determined_bucket=bucket if i - 1 == RECLASSIFIED_INDEX else None,
                rationale="synthetic determination")
            db.add(det)
            if state in ("RETURNED", "APPROVED"):
                qa_at = det.occurred_at + timedelta(days=2)
                db.add(reg.ReviewDecisionEvent(
                    id=uuid.uuid4(), review_id=record.review_id, sequence_number=2,
                    event_type="QA_REVIEW", actor_user_id=QA.id, actor_email=QA.email,
                    actor_role=QA.role, occurred_at=qa_at,
                    qa_action="APPROVE" if state == "APPROVED" else "RETURN",
                    qa_reason="synthetic QA", rationale="synthetic QA"))
                if state == "APPROVED":
                    record.reportable_at = qa_at
        cases.append(record)
    await db.flush()
    return {**d, "sample_id": sample.id, "cycle_id": cycle.id, "cases": cases,
            "qhins": qhins, "label": label}


DETERMINED = ("SUBMITTED_FOR_QA", "RETURNED", "APPROVED")


def _expected(kind="weekly"):
    """What the seeded population says, computed independently of the service.

    Weekly / final count every classified case to date; the monthly report
    counts the determinations that fall inside its period (the seeded
    determinations all do)."""
    counted = [(b, s, q) for b, s, q in POPULATION
               if b and (kind != "monthly" or s in DETERMINED)]
    by_bucket = {}
    for b in ("B1", "B2", "B3", "B4"):
        g = [(s, q) for bb, s, q in counted if bb == b]
        by_bucket[b] = {
            "total": len(g),
            "unassigned": sum(s == "AVAILABLE" for s, _ in g),
            "assigned": sum(s != "AVAILABLE" for s, _ in g),
            "completed": sum(s == "APPROVED" for s, _ in g),
            "in_review": sum(s in ("CLAIMED", "RETURNED") for s, _ in g),
            "awaiting_qa": sum(s == "SUBMITTED_FOR_QA" for s, _ in g),
        }
    return {"reviewed": len(counted), "buckets": by_bucket,
            "unclassified": sum(1 for b, _, _ in POPULATION if not b),
            "qhin_reviewed": {q: sum(1 for _, _, qq in counted if qq == q) for q in ("QHIN-ALPHA", "QHIN-BRAVO")}}


async def _generate(db, report_type, scope, *, persist=True, generated_by="reviewer@synthetic.invalid",
                    period=None, **params):
    from app.reports.generator import generate_report

    p_start, p_end = period or ("2026-09-01", "2026-09-30")
    return await generate_report(
        db, report_type=report_type, persist=persist, generated_by=generated_by,
        query_parameters={"job_id": str(scope["job_id"]), "period_start": p_start,
                          "period_end": p_end, **params})


def _body(html):
    return re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)


ANCHOR = date(2026, 9, 25)


# ── arithmetic controls, from the seeded rows ────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", PROGRESS_TYPES)
async def test_bucket_assignment_and_workflow_arithmetic_reconcile(rolled_back_db, artifact_root, report_type):
    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} {report_type}", period_anchor=ANCHOR)
    result = await _generate(db, report_type, scope, persist=False)
    p = result["dataset"]["progress"]
    exp = _expected(p["kind"])

    assert p["buckets_total"]["total"] == exp["reviewed"]
    assert sum(b["total"] for b in p["buckets"]) == exp["reviewed"]          # B1+B2+B3+B4 = reviewed
    assert p["unclassified"] == exp["unclassified"]                          # never inside B1–B4
    for row in p["buckets"]:
        e = exp["buckets"][row["bucket"]]
        assert row["total"] == e["total"], row
        assert row["assigned"] + row["unassigned"] == row["total"]
        assert row["completed"] + row["in_review"] + row["awaiting_qa"] == row["assigned"]
        assert (row["assigned"], row["unassigned"], row["completed"], row["in_review"], row["awaiting_qa"]) == \
            (e["assigned"], e["unassigned"], e["completed"], e["in_review"], e["awaiting_qa"]), row
    # the RECLASSIFY case is counted under its determined bucket, not the system one
    assert next(b for b in p["buckets"] if b["bucket"] == "B3")["total"] == exp["buckets"]["B3"]["total"]
    # QHIN subtotals reconcile to the overall total, per bucket and in total
    assert sum(q["reviewed"] for q in p["qhins"]) == exp["reviewed"]
    for q in p["qhins"]:
        assert q["reviewed"] == exp["qhin_reviewed"][q["qhin"].split()[-1]]
    for b in ("B1", "B2", "B3", "B4"):
        assert sum(q[b] for q in p["qhins"]) == exp["buckets"][b]["total"]
    assert p["arithmetic"]["ok"] is True
    # the sample sizes come from the drawn sample, per QHIN, not from the population
    assert p["qhins_total"]["sample"] == 11
    assert p["sampling_note"] is None


@pytest.mark.asyncio
async def test_monthly_period_rows_and_final_trend_reconcile_to_the_total(rolled_back_db, artifact_root):
    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} rollup", period_anchor=ANCHOR)
    monthly = (await _generate(db, "retro_monthly", scope, persist=False))["dataset"]["progress"]
    assert monthly["periods"], "a monthly report reconciles week by week"
    assert monthly["periods_total"]["reviewed"] == monthly["buckets_total"]["total"]
    assert monthly["periods"][-1]["cumulative"] == monthly["buckets_total"]["total"]
    for b in ("B1", "B2", "B3", "B4"):
        assert monthly["periods_total"][b] == next(t["total"] for t in monthly["buckets"] if t["bucket"] == b)

    final = (await _generate(db, "retrospective_final", scope, persist=False))["dataset"]["progress"]
    assert final["periods_heading"].startswith("Trend")
    assert final["periods_total"]["reviewed"] == final["buckets_total"]["total"]
    assert final["kind"] == "final" and final["themes"], "B3/B4 themes are derived from the cases"


@pytest.mark.asyncio
async def test_monthly_counts_only_determinations_inside_the_period(rolled_back_db, artifact_root):
    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} window", period_anchor=ANCHOR)
    inside = (await _generate(db, "retro_monthly", scope, persist=False,
                              period=("2026-09-01", "2026-09-30")))["dataset"]["progress"]
    before = (await _generate(db, "retro_monthly", scope, persist=False,
                              period=("2026-01-01", "2026-01-31")))["dataset"]["progress"]
    determined = sum(1 for b, s, _ in POPULATION if b and s in ("SUBMITTED_FOR_QA", "RETURNED", "APPROVED"))
    assert inside["buckets_total"]["total"] == determined
    assert before["buckets_total"]["total"] == 0
    assert before["arithmetic"]["ok"] is True


# ── nothing hard-coded, nothing invented ─────────────────────────────────────

@pytest.mark.asyncio
async def test_no_figure_is_hard_coded_a_second_population_reports_its_own_numbers(rolled_back_db, artifact_root):
    """Two deliveries, different populations → different, each-correct reports."""
    from app.tefca_registry import models as reg

    db = rolled_back_db
    a = await seed_scope(db, label=f"{SYN} popA", period_anchor=ANCHOR)
    b = await seed_scope(db, label=f"{SYN} popB", period_anchor=ANCHOR)
    # drop three of B's cases so the two populations differ
    for rec in b["cases"][:3]:
        await db.execute(reg.TefcaVerification.__table__.delete().where(
            reg.TefcaVerification.review_id == rec.review_id))
        await db.execute(reg.ReviewDecisionEvent.__table__.delete().where(
            reg.ReviewDecisionEvent.review_id == rec.review_id))
        await db.delete(rec)
    await db.flush()
    pa = (await _generate(db, "retrospective_weekly", a, persist=False))["dataset"]["progress"]
    pb = (await _generate(db, "retrospective_weekly", b, persist=False))["dataset"]["progress"]
    assert pa["counts"]["cases"] == len(POPULATION)
    assert pb["counts"]["cases"] == len(POPULATION) - 3
    assert pa["buckets_total"]["total"] != pb["buckets_total"]["total"]
    for p in (pa, pb):
        assert p["arithmetic"]["ok"]
    for banned in ("24,589", "94,231", "383"):
        html = (await _generate(db, "retrospective_weekly", a, persist=False))["html"]
        assert banned not in _body(html)


@pytest.mark.asyncio
async def test_without_a_drawn_sample_the_sampling_population_is_stated_as_pending(rolled_back_db, artifact_root):
    from app.tefca_registry import models as reg

    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} nosample", period_anchor=ANCHOR)
    # unlink the sample: cases keep their delivery, but no sizing exists
    for rec in scope["cases"]:
        rec.sample_id = None
    await db.execute(reg.ReviewCycle.__table__.delete().where(reg.ReviewCycle.id == scope["cycle_id"]))
    await db.execute(reg.SampleEntity.__table__.delete().where(reg.SampleEntity.sample_id == scope["sample_id"]))
    await db.delete(await db.get(reg.ReviewSample, scope["sample_id"]))
    await db.flush()
    result = await _generate(db, "retrospective_weekly", scope, persist=False)
    p = result["dataset"]["progress"]
    assert p["sampling_note"] == "Sampling population pending COR/RCE confirmation."
    assert "Sampling population pending COR/RCE confirmation." in result["html"]
    assert p["qhins_total"]["sample"] == "—"
    assert p["arithmetic"]["ok"]


@pytest.mark.asyncio
async def test_a_scope_with_no_review_cases_fails_closed_with_a_clear_message(rolled_back_db, artifact_root):
    from app.reports.generator import ReportParameterError

    db = rolled_back_db
    d = await seed_delivery(db, label=f"{SYN} empty")      # the shared fixture's one case has no intake link
    with pytest.raises(ReportParameterError) as exc:
        from app.reports.generator import generate_report

        await generate_report(db, report_type="retrospective_weekly", persist=False,
                              query_parameters={"job_id": str(d["job_id"]),
                                                "period_start": "2026-09-01", "period_end": "2026-09-30"})
    assert exc.value.code == "PROGRESS_SCOPE_EMPTY"
    assert "Nothing was generated" in str(exc.value)


@pytest.mark.asyncio
async def test_a_review_cycle_of_another_delivery_is_refused(rolled_back_db, artifact_root):
    from app.reports.generator import ReportParameterError

    db = rolled_back_db
    a = await seed_scope(db, label=f"{SYN} mixA", period_anchor=ANCHOR)
    b = await seed_scope(db, label=f"{SYN} mixB", period_anchor=ANCHOR)
    with pytest.raises(ReportParameterError) as exc:
        await _generate(db, "retrospective_weekly", a, persist=False, review_cycle_id=str(b["cycle_id"]))
    assert exc.value.code == "REVIEW_CYCLE_DELIVERY_MISMATCH"


# ── the document: template, marking, fields, privacy ─────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("report_type,title,deliverable", [
    ("retrospective_weekly", "Task 3 Weekly Progress Report", "D3.1"),
    ("retro_monthly", "Task 3 Monthly Progress Report", "D3.1"),
    ("retrospective_final", "Task 3 Final Report", "D3.2"),
])
async def test_the_branded_front_page_carries_the_approved_fields_and_the_dev_marking(
        rolled_back_db, artifact_root, report_type, title, deliverable):
    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} front {report_type[:6]}", period_anchor=ANCHOR)
    result = await _generate(db, report_type, scope, persist=False,
                             suggested_changes="Standardise addresses before comparison",
                             implemented_changes="Exclusion-list pre-screen at intake")
    html = result["html"]
    body = _body(html)
    assert "sow_progress_report" in str(__import__("app.reports.generator", fromlist=["TEMPLATES"]).TEMPLATES[report_type])
    # header / footer / contract / deliverable
    assert "Alliance Global Tech" in body
    assert "TEFCA Audit, Review &amp; Compliance" in body
    assert f"Deliverable {deliverable}" in body
    assert "7571MN26F80064" in body
    assert title in body
    assert "Prepared for the COR" in body
    assert 'alt="Alliance Global Tech Inc. logo"' in html, "the official AGT logo is embedded"
    # marking: client-review DRAFT (front page, footer, page margin), never
    # MOCK-UP, never FINAL, none of the development wording, no red banner;
    # a discreet synthetic-data note instead.
    assert body.count(DEV_MARKING) >= 2                     # front-page marking line + footer
    assert html.count(DEV_MARKING) >= 3                     # + the @page running header
    assert "MOCK-UP" not in body and "MOCKUP" not in body
    for banned in ("DEV DEMONSTRATION", "NOT FOR OFFICIAL SUBMISSION", "DEVELOPMENT / TEST DATA",
                   "NOT FOR GOVERNMENT DELIVERY", 'class="dev-banner"'):
        assert banned not in html, banned
    assert SYNTHETIC_NOTE in body
    assert not re.search(r"\bFINAL\b(?! Report)", body)
    # the approved palette and B1–B4 colours are in the stylesheet
    for colour in ("#0A1628", "#002D5E", "#0066B3", "#C8A951", "#107C10", "#E87722", "#D13438"):
        assert colour in html
    # section grammar
    for heading in ("classification, assignment and workflow", "QHIN coverage", "Important findings",
                    "Source categories used", "Reconciliation &amp; source integrity",
                    "Suggested methodology / control-framework", "Package:"):
        assert heading in body, heading
    if report_type == "retrospective_weekly":
        assert "Exclusion-list pre-screen" not in body     # weekly: suggested only
    else:
        assert "Exclusion-list pre-screen" in body
    assert "Standardise addresses" in body
    # generation identity and time on the page
    assert "reviewer@synthetic.invalid" in body
    assert "Generated 20" in body
    assert result["accessibility"]["automated_checks_passed"], result["accessibility"]["errors"]


@pytest.mark.asyncio
async def test_no_pii_npi_names_or_secrets_in_report_annex_or_logs(rolled_back_db, artifact_root, caplog):
    db = rolled_back_db
    label = f"{SYN} privacy"
    scope = await seed_scope(db, label=label, period_anchor=ANCHOR)
    import logging
    with caplog.at_level(logging.DEBUG):
        result = await _generate(db, "retrospective_weekly", scope, persist=True)
    p = result["dataset"]["progress"]
    csv_text = result["csv"]
    front = _body(result["html"]).split("Government categories", 1)[0]   # the executive section
    for text in (csv_text, front, caplog.text):
        assert VALID_NPI not in text, "NPIs are never exposed"
        assert f"{label} ENTITY" not in text, "entity names are never exposed"
        assert "password" not in text.lower() and "bearer " not in text.lower()
        assert "Testville" not in text and "Newtown" not in text
    # the annex identifies cases by review reference only
    rows = list(csv.reader(io.StringIO(csv_text)))
    header = next(r for r in rows if r and r[0] == ANNEX_COLUMNS[0])
    assert header == ANNEX_COLUMNS
    data = [r for r in rows[rows.index(header) + 1:] if r]
    assert len(data) == len(POPULATION)                  # weekly annex: every case, open ones marked
    assert all(r[0].startswith("REV-") for r in data)
    assert sum(r[5].startswith("Unclassified") for r in data) == p["unclassified"]
    assert rows[0][0].startswith(f"# {DEV_MARKING}")
    assert any(r and r[0].startswith(f"# {SYNTHETIC_NOTE}") for r in rows[:4])
    assert any(r[7] == "Unassigned" for r in data) and any(r[9] == "QA approved" for r in data)
    assert p["annex_columns"] == ANNEX_COLUMNS


# ── generation controls: one report, idempotency, audit, downloads, RBAC ─────

async def _audit_count(db, key):
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import ACTION_GENERATED

    return int((await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.action == ACTION_GENERATED,
               AuditLog.details["idempotency_key"].as_string() == key))).scalar() or 0)


@pytest.mark.asyncio
async def test_one_generate_is_one_report_same_key_replays_and_audits_exactly_once(rolled_back_db, artifact_root):
    from app.reports import routes
    from app.tefca_registry import models as reg

    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} once", period_anchor=ANCHOR)
    user = SimpleNamespace(id=None, email="reviewer@synthetic.invalid", role="reviewer")
    key = f"rep-{uuid.uuid4()}"
    request = routes.GenerateReportRequest(
        report_type="retro_monthly", format="json", idempotency_key=key,
        period_start="2026-09-01", period_end="2026-09-30",
        parameters={"job_id": str(scope["job_id"])})
    before = int((await db.execute(select(func.count()).select_from(reg.ReviewReport)
                                   .where(reg.ReviewReport.report_type == "retro_monthly"))).scalar() or 0)
    first = await routes.generate(request, db=db, user=user)
    second = await routes.generate(request, db=db, user=user)
    after = int((await db.execute(select(func.count()).select_from(reg.ReviewReport)
                                  .where(reg.ReviewReport.report_type == "retro_monthly"))).scalar() or 0)
    assert after == before + 1
    assert second["replayed"] is True and second["report_id"] == first["report_id"]
    assert await _audit_count(db, key) == 1
    # the register names the source delivery and the marking
    listing = await routes.list_reports(limit=5, report_type="retro_monthly", db=db, user=user)
    item = next(i for i in listing["items"] if i["report_id"] == first["report_id"])
    assert item["source"]["job_id"] == str(scope["job_id"])
    assert item["source"]["review_cycle_id"] == str(scope["cycle_id"])
    assert item["document_marking"] == DEV_MARKING
    assert item["deliverable"] == "D3.1" and item["kind"] == "Monthly"


@pytest.mark.asyncio
async def test_html_csv_and_pdf_downloads_serve_the_stored_report(rolled_back_db, artifact_root):
    from app.reports import routes

    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} dl", period_anchor=ANCHOR)
    user = SimpleNamespace(id=None, email="reviewer@synthetic.invalid", role="reviewer")
    result = await _generate(db, "retrospective_final", scope, persist=True)
    rid = result["report_id"]
    # the annex was registered as a durable artifact at generation
    kinds = {a.get("content_type") for a in (result["artifacts"] or {}).get("artifacts") or []}
    assert "text/csv" in kinds and "text/html" in kinds

    html = await routes.get_report_html(rid, job_id=None, db=db, user=user)
    assert html.status_code == 200 and DEV_MARKING.encode("utf-8") in html.body
    csv_resp = await routes.get_report_csv(rid, job_id=None, db=db, user=user)
    assert csv_resp.status_code == 200
    assert ",".join(ANNEX_COLUMNS).encode("utf-8") in csv_resp.body
    from fastapi import HTTPException

    from app.reports.engine.pdf_engine import pdf_available
    if pdf_available():
        pdf = await routes.get_report_pdf(rid, job_id=None, db=db, user=user)
        assert pdf.status_code == 200 and pdf.body[:4] == b"%PDF"
        assert "application/pdf" in kinds, "the PDF was registered at generation"
    else:
        with pytest.raises(HTTPException) as exc:     # the engine's absence is stated, never a 500
            await routes.get_report_pdf(rid, job_id=None, db=db, user=user)
        assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_generation_leaves_the_delivery_and_its_decisions_untouched(rolled_back_db, artifact_root):
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    db = rolled_back_db
    scope = await seed_scope(db, label=f"{SYN} readonly", period_anchor=ANCHOR)

    async def fingerprint():
        job = await db.get(RceDeliveryJob, scope["job_id"])
        recs = (await db.execute(select(reg.ReviewRecord.review_id, reg.ReviewRecord.classification_bucket,
                                        reg.ReviewRecord.reclassified_to, reg.ReviewRecord.reportable_at,
                                        reg.ReviewRecord.assigned_to_user_id)
                                 .where(reg.ReviewRecord.sample_id == scope["sample_id"])
                                 .order_by(reg.ReviewRecord.review_id))).all()
        events = int((await db.execute(select(func.count()).select_from(reg.ReviewDecisionEvent))).scalar() or 0)
        return (job.state, job.stage, job.sha256, job.records_received, tuple(recs), events)

    before = await fingerprint()
    for rt in PROGRESS_TYPES:
        await _generate(db, rt, scope, persist=True)
    assert await fingerprint() == before


def test_viewer_cannot_generate_reviewer_can_at_the_route():
    """The route floor is `reviewer` (audited); the policy suite proves the
    dependency, this pins it for the progress types' shared route."""
    import inspect

    from app.reports import routes

    src = inspect.getsource(routes.generate)
    assert 'require_role_audited("reviewer"' in src
    from app.core.security import ROLE_HIERARCHY
    assert ROLE_HIERARCHY["viewer"] < ROLE_HIERARCHY["reviewer"]


def test_the_three_types_are_registered_everywhere_the_engine_looks():
    from app.reports.data.report_snapshot import REPORT_TYPES
    from app.reports.data.sow_report_data import PROGRESS_KINDS, SOW_REPORT_TYPES
    from app.reports.generator import SOW_TYPES, TEMPLATES
    from app.reports.routes import SOW_DELIVERABLES

    for rt in PROGRESS_TYPES:
        assert len(rt) <= 20, "review_reports.report_type is String(20)"
        assert rt in REPORT_TYPES and rt in SOW_TYPES and rt in SOW_REPORT_TYPES
        assert TEMPLATES[rt] == "sow_progress_report.html"
        assert rt in PROGRESS_KINDS
    assert SOW_DELIVERABLES["D3.1M"][0] == "retro_monthly"
