"""
DocuAction Bulletin Intelligence — Claude-Powered Engine
Uses ONLY Anthropic Claude API for all functions:
  - News ingestion:      Claude web_search tool (replaces Perigon $449/mo)
  - Broadcast:          Claude web_search for TV/radio coverage (replaces TVEyes $600/mo)
  - Social monitoring:  Claude web_search for social mentions (replaces Twitter API $100/mo)
  - Regulatory:         Federal Register + Congress.gov APIs (FREE — no key needed)
  - Classification:     Claude Haiku
  - Briefing:           Claude Sonnet
  - LLM Visibility:     Claude Haiku

SWAP IN PAID APIs LATER by changing only the ingest_* functions below.
Everything else stays identical.

Required env vars (already set in Railway):
  ANTHROPIC_API_KEY
  SENDGRID_API_KEY
  CONGRESS_API_KEY (free — register at api.congress.gov)
"""

import os, json, logging, asyncio, hashlib, re
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict, field

import httpx
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SENDGRID_KEY  = os.getenv("SENDGRID_API_KEY", "")
CONGRESS_KEY  = os.getenv("CONGRESS_API_KEY", "")

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


def _hash(url: str, title: str) -> str:
    return hashlib.md5(f"{url}{title}".encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    queries_to_run = agency.search_queries[:4]  # limit to 4 queries per cycle

    for query in queries_to_run:
        try:
            # Step 1: Claude searches the web
            search_response = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": f"Search for news articles published in the last {lookback_hours} hours about: {query}\n\nFind recent, relevant news articles."
                }]
            )

            # Step 2: Extract structured article data
            extract_response = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2000,
                messages=[
                    {"role": "user", "content": f"Search for news about: {query}"},
                    {"role": "assistant", "content": search_response.content},
                    {"role": "user", "content": """From those search results, extract up to 6 news articles as a JSON array.
Each object must have: title, url, outlet, author, published_at (ISO date or today), summary (2 sentences max), is_paywalled (bool).
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

        prompt = f"""Classify each article. Return a JSON array, one object per article:
  id, topic (from list), article_type (news/opinion/analysis/editorial/press_release/regulatory), sentiment (positive/negative/neutral), relevance_score (0.0-1.0)

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

    prompt = f"""Generate a complete HTML executive briefing email for {agency.name} ({agency.short_name}).
Date: {briefing_date}
Primary color: {agency.primary_color}

Requirements:
1. Branded header: "{agency.short_name} Morning Intelligence Briefing — {briefing_date}"
2. Table of contents with anchor links to each topic section
3. One section per topic (only include topics that have articles)
4. Each article: linked title, outlet/author, 2-sentence synopsis
5. Article type badges: news=blue, opinion=orange, regulatory=green
6. Sentiment indicator: positive=green dot, negative=red dot, neutral=gray dot
7. Broadcast items: show 📺 icon and link the clip URL
8. Paywalled items: show 🔒 badge
9. Regulatory items: show ⚖️ badge with source (Federal Register / Congress.gov)
10. Footer: "Generated by DocuAction AI — Alliance Global Tech, Inc. | imran@agtbi.com"
11. Section 508 compliant: semantic HTML, proper headings (h1/h2/h3), sufficient contrast
12. Fully inline CSS — works in all email clients including Outlook

Keep article synopses factual and neutral. Do not add editorial opinion.

Articles:
{''.join(context)}

Write the complete HTML document."""

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


# ── LLM Visibility Tracker ─────────────────────────────────────────────────────
async def run_llm_visibility_check(agency: AgencyConfig) -> Dict[str, Any]:
    if not ANTHROPIC_KEY:
        return {"status": "skipped"}

    client = _get_client()
    queries = [
        f"What is the {agency.name} currently working on?",
        f"What are the most important recent decisions from the {agency.name}?",
        f"What controversies or issues is the {agency.name} facing?",
    ]
    results = {}
    for q in queries:
        try:
            resp = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=400,
                messages=[{"role": "user", "content": q}]
            )
            results[q] = _extract_text(resp.content)
        except Exception as e:
            results[q] = f"Error: {e}"

    return {
        "agency_id": agency.agency_id,
        "checked_at": _now(),
        "llm_source": "Claude Haiku",
        "results": results,
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


# ── Master daily cycle ─────────────────────────────────────────────────────────
async def run_daily_cycle(
    agency_id: str,
    auto_deliver: bool = False,
    lookback_hours: int = 24
) -> Dict[str, Any]:
    agency = get_agency(agency_id)
    if not agency:
        return {"error": f"Agency {agency_id} not registered"}

    briefing_date = datetime.now().strftime("%B %d, %Y")
    briefing_id = f"{agency_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info(f"Daily cycle starting: {agency.name}")

    # Ingest all sources concurrently
    tasks = [ingest_news(agency, lookback_hours)]
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
    classified = await classify_articles(unique, agency)

    # Store in archive
    for art in classified:
        _articles[art.article_id] = art

    # Filter for briefing
    briefing_arts = sorted(
        [a for a in classified if a.relevance_score >= 0.4],
        key=lambda a: a.relevance_score, reverse=True
    )[:60]

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
    return {"briefing_id": briefing_id, "status": briefing.status, "delivery": result}


def get_editorial_queue(agency_id: str) -> List[Dict]:
    return [asdict(b) for b in _briefings.values() if b.agency_id == agency_id and b.status == "pending_approval"]


def get_briefing(briefing_id: str) -> Optional[Dict]:
    b = _briefings.get(briefing_id)
    return asdict(b) if b else None


def get_briefing_html(briefing_id: str) -> Optional[str]:
    b = _briefings.get(briefing_id)
    return b.html_content if b else None


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
FCC_TOPIC_LABELS = TOPIC_LABELS