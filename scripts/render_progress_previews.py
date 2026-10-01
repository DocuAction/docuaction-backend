"""Render the three Task 3 progress deliverables from the controlled test
population and write HTML, PDF (WeasyPrint) and per-page PNG previews.

Used by .github/workflows/progress-previews.yml so the REAL engine's output
can be inspected page by page before a template change ships. Everything is
generated inside one database transaction that is rolled back at the end;
nothing is persisted.

    DATABASE_URL=... python scripts/render_progress_previews.py out/
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

SPECS = (
    ("retrospective_weekly", ("2026-09-22", "2026-09-28")),
    ("retro_monthly", ("2026-09-01", "2026-09-30")),
    ("retrospective_final", ("2026-06-02", "2026-09-30")),
)


async def main(out: str) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url
    from app.reports.engine.pdf_engine import pdf_available, render_pdf, unavailable_reason
    from app.reports.generator import generate_report
    from test_contract_progress_reports import ANCHOR, seed_scope

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
        scope = await seed_scope(db, label="SYNTHETIC-PREVIEW", period_anchor=ANCHOR)
        for rt, period in SPECS:
            r = await generate_report(
                db, report_type=rt, persist=False, generated_by="preview@synthetic.invalid",
                query_parameters={"job_id": str(scope["job_id"]), "period_start": period[0],
                                  "period_end": period[1],
                                  "suggested_changes": "USPS standardisation before comparison\nExclusion-list pre-screen at intake",
                                  "implemented_changes": "Second-source rule for B3 determinations"})
            html_path = os.path.join(out, f"{rt}.html")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(r["html"])
            pdf = render_pdf(r["html"], title=rt)
            pdf_path = os.path.join(out, f"{rt}.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(pdf)
            with open(os.path.join(out, f"{rt}.csv"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(r["csv"])
            # pages → PNG (poppler), 60 dpi is enough to judge layout
            subprocess.run(["pdftoppm", "-r", "60", "-png", pdf_path, os.path.join(out, f"{rt}_p")], check=False)
            pages = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True).stdout
            n = next((line.split()[-1] for line in pages.splitlines() if line.startswith("Pages:")), "?")
            ok = r["accessibility"]["automated_checks_passed"]
            print(f"{rt}: {len(pdf):,} bytes, {n} pages, a11y={ok}, report_id={r['report_id']}")
            failures += 0 if ok else 1
    finally:
        await db.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "progress-previews")))
