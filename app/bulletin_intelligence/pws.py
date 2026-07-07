"""FCC Bulletin — PWS coverage aggregation + source classification (additive).

Purpose: help AGT answer "did we search enough quality sources, cover the required
topics, and meet the operational intent of the PWS?" — using ONLY real run/registry
data.

HONESTY GUARANTEES:
  * Required-source coverage and PWS-topic compliance are computed ONLY from an
    Appendix A source registry that AGT loads (POST /sources). Until then they are
    reported as "pending_appendix_a" — never fabricated, never estimated.
  * Source classifications from the registry are "authoritative"; a small,
    transparently-labeled heuristic provides non-authoritative hints for sources
    not yet in the registry. Counts are reported separately so nothing is passed
    off as verified.
  * No Coverage % is invented. Editorial confidence is reported as null until a
    real per-article confidence signal is instrumented.
"""
import os
import re
from typing import Any, Dict, List

# Minimum story target (contractual intent). Configurable; NEVER used to force or
# dilute stories — only to flag "Coverage Below Target" for editor review.
PWS_MIN_TARGET = int(os.getenv("BULLETIN_PWS_MIN_TARGET", "60"))

# The 10 PWS source classifications (from the Final Engineering Directive).
SOURCE_CLASSIFICATIONS = [
    "wire_service", "major_newspaper", "trade_publication", "technology_publication",
    "business_publication", "broadcast", "radio", "government", "regional", "international",
]
CLASSIFICATION_LABELS = {
    "wire_service": "Wire Service",
    "major_newspaper": "Major Newspaper",
    "trade_publication": "Trade Publication",
    "technology_publication": "Technology Publication",
    "business_publication": "Business Publication",
    "broadcast": "Broadcast",
    "radio": "Radio",
    "government": "Government",
    "regional": "Regional",
    "international": "International",
    "unclassified": "Unclassified (assign in registry)",
}

# Minimal, TRANSPARENT heuristic hints — NON-AUTHORITATIVE. Registry assignments
# always win. Kept intentionally conservative; the real classifications come from
# the Appendix A registry loaded by AGT.
_HEURISTIC = [
    (r"\.gov(\b|/|$)|federalregister|fcc\.gov|congress\.gov|regulations\.gov", "government"),
    (r"reuters|apnews|ap\.org|bloomberg|afp\.com|prnewswire|businesswire", "wire_service"),
    (r"techcrunch|theverge|arstechnica|wired|zdnet|techtimes|ibtimes|techcabal", "technology_publication"),
    (r"wsj\.com|ft\.com|cnbc|forbes|marketwatch|businesstoday|economictimes", "business_publication"),
    (r"broadcastingcable|tvtechnology|radioworld|insideradio|rbr\.com", "trade_publication"),
]


def classify(name: str, registry_map: Dict[str, Dict[str, Any]]):
    """Return (classification, source) where source ∈ {'registry','heuristic','none'}."""
    if not name:
        return "unclassified", "none"
    reg = registry_map.get(name)
    if reg and reg.get("type"):
        return reg["type"], "registry"
    low = name.lower()
    for pat, cls in _HEURISTIC:
        if re.search(pat, low):
            return cls, "heuristic"
    return "unclassified", "none"


def _suggest(dist: Dict[str, Dict[str, int]], missing: List[str], has_run: bool) -> List[str]:
    """Editor coverage-gap SUGGESTIONS (never auto-import). Derived from real gaps."""
    if not has_run:
        return ["No collection run recorded yet — run a cycle to assess coverage."]
    s: List[str] = []
    for c in ["broadcast", "radio", "trade_publication", "regional", "government"]:
        if dist.get(c, {}).get("sources", 0) == 0:
            s.append(f"Coverage appears light: no {CLASSIFICATION_LABELS[c]} sources in the latest run — consider adding coverage.")
    if missing:
        s.append("Missing FCC categories: " + ", ".join(missing) + " — consider searching these areas.")
    if not s:
        s.append("No obvious coverage gaps detected in the latest run.")
    return s


async def build_pws_coverage(agency_id: str) -> Dict[str, Any]:
    """Aggregate an honest PWS coverage picture from the latest run + registry."""
    from . import bulletin_store as store
    registry = await store.load_source_registry()
    reg_map = {r.get("name"): r for r in registry if r.get("name")}
    runs = await store.load_run_logs(agency_id=agency_id, limit=1)
    run = runs[0] if runs else None
    outcomes = await store.load_source_outcomes(run["run_id"]) if run else []
    cov = (run or {}).get("coverage") or {}

    # Source distribution by classification (authoritative vs heuristic split).
    dist: Dict[str, Dict[str, int]] = {c: {"sources": 0, "authoritative": 0, "heuristic": 0}
                                       for c in SOURCE_CLASSIFICATIONS + ["unclassified"]}
    for o in outcomes:
        cls, how = classify(o.get("source"), reg_map)
        d = dist.setdefault(cls, {"sources": 0, "authoritative": 0, "heuristic": 0})
        d["sources"] += 1
        if how == "registry":
            d["authoritative"] += 1
        elif how == "heuristic":
            d["heuristic"] += 1

    # Required-source coverage — HONEST: pending until Appendix A registry loaded.
    expected = [r for r in registry if r.get("enabled")]
    if expected:
        succeeded = {o.get("source") for o in outcomes if o.get("succeeded")}
        covered = [r for r in expected if r.get("name") in succeeded]
        required = {
            "status": "measured",
            "expected": len(expected),
            "covered": len(covered),
            "coverage_pct": round(100.0 * len(covered) / len(expected), 1),
        }
    else:
        required = {
            "status": "pending_appendix_a",
            "expected": 0, "covered": 0, "coverage_pct": None,
            "note": ("Load the Appendix A approved-source list into the registry "
                     "(POST /sources) to measure required-source coverage. Until then "
                     "this is not computed (no estimate)."),
        }

    missing = cov.get("missing_category_warnings") or []
    accepted = run.get("in_briefing") if run else None
    suggestions = _suggest(dist, missing, run is not None)

    # Minimum target (contractual intent) — flag below-target, NEVER force stories.
    target = {
        "minimum": PWS_MIN_TARGET,
        "accepted": accepted,
        "meets_target": (accepted is not None and accepted >= PWS_MIN_TARGET),
        "shortfall": (max(0, PWS_MIN_TARGET - accepted) if accepted is not None else None),
        "status": ("no_run" if accepted is None else ("meets_target" if accepted >= PWS_MIN_TARGET else "below_target")),
        "note": ("If fewer legitimate FCC stories exist than the target, return fewer and flag "
                 "for editor review — never pad with unrelated news to reach the number."),
    }

    # Editor-assistance signals — real counts where available; honest pending otherwise.
    editor_assistance = {
        "rejected_stories": run.get("rejected") if run else None,
        "duplicate_stories": run.get("dupes_removed") if run else None,
        "subscription_articles": cov.get("subscription_stories"),
        "missing_categories": missing,
        "source_distribution": "see source_distribution",
        "coverage_suggestions": suggestions,
        "low_confidence_stories": None,     # pending per-article confidence instrumentation
        "potential_missing_stories": None,  # pending Talkwalker comparison
    }

    return {
        "agency_id": agency_id,
        "has_run": run is not None,
        "run_id": run["run_id"] if run else None,
        "generated_from": "latest_run",
        "target": target,
        "editor_assistance": editor_assistance,
        "totals": {
            "collected": run.get("ingested") if run else None,
            "duplicates_removed": run.get("dupes_removed") if run else None,
            "accepted": run.get("in_briefing") if run else None,
            "rejected": run.get("rejected") if run else None,
            "subscription": cov.get("subscription_stories"),
        },
        "categories": {"missing": missing, "missing_count": len(missing)},
        "source_distribution": dist,
        "registry_size": len(registry),
        "required_source_coverage": required,
        "classification_coverage": {
            c: dist.get(c, {}).get("sources", 0)
            for c in ["broadcast", "radio", "trade_publication", "government", "regional"]
        },
        "editorial_confidence": None,  # honest: no per-article confidence signal instrumented yet
        "coverage_status": ("review_gaps" if missing else ("ok" if run else "no_run")),
        "suggestions": suggestions,
        "notes": ("PWS-topic compliance and required-source coverage require the "
                  "Appendix A source list loaded into the registry. Classifications "
                  "marked 'heuristic' are non-authoritative hints — assign authoritative "
                  "classifications in the registry. No Coverage % or confidence value is "
                  "estimated."),
    }
