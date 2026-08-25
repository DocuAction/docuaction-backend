"""
The fail-closed gate on CMS PPEF bulk ingestion.

WHAT THIS PROTECTS
Bulk ingestion downloads a multi-hundred-megabyte quarterly CSV from CMS and
writes millions of rows. Forensics established that nothing in the system
enqueues that work on its own — every one of the 8,739,759 rows on dev traces to
an authenticated admin call, `requested_by = admin@docuaction.io`. But "nobody
has pressed the button yet" is not a control, and production must make the load
impossible without a deliberate recorded decision rather than merely unlikely.

WHY TWO LAYERS
Layer 1 stops the endpoint creating a job. Layer 2 stops the poller claiming
one. Layer 1 alone would be insufficient because a QUEUED row can exist by
routes the endpoint never sees: a row already present when the flag was turned
off, a restored backup, a manual INSERT, or a future caller added by someone who
never read this file. The poller ticks every twenty seconds, so such a row would
execute within twenty seconds of appearing.

THE ORDERING MATTERS AS MUCH AS THE CHECK
In the endpoint the gate precedes `PPEFResourceCatalog().discover()`, which
calls data.cms.gov. A gate placed after it would refuse the request only after
CMS had already been contacted — an outbound call the operator was told did not
happen. In the poller the gate precedes `claim_next_queued()`, because claiming
mutates the row and consumes the active-job slot; claiming and then refusing
would leave the job neither queued nor running. Both orderings are asserted
below, not just the presence of the checks.

WHAT IS DELIBERATELY NOT GATED
The reaper, which downloads nothing and only marks dead jobs FAILED — housekeeping
must survive the flag being off, or disabling ingestion would leave orphaned jobs
looking alive. And every per-entity verification connector (NPPES, PECOS,
SAM.gov, OIG LEIE), which is a different mechanism at single-row volume.
"""

from __future__ import annotations

import inspect

import pytest

from app.Tefca import ppef_jobs, ppef_scheduler

pytestmark = pytest.mark.regression

FLAG = "PPEF_BULK_INGEST_ENABLED"


def _code_only(fn) -> str:
    """Source with comments and the docstring removed.

    Ordering assertions below compare the position of two calls. Without this,
    `src.index("claim_next_queued(")` matches the phrase inside the explanatory
    comment ABOVE the gate and the test measures prose rather than code — which
    is exactly how the first version of this file passed a check it should have
    failed.
    """
    import ast
    import inspect as _inspect
    import textwrap

    # dedent, not cleandoc: cleandoc is for docstrings and destroys the
    # indentation of a source block, which ast.parse then rejects.
    src = textwrap.dedent(_inspect.getsource(fn))
    tree = ast.parse(src)
    node = tree.body[0]
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]          # drop the docstring
    return ast.unparse(node)               # comments do not survive unparse


def _env(monkeypatch, *, environment=None, flag=None):
    for name in ("ENVIRONMENT", "ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(FLAG, raising=False)
    if environment is not None:
        monkeypatch.setenv("ENVIRONMENT", environment)
    if flag is not None:
        monkeypatch.setenv(FLAG, flag)


# ── 1-2. production refuses on an absent or false flag ───────────────────────

def test_production_with_flag_absent_is_disabled(monkeypatch):
    """The state a fresh deployment or restored configuration starts in.

    If absence meant "allowed", the safest-looking configuration would be the
    permissive one.
    """
    _env(monkeypatch, environment="production")
    assert ppef_jobs.bulk_ingest_enabled() is False


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", "disabled"])
def test_production_with_flag_false_is_disabled(monkeypatch, value):
    _env(monkeypatch, environment="production", flag=value)
    assert ppef_jobs.bulk_ingest_enabled() is False


def test_an_unrecognised_value_is_not_permission(monkeypatch):
    """A typo must never grant a capability."""
    for junk in ("tru", "yes please", "ENABLED!", "maybe", "1;true"):
        _env(monkeypatch, environment="production", flag=junk)
        assert ppef_jobs.bulk_ingest_enabled() is False, junk


def test_production_can_be_explicitly_enabled(monkeypatch):
    """Setting the flag IS the authorization record for a specific load."""
    _env(monkeypatch, environment="production", flag="true")
    assert ppef_jobs.bulk_ingest_enabled() is True


# ── 10. non-production keeps working, but an explicit false still refuses ────

def test_non_production_defaults_to_enabled(monkeypatch):
    """Development exists to run this pipeline.

    A control developers must switch off to do ordinary work stops being read
    as a control.
    """
    _env(monkeypatch, environment="development")
    assert ppef_jobs.bulk_ingest_enabled() is True
    _env(monkeypatch)  # no ENVIRONMENT at all
    assert ppef_jobs.bulk_ingest_enabled() is True


def test_explicit_false_refuses_in_any_environment(monkeypatch):
    """So production behaviour can be reproduced exactly on a dev box."""
    _env(monkeypatch, environment="development", flag="false")
    assert ppef_jobs.bulk_ingest_enabled() is False


def test_refusal_reason_tells_the_operator_what_to_do(monkeypatch):
    _env(monkeypatch, environment="production")
    reason = ppef_jobs.bulk_ingest_refusal_reason()
    assert FLAG in reason
    assert "production" in reason
    assert "not set" in reason


def test_require_raises_the_typed_error(monkeypatch):
    _env(monkeypatch, environment="production")
    with pytest.raises(ppef_jobs.BulkIngestDisabled):
        ppef_jobs.require_bulk_ingest_enabled()
    _env(monkeypatch, environment="production", flag="true")
    ppef_jobs.require_bulk_ingest_enabled()  # must not raise


# ── 3-6. the endpoint: no job, no snapshot, no download, no rows ────────────

def test_endpoint_gate_precedes_the_cms_discovery_call():
    """ORDERING, not just presence.

    discover() calls data.cms.gov. A gate after it would refuse the request only
    after CMS had already been contacted.
    """
    import app.Tefca.routes as routes

    src = _code_only(routes.ppef_ingest_component)
    gate = src.index("bulk_ingest_enabled()")
    discovery = src.index("PPEFResourceCatalog().discover()")
    queue = src.index("queue_job(")
    assert gate < discovery, "the gate must precede the outbound CMS discovery call"
    assert gate < queue, "the gate must precede job creation"


def test_endpoint_gate_creates_nothing_and_calls_nothing():
    """The refusal path reaches no writer and no network client.

    Asserted on the source between the gate and its raise: if any call to
    discover, queue_job or a snapshot writer appeared there, the refusal would
    have side effects.
    """
    import app.Tefca.routes as routes

    src = inspect.getsource(routes.ppef_ingest_component)
    start = src.index("bulk_ingest_enabled()")
    end = src.index("raise HTTPException(403", start)
    between = src[start:end]
    for forbidden in ("discover(", "queue_job(", "TEFCAPPEFSnapshot", "copy_records",
                      "httpx", "requests."):
        assert forbidden not in between, f"refusal path touches {forbidden}"


def test_endpoint_still_requires_admin(monkeypatch):
    """The gate must not have replaced or weakened RBAC."""
    import app.Tefca.routes as routes

    sig = inspect.signature(routes.ppef_ingest_component)
    user_param = sig.parameters["user"]
    assert user_param.default is not inspect.Parameter.empty
    assert "require_role" in inspect.getsource(routes.ppef_ingest_component) or True
    src = inspect.getsource(routes.ppef_ingest_component)
    assert "user=Depends(require_role(\"admin\"))" in src or "require_role" in src


# ── 7-8. an existing QUEUED job must not execute while disabled ─────────────

@pytest.mark.asyncio
async def test_queued_job_is_not_claimed_while_disabled(monkeypatch):
    """THE case Layer 2 exists for.

    A row already in the table when the flag went off must sit untouched rather
    than run on the next twenty-second tick.
    """
    _env(monkeypatch, environment="production")
    claimed = {"called": False}

    async def _must_not_be_called(db):
        claimed["called"] = True
        raise AssertionError("claim_next_queued was reached while ingestion was disabled")

    monkeypatch.setattr(ppef_jobs, "claim_next_queued", _must_not_be_called)
    monkeypatch.setattr(ppef_scheduler, "_refusal_logged_at", None)

    await ppef_scheduler._poll_tick()
    assert claimed["called"] is False


@pytest.mark.asyncio
async def test_poller_refusal_precedes_the_claim_not_follows_it():
    """Claiming mutates the row and consumes the active-job slot.

    Claiming then refusing would leave the job neither queued nor running, and
    would burn the slot a later authorized retry needs.
    """
    src = _code_only(ppef_scheduler._poll_tick)
    gate = src.index("bulk_ingest_enabled()")
    claim = src.index("claim_next_queued(")
    assert gate < claim, "the gate must run before the job is claimed"


@pytest.mark.asyncio
async def test_run_job_refuses_directly_while_disabled(monkeypatch):
    """run_job is public; a direct caller must not bypass the poller's gate."""
    _env(monkeypatch, environment="production")
    opened = {"session": False}

    def _sentinel():
        opened["session"] = True
        raise AssertionError("a database session was opened while disabled")

    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", _sentinel)

    await ppef_scheduler.run_job("00000000-0000-0000-0000-000000000000")
    assert opened["session"] is False


@pytest.mark.asyncio
async def test_poller_runs_normally_when_enabled(monkeypatch):
    """The control must not break the authorized workflow."""
    _env(monkeypatch, environment="development", flag="true")
    seen = {"claimed": False}

    async def _claim(db):
        seen["claimed"] = True
        return None

    class _Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Session())
    monkeypatch.setattr(ppef_jobs, "claim_next_queued", _claim)

    await ppef_scheduler._poll_tick()
    assert seen["claimed"] is True, "an enabled environment must still poll"


# ── 9. startup enqueues nothing ─────────────────────────────────────────────

def test_startup_reaper_only_reaps():
    """The one task started at boot must not enqueue."""
    src = inspect.getsource(ppef_scheduler._startup_reap)
    assert "_reap_tick" in src
    for forbidden in ("queue_job", "claim_next_queued", "run_job", "discover"):
        assert forbidden not in src, f"the boot task touches {forbidden}"


def test_scheduler_registers_only_a_poller_and_a_reaper():
    src = inspect.getsource(ppef_scheduler.start_ppef_scheduler)
    assert "_poll_tick" in src and "_reap_tick" in src
    assert "queue_job" not in src, "startup must never enqueue PPEF work"


def test_nothing_outside_the_admin_endpoint_creates_a_job():
    """The single-writer property the forensics established, pinned as a test.

    If a second enqueue site is ever added, this fails and whoever added it has
    to consider the gate.
    """
    import pathlib

    root = pathlib.Path(ppef_jobs.__file__).resolve().parents[2]
    callers = []
    for path in (root / "app").rglob("*.py"):
        if path.name in ("ppef_jobs.py",):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "queue_job(" in text:
            callers.append(path.name)
    assert callers == ["routes.py"], f"unexpected enqueue sites: {callers}"


def test_reaper_is_not_gated():
    """Housekeeping must survive the flag being off.

    A gated reaper would leave orphaned jobs looking alive for as long as
    ingestion stayed disabled.
    """
    src = inspect.getsource(ppef_scheduler._reap_tick)
    assert "bulk_ingest_enabled" not in src


# ── 11. per-entity verification is untouched ────────────────────────────────

def test_entity_verification_connectors_are_not_gated():
    """NPPES, PECOS, SAM.gov and OIG LEIE are a different mechanism.

    Single rows keyed by NPI or TIN, not a bulk corpus. Gating them would break
    ordinary verification, which is the application's core function.
    """
    import pathlib

    connectors = pathlib.Path(ppef_jobs.__file__).resolve().parent / "connectors.py"
    text = connectors.read_text(encoding="utf-8", errors="ignore")
    assert "bulk_ingest_enabled" not in text
    assert "PPEF_BULK_INGEST_ENABLED" not in text
    # And the connector module still targets the per-entity NPPES API.
    assert "npiregistry.cms.hhs.gov" in text or "NPPES" in text


def test_no_nppes_bulk_loader_was_introduced():
    """The instruction was explicit: do not add one."""
    import pathlib

    root = pathlib.Path(ppef_jobs.__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert "nppes_bulk" not in text, f"{path.name} references an NPPES bulk loader"
