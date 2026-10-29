"""Path-scoped gzip for the registered-HTML report download (DEF-2026-10-26-06).

WHY PATH-SCOPED, NOT GLOBAL (pre-merge review, 2026-10-29)
──────────────────────────────────────────────────────────
The first candidate applied Starlette's GZipMiddleware to the WHOLE app.
Review found three defects in that shape, all stemming from its breadth:

  1. Starlette 1.3.1 compresses whenever the literal substring "gzip" appears
     in Accept-Encoding — including ``gzip;q=0``, which is an explicit
     refusal. Every route inherited that spec violation.
  2. Its compression is synchronous inside the event loop. Globally, that
     newly blocked the loop for every large response — including CSV exports
     of up to 200,000 rows that were never part of this defect.
  3. The defect this exists for (an Azure App Service platform-level sniffer
     injecting a <script> tag into HTML-looking wire bytes) concerns exactly
     one route: ``/api/reports/{id}/html``. A global middleware still left
     THAT route unprotected whenever the client did not accept gzip or the
     body was under the 500-byte minimum — the integrity guarantee the
     X-Artifact-SHA256 header makes was silently conditional.

So: this middleware compresses ONLY the registered-HTML download path, with
no minimum size, with a q-aware Accept-Encoding parse, and with the gzip
call offloaded to a worker thread (reports are routinely megabytes). The
route itself REFUSES (406) a request that does not accept gzip — serving
those bytes uncompressed would hand the platform sniffer exactly the
"<html"/"<script" text the whole defense exists to deny it, while the
response still carried an integrity header it could no longer honour.

Every other route is exactly as it was before DEF-06: uncompressed,
unchanged, no new event-loop work. In-process callers (every test that
calls the route function directly) never pass through ASGI middleware, so
``response.body`` contracts are untouched — only real wire traffic differs.
"""

from __future__ import annotations

import gzip
import re

import anyio.to_thread
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: The one protected path. Kept in lock-step with app/reports/routes.py's
#: ``@router.get("/{report_id}/html")`` under prefix /api/reports.
REPORT_HTML_PATH_RE = re.compile(r"^/api/reports/[^/]+/html/?$")

#: Compression level 6: measured sweet spot for text; level 9 buys ~1-2%
#: size on HTML for several times the CPU, all of it in our worker thread
#: but still latency the client waits out.
_COMPRESSLEVEL = 6


def gzip_accepted(accept_encoding: str) -> bool:
    """RFC 9110 content-coding negotiation, reduced to the one question this
    module asks: may this response be gzip-coded?

    ``gzip;q=0`` is an explicit refusal and must answer False — the exact
    case Starlette 1.3.1's substring check gets wrong. A wildcard counts
    unless itself refused. No header at all means no gzip was offered.
    """
    gzip_q: float | None = None
    star_q: float | None = None
    for token in (accept_encoding or "").split(","):
        token = token.strip().lower()
        if not token:
            continue
        name, _, params = token.partition(";")
        name = name.strip()
        q = 1.0
        for p in params.split(";"):
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if name in ("gzip", "x-gzip"):
            gzip_q = q
        elif name == "*":
            star_q = q
    if gzip_q is not None:
        return gzip_q > 0
    if star_q is not None:
        return star_q > 0
    return False


class RegisteredReportHTMLGzip:
    """Compress the registered-HTML download's wire bytes, off the event loop.

    The whole body is buffered before compressing — deliberately. The route
    serves an already-in-memory registered artifact, so buffering costs what
    the route already paid, and the body must be compressed as one unit so
    no sniffer-visible text ever goes out ahead of compression. Responses
    can arrive here CHUNKED even though the route returns a plain
    ``Response`` — verified against this app's real middleware stack
    (2026-10-29): an inner layer re-chunks single-shot bodies into
    ``more_body=True`` plus an empty terminal chunk, so end-of-body, not
    message count, is the only correct compression trigger (the first
    draft's "single-shot only" assumption silently passed real wire traffic
    through uncompressed — caught by CI's wire tests, exactly what they are
    for). Responses that already carry a Content-Encoding are passed
    through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not REPORT_HTML_PATH_RE.match(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        if not gzip_accepted(Headers(scope=scope).get("accept-encoding", "")):
            # The route answers 406 for these; nothing for us to encode.
            await self.app(scope, receive, send)
            return

        initial_message: Message = {}
        passthrough = False
        chunks: list[bytes] = []

        async def send_compressed(message: Message) -> None:
            nonlocal initial_message, passthrough
            if message["type"] == "http.response.start":
                headers = Headers(raw=message["headers"])
                if "content-encoding" in headers:
                    passthrough = True
                    await send(message)
                else:
                    initial_message = message
                return
            if message["type"] != "http.response.body" or passthrough:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return  # buffer until end-of-body
            body = b"".join(chunks)
            # Megabyte-scale HTML: compress in a worker thread, never on the
            # event loop. No minimum size — the integrity guarantee is not
            # conditional on the report being big enough.
            compressed = await anyio.to_thread.run_sync(
                gzip.compress, body, _COMPRESSLEVEL)
            headers = MutableHeaders(raw=initial_message["headers"])
            headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(compressed))
            headers.add_vary_header("Accept-Encoding")
            await send(initial_message)
            await send({"type": "http.response.body", "body": compressed})

        await self.app(scope, receive, send_compressed)
