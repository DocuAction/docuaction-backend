"""USPS APIs v3 client — OAuth 2.0, address standardization, circuit breaker.

This is the modern USPS platform (`apis.usps.com`, OAuth 2.0 client-credentials),
NOT the legacy Web Tools XML endpoint that `usps_connector.py` calls. The two
coexist deliberately: the legacy connector is wired into existing code paths and
keyed on `USPS_API_USER_ID`, this one is keyed on `USPS_CLIENT_ID` /
`USPS_CLIENT_SECRET`, and neither is required for the system to work.

Nothing here is load-bearing. Without credentials every entry point reports
itself unavailable and callers fall back to code-only normalization
(`address_normalizer`), which needs no key, no network, and no quota. That is the
same contract the legacy connector honours, and it is what lets this ship before
anyone has registered for a key.

The failure modes this guards against, in order of how much damage they do:

  - A hung or flapping USPS breaks address checks for every verification run.
    Hence the circuit breaker: five consecutive failures and we stop calling for
    five minutes, falling back to code normalization rather than paying the
    timeout on every entity.
  - A leaked bearer token in a log file is a credential disclosure in a system
    under FedRAMP-style review. Hence `_safe_log`, and hence tokens never being
    passed to a log call anywhere in this module.
  - A retry storm against a rate-limited endpoint turns a slow period into an
    outage. Hence bounded retries with exponential backoff, and no retry at all
    on the statuses that will never succeed on repeat (400, 401, 404).

Never raises to the caller. Every failure path returns a `USPSAddressResult` with
`available=False` and a reason, so an address check cannot break a review.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("docuaction.tefca.usps_v3")

# httpx logs every request as `HTTP Request: GET <full url> "200 OK"` at INFO.
# The USPS v3 address API takes the street address as a QUERY PARAMETER, so that
# line puts a full street address into the application log — PII this module
# takes care never to log itself. Raising httpx's own level to WARNING is the
# only place that can be stopped, since the URL is built by httpx internals.
# Errors and warnings from httpx still come through.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Endpoints (REFINEMENT 1) ─────────────────────────────────────────────────
# Every USPS path in this module comes from here. A URL built inline in a method
# is a URL that does not get updated when the API version moves.
OAUTH_ENDPOINT = "/oauth2/v3/token"
ADDRESS_ENDPOINT = "/addresses/v3/address"

BASE_URLS = {
    "production": "https://apis.usps.com",
    "testing": "https://apis-tem.usps.com",
}

# Retryable = the server said "not now". Everything else is a request that will
# fail identically on repeat, and retrying it only spends quota and latency.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 3
BACKOFF_SCHEDULE = (1.0, 2.0, 4.0)

CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_COOLDOWN_S = 300.0

# Refreshed this far before actual expiry, so a token cannot expire mid-flight
# between the check and the request that uses it.
TOKEN_REFRESH_MARGIN_S = 60.0

DEFAULT_TIMEOUT_S = float(os.getenv("USPS_TIMEOUT_S", "10"))


# ── Models (REFINEMENT 8) ────────────────────────────────────────────────────

class USPSAddressResult(BaseModel):
    """One address standardization attempt.

    `available` answers "did USPS give us an answer", which is a different
    question from "is this address good". A non-deliverable address that USPS
    confirmed is available=True, is_deliverable=False. An address we never got to
    ask about is available=False. Collapsing those two would let an outage read
    as a registry full of bad addresses.
    """
    available: bool
    method: str  # "usps_api" | "usps_not_configured" | "usps_error"
    standardized_street: Optional[str] = None
    standardized_city: Optional[str] = None
    standardized_state: Optional[str] = None
    zip5: Optional[str] = None
    zip4: Optional[str] = None
    dpv_confirmed: bool = False
    is_deliverable: bool = False
    corrections: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class AddressMatch(BaseModel):
    """Result of comparing a submitted address against a registry address.

    NOTE: `address_normalizer.AddressMatch` is a different, older dataclass with
    different field names, still used by `entity_resolver`. This one is what the
    three-layer comparison returns. Import one or the other explicitly; do not
    assume a bare `AddressMatch` is this class.
    """
    match: bool
    confidence: float
    method: str
    usps_zip4_match: Optional[bool] = None
    dpv_confirmed: Optional[bool] = None
    submitted_normalized: Optional[str] = None
    registry_normalized: Optional[str] = None


# ── Metrics (REFINEMENT 7) ───────────────────────────────────────────────────

class USPSMetrics:
    """Counters for Azure Monitor. Rates are derived, never stored.

    Storing a rate means two numbers that can disagree; deriving it means the
    rate cannot drift from the counts it summarises.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.requests_total = 0
        self.errors_total = 0
        self.latency_total_ms = 0.0
        self.dpv_confirmed_total = 0
        self.zip4_returned_total = 0
        self.fallback_total = 0
        self.attempts_total = 0

    def record_attempt(self) -> None:
        """One call into the client, whether or not it reached USPS.

        The denominator for fallback_rate. Counting only requests that reached
        USPS would make the rate undefined exactly when it matters most — when
        the circuit is open and nothing is reaching USPS at all.
        """
        self.attempts_total += 1

    def record_request(self, *, latency_ms: float, dpv: bool, zip4: bool) -> None:
        self.requests_total += 1
        self.latency_total_ms += latency_ms
        if dpv:
            self.dpv_confirmed_total += 1
        if zip4:
            self.zip4_returned_total += 1

    def record_error(self) -> None:
        self.errors_total += 1

    def record_fallback(self) -> None:
        self.fallback_total += 1

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    def snapshot(self, circuit_status: str) -> Dict[str, Any]:
        return {
            "usps_requests_total": self.requests_total,
            "usps_latency_avg_ms": (
                round(self.latency_total_ms / self.requests_total, 2)
                if self.requests_total else 0.0),
            "usps_errors_total": self.errors_total,
            "usps_dpv_success_rate": self._rate(self.dpv_confirmed_total,
                                                self.requests_total),
            "usps_zip4_success_rate": self._rate(self.zip4_returned_total,
                                                 self.requests_total),
            "usps_fallback_rate": self._rate(self.fallback_total,
                                             self.attempts_total),
            "usps_circuit_breaker_status": circuit_status,
        }


# ── Circuit breaker (REFINEMENT 6) ───────────────────────────────────────────

class CircuitBreaker:
    """Closed → (5 consecutive failures) → open → (5 min) → half-open → …

    Half-open lets exactly one request through. Letting the whole backlog through
    at the moment the cooldown expires is how a struggling upstream gets knocked
    over a second time by the client that was supposed to be protecting it.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, *, threshold: int = CIRCUIT_FAILURE_THRESHOLD,
                 cooldown_s: float = CIRCUIT_COOLDOWN_S, clock=time.monotonic):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self.consecutive_failures = 0
        self.opened_at: Optional[float] = None
        self._half_open = False

    @property
    def status(self) -> str:
        """What /health reports. Externally there are two states that matter —
        calls are flowing or they are not — so half-open reports as open until a
        probe actually succeeds."""
        if self.opened_at is None:
            return self.CLOSED
        return self.OPEN

    def is_open(self) -> bool:
        """Read-only "should callers bother". Unlike `allows_request` this does
        NOT consume the half-open probe slot, so a caller can check whether to
        route around USPS without silently spending the one probe that decides
        whether the circuit closes."""
        if self.opened_at is None:
            return False
        return self._clock() - self.opened_at < self.cooldown_s

    def allows_request(self) -> bool:
        if self.opened_at is None:
            return True
        if self._clock() - self.opened_at < self.cooldown_s:
            return False
        if self._half_open:
            # A probe is already out. Everyone else keeps falling back.
            return False
        self._half_open = True
        logger.info("USPS circuit breaker half-open — probing with one request")
        return True

    def record_success(self) -> None:
        was_open = self.opened_at is not None
        self.consecutive_failures = 0
        self.opened_at = None
        self._half_open = False
        if was_open:
            logger.info("USPS circuit breaker closed — upstream recovered")

    def record_failure(self) -> None:
        if self._half_open:
            # The probe failed. Serve another full cooldown rather than
            # re-probing on the very next call.
            self._half_open = False
            self.opened_at = self._clock()
            logger.warning("USPS circuit breaker probe failed — reopening for %.0fs",
                           self.cooldown_s)
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and self.opened_at is None:
            self.opened_at = self._clock()
            logger.warning(
                "USPS circuit breaker open — falling back (%d consecutive failures, "
                "retrying in %.0fs)", self.consecutive_failures, self.cooldown_s)


# ── Safe logging (REFINEMENT 5) ──────────────────────────────────────────────

_NEVER_LOG = ("token", "access_token", "client_secret", "authorization", "secret")


def _safe_log(level: int, message: str, **fields: Any) -> None:
    """Log structured fields with the credential-bearing ones removed.

    The filter is a backstop, not the control. Nothing in this module passes a
    token to a log call in the first place; this exists so that a future edit
    that does gets caught rather than shipped. Street addresses are dropped for
    the same reason PII is minimised everywhere else in the registry — city and
    state are enough to trace a request.
    """
    safe = {k: v for k, v in fields.items()
            if not any(banned in k.lower() for banned in _NEVER_LOG)}
    logger.log(level, "%s %s", message,
               " ".join(f"{k}={v}" for k, v in safe.items() if v is not None))


# ── Client ───────────────────────────────────────────────────────────────────

class USPSClient:
    """USPS APIs v3 client. Safe to construct with no credentials."""

    def __init__(self, *, client_id: Optional[str] = None,
                 client_secret: Optional[str] = None,
                 environment: Optional[str] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 clock=time.monotonic):
        # Read at construction, not at import, so adding the App Service settings
        # and restarting is enough to activate this — no code change, which is
        # the whole point of shipping it unconfigured.
        self.client_id = (client_id if client_id is not None
                          else os.getenv("USPS_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret if client_secret is not None
                              else os.getenv("USPS_CLIENT_SECRET", "")).strip()
        env = (environment if environment is not None
               else os.getenv("USPS_ENV", "production")).strip().lower()
        if env not in BASE_URLS:
            logger.warning("USPS_ENV=%r not recognised — using production", env)
            env = "production"
        self.environment = env
        self.base_url = BASE_URLS[env]

        self.metrics = USPSMetrics()
        self.circuit = CircuitBreaker(clock=clock)
        self._clock = clock

        # REFINEMENT 2 — ONE client, connection-pooled, for the process. A client
        # per request throws away the pool and re-does the TLS handshake every
        # time, which on a 383-entity sample is 383 handshakes.
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"Accept": "application/json"},
        )

        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._auth_failed = False

        # Injectable so tests exercise the backoff schedule without sleeping
        # through it. Retry timing is behaviour worth testing; 7 real seconds of
        # it per test is not.
        self._sleep = asyncio.sleep

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def aclose(self) -> None:
        """Close the pooled connections. Call on application shutdown."""
        await self._http.aclose()

    # ── Token handling (REFINEMENT 4) ────────────────────────────────────────

    def _token_valid(self) -> bool:
        return bool(self._token) and self._clock() < self._token_expires_at

    def _clear_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

    async def _get_token(self) -> Optional[str]:
        """Cached client-credentials token, fetched on demand.

        The lock keeps a burst of concurrent verifications from firing N token
        requests at once when the cache is cold — USPS counts those.
        """
        if self._token_valid():
            return self._token
        async with self._token_lock:
            if self._token_valid():        # another waiter refreshed it
                return self._token
            started = time.perf_counter()
            request_id = uuid.uuid4().hex[:12]
            try:
                resp = await self._http.post(
                    OAUTH_ENDPOINT,
                    json={"grant_type": "client_credentials",
                          "client_id": self.client_id,
                          "client_secret": self.client_secret},
                )
            except Exception as e:  # noqa: BLE001 — never raise into a review
                _safe_log(logging.WARNING, "USPS token request failed",
                          request_id=request_id, endpoint=OAUTH_ENDPOINT,
                          error=type(e).__name__)
                return None

            latency_ms = (time.perf_counter() - started) * 1000
            if resp.status_code != 200:
                # 401 here means the credentials themselves are wrong. That is a
                # configuration fault, not a transient one, and it is worth
                # surfacing on /health rather than burying in a retry count.
                if resp.status_code == 401:
                    self._auth_failed = True
                _safe_log(logging.WARNING, "USPS token rejected",
                          request_id=request_id, endpoint=OAUTH_ENDPOINT,
                          http_status=resp.status_code,
                          latency_ms=round(latency_ms, 1))
                return None

            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                _safe_log(logging.WARNING, "USPS token response was not JSON",
                          request_id=request_id, endpoint=OAUTH_ENDPOINT)
                return None

            token = payload.get("access_token")
            if not token:
                _safe_log(logging.WARNING, "USPS token response had no access_token",
                          request_id=request_id, endpoint=OAUTH_ENDPOINT)
                return None

            expires_in = float(payload.get("expires_in", 3600) or 3600)
            self._token = token
            self._token_expires_at = self._clock() + max(
                0.0, expires_in - TOKEN_REFRESH_MARGIN_S)
            self._auth_failed = False
            _safe_log(logging.INFO, "USPS token acquired",
                      request_id=request_id, endpoint=OAUTH_ENDPOINT,
                      http_status=200, latency_ms=round(latency_ms, 1))
            return self._token

    # ── Request execution (REFINEMENTS 3 + 4) ────────────────────────────────

    async def _request_with_retries(self, params: Dict[str, str], *,
                                    request_id: str) -> Optional[httpx.Response]:
        """GET the address endpoint, retrying only what is worth retrying.

        Returns the response (any status), or None when the request could not be
        completed at all. A 4xx comes back as a response — the caller needs the
        status to decide what to report.
        """
        attempt = 0
        refreshed_once = False
        while True:
            token = await self._get_token()
            if not token:
                return None
            try:
                resp = await self._http.get(
                    ADDRESS_ENDPOINT, params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except Exception as e:  # noqa: BLE001
                if attempt < MAX_RETRIES:
                    await self._sleep(BACKOFF_SCHEDULE[attempt])
                    attempt += 1
                    continue
                _safe_log(logging.WARNING, "USPS address request failed",
                          request_id=request_id, endpoint=ADDRESS_ENDPOINT,
                          error=type(e).__name__, attempts=attempt + 1)
                return None

            # REFINEMENT 4 — a 401 on the address call means the token went stale
            # early. Clear, refetch, retry ONCE. A second 401 is a real auth
            # problem, and looping on it would hammer the token endpoint.
            if resp.status_code == 401 and not refreshed_once:
                refreshed_once = True
                self._clear_token()
                _safe_log(logging.INFO, "USPS returned 401 — refreshing token once",
                          request_id=request_id, endpoint=ADDRESS_ENDPOINT,
                          http_status=401)
                continue
            if resp.status_code == 401:
                self._auth_failed = True
                return resp

            if resp.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                delay = BACKOFF_SCHEDULE[attempt]
                _safe_log(logging.INFO, "USPS transient status — backing off",
                          request_id=request_id, endpoint=ADDRESS_ENDPOINT,
                          http_status=resp.status_code, retry_in_s=delay,
                          attempt=attempt + 1)
                await self._sleep(delay)
                attempt += 1
                continue

            return resp

    # ── Public API ───────────────────────────────────────────────────────────

    async def standardize(self, street: str, *, city: str = "", state: str = "",
                          zip5: str = "", secondary: str = "") -> USPSAddressResult:
        """Standardize one address. Never raises."""
        self.metrics.record_attempt()

        if not self.configured:
            self.metrics.record_fallback()
            return USPSAddressResult(
                available=False, method="usps_not_configured",
                error="USPS_CLIENT_ID / USPS_CLIENT_SECRET not set")

        if not (street or "").strip():
            self.metrics.record_fallback()
            return USPSAddressResult(available=False, method="usps_error",
                                     error="empty street address")

        # REFINEMENT 6 — refuse before spending a timeout, not after.
        if not self.circuit.allows_request():
            self.metrics.record_fallback()
            _safe_log(logging.INFO, "USPS circuit breaker open — falling back",
                      endpoint=ADDRESS_ENDPOINT, city=city, state=state)
            return USPSAddressResult(available=False, method="usps_error",
                                     error="circuit breaker open")

        request_id = uuid.uuid4().hex[:12]
        params = {"streetAddress": street.strip()}
        if secondary:
            params["secondaryAddress"] = secondary.strip()
        if city:
            params["city"] = city.strip()
        if state:
            params["state"] = state.strip()
        if zip5:
            params["ZIPCode"] = zip5.strip()

        started = time.perf_counter()
        resp = await self._request_with_retries(params, request_id=request_id)
        latency_ms = (time.perf_counter() - started) * 1000

        if resp is None:
            return self._fail(request_id, latency_ms, city, state,
                              "request could not be completed", None)

        if resp.status_code != 200:
            return self._fail(request_id, latency_ms, city, state,
                              f"USPS returned HTTP {resp.status_code}",
                              resp.status_code)

        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            return self._fail(request_id, latency_ms, city, state,
                              "USPS response was not JSON", resp.status_code)

        result = self._parse(payload, street=street, city=city, state=state,
                             zip5=zip5, latency_ms=latency_ms)
        self.circuit.record_success()
        self.metrics.record_request(latency_ms=latency_ms,
                                    dpv=result.dpv_confirmed,
                                    zip4=bool(result.zip4))
        _safe_log(logging.INFO, "USPS address standardized",
                  request_id=request_id, endpoint=ADDRESS_ENDPOINT,
                  http_status=200, latency_ms=round(latency_ms, 1),
                  city=result.standardized_city or city,
                  state=result.standardized_state or state,
                  dpv=result.dpv_confirmed)
        return result

    def _fail(self, request_id: str, latency_ms: float, city: str, state: str,
              error: str, http_status: Optional[int]) -> USPSAddressResult:
        self.circuit.record_failure()
        self.metrics.record_error()
        self.metrics.record_fallback()
        _safe_log(logging.WARNING, "USPS address request unsuccessful",
                  request_id=request_id, endpoint=ADDRESS_ENDPOINT,
                  http_status=http_status, latency_ms=round(latency_ms, 1),
                  city=city, state=state, error=error)
        return USPSAddressResult(available=False, method="usps_error",
                                 error=error, latency_ms=round(latency_ms, 2))

    @staticmethod
    def _parse(payload: Dict[str, Any], *, street: str, city: str, state: str,
               zip5: str, latency_ms: float) -> USPSAddressResult:
        address = payload.get("address") or {}
        extra = payload.get("additionalInfo") or {}

        out_street = (address.get("streetAddress") or "").strip()
        out_city = (address.get("city") or "").strip()
        out_state = (address.get("state") or "").strip()
        out_zip5 = (address.get("ZIPCode") or "").strip()
        out_zip4 = (address.get("ZIPPlus4") or "").strip()

        dpv = str(extra.get("DPVConfirmation") or "").strip().upper()
        # Y = the whole address is deliverable. D and S mean the primary number
        # is deliverable but the secondary (suite/apt) is missing or wrong — mail
        # gets there, so they count as deliverable but NOT as confirmed.
        dpv_confirmed = dpv == "Y"
        is_deliverable = dpv in ("Y", "D", "S")

        corrections: List[str] = []
        for label, submitted, returned in (
            ("street", street, out_street),
            ("city", city, out_city),
            ("state", state, out_state),
            ("ZIP", zip5, out_zip5),
        ):
            if submitted and returned and submitted.strip().upper() != returned.upper():
                corrections.append(f"{label}: {submitted.strip()} → {returned}")

        return USPSAddressResult(
            available=True,
            method="usps_api",
            standardized_street=out_street or None,
            standardized_city=out_city or None,
            standardized_state=out_state or None,
            zip5=out_zip5 or None,
            zip4=out_zip4 or None,
            dpv_confirmed=dpv_confirmed,
            is_deliverable=is_deliverable,
            corrections=corrections,
            latency_ms=round(latency_ms, 2),
        )

    # ── Observability ────────────────────────────────────────────────────────

    def metrics_snapshot(self) -> Dict[str, Any]:
        return self.metrics.snapshot(self.circuit.status)

    def health(self) -> Dict[str, Any]:
        """State-only. /health is a liveness probe and must not make a network
        call — reporting "Operational" here means configured with the circuit
        closed, not that USPS answered just now. The metrics in the same payload
        are what show whether it has actually been exercised."""
        if not self.configured:
            status = "Not configured — code normalization active"
        elif self._auth_failed:
            status = "Authentication failed"
        elif self.circuit.status == CircuitBreaker.OPEN:
            status = "Circuit breaker open"
        else:
            status = "Operational"
        return {
            "status": status,
            "configured": self.configured,
            "environment": self.environment,
            "circuit_breaker": self.circuit.status,
            "metrics": self.metrics_snapshot(),
        }


# ── Process-wide singleton ───────────────────────────────────────────────────
# The circuit breaker and metrics are only meaningful if every caller shares
# them. A per-call client would count to five failures forever and never open.

_client: Optional[USPSClient] = None


def get_usps_client() -> USPSClient:
    global _client
    if _client is None:
        _client = USPSClient()
    return _client


def reset_usps_client() -> None:
    """Drop the singleton. For tests and for re-reading env after a config change."""
    global _client
    _client = None
