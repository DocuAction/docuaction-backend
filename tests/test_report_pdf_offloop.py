"""PDF rendering never runs on the event loop.

DEV, 2026-09-24: `GET /api/reports/DA-ARC-2026-028/pdf` (a delivery_processing
report whose stored HTML is ~29 MB) called WeasyPrint synchronously inside the
async request handler. The render held the event loop for the whole run,
`/health` stopped answering for roughly eight minutes and the platform recycled
the container mid-render. `_pdf_response` (the /pdf route and the
`format=pdf` branch of /generate) and the generation-time durable-PDF
registration in `finalize_report_renderings` now hand the render to a worker
thread with `asyncio.to_thread`.

These tests replace WeasyPrint with a stub, so they run on hosts without the
native stack. The stub records which thread it ran on and whether an event
loop was running in that thread; both must say "not the loop".
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

SYNTHETIC_PDF = b"%PDF-1.7\n% synthetic test bytes, not a document\n"
HTML = "<html lang='en'><head><title>t</title></head><body><p>x</p></body></html>"


class _Probe:
    """A stand-in for `render_pdf` that records where it was called from."""

    def __init__(self):
        self.calls = []

    def __call__(self, html, *, title=None, variant=None):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        self.calls.append({"thread": threading.get_ident(), "on_loop": on_loop,
                           "title": title, "html": html})
        return SYNTHETIC_PDF

    @property
    def only(self):
        assert len(self.calls) == 1, self.calls
        return self.calls[0]


@pytest.fixture
def stub_engine(monkeypatch):
    """`pdf_available()` True and `render_pdf` replaced by a probe, plus a spy
    on `asyncio.to_thread` so the test can see the hand-off itself."""
    from app.reports.engine import pdf_engine

    probe = _Probe()
    monkeypatch.setattr(pdf_engine, "pdf_available", lambda: True)
    monkeypatch.setattr(pdf_engine, "render_pdf", probe)

    handed_off = []
    real_to_thread = asyncio.to_thread

    async def spy(func, /, *args, **kwargs):
        handed_off.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    return SimpleNamespace(probe=probe, handed_off=handed_off)


def _assert_off_loop(call):
    assert call["thread"] != threading.get_ident(), (
        "render_pdf ran on the request's thread: the event loop was blocked")
    assert call["on_loop"] is False, (
        "render_pdf found a running event loop in its thread")


class TestPdfResponseHelper:
    @pytest.mark.asyncio
    async def test_render_is_awaited_in_a_worker_thread(self, stub_engine):
        from app.reports import routes

        response = await routes._pdf_response(HTML, "DA-ARC-2026-999")

        assert stub_engine.handed_off == [stub_engine.probe]
        call = stub_engine.probe.only
        _assert_off_loop(call)
        assert call["html"] == HTML and call["title"] == "DA-ARC-2026-999"
        assert response.body == SYNTHETIC_PDF

    @pytest.mark.asyncio
    async def test_headers_are_unchanged(self, stub_engine):
        from app.reports import routes

        response = await routes._pdf_response(HTML, 'DA-ARC-2026-999"; evil')

        assert response.media_type == "application/pdf"
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.headers["content-disposition"] == (
            'attachment; filename="DA-ARC-2026-999___evil.pdf"')
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store, private"

    @pytest.mark.asyncio
    async def test_unavailable_engine_is_still_503_without_a_thread(self, monkeypatch):
        from fastapi import HTTPException

        from app.reports import routes
        from app.reports.engine import pdf_engine

        monkeypatch.setattr(pdf_engine, "pdf_available", lambda: False)
        monkeypatch.setattr(pdf_engine, "unavailable_reason", lambda: "no native stack")
        with pytest.raises(HTTPException) as excinfo:
            await routes._pdf_response(HTML, "DA-ARC-2026-999")
        assert excinfo.value.status_code == 503
        assert "no native stack" in excinfo.value.detail

    @pytest.mark.asyncio
    async def test_engine_failure_inside_the_thread_is_503(self, monkeypatch):
        from fastapi import HTTPException

        from app.reports import routes
        from app.reports.engine import pdf_engine

        def broken(html, *, title=None, variant=None):
            raise pdf_engine.PDFEngineUnavailable("libpango missing")

        monkeypatch.setattr(pdf_engine, "pdf_available", lambda: True)
        monkeypatch.setattr(pdf_engine, "render_pdf", broken)
        with pytest.raises(HTTPException) as excinfo:
            await routes._pdf_response(HTML, "DA-ARC-2026-999")
        assert excinfo.value.status_code == 503
        assert "libpango" in excinfo.value.detail


class TestRoutesThatRender:
    """The two callers of `_pdf_response`, driven as functions with their
    database work stubbed, so the thread hand-off is proven end to end."""

    @pytest.mark.asyncio
    async def test_get_report_pdf_renders_off_loop(self, stub_engine, monkeypatch):
        from app.reports import routes

        row = SimpleNamespace(report_id="DA-ARC-2026-998", report_type="delivery_processing",
                              report_html=HTML, report_data={}, period_start=None,
                              period_end=None)
        audited = []

        async def fake_stored(db, report_id, job_id=None):
            return row

        async def fake_audit(db, row_, fmt, user, **extra):
            audited.append(fmt)

        monkeypatch.setattr(routes, "_stored", fake_stored)
        monkeypatch.setattr(routes, "_audit_download", fake_audit)

        response = await routes.get_report_pdf(
            "DA-ARC-2026-998", job_id=None, db=None,
            user=SimpleNamespace(email="qa@synthetic.invalid", id=None))

        _assert_off_loop(stub_engine.probe.only)
        assert stub_engine.probe.only["html"] == HTML
        assert response.media_type == "application/pdf"
        assert response.body == SYNTHETIC_PDF
        assert response.headers["content-disposition"] == (
            'attachment; filename="DA-ARC-2026-998.pdf"')
        assert audited == ["pdf"]

    @pytest.mark.asyncio
    async def test_generate_format_pdf_renders_off_loop(self, stub_engine, monkeypatch):
        from app.reports import generator, routes

        async def fake_generate_report(db, **kwargs):
            return {"report_id": "DA-ARC-2026-997", "html": HTML}

        monkeypatch.setattr(generator, "generate_report", fake_generate_report)

        response = await routes.generate(
            routes.GenerateReportRequest(report_type="verification", format="pdf"),
            db=None, user=SimpleNamespace(email="qa@synthetic.invalid", id=None))

        _assert_off_loop(stub_engine.probe.only)
        assert response.media_type == "application/pdf"
        assert response.headers["content-disposition"] == (
            'attachment; filename="DA-ARC-2026-997.pdf"')


class TestGenerationTimeRegistration:
    """`finalize_report_renderings` renders the durable PDF copy during
    `POST /generate`; the same blocking defect lived there."""

    @pytest.mark.asyncio
    async def test_durable_pdf_render_is_off_loop(self, stub_engine, monkeypatch):
        from app.core.storage import artifact_store
        from app.reports.data import artifact_registry, delivery_report_artifacts

        registered = []

        async def fake_finalize(db, *, content, content_type, **common):
            registered.append((content_type, content))
            return {"content_type": content_type, "rendered_sha256": "x" * 64,
                    "registered": True}

        class _Db:
            async def commit(self):
                pass

            async def rollback(self):
                pass

        monkeypatch.setattr(artifact_registry, "finalize_artifact", fake_finalize)
        monkeypatch.setattr(artifact_store, "get_artifact_store",
                            lambda: SimpleNamespace(backend="local"))
        snapshot = SimpleNamespace(review_cycle_id="cycle", template_version="1.0.0",
                                   b1_b4_rule_version=None, data_payload_hash="h" * 64,
                                   data_classification="DEVELOPMENT_TEST",
                                   rce_source_file_sha256=None)

        out = await delivery_report_artifacts.finalize_report_renderings(
            _Db(), report_id="DA-ARC-2026-996", report_type="delivery_processing",
            html=HTML, csv_text=None, snapshot=snapshot, dataset={"delivery": {}},
            generated_by="qa@synthetic.invalid", include_csv=False,
            html_artifact={"registered": True, "content_type": "text/html"})

        _assert_off_loop(stub_engine.probe.only)
        assert out["pdf_unavailable_reason"] is None
        assert registered == [("application/pdf", SYNTHETIC_PDF)]
        assert out["errors"] == []
