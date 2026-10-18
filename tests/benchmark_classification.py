"""Classification benchmark harness for `verify_and_classify` (Lane P, 2026-10-03).

NOT collected by a normal `pytest` run (the filename has no `test_` prefix);
run it explicitly:

    python -m pytest tests/benchmark_classification.py -s -p no:cacheprovider

It has to be a pytest test (not a plain script) because the app's SQLAlchemy
mapper registry only configures correctly when the full import graph loads
through `tests/conftest.py` -- the same reason every earlier benchmark in
this project was a pytest file.

Everything is driven by env vars so one file covers every variant:

  BENCH_N                 entities to seed (default 2500); ignored when BENCH_INTAKE_ID set
  BENCH_INTAKE_ID         reuse an already-promoted delivery (a legitimate repeat cycle)
  BENCH_CHUNK_SIZE        override arc_pipeline._GATHER_CHUNK_SIZE (default: code value)
  BENCH_CONCURRENCY       override arc_pipeline._EVIDENCE_GATHER_CONCURRENCY
  BENCH_POOL              null (conftest default, the historical harness) | queue
  BENCH_POOL_SIZE         queue pool_size (default 5, production)
  BENCH_POOL_OVERFLOW     queue max_overflow (default 10, production)
  BENCH_CONNECTORS        live (real NPPES/CMS, SAM keyless) | mock (canned, no network)
  BENCH_MOCK_LATENCY_S    sleep per mocked outbound call (default 0.05)
  BENCH_GENERATE_REPORT   1 -> also time build_dataset() and generate_report()
  BENCH_RESULT_JSON       where to write the result (default PERF_LP_RESULT.json)
  BENCH_PROGRESS_JSONL    phase log (default PERF_LP_PROGRESS.jsonl)
  BENCH_PID_FILE          written at start so scripts/perf/rss_sampler.py can attach
  BENCH_LABEL             free text copied into the result
  BENCH_PREWARM_LEIE      1 (default) -> load the LEIE CSV before timing classify, recorded separately

The result JSON records population, configuration, stage durations,
throughput, self-reported AND external peak RSS (the latter filled in by the
sampler's CSV if BENCH_RSS_CSV points at it), the pipeline_metrics counters
(SQL statements, physical connects, pool checkouts/waits, connector calls,
rate-limit waits, semaphore waits, per-chunk gather/serial seconds), and a
pointer to the equivalence proof (tests/test_chunked_gather_correctness.py).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time

import pytest

from test_review_id_concurrency import _seed_promoted_delivery, _promoted_refs

pytestmark = pytest.mark.asyncio


def _env(name: str, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


class _RssSampler:
    """Self-reported RSS sampling on a daemon thread (1 s)."""

    def __init__(self):
        import psutil
        self._p = psutil.Process()
        self.peak = 0.0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, self._p.memory_info().rss / 1e6)
            time.sleep(1.0)

    def start(self):
        self._t.start()

    def stop(self):
        self._stop.set()
        self._t.join(timeout=3)
        self.peak = max(self.peak, self._p.memory_info().rss / 1e6)


def _install_mock_connectors(latency_s: float):
    """Replace `_get_with_retry` in both modules that bind it with a canned
    responder. Bypasses the token bucket on purpose (it lives inside the real
    helper) -- this mode isolates DB/pool/chunk overhead from network and
    pacing. Returns an undo callable."""
    import httpx
    from app.Tefca import connectors as c
    from app.Tefca import cms_ppef

    original = c._get_with_retry
    calls = {"n": 0}

    async def _mock_get(url, params, headers, timeout=c.SOURCE_TIMEOUT_SECONDS,
                        source="UNSPECIFIED"):
        calls["n"] += 1
        if latency_s:
            await asyncio.sleep(latency_s)
        req = httpx.Request("GET", url, params=params)
        if source == "NPPES":
            body = {"result_count": 0, "results": []}
        elif source == "CMS_PPEF":
            body = []
        else:
            body = {}
        return httpx.Response(200, json=body, request=req)

    c._get_with_retry = _mock_get
    cms_bound = getattr(cms_ppef, "_get_with_retry", None) is original
    if cms_bound:
        cms_ppef._get_with_retry = _mock_get

    def undo():
        c._get_with_retry = original
        if cms_bound:
            cms_ppef._get_with_retry = original

    return undo, calls


def _install_queue_pool(pool_size: int, max_overflow: int):
    """Swap app.core.database's engine for a real QueuePool engine (the
    production shape) for the duration of the benchmark. conftest replaced the
    pool with NullPool at import; this restores a production-like pool."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core import database as core_db

    old_engine, old_maker = core_db._engine, core_db._session_maker
    url = core_db._normalize_url(os.environ["DATABASE_URL"])
    new_engine = create_async_engine(url, echo=False, pool_size=pool_size,
                                     max_overflow=max_overflow, pool_pre_ping=True)
    core_db._engine, core_db._session_maker = new_engine, None

    async def undo():
        await new_engine.dispose()
        core_db._engine, core_db._session_maker = old_engine, old_maker

    return new_engine, undo


async def test_benchmark_classification(db_required, monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    from app.core import database as core_db
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import arc_pipeline, pipeline_metrics

    n = int(_env("BENCH_N", 2500))
    intake_id = _env("BENCH_INTAKE_ID")
    chunk = _env("BENCH_CHUNK_SIZE")
    conc = _env("BENCH_CONCURRENCY")
    pool_mode = _env("BENCH_POOL", "null")
    pool_size = int(_env("BENCH_POOL_SIZE", 5))
    pool_overflow = int(_env("BENCH_POOL_OVERFLOW", 10))
    connectors = _env("BENCH_CONNECTORS", "live")
    mock_latency = float(_env("BENCH_MOCK_LATENCY_S", 0.05))
    do_report = _env("BENCH_GENERATE_REPORT", "0") == "1"
    result_path = _env("BENCH_RESULT_JSON", "PERF_LP_RESULT.json")
    progress_path = _env("BENCH_PROGRESS_JSONL", "PERF_LP_PROGRESS.jsonl")
    pid_file = _env("BENCH_PID_FILE")
    rss_csv = _env("BENCH_RSS_CSV")

    if pid_file:
        with open(pid_file, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    sampler = _RssSampler()
    sampler.start()

    def progress(phase, **kw):
        rec = {"phase": phase, "t": time.time(), "rss_mb": round(sampler._p.memory_info().rss / 1e6, 1), **kw}
        with open(progress_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"[bench] {rec}", file=sys.stderr, flush=True)

    if chunk:
        monkeypatch.setattr(arc_pipeline, "_GATHER_CHUNK_SIZE", int(chunk))
    if conc:
        monkeypatch.setattr(arc_pipeline, "_EVIDENCE_GATHER_CONCURRENCY", int(conc))

    undo_mock, mock_calls = (None, None)
    if connectors == "mock":
        undo_mock, mock_calls = _install_mock_connectors(mock_latency)

    engine_for_metrics = None
    undo_pool = None
    if pool_mode == "queue":
        engine_for_metrics, undo_pool = _install_queue_pool(pool_size, pool_overflow)

    config = {
        "chunk_size": arc_pipeline._GATHER_CHUNK_SIZE,
        "concurrency": arc_pipeline._EVIDENCE_GATHER_CONCURRENCY,
        "pool": pool_mode,
        "pool_size": pool_size if pool_mode == "queue" else None,
        "max_overflow": pool_overflow if pool_mode == "queue" else None,
        "pool_class_in_effect": type(core_db._get_engine().sync_engine.pool).__name__,
        "connectors": connectors,
        "mock_latency_s": mock_latency if connectors == "mock" else None,
        "sam_api_key_set": bool(os.environ.get("SAM_GOV_API_KEY")),
        "rate_limits_rps": {k: float(os.environ.get(f"TEFCA_RATE_LIMIT_{k}_RPS", v))
                            for k, v in __import__("app.Tefca.connectors", fromlist=["x"])._RATE_LIMIT_DEFAULTS_RPS.items()},
        "database": os.environ["DATABASE_URL"].rsplit("/", 1)[-1],
        "label": _env("BENCH_LABEL", ""),
        "git_head": os.popen("git rev-parse --short HEAD").read().strip(),
    }
    result = {"population": None, "configuration": config, "stages": {}, "metrics": None,
              "failures": [], "equivalence_proof": "tests/test_chunked_gather_correctness.py"}

    try:
        # ---- seed (or reuse) ---------------------------------------------
        if not intake_id:
            progress("seed_start", n=n)
            t0 = time.perf_counter()
            intake_id = await _seed_promoted_delivery(n=n)
            result["stages"]["seed_seconds"] = round(time.perf_counter() - t0, 3)
            progress("seed_done", seconds=result["stages"]["seed_seconds"], intake_id=intake_id)
        else:
            result["stages"]["seed_seconds"] = None
            progress("seed_reused", intake_id=intake_id)

        async with async_session_maker() as db:
            refs = await _promoted_refs(db, intake_id, n)
        result["population"] = {"entities": len(refs), "intake_id": intake_id, "requested_n": n}
        progress("refs_loaded", n=len(refs))

        # ---- one-time warm-up, timed on its own -----------------------------
        # The OIG LEIE connector downloads + indexes the full exclusion CSV on
        # first use (`connectors._ensure_leie_loaded`). Measured separately so
        # the classify figure below is steady-state and the warm-up is never
        # hidden inside it (nor mistaken for per-entity cost).
        if _env("BENCH_PREWARM_LEIE", "1") == "1":
            from app.Tefca import connectors as _c
            r0 = sampler._p.memory_info().rss / 1e6
            t0 = time.perf_counter()
            ok = await _c._ensure_leie_loaded()
            result["stages"]["leie_warmup_seconds"] = round(time.perf_counter() - t0, 3)
            result["stages"]["leie_warmup_rss_delta_mb"] = round(sampler._p.memory_info().rss / 1e6 - r0, 1)
            result["stages"]["leie_rows"] = _c._LEIE_CACHE["row_count"] if ok else None
            progress("leie_warmup_done", seconds=result["stages"]["leie_warmup_seconds"], ok=ok)

        # ---- classify ----------------------------------------------------
        metrics = pipeline_metrics.activate(engine=engine_for_metrics)
        t0 = time.perf_counter()
        try:
            async with async_session_maker() as db:
                vc = await arc_pipeline.verify_and_classify(
                    db, refs, intake_id=intake_id, actor="lane-p-benchmark")
        except Exception as exc:  # noqa: BLE001 - record, then re-raise after writing the result
            result["failures"].append(f"verify_and_classify: {type(exc).__name__}: {exc}")
            raise
        finally:
            classify_s = time.perf_counter() - t0
            result["stages"]["classify_seconds"] = round(classify_s, 3)
            pipeline_metrics.deactivate()
            result["metrics"] = metrics.as_dict()
        result["stages"]["classify_ms_per_entity"] = round(classify_s * 1000 / max(len(refs), 1), 3)
        result["stages"]["classify_entities_per_second"] = round(len(refs) / classify_s, 3)
        result["outcome"] = {"verified": vc["verified"], "unresolved": len(vc["unresolved"]),
                             "bucket_counts": vc["bucket_counts"], "rule_set_size": vc["rule_set_size"]}
        progress("classify_done", seconds=result["stages"]["classify_seconds"],
                 verified=vc["verified"], unresolved=len(vc["unresolved"]))

        # ---- optional report stages ---------------------------------------
        if do_report:
            from app.reports.data.delivery_processing_data import DeliveryProcessingDataService
            from app.reports.generator import generate_report
            t0 = time.perf_counter()
            async with async_session_maker() as db:
                ds = await DeliveryProcessingDataService(db, intake_id=intake_id).build_dataset()
            result["stages"]["build_dataset_seconds"] = round(time.perf_counter() - t0, 3)
            progress("build_dataset_done", seconds=result["stages"]["build_dataset_seconds"])
            t0 = time.perf_counter()
            async with async_session_maker() as db:
                rep = await generate_report(db, report_type="delivery_processing",
                                            generated_by="lane-p-benchmark", persist=True,
                                            query_parameters={"intake_id": intake_id})
            result["stages"]["generate_report_seconds"] = round(time.perf_counter() - t0, 3)
            result["report"] = {"html_bytes": len(rep.get("html") or ""),
                                "csv_bytes": len(rep.get("csv") or ""),
                                "report_id": (rep.get("snapshot") or {}).get("report_id") if isinstance(rep.get("snapshot"), dict) else None}
            progress("generate_report_done", seconds=result["stages"]["generate_report_seconds"])
    finally:
        sampler.stop()
        result["memory"] = {"peak_rss_mb_self_reported": round(sampler.peak, 1),
                            "peak_rss_mb_external": None}
        if rss_csv and os.path.exists(rss_csv):
            try:
                import csv as _csv
                with open(rss_csv, encoding="utf-8") as fh:
                    rows = list(_csv.DictReader(fh))
                if rows:
                    result["memory"]["peak_rss_mb_external"] = max(float(r["rss_mb"]) for r in rows)
                    result["memory"]["host_free_mb_min_during_run"] = min(float(r["host_free_mb"]) for r in rows)
            except Exception as exc:  # noqa: BLE001
                result["failures"].append(f"rss_csv: {exc}")
        if mock_calls is not None:
            result["configuration"]["mock_calls"] = mock_calls["n"]
        if undo_mock:
            undo_mock()
        if undo_pool:
            await undo_pool()
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        progress("result_written", path=result_path)

    assert vc["verified"] == len(refs), f"expected every entity classified: {vc['verified']} of {len(refs)}"
