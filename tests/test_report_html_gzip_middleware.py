"""app/core/report_html_gzip.py — the three pre-merge review findings, pinned.

1. q-aware Accept-Encoding: ``gzip;q=0`` is a refusal (Starlette 1.3.1's
   substring check got this wrong app-wide).
2. Off-event-loop compression: the gzip call for this path runs in a worker
   thread (reports are megabytes; the global middleware compressed them on
   the loop).
3. No unprotected emission: no minimum-size hole (tiny bodies still
   compress), non-matching paths untouched, already-encoded responses passed
   through. The route-level 406 for non-gzip clients is tested at the wire in
   test_report_storage_durable.py.

No database, no network — a minimal ASGI app driven directly.
"""
from __future__ import annotations

import gzip

import anyio
import pytest

from app.core.report_html_gzip import RegisteredReportHTMLGzip, gzip_accepted


# ── finding 1: the q-aware parse ─────────────────────────────────────────────

@pytest.mark.parametrize("header,expected", [
    ("gzip", True),
    ("gzip, deflate, br", True),
    ("gzip;q=0", False),                 # the exact Starlette 1.3.1 miss
    ("gzip; q=0", False),
    ("gzip;q=0.0", False),
    ("gzip;q=0.001", True),
    ("deflate, gzip;q=0.5", True),
    ("*", True),                          # wildcard offers gzip
    ("*;q=0", False),
    ("identity", False),                  # identity-only never offers gzip
    ("deflate, br", False),
    ("", False),                          # no header -> no gzip offered
    ("x-gzip", True),                     # historical alias
    ("GZIP", True),                       # case-insensitive
    ("gzip;q=nonsense", False),           # unparseable q fails closed
])
def test_gzip_accepted_is_q_aware(header, expected):
    assert gzip_accepted(header) is expected


# ── a minimal ASGI harness ───────────────────────────────────────────────────

def _app_returning(body: bytes, *, headers=None, chunks=None):
    async def app(scope, receive, send):
        hdrs = [(b"content-type", b"application/octet-stream"),
                (b"content-length", str(len(body)).encode())]
        for k, v in (headers or []):
            hdrs.append((k, v))
        await send({"type": "http.response.start", "status": 200, "headers": hdrs})
        if chunks:
            for i, c in enumerate(chunks):
                await send({"type": "http.response.body", "body": c,
                            "more_body": i < len(chunks) - 1})
        else:
            await send({"type": "http.response.body", "body": body})
    return app


async def _drive(mw, path, accept_encoding, *, method="GET"):
    scope = {"type": "http", "method": method, "path": path,
             "headers": [(b"accept-encoding", accept_encoding.encode())] if accept_encoding is not None else []}
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():  # pragma: no cover - never called for GET
        return {"type": "http.request"}

    await mw(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return dict((k.decode(), v.decode()) for k, v in start["headers"]), body


REPORT_PATH = "/api/reports/DA-ARC-2026-999/html"


# ── finding 3: no minimum-size hole, path scoping, passthroughs ──────────────

@pytest.mark.anyio
async def test_tiny_body_on_the_protected_path_is_still_compressed():
    """GZipMiddleware's minimum_size=500 left small bodies unprotected; the
    integrity guarantee must not be conditional on the report being big."""
    tiny = b"<html>x</html>"  # 14 bytes -- far under the old 500-byte floor
    mw = RegisteredReportHTMLGzip(_app_returning(tiny))
    headers, body = await _drive(mw, REPORT_PATH, "gzip")
    assert headers.get("content-encoding") == "gzip"
    assert gzip.decompress(body) == tiny
    assert int(headers["content-length"]) == len(body)
    assert "Accept-Encoding" in headers.get("vary", "")


@pytest.mark.anyio
async def test_other_paths_are_never_touched():
    big = b"x" * 10_000
    mw = RegisteredReportHTMLGzip(_app_returning(big))
    for path in ("/api/reports/DA-ARC-2026-999/csv", "/api/reports/x/html/extra",
                 "/health", "/api/tefca/arc/review-rules"):
        headers, body = await _drive(mw, path, "gzip")
        assert "content-encoding" not in headers, path
        assert body == big


@pytest.mark.anyio
async def test_gzip_q0_is_not_compressed_by_the_middleware():
    """The middleware itself must honour the refusal (the route's 406 is the
    wire-level guarantee; this pins the middleware never contradicts it)."""
    mw = RegisteredReportHTMLGzip(_app_returning(b"<html>" + b"y" * 1000))
    headers, body = await _drive(mw, REPORT_PATH, "gzip;q=0")
    assert "content-encoding" not in headers
    assert body.startswith(b"<html>")


@pytest.mark.anyio
async def test_already_encoded_response_is_passed_through():
    pre = gzip.compress(b"<html>z</html>")
    mw = RegisteredReportHTMLGzip(
        _app_returning(pre, headers=[(b"content-encoding", b"gzip")]))
    headers, body = await _drive(mw, REPORT_PATH, "gzip")
    assert body == pre  # not double-compressed


@pytest.mark.anyio
async def test_chunked_body_is_buffered_and_compressed_as_one_unit():
    """The real middleware stack re-chunks single-shot responses into
    more_body=True + an empty terminal chunk (verified 2026-10-29; the first
    draft passed chunked traffic through UNCOMPRESSED and CI's wire tests
    caught it). End-of-body is the compression trigger, never chunk count."""
    chunks = [b"<html>", b"part2", b"part3", b""]  # empty terminal chunk, as observed
    mw = RegisteredReportHTMLGzip(_app_returning(b"", chunks=chunks))
    headers, body = await _drive(mw, REPORT_PATH, "gzip")
    assert headers.get("content-encoding") == "gzip"
    assert gzip.decompress(body) == b"".join(chunks)
    assert int(headers["content-length"]) == len(body)


# ── finding 2: compression runs off the event loop ──────────────────────────

@pytest.mark.anyio
async def test_compression_is_offloaded_to_a_worker_thread(monkeypatch):
    import app.core.report_html_gzip as m

    calls = {}
    real = anyio.to_thread.run_sync

    async def spy(fn, *args, **kwargs):
        calls["fn"] = getattr(fn, "__name__", str(fn))
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(m.anyio.to_thread, "run_sync", spy)
    body = b"<html>" + b"a" * 50_000
    mw = RegisteredReportHTMLGzip(_app_returning(body))
    headers, wire = await _drive(mw, REPORT_PATH, "gzip")
    assert headers.get("content-encoding") == "gzip"
    assert gzip.decompress(wire) == body
    assert calls.get("fn") == "compress"
