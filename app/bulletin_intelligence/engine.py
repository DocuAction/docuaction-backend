"""
DocuAction Bulletin Intelligence — Multi-Source Intelligence Engine v2
News Sources:
  - GDELT Project API    FREE  — global news every 15 min, no key needed
  - Tavily AI Search     FREE  — 1,000/mo free, AI-optimized (TAVILY_API_KEY)
  - NewsAPI              FREE  — dev tier, 80K+ sources (NEWSAPI_KEY)
  - Claude web_search    —     — fallback, uses existing ANTHROPIC_API_KEY
  - Federal Register     FREE  — no key needed
  - Congress.gov         FREE  — CONGRESS_API_KEY (optional)

LLM Visibility Tracker (unique — no competitor has this):
  - Claude (Anthropic)   existing ANTHROPIC_API_KEY
  - ChatGPT (OpenAI)     OPENAI_API_KEY
  - Perplexity           PERPLEXITY_API_KEY (sonar has real-time web)
  - Gemini (Google)      GEMINI_API_KEY

Classification:  Claude Haiku
Briefing:        Claude Sonnet → FCC Daily News Summary format

Required env vars:
  ANTHROPIC_API_KEY      already set ✓
  SENDGRID_API_KEY       set in Railway
  TAVILY_API_KEY         free at tavily.com
  NEWSAPI_KEY            free at newsapi.org
  OPENAI_API_KEY         for ChatGPT LLM visibility
  PERPLEXITY_API_KEY     for Perplexity LLM visibility
  GEMINI_API_KEY         for Gemini LLM visibility
  CONGRESS_API_KEY       free at api.congress.gov
"""

import os, json, logging, asyncio, hashlib, re
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict, field

import httpx
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
SENDGRID_KEY    = os.getenv("SENDGRID_API_KEY", "")
CONGRESS_KEY    = os.getenv("CONGRESS_API_KEY", "")
TAVILY_KEY      = os.getenv("TAVILY_API_KEY", "")
NEWSAPI_KEY     = os.getenv("NEWSAPI_KEY", "")
OPENAI_KEY      = os.getenv("OPENAI_API_KEY", "")
PERPLEXITY_KEY  = os.getenv("PERPLEXITY_API_KEY", "")
GEMINI_KEY      = os.getenv("GEMINI_API_KEY", "")

TIMEOUT = httpx.Timeout(30.0)
HTTP_HEADERS = {"User-Agent": "DocuAction-BulletinIntelligence/1.0 (Alliance Global Tech)"}

# ── Topic taxonomy ─────────────────────────────────────────────────────────────
FCC_TOPICS = [
    "fcc_news_events", "consumers_advocacy", "media_broadcasting",
    "public_safety_emergency", "wireless_mobile", "ai_emerging_tech",
    "business_industry", "international_affairs", "space_communications", "spectrum_policy",
]

TOPIC_LABELS = {
    "fcc_news_events":         "FCC News & Events",
    "consumers_advocacy":      "Consumers & Advocacy",
    "media_broadcasting":      "Media & Broadcasting",
    "public_safety_emergency": "Public Safety",
    "wireless_mobile":         "Wireless & Mobile",
    "ai_emerging_tech":        "AI & Emerging Tech",
    "business_industry":       "Business & Industry",
    "international_affairs":   "International Affairs",
    "space_communications":    "Space Communications",
    "spectrum_policy":         "Spectrum & Policy",
    "other":                   "Other",
}
FCC_TOPIC_LABELS = TOPIC_LABELS  # alias for backward compatibility


# ── Data classes ───────────────────────────────────────────────────────────────
@dataclass
class AgencyConfig:
    agency_id: str
    name: str
    short_name: str
    primary_color: str
    search_queries: List[str]
    topics: List[str]
    distribution_email: str
    distribution_list: List[str]
    delivery_time_et: str = "07:30"
    include_broadcast: bool = True
    include_social: bool = True
    include_regulatory: bool = True
    archive_months: int = 12


@dataclass
class Article:
    article_id: str
    agency_id: str
    source: str
    source_type: str
    title: str
    url: str
    published_at: str
    summary: str
    full_text: str
    author: str
    outlet: str
    topic: str = "other"
    article_type: str = "news"
    relevance_score: float = 0.5
    sentiment: str = "neutral"
    is_paywalled: bool = False
    broadcast_clip_url: str = ""
    ingested_at: str = ""
    dedup_hash: str = ""


@dataclass
class Briefing:
    briefing_id: str
    agency_id: str
    briefing_date: str
    status: str
    html_content: str
    article_count: int
    topic_counts: Dict[str, int] = field(default_factory=dict)
    generated_at: str = ""
    approved_at: str = ""
    delivered_at: str = ""
    delivery_recipients: int = 0


# ── In-memory store ────────────────────────────────────────────────────────────
_articles:  Dict[str, Article]  = {}
_briefings: Dict[str, Briefing] = {}
_agencies:  Dict[str, AgencyConfig] = {}

# Cost guard: prevents overlapping/duplicate ingest cycles (each cycle makes
# ~20+ Claude calls). Maps agency_id -> start timestamp; a cycle is considered
# in-progress while a recent timestamp is present. Auto-expires after the TTL so
# a crashed cycle can never permanently block future runs.
_running_cycles: Dict[str, float] = {}
CYCLE_LOCK_TTL = 180  # seconds (covers a typical 1-2 min cycle)


def _hash(url: str, title: str) -> str:
    return hashlib.md5(f"{url}{title}".encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_pub(s: str) -> str:
    """Normalize a publish date to ISO 8601. Handles ISO, GDELT (YYYYMMDDTHHMMSSZ),
    and RFC 2822 (RSS). Falls back to now() if unparseable."""
    s = (s or "").strip()
    if not s:
        return _now()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except Exception:
        pass
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        pass
    # Unix epoch seconds (Reddit created_utc), guarded to year 2001+ to avoid
    # misreading short numeric strings as dates.
    try:
        v = float(s)
        if v >= 1_000_000_000:
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
    except Exception:
        pass
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).isoformat()
    except Exception:
        return _now()


def _dict_to_article(d: dict, agency_id: str, default_source_type: str = "news") -> Optional["Article"]:
    """Convert a raw ingester dict (GDELT, Reddit, etc.) into an Article. Returns
    None for unusable rows (no url/title)."""
    url = (d.get("url") or "").strip()
    title = (d.get("title") or "").strip()
    if not url or not title:
        return None
    summary = (d.get("summary") or "").strip() or title
    outlet = d.get("outlet") or d.get("source") or ""
    return Article(
        article_id=f"{agency_id}_{_hash(url, title)}",
        agency_id=agency_id,
        source=d.get("source", "") or default_source_type,
        source_type=d.get("source_type", default_source_type),
        title=title,
        url=url,
        published_at=_normalize_pub(d.get("published_at", "")),
        summary=summary,
        full_text=d.get("full_text", "") or summary,
        author=d.get("author", ""),
        outlet=outlet,
        ingested_at=_now(),
        dedup_hash=_hash(url, title),
    )


async def hydrate_from_store() -> Dict[str, int]:
    """Restore persisted articles/briefings into the in-memory cache on startup.

    Best-effort: if the store is empty or unavailable, the cache simply stays
    empty and the app runs as before.
    """
    try:
        from dataclasses import fields as _fields
        from app.bulletin_intelligence import bulletin_store
        articles, briefings = await bulletin_store.load_all()
        art_fields = {f.name for f in _fields(Article)}
        brief_fields = {f.name for f in _fields(Briefing)}
        for d in articles:
            try:
                _articles[d["article_id"]] = Article(**{k: v for k, v in d.items() if k in art_fields})
            except Exception:
                pass
        for d in briefings:
            try:
                _briefings[d["briefing_id"]] = Briefing(**{k: v for k, v in d.items() if k in brief_fields})
            except Exception:
                pass
        logger.info(f"Bulletin hydrate: {len(_articles)} articles, {len(_briefings)} briefings restored")
        return {"articles": len(_articles), "briefings": len(_briefings)}
    except Exception as e:
        logger.warning(f"Bulletin hydrate failed: {e}")
        return {"articles": 0, "briefings": 0}


# ── Agency management ──────────────────────────────────────────────────────────
def register_agency(config: AgencyConfig) -> None:
    _agencies[config.agency_id] = config
    logger.info(f"Bulletin: registered agency {config.name}")


def get_agency(agency_id: str) -> Optional[AgencyConfig]:
    return _agencies.get(agency_id)


def list_agencies() -> List[AgencyConfig]:
    return list(_agencies.values())


# ── Claude API helper ──────────────────────────────────────────────────────────
def _get_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=ANTHROPIC_KEY)


def _extract_text(content) -> str:
    """Extract plain text from Anthropic message content blocks."""
    parts = []
    for block in (content if isinstance(content, list) else [content]):
        if hasattr(block, 'type'):
            if block.type == 'text':
                parts.append(block.text)
        elif isinstance(block, dict) and block.get('type') == 'text':
            parts.append(block.get('text', ''))
    return '\n'.join(parts)


def _parse_json_safe(text: str) -> Any:
    """Parse JSON from Claude response — handles markdown code fences."""
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    # Find first [ or { and last ] or }
    start = min(
        (text.find('[') if '[' in text else len(text)),
        (text.find('{') if '{' in text else len(text))
    )
    end_bracket = text.rfind(']')
    end_brace = text.rfind('}')
    end = max(end_bracket, end_brace)

    if start <= end:
        text = text[start:end + 1]

    return json.loads(text)


# ── FCC Relevance Pre-Filter ──────────────────────────────────────────────────
FCC_KEYWORDS = {
    "fcc", "federal communications commission", "fcc chairman", "fcc commissioner",
    "brendan carr", "olivia trusty", "anna gomez",
    "spectrum auction", "spectrum license", "aws-3", "spectrum policy",
    "robocall", "tcpa", "stir-shaken", "robocall mitigation",
    "net neutrality", "open internet", "e-rate", "lifeline program",
    "media ownership", "broadcast license", "radio license", "tv license",
    "submarine cable", "undersea cable", "subsea cable",
    "911 fcc", "e911", "psap", "emergency alert system",
    "fcc enforcement", "fcc fine", "fcc ruling", "fcc vote", "fcc proposes",
    "fcc approves", "fcc order", "fcc notice", "fcc meeting",
    "telecom policy", "telecommunications policy", "fcc regulation",
}

def _is_fcc_relevant(title: str, summary: str) -> bool:
    """Return True only if article is genuinely FCC-relevant."""
    text = (title + " " + summary).lower()
    return any(kw in text for kw in FCC_KEYWORDS)




# ── INGESTION: RSS Feeds (Appendix B Sources — FREE, always FCC-relevant) ─────
FCC_RSS_FEEDS = {
    "fcc_news_events": [
        ("https://www.fcc.gov/news-events/rss", "FCC"),
        ("https://www.fcc.gov/rss/headlines", "FCC"),
    ],
    "wireless_mobile": [
        ("https://www.fiercewireless.com/rss/xml", "FierceWireless"),
        ("https://www.rcrwireless.com/feed", "RCR Wireless"),
    ],
    "media_broadcasting": [
        ("https://www.radioworld.com/feed", "Radio World"),
        ("https://www.tvtechnology.com/rss/all", "TV Technology"),
        ("https://www.multichannel.com/rss/all", "Multichannel News"),
        ("https://rbr.com/feed/", "RBR"),
    ],
    "consumers_advocacy": [
        ("https://broadbandbreakfast.com/feed/", "Broadband Breakfast"),
        ("https://www.telecompetitor.com/feed/", "Telecompetitor"),
    ],
    "spectrum_policy": [
        ("https://broadbandbreakfast.com/feed/", "Broadband Breakfast"),
        ("https://www.fiercewireless.com/rss/xml", "FierceWireless"),
    ],
    "public_safety_emergency": [
        ("https://www.fcc.gov/news-events/rss", "FCC"),
    ],
    "business_industry": [
        ("https://thehill.com/policy/technology/feed/", "The Hill"),
        ("https://www.politico.com/rss/politicopicks.xml", "Politico"),
    ],
    "international_affairs": [
        ("https://www.telegeography.com/feed/", "TeleGeography"),
    ],
    "ai_emerging_tech": [
        ("https://thehill.com/policy/technology/feed/", "The Hill"),
    ],
}

async def ingest_rss(agency: AgencyConfig, lookback_hours: int = 24) -> list:
    """
    Ingest RSS feeds from Appendix B sources.
    Always FCC-relevant — no filtering needed.
    FREE — no API key required.
    """
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    seen = set()

    all_feeds = []
    for topic, feeds in FCC_RSS_FEEDS.items():
        for url, outlet in feeds:
            all_feeds.append((url, outlet, topic))

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for feed_url, outlet, topic in all_feeds:
            try:
                resp = await client.get(feed_url, headers=HTTP_HEADERS, follow_redirects=True)
                if resp.status_code != 200:
                    continue

                root = ET.fromstring(resp.text)
                ns = {"atom": "http://www.w3.org/2005/Atom"}

                # Handle both RSS and Atom feeds
                items = root.findall(".//item") or root.findall(".//atom:entry", ns)

                for item in items[:10]:
                    # Extract fields handling both RSS and Atom
                    title = (
                        getattr(item.find("title"), "text", "") or
                        getattr(item.find("atom:title", ns), "text", "") or ""
                    ).strip()

                    link = (
                        getattr(item.find("link"), "text", "") or
                        (item.find("atom:link", ns).get("href") if item.find("atom:link", ns) is not None else "") or ""
                    ).strip()

                    pub_date = (
                        getattr(item.find("pubDate"), "text", "") or
                        getattr(item.find("atom:published", ns), "text", "") or
                        getattr(item.find("atom:updated", ns), "text", "") or ""
                    ).strip()

                    description = (
                        getattr(item.find("description"), "text", "") or
                        getattr(item.find("atom:summary", ns), "text", "") or ""
                    ).strip()[:400]

                    if not title or not link:
                        continue

                    # Skip duplicates
                    dedup = _hash(link, title)
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    # Date filter
                    try:
                        pub_dt = parsedate_to_datetime(pub_date)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            continue
                        pub_iso = pub_dt.isoformat()
                    except Exception:
                        pub_iso = _now()

                    art = Article(
                        article_id=f"{agency.agency_id}_rss_{dedup}",
                        agency_id=agency.agency_id,
                        source="rss",
                        source_type="news",
                        title=title,
                        url=link,
                        published_at=pub_iso,
                        summary=description,
                        full_text=description,
                        author="",
                        outlet=outlet,
                        topic=topic,           # pre-assigned by feed category
                        relevance_score=0.75,  # RSS feeds are always on-topic
                        ingested_at=_now(),
                        dedup_hash=dedup,
                    )
                    articles.append(art)

            except Exception as e:
                logger.error(f"RSS error {feed_url}: {e}")

    logger.info(f"RSS: {len(articles)} articles for {agency.agency_id}")
    return articles

# ── INGESTION: GDELT Project (FREE — no key needed) ──────────────────────────
# GDELT monitors 300+ languages, 65+ countries, updates every 15 minutes
# Perfect for FCC broadcast, international, and US domestic news

async def ingest_gdelt(agency: AgencyConfig, lookback_hours: int = 24) -> List[Article]:
    """
    GDELT Project API — completely free, real-time global news monitoring.
    Updates every 15 minutes. No API key required.
    """
    articles = []
    # GDELT: exact phrase only - strict FCC filter
    query = "Federal Communications Commission sourcelang:eng"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query": query,
                    "mode": "artlist",
                    "maxrecords": 25,
                    "timespan": f"{min(lookback_hours, 24)}H",
                    "sort": "DateDesc",
                    "format": "json",
                },
                headers=HTTP_HEADERS
            )
            if resp.status_code == 200:
                data = resp.json()
                for art_data in data.get("articles", []):
                    title = art_data.get("title", "")
                    url   = art_data.get("url", "")
                    if not title or not url:
                        continue
                    dedup = _hash(url, title)
                    art = Article(
                        article_id=f"{agency.agency_id}_gdelt_{dedup}",
                        agency_id=agency.agency_id,
                        source="gdelt",
                        source_type="news",
                        title=title,
                        url=url,
                        published_at=art_data.get("seendate", _now()),
                        summary=title,
                        full_text=title,
                        author="",
                        outlet=art_data.get("domain", "News"),
                        relevance_score=0.6,
                        ingested_at=_now(),
                        dedup_hash=dedup,
                    )
                    articles.append(art)
    except Exception as e:
        logger.error(f"GDELT ingestion error: {e}")

    logger.info(f"GDELT: {len(articles)} articles for {agency.agency_id}")
    return articles


# ── INGESTION: Tavily AI Search (1,000 free searches/month) ──────────────────
# AI-optimized search built specifically for AI agents — returns clean summaries

async def ingest_tavily(agency: AgencyConfig, lookback_hours: int = 24) -> List[Article]:
    """
    Tavily AI Search — purpose-built for AI agents.
    Free tier: 1,000 searches/month. Sign up at tavily.com.
    Set TAVILY_API_KEY in Railway.
    """
    if not TAVILY_KEY:
        return []

    articles = []
    for query in agency.search_queries[:3]:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Content-Type": "application/json"},
                    json={
                        "api_key": TAVILY_KEY,
                        "query": query,
                        "search_depth": "advanced",
                        "include_answer": True,
                        "include_raw_content": False,
                        "max_results": 8,
                        "include_domains": [],
                        "exclude_domains": [],
                        "topic": "news",
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for r in data.get("results", []):
                        dedup = _hash(r.get("url",""), r.get("title",""))
                        art = Article(
                            article_id=f"{agency.agency_id}_tavily_{dedup}",
                            agency_id=agency.agency_id,
                            source="tavily",
                            source_type="news",
                            title=r.get("title",""),
                            url=r.get("url",""),
                            published_at=r.get("published_date", _now()),
                            summary=r.get("content","")[:400],
                            full_text=r.get("content",""),
                            author="",
                            outlet=r.get("url","").split("/")[2] if r.get("url") else "Web",
                            relevance_score=r.get("score", 0.7),
                            ingested_at=_now(),
                            dedup_hash=dedup,
                        )
                        articles.append(art)
        except Exception as e:
            logger.error(f"Tavily error for query '{query}': {e}")

    logger.info(f"Tavily: {len(articles)} articles for {agency.agency_id}")
    return articles


# ── INGESTION: NewsAPI (free dev tier, 80K+ sources) ─────────────────────────

async def ingest_newsapi(agency: AgencyConfig, lookback_hours: int = 24) -> List[Article]:
    """
    NewsAPI — 80,000+ news sources. Free dev tier.
    Sign up at newsapi.org. Set NEWSAPI_KEY in Railway.
    """
    if not NEWSAPI_KEY:
        return []

    articles = []
    from_date = (datetime.now() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%S")
    query = " OR ".join([f'"{q.split()[0]}"' for q in agency.search_queries[:2]])

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "apiKey": NEWSAPI_KEY,
                    "q": query,
                    "from": from_date,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": 20,
                },
                headers=HTTP_HEADERS
            )
            if resp.status_code == 200:
                for r in resp.json().get("articles", []):
                    dedup = _hash(r.get("url",""), r.get("title",""))
                    art = Article(
                        article_id=f"{agency.agency_id}_newsapi_{dedup}",
                        agency_id=agency.agency_id,
                        source="newsapi",
                        source_type="news",
                        title=r.get("title",""),
                        url=r.get("url",""),
                        published_at=r.get("publishedAt", _now()),
                        summary=r.get("description","")[:400],
                        full_text=r.get("content","")[:800],
                        author=r.get("author",""),
                        outlet=r.get("source",{}).get("name","News"),
                        is_paywalled=False,
                        ingested_at=_now(),
                        dedup_hash=dedup,
                    )
                    articles.append(art)
    except Exception as e:
        logger.error(f"NewsAPI error: {e}")

    logger.info(f"NewsAPI: {len(articles)} articles for {agency.agency_id}")
    return articles


# ── INGESTION: Claude Web Search → News ──────────────────────────────────────
# TO REPLACE WITH PERIGON LATER: swap this function body only.
# Function signature stays identical so nothing else changes.

async def ingest_news(agency: AgencyConfig, lookback_hours: int = 24) -> List[Article]:
    """
    Uses Claude web_search tool to find real current news articles.
    Returns same Article format as Perigon integration would.
    Swap body with Perigon API call when ready.
    """
    if not ANTHROPIC_KEY:
        logger.warning("ANTHROPIC_API_KEY not set")
        return []

    client = _get_client()
    articles = []
    queries_to_run = agency.search_queries  # run ALL topic queries for full coverage

    for query in queries_to_run:
        try:
            # Step 1: Claude searches the web with FCC-specific query
            fcc_query = f"site:reuters.com OR site:politico.com OR site:broadbandbreakfast.com OR site:fiercewireless.com {query}"
            search_response = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": f"Search for FCC Federal Communications Commission news from the last {lookback_hours} hours. Query: {query}. Focus on telecom industry news sources like Reuters, Politico, Broadband Breakfast, FierceWireless, Broadcasting+Cable, Multichannel News."
                }]
            )

            # Step 2: Extract structured article data
            extract_response = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2000,
                messages=[
                    {"role": "user", "content": f"Search for news about: {query}"},
                    {"role": "assistant", "content": search_response.content},
                    {"role": "user", "content": """From those search results, extract up to 8 news articles as a JSON array.
Each object: title, url, outlet, author, published_at (ISO date or today), summary (2 sentences), is_paywalled (bool).
Return ONLY the JSON array. No explanation."""}
                ]
            )

            raw = _extract_text(extract_response.content)
            extracted = _parse_json_safe(raw)

            if not isinstance(extracted, list):
                extracted = [extracted] if isinstance(extracted, dict) else []

            for item in extracted:
                if not item.get('title') or not item.get('url'):
                    continue
                dedup = _hash(item.get('url', ''), item.get('title', ''))
                art = Article(
                    article_id=f"{agency.agency_id}_news_{dedup}",
                    agency_id=agency.agency_id,
                    source="claude_search",
                    source_type="news",
                    title=item.get('title', ''),
                    url=item.get('url', ''),
                    published_at=item.get('published_at', _now()),
                    summary=item.get('summary', ''),
                    full_text=item.get('summary', ''),
                    author=item.get('author', ''),
                    outlet=item.get('outlet', 'Web'),
                    is_paywalled=bool(item.get('is_paywalled', False)),
                    ingested_at=_now(),
                    dedup_hash=dedup,
                )
                articles.append(art)

            await asyncio.sleep(0.3)  # rate limit courtesy

        except Exception as e:
            logger.error(f"News search error for query '{query}': {e}")

    logger.info(f"News ingestion: {len(articles)} articles for {agency.agency_id}")
    return articles


# ── INGESTION: Claude Web Search → Broadcast ─────────────────────────────────
# TO REPLACE WITH TVEYES LATER: swap this function body only.

async def ingest_broadcast(agency: AgencyConfig, lookback_hours: int = 24) -> List[Article]:
    """
    Uses Claude to find and summarize broadcast TV/radio coverage.
    Returns broadcast-format Articles with clip placeholders.
    Swap body with TVEyes API call when ready.
    """
    if not ANTHROPIC_KEY or not agency.include_broadcast:
        return []

    client = _get_client()
    articles = []
    query = agency.search_queries[0] if agency.search_queries else agency.short_name

    try:
        search_response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": f"Search for TV news broadcasts, radio coverage, and video news segments about: {query}. Look for CNN, Fox News, MSNBC, C-SPAN, NPR, ABC News, CBS News, NBC News coverage."
            }]
        )

        extract_response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[
                {"role": "user", "content": f"Search for TV/radio broadcast coverage of: {query}"},
                {"role": "assistant", "content": search_response.content},
                {"role": "user", "content": """Extract up to 4 broadcast/video news items as a JSON array.
Each object: title, url, outlet (TV/radio station), published_at, summary (1-2 sentences about what was broadcast).
Return ONLY the JSON array."""}
            ]
        )

        raw = _extract_text(extract_response.content)
        extracted = _parse_json_safe(raw)

        if isinstance(extracted, list):
            for item in extracted:
                dedup = _hash(item.get('url', ''), item.get('title', ''))
                art = Article(
                    article_id=f"{agency.agency_id}_tv_{dedup}",
                    agency_id=agency.agency_id,
                    source="claude_broadcast_search",
                    source_type="broadcast",
                    title=f"[Broadcast] {item.get('title', '')}",
                    url=item.get('url', ''),
                    published_at=item.get('published_at', _now()),
                    summary=item.get('summary', ''),
                    full_text=item.get('summary', ''),
                    author="",
                    outlet=item.get('outlet', 'Broadcast'),
                    broadcast_clip_url=item.get('url', ''),
                    ingested_at=_now(),
                    dedup_hash=dedup,
                )
                articles.append(art)

    except Exception as e:
        logger.error(f"Broadcast search error: {e}")

    logger.info(f"Broadcast: {len(articles)} clips for {agency.agency_id}")
    return articles


# ── INGESTION: Claude Web Search → Social ─────────────────────────────────────
# TO REPLACE WITH TWITTER/REDDIT APIS LATER: swap this function body only.

async def ingest_social(agency: AgencyConfig) -> List[Article]:
    """
    Uses Claude to find social media discussions about the agency's topics.
    Swap body with Twitter/Reddit API calls when ready.
    """
    if not ANTHROPIC_KEY or not agency.include_social:
        return []

    client = _get_client()
    articles = []
    query = f"{agency.short_name} {agency.search_queries[0][:60] if agency.search_queries else ''}"

    try:
        search_response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": f"Search for social media discussions, tweets, Reddit posts, and public commentary about: {query}. Focus on significant public discussion."
            }]
        )

        extract_response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[
                {"role": "user", "content": f"Search social media about: {query}"},
                {"role": "assistant", "content": search_response.content},
                {"role": "user", "content": """Extract up to 4 social media items (tweets, Reddit posts, LinkedIn posts, YouTube comments) as JSON array.
Each object: title (brief description), url, outlet (Twitter/Reddit/etc.), published_at, summary, author.
Return ONLY the JSON array."""}
            ]
        )

        raw = _extract_text(extract_response.content)
        extracted = _parse_json_safe(raw)

        if isinstance(extracted, list):
            for item in extracted:
                dedup = _hash(item.get('url', ''), item.get('title', ''))
                art = Article(
                    article_id=f"{agency.agency_id}_social_{dedup}",
                    agency_id=agency.agency_id,
                    source="claude_social_search",
                    source_type="social",
                    title=item.get('title', ''),
                    url=item.get('url', ''),
                    published_at=item.get('published_at', _now()),
                    summary=item.get('summary', ''),
                    full_text=item.get('summary', ''),
                    author=item.get('author', ''),
                    outlet=item.get('outlet', 'Social'),
                    ingested_at=_now(),
                    dedup_hash=dedup,
                )
                articles.append(art)

    except Exception as e:
        logger.error(f"Social search error: {e}")

    logger.info(f"Social: {len(articles)} posts for {agency.agency_id}")
    return articles


# ── INGESTION: Federal Register + Congress.gov (REAL — FREE APIs) ─────────────
# These stay as real APIs — no cost, no key required for Federal Register.

async def ingest_regulatory(agency: AgencyConfig) -> List[Article]:
    """Real Federal Register and Congress.gov APIs — free, no API key for Federal Register."""
    if not agency.include_regulatory:
        return []

    articles = []

    # Federal Register — completely free, no auth
    for query in agency.search_queries[:2]:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    "https://www.federalregister.gov/api/v1/documents.json",
                    params={
                        "conditions[term]": query[:80],
                        "conditions[publication_date][gte]": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                        "fields[]": ["title", "type", "abstract", "html_url", "publication_date", "agencies"],
                        "per_page": 5,
                        "order": "newest",
                    },
                    headers=HTTP_HEADERS
                )
                if resp.status_code == 200:
                    for doc in resp.json().get("results", []):
                        dedup = _hash(doc.get("html_url", ""), doc.get("title", ""))
                        agency_names = ", ".join([a.get("name", "") for a in doc.get("agencies", [])])
                        art = Article(
                            article_id=f"{agency.agency_id}_fr_{dedup}",
                            agency_id=agency.agency_id,
                            source="federal_register",
                            source_type="regulatory",
                            title=f"[Federal Register] {doc.get('title', '')}",
                            url=doc.get("html_url", ""),
                            published_at=doc.get("publication_date", datetime.now().strftime("%Y-%m-%d")),
                            summary=doc.get("abstract", "")[:400],
                            full_text=doc.get("abstract", ""),
                            author="",
                            outlet=f"Federal Register — {agency_names}",
                            article_type="regulatory",
                            relevance_score=0.85,
                            ingested_at=_now(),
                            dedup_hash=dedup,
                        )
                        articles.append(art)
        except Exception as e:
            logger.error(f"Federal Register error: {e}")

    # Congress.gov — free, optional API key for higher rate limits
    if CONGRESS_KEY:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    "https://api.congress.gov/v3/bill",
                    params={
                        "apiKey": CONGRESS_KEY,
                        "format": "json",
                        "limit": 5,
                        "fromDateTime": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z"),
                    },
                    headers=HTTP_HEADERS
                )
                if resp.status_code == 200:
                    for bill in resp.json().get("bills", []):
                        dedup = _hash(bill.get("url", ""), bill.get("title", ""))
                        art = Article(
                            article_id=f"{agency.agency_id}_cg_{dedup}",
                            agency_id=agency.agency_id,
                            source="congress_gov",
                            source_type="regulatory",
                            title=f"[Congress] {bill.get('title', 'Untitled Bill')}",
                            url=bill.get("url", ""),
                            published_at=bill.get("latestAction", {}).get("actionDate", _now()),
                            summary=bill.get("latestAction", {}).get("text", ""),
                            full_text=bill.get("title", ""),
                            author="",
                            outlet=f"Congress.gov — {bill.get('type', '')} {bill.get('number', '')}",
                            article_type="regulatory",
                            relevance_score=0.80,
                            ingested_at=_now(),
                            dedup_hash=dedup,
                        )
                        articles.append(art)
        except Exception as e:
            logger.error(f"Congress.gov error: {e}")

    logger.info(f"Regulatory: {len(articles)} items for {agency.agency_id}")
    return articles


# ── Deduplication ──────────────────────────────────────────────────────────────
def deduplicate(articles: List[Article]) -> List[Article]:
    seen_hashes, seen_titles, unique = set(), {}, []
    for art in sorted(articles, key=lambda a: a.relevance_score, reverse=True):
        if art.dedup_hash in seen_hashes:
            continue
        title_key = art.title[:60].lower().strip()
        if title_key in seen_titles:
            continue
        seen_hashes.add(art.dedup_hash)
        seen_titles[title_key] = True
        unique.append(art)
    logger.info(f"Dedup: {len(articles)} → {len(unique)} articles")
    return unique


# ── Classification: Claude Haiku ──────────────────────────────────────────────
async def classify_articles(articles: List[Article], agency: AgencyConfig) -> List[Article]:
    if not ANTHROPIC_KEY or not articles:
        return articles

    client = _get_client()
    topics_str = "\n".join([f"  {t}: {TOPIC_LABELS.get(t, t)}" for t in agency.topics])
    batch_size = 8
    classified = []

    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        items = [{"id": a.article_id, "title": a.title, "summary": a.summary[:200]} for a in batch]

        prompt = f"""You are classifying news for a daily intelligence briefing about {agency.name} ({agency.short_name}).
Return a JSON array, one object per article:
  id, topic (from list), article_type (news/opinion/analysis/editorial/press_release/regulatory), sentiment (positive/negative/neutral), relevance_score (0.0-1.0)

relevance_score = how directly the article is about {agency.short_name} itself — its leadership/officials, decisions, proceedings, rulings, votes, or enforcement. Be STRICT and do not inflate:
  0.85-1.0 : explicitly about {agency.short_name} actions, officials, or proceedings
  0.55-0.80: a topic {agency.short_name} regulates, with {agency.short_name} clearly involved
  0.40-0.54: {agency.short_name}-adjacent but the agency is not central
  0.00-0.39: general tech/business/world news with no real {agency.short_name} connection
If {agency.short_name} (the agency, its commissioners, or its specific actions) is not actually involved, score BELOW 0.4 even if the topic is telecom/media/tech.

Topics:
{topics_str}
  other: Does not fit above topics

Articles:
{json.dumps(items)}

Return ONLY the JSON array."""

        try:
            resp = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            results = _parse_json_safe(_extract_text(resp.content))
            rmap = {r["id"]: r for r in (results if isinstance(results, list) else [])}

            for art in batch:
                r = rmap.get(art.article_id, {})
                art.topic          = r.get("topic", "other")
                art.article_type   = r.get("article_type", "news")
                art.sentiment      = r.get("sentiment", "neutral")
                art.relevance_score = float(r.get("relevance_score", 0.5))
                classified.append(art)

        except Exception as e:
            logger.error(f"Classification error: {e}")
            classified.extend(batch)

    logger.info(f"Classified {len(classified)} articles for {agency.agency_id}")
    return classified


# ── Briefing Generator: Claude Sonnet ─────────────────────────────────────────
async def generate_briefing_html(agency: AgencyConfig, articles: List[Article], briefing_date: str) -> str:
    if not ANTHROPIC_KEY:
        return _simple_html(agency, articles, briefing_date)

    client = _get_client()

    # Group by topic, top 5 per topic
    by_topic: Dict[str, List[Article]] = {}
    for art in articles:
        if art.relevance_score >= 0.4:
            by_topic.setdefault(art.topic, []).append(art)

    context = []
    for topic, arts in sorted(by_topic.items()):
        label = TOPIC_LABELS.get(topic, topic)
        context.append(f"\n=== {label} ===")
        for art in sorted(arts, key=lambda a: a.relevance_score, reverse=True)[:5]:
            clip = f"[BROADCAST CLIP: {art.broadcast_clip_url}]" if art.broadcast_clip_url else ""
            lock = "[SUBSCRIPTION REQUIRED]" if art.is_paywalled else ""
            reg  = "[REGULATORY]" if art.source_type == "regulatory" else ""
            context.append(f"""
TITLE: {art.title}
TYPE: {art.article_type} | SENTIMENT: {art.sentiment} | SCORE: {art.relevance_score:.2f} {reg}
OUTLET: {art.outlet} | AUTHOR: {art.author}
URL: {art.url} {clip} {lock}
SUMMARY: {art.summary[:300]}
---""")

    # Coverage window
    from datetime import datetime as _dt, timedelta as _td
    _today = _dt.now()
    _start = (_today - _td(days=3)).strftime("%B %d")
    _window = f"{_start} - {_today.strftime('%B %d, %Y')}"

    # Count by topic for social media estimate
    total_articles = len(articles)
    social_arts = [a for a in articles if a.source_type == "social"]

    prompt = f"""You are generating the FCC Daily News Briefing exactly matching this official format.

OUTPUT FORMAT — follow this exactly:

===HTML HEADER===
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">
<title>{agency.short_name} Daily News Summary — {briefing_date}</title>
<style>
  body {{font-family: Arial, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #333; font-size: 13px; line-height: 1.5;}}
  h1 {{color: #0B3C5D; font-size: 22px; border-bottom: 2px solid #0078D4; padding-bottom: 8px; margin-bottom: 4px;}}
  h2 {{color: #0B3C5D; font-size: 15px; margin-top: 24px; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #ddd;}}
  .date {{font-size: 18px; font-weight: bold; color: #0B3C5D; margin-bottom: 0;}}
  .agency {{font-size: 20px; font-weight: bold; color: #0B3C5D;}}
  .toc-item {{margin: 3px 0; padding-left: 0;}}
  .outlet {{font-weight: bold; text-transform: uppercase;}}
  .sub-required {{color: #666; font-style: italic;}}
  .similar {{padding-left: 20px; margin: 2px 0; color: #555; font-size: 12px; list-style: none;}}
  .similar li::before {{content: "▪ "; color: #0078D4;}}
  .story {{margin-top: 18px; padding-bottom: 12px; border-bottom: 1px solid #f0f0f0;}}
  .story-headline {{font-weight: bold; font-size: 13px; margin-bottom: 6px;}}
  .story-body {{color: #333; margin-bottom: 8px;}}
  .backtotop {{font-size: 11px; color: #0078D4; text-decoration: none;}}
  .social-section {{background: #f9f9f9; border: 1px solid #ddd; padding: 14px; margin-top: 24px; border-radius: 4px;}}
  .footer {{margin-top: 30px; padding-top: 12px; border-top: 1px solid #ddd; font-size: 11px; color: #888;}}
</style>
</head>
<body>
<p class="date">{briefing_date}</p>
<p class="agency">{agency.name}</p>
<h1>Daily News Summary</h1>
<p style="color:#666;font-size:12px;">Coverage window: {_window}</p>

===TABLE OF CONTENTS (TOC)===
For EACH topic section that has articles, write:

<h2>[Topic Name]</h2>
<p class="toc-item"><span class="outlet">OUTLET NAME:</span> <a href="#story-[id]">Article Title</a> [add (SUBSCRIPTION REQUIRED) if paywalled]</p>

RULES FOR TOC:
- Outlet name in ALL CAPS followed by colon
- If same story covered by multiple outlets, list PRIMARY outlet only, group others as similar stories in summaries section
- FCC.gov as source: include MAXIMUM 2 items total across entire briefing
- Mark paywalled: (SUBSCRIPTION REQUIRED) in parentheses after title
- Each article gets unique anchor id like #story-1, #story-2 etc.

===STORY SUMMARIES===
After the complete TOC, write the summaries section:

<h2>Story Summaries</h2>

For each PRIMARY story:
<div class="story" id="story-[id]">
<p class="story-headline"><span class="outlet">OUTLET:</span> <a href="URL">Title</a>[if paywalled: <span class="sub-required"> (SUBSCRIPTION REQUIRED)</span>]</p>
[If NOT paywalled: <p class="story-body">2-3 factual sentences summarizing the article.</p>]
[If paywalled: <p class="sub-required">[SUBSCRIPTION REQUIRED]</p>]
[If has similar stories:]
<p style="font-size:12px;font-weight:bold;margin-bottom:3px;">Similar stories:</p>
<ul class="similar">
  <li><span class="outlet">OUTLET:</span> <a href="URL">Title</a></li>
</ul>
<a href="#top" class="backtotop">Back to Top</a>
</div>

===SOCIAL MEDIA SUMMARY===
At the end, write a social media summary section:

<div class="social-section">
<h2>Social Media Summary</h2>
<p>From {_start}–{_today.strftime("%B %d")}, social media posts mentioned {agency.short_name} across major platforms. 
Most conversation took place on X/Twitter, followed by Reddit, LinkedIn, and YouTube.
[Write 2-3 sentences summarizing the most significant social media posts found about the agency's topics, based on any social media articles in the list below. Include reach/engagement numbers if available from the articles.]</p>
</div>

===FOOTER===
<div class="footer">
<p>Generated by DocuAction AI — Alliance Global Tech, Inc. | {agency.distribution_email}<br>
Solicitation 7571MN26Q00027 · TEFCA ARC · FCC Daily News Briefing Service</p>
</div>
</body></html>

CRITICAL RULES:
1. NO DUPLICATE STORIES — if multiple outlets cover same story, pick the most authoritative outlet as primary, list others as similar stories
2. FCC.GOV SOURCE — include maximum 2 articles from fcc.gov across the entire briefing
3. SUBSCRIPTION REQUIRED — mark clearly any paywalled article, do not summarize paywalled content
4. FACTUAL ONLY — no editorial opinion, exactly like an official government clipping service
5. OUTLET NAMES — always in ALL CAPS (REUTERS, BROADBAND BREAKFAST, etc.)
6. ALL CAPS for section headers matching FCC taxonomy

Articles to process:
{"".join(context)}

Write the complete HTML document now — exact FCC Daily News Briefing format."""

    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}]
        )
        html = _extract_text(resp.content)
        # Ensure it's clean HTML
        if not html.strip().startswith('<!') and not html.strip().startswith('<html'):
            html = f"<!DOCTYPE html>\n{html}"
        return html
    except Exception as e:
        logger.error(f"Briefing generation error: {e}")
        return _simple_html(agency, articles, briefing_date)


def _simple_html(agency: AgencyConfig, articles: List[Article], briefing_date: str) -> str:
    rows = "".join(
        f'<tr><td><a href="{a.url}">{a.title}</a></td><td>{a.outlet}</td><td>{TOPIC_LABELS.get(a.topic, a.topic)}</td></tr>'
        for a in articles[:20]
    )
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>{agency.name} Briefing {briefing_date}</title></head>
<body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;padding:20px">
<h1 style="color:{agency.primary_color}">{agency.name} Morning Briefing — {briefing_date}</h1>
<table border="1" cellpadding="8" cellspacing="0" width="100%">{rows}</table>
<p style="color:#666;font-size:12px">Generated by DocuAction AI — Alliance Global Tech, Inc.</p>
</body></html>"""


# ── Email Delivery: SendGrid ───────────────────────────────────────────────────
async def deliver_briefing(agency: AgencyConfig, html: str, briefing_date: str) -> Dict[str, Any]:
    if not SENDGRID_KEY:
        logger.warning("SENDGRID_API_KEY not set — dry run mode")
        return {"status": "dry_run", "recipients": len(agency.distribution_list)}

    subject = f"{agency.short_name} Morning Intelligence Briefing — {briefing_date}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
                json={
                    "personalizations": [{"to": [{"email": e} for e in agency.distribution_list]}],
                    "from": {"email": agency.distribution_email, "name": f"{agency.name} Intelligence"},
                    "subject": subject,
                    "content": [{"type": "text/html", "value": html}],
                }
            )
            resp.raise_for_status()
            return {"status": "delivered", "recipients": len(agency.distribution_list), "subject": subject}
    except Exception as e:
        logger.error(f"SendGrid error: {e}")
        return {"status": "error", "error": str(e)}


# ── LLM Visibility Tracker — Multi-AI (UNIQUE FEATURE) ───────────────────────
# No competitor tracks what AI engines say about a federal agency.
# This answers: "When a journalist asks ChatGPT about the FCC, what does it say?"

async def _query_openai(question: str) -> str:
    """Query ChatGPT for LLM visibility check."""
    if not OPENAI_KEY:
        return "OpenAI API key not configured (set OPENAI_API_KEY in Railway)"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 400,
                    "messages": [{"role": "user", "content": question}]
                }
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"ChatGPT error: {e}"


async def _query_perplexity(question: str) -> str:
    """Query Perplexity (has real-time web access via sonar model)."""
    if not PERPLEXITY_KEY:
        return "Perplexity API key not configured (set PERPLEXITY_API_KEY in Railway)"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "sonar",
                    "max_tokens": 400,
                    "messages": [{"role": "user", "content": question}]
                }
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Perplexity error: {e}"


async def _query_gemini(question: str) -> str:
    """Query Google Gemini for LLM visibility check."""
    if not GEMINI_KEY:
        return "Gemini API key not configured (set GEMINI_API_KEY in Railway)"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": question}]}]}
            )
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"Gemini error: {e}"


async def run_llm_visibility_check(agency: AgencyConfig) -> Dict[str, Any]:
    """
    Query all major AI engines to see what they say about the agency.
    Unique feature — no media monitoring vendor offers this.
    Results show: AI engine awareness, accuracy, recency of information.
    """
    questions = [
        f"What is the {agency.name} ({agency.short_name}) currently working on? What are their main priorities?",
        f"What are the most important recent decisions or actions taken by the {agency.name}?",
        f"What controversies, criticisms, or issues is the {agency.name} currently facing?",
        f"Who are the current key leaders and commissioners at the {agency.name}?",
    ]

    results = {}

    for question in questions:
        q_results = {}

        # Claude (existing)
        if ANTHROPIC_KEY:
            try:
                client = _get_client()
                resp = await client.messages.create(
                    model="claude-haiku-4-5", max_tokens=400,
                    messages=[{"role": "user", "content": question}]
                )
                q_results["Claude (Anthropic)"] = _extract_text(resp.content)
            except Exception as e:
                q_results["Claude (Anthropic)"] = f"Error: {e}"

        # ChatGPT
        q_results["ChatGPT (OpenAI)"] = await _query_openai(question)

        # Perplexity
        q_results["Perplexity"] = await _query_perplexity(question)

        # Gemini
        q_results["Gemini (Google)"] = await _query_gemini(question)

        results[question] = q_results

    return {
        "agency_id": agency.agency_id,
        "agency_name": agency.name,
        "checked_at": _now(),
        "engines_queried": ["Claude (Anthropic)", "ChatGPT (OpenAI)", "Perplexity", "Gemini (Google)"],
        "questions_asked": len(questions),
        "results": results,
        "summary": f"Queried 4 AI engines on {len(questions)} questions about {agency.name}. This report shows AI engine awareness and information recency — a unique competitive differentiator unavailable from any other media monitoring vendor.",
    }


# ── Archive search ─────────────────────────────────────────────────────────────
def search_archive(
    agency_id: str,
    keyword: Optional[str] = None,
    topic: Optional[str] = None,
    source_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_relevance: float = 0.0,
    page: int = 1,
    page_size: int = 50
) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    results = []

    for art in _articles.values():
        if art.agency_id != agency_id:
            continue
        try:
            pub = datetime.fromisoformat(art.published_at.replace("Z", "+00:00"))
            if pub < cutoff:
                continue
            if start_date:
                sd = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
                if pub < sd:
                    continue
            if end_date:
                ed = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
                if pub > ed:
                    continue
        except Exception:
            pass

        if topic and art.topic != topic:
            continue
        if source_type and art.source_type != source_type:
            continue
        if art.relevance_score < min_relevance:
            continue
        if keyword:
            kw = keyword.lower()
            if kw not in art.title.lower() and kw not in art.summary.lower():
                continue
        results.append(art)

    results.sort(key=lambda a: a.published_at, reverse=True)
    total = len(results)
    start = (page - 1) * page_size

    return {
        "agency_id": agency_id,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "articles": [asdict(a) for a in results[start:start + page_size]],
    }


def get_archive_stats(agency_id: str) -> Dict[str, Any]:
    arts = [a for a in _articles.values() if a.agency_id == agency_id]
    by_topic, by_source, by_type, monthly = {}, {}, {}, {}
    for a in arts:
        by_topic[a.topic] = by_topic.get(a.topic, 0) + 1
        by_source[a.source_type] = by_source.get(a.source_type, 0) + 1
        by_type[a.article_type] = by_type.get(a.article_type, 0) + 1
        month = a.published_at[:7]
        monthly[month] = monthly.get(month, 0) + 1
    return {
        "agency_id": agency_id,
        "total_articles": len(arts),
        "archive_months": 12,
        "by_topic": by_topic,
        "by_source_type": by_source,
        "by_article_type": by_type,
        "monthly_volume": dict(sorted(monthly.items())),
    }


async def _ingest_gdelt_doc_articles(agency: AgencyConfig, lookback_hours: int) -> List["Article"]:
    """GDELT DOC 2.0 online news (free, no API key, thousands of outlets) → Articles."""
    try:
        from app.bulletin_intelligence.gdelt_doc_ingest import ingest_gdelt_doc
        raw = await ingest_gdelt_doc(lookback_hours)
        out = [a for a in (_dict_to_article(d, agency.agency_id, "news") for d in raw) if a]
        logger.info(f"GDELT DOC → {len(out)} articles for {agency.agency_id}")
        return out
    except Exception as e:
        logger.warning(f"GDELT DOC ingest failed: {e}")
        return []


async def _ingest_reddit_articles(agency: AgencyConfig) -> List["Article"]:
    """Reddit posts (free; needs REDDIT_CLIENT_ID/SECRET) → Articles. Skips if unset."""
    try:
        from app.bulletin_intelligence.reddit_ingest import ingest_reddit
        raw = await ingest_reddit()
        out = [a for a in (_dict_to_article(d, agency.agency_id, "social") for d in raw) if a]
        if out:
            logger.info(f"Reddit → {len(out)} posts for {agency.agency_id}")
        return out
    except Exception as e:
        logger.warning(f"Reddit ingest failed: {e}")
        return []


# ── Master daily cycle ─────────────────────────────────────────────────────────
async def run_daily_cycle(
    agency_id: str,
    auto_deliver: bool = False,
    lookback_hours: int = 24
) -> Dict[str, Any]:
    agency = get_agency(agency_id)
    if not agency:
        return {"error": f"Agency {agency_id} not registered"}

    # Cost guard: refuse to start if a cycle for this agency is already running
    # (or started within the TTL). Prevents duplicate clicks / overlapping
    # scheduler+manual runs from multiplying Claude API spend.
    now_ts = datetime.now(timezone.utc).timestamp()
    last = _running_cycles.get(agency_id, 0)
    if last and (now_ts - last) < CYCLE_LOCK_TTL:
        logger.info(f"Cycle already in progress for {agency_id}; skipping duplicate")
        return {
            "agency_id": agency_id,
            "status": "already_running",
            "message": "A collection cycle is already in progress for this agency. Please wait for it to finish.",
        }
    _running_cycles[agency_id] = now_ts

    briefing_date = datetime.now().strftime("%B %d, %Y")
    briefing_id = f"{agency_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"Daily cycle starting: {agency.name}")

    # RSS ONLY — Appendix B sources, always FCC-relevant
    tasks = [ingest_rss(agency, lookback_hours)]
    # NewsAPI with FCC domain restrictions
    if NEWSAPI_KEY:
        tasks.append(ingest_newsapi(agency, lookback_hours))
    # Tavily for additional FCC coverage
    if TAVILY_KEY:
        tasks.append(ingest_tavily(agency, lookback_hours))
    # Claude web_search ingest_news disabled — too noisy/expensive
    # tasks.append(ingest_news(agency, lookback_hours))

    # ── Free broad-coverage sources (no Claude cost; gather ignores failures) ──
    # GDELT DOC 2.0 — thousands of online outlets, free, no key
    tasks.append(_ingest_gdelt_doc_articles(agency, lookback_hours))
    # BlueSky public search — free social, no key
    try:
        from app.bulletin_intelligence.bluesky_ingest import ingest_bluesky
        tasks.append(ingest_bluesky(agency, lookback_hours, make_article=Article, hasher=_hash, now_iso=_now))
    except Exception as e:
        logger.warning(f"BlueSky source unavailable: {e}")
    # YouTube — free video/broadcast (auto-skips if YOUTUBE_API_KEY is unset)
    try:
        from app.bulletin_intelligence.youtube_ingest import ingest_youtube
        tasks.append(ingest_youtube(agency, lookback_hours, make_article=Article, hasher=_hash, now_iso=_now))
    except Exception as e:
        logger.warning(f"YouTube source unavailable: {e}")
    # Reddit — free social (auto-skips if REDDIT_CLIENT_ID/SECRET unset)
    try:
        from app.bulletin_intelligence.reddit_ingest import ingest_reddit
        tasks.append(_ingest_reddit_articles(agency))
    except Exception as e:
        logger.warning(f"Reddit source unavailable: {e}")

    if agency.include_broadcast:
        tasks.append(ingest_broadcast(agency, lookback_hours))
    if agency.include_social:
        tasks.append(ingest_social(agency))
    if agency.include_regulatory:
        tasks.append(ingest_regulatory(agency))

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    all_articles = []
    for r in gathered:
        if isinstance(r, list):
            all_articles.extend(r)

    # Process pipeline
    unique = deduplicate(all_articles)
    try:
        classified = await classify_articles(unique, agency)
    except Exception as classify_err:
        logger.error(f"Classification failed: {classify_err}")
        for art in unique:
            art.topic = art.topic or "fcc_news_events"
            art.relevance_score = 0.7
        classified = unique

    # Store in archive
    for art in classified:
        _articles[art.article_id] = art

    # Filter for briefing
    briefing_arts = sorted(
        [a for a in classified if not (a.topic == "other" and a.relevance_score < 0.4)],
        key=lambda a: (a.topic != "other", a.relevance_score), reverse=True
    )[:80]
    logger.info(f"Briefing: {len(briefing_arts)} articles from {len(classified)} classified")

    # Generate briefing
    html = await generate_briefing_html(agency, briefing_arts, briefing_date)

    topic_counts = {}
    for art in briefing_arts:
        topic_counts[art.topic] = topic_counts.get(art.topic, 0) + 1

    # Deliver if auto_deliver
    delivery = {}
    status = "pending_approval"
    if auto_deliver:
        delivery = await deliver_briefing(agency, html, briefing_date)
        status = "delivered" if delivery.get("status") in ("delivered", "dry_run") else "error"

    briefing = Briefing(
        briefing_id=briefing_id,
        agency_id=agency_id,
        briefing_date=briefing_date,
        status=status,
        html_content=html,
        article_count=len(briefing_arts),
        topic_counts=topic_counts,
        generated_at=_now(),
        delivery_recipients=len(agency.distribution_list),
    )
    _briefings[briefing_id] = briefing

    # Persist to durable store (best-effort; never blocks/fails the cycle)
    try:
        from app.bulletin_intelligence import bulletin_store
        await bulletin_store.save_articles([asdict(a) for a in classified])
        await bulletin_store.save_briefing(asdict(briefing))
    except Exception as e:
        logger.warning(f"Persist after daily cycle failed: {e}")

    # Release the cost guard so a fresh cycle can run once this one is done.
    _running_cycles.pop(agency_id, None)

    return {
        "agency_id": agency_id,
        "briefing_id": briefing_id,
        "briefing_date": briefing_date,
        "status": status,
        "ingested": len(all_articles),
        "after_dedup": len(unique),
        "in_briefing": len(briefing_arts),
        "topic_counts": topic_counts,
        "delivery": delivery,
        "message": f"Briefing ready. Approve at POST /api/v1/bulletin/briefings/{briefing_id}/approve"
    }


async def approve_and_deliver(briefing_id: str) -> Dict[str, Any]:
    briefing = _briefings.get(briefing_id)
    if not briefing:
        return {"error": "Briefing not found"}
    agency = get_agency(briefing.agency_id)
    if not agency:
        return {"error": "Agency not found"}

    result = await deliver_briefing(agency, briefing.html_content, briefing.briefing_date)
    briefing.status = "delivered" if result.get("status") in ("delivered", "dry_run") else "error"
    briefing.approved_at = _now()
    briefing.delivered_at = _now()

    # Persist the updated status (best-effort)
    try:
        from app.bulletin_intelligence import bulletin_store
        await bulletin_store.save_briefing(asdict(briefing))
    except Exception as e:
        logger.warning(f"Persist after approve failed: {e}")

    return {"briefing_id": briefing_id, "status": briefing.status, "delivery": result}


def get_editorial_queue(agency_id: str) -> List[Dict]:
    return [asdict(b) for b in _briefings.values() if b.agency_id == agency_id and b.status == "pending_approval"]


def get_briefing(briefing_id: str) -> Optional[Dict]:
    b = _briefings.get(briefing_id)
    return asdict(b) if b else None


def get_briefing_html(briefing_id: str) -> Optional[str]:
    b = _briefings.get(briefing_id)
    return b.html_content if b else None


def get_briefing_history(agency_id: str) -> List[Dict]:
    """All briefings for an agency (any status), newest first, without HTML payload."""
    items = [b for b in _briefings.values() if b.agency_id == agency_id]
    items.sort(key=lambda b: b.generated_at or "", reverse=True)
    history = []
    for b in items:
        d = asdict(b)
        d.pop("html_content", None)
        history.append(d)
    return history


# ── Pre-register FCC ───────────────────────────────────────────────────────────
register_agency(AgencyConfig(
    agency_id="fcc",
    name="Federal Communications Commission",
    short_name="FCC",
    primary_color="#0B3C5D",
    search_queries=[
        "FCC Federal Communications Commission spectrum broadband",
        "net neutrality internet access broadband deployment FCC",
        "media ownership broadcast license radio television FCC",
        "wireless 5G spectrum auction mobile telecommunications",
        "artificial intelligence AI telecom media FCC regulation",
        "public safety E911 emergency communications FCC",
        "robocalls TCPA consumer protection FCC",
        "satellite space communications SpaceX FCC",
    ],
    topics=FCC_TOPICS,
    distribution_email="intelligence@docuaction.io",
    distribution_list=["imran@agtbi.com"],
    delivery_time_et="07:30",
    archive_months=12,
))
