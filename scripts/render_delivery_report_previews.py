"""Render the Delivery Processing Report from the synthetic test delivery and write HTML, PDF
(WeasyPrint) and per-page PNG previews, so the REAL engine's output can be inspected page by page.

Used by .github/workflows/delivery-report-previews.yml. Everything is generated inside one database
transaction that is rolled back at the end; nothing is persisted.

    DATABASE_URL=... python scripts/render_delivery_report_previews.py out/

Two renderings of the same synthetic delivery:
  A  as seeded: no source verification, no QA approval (the state of a freshly processed delivery)
  B  the same delivery plus a source-readiness (preflight) run and a controlled recheck, so the
     "Source readiness and rechecks" section shows real rows
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


async def _add_readiness_and_recheck(db, intake_id):
    from app.tefca_registry.rce import recheck_models as rm
    from app.tefca_registry.rce.preflight_shadow_models import RcePreflightFinding, RcePreflightRun

    run = RcePreflightRun(
        id=uuid.uuid4(), source_intake_id=intake_id, field_map_version="1.0.0", rule_set_version="1.3.0",
        preflight_version="1.0.0", status="COMPLETE", classification_gate="RECORDED_ONLY",
        records_evaluated=5, findings_count=2, normalizations_count=0, summary={},
        actor="preview@synthetic.invalid", correlation_id="preview", build_sha="0" * 40)
    db.add(run)
    await db.flush()
    for seq, (cat, code, disp) in enumerate((("IDENTIFIER", "NPI_FORMAT", "open"),
                                              ("MISSING_CONTEXT", "CTX_BLANK", "informational")), start=1):
        db.add(RcePreflightFinding(
            run_id=run.id, sequence=seq, category=cat, code=code, applicability="applies",
            execution="done", disposition=disp, description=f"synthetic preflight finding {code}"))
    db.add(rm.RceRecheckJob(
        intake_id=intake_id, trigger_kind=rm.TRIGGER_SOURCE_RECOVERY, source_id="nppes", trigger_ref="preview",
        idempotency_key=uuid.uuid4().hex, state=rm.STATE_PENDING_APPROVAL,
        requested_by="analyst@synthetic.invalid", rationale="preview", baseline_hash="0" * 64,
        target_count=3, processed_count=0))
    await db.flush()


async def main(out: str) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url
    from app.reports.engine.pdf_engine import pdf_available, render_pdf, unavailable_reason
    from app.reports.generator import generate_report
    from test_delivery_processing_report import seed_delivery

    if not pdf_available():
        print("PDF engine unavailable:", unavailable_reason())
        return 2
    os.makedirs(out, exist_ok=True)
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    conn = await engine.connect()
    outer = await conn.begin()
    db = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    failures = 0
    try:
        ids = await seed_delivery(db)
        for name, extra in (("A_as_seeded", False), ("B_with_preflight_and_recheck", True)):
            if extra:
                await _add_readiness_and_recheck(db, ids["intake_id"])
            r = await generate_report(db, report_type="delivery_processing", persist=False,
                                      generated_by="preview@synthetic.invalid",
                                      query_parameters={"job_id": str(ids["job_id"])})
            html_path = os.path.join(out, f"{name}.html")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(r["html"])
            pdf = render_pdf(r["html"], title="Delivery Processing Report")
            pdf_path = os.path.join(out, f"{name}.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(pdf)
            with open(os.path.join(out, f"{name}.csv"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(r["csv"])
            subprocess.run(["pdftoppm", "-r", "70", "-png", pdf_path, os.path.join(out, f"{name}_p")], check=False)
            info = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True).stdout
            pages = next((int(line.split()[-1]) for line in info.splitlines() if line.startswith("Pages:")), 0)
            text = subprocess.run(["pdftotext", "-layout", pdf_path, "-"], capture_output=True, text=True).stdout
            with open(os.path.join(out, f"{name}.txt"), "w", encoding="utf-8") as fh:
                fh.write(text)
            # a word broken by a hyphen at a line end inside a table cell is the defect this layout fixes
            hyphen_breaks = re.findall(r"[a-z]{3,}-\n\s*[a-z]{3,}", text)
            ok = r["accessibility"]["automated_checks_passed"]
            print(f"{name}: {len(pdf):,} bytes, {pages} pages, a11y={ok}, "
                  f"hyphen_broken_words={len(hyphen_breaks)}, report_id={r['report_id']}")
            failures += 0 if ok else 1
    finally:
        await db.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "delivery-report-previews")))
