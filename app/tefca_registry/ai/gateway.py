"""Vendor-agnostic LLM transport for TEFCA entity resolution.

THIS IS THE ONLY MODULE IN TEFCA THAT MAY TALK TO AN LLM PROVIDER. A CI test
(tests/test_tefca_ai_control_plane.py::test_no_direct_llm_calls_in_tefca)
scans app/tefca_registry/ and app/Tefca/ and fails the build if a provider call
appears anywhere else, so the boundary is enforced rather than documented.

WHAT THIS IS: a transport. It sends a prompt, enforces limits, retries
transient failures, and returns raw text.

WHAT THIS IS NOT: a decision-maker. It has ZERO decision authority. It never
parses, validates, interprets, or acts on a response. It does not know what a
match is. Interpretation belongs to validation.py, scoring to the evidence
engine, and the decision to a human — keeping those in separate modules is what
makes "the model decided" impossible to say about this system.

The bulletin module keeps its own direct provider calls and is untouched.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("docuaction.tefca.ai.gateway")


# ── Model allowlist ──────────────────────────────────────────────────────────
# A governance artifact, not a convenience default. A model absent from this
# map cannot be reached through the gateway at all. Changing it is a code change
# that goes through review, which is the point: "which model produced this
# determination" must be answerable from git history for any date.
#
# NOTE (2026-08): claude-haiku-4-5 and claude-sonnet-4-6 are both current, active
# model IDs. Newer generations (claude-opus-5, claude-sonnet-5) exist and are
# more capable; they are deliberately NOT added here without ONC review, since
# adding a model silently changes what produced a compliance determination.
ALLOWED_MODELS: Dict[str, Dict[str, str]] = {
    "claude": {
        "fast": "claude-haiku-4-5",
        "standard": "claude-sonnet-4-6",
    },
    "openai": {
        "fast": "gpt-5.6-luna",
        "standard": "gpt-5.6-terra",
    },
}

# TEFCA entity resolution is the only task type. One entry, deliberately: a
# limits table with one row cannot be widened by picking a different key.
TOKEN_LIMITS: Dict[str, Dict[str, int]] = {
    "entity_match": {"max_input": 2000, "max_output": 500},
}

# Determinism is a compliance property here, not a tuning knob. The same two
# records must produce the same recommendation whenever the question is asked.
TEMPERATURE = 0.0

# Models that reject sampling parameters outright (Opus 4.7+ / Sonnet 5 / Opus 5
# / Fable 5 return 400 for `temperature`). None are currently allowlisted; the
# check exists so that adding one later degrades to "omit the parameter" rather
# than to "every request 400s". The invariant that callers may not request a
# non-zero temperature is enforced regardless of model — see _check_temperature.
_SAMPLING_REJECTED = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
                      "claude-sonnet-5", "claude-fable-5", "claude-mythos-5")

# Roughly 4 characters per token. Deliberately an estimate: the alternative is a
# count_tokens round trip per call, which costs a network hop to enforce a limit
# whose purpose is to stop a runaway payload, not to bill accurately. The
# estimate errs high (it will reject slightly before the true limit), which is
# the correct direction for a guard.
_CHARS_PER_TOKEN = 4

CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_COOLDOWN_SECONDS = 300.0
MAX_ATTEMPTS = 3


@dataclass
class GatewayResponse:
    """Raw provider output. Uninterpreted by design."""
    text: str
    provider: str
    model: str
    latency_ms: float
    tier: str = "standard"
    attempts: int = 1
    # Deliberately no `confidence` field. The gateway never reads the response
    # body, so it has nothing to put in one. The model's self-reported
    # confidence is extracted by validation.py as `ai_raw_confidence` — a name
    # that makes decision use obviously wrong at every call site.


@dataclass
class DualResponse:
    """Two independent provider opinions.

    `agree` is ONE SIGNAL among several, fed to the evidence score as
    `models_agree`. It is not authorization and it does not shorten review: two
    models trained on overlapping data agreeing is weak evidence, and treating
    it as strong evidence is the exact failure this architecture exists to
    prevent. See human_gate.py.
    """
    primary: Optional[GatewayResponse]
    secondary: Optional[GatewayResponse]
    agree: Optional[bool] = None

    @property
    def best(self) -> Optional[GatewayResponse]:
        """The response the pipeline carries forward. Primary wins; secondary
        is a cross-check, not a replacement."""
        return self.primary or self.secondary


class GatewayError(Exception):
    """Raised for caller mistakes (bad tier, bad task, non-zero temperature).

    Distinct from provider failure, which returns None. A caller asking for
    something the gateway must not do is a bug to surface loudly; a provider
    being down is an operational condition to degrade through.
    """


class _CircuitBreaker:
    """Per-provider breaker: 5 consecutive failures disable it for 5 minutes.

    Consecutive, not cumulative — a provider that fails once an hour is healthy
    and should not eventually trip. Any success resets the count.
    """

    def __init__(self, threshold: int = CIRCUIT_FAILURE_THRESHOLD,
                 cooldown: float = CIRCUIT_COOLDOWN_SECONDS):
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures = 0
        self._opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if (time.monotonic() - self._opened_at) >= self.cooldown:
            # Cooldown elapsed: half-open. The next call is a live probe; it
            # either succeeds (reset) or fails (re-open for another cooldown).
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.error("TEFCA AI circuit breaker OPEN after %d consecutive "
                         "failures — provider disabled for %.0fs",
                         self._failures, self.cooldown)


def _redact(text: str) -> str:
    """A length-and-shape summary, never content.

    Prompts carry organization names, addresses, and NPIs. Those are public
    directory data, but a log line is a different retention and access regime
    than the audit table, and the audit table already records a SHA-256 of the
    exact input. Logging the payload again here would add no forensic value and
    a second place to leak from.
    """
    return f"<{len(text)} chars>" if text else "<empty>"


class TEFCAAIGateway:
    """Controlled routing to an LLM provider. No decision authority."""

    def __init__(self, *, primary: Optional[str] = None,
                 fallback_enabled: Optional[bool] = None,
                 http_client: Any = None):
        self.primary_provider = (primary or os.getenv("AI_PRIMARY_PROVIDER", "claude")
                                 or "claude").strip().lower()
        if self.primary_provider not in ALLOWED_MODELS:
            logger.warning("AI_PRIMARY_PROVIDER=%r is not an allowlisted provider "
                           "— falling back to 'claude'", self.primary_provider)
            self.primary_provider = "claude"

        if fallback_enabled is None:
            fallback_enabled = (os.getenv("AI_FALLBACK_ENABLED", "true") or "").strip().lower() \
                not in ("false", "0", "no", "off")
        self.fallback_enabled = bool(fallback_enabled)

        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._anthropic_client: Any = None
        self._breakers: Dict[str, _CircuitBreaker] = {
            name: _CircuitBreaker() for name in ALLOWED_MODELS
        }

    # ── Credentials and availability ─────────────────────────────────────
    @staticmethod
    def _api_key(provider: str) -> str:
        env = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider, "")
        return (os.getenv(env, "") or "").strip() if env else ""

    def provider_available(self, provider: str) -> bool:
        return (provider in ALLOWED_MODELS
                and bool(self._api_key(provider))
                and not self._breakers[provider].is_open)

    @property
    def fallback_provider(self) -> Optional[str]:
        if not self.fallback_enabled:
            return None
        return next((p for p in ALLOWED_MODELS if p != self.primary_provider), None)

    @property
    def dual_available(self) -> bool:
        """Both providers usable. OPENAI_API_KEY is unset until the BAA is
        signed, so today this is False everywhere and dual verification simply
        does not run — the single-provider path is taken and review is
        unchanged, because review was never conditional on it."""
        other = self.fallback_provider
        return bool(other and self.provider_available(self.primary_provider)
                    and self.provider_available(other))

    # ── Shared HTTP client ───────────────────────────────────────────────
    def _client(self):
        """One pooled httpx.AsyncClient for the gateway's lifetime.

        Shared so TCP and TLS handshakes are not repaid per call. Constructed
        lazily so that importing this module in a test process that never makes
        a call does not open sockets.
        """
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_client

    async def aclose(self) -> None:
        """Release the pool. Only closes a client the gateway itself created —
        an injected client belongs to the caller."""
        if self._http_client is not None and self._owns_http_client:
            try:
                await self._http_client.aclose()
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                logger.debug("gateway http client close failed: %s", exc)
        self._http_client = None
        self._anthropic_client = None

    # ── Limit enforcement ────────────────────────────────────────────────
    @staticmethod
    def resolve_model(provider: str, tier: str) -> str:
        try:
            return ALLOWED_MODELS[provider][tier]
        except KeyError:
            raise GatewayError(
                f"no allowlisted model for provider={provider!r} tier={tier!r}") from None

    @staticmethod
    def _limits(task: str = "entity_match") -> Dict[str, int]:
        if task not in TOKEN_LIMITS:
            raise GatewayError(f"no token limits defined for task {task!r}")
        return TOKEN_LIMITS[task]

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        return (len(text or "") + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN

    @classmethod
    def check_input_limit(cls, prompt: str, task: str = "entity_match") -> Tuple[bool, int, int]:
        """(within_limit, estimated, max). Checked before dispatch, so an
        oversized payload never leaves the process."""
        cap = cls._limits(task)["max_input"]
        estimated = cls.estimate_tokens(prompt)
        return estimated <= cap, estimated, cap

    @staticmethod
    def _check_temperature(temperature: float) -> None:
        if temperature != TEMPERATURE:
            raise GatewayError(
                f"temperature must be {TEMPERATURE} for TEFCA entity resolution; "
                f"got {temperature!r}. Determinism is a compliance requirement.")

    # ── Public API ───────────────────────────────────────────────────────
    async def call(self, prompt: str, context: Optional[Dict[str, Any]] = None,
                   tier: str = "standard", temperature: float = TEMPERATURE,
                   task: str = "entity_match") -> Optional[GatewayResponse]:
        """Primary → fallback → None.

        Returns None when no provider could answer. None is a first-class
        outcome, not an error: the caller's response to it is to continue with
        the deterministic result, which is the same answer the system gives when
        AI is disabled entirely.
        """
        self._check_temperature(temperature)

        within, estimated, cap = self.check_input_limit(prompt, task)
        if not within:
            # A limit breach is a caller bug (an unexpectedly large record), not
            # a provider problem. Refuse locally and let the deterministic path
            # carry the case rather than paying for a request that is wrong.
            logger.error("TEFCA AI prompt exceeds input limit (%d > %d tokens estimated) "
                         "— not dispatched", estimated, cap)
            return None

        order = [self.primary_provider]
        fallback = self.fallback_provider
        if fallback:
            order.append(fallback)

        for provider in order:
            if not self.provider_available(provider):
                logger.info("TEFCA AI provider %s unavailable (key missing or circuit "
                            "open) — skipping", provider)
                continue
            response = await self._call_provider(provider, prompt, tier, task, temperature)
            if response is not None:
                return response
            logger.warning("TEFCA AI provider %s failed; %s", provider,
                           "trying fallback" if provider != order[-1] else "no providers left")

        logger.warning("TEFCA AI unavailable across all providers — "
                       "deterministic resolution only")
        return None

    async def call_dual(self, prompt: str, context: Optional[Dict[str, Any]] = None,
                        tier: str = "standard", temperature: float = TEMPERATURE,
                        task: str = "entity_match") -> DualResponse:
        """Both providers evaluate the same prompt independently.

        Concurrent, not sequential: they are independent opinions, and running
        them in series would double latency for no added independence. Neither
        sees the other's answer.

        Agreement is recorded as a signal. It is not authorization.
        """
        self._check_temperature(temperature)

        within, estimated, cap = self.check_input_limit(prompt, task)
        if not within:
            logger.error("TEFCA AI prompt exceeds input limit (%d > %d tokens estimated) "
                         "— not dispatched", estimated, cap)
            return DualResponse(None, None, None)

        other = self.fallback_provider
        if not other or not self.dual_available:
            # Only one provider is usable. Degrade to a single opinion rather
            # than failing: one governed answer beats none, and review is
            # required either way.
            single = await self.call(prompt, context, tier, temperature, task)
            return DualResponse(single, None, None)

        primary, secondary = await asyncio.gather(
            self._call_provider(self.primary_provider, prompt, tier, task, temperature),
            self._call_provider(other, prompt, tier, task, temperature),
        )
        return DualResponse(primary, secondary, _texts_agree(primary, secondary))

    # ── Dispatch with retry ──────────────────────────────────────────────
    async def _call_provider(self, provider: str, prompt: str, tier: str,
                             task: str, temperature: float) -> Optional[GatewayResponse]:
        model = self.resolve_model(provider, tier)
        max_output = self._limits(task)["max_output"]
        breaker = self._breakers[provider]
        started = time.monotonic()

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                if provider == "claude":
                    text = await self._call_anthropic(model, prompt, max_output, temperature)
                else:
                    text = await self._call_openai(model, prompt, max_output, temperature)
            except _RetryableProviderError as exc:
                breaker.record_failure()
                if attempt == MAX_ATTEMPTS:
                    logger.warning("TEFCA AI %s/%s exhausted %d attempts: %s",
                                   provider, model, MAX_ATTEMPTS, exc)
                    return None
                # Full jitter. Identical backoff across concurrent callers would
                # reconverge them onto the same retry instant and re-create the
                # burst that caused the 429.
                delay = min(8.0, 0.5 * (2 ** (attempt - 1)))
                await asyncio.sleep(delay * (0.5 + random.random() / 2))
                continue
            except Exception as exc:  # noqa: BLE001 — AI must never break the pipeline
                breaker.record_failure()
                logger.warning("TEFCA AI %s/%s failed (%s: %s) — prompt %s",
                               provider, model, type(exc).__name__, exc, _redact(prompt))
                return None

            breaker.record_success()
            latency_ms = (time.monotonic() - started) * 1000.0
            logger.info("TEFCA AI %s/%s ok in %.0fms (attempt %d), prompt %s",
                        provider, model, latency_ms, attempt, _redact(prompt))
            return GatewayResponse(text=text, provider=provider, model=model,
                                   latency_ms=latency_ms, tier=tier, attempts=attempt)
        return None

    # ── Provider adapters ────────────────────────────────────────────────
    async def _call_anthropic(self, model: str, prompt: str, max_output: int,
                              temperature: float) -> str:
        """The one place in TEFCA that may import the Anthropic SDK."""
        import anthropic

        if self._anthropic_client is None:
            # The SDK is given the gateway's pooled httpx client so both
            # providers share one connection pool rather than each holding
            # their own — "shared client with connection pooling" applies to the
            # gateway, not to one vendor's transport.
            kwargs: Dict[str, Any] = {"api_key": self._api_key("claude")}
            try:
                from anthropic import DefaultAsyncHttpxClient  # noqa: F401
                kwargs["http_client"] = self._client()
            except Exception:  # pragma: no cover - older SDK without the hook
                pass
            self._anthropic_client = anthropic.AsyncAnthropic(**kwargs)

        request: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_output,
            "messages": [{"role": "user", "content": prompt}],
        }
        if not model.startswith(_SAMPLING_REJECTED):
            request["temperature"] = temperature

        try:
            resp = await self._anthropic_client.messages.create(**request)
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc

        parts = []
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", "") or "")
        return "\n".join(parts).strip()

    async def _call_openai(self, model: str, prompt: str, max_output: int,
                           temperature: float) -> str:
        """OpenAI over the shared pooled client.

        Raw HTTP rather than an SDK because the OpenAI SDK is not a dependency
        of this application and adding one to reach a provider that is not yet
        contractually usable (no BAA, no key) would install a package that runs
        no code. This path is unreachable until OPENAI_API_KEY is set.
        """
        client = self._client()
        try:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key('openai')}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "max_completion_tokens": max_output,
                      "temperature": temperature,
                      "messages": [{"role": "user", "content": prompt}]},
            )
        except Exception as exc:  # noqa: BLE001 — transport errors are retryable
            raise _RetryableProviderError(f"{type(exc).__name__}: {exc}") from exc

        status = getattr(resp, "status_code", 0)
        if status == 429 or status >= 500:
            raise _RetryableProviderError(f"HTTP {status}")
        if status >= 400:
            raise RuntimeError(f"OpenAI returned HTTP {status}")

        payload = resp.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()


class _RetryableProviderError(Exception):
    """A transient provider condition: 429 or 5xx, or a connection error."""


def _classify(exc: Exception) -> Exception:
    """Map an SDK exception to retryable or not.

    Matched on the exception's status attribute first and its class name second,
    so this does not import provider exception types (which would pull the SDK
    into module scope) and does not string-match error messages (which change).
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return _RetryableProviderError(f"HTTP {status}")
    name = type(exc).__name__
    if name in ("RateLimitError", "InternalServerError", "APIConnectionError",
                "APITimeoutError", "OverloadedError"):
        return _RetryableProviderError(name)
    return exc


def _texts_agree(a: Optional[GatewayResponse], b: Optional[GatewayResponse]) -> Optional[bool]:
    """Do the two providers reach the same match verdict?

    None when either side is missing — "unknown", not "disagree". Recording an
    absent second opinion as disagreement would depress the evidence score of a
    perfectly ordinary single-provider call.

    Compares the parsed `match` verdict only. Two models will phrase a rationale
    differently while reaching the same conclusion, so comparing raw text would
    report disagreement on nearly every genuine agreement.
    """
    if a is None or b is None:
        return None
    return _verdict(a.text) == _verdict(b.text)


def _verdict(text: str) -> Any:
    """The `match` field, or a sentinel when unparseable.

    Deliberately tolerant: this is a signal fed to a score, and a parse failure
    here must not raise into the gateway. validation.py is the module that
    rejects malformed output; a distinct sentinel object per unparseable
    response means two unparseable responses never compare as agreeing.
    """
    import json
    try:
        return json.loads(text).get("match")
    except Exception:  # noqa: BLE001
        return object()
