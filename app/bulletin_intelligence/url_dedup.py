"""URL normalization and duplicate detection for the bulletin.

The problem: the same story reaches us several times wearing different URLs —
an AMP variant, a tracking-parameter variant, a Google News redirect wrapper, and
syndicated copies at other outlets. The existing dedup compares dedup_hash and a
60-char title prefix, which catches exact repeats and misses all four of those.

Four independent signals, cheapest first:
    1. Same normalized URL      — AMP, tracking params, trailing slash
    2. Same publisher article ID — law360.com/articles/2509705
    3. Same headline, same source (>0.85)
    4. Very similar headline, any source (>0.92) — AP/Reuters syndication

Jaro-Winkler is implemented locally rather than imported from `jellyfish`, which
is not installed and would be a new dependency. Same reasoning as elsewhere in
this codebase: DEPLOYMENT_GUIDE.md:143-149 records one install moving 11 pinned
packages including fastapi.

NOTHING IS DELETED HERE. `find_duplicates` marks and reports; the caller decides.
A dedup bug that silently drops a real story is far more costly than one that
leaves a duplicate in — the duplicate is visible in review, the omission is not.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger("docuaction.bulletin.dedup")

SAME_SOURCE_THRESHOLD = 0.85
CROSS_SOURCE_THRESHOLD = 0.92

# Parameters that never change which article you land on.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {
    "fbclid", "gclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ref", "source", "src", "cmpid", "campaign_id", "spm", "at_medium",
    "at_campaign", "smid", "partner", "sh",
}


def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    window = max(max(len1, len2) // 2 - 1, 0)
    m1, m2 = [False] * len1, [False] * len2
    matches = 0
    for i in range(len1):
        for j in range(max(0, i - window), min(i + window + 1, len2)):
            if m2[j] or s1[i] != s2[j]:
                continue
            m1[i] = m2[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    transpositions, k = 0, 0
    for i in range(len1):
        if not m1[i]:
            continue
        while not m2[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    return (matches / len1 + matches / len2
            + (matches - transpositions) / matches) / 3.0


def jaro_winkler_similarity(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler similarity in [0, 1]. Local — no `jellyfish` dependency."""
    j = _jaro(s1 or "", s2 or "")
    if j <= 0.7:
        return j
    prefix = 0
    for a, b in zip((s1 or "")[:4], (s2 or "")[:4]):
        if a != b:
            break
        prefix += 1
    return j + prefix * prefix_weight * (1 - j)


def is_amp_url(url: str) -> bool:
    """True when the URL looks like an AMP variant of a canonical article."""
    u = (url or "").lower()
    if not u:
        return False
    parsed = urlparse(u if "//" in u else "//" + u)
    host, path = parsed.netloc, parsed.path
    return (
        host.startswith("amp.")
        or host.endswith(".ampproject.org")
        or "/amp/" in path
        or path.endswith("/amp")
        or path.endswith(".amp")
        or parse_qs(parsed.query).get("amp") is not None
        or "outputType=amp" in (url or "")
    )


def normalize_url(url: str) -> str:
    """Normalize a URL for duplicate comparison.

    Strips the AMP marker, tracking parameters, scheme, `www.`, fragment and
    trailing slash so that AMP and canonical variants of one article collapse to
    the same key. Meaningful query parameters are KEPT and sorted — dropping the
    whole query string would merge `?story=1` and `?story=2`, which are different
    articles on some CMSs.
    """
    if not (url or "").strip():
        return ""
    u = url.strip()

    parsed = urlparse(u if "//" in u else "//" + u)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("amp."):
        host = host[4:]

    path = parsed.path or ""
    path = re.sub(r"/amp/", "/", path, flags=re.I)
    path = re.sub(r"/amp/?$", "", path, flags=re.I)
    path = re.sub(r"\.amp$", "", path, flags=re.I)

    params = parse_qs(parsed.query)
    clean = {
        k: v for k, v in params.items()
        if not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
        and k.lower() not in _TRACKING_KEYS
        and k.lower() != "amp"
        and k.lower() != "outputtype"
    }
    query = urlencode(sorted(clean.items()), doseq=True)

    out = urlunparse(("", host, path, "", query, ""))
    return out.lstrip("/").rstrip("/").lower() if not query else out.lstrip("/").lower()


def extract_article_id(url: str) -> Optional[str]:
    """Numeric publisher article ID, when the URL carries one.

    law360.com/articles/2509705 -> "2509705"

    Scoped to /article(s)/<digits> deliberately. A looser "any long number in the
    path" rule collides with dates (/2026/08/05/) and would merge unrelated
    stories published on the same day.
    """
    if not url:
        return None
    m = re.search(r"/articles?/(\d{4,})", url, flags=re.I)
    return m.group(1) if m else None


def _norm_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _field(article: Any, name: str, default: str = "") -> str:
    if isinstance(article, dict):
        return article.get(name) or default
    return getattr(article, name, default) or default


def is_duplicate(a: Any, b: Any) -> Tuple[bool, str]:
    """Return (is_duplicate, reason). Reason names the signal that fired."""
    ua, ub = _field(a, "url"), _field(b, "url")

    na, nb = normalize_url(ua), normalize_url(ub)
    if na and nb and na == nb:
        return True, "same normalized URL"

    ida, idb = extract_article_id(ua), extract_article_id(ub)
    if ida and idb and ida == idb:
        return True, f"same article id ({ida})"

    ta, tb = _norm_title(_field(a, "title")), _norm_title(_field(b, "title"))
    if not ta or not tb:
        return False, ""
    sim = jaro_winkler_similarity(ta, tb)

    sa = (_field(a, "outlet") or _field(a, "source")).lower()
    sb = (_field(b, "outlet") or _field(b, "source")).lower()
    if sim > SAME_SOURCE_THRESHOLD and sa and sa == sb:
        return True, f"same source, headline {sim:.2f}"

    if sim > CROSS_SOURCE_THRESHOLD:
        return True, f"syndicated headline {sim:.2f}"

    return False, ""


@dataclass
class DuplicateGroup:
    keeper: Any
    duplicates: List[Any] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)


def _prefer(current: Any, candidate: Any) -> bool:
    """True when `candidate` should replace `current` as the keeper.

    Only one rule: a non-AMP URL beats an AMP one. Otherwise first-seen wins, so
    the ordering the caller established (authority score, in this pipeline) is
    preserved rather than quietly re-litigated here.
    """
    return is_amp_url(_field(current, "url")) and not is_amp_url(_field(candidate, "url"))


def find_duplicates(articles: List[Any]) -> Tuple[List[Any], List[DuplicateGroup]]:
    """Group duplicates without deleting anything.

    Returns (keepers, groups). Every input article appears exactly once across
    `keepers` and the groups' `duplicates` — nothing is dropped on the floor.
    """
    keepers: List[Any] = []
    groups: List[DuplicateGroup] = []

    for art in articles or []:
        matched = None
        reason = ""
        for g in groups:
            dup, why = is_duplicate(g.keeper, art)
            if dup:
                matched, reason = g, why
                break
        if matched is None:
            g = DuplicateGroup(keeper=art)
            groups.append(g)
            keepers.append(art)
            continue

        if _prefer(matched.keeper, art):
            # Promote the non-AMP copy; the previous keeper becomes the duplicate.
            demoted = matched.keeper
            idx = keepers.index(demoted)
            keepers[idx] = art
            matched.keeper = art
            matched.duplicates.append(demoted)
            matched.reasons.append(f"{reason} (AMP demoted)")
        else:
            matched.duplicates.append(art)
            matched.reasons.append(reason)

    dup_total = sum(len(g.duplicates) for g in groups)
    if dup_total:
        logger.info("URL dedup: %d article(s) marked as duplicates across %d group(s)",
                    dup_total, sum(1 for g in groups if g.duplicates))
        for g in groups:
            for d, why in zip(g.duplicates, g.reasons):
                logger.debug("  duplicate: %s  <- %s (%s)",
                             _field(g.keeper, "url")[:80], _field(d, "url")[:80], why)
    return keepers, groups


def duplicate_flag(article: Any, groups: List[DuplicateGroup]) -> str:
    """'Yes' | 'AMP' | 'No' — for the QA spreadsheet's Duplicate Flag column."""
    url = _field(article, "url")
    for g in groups:
        for d in g.duplicates:
            if _field(d, "url") == url:
                return "AMP" if is_amp_url(url) else "Yes"
    return "No"
