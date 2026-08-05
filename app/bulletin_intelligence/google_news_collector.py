"""Google News RSS collector and bulletin QA layer.

Why this exists: the FCC checks our bulletin against Google News. Any story they
find there that we missed is a visible gap, so Google News is treated as a QA
verification source — we compare against it and fold in whatever we missed rather
than discovering the omission after delivery.

Free: RSS only, no API key, no quota, no cost. That also makes it a safe primary
source in a way Perigon (150 requests/day) is not.

Design notes:
  * XML is parsed with defusedxml, matching engine.py's handling of remote XML.
    Google News is a remote, untrusted document; stdlib ElementTree is only the
    fallback when defusedxml is unavailable.
  * One feed failing must never block the rest, and the collector as a whole must
    never raise into the bulletin cycle — same failure contract as every other
    optional collector.
  * Jaro-Winkler is implemented here rather than pulled from `jellyfish` so a P0
    fix adds no dependency and the matching stays deterministic under test.
"""

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("docuaction.bulletin.google_news")

TIMEOUT = float(10)
# Above this Jaro-Winkler score two headlines are considered the same story.
MATCH_THRESHOLD = 0.7
# A QA run passes when fewer than this many Google News stories are missing.
QA_MAX_MISSING = 5

FEEDS: List[Dict[str, str]] = [
    {"name": "FCC general",
     "url": "https://news.google.com/rss/search?q=FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Federal Communications Commission",
     "url": "https://news.google.com/rss/search?q=%22Federal+Communications+Commission%22+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Brendan Carr",
     "url": "https://news.google.com/rss/search?q=%22Brendan+Carr%22+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Anna Gomez",
     "url": "https://news.google.com/rss/search?q=%22Anna+Gomez%22+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Geoffrey Starks",
     "url": "https://news.google.com/rss/search?q=%22Geoffrey+Starks%22+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Nathan Simington",
     "url": "https://news.google.com/rss/search?q=%22Nathan+Simington%22+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Olivia Trusty",
     "url": "https://news.google.com/rss/search?q=%22Olivia+Trusty%22+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Spectrum auction",
     "url": "https://news.google.com/rss/search?q=spectrum+auction+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Broadband FCC",
     "url": "https://news.google.com/rss/search?q=broadband+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Robocall",
     "url": "https://news.google.com/rss/search?q=robocall+FCC+when:1d&hl=en-US&gl=US&ceid=US:en"},
]


# ── Fuzzy matching ───────────────────────────────────────────────────────────

def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    window = max(len1, len2) // 2 - 1
    if window < 0:
        window = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    for i in range(len1):
        start, end = max(0, i - window), min(i + window + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0

    transpositions = 0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    return (matches / len1 + matches / len2
            + (matches - transpositions) / matches) / 3.0


def jaro_winkler(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler similarity in [0, 1]. Local implementation — see module docstring."""
    j = _jaro(s1, s2)
    if j <= 0.7:  # standard Winkler guard: only boost already-similar strings
        return j
    prefix = 0
    for a, b in zip(s1[:4], s2[:4]):
        if a != b:
            break
        prefix += 1
    return j + prefix * prefix_weight * (1 - j)


_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, strip the Google News ' - Outlet' suffix, drop punctuation.

    Google News appends the outlet to every headline ('Headline - Reuters'), which
    would otherwise depress similarity against the same story from our own feeds.
    """
    t = (title or "").strip()
    if " - " in t:
        head, _, tail = t.rpartition(" - ")
        # Only strip when the tail looks like an outlet name, not part of the
        # headline (outlets are short and have no sentence punctuation).
        if head and len(tail) <= 40 and not tail.endswith((".", "?", "!")):
            t = head
    t = _PUNCT.sub(" ", t.lower())
    return _WS.sub(" ", t).strip()


def titles_match(a: str, b: str, threshold: float = MATCH_THRESHOLD) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    return jaro_winkler(na, nb) > threshold


# ── RSS parsing ──────────────────────────────────────────────────────────────

def _ET():
    # defusedxml, not stdlib: this parses XML fetched from a remote host.
    try:
        from defusedxml import ElementTree as ET
        return ET
    except Exception:  # pragma: no cover — only when defusedxml is absent
        import xml.etree.ElementTree as ET
        return ET


def unwrap_google_news_url(link: str, source_url: str = "") -> str:
    """Return the publisher's URL, unwrapping a Google News redirect when possible.

    Google News RSS <link> values are redirect wrappers
    (news.google.com/rss/articles/CBMi...). Storing those is bad in three ways:
    they defeat URL-based dedup (every wrapper is unique), they are opaque in the
    briefing, and they rot when Google rotates them.

    Two extraction paths, no network call:
      1. The <source url="..."> attribute, when the feed provides it. Note this is
         the publisher's HOME page, not the article — useful for the domain, not
         as the article link, so it is only used when nothing better exists.
      2. A base64url payload in the path. Older wrappers embed the target URL
         there; newer ones embed an opaque internal id instead, so this decodes
         only when a plausible http(s) URL actually falls out.

    Following the redirect over HTTP would resolve every case, but costs one
    request per article and Google rate-limits it — not worth it inside a
    collection cycle. Unresolvable wrappers are returned unchanged rather than
    dropped: a wrapped link still works for a human clicking it.
    """
    link = (link or "").strip()
    if not link or "news.google.com" not in link:
        return link

    m = re.search(r"/(?:rss/)?articles/([A-Za-z0-9_\-]+)", link)
    if m:
        token = m.group(1)
        for candidate in (token, token + "=" * (-len(token) % 4)):
            try:
                raw = base64.urlsafe_b64decode(candidate).decode("utf-8", "replace")
            except Exception:
                continue
            found = re.search(r"https?://[^\s\x00-\x1f\"'<>]{6,}", raw)
            if found:
                url = found.group(0).rstrip("\\").strip()
                # Guard against decoding into another Google wrapper.
                if "news.google.com" not in url:
                    return url
            break

    return link


def parse_rss(xml_text: str) -> List[Dict[str, str]]:
    """Parse a Google News RSS document into plain dicts.

    Returns [] on malformed XML rather than raising: one bad feed response must
    not take down a collection cycle.
    """
    if not (xml_text or "").strip():
        return []
    try:
        root = _ET().fromstring(xml_text)
    except Exception as e:
        logger.warning("Google News: XML parse failed: %s", e)
        return []

    out: List[Dict[str, str]] = []
    for item in root.iter("item"):
        def _text(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        link = _text("link")
        title = _text("title")
        if not link or not title:
            continue
        source_el = item.find("source")
        source = ""
        source_url = ""
        if source_el is not None:
            if source_el.text:
                source = source_el.text.strip()
            source_url = (source_el.get("url") or "").strip()
        real = unwrap_google_news_url(link, source_url)
        out.append({
            "title": title,
            "url": real,
            "google_url": link if real != link else "",
            "source": source,
            "published": _text("pubDate"),
            "summary": _text("description"),
        })
    return out


def _parse_pubdate(value: str) -> Optional[datetime]:
    """Parse an RSS pubDate to an aware UTC datetime, or None."""
    if not (value or "").strip():
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value.strip())
    except Exception:
        return None
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_from_today(published: str, *, now: Optional[datetime] = None) -> bool:
    """True when the item was published on today's UTC date.

    `when:1d` in the feed query is a relative 24h window, not a calendar day, so
    it still returns yesterday-evening stories. This is the calendar-day gate.

    An UNPARSEABLE or missing date returns True. Google News omits pubDate on some
    items, and treating "no date" as "not today" would silently discard real
    stories — the failure this whole QA layer exists to prevent. Better a stale
    item a reviewer can see than a missing one they cannot.
    """
    dt = _parse_pubdate(published)
    if dt is None:
        return True
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    return dt.date() == today


def filter_to_today(items: List[Dict[str, str]], *,
                    now: Optional[datetime] = None) -> List[Dict[str, str]]:
    kept = [i for i in items if is_from_today(i.get("published", ""), now=now)]
    dropped = len(items) - len(kept)
    if dropped:
        logger.info("Google News: dropped %d item(s) not from today's UTC date", dropped)
    return kept


def deduplicate_by_url(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """First occurrence wins. Also collapses same-headline duplicates, which the
    per-commissioner feeds produce heavily (one story matches several queries)."""
    seen_urls, seen_titles = set(), set()
    out = []
    for it in items:
        url = (it.get("url") or "").strip()
        key_title = normalize_title(it.get("title", ""))
        if not url or url in seen_urls or (key_title and key_title in seen_titles):
            continue
        seen_urls.add(url)
        if key_title:
            seen_titles.add(key_title)
        out.append(it)
    return out


# ── QA result ────────────────────────────────────────────────────────────────

# ── Latest-QA store ──────────────────────────────────────────────────────────
# The engine computes the QA comparison during a cycle; the endpoint reads it
# afterwards. Kept in-process deliberately: it is disposable observability, not
# durable state, and must never add a DB write to the delivery path.
_latest_qa: Dict[str, Dict[str, Any]] = {}


def store_qa_report(agency_id: str, report: Dict[str, Any]) -> None:
    _latest_qa[agency_id or "fcc"] = report


def get_qa_report(agency_id: str = "fcc") -> Optional[Dict[str, Any]]:
    return _latest_qa.get(agency_id or "fcc")


def summarize_legacy_qa_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt fcc_qa_verification's report to the QA response shape.

    That module is the live QA path — it runs after classification and behind the
    FCC_KEYWORDS relevance gate. This maps its counters onto the fields the
    endpoint serves, so the endpoint reports the same numbers the pipeline acted
    on rather than a second, ungated comparison.
    """
    report = report or {}
    found = int(report.get("google_news_found", 0) or 0)
    added = int(report.get("added_from_qa", 0) or 0)
    already = int(report.get("already_in_bulletin", 0) or 0)
    # "Missing" = stories QA had to add because no other source found them.
    coverage = (already / found) if found else 1.0
    return {
        "google_news_count": found,
        "bulletin_count": already + added,
        "matched": already,
        "missing_from_bulletin": added,
        "coverage_rate": round(coverage, 4),
        "qa_passed": added < QA_MAX_MISSING,
        "skipped_not_relevant": int(report.get("skipped_not_relevant", 0) or 0),
        "skipped_duplicate": int(report.get("skipped_duplicate", 0) or 0),
        "talkwalker_found": int(report.get("talkwalker_found", 0) or 0),
        "qa_sources_checked": int(report.get("qa_sources_checked", 0) or 0),
        "errors": report.get("errors", []),
        # Per-row matching data for the QA spreadsheet; excluded from the endpoint
        # payload by the route, which serves counts.
        "google_titles": list(report.get("google_titles") or []),
        "source": "fcc_qa_verification",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@dataclass
class QAResult:
    matched: List[Dict[str, Any]] = field(default_factory=list)
    missing_from_bulletin: List[Dict[str, Any]] = field(default_factory=list)
    bulletin_only: List[Any] = field(default_factory=list)
    google_news_count: int = 0
    bulletin_count: int = 0


class GoogleNewsCollector:
    """Collects FCC news from Google News RSS feeds.

    Free, no API key, no cost. Used both as a collection source and as the QA
    verification layer against the assembled bulletin.
    """

    def __init__(self, feeds: Optional[List[Dict[str, str]]] = None,
                 timeout: float = TIMEOUT):
        self.feeds = feeds if feeds is not None else FEEDS
        self.timeout = timeout

    async def _fetch_feed(self, client: httpx.AsyncClient,
                          feed: Dict[str, str]) -> List[Dict[str, str]]:
        resp = await client.get(feed["url"])
        if resp.status_code != 200:
            logger.warning("Google News feed %s: HTTP %s", feed["name"], resp.status_code)
            return []
        items = parse_rss(resp.text)
        for it in items:
            it["feed"] = feed["name"]
        return items

    async def collect_raw(self, *, now: Optional[datetime] = None) -> List[Dict[str, str]]:
        """Fetch every feed concurrently; return deduplicated raw dicts.

        `now` overrides the clock used by the today-only filter. Production leaves
        it None; tests pass a fixed instant so a dated fixture does not silently
        start failing the day after it was written.
        """
        results: List[Dict[str, str]] = []
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DocuActionBulletin/1.0)"},
                follow_redirects=True,
            ) as client:
                gathered = await asyncio.gather(
                    *[self._fetch_feed(client, f) for f in self.feeds],
                    return_exceptions=True,
                )
        except Exception as e:
            logger.warning("Google News: collection failed entirely: %s", e)
            return []

        for feed, r in zip(self.feeds, gathered):
            if isinstance(r, list):
                results.extend(r)
            else:
                # Never block on one feed failure.
                logger.warning("Google News feed failed: %s: %s", feed["name"], r)
        results = filter_to_today(results, now=now)
        deduped = deduplicate_by_url(results)
        logger.info("Google News: %d articles from %d feeds (%d before dedup)",
                    len(deduped), len(self.feeds), len(results))
        return deduped

    async def collect(self, agency=None, *, make_article=None, hasher=None,
                      now_iso=None) -> List[Any]:
        """Collect and map onto the engine's Article schema.

        Returns [] rather than raising on any failure, matching the contract of
        every other optional collector.
        """
        raw = await self.collect_raw()
        if not raw:
            return []
        if make_article is None or hasher is None or now_iso is None:
            try:
                from app.bulletin_intelligence.engine import (
                    Article as _A, _hash as _h, _now as _n)
                make_article = make_article or _A
                hasher = hasher or _h
                now_iso = now_iso or _n
            except Exception as e:
                logger.warning("Google News: engine helpers unavailable (%s)", e)
                return []

        agency_id = getattr(agency, "agency_id", "fcc")
        collected_at = now_iso()
        out = []
        for it in raw:
            try:
                out.append(make_article(
                    article_id=f"{agency_id}_gnews_{hasher(it['url'], it['title'])}",
                    agency_id=agency_id,
                    source="google_news",
                    source_type="news",
                    title=it["title"],
                    url=it["url"],
                    published_at=it.get("published") or collected_at,
                    summary=it.get("summary", ""),
                    full_text="",
                    author="",
                    outlet=it.get("source", ""),
                    dedup_hash=hasher(it["url"], it["title"]),
                    ingested_at=collected_at,
                    provider="Google News",
                    provider_url="https://news.google.com/rss",
                    source_name=it.get("source", ""),
                    collection_method="rss",
                    collection_time=collected_at,
                ))
            except Exception as e:  # pragma: no cover — schema drift guard
                logger.warning("Google News: could not map article (%s)", e)
        return out

    async def compare_with_bulletin(self, google_articles: List[Dict[str, Any]],
                                    bulletin_articles: List[Any]) -> QAResult:
        """Compare Google News stories against the assembled bulletin.

        Matching is fuzzy (Jaro-Winkler > 0.7 on normalized headlines) because the
        same story carries different headlines across outlets and Google News
        appends the outlet name to every title.
        """
        def _title_of(a) -> str:
            if isinstance(a, dict):
                return a.get("title", "")
            return getattr(a, "title", "") or ""

        bulletin_titles = [_title_of(a) for a in bulletin_articles]
        matched, missing = [], []
        matched_bulletin_idx = set()

        for g in google_articles:
            g_title = _title_of(g)
            hit_idx = None
            for i, b_title in enumerate(bulletin_titles):
                if titles_match(g_title, b_title):
                    hit_idx = i
                    break
            if hit_idx is None:
                missing.append(g)
            else:
                matched.append(g)
                matched_bulletin_idx.add(hit_idx)

        bulletin_only = [a for i, a in enumerate(bulletin_articles)
                         if i not in matched_bulletin_idx]

        return QAResult(
            matched=matched,
            missing_from_bulletin=missing,
            bulletin_only=bulletin_only,
            google_news_count=len(google_articles),
            bulletin_count=len(bulletin_articles),
        )

    async def generate_qa_report(self, qa_result: QAResult) -> Dict[str, Any]:
        """Structured QA comparison for the endpoint and the Excel QA sheet."""
        def _field(a, name, default=""):
            if isinstance(a, dict):
                return a.get(name, default)
            return getattr(a, name, default) or default

        total = qa_result.google_news_count
        matched_n = len(qa_result.matched)
        missing_n = len(qa_result.missing_from_bulletin)
        # Coverage is "of what Google News saw, how much did we already have".
        coverage = (matched_n / total) if total else 1.0
        return {
            "google_news_count": total,
            "bulletin_count": qa_result.bulletin_count,
            "matched": matched_n,
            "missing_from_bulletin": missing_n,
            "missing_articles": [
                {
                    "title": _field(a, "title"),
                    "source": _field(a, "source") or _field(a, "outlet"),
                    "url": _field(a, "url"),
                    "date": _field(a, "published") or _field(a, "published_at"),
                }
                for a in qa_result.missing_from_bulletin
            ],
            "coverage_rate": round(coverage, 4),
            "qa_passed": missing_n < QA_MAX_MISSING,
            "match_threshold": MATCH_THRESHOLD,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
