"""
DocuAction Bulletin Intelligence — Scoring Service
Problem #2: Source Authority Scoring + composite FinalScore ranking.

FinalScore = RelevanceScore (0-100) + AuthorityWeight (0-100) + RecencyWeight (0-100)
All three normalized to 0-100, then summed (max 300) for ranking only.

Does NOT touch Boolean section assignment — purely a ranking layer.
"""

from datetime import datetime, timezone
from typing import Optional

# ── Source authority table (Problem #2) ───────────────────────────────────────
SOURCE_AUTHORITY_SCORE = {
    "reuters": 100,
    "associated press": 95,
    "ap": 95,
    "bloomberg": 95,
    "wall street journal": 95,
    "wsj": 95,
    "politico": 90,
    "law360": 90,
    "federal register": 90,
    "fcc": 100,
    "fcc.gov": 100,
    "broadband breakfast": 85,
    "fiercewireless": 85,
    "fierce wireless": 85,
    "rcr wireless": 85,
    "tv news check": 80,
    "tvnewscheck": 80,
    "radio world": 80,
    "rbr": 80,
    "the hill": 80,
    "fedscoop": 80,
    # Reasonable defaults for other recognized outlets
    "usa today": 88,
    "washington post": 92,
    "new york times": 92,
    "spacenews": 82,
    "cisa": 90,
    "communications daily": 85,
    "telecompetitor": 78,
    "telegeography": 80,
    "congress.gov": 90,
    "pc mag": 70,
    "inside radio": 78,
}

DEFAULT_AUTHORITY = 60  # unknown outlets


def authority_weight(outlet: str) -> int:
    """Return 0-100 authority for an outlet (case-insensitive, substring-aware)."""
    if not outlet:
        return DEFAULT_AUTHORITY
    key = outlet.strip().lower()
    if key in SOURCE_AUTHORITY_SCORE:
        return SOURCE_AUTHORITY_SCORE[key]
    # substring match (e.g. "Federal Register — FCC" → federal register)
    for name, score in SOURCE_AUTHORITY_SCORE.items():
        if name in key:
            return score
    return DEFAULT_AUTHORITY


def recency_weight(published_at: str, now: Optional[datetime] = None) -> int:
    """
    0-100 recency score. <6h = 100, decays to ~40 at 24h, ~10 by 72h.
    Robust to bad/empty dates (returns mid value 50).
    """
    now = now or datetime.now(timezone.utc)
    try:
        # Accept ISO 8601 with or without tz
        s = (published_at or "").strip()
        if not s:
            return 50
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = max(0.0, (now - dt).total_seconds() / 3600.0)
    except Exception:
        return 50
    if hours <= 6:
        return 100
    if hours <= 12:
        return 85
    if hours <= 24:
        return 70
    if hours <= 48:
        return 45
    if hours <= 72:
        return 25
    return 10


def final_score(relevance_score: float, outlet: str, published_at: str,
                now: Optional[datetime] = None) -> float:
    """
    Composite ranking score. relevance_score is the engine's 0.0-1.0 value,
    scaled to 0-100 here. Returns 0-300.
    """
    rel = max(0.0, min(1.0, float(relevance_score or 0.0))) * 100.0
    auth = authority_weight(outlet)
    rec = recency_weight(published_at, now)
    return rel + auth + rec
