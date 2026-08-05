"""
DocuAction Bulletin Intelligence — API Routes
Registers as /api/v1/bulletin on the main FastAPI app
"""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends, Request
from pydantic import BaseModel
from typing import Optional, List
import logging

# Phase 2 — flag-gated auth (default OFF -> no-op, no behavior change).
from .auth import guard, rate_limit
# Phase 3 — flag-gated audit logging (default OFF -> no-op, best-effort).
from .audit import audit
# PWS coverage foundation (additive): source classification + honest aggregation.
from .pws import SOURCE_CLASSIFICATIONS, CLASSIFICATION_LABELS, build_pws_coverage

from app.bulletin_intelligence.engine import (
    AgencyConfig, run_daily_cycle, approve_and_deliver,
    get_editorial_queue, get_briefing, get_briefing_html,
    search_archive, get_archive_stats, run_llm_visibility_check,
    register_agency, get_agency, list_agencies, get_briefing_history,
    get_latest_briefing, get_today_briefing, send_briefing_email,
    _briefing_preview_url, _latest_preview_url,
    _articles, _briefings, _last_window_stats,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/bulletin", tags=["Bulletin Intelligence"])


# ── Health ─────────────────────────────────────────────────────────────────────
@router.get("/health")
async def health():
    try:
        from app.bulletin_intelligence import bulletin_store
        persisted = await bulletin_store.counts()
    except Exception:
        persisted = {"enabled": False}
    try:
        from app.bulletin_intelligence.scheduler import scheduler_status
        scheduler = scheduler_status()
    except Exception:
        scheduler = {"running": False}
    return {
        "module": "bulletin_intelligence",
        "status": "active",
        "version": "1.0.0",
        "agencies_registered": len(list_agencies()),
        "articles_in_memory": len(_articles),
        "briefings_in_memory": len(_briefings),
        "persisted": persisted,
        "scheduler": scheduler,
    }


# ── Claude API cost tracking (Phase 1) ─────────────────────────────────────────
# guard(): spend and token counts let an unauthenticated caller measure the cost
# of each request and size an amplification attack. Not public.
@router.get("/costs", dependencies=guard("contributor"))
async def bulletin_costs(
    agency_id: str = Query(None, description="Filter to one agency; omit for all"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
):
    """Claude API token usage and cost for bulletin runs.

    Read-only aggregate over `bulletin_cost_logs`. Returns `enabled: false` when
    cost tracking has never written (BULLETIN_COST_TRACKING_ENABLED unset, or the
    bulletin store is unavailable) rather than failing — an empty cost history is a
    valid state, not an error.

    `cost_usd` is computed from the point-in-time rate table in
    costs/cost_tracker.py; `tokens_in`/`tokens_out` are the raw measured values and
    are authoritative if rates later change.
    """
    try:
        from app.bulletin_intelligence import bulletin_store
        return await bulletin_store.fetch_cost_summary(agency_id=agency_id, days=days)
    except Exception as e:
        return {"enabled": False, "error": str(e)[:200]}


# ── Boolean search profiles (Phase 2) ──────────────────────────────────────────
# Boolean profiles are editorial configuration, not published content. The
# preview and latest endpoints stay public because FCC contacts open them from
# an email link with no account; this one has no such consumer.
@router.get("/profiles", dependencies=guard("viewer"))
async def list_search_profiles(agency_id: str = Query("fcc")):
    """Boolean search profiles plus which source is currently live.

    `active_source` is "hardcoded" until BULLETIN_PROFILES_DB_ENABLED=true AND the
    table has rows — an empty table is a valid state that falls back to the
    fcc_boolean_search constants, so matching behaviour never depends on this table
    existing.
    """
    try:
        from app.bulletin_intelligence.profiles.boolean_profiles import profiles_status
        status = profiles_status()
    except Exception as e:
        return {"error": str(e)[:200]}
    try:
        from app.bulletin_intelligence import bulletin_store
        db_rows = await bulletin_store.fetch_search_profiles(agency_id, enabled_only=False)
    except Exception:
        db_rows = []
    return {**status, "agency_id": agency_id, "db_rows": len(db_rows), "profiles": db_rows}


@router.post("/profiles/seed", dependencies=guard("admin"))
async def seed_search_profiles_endpoint(agency_id: str = Query("fcc")):
    """Seed the profile table from the hardcoded constants. Idempotent — existing
    rows are never overwritten, so operator edits survive re-seeding."""
    try:
        from app.bulletin_intelligence.profiles.boolean_profiles import seed_defaults
        inserted = await seed_defaults(agency_id)
        return {"agency_id": agency_id, "inserted": inserted}
    except Exception as e:
        raise HTTPException(500, f"seed failed: {str(e)[:200]}")


# ── Perigon quota observability ────────────────────────────────────────────────
@router.get("/perigon/health", dependencies=guard("admin"))
async def perigon_quota_health(probe: bool = Query(
        False, description="Also make one live reachability call (spends budget).")):
    """Perigon quota state — budget remaining, calls today, cache size.

    Exists because the free tier was being exhausted daily by ~06:00 with no
    visible signal: the provider fails silently by design (a Perigon problem must
    never stop a bulletin), so the only evidence anything was wrong was a thinner
    briefing. Without this route the guards are unobservable and the first sign
    they stopped working would again be the absence of a problem.

    Read-only by default. `probe=true` performs one live reachability call, which
    spends a request from the same budget it reports — off by default so that
    monitoring this endpoint cannot itself drain the quota, which is exactly how
    perigon_health() contributed to the original problem.

    Admin-guarded: it reports provider capacity and configuration state.
    """
    from app.bulletin_intelligence.providers.perigon import (
        PERIGON_ENABLED, budget_status, perigon_health)

    status = budget_status()
    payload = {
        # Documented contract — flat, so a monitor can read it without unwrapping.
        "budget_total": status["budget_total"],
        "budget_remaining": status["budget_remaining"],
        "calls_today": status["calls_today"],
        "cache_hits_today": status["cache_hits_today"],
        "last_call": status["last_call"],
        "status": status["status"],
        "provider": "perigon",
        "enabled": PERIGON_ENABLED,
        "budget": status,
        "note": ("The tier is 150 requests per MONTH, so budget_remaining counts "
                 "down over the month, not the day. Counters are per worker "
                 "process; Azure App Service may run several, so the ceiling is "
                 "per-worker and the 24h response cache is what bounds total spend."),
    }
    if not PERIGON_ENABLED:
        payload["status"] = "not_configured"
        payload["reason"] = "PERIGON_API_KEY not set — provider inert, no calls made"
        return payload
    if probe:
        payload["probe"] = await perigon_health()
    return payload


# ── Google News QA comparison ──────────────────────────────────────────────────
@router.get("/qa/google-news-compare", dependencies=guard("viewer"))
async def google_news_compare(agency_id: str = "fcc"):
    """Google News QA comparison for the latest cycle.

    The FCC verifies our bulletin against Google News, so this answers the same
    question before delivery: which stories did Google News carry that none of our
    other sources found?

    `missing_from_bulletin` counts stories unique to Google News. They ARE in the
    briefing — the Google News collector runs as a source — so this is a coverage
    signal about the other sources, not a list of gaps in what gets delivered.

    `qa_passed` is false when 5 or more stories were unique to Google News, which
    means the rest of the source set under-performed for that cycle.
    """
    from app.bulletin_intelligence.google_news_collector import get_qa_report
    report = get_qa_report(agency_id)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"No Google News QA report yet for {agency_id}. Run a cycle first "
                   f"(POST /api/v1/bulletin/run/{agency_id}).",
        )
    return report


# ── Source Coverage Report ─────────────────────────────────────────────────────
@router.get("/coverage/{agency_id}", dependencies=guard("viewer"))
async def coverage_report(agency_id: str):
    """Daily source/coverage analytics from the most recent cycle: sources scanned,
    stories collected/rejected, duplicates removed, subscription stories, coverage
    by category/section, and missing-category warnings."""
    from app.bulletin_intelligence.engine import _last_coverage
    report = _last_coverage.get(agency_id)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"No coverage report yet for {agency_id}. Run a cycle first "
                   f"(POST /api/v1/bulletin/run/{agency_id}).",
        )
    return report


# ── Refresh cache from the durable store ───────────────────────────────────────
@router.post("/refresh/{agency_id}", dependencies=guard("contributor"))
async def refresh_cache(agency_id: str):
    """Reload the in-memory article/briefing cache from the shared database, so the
    dashboard reflects everything collected — including by the 1 AM scheduler box,
    which writes to the same DB but a different process. FAST: this does NOT run a
    new collection cycle (no ingestion, no AI cost), it just re-reads the store.
    """
    from app.bulletin_intelligence.engine import (
        hydrate_from_store, _articles, _briefings, get_agency,
    )
    if not get_agency(agency_id):
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    counts = await hydrate_from_store()
    return {
        "status": "refreshed",
        "agency_id": agency_id,
        "articles_in_memory": len(_articles),
        "briefings_in_memory": len(_briefings),
        "restored": counts,
    }


# ── Agency Management ─────────────────────────────────────────────────────────
class AgencyCreateRequest(BaseModel):
    agency_id: str
    name: str
    short_name: str
    primary_color: str = "#0B3C5D"
    boolean_queries: List[str]
    topics: List[str]
    distribution_email: str
    distribution_list: List[str]
    delivery_time_et: str = "07:30"
    include_broadcast: bool = True
    include_social: bool = True
    include_regulatory: bool = True
    archive_months: int = 12


@router.post("/agencies", dependencies=guard("admin"))
async def create_agency(req: AgencyCreateRequest):
    # The request model calls it `boolean_queries`; AgencyConfig's field is
    # `search_queries`. Map it across so AgencyConfig(**...) doesn't raise.
    data = req.dict()
    data["search_queries"] = data.pop("boolean_queries", [])
    config = AgencyConfig(**data)
    register_agency(config)
    return {"status": "registered", "agency_id": config.agency_id, "name": config.name}


@router.get("/agencies", dependencies=guard("viewer"))
async def list_agencies_endpoint():
    agencies = list_agencies()
    return {
        "count": len(agencies),
        "agencies": [
            {
                "agency_id": a.agency_id,
                "name": a.name,
                "short_name": a.short_name,
                "topics": a.topics,
                "distribution_list_size": len(a.distribution_list),
                "delivery_time_et": a.delivery_time_et,
                "archive_months": a.archive_months,
            }
            for a in agencies
        ]
    }


@router.get("/agencies/{agency_id}", dependencies=guard("viewer"))
async def get_agency_endpoint(agency_id: str):
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return {
        "agency_id": agency.agency_id,
        "name": agency.name,
        "short_name": agency.short_name,
        "primary_color": agency.primary_color,
        # AgencyConfig stores this as `search_queries`; the API/UI calls it
        # boolean_queries. Reading the wrong attribute here was a hard 500 on
        # GET /agencies/{id} (AttributeError) — the likely "Failed to load
        # articles" the dashboard showed.
        "boolean_queries": agency.search_queries,
        "topics": agency.topics,
        "distribution_email": agency.distribution_email,
        "distribution_list_size": len(agency.distribution_list),
        "delivery_time_et": agency.delivery_time_et,
        "include_broadcast": agency.include_broadcast,
        "include_social": agency.include_social,
        "include_regulatory": agency.include_regulatory,
        "archive_months": agency.archive_months,
    }


# ── Daily Cycle ────────────────────────────────────────────────────────────────
@router.post("/run/{agency_id}", dependencies=guard("contributor"))
async def trigger_daily_cycle(
    agency_id: str,
    background_tasks: BackgroundTasks,
    auto_deliver: bool = False,
    lookback_hours: int = 72,
    coverage_start: str = None,
    coverage_end: str = None,
):
    """Trigger the daily intelligence cycle for an agency. Returns immediately; runs in background."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")

    background_tasks.add_task(run_daily_cycle, agency_id, auto_deliver,
                           lookback_hours)
                           
    return {
        "status": "started",
        "agency_id": agency_id,
        "agency_name": agency.name,
        "auto_deliver": auto_deliver,
        "message": "Daily cycle started in background. Check /queue for results."
    }


@router.post("/run/{agency_id}/sync", dependencies=guard("contributor"))
async def trigger_daily_cycle_sync(
    agency_id: str,
    auto_deliver: bool = False,
    lookback_hours: int = 72,
    coverage_start: str = None,
    coverage_end: str = None,
):
    """Synchronous version — waits for completion. Use for demos and testing."""
    result = await run_daily_cycle(agency_id, auto_deliver, lookback_hours)
                                  
    return result


# ── Admin: purge the article archive ────────────────────────────────────────────
@router.post("/admin/purge-articles", dependencies=guard("admin"))
async def purge_articles(confirm: str = Query("")):
    """Clear the rolling article archive — in-memory cache AND the durable store —
    so the next run rebuilds a clean archive from scratch. Guarded by a confirm
    token to prevent accidental calls. The archive is a rebuildable cache, so the
    blast radius is limited to a one-cycle rebuild.

    Use: POST /api/v1/bulletin/admin/purge-articles?confirm=PURGE_ARCHIVE
    """
    if confirm != "PURGE_ARCHIVE":
        raise HTTPException(status_code=400,
                            detail="pass ?confirm=PURGE_ARCHIVE to purge the article archive")
    mem = len(_articles)
    _articles.clear()
    try:
        from app.bulletin_intelligence import bulletin_store
        deleted = await bulletin_store.clear_articles()
    except Exception as e:
        logger.warning(f"purge_articles durable clear failed: {e}")
        deleted = -1
    logger.info(f"Article archive purged: memory={mem}, durable_deleted={deleted}")
    await audit("manual", entity_type="archive", action="purge",
                details={"memory_cleared": mem, "durable_deleted": deleted})
    return {"status": "purged", "memory_cleared": mem, "durable_deleted": deleted}


@router.get("/admin/last-window/{agency_id}", dependencies=guard("contributor"))
async def last_window(agency_id: str):
    """Report the freshness window + in/out-of-window counts from the most recent
    run for this agency (observability for the date-window filter)."""
    stats = _last_window_stats.get(agency_id)
    if not stats:
        return {"agency_id": agency_id, "available": False,
                "detail": "no run recorded since process start"}
    return {"agency_id": agency_id, "available": True, **stats}


# ── Live Feed: always-available briefings ───────────────────────────────────────
# Live-feed model: whatever has been collected is immediately available. No
# approval gate, no waiting for a schedule. These endpoints are the public
# surface FCC contacts can bookmark.

def _briefing_summary(b: dict) -> dict:
    """Shape a briefing dict (already HTML-stripped) into the live-feed payload."""
    return {
        "briefing_id": b["briefing_id"],
        "agency_id": b.get("agency_id"),
        "briefing_date": b.get("briefing_date"),
        "status": b.get("status"),
        "generated_at": b.get("generated_at"),
        "delivered_at": b.get("delivered_at", ""),
        "article_count": b.get("article_count", 0),
        "topic_counts": b.get("topic_counts", {}),
        "preview_url": _briefing_preview_url(b["briefing_id"]),
    }


@router.get("/latest/{agency_id}")
async def latest_briefing(agency_id: str):
    """Metadata for the MOST RECENT briefing — the 'what's available right now'
    endpoint. Any status counts; a briefing is live the moment it's generated."""
    b = get_latest_briefing(agency_id)
    if not b:
        raise HTTPException(status_code=404,
                            detail=f"No briefing available yet for {agency_id}. "
                                   f"Trigger one: POST /api/v1/bulletin/collect/{agency_id}")
    return _briefing_summary(b)


@router.get("/latest/{agency_id}/preview")
async def latest_briefing_preview(agency_id: str):
    """Redirect to the newest briefing's full preview. This is the stable URL to
    share/bookmark — it always lands on the latest briefing."""
    from fastapi.responses import RedirectResponse
    b = get_latest_briefing(agency_id)
    if not b:
        raise HTTPException(status_code=404,
                            detail=f"No briefing available yet for {agency_id}.")
    # 307 keeps method/semantics; browsers follow it to the current newest preview.
    return RedirectResponse(url=f"/api/v1/bulletin/briefings/{b['briefing_id']}/preview",
                            status_code=307)


@router.get("/today/{agency_id}", dependencies=guard("viewer"))
async def today_briefing(agency_id: str, lookback_hours: int = 72):
    """Today's briefing — the 'always works' endpoint. If one exists for today it's
    returned; otherwise a collection runs now and its result is returned. Never
    empty (unless collection itself fails)."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")

    b = get_today_briefing(agency_id)
    created_now = False
    if not b:
        result = await run_daily_cycle(agency_id, auto_deliver=False,
                                       lookback_hours=lookback_hours)
        if result.get("status") == "already_running":
            # A cycle is mid-flight — hand back the latest we have rather than error.
            b = get_latest_briefing(agency_id)
            if not b:
                raise HTTPException(status_code=503,
                                    detail="Collection in progress — retry in a moment.")
        elif result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        else:
            created_now = True
            b = get_today_briefing(agency_id) or get_latest_briefing(agency_id)

    if not b:
        raise HTTPException(status_code=500, detail="Failed to produce a briefing")
    return {**_briefing_summary(b), "created_now": created_now}


@router.post("/collect/{agency_id}", dependencies=guard("contributor") + [Depends(rate_limit)])
async def collect_now(agency_id: str, lookback_hours: int = 72):
    """Trigger a fresh collection cycle NOW (synchronous) and return the resulting
    briefing. The briefing is live immediately (status=delivered); email is a
    separate step (POST /send/{agency_id}/{briefing_id})."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")

    result = await run_daily_cycle(agency_id, auto_deliver=False,
                                   lookback_hours=lookback_hours)
    if result.get("status") == "already_running":
        latest = get_latest_briefing(agency_id)
        return {
            "status": "already_running",
            "message": "A collection cycle is already in progress; returning the latest available.",
            "latest": _briefing_summary(latest) if latest else None,
        }
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    bid = result.get("briefing_id")
    await audit("collection", entity_type="briefing", entity_id=bid, action="collect",
                details={"in_briefing": result.get("in_briefing"), "ingested": result.get("ingested"),
                         "after_dedup": result.get("after_dedup")})
    return {
        "status": "collected",
        "briefing_id": bid,
        "briefing_date": result.get("briefing_date"),
        "article_count": result.get("in_briefing", 0),
        "preview_url": _briefing_preview_url(bid) if bid else None,
        "window": result.get("window", {}),
    }


@router.post("/send/{agency_id}/{briefing_id}", dependencies=guard("qalead") + [Depends(rate_limit)])
async def send_briefing(agency_id: str, briefing_id: str):
    """Email the summary (short summary + VIEW FULL BRIEFING button) for a specific
    briefing to the agency's distribution list. Separate from collection."""
    b = get_briefing(briefing_id)
    if not b or b.get("agency_id") != agency_id:
        raise HTTPException(status_code=404,
                            detail=f"Briefing {briefing_id} not found for {agency_id}")
    result = await send_briefing_email(briefing_id)
    if result.get("error"):
        await audit("delivery", entity_type="briefing", entity_id=briefing_id, action="send",
                    result="error", details={"error": result["error"]})
        raise HTTPException(status_code=400, detail=result["error"])
    await audit("delivery", entity_type="briefing", entity_id=briefing_id, action="send",
                details={"recipients": result.get("recipients"), "sent": result.get("status")})
    return {"briefing_id": briefing_id, **result}


# ── Editorial Queue ────────────────────────────────────────────────────────────
@router.get("/queue/{agency_id}", dependencies=guard("viewer"))
async def get_queue(agency_id: str):
    """Get all briefings pending editorial approval."""
    queue = get_editorial_queue(agency_id)
    return {
        "agency_id": agency_id,
        "pending_count": len(queue),
        "briefings": [
            {
                "briefing_id": b["briefing_id"],
                "briefing_date": b["briefing_date"],
                "status": b["status"],
                "article_count": b["article_count"],
                "topic_counts": b["topic_counts"],
                "generated_at": b["generated_at"],
            }
            for b in queue
        ]
    }


@router.get("/history/{agency_id}")
async def briefing_history(agency_id: str):
    """Full run history — all briefings for an agency (any status), newest first."""
    history = get_briefing_history(agency_id)
    return {
        "agency_id": agency_id,
        "count": len(history),
        "briefings": history,
    }


@router.get("/audit/{agency_id}", dependencies=guard("contributor"))
async def get_audit(agency_id: str, event_type: str = Query(""), limit: int = Query(200)):
    """Phase 3 — recent bulletin audit events (newest first). Empty list unless
    BULLETIN_AUDIT_ENABLED wrote rows. Optional event_type filter."""
    from app.bulletin_intelligence import bulletin_store
    try:
        rows = await bulletin_store.load_audit(event_type=event_type or "", limit=limit)
    except Exception as e:
        logger.warning(f"get_audit failed: {e}")
        rows = []
    return {"agency_id": agency_id, "count": len(rows), "events": rows}


@router.get("/runs/{agency_id}", dependencies=guard("contributor"))
async def get_runs(agency_id: str, limit: int = Query(50)):
    """Phase 4 — persisted run log (funnel + timing), newest first. Empty unless
    BULLETIN_INSTRUMENT_ENABLED recorded runs."""
    from app.bulletin_intelligence import bulletin_store
    try:
        runs = await bulletin_store.load_run_logs(agency_id=agency_id, limit=limit)
    except Exception as e:
        logger.warning(f"get_runs failed: {e}")
        runs = []
    return {"agency_id": agency_id, "count": len(runs), "runs": runs}


@router.get("/runs/{agency_id}/{run_id}", dependencies=guard("contributor"))
async def get_run_detail(agency_id: str, run_id: str):
    """Phase 4 — single run detail + per-source outcomes."""
    from app.bulletin_intelligence import bulletin_store
    try:
        runs = await bulletin_store.load_run_logs(agency_id=agency_id, limit=500)
        run = next((r for r in runs if r.get("run_id") == run_id), None)
        outcomes = await bulletin_store.load_source_outcomes(run_id)
    except Exception as e:
        logger.warning(f"get_run_detail failed: {e}")
        run, outcomes = None, []
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run, "source_outcomes": outcomes}


# ── Phase 6: expected-source registry + HONEST coverage assurance ────────────
class SourceRegistryItem(BaseModel):
    source_id: str
    name: str
    type: str = ""
    tier: str = ""
    importance_weight: float = 1.0
    enabled: bool = True
    method: str = ""
    url: str = ""
    notes: str = ""


# ── Phase 4: source registry ─────────────────────────────────────────────────

@router.get("/quality/latest")
async def quality_latest(agency_id: str = "fcc"):
    """Quality-gate result from the most recent bulletin run for this agency."""
    from app.bulletin_intelligence.quality_gate import last_quality
    return last_quality(agency_id)


@router.get("/sources")
async def list_sources(enabled_only: bool = False, limit: int = Query(500, ge=1, le=2000)):
    """All registry sources with their catalogue metadata.

    Public read, consistent with the other bulletin read endpoints. Returns no
    credentials and no PHI - only publication metadata and production counts.
    """
    from app.bulletin_intelligence.source_registry import fetch_sources
    rows = await fetch_sources(enabled_only=enabled_only, limit=limit)
    return {"count": len(rows), "enabled_only": enabled_only, "sources": rows}


@router.get("/sources/health")
async def sources_health():
    """Aggregate source health: producing, silent, or never seen."""
    from app.bulletin_intelligence.source_registry import source_health
    return await source_health()


@router.get("/sources/missing")
async def sources_missing(hours: int = Query(24, ge=1, le=168)):
    """Sources with a production history that have gone quiet."""
    from app.bulletin_intelligence.source_registry import missing_sources
    return await missing_sources(hours=hours)


@router.post("/sources/load-catalog", dependencies=guard("admin"))
async def load_source_catalog():
    """Merge Master_Source_Catalog.csv into the registry. Idempotent."""
    from app.bulletin_intelligence.source_registry import load_catalog
    return await load_catalog()


@router.get("/sources/{agency_id}", dependencies=guard("contributor"))
async def list_sources(agency_id: str):
    """Expected-source registry (Coverage % denominator). Empty until seeded."""
    from app.bulletin_intelligence import bulletin_store
    return {"agency_id": agency_id, "sources": await bulletin_store.load_source_registry()}


@router.post("/sources/{agency_id}", dependencies=guard("admin"))
async def upsert_sources(agency_id: str, items: List[SourceRegistryItem]):
    """Seed / update the expected-source registry (admin)."""
    from app.bulletin_intelligence import bulletin_store
    n = await bulletin_store.save_source_registry([i.dict() for i in items])
    return {"agency_id": agency_id, "upserted": n}


@router.get("/coverage-assurance/{agency_id}", dependencies=guard("viewer"))
async def coverage_assurance(agency_id: str):
    """Phase 6 — HONEST coverage assurance. Coverage % is computed ONLY when an
    expected-source registry AND per-source outcomes both exist; otherwise it is
    `null` with status `pending_instrumentation` — never estimated or fabricated."""
    from app.bulletin_intelligence import bulletin_store
    registry = await bulletin_store.load_source_registry()
    expected = [r for r in registry if r.get("enabled")]
    runs = await bulletin_store.load_run_logs(agency_id=agency_id, limit=1)
    outcomes = await bulletin_store.load_source_outcomes(runs[0]["run_id"]) if runs else []

    result = {
        "agency_id": agency_id,
        "expected_sources": len(expected),
        "sources_with_outcome": len(outcomes),
        "coverage_pct": None,
        "coverage_confidence": None,
        "status": "pending_instrumentation",
        "note": ("Coverage % is shown only when an expected-source registry AND per-source "
                 "outcomes both exist. Until then it is not computed (no estimate). "
                 "Primary-source backstop: FCC.gov is always collected."),
    }
    if expected and outcomes:
        succeeded = {o.get("source") for o in outcomes if o.get("succeeded")}
        covered = [r for r in expected if r.get("name") in succeeded]
        result["coverage_pct"] = round(100.0 * len(covered) / len(expected), 1)
        wtot = sum((r.get("importance_weight") or 1.0) for r in expected)
        wcov = sum((r.get("importance_weight") or 1.0) for r in covered)
        result["coverage_confidence"] = round(100.0 * wcov / wtot, 1) if wtot else None
        result["status"] = "measured"
    return result


# ── PWS coverage foundation (additive) ───────────────────────────────────────
@router.get("/source-classifications", dependencies=guard("viewer"))
async def source_classifications():
    """The PWS source-classification taxonomy (for registry editors / dashboard)."""
    return {"classifications": [{"id": c, "label": CLASSIFICATION_LABELS[c]} for c in SOURCE_CLASSIFICATIONS]}


@router.get("/pws-coverage/{agency_id}", dependencies=guard("contributor"))
async def pws_coverage(agency_id: str):
    """Internal PWS coverage picture (honest): totals, source distribution by
    classification, required-source coverage (pending until Appendix A is loaded),
    category gaps, and editor suggestions. Never fabricates a % or compliance status."""
    try:
        return await build_pws_coverage(agency_id)
    except Exception as e:
        logger.warning(f"pws_coverage failed: {e}")
        raise HTTPException(status_code=500, detail="pws coverage aggregation failed")


@router.post("/briefings/{briefing_id}/approve", dependencies=guard("qalead"))
async def approve_briefing(briefing_id: str):
    """Editor approves briefing and triggers email delivery."""
    result = await approve_and_deliver(briefing_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/briefings/{briefing_id}", dependencies=guard("viewer"))
async def get_briefing_endpoint(briefing_id: str):
    briefing = get_briefing(briefing_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found")
    # Return without the big HTML/DOCX blobs for the summary view
    return {k: v for k, v in briefing.items() if k not in ("html_content", "docx_b64")}


def _inject_download_bar(html: str, briefing_id: str) -> str:
    """Add the View-HTML / Download-Excel bar to a briefing's header.

    Injected when the preview is SERVED rather than baked in when the briefing is
    rendered, for two reasons. Briefings already generated — the ones the FCC
    contacts' current email links point at — get the buttons without being
    regenerated. And the stored html_content is also what gets emailed, so
    leaving it untouched keeps the email body free of buttons that only make
    sense in a browser.

    Links are relative: the preview is served from the same origin as the API, so
    they resolve without depending on PUBLIC_BASE_URL being set correctly.

    Never raises. If the header anchor isn't found (e.g. the simple-HTML
    fallback render), the page is returned exactly as stored — the preview
    working matters more than the buttons.
    """
    base = f"/api/v1/bulletin/briefings/{briefing_id}"
    bar = (
        '<div style="text-align:center;margin-top:12px">'
        f'<a href="{base}/preview" '
        'style="display:inline-block;background:#003087;color:#ffffff;padding:8px 16px;'
        'text-decoration:none;font-size:12px;border-radius:4px;margin:0 4px">'
        '&#127760; View Full Bulletin</a>'
        f'<a href="{base}/excel" '
        'style="display:inline-block;background:#0078D4;color:#ffffff;padding:8px 16px;'
        'text-decoration:none;font-size:12px;border-radius:4px;margin:0 4px">'
        '&#128229; Download FCC Bulletin</a>'
        '</div>'
    )
    anchor = '<div style="border-top:1px solid #4f7fbd;margin:12px auto;max-width:560px"></div>'
    if anchor in html:
        return html.replace(anchor, bar + anchor, 1)
    return html


@router.get("/briefings/{briefing_id}/preview")
async def preview_briefing(briefing_id: str):
    """Return the full HTML briefing for preview."""
    html = get_briefing_html(briefing_id)
    if not html:
        raise HTTPException(status_code=404, detail="Briefing not found")
    from fastapi.responses import HTMLResponse
    try:
        html = _inject_download_bar(html, briefing_id)
    except Exception as e:  # pragma: no cover - never break the preview
        logger.warning(f"Download bar injection skipped for {briefing_id}: {e}")
    return HTMLResponse(content=html)


@router.get("/briefings/{briefing_id}/docx", dependencies=guard("viewer"))
async def download_briefing_docx(briefing_id: str):
    """Download the editable Word (.docx) version of a briefing — open in Word,
    tweak, then run it through fcc_digest.py to produce the final email."""
    briefing = get_briefing(briefing_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found")
    b64 = briefing.get("docx_b64") or ""
    if not b64:
        raise HTTPException(status_code=404, detail="No Word document for this briefing yet (re-run the cycle).")
    import base64
    from fastapi.responses import Response
    data = base64.b64decode(b64)
    date_slug = (briefing.get("briefing_date") or "briefing").replace(",", "").replace(" ", "_")
    fname = f"{briefing.get('agency_id', 'fcc').upper()}_Daily_News_{date_slug}.docx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Archive ────────────────────────────────────────────────────────────────────
@router.get("/archive/{agency_id}", dependencies=guard("viewer"))
async def archive_search(
    agency_id: str,
    keyword: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None, description="news | broadcast | social | regulatory"),
    start_date: Optional[str] = Query(None, description="ISO date e.g. 2025-01-01"),
    end_date: Optional[str] = Query(None, description="ISO date e.g. 2026-01-01"),
    min_relevance: float = Query(0.0, ge=0.0, le=1.0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """
    Search the 12-month article archive for any agency.
    Supports keyword search, topic filter, source type, date range, and relevance threshold.
    """
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")

    return search_archive(
        agency_id=agency_id,
        keyword=keyword,
        topic=topic,
        source_type=source_type,
        start_date=start_date,
        end_date=end_date,
        min_relevance=min_relevance,
        page=page,
        page_size=page_size,
    )


@router.get("/archive/{agency_id}/stats", dependencies=guard("viewer"))
async def archive_statistics(agency_id: str):
    """Get 12-month archive statistics — volume by topic, source type, and month."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return get_archive_stats(agency_id)


@router.get("/archive/{agency_id}/clips", dependencies=guard("viewer"))
async def get_broadcast_clips(
    agency_id: str,
    topic: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Get all broadcast TV/radio clips from the 12-month archive."""
    result = search_archive(
        agency_id=agency_id,
        source_type="broadcast",
        topic=topic,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )
    # Add clip URL to each result
    for art in result["articles"]:
        art["clip_url"] = art.get("broadcast_clip_url", "")
    return result


# ── LLM Visibility Tracker ─────────────────────────────────────────────────────
@router.post("/llm-visibility/{agency_id}", dependencies=guard("contributor"))
async def llm_visibility_check(agency_id: str):
    """Run LLM visibility check — query Claude on what it knows about the agency."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return await run_llm_visibility_check(agency)


# ── Demo Endpoint ──────────────────────────────────────────────────────────────


@router.get("/briefings/{briefing_id}/pdf", dependencies=guard("viewer"))
async def download_briefing_pdf(briefing_id: str):
    """Download briefing as PDF."""
    from fastapi.responses import Response
    html = get_briefing_html(briefing_id)
    if not html:
        raise HTTPException(status_code=404, detail="Briefing not found")

    # Try WeasyPrint first, fall back to pdfkit, fall back to HTML.
    # Catch any exception (not just ImportError): WeasyPrint imports fine on
    # many servers but fails at render time when native libs (pango/cairo) are
    # missing — that must fall through to the HTML fallback, never 500.
    pdf_bytes = None
    try:
        import weasyprint
        pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    except Exception as e:
        logger.warning(f"WeasyPrint PDF render failed, falling back: {e}")

    if not pdf_bytes:
        try:
            import pdfkit
            pdf_bytes = pdfkit.from_string(html, False)
        except (ImportError, Exception):
            pass

    if pdf_bytes:
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=FCC_Briefing_{briefing_id}.pdf"}
        )
    else:
        # Fall back — return HTML with PDF print styles
        from fastapi.responses import HTMLResponse
        print_html = html.replace("</head>", "<style>@media print{body{margin:0}}</style></head>")
        return HTMLResponse(
            content=print_html,
            headers={"Content-Disposition": f"inline; filename=FCC_Briefing_{briefing_id}.html"}
        )


@router.get("/run/{agency_id}/preview", dependencies=guard("contributor"))
async def run_and_preview(agency_id: str, lookback_hours: int = 48):
    """Run full cycle and return HTML briefing directly in browser.

    Auth added 2026-07-27: this endpoint calls run_daily_cycle(), i.e. a full
    collection cycle that spends Anthropic API budget. It was the only trigger in the
    module without a guard — /run, /run/sync, /collect and /refresh are all
    guard("contributor") — which left an unauthenticated cost-amplification vector
    open to anyone on the internet. Matched to its siblings.
    """
    from fastapi.responses import HTMLResponse
    result = await run_daily_cycle(agency_id, auto_deliver=False, lookback_hours=lookback_hours)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    html = get_briefing_html(result.get("briefing_id", ""))
    if not html:
        return HTMLResponse(content=f"<h1>Cycle complete</h1><pre>{result}</pre>")
    return HTMLResponse(content=html)

@router.get("/demo/{agency_id}")
async def demo_cycle(agency_id: str = "fcc"):
    """Demo: run a mock daily cycle with simulated articles."""
    import os
    if os.environ.get("ENABLE_DEMO", "false").lower() != "true":
        return {"status": "disabled", "message": "Demo mode disabled in production"}
    import random, uuid
    from datetime import datetime, timezone
    try:
        from app.bulletin_intelligence.engine import (
            Article, Briefing, _articles, _briefings, _now, get_agency
        )
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Engine import error: {e}")

    agency_obj = get_agency(agency_id)
    if not agency_obj:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    agency = agency_obj

    topics = agency.topics[:6]
    sources = [
        ("Reuters", "news"), ("Associated Press", "news"), ("Politico", "news"),
        ("Broadcasting & Cable", "news"), ("FierceWireless", "news"),
        ("CNN", "broadcast"), ("Fox News", "broadcast"), ("C-SPAN", "broadcast"),
        ("r/technology", "social"), ("@FCCNews", "social"),
        ("Federal Register", "regulatory"), ("Congress.gov", "regulatory"),
    ]
    article_types = ["news", "news", "news", "opinion", "analysis", "regulatory"]

    demo_articles = []
    for i in range(30):
        topic = topics[i % len(topics)]
        outlet, src_type = sources[i % len(sources)]
        is_broadcast = src_type == "broadcast"
        a = Article(
            article_id=f"{agency_id}_demo_{uuid.uuid4().hex[:8]}",
            agency_id=agency_id,
            source=outlet.lower().replace(" ", "_"),
            source_type=src_type,
            title=f"[Demo] {topic.replace('_', ' ').title()} Coverage — Article {i+1}",
            url=f"https://example.com/article/{i+1}",
            published_at=datetime.now(timezone.utc).isoformat(),
            summary=f"This is a demonstration article about {topic.replace('_', ' ').title()} for the {agency.name} daily intelligence briefing.",
            full_text="Full article text would appear here in production.",
            author=f"Demo Author {i+1}",
            outlet=outlet,
            topic=topic,
            article_type=article_types[i % len(article_types)],
            relevance_score=round(0.5 + random.random() * 0.5, 2),
            sentiment=["positive", "negative", "neutral"][i % 3],
            is_paywalled=i % 7 == 0,
            broadcast_clip_url=f"https://tveyes.com/demo/clip/{i+1}" if is_broadcast else "",
            ingested_at=datetime.now(timezone.utc).isoformat(),
            dedup_hash=uuid.uuid4().hex,
        )
        demo_articles.append(a)
        _articles[a.article_id] = a

    topic_counts = {}
    for art in demo_articles:
        topic_counts[art.topic] = topic_counts.get(art.topic, 0) + 1

    briefing_id = f"{agency_id}_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    briefing = Briefing(
        briefing_id=briefing_id,
        agency_id=agency_id,
        briefing_date=datetime.now().strftime("%B %d, %Y"),
        status="pending_approval",
        html_content=f"<h1>Demo Briefing — {agency.name}</h1><p>30 demo articles loaded.</p>",
        article_count=30,
        topic_counts=topic_counts,
        generated_at=_now(),
        approved_at="",
        delivered_at="",
        delivery_recipients=len(agency.distribution_list),
    )
    _briefings[briefing_id] = briefing

    # Persist demo run so it survives restarts (best-effort)
    try:
        from dataclasses import asdict
        from app.bulletin_intelligence import bulletin_store
        await bulletin_store.save_articles([asdict(a) for a in demo_articles])
        await bulletin_store.save_briefing(asdict(briefing))
    except Exception as e:
        logger.warning(f"Persist demo failed: {e}")

    return {
        "status": "demo_complete",
        "agency_id": agency_id,
        "agency_name": agency.name,
        "briefing_id": briefing_id,
        "articles_loaded": 30,
        "topic_distribution": topic_counts,
        "sources_represented": list(set(a.source_type for a in demo_articles)),
        "message": f"Demo complete. Approve briefing at POST /api/v1/bulletin/briefings/{briefing_id}/approve"
    }
