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
    register_agency, get_agency, list_agencies, get_briefing_history,
    _articles, _briefings,
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
    # The request model calls it `boolean_queries`; AgencyConfig's field is
    # `search_queries`. Map it across so AgencyConfig(**...) doesn't raise.
    data = req.dict()
    data["search_queries"] = data.pop("boolean_queries", [])
    config = AgencyConfig(**data)
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
@router.post("/run/{agency_id}")
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


@router.post("/run/{agency_id}/sync")
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


@router.get("/history/{agency_id}")
async def briefing_history(agency_id: str):
    """Full run history — all briefings for an agency (any status), newest first."""
    history = get_briefing_history(agency_id)
    return {
        "agency_id": agency_id,
        "count": len(history),
        "briefings": history,
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
    # Return without the big HTML/DOCX blobs for the summary view
    return {k: v for k, v in briefing.items() if k not in ("html_content", "docx_b64")}


@router.get("/briefings/{briefing_id}/preview")
async def preview_briefing(briefing_id: str):
    """Return the full HTML briefing for preview."""
    html = get_briefing_html(briefing_id)
    if not html:
        raise HTTPException(status_code=404, detail="Briefing not found")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@router.get("/briefings/{briefing_id}/docx")
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


@router.get("/briefings/{briefing_id}/pdf")
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


@router.get("/run/{agency_id}/preview")
async def run_and_preview(agency_id: str, lookback_hours: int = 48):
    """Run full cycle and return HTML briefing directly in browser."""
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
