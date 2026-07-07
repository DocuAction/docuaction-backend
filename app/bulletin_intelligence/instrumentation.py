"""FCC Bulletin — collection instrumentation (Phase 4). Additive + flag-gated.

BULLETIN_INSTRUMENT_ENABLED=false (default): record_run() is a no-op -> no DB
writes, no behavior change. =true: persists one bulletin_run_log row (funnel +
timing) and per-source outcome rows per collection cycle. Best-effort: never
raises, never blocks a cycle.

NOTE (honest scope): per-source outcomes are derived from the coverage report's
`sources_scanned` (sources that returned items -> succeeded + item count).
Per-source FAILURE/timing capture requires deeper ingest wrapping and remains
pending; failed-source metrics stay unmeasured until then.
"""
import os
import uuid
from typing import Any, Dict, Optional

BULLETIN_INSTRUMENT_ENABLED = os.getenv("BULLETIN_INSTRUMENT_ENABLED", "false").strip().lower() == "true"


async def record_run(agency_id: str, *, run_id: str, trigger: str,
                     started_at: str, finished_at: str, duration_ms: int,
                     ingested: int, after_dedup: int, in_briefing: int,
                     rejected: int, dupes_removed: int,
                     coverage: Optional[Dict[str, Any]] = None,
                     sources_scanned: Optional[Dict[str, Any]] = None,
                     status: str = "completed", error: Optional[str] = None) -> None:
    if not BULLETIN_INSTRUMENT_ENABLED:
        return
    try:
        from app.bulletin_intelligence import bulletin_store
        await bulletin_store.save_run_log({
            "run_id": run_id, "agency_id": agency_id, "trigger": trigger,
            "started_at": started_at, "finished_at": finished_at, "duration_ms": duration_ms,
            "ingested": ingested, "after_dedup": after_dedup, "in_briefing": in_briefing,
            "rejected": rejected, "dupes_removed": dupes_removed, "cluster_count": None,
            "status": status, "error": error, "coverage": coverage or {},
        })
        if sources_scanned:
            rows = [{
                "id": uuid.uuid4().hex, "run_id": run_id, "source": str(src),
                "type": None, "tier": None, "attempted": True, "succeeded": True,
                "items": int(cnt) if isinstance(cnt, (int, float)) else None,
                "http_status": None, "error": None, "response_ms": None, "retries": 0,
            } for src, cnt in dict(sources_scanned).items()]
            await bulletin_store.save_source_outcomes(rows)
    except Exception:
        pass  # never break a cycle over instrumentation
