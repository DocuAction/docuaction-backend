"""
DocuAction Bulletin Intelligence — Editorial Rules (deterministic pre-pass)
Enforces FCC gold-standard rules in CODE, not just LLM prompt instructions:

  1. Subscription source flagging — known paywalled outlets get is_paywalled=True
     so the briefing shows "[SUBSCRIPTION REQUIRED]" and NEVER an AI summary.
  2. FCC.gov limitation — max 3 fcc.gov, max 2 FCC blog, max 1 fact sheet in the
     daily briefing; surplus FCC.gov content is dropped from the briefing
     (still archived upstream) unless a section would otherwise be empty.
  3. 24-hour freshness — drop anything older than the lookback window from the
     final briefing set, regardless of source.

Pure stdlib. Operates on Article-like objects (attrs: outlet, url, title,
published_at, is_paywalled, summary, source_type, topic).
"""

import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Any, Tuple

# Outlets that are subscription-only — show headline + [SUBSCRIPTION REQUIRED].
# Substring-matched against the outlet name (case-insensitive), so "The New York
# Times" matches "new york times". Keep these lowercase.
SUBSCRIPTION_SOURCES = {
    "communications daily",
    "law360",
    "inside cybersecurity",
    "inside radio",
    "inside towers",
    "politico pro",
    "the information",
    "telecompetitor pro",
    "wall street journal",   # WSJ
    "wsj",
    "bloomberg",
    "new york times",        # NYT
    "nytimes",
    "washington post",       # WaPo
    "financial times",
    "the economist",
    "light reading",         # registration-walled
    "telecom paper",
}

# FCC.gov caps for the daily briefing
MAX_FCCGOV = 1            # max 1 FCC.gov item in the entire briefing; client
                           # gets FCC.gov directly — this service's value is
                           # EXTERNAL media coverage (Reuters, AP, Axios, etc.)
MAX_FCC_BLOG = 0           # no FCC blog posts — external media only
MAX_FCC_FACTSHEET = 0      # no FCC fact sheets


def _outlet_lc(a: Any) -> str:
    return (getattr(a, "outlet", "") or "").strip().lower()


def _url_lc(a: Any) -> str:
    return (getattr(a, "url", "") or "").lower()


def flag_subscriptions(articles: List[Any]) -> int:
    """Set is_paywalled=True on known subscription outlets. Returns count flagged."""
    n = 0
    for a in articles:
        outlet = _outlet_lc(a)
        if any(src in outlet for src in SUBSCRIPTION_SOURCES):
            if not getattr(a, "is_paywalled", False):
                try:
                    a.is_paywalled = True
                    n += 1
                except Exception:
                    pass
    return n


def _is_fccgov(a: Any) -> bool:
    return "fcc.gov" in _url_lc(a) or _outlet_lc(a) == "fcc"


def _is_fcc_blog(a: Any) -> bool:
    return "fcc.gov/news-events/blog" in _url_lc(a) or "blog" in (getattr(a, "title", "") or "").lower() and _is_fccgov(a)


def _is_fcc_factsheet(a: Any) -> bool:
    t = (getattr(a, "title", "") or "").lower()
    return _is_fccgov(a) and ("fact sheet" in t or "factsheet" in t)


def enforce_fccgov_cap(articles: List[Any]) -> List[Any]:
    """
    Drop FCC.gov content beyond the caps from the briefing. Keeps highest-ranked
    FCC.gov items first (input should be pre-sorted by FinalScore). Non-FCC.gov
    articles pass through untouched.
    """
    kept, fccgov_count, blog_count, fs_count = [], 0, 0, 0
    for a in articles:
        if not _is_fccgov(a):
            kept.append(a)
            continue
        if _is_fcc_factsheet(a):
            if fs_count < MAX_FCC_FACTSHEET and fccgov_count < MAX_FCCGOV:
                fs_count += 1; fccgov_count += 1; kept.append(a)
        elif _is_fcc_blog(a):
            if blog_count < MAX_FCC_BLOG and fccgov_count < MAX_FCCGOV:
                blog_count += 1; fccgov_count += 1; kept.append(a)
        else:
            if fccgov_count < MAX_FCCGOV:
                fccgov_count += 1; kept.append(a)
        # else: surplus FCC.gov dropped from briefing (archived upstream)
    return kept


def enforce_freshness(articles: List[Any], lookback_hours: int = 24,
                      now: datetime = None) -> List[Any]:
    """Drop articles older than the lookback window. Undated items are kept
    (they came from live ingestion this cycle)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    kept = []
    for a in articles:
        s = (getattr(a, "published_at", "") or "").strip()
        if not s:
            kept.append(a)  # no date → assume current-cycle
            continue
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                kept.append(a)
        except Exception:
            kept.append(a)  # unparseable date → don't silently drop
    return kept


# ── Corporate-announcement filter (UAT false-positive fix) ────────────────────
# UAT example: "T-Mobile executive appointment" must NOT appear. A corporate
# announcement (personnel move, earnings, product/plan launch, generic marketing
# partnership) is NOT FCC news UNLESS it is directly tied to an FCC nexus below.
# Reversible via BULLETIN_EDITORIAL_STRICT=false (default on — this is a UAT fix).
EDITORIAL_STRICT = os.getenv("BULLETIN_EDITORIAL_STRICT", "true").strip().lower() == "true"

# Markers that a story is a corporate/business announcement rather than policy news.
_CORP_ANNOUNCE_MARKERS = (
    # personnel / leadership moves
    "appoint", "appoints", "appointed", "names ", " named ", "hires", "hired",
    "promotes", "promoted", "joins as", "to lead", "steps down", "resigns",
    "retires", "new ceo", "new cfo", "new cto", "new coo", "chief executive",
    "chief financial", "chief technology", "chief operating", "board of directors",
    "appointment", "executive appointment", "elevates", "taps ",
    # Added 2026-07-08 (validation-run finding): exec-move phrasings that slipped
    # through, e.g. "T-Mobile Bringing on Former AT&T Exec Chris Sambar".
    "bringing on", "brings on", "onboards", "poaches", "recruits", " joins ",
    "exec ", " exec", "chief revenue", "chief marketing", "chief commercial",
    "general manager", "managing director", "svp", "evp", " vp ", "vp for",
    "rises to", "regional vp", "welcomes", "rejoins",
    # earnings / financial results
    "quarterly earnings", "quarterly results", "q1 results", "q2 results",
    "q3 results", "q4 results", "earnings report", "reports revenue",
    "beats estimates", "misses estimates", "dividend", "stock buyback",
    "share repurchase", "guidance", "posts profit", "posts loss",
    # product / marketing announcements
    "unveils", "launches new", "new plan", "new pricing", "rolls out",
    "introduces", "now available", "expands service", "customer growth",
    "loyalty program", "rebrand", "sponsorship",
)

# FCC-nexus signals that KEEP an otherwise-corporate story (the client's allowed
# hooks: FCC, spectrum, licensing, rulemaking, enforcement, mergers requiring FCC
# approval, broadcast ownership, telecommunications regulation).
_FCC_NEXUS_SIGNALS = (
    "fcc", "federal communications commission", "f.c.c.",
    "spectrum", "auction", "license", "licensing", "licensed",
    "rulemaking", "nprm", "notice of proposed", "docket", "part 15", "part 25",
    "rule", "regulation", "regulatory", "regulator",
    "enforcement", "forfeiture", "consent decree", "notice of apparent liability",
    "merger", "acquisition review", "deal review", "transfer of control",
    "broadcast ownership", "ownership cap", "retransmission", "must-carry",
    "net neutrality", "title ii", "universal service", "usf", "e-rate",
    "robocall", "tcpa", "stir-shaken", "earth station", "ngso",
    "commissioner", "brendan carr", "anna gomez", "olivia trusty",
    "wireless bureau", "wireline", "media bureau", "space bureau",
)


def _blob(a: Any) -> str:
    return " ".join([
        (getattr(a, "title", "") or ""),
        (getattr(a, "summary", "") or ""),
        (getattr(a, "full_text", "") or ""),
    ]).lower()


def is_corporate_noise(a: Any) -> bool:
    """True if the article is a corporate announcement with NO FCC nexus.

    Conservative by design: it only rejects when a corporate marker is present
    AND no FCC-nexus signal appears anywhere in the text — so a genuine FCC story
    that happens to mention a company (e.g. 'FCC approves T-Mobile spectrum
    transfer') is always kept."""
    blob = _blob(a)
    if not blob.strip():
        return False
    has_corp = any(m in blob for m in _CORP_ANNOUNCE_MARKERS)
    if not has_corp:
        return False
    has_nexus = any(s in blob for s in _FCC_NEXUS_SIGNALS)
    return not has_nexus


def filter_corporate_noise(articles: List[Any]) -> Tuple[List[Any], List[Any]]:
    """Split into (kept, rejected_corporate). No-op passthrough when the flag is
    off. Never raises."""
    if not EDITORIAL_STRICT:
        return list(articles), []
    kept, rejected = [], []
    for a in articles:
        try:
            if is_corporate_noise(a):
                rejected.append(a)
            else:
                kept.append(a)
        except Exception:
            kept.append(a)
    return kept, rejected


def apply_editorial_rules(articles: List[Any], lookback_hours: int = 24,
                          now: datetime = None) -> List[Any]:
    """Run all deterministic editorial rules in order. Returns filtered list."""
    flag_subscriptions(articles)
    fresh = enforce_freshness(articles, lookback_hours, now)
    capped = enforce_fccgov_cap(fresh)
    kept, _rejected = filter_corporate_noise(capped)
    return kept
