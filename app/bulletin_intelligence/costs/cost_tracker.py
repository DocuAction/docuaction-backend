"""FCC Bulletin — Claude API cost tracking (Phase 1). Additive + best-effort.

WHY THIS EXISTS
    Before this module the bulletin pipeline had NO cost or token accounting of any
    kind — a grep for `cost_usd|tokens_in|usage.` across the module returned nothing.
    Every statement about what a run costs was therefore an estimate. This records
    the real numbers so later optimisation work can be measured instead of asserted.

SCOPE — deliberately narrow
    Instruments the two Claude call sites that actually run in a cycle:
      * engine.classify_articles   (Haiku, one call per batch of 8 articles)
      * engine._summaries_for      (Haiku, one call per batch of 8 clusters)
    It does NOT change prompts, models, batching, ordering, or any bulletin logic.

DESIGN RULES (mirroring bulletin_store.py / instrumentation.py conventions)
    - Never raises. A failure to record cost must never fail a bulletin run.
    - Never blocks. Writes are awaited but wrapped; DB unavailable => silent skip.
    - Flag-gated OFF by default via BULLETIN_COST_TRACKING_ENABLED, so merging this
      changes nothing until it is switched on.
    - run_id travels via a contextvar rather than new function parameters, so the
      two call sites stay one-line additions and no signature changes ripple through
      engine.py. asyncio.gather-ed batches inherit the context automatically.

PRICING
    Rates are per million tokens and are a POINT-IN-TIME CONSTANT, not a live feed.
    If Anthropic pricing changes this table must be updated; cost_usd is only as
    accurate as these numbers. tokens_in/tokens_out are recorded raw so historical
    rows can always be re-priced.
"""

import contextvars
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("docuaction.bulletin.costs")

# OFF by default: merging this module is a no-op until explicitly enabled.
COST_TRACKING_ENABLED = (
    os.getenv("BULLETIN_COST_TRACKING_ENABLED", "false").strip().lower() == "true"
)

# USD per 1,000,000 tokens. Update when Anthropic pricing changes.
_PRICING = {
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
    "claude-sonnet-4-5": {"in": 3.00, "out": 15.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-opus-4-6": {"in": 5.00, "out": 25.00},
}
_DEFAULT_PRICING = {"in": 1.00, "out": 5.00}  # assume Haiku-tier if unknown

# Set once per cycle by run_context(); read by record_usage(). A contextvar (not a
# global) so concurrent cycles for different agencies cannot attribute cost to each
# other's run.
_run_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "bulletin_cost_run_id", default=None
)
_agency_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "bulletin_cost_agency_id", default=None
)


def current_run_id() -> Optional[str]:
    return _run_id_var.get()


def set_run_context(run_id: str, agency_id: str) -> None:
    """Tag subsequent Claude calls in THIS task with run_id/agency_id.

    Used by engine.run_daily_cycle as a one-line call, avoiding a `with` block that
    would force re-indenting the whole 336-line cycle body. contextvars are
    task-scoped, so concurrent cycles for different agencies do not cross-attribute,
    and each cycle simply overwrites its own context — no reset needed.
    """
    _run_id_var.set(run_id)
    _agency_var.set(agency_id)


@contextmanager
def run_context(run_id: str, agency_id: str):
    """Tag every Claude call made inside this block with a run_id/agency_id.

    Used by engine.run_daily_cycle. Calls made outside any run_context (e.g. an
    ad-hoc API call) still record, with run_id=None.
    """
    t1 = _run_id_var.set(run_id)
    t2 = _agency_var.set(agency_id)
    try:
        yield
    finally:
        _run_id_var.reset(t1)
        _agency_var.reset(t2)


def _usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p = _PRICING.get(model, _DEFAULT_PRICING)
    return round((tokens_in / 1_000_000) * p["in"] + (tokens_out / 1_000_000) * p["out"], 6)


async def record_usage(response: Any, *, operation: str, model: str) -> None:
    """Record token usage + computed cost for one Claude response.

    `response` is the object returned by client.messages.create(); only its
    .usage.input_tokens / .output_tokens are read. Any problem — tracking disabled,
    missing usage, DB down — is swallowed. This function must never be the reason a
    bulletin run fails.
    """
    if not COST_TRACKING_ENABLED:
        return
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
        if not tokens_in and not tokens_out:
            return

        row = {
            "id": uuid.uuid4().hex,
            "run_id": _run_id_var.get(),
            "agency_id": _agency_var.get(),
            "operation": operation,
            "provider": "anthropic",
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "api_calls": 1,
            "cost_usd": _usd(model, tokens_in, tokens_out),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        from app.bulletin_intelligence import bulletin_store

        await bulletin_store.save_cost_log(row)
    except Exception as e:  # pragma: no cover - defensive by design
        logger.debug(f"cost record skipped ({operation}): {e}")
