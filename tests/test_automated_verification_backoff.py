"""Automated verification coverage: adaptive backoff for the scheduler tick
(2026-09-25 follow-up to the load-protection remediation).

Observed on DEV (a 1-vCPU burstable database): after the bounded lookup and
its 15 s statement timeout shipped, EVERY 15-second tick's lookup still ran
into the timeout and was cancelled, so a bounded query at a fixed cadence
kept the database at ~100% CPU with no burst credits left. Pinned here:

  * a timed-out (or raising) lookup arms a cooldown; ticks during the
    cooldown return before touching the engine — no connection, no SQL;
  * the cooldown doubles per consecutive failure from the base to the cap
    (env-configurable, clamped) and resets on the first lookup that
    completes, whether it found a candidate or not;
  * a duty-cycle guard rests the database after any tick whose wall time
    exceeded half the interval, without ever shortening a timeout cooldown;
  * over a simulated hour in which every lookup times out, the database is
    asked a handful of times, not 240;
  * the admin health payload carries the metrics with safe types only.

All timing uses an injected fake clock; nothing here sleeps.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from rce_traceability_support import rolled_back_db  # noqa: F401 - fixture

from app.tefca_registry.rce import automated_verification as av

pytestmark = pytest.mark.asyncio

_EPOCH = 1_800_000_000.0  # any fixed instant; rendered as ISO-8601 by the status


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self, start: float = _EPOCH):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    """Answers the advisory lock/unlock (and any other statement) with True,
    and records every statement it was asked to run."""

    def __init__(self, engine, lock_free: bool):
        self._engine = engine
        self._lock_free = lock_free

    async def execute(self, stmt, params=None):
        self._engine.statements.append(str(stmt))
        if "pg_try_advisory_lock" in str(stmt):
            return _Scalar(self._lock_free)
        return _Scalar(True)

    async def commit(self):
        return None


class FakeEngine:
    def __init__(self, lock_free: bool = True):
        self.connections = 0
        self.statements = []
        self.lock_free = lock_free

    def connect(self):
        engine = self

        @asynccontextmanager
        async def _cm():
            engine.connections += 1
            yield _FakeConn(engine, engine.lock_free)

        return _cm()


class _FakeSession:
    """Stands in for `AsyncSession(bind=conn, ...)`; the lookup and batch are
    patched, so the session itself is never asked to run anything."""

    def __init__(self, bind=None, **kw):
        self.bind = bind

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def harness(monkeypatch):
    """Feature on, state reset, engine and session faked, default backoff env."""
    monkeypatch.setenv(av.ENV_FLAG, "true")
    monkeypatch.delenv(av.ENV_BACKOFF_BASE_SECONDS, raising=False)
    monkeypatch.delenv(av.ENV_BACKOFF_MAX_SECONDS, raising=False)
    av.reset_coverage_scheduler_state()
    engine = FakeEngine()
    clock = FakeClock()
    with patch("app.core.database._get_engine", return_value=engine), \
            patch.object(av, "AsyncSession", _FakeSession):
        yield engine, clock
    av.reset_coverage_scheduler_state()


def _timing_out(clock: FakeClock, seconds: float):
    """A lookup that keeps the database busy for `seconds` and is cancelled."""

    async def _lookup(db, **kw):
        clock.advance(seconds)
        raise av.CandidateLookupTimeout(int(seconds * 1000))

    return AsyncMock(side_effect=_lookup)


def _status():
    return av.coverage_scheduler_status()


# ── (a) a timed-out lookup arms a cooldown; ticks in cooldown run no SQL ─────

async def test_timed_out_lookup_arms_cooldown_and_later_ticks_run_no_sql(harness):
    engine, clock = harness
    lookup = _timing_out(clock, 15)
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup), \
            patch.object(av, "run_coverage_batch", new=AsyncMock()) as batch:
        await av._coverage_tick(now=clock)
        assert lookup.await_count == 1 and engine.connections == 1
        assert batch.await_count == 0
        s = _status()
        assert s["last_outcome"] == av.TICK_TIMEOUT
        assert s["consecutive_timeouts"] == 1
        assert s["last_lookup_ms"] == 15_000
        armed_until = av._state.next_allowed_at
        assert armed_until == pytest.approx(clock.t + av.DEFAULT_BACKOFF_BASE_SECONDS)

        # During the cooldown the engine is never even asked for a connection.
        def _never():
            raise AssertionError("a tick in cooldown must not touch the engine")

        with patch("app.core.database._get_engine", side_effect=_never):
            for _ in range(10):
                clock.advance(av.TICK_INTERVAL_SECONDS)
                await av._coverage_tick(now=clock)
        assert lookup.await_count == 1, "no lookup ran during the cooldown"
        assert engine.connections == 1 and batch.await_count == 0
        s = _status()
        assert s["ticks_skipped_backoff"] == 10
        assert s["last_outcome"] == av.TICK_BACKOFF
        assert s["consecutive_timeouts"] == 1
        assert av._state.next_allowed_at == armed_until, "skipping never moves the deadline"

        # The first tick at or after the deadline runs again.
        clock.t = armed_until
        await av._coverage_tick(now=clock)
        assert lookup.await_count == 2 and engine.connections == 2


async def test_a_lookup_that_raises_also_enters_backoff_and_the_tick_survives(harness, caplog):
    engine, clock = harness
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(side_effect=RuntimeError("simulated lookup failure"))) as lookup, \
            patch.object(av, "run_coverage_batch", new=AsyncMock()), \
            caplog.at_level("INFO", logger=av.logger.name):
        await av._coverage_tick(now=clock)  # never raises
        clock.advance(av.TICK_INTERVAL_SECONDS)
        await av._coverage_tick(now=clock)
    assert lookup.await_count == 1
    s = _status()
    assert s["last_outcome"] == av.TICK_BACKOFF and s["consecutive_timeouts"] == 1
    assert any("tick error" in r.getMessage() and r.levelname == "ERROR" for r in caplog.records)
    entering = [r for r in caplog.records
                if "entering backoff" in r.getMessage() and r.levelname == "INFO"]
    assert len(entering) == 1, "one INFO line when entering backoff"
    # The advisory lock was still released on the way out.
    assert any("pg_advisory_unlock" in stmt for stmt in engine.statements)


async def test_a_tick_that_loses_the_lock_leaves_backoff_state_alone(harness):
    engine, clock = harness
    engine.lock_free = False
    with patch.object(av, "_next_delivery_needing_coverage", new=AsyncMock()) as lookup:
        await av._coverage_tick(now=clock)
    assert lookup.await_count == 0
    s = _status()
    assert s["last_outcome"] == av.TICK_SKIPPED_LOCK
    assert s["consecutive_timeouts"] == 0 and s["next_allowed_at"] is None


# ── (b) exponential growth and cap ───────────────────────────────────────────

def test_backoff_seconds_doubles_from_base_to_cap(monkeypatch):
    monkeypatch.delenv(av.ENV_BACKOFF_BASE_SECONDS, raising=False)
    monkeypatch.delenv(av.ENV_BACKOFF_MAX_SECONDS, raising=False)
    assert av.backoff_base_seconds() == av.DEFAULT_BACKOFF_BASE_SECONDS == 300
    assert av.backoff_max_seconds() == av.DEFAULT_BACKOFF_MAX_SECONDS == 3600
    assert av.backoff_seconds(0) == 0
    assert [av.backoff_seconds(n) for n in range(1, 8)] == [300, 600, 1200, 2400, 3600, 3600, 3600]
    assert av.backoff_seconds(10_000) == 3600  # no overflow, still the cap


def test_backoff_env_is_read_and_clamped(monkeypatch):
    monkeypatch.setenv(av.ENV_BACKOFF_BASE_SECONDS, "60")
    monkeypatch.setenv(av.ENV_BACKOFF_MAX_SECONDS, "600")
    assert [av.backoff_seconds(n) for n in range(1, 6)] == [60, 120, 240, 480, 600]
    monkeypatch.setenv(av.ENV_BACKOFF_MAX_SECONDS, "10")        # cap below base: base wins
    assert av.backoff_max_seconds() == 60 and av.backoff_seconds(5) == 60
    monkeypatch.setenv(av.ENV_BACKOFF_BASE_SECONDS, "0")        # clamped up
    assert av.backoff_base_seconds() == av.MIN_BACKOFF_SECONDS
    monkeypatch.setenv(av.ENV_BACKOFF_BASE_SECONDS, "99999999")  # clamped down
    assert av.backoff_base_seconds() == av.MAX_BACKOFF_SECONDS
    monkeypatch.setenv(av.ENV_BACKOFF_BASE_SECONDS, "soon")      # not an int: default
    assert av.backoff_base_seconds() == av.DEFAULT_BACKOFF_BASE_SECONDS


async def test_consecutive_timeouts_grow_the_cooldown_through_ticks(harness):
    engine, clock = harness
    lookup = _timing_out(clock, 0)
    observed = []
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup):
        for _ in range(7):
            await av._coverage_tick(now=clock)
            observed.append(round(av._state.next_allowed_at - clock.t))
            clock.t = av._state.next_allowed_at  # jump to the deadline
    assert observed == [300, 600, 1200, 2400, 3600, 3600, 3600]
    assert _status()["consecutive_timeouts"] == 7 and lookup.await_count == 7


# ── (c) reset on success ─────────────────────────────────────────────────────

@pytest.mark.parametrize("found", [None, uuid.UUID(int=1)], ids=["no_candidate", "batch"])
async def test_a_completed_lookup_resets_the_backoff(harness, caplog, found):
    engine, clock = harness
    with patch.object(av, "_next_delivery_needing_coverage", new=_timing_out(clock, 0)):
        await av._coverage_tick(now=clock)
        clock.t = av._state.next_allowed_at
        await av._coverage_tick(now=clock)
    assert _status()["consecutive_timeouts"] == 2
    clock.t = av._state.next_allowed_at

    batch_result = {"covered": 1, "eligible": 2, "remaining": 1}
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value=found)) as lookup, \
            patch.object(av, "run_coverage_batch", new=AsyncMock(return_value=batch_result)) as batch, \
            caplog.at_level("INFO", logger=av.logger.name):
        await av._coverage_tick(now=clock)
    assert lookup.await_count == 1
    assert batch.await_count == (1 if found is not None else 0)
    s = _status()
    assert s["consecutive_timeouts"] == 0 and s["next_allowed_at"] is None
    assert s["last_outcome"] == (av.TICK_BATCH if found is not None else av.TICK_NO_CANDIDATE)
    leaving = [r for r in caplog.records
               if "leaving backoff" in r.getMessage() and r.levelname == "INFO"]
    assert len(leaving) == 1, "one INFO line when leaving backoff"

    # The next tick runs immediately, and a fresh failure starts at the base again.
    clock.advance(av.TICK_INTERVAL_SECONDS)
    with patch.object(av, "_next_delivery_needing_coverage", new=_timing_out(clock, 0)) as again:
        await av._coverage_tick(now=clock)
    assert again.await_count == 1
    assert round(av._state.next_allowed_at - clock.t) == av.DEFAULT_BACKOFF_BASE_SECONDS


# ── (d) duty-cycle guard ─────────────────────────────────────────────────────

def _batch_taking(clock: FakeClock, seconds: float):
    async def _batch(db, intake_id, **kw):
        clock.advance(seconds)
        return {"covered": 100, "eligible": 25_000, "remaining": 24_900}

    return AsyncMock(side_effect=_batch)


async def test_a_long_tick_is_followed_by_a_rest_of_twice_its_wall_time(harness):
    engine, clock = harness
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value=uuid.UUID(int=1))) as lookup, \
            patch.object(av, "run_coverage_batch", new=_batch_taking(clock, 10)):
        await av._coverage_tick(now=clock)            # 10 s > 50% of 15 s
        ended = clock.t
        s = _status()
        assert s["last_outcome"] == av.TICK_BATCH and s["last_batch_ms"] == 10_000
        assert s["consecutive_timeouts"] == 0, "a long tick is not a failure"
        assert av._state.next_allowed_at == pytest.approx(ended + 20)

        clock.advance(av.TICK_INTERVAL_SECONDS)       # +15 s: still resting
        await av._coverage_tick(now=clock)
        assert lookup.await_count == 1 and _status()["ticks_skipped_backoff"] == 1
        clock.advance(av.TICK_INTERVAL_SECONDS)       # +30 s: rest over
        await av._coverage_tick(now=clock)
        assert lookup.await_count == 2


async def test_a_short_tick_sets_no_rest_and_a_moderately_long_one_rests_one_interval(harness):
    engine, clock = harness
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value=uuid.UUID(int=1))), \
            patch.object(av, "run_coverage_batch", new=_batch_taking(clock, 2)):
        await av._coverage_tick(now=clock)            # 2 s: well under half the interval
    assert av._state.next_allowed_at is None
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value=uuid.UUID(int=1))), \
            patch.object(av, "run_coverage_batch", new=_batch_taking(clock, 6)):
        await av._coverage_tick(now=clock)            # 6 s <= 7.5 s: still no rest
    assert av._state.next_allowed_at is None
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value=uuid.UUID(int=1))), \
            patch.object(av, "run_coverage_batch", new=_batch_taking(clock, 8)):
        await av._coverage_tick(now=clock)            # 8 s > 7.5 s: rest max(15, 16) = 16 s
    assert av._state.next_allowed_at == pytest.approx(clock.t + 16)


async def test_the_duty_cycle_rest_never_shortens_a_timeout_cooldown(harness):
    engine, clock = harness
    with patch.object(av, "_next_delivery_needing_coverage", new=_timing_out(clock, 15)):
        await av._coverage_tick(now=clock)            # 15 s wall: guard would rest 30 s
    assert av._state.next_allowed_at == pytest.approx(clock.t + av.DEFAULT_BACKOFF_BASE_SECONDS)


# ── (e) an hour of timeouts: a handful of lookups, not 240 ───────────────────

async def test_forty_ticks_of_timeouts_run_at_most_two_lookups(harness):
    engine, clock = harness
    lookup = _timing_out(clock, 15)
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup):
        for _ in range(40):                            # 40 x 15 s = 10 simulated minutes
            await av._coverage_tick(now=clock)
            clock.advance(av.TICK_INTERVAL_SECONDS)
    assert lookup.await_count <= 2, lookup.await_count
    assert engine.connections == lookup.await_count
    assert _status()["ticks_skipped_backoff"] == 40 - lookup.await_count


async def test_an_hour_of_timeouts_asks_the_database_at_most_four_times(harness):
    """The proof for the 1-vCPU case: with the defaults (base 300 s, cap
    3600 s) and a lookup that burns its full 15 s timeout every time, one
    simulated hour of 15-second ticks reaches the database at most four
    times (at ~0, ~5, ~15 and ~35 minutes) and keeps it busy for about a
    minute, against 240 lookups and 60 busy minutes before this change."""
    engine, clock = harness
    lookup = _timing_out(clock, 15)
    start = clock.t
    busy = 0.0
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup):
        while clock.t - start < 3600:
            before = lookup.await_count
            t0 = clock.t
            await av._coverage_tick(now=clock)
            if lookup.await_count > before:
                busy += clock.t - t0
            clock.t = t0 + av.TICK_INTERVAL_SECONDS
    assert lookup.await_count <= 4, lookup.await_count
    assert engine.connections == lookup.await_count
    assert busy <= 4 * 15, busy                        # <= ~1.7% of the hour on the database
    s = _status()
    assert s["consecutive_timeouts"] == lookup.await_count
    assert s["ticks_skipped_backoff"] == 240 - lookup.await_count
    assert av.backoff_seconds(s["consecutive_timeouts"] + 1) == av.DEFAULT_BACKOFF_MAX_SECONDS or \
        s["consecutive_timeouts"] < 5


# ── (f) admin health surface ─────────────────────────────────────────────────

_SAFE_TYPES = (bool, int, str, type(None))
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_METRIC_KEYS = {"enabled", "last_tick_at", "last_outcome", "consecutive_timeouts",
                "next_allowed_at", "last_lookup_ms", "last_batch_ms", "ticks_skipped_backoff"}


def _assert_safe_metrics(block):
    assert _METRIC_KEYS <= set(block), set(block)
    for key, value in block.items():
        assert isinstance(value, _SAFE_TYPES) and not isinstance(value, float), (key, value)
        assert not _UUID_RE.search(str(value)), key
        assert "select" not in str(value).lower(), key
    assert isinstance(block["enabled"], bool)
    assert block["last_outcome"] in {av.TICK_IDLE, av.TICK_BATCH, av.TICK_NO_CANDIDATE,
                                     av.TICK_TIMEOUT, av.TICK_SKIPPED_LOCK, av.TICK_BACKOFF,
                                     av.TICK_ERROR}
    for key in ("consecutive_timeouts", "ticks_skipped_backoff"):
        assert isinstance(block[key], int) and not isinstance(block[key], bool)
    for key in ("last_tick_at", "next_allowed_at"):
        assert block[key] is None or block[key].endswith("+00:00"), block[key]


async def test_status_after_a_timeout_and_a_batch_is_safe_and_timestamped(harness):
    engine, clock = harness
    with patch.object(av, "_next_delivery_needing_coverage", new=_timing_out(clock, 15)):
        await av._coverage_tick(now=clock)
    s = _status()
    _assert_safe_metrics(s)
    assert s["enabled"] is True
    assert s["last_tick_at"] == "2027-01-15T08:00:00+00:00"
    assert s["next_allowed_at"] == "2027-01-15T08:05:15+00:00"
    assert s["backoff_base_seconds"] == 300 and s["backoff_max_seconds"] == 3600
    assert s["tick_interval_seconds"] == 15

    clock.t = av._state.next_allowed_at
    with patch.object(av, "_next_delivery_needing_coverage",
                      new=AsyncMock(return_value=uuid.UUID(int=7))), \
            patch.object(av, "run_coverage_batch", new=_batch_taking(clock, 1)):
        await av._coverage_tick(now=clock)
    s = _status()
    _assert_safe_metrics(s)                            # the intake id never surfaces
    assert s["last_outcome"] == av.TICK_BATCH and s["last_batch_ms"] == 1000


def test_status_is_inert_by_default(monkeypatch):
    monkeypatch.delenv(av.ENV_FLAG, raising=False)
    av.reset_coverage_scheduler_state()
    s = _status()
    _assert_safe_metrics(s)
    assert s == {**s, "enabled": False, "last_tick_at": None, "last_outcome": av.TICK_IDLE,
                 "consecutive_timeouts": 0, "next_allowed_at": None, "last_lookup_ms": None,
                 "last_batch_ms": None, "ticks_skipped_backoff": 0}


class _User:
    def __init__(self, role):
        self.id = str(uuid.uuid4()); self.email = f"{role}@test.local"; self.role = role
        self.is_active = True; self.status = "active"; self.tokens_revoked_at = None


class _Result:
    def __init__(self, user): self._user = user
    def scalar_one_or_none(self): return self._user
    def scalar(self): return None


class _Session:
    def __init__(self, user): self._user = user
    async def execute(self, *a, **k): return _Result(self._user)
    async def commit(self): return None
    async def rollback(self): return None
    async def close(self): return None
    def add(self, *a, **k): return None


@pytest.fixture
def as_admin(client):
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app

    user = _User("admin")

    async def _override():
        yield _Session(user)

    app.dependency_overrides[get_db] = _override
    token = create_access_token({"sub": user.id, "role": "admin"}, is_admin=True)
    try:
        yield lambda path: client.get(path, headers={"Authorization": f"Bearer {token}"})
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_admin_health_carries_the_automated_coverage_metrics(as_admin, monkeypatch):
    monkeypatch.delenv(av.ENV_FLAG, raising=False)
    av.reset_coverage_scheduler_state()
    r = as_admin("/api/admin/health")
    assert r.status_code == 200, r.text
    block = r.json()["automated_coverage"]
    _assert_safe_metrics(block)
    assert block["enabled"] is False and block["last_outcome"] == av.TICK_IDLE
    lowered = r.text.lower()
    for leak in ("postgresql://", "postgres://", "statement_timeout", "rce_delivery_jobs"):
        assert leak not in lowered


def test_admin_health_never_fails_because_of_the_coverage_block(as_admin):
    with patch("app.tefca_registry.rce.automated_verification.coverage_scheduler_status",
               side_effect=RuntimeError("simulated")):
        r = as_admin("/api/admin/health")
    assert r.status_code == 200, r.text
    assert r.json()["automated_coverage"] == {"enabled": None, "error_class": "RuntimeError"}


# ── database: the real lookup raises on request ──────────────────────────────

async def test_a_cancelled_lookup_raises_only_when_asked_to(rolled_back_db, monkeypatch):
    from sqlalchemy import text

    db = rolled_back_db
    monkeypatch.setattr(av, "_ONE_DELIVERY_NEEDING_COVERAGE_SQL",
                        "SELECT pg_sleep(5), CAST(:job_limit AS int)")
    with pytest.raises(av.CandidateLookupTimeout):
        await av._next_delivery_needing_coverage(db, timeout_ms=1000, raise_on_timeout=True)
    # The default contract is unchanged: None, session usable.
    assert await av._next_delivery_needing_coverage(db, timeout_ms=1000) is None
    assert (await db.execute(text("SELECT 1"))).scalar() == 1
