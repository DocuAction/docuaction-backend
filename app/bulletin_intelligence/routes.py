"""
DocuAction Bulletin Intelligence — API Routes
Registers as /api/v1/bulletin on the main FastAPI app
"""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import logging

from app.bulletin_intelligence.engine import (
    AgencyConfig, run_daily_cycle, approve_and_deliver,
    get_editorial_queue, get_briefing, get_briefing_html,
    search_archive, get_archive_stats, run_llm_visibility_check,
    register_agency, get_agency, list_agencies,
    _articles, _briefings,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/bulletin", tags=["Bulletin Intelligence"])


# ── Health ─────────────────────────────────────────────────────────────────────
@router.get("/health")
async def health():
    return {
        "module": "bulletin_intelligence",
        "status": "active",
        "version": "1.0.0",
        "agencies_registered": len(list_agencies()),
        "articles_in_memory": len(_articles),
        "briefings_in_memory": len(_briefings),
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


@router.post("/agencies")
async def create_agency(req: AgencyCreateRequest):
    config = AgencyConfig(**req.dict())
    register_agency(config)
    return {"status": "registered", "agency_id": config.agency_id, "name": config.name}


@router.get("/agencies")
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


@router.get("/agencies/{agency_id}")
async def get_agency_endpoint(agency_id: str):
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return {
        "agency_id": agency.agency_id,
        "name": agency.name,
        "short_name": agency.short_name,
        "primary_color": agency.primary_color,
        "boolean_queries": agency.boolean_queries,
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
@router.post("/run/{agency_id}")
async def trigger_daily_cycle(
    agency_id: str,
    background_tasks: BackgroundTasks,
    auto_deliver: bool = False,
    lookback_hours: int = 24,
):
    """Trigger the daily intelligence cycle for an agency. Returns immediately; runs in background."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")

    background_tasks.add_task(run_daily_cycle, agency_id, auto_deliver, lookback_hours)
    return {
        "status": "started",
        "agency_id": agency_id,
        "agency_name": agency.name,
        "auto_deliver": auto_deliver,
        "message": "Daily cycle started in background. Check /queue for results."
    }


@router.post("/run/{agency_id}/sync")
async def trigger_daily_cycle_sync(
    agency_id: str,
    auto_deliver: bool = False,
    lookback_hours: int = 24,
):
    """Synchronous version — waits for completion. Use for demos and testing."""
    result = await run_daily_cycle(agency_id, auto_deliver, lookback_hours)
    return result


# ── Editorial Queue ────────────────────────────────────────────────────────────
@router.get("/queue/{agency_id}")
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


@router.post("/briefings/{briefing_id}/approve")
async def approve_briefing(briefing_id: str):
    """Editor approves briefing and triggers email delivery."""
    result = await approve_and_deliver(briefing_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/briefings/{briefing_id}")
async def get_briefing_endpoint(briefing_id: str):
    briefing = get_briefing(briefing_id)
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found")
    # Return without full HTML for summary view
    return {k: v for k, v in briefing.items() if k != "html_content"}


@router.get("/briefings/{briefing_id}/preview")
async def preview_briefing(briefing_id: str):
    """Return the full HTML briefing for preview."""
    html = get_briefing_html(briefing_id)
    if not html:
        raise HTTPException(status_code=404, detail="Briefing not found")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


# ── Archive ────────────────────────────────────────────────────────────────────
@router.get("/archive/{agency_id}")
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


@router.get("/archive/{agency_id}/stats")
async def archive_statistics(agency_id: str):
    """Get 12-month archive statistics — volume by topic, source type, and month."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return get_archive_stats(agency_id)


@router.get("/archive/{agency_id}/clips")
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
@router.post("/llm-visibility/{agency_id}")
async def llm_visibility_check(agency_id: str):
    """Run LLM visibility check — query Claude on what it knows about the agency."""
    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")
    return await run_llm_visibility_check(agency)


# ── Demo Endpoint ──────────────────────────────────────────────────────────────
@router.get("/demo/{agency_id}")
async def demo_cycle(agency_id: str = "fcc"):
    """Demo: run a mock daily cycle with simulated articles."""
    from app.bulletin_intelligence.engine import (
        Article, Briefing, _articles, _briefings, FCC_TOPIC_LABELS
    )
    import random, uuid
    from datetime import datetime, timezone

    agency = get_agency(agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail=f"Agency {agency_id} not found")

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
            title=f"[Demo] {FCC_TOPIC_LABELS.get(topic, topic)} Coverage — Article {i+1}",
            url=f"https://example.com/article/{i+1}",
            published_at=datetime.now(timezone.utc).isoformat(),
            summary=f"This is a demonstration article about {FCC_TOPIC_LABELS.get(topic, topic)} for the {agency.name} daily intelligence briefing.",
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
    from app.bulletin_intelligence.engine import _now
    briefing = Briefing(
        briefing_id=briefing_id,
        agency_id=agency_id,
        briefing_date=datetime.now().strftime("%B %d, %Y"),
        status="pending_approval",
        html_content=f"<h1>Demo Briefing — {agency.name}</h1><p>30 demo articles loaded.</p>",
        plain_text="",
        article_count=30,
        topic_counts=topic_counts,
        generated_at=_now(),
        approved_at="",
        delivered_at="",
        delivery_recipients=len(agency.distribution_list),
    )
    _briefings[briefing_id] = briefing

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
