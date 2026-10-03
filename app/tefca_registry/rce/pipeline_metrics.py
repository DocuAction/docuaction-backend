"""Opt-in measurement hooks for `verify_and_classify` (2026-10-03, Lane P).

ZERO COST WHEN OFF. Nothing in this module runs unless a caller (a benchmark
or diagnostic) explicitly calls `activate()`; the pipeline's only obligation
is a single `if _ACTIVE is not None` check at a handful of points. No env var
is read on the hot path, no event listener is registered at import time, and
production never calls `activate()`.

WHAT IS COUNTED (all attributable per `verify_and_classify` call, since the
collector is reset by the caller):

* `sql_statements`     - every statement sent through the SQLAlchemy engine
                         (`before_cursor_execute` on the sync engine).
* `physical_connects`  - DBAPI connections actually opened (`connect` pool
                         event). Under `NullPool` this equals checkouts; under
                         `QueuePool` it stays at the pool size once warm.
* `pool_checkouts` /   - `Pool.connect()` calls and the wall time spent
  `pool_wait_seconds`    inside them (for a queue pool this is the checkout
                         wait; for `NullPool` it is the physical connect cost).
* `connector_calls`    - outbound `_get_with_retry` attempts, per source label.
* `rate_limit_wait_s`  - seconds slept inside the per-source token bucket,
                         per source label.
* `semaphore_wait_s`   - total time gather tasks waited for the concurrency
                         semaphore.
* `chunks`             - per-chunk gather seconds and serial
                         (persist+classify) seconds.

The collector deliberately wraps the real objects (`Pool.connect`,
`_TokenBucket.acquire`, `_get_with_retry`) rather than copying their logic,
so what is measured is what runs.
"""
from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_ACTIVE: Optional["PipelineMetrics"] = None


@dataclass
class PipelineMetrics:
    sql_statements: int = 0
    physical_connects: int = 0
    pool_checkouts: int = 0
    pool_wait_seconds: float = 0.0
    pool_wait_max_seconds: float = 0.0
    connector_calls: Dict[str, int] = field(default_factory=dict)
    rate_limit_wait_s: Dict[str, float] = field(default_factory=dict)
    semaphore_wait_s: float = 0.0
    semaphore_wait_max_s: float = 0.0
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    _unhooks: List[Any] = field(default_factory=list, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sql_statements": self.sql_statements,
            "physical_connects": self.physical_connects,
            "pool_checkouts": self.pool_checkouts,
            "pool_wait_seconds": round(self.pool_wait_seconds, 3),
            "pool_wait_max_seconds": round(self.pool_wait_max_seconds, 3),
            "connector_calls": dict(self.connector_calls),
            "connector_calls_total": sum(self.connector_calls.values()),
            "rate_limit_wait_s": {k: round(v, 3) for k, v in self.rate_limit_wait_s.items()},
            "rate_limit_wait_total_s": round(sum(self.rate_limit_wait_s.values()), 3),
            "semaphore_wait_s": round(self.semaphore_wait_s, 3),
            "semaphore_wait_max_s": round(self.semaphore_wait_max_s, 3),
            "chunks": self.chunks,
            "gather_seconds_total": round(sum(c.get("gather_s", 0.0) for c in self.chunks), 3),
            "serial_seconds_total": round(sum(c.get("serial_s", 0.0) for c in self.chunks), 3),
            "errors": list(self.errors),
        }


def active() -> Optional[PipelineMetrics]:
    return _ACTIVE


def activate(engine=None) -> PipelineMetrics:
    """Install the hooks and return the live collector. `engine` is the
    SQLAlchemy *async* engine whose pool/statements should be counted; when
    None, `app.core.database`'s lazily-built engine is used."""
    global _ACTIVE
    if _ACTIVE is not None:
        deactivate()
    m = PipelineMetrics()

    # --- SQL statements + physical connects + pool checkout timing -------
    if engine is None:
        from app.core import database as core_db
        engine = core_db._get_engine()
    sync_engine = engine.sync_engine
    from sqlalchemy import event

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        m.sql_statements += 1

    event.listen(sync_engine, "before_cursor_execute", _before_cursor_execute)
    m._unhooks.append(lambda: event.remove(sync_engine, "before_cursor_execute",
                                           _before_cursor_execute))

    pool = sync_engine.pool

    def _on_connect(dbapi_conn, record):
        m.physical_connects += 1

    event.listen(pool, "connect", _on_connect)
    m._unhooks.append(lambda: event.remove(pool, "connect", _on_connect))

    original_pool_connect = pool.connect

    @functools.wraps(original_pool_connect)
    def _timed_connect(*a, **kw):
        t0 = time.perf_counter()
        try:
            return original_pool_connect(*a, **kw)
        finally:
            dt = time.perf_counter() - t0
            m.pool_checkouts += 1
            m.pool_wait_seconds += dt
            if dt > m.pool_wait_max_seconds:
                m.pool_wait_max_seconds = dt

    pool.connect = _timed_connect  # type: ignore[method-assign]
    m._unhooks.append(lambda: setattr(pool, "connect", original_pool_connect))

    # --- connector calls + rate-limit waits -------------------------------
    from app.Tefca import connectors as c

    original_get = c._get_with_retry

    @functools.wraps(original_get)
    async def _counted_get(url, params, headers, timeout=c.SOURCE_TIMEOUT_SECONDS,
                           source="UNSPECIFIED"):
        m.connector_calls[source] = m.connector_calls.get(source, 0) + 1
        return await original_get(url, params, headers, timeout=timeout, source=source)

    c._get_with_retry = _counted_get
    m._unhooks.append(lambda: setattr(c, "_get_with_retry", original_get))
    # cms_ppef imported the name directly; patch its binding too.
    try:
        from app.Tefca import cms_ppef
        if getattr(cms_ppef, "_get_with_retry", None) is original_get:
            cms_ppef._get_with_retry = _counted_get
            m._unhooks.append(lambda: setattr(cms_ppef, "_get_with_retry", original_get))
    except Exception:  # pragma: no cover - diagnostics must never break the app
        pass

    original_acquire = c._TokenBucket.acquire

    async def _timed_acquire(self):
        t0 = time.perf_counter()
        await original_acquire(self)
        dt = time.perf_counter() - t0
        label = getattr(self, "_metrics_label", None)
        if label is None:
            label = next((k for k, v in c._RATE_LIMITERS.items() if v is self), "UNKNOWN")
            self._metrics_label = label
        m.rate_limit_wait_s[label] = m.rate_limit_wait_s.get(label, 0.0) + dt

    c._TokenBucket.acquire = _timed_acquire  # type: ignore[method-assign]
    m._unhooks.append(lambda: setattr(c._TokenBucket, "acquire", original_acquire))

    _ACTIVE = m
    return m


def deactivate() -> Optional[PipelineMetrics]:
    global _ACTIVE
    m = _ACTIVE
    if m is None:
        return None
    for undo in reversed(m._unhooks):
        try:
            undo()
        except Exception as exc:  # pragma: no cover
            m.errors.append(f"unhook: {type(exc).__name__}: {exc}")
    m._unhooks.clear()
    _ACTIVE = None
    return m


# --- hooks called from arc_pipeline (single `if` when inactive) -------------

def note_semaphore_wait(seconds: float) -> None:
    m = _ACTIVE
    if m is None:
        return
    m.semaphore_wait_s += seconds
    if seconds > m.semaphore_wait_max_s:
        m.semaphore_wait_max_s = seconds


def note_chunk(chunk_start: int, n: int, gather_s: float, serial_s: float) -> None:
    m = _ACTIVE
    if m is None:
        return
    m.chunks.append({"chunk_start": chunk_start, "n": n,
                     "gather_s": round(gather_s, 3), "serial_s": round(serial_s, 3)})
