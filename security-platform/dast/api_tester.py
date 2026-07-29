"""Core HTTP test engine.

Owns three things every test module depends on:

  * the production guard, re-checked before EVERY request (not just at construction,
    because a redirect or a mutated attribute could otherwise smuggle one through);
  * rate limiting, so a security test never becomes a denial-of-service against a
    shared dev environment;
  * evidence capture, so each test leaves a record whether it passes or fails.

Uses httpx if available and falls back to urllib, so the engine has no hard
third-party dependency - consistent with the rest of the platform.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from dast.config import (DastConfig, ProductionTargetError, assert_safe_target)
from dast.results import Evidence, EvidenceWriter, Outcome, TestRun

MAX_CAPTURE = 1200


@dataclass
class Response:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    text: str = ""
    elapsed_ms: float = 0.0
    error: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except Exception:
            return None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class RateLimiter:
    """Sliding window. Default 8 requests / 6 s, matching the app's own limiter so a
    scan is never mistaken for an attack and never trips it."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max = max(1, int(max_requests))
        self.window = float(window_seconds)
        self._hits: Deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._hits and now - self._hits[0] > self.window:
                self._hits.popleft()
            if len(self._hits) < self.max:
                self._hits.append(now)
                return
            await asyncio.sleep(max(0.05, self.window - (now - self._hits[0]) + 0.05))


class APISecurityTester:
    """HTTP engine with an unbypassable production guard."""

    def __init__(self, base_url: str, config: Optional[DastConfig] = None,
                 auth_tokens: Optional[Dict[str, str]] = None,
                 run: Optional[TestRun] = None,
                 evidence_root: Optional[Path] = None):
        self.config = config or DastConfig.load()
        # Guard #1: at construction. Raises before any network object exists.
        self.base_url = assert_safe_target(base_url, self.config.never_test).rstrip("/")
        self.auth_tokens: Dict[str, str] = dict(auth_tokens or {})
        self.limiter = RateLimiter(self.config.rate_limit.max_requests_per_window,
                                   self.config.rate_limit.window_seconds)
        self.run = run or TestRun(run_id=TestRun.new_id(), target=self.base_url,
                                  target_kind=self.config.target_kind or "")
        root = evidence_root or (Path(__file__).resolve().parent.parent /
                                 self.config.evidence_dir)
        self.writer = EvidenceWriter(root, self.run.run_id)
        self.request_count = 0
        self._client = None

    # ── request ──────────────────────────────────────────────────────────────

    def url_for(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            # Guard #2: an absolute path supplied by a test is re-validated.
            return assert_safe_target(path, self.config.never_test)
        return urljoin(self.base_url + "/", path.lstrip("/"))

    async def request(self, method: str, path: str, *,
                      token: Optional[str] = None,
                      headers: Optional[Dict[str, str]] = None,
                      json_body: Any = None,
                      data: Any = None,
                      files: Any = None,
                      allow_redirects: bool = False) -> Response:
        url = self.url_for(path)
        # Guard #3: immediately before the socket is opened. This is the one that
        # cannot be skipped by any code path.
        assert_safe_target(url, self.config.never_test)

        hdrs = {"User-Agent": "AGT-Security-Platform-DAST/1.0",
                "Accept": "application/json"}
        if headers:
            hdrs.update({k: v for k, v in headers.items() if v is not None})
        if token:
            hdrs["Authorization"] = f"Bearer {token}"

        await self.limiter.acquire()
        self.request_count += 1
        started = time.monotonic()
        try:
            resp = await self._send(method, url, hdrs, json_body, data, files,
                                    allow_redirects)
            resp.elapsed_ms = (time.monotonic() - started) * 1000.0
            return resp
        except Exception as exc:
            return Response(status=0, elapsed_ms=(time.monotonic() - started) * 1000.0,
                            error=f"{type(exc).__name__}: {exc}")

    async def _send(self, method: str, url: str, headers: Dict[str, str],
                    json_body: Any, data: Any, files: Any,
                    allow_redirects: bool) -> Response:
        try:
            import httpx
        except ImportError:
            return await self._send_urllib(method, url, headers, json_body, data)

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                follow_redirects=allow_redirects, verify=True)
        kw: Dict[str, Any] = {"headers": headers}
        if json_body is not None:
            kw["json"] = json_body
        if data is not None:
            kw["content"] = data if isinstance(data, (bytes, str)) else None
            if kw["content"] is None:
                kw.pop("content")
                kw["data"] = data
        if files is not None:
            kw["files"] = files
        r = await self._client.request(method.upper(), url, **kw)
        return Response(status=r.status_code,
                        headers={k.lower(): v for k, v in r.headers.items()},
                        text=r.text[:20000])

    async def _send_urllib(self, method: str, url: str, headers: Dict[str, str],
                           json_body: Any, data: Any) -> Response:
        import urllib.error
        import urllib.request

        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers = {**headers, "Content-Type": "application/json"}
        elif isinstance(data, (bytes, str)):
            body = data.encode("utf-8") if isinstance(data, str) else data

        req = urllib.request.Request(url, data=body, method=method.upper())
        for k, v in headers.items():
            req.add_header(k, v)

        def _do():
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as r:
                    return Response(status=r.status,
                                    headers={k.lower(): v for k, v in r.headers.items()},
                                    text=r.read(20000).decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                return Response(status=e.code,
                                headers={k.lower(): v for k, v in (e.headers or {}).items()},
                                text=(e.read(20000) or b"").decode("utf-8", "replace"))
        return await asyncio.get_event_loop().run_in_executor(None, _do)

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ── evidence ─────────────────────────────────────────────────────────────

    def generate_evidence(self, test_id: str, category: str, test_name: str, *,
                          method: str = "", endpoint: str = "",
                          response: Optional[Response] = None,
                          request_summary: Optional[Dict[str, Any]] = None,
                          expected: str = "", observed: str = "",
                          outcome: Outcome = Outcome.SKIP,
                          finding: str = "", severity: str = "info",
                          confidence: str = "high",
                          owasp: Optional[List[str]] = None,
                          owasp_api: Optional[List[str]] = None,
                          cwe: Optional[List[str]] = None,
                          nist: Optional[List[str]] = None,
                          hipaa: Optional[List[str]] = None,
                          asvs: Optional[List[str]] = None,
                          remediation: str = "", notes: str = "") -> Evidence:
        """Build, persist and register one evidence record."""
        resp_summary: Dict[str, Any] = {}
        status = None
        if response is not None:
            status = response.status
            resp_summary = {
                "status": response.status,
                "elapsed_ms": round(response.elapsed_ms, 1),
                "headers": response.headers,
                "body_excerpt": (response.text or "")[:MAX_CAPTURE],
                "transport_error": response.error,
            }
            if not observed:
                observed = (f"HTTP {response.status}" if not response.error
                            else f"transport error: {response.error}")

        ev = Evidence(
            test_id=test_id, category=category, test_name=test_name,
            endpoint=endpoint, method=method.upper(),
            request_summary=request_summary or {},
            response_status=status, response_summary=resp_summary,
            expected=expected, observed=observed, outcome=outcome,
            finding=finding, severity=severity, confidence=confidence,
            owasp=owasp or [], owasp_api=owasp_api or [], cwe=cwe or [],
            nist=nist or [], hipaa=hipaa or [], asvs=asvs or [],
            remediation=remediation,
            duration_ms=round(response.elapsed_ms, 1) if response else 0.0,
            notes=notes,
        )
        self.writer.write(ev)
        return self.run.add(ev)

    # ── helpers for test modules ─────────────────────────────────────────────

    @staticmethod
    def leaks_stack_trace(resp: Response) -> bool:
        """True if the body exposes internals a client must never see."""
        body = (resp.text or "")[:6000]
        markers = ("Traceback (most recent call last)", "File \"/", "sqlalchemy.",
                   "asyncpg.", "psycopg2.", "at /home/site/wwwroot",
                   "django.", "werkzeug", "InternalServerError")
        return any(m in body for m in markers)

    def token(self, role: str) -> Optional[str]:
        return self.auth_tokens.get(role)

    @property
    def have_credentials(self) -> bool:
        return bool(self.auth_tokens)
