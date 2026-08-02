"""Weekly / quarterly / priority review reports, archived as delivered.

TWO RULES THIS MODULE EXISTS TO ENFORCE

1. A report is a snapshot, not a query. Once generated it is stored — data and
   rendered HTML — and never recomputed. If an entity is re-verified next week,
   the report issued this week must still say what the client received.
   Regenerating on read would quietly rewrite history.

2. The limitations section is MANDATORY and always present, even when it reads
   "None identified." A report that silently omits what could not be checked
   invites the reader to assume full coverage. Stating "SAM.gov: unavailable —
   API key not provisioned" is the difference between a defensible federal
   deliverable and an overclaim.
"""
from __future__ import annotations

import html as _html
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.tefca_registry.bucket_classifier import (
    FAILED, NOT_CHECKED, NOT_FOUND, UNAVAILABLE, VERIFIED)
from app.tefca_registry.sampling_engine import discrepancy_rate_ci

logger = logging.getLogger(__name__)

CONTRACT = "7571MN26F80064"
NAVY = "#003087"
BLUE = "#0078D4"

BUCKET_LABELS = {
    "B1": "No Discrepancy",
    "B2": "Minor / Administrative",
    "B3": "Inexplicable — manual review",
    "B4": "Non-Compliant",
}

# Sources with no connector today. Named explicitly so the limitations section
# reports a known gap rather than leaving the reader to infer one.
KNOWN_GAPS = {
    "sam_gov": "SAM.gov: API key configured. Entity lookup endpoints returning "
               "404 — API version under investigation.",
    "rce_directory": "Not checked — access pending Case #00055525",
    "state_registry": "Not checked — no connector implemented",
    "irs": "Not checked — no connector implemented; IRS data is keyed on EIN, "
           "which the registry does not currently hold",
}


def report_id_for(report_type: str, period_end: date) -> str:
    """WR-2026-W31 / QR-2026-Q3 / PR-2026-08-01."""
    if report_type == "weekly":
        iso = period_end.isocalendar()
        return f"WR-{iso[0]}-W{iso[1]:02d}"
    if report_type == "quarterly":
        return f"QR-{period_end.year}-Q{((period_end.month - 1) // 3) + 1}"
    return f"PR-{period_end.isoformat()}"


def _bucket_counts(reviews: List[dict]) -> Dict[str, Any]:
    counts = {b: 0 for b in ("B1", "B2", "B3", "B4")}
    ids: Dict[str, List[str]] = {b: [] for b in counts}
    for r in reviews:
        b = r.get("classification_bucket")
        # A reclassified review counts in the bucket a human resolved it to,
        # not the one the engine first assigned — the resolution is the finding.
        if r.get("reviewer_resolution") == "reclassified" and r.get("reclassified_to"):
            b = r["reclassified_to"]
        if b in counts:
            counts[b] += 1
            ids[b].append(r.get("review_id"))
    return {"counts": counts, "review_ids": ids}


def _source_coverage(verifications: List[dict]) -> Dict[str, Dict[str, int]]:
    """Per-source tallies across the five states, kept distinct.

    `unavailable` must never be added to `not_found`: one is a third party's
    outage, the other is a statement about the entity.
    """
    out: Dict[str, Dict[str, int]] = {}
    for v in verifications:
        src = v.get("source") or "unknown"
        row = out.setdefault(src, {s: 0 for s in
                                   (VERIFIED, NOT_FOUND, NOT_CHECKED, UNAVAILABLE, FAILED)})
        st = v.get("verification_status")
        if st in row:
            row[st] += 1
        elif st == "clear":
            row[VERIFIED] += 1
    return out


def build_limitations(source_coverage: Dict[str, Dict[str, int]],
                      reviews: List[dict],
                      extra: Optional[List[str]] = None) -> List[str]:
    """Always returns at least one line. Never empty."""
    lines: List[str] = []

    for src, why in KNOWN_GAPS.items():
        seen = source_coverage.get(src)
        if not seen or (seen.get(VERIFIED, 0) + seen.get(NOT_FOUND, 0)) == 0:
            lines.append(f"{src}: {why}")

    for src, row in sorted(source_coverage.items()):
        if src in KNOWN_GAPS:
            continue
        if row.get(UNAVAILABLE):
            lines.append(f"{src}: {row[UNAVAILABLE]} lookup(s) could not reach the "
                         f"source; those entities are not counted as discrepancies.")
        if row.get(FAILED):
            lines.append(f"{src}: {row[FAILED]} lookup(s) returned an error.")

    pending = [r for r in reviews
               if (r.get("classification_bucket") == "B3"
                   and not r.get("reviewer_resolution"))]
    if pending:
        lines.append(f"{len(pending)} B3 entit{'y' if len(pending) == 1 else 'ies'} "
                     f"pending manual resolution: "
                     f"{', '.join(str(r.get('review_id')) for r in pending[:10])}"
                     f"{' …' if len(pending) > 10 else ''}")

    if not reviews:
        lines.append("No reviews were completed in this period; all figures are zero "
                     "by absence of data, not by absence of discrepancies.")

    lines.extend(extra or [])
    return lines or ["None identified."]


def weekly_trend(reviews: List[dict]) -> List[dict]:
    """Per-ISO-week B1-B4 counts, oldest first.

    Only reviews carrying a usable `created_at` can be placed on the timeline.
    Rather than silently dropping the rest — which would make the trend totals
    disagree with the distribution totals for no visible reason — undated
    reviews are counted under an explicit "undated" bucket.
    """
    weeks: Dict[str, Dict[str, int]] = {}
    for r in reviews:
        raw = r.get("created_at")
        key = "undated"
        if raw:
            try:
                dt = (raw if isinstance(raw, datetime)
                      else datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
                iso = dt.isocalendar()
                key = f"{iso[0]}-W{iso[1]:02d}"
            except Exception:
                key = "undated"
        row = weeks.setdefault(key, {"week": key, "b1": 0, "b2": 0, "b3": 0, "b4": 0})
        bucket = (r.get("reclassified_to")
                  if r.get("reviewer_resolution") == "reclassified"
                  else r.get("classification_bucket"))
        if bucket in ("B1", "B2", "B3", "B4"):
            row[bucket.lower()] += 1

    dated = sorted((v for k, v in weeks.items() if k != "undated"),
                   key=lambda x: x["week"])
    return dated + ([weeks["undated"]] if "undated" in weeks else [])


def build_report_data(*, report_type: str, period_start: date, period_end: date,
                      reviews: List[dict], verifications: List[dict],
                      sample: Optional[dict] = None,
                      rule_set_version: Optional[int] = None,
                      extra_limitations: Optional[List[str]] = None) -> dict:
    """The structured report. Every section is present in every report."""
    buckets = _bucket_counts(reviews)
    coverage = _source_coverage(verifications)
    total = len(reviews)
    # A discrepancy is anything that is not B1. B3 counts: "we could not
    # explain it" is a finding, not a pass.
    discrepancies = total - buckets["counts"]["B1"]
    ci = discrepancy_rate_ci(discrepancies, total,
                             confidence=(sample or {}).get("confidence_level", 0.95))

    resolved = [r for r in reviews if r.get("reviewer_resolution")]
    b3_pending = [r for r in reviews
                  if r.get("classification_bucket") == "B3"
                  and not r.get("reviewer_resolution")]
    b4 = [r for r in reviews
          if (r.get("reclassified_to") or r.get("classification_bucket")) == "B4"]

    return {
        "report_type": report_type,
        "contract": CONTRACT,
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "generated_at": datetime.utcnow().isoformat() + "Z",

        "executive_summary": {
            "entities_reviewed": total,
            "discrepancies_found": discrepancies,
            "discrepancy_rate": ci["rate"],
            "b3_pending_manual_review": len(b3_pending),
            "b4_requiring_action": len(b4),
        },

        "sampling_summary": sample or {
            "note": "No sample was drawn for this period; the report covers all "
                    "reviews completed in the window."},

        "classification_distribution": {
            "counts": buckets["counts"],
            "labels": BUCKET_LABELS,
            "review_ids": buckets["review_ids"],
        },

        "discrepancy_rate": ci,

        "verification_coverage": coverage,

        "outstanding_items": {
            "b3_pending_manual_review": {
                "count": len(b3_pending),
                "review_ids": [r.get("review_id") for r in b3_pending],
            },
            "b4_requiring_action": {
                "count": len(b4),
                "review_ids": [r.get("review_id") for r in b4],
            },
            "resolved_this_period": len(resolved),
        },

        "data_sources_used": sorted(coverage.keys()),

        "methodology": {
            "sample_size_formula": "Cochran, with finite population correction",
            "interval_method": "Wilson score interval",
            "interval_note": "Wilson rather than the normal approximation: at these "
                             "sample sizes and rates the normal interval can extend "
                             "below zero, which is not a reportable figure.",
            "bucket_definitions": BUCKET_LABELS,
            "discrepancy_definition": "Any review not classified B1. B3 is counted as "
                                      "a discrepancy: unexplained is not the same as "
                                      "clean.",
            "unavailable_handling": "A source that could not be reached is recorded as "
                                    "unavailable and does NOT count against the entity. "
                                    "Only a source that was reached and returned no "
                                    "record counts as a finding.",
        },

        # Quarterly adds the per-week series; a quarter reported as one number
        # hides whether the rate is improving or deteriorating inside it.
        **({"weekly_trend": weekly_trend(reviews)}
           if report_type == "quarterly" else {}),

        # MANDATORY. Never omitted, never empty.
        "limitations": build_limitations(coverage, reviews, extra_limitations),

        "configuration": {
            "rule_set_version": rule_set_version,
            "confidence_level": (sample or {}).get("confidence_level"),
            "margin_of_error": (sample or {}).get("margin_of_error"),
            "proportion": (sample or {}).get("proportion"),
            "use_fpc": (sample or {}).get("use_fpc"),
            "random_seed": (sample or {}).get("random_seed"),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        },
    }


# ── HTML rendering ───────────────────────────────────────────────────────────

def _esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def render_html(data: dict, report_id: str) -> str:
    """AGT-branded federal-style HTML. Self-contained, no external assets."""
    d = data
    ex = d["executive_summary"]
    counts = d["classification_distribution"]["counts"]
    ci = d["discrepancy_rate"]

    def section(title: str, body: str) -> str:
        return (f'<h2 style="color:{NAVY};font-size:15px;margin:26px 0 8px;'
                f'border-bottom:2px solid {NAVY};padding-bottom:4px">{_esc(title)}</h2>{body}')

    def table(headers, rows) -> str:
        th = "".join(f'<th style="background:{NAVY};color:#fff;padding:6px 10px;'
                     f'text-align:left;font-size:11px">{_esc(h)}</th>' for h in headers)
        trs = []
        for i, r in enumerate(rows):
            bg = "#F5F8FD" if i % 2 else "#FFFFFF"
            tds = "".join(f'<td style="padding:5px 10px;border:1px solid #D0D0D0;'
                          f'font-size:11px">{_esc(c)}</td>' for c in r)
            trs.append(f'<tr style="background:{bg}">{tds}</tr>')
        return ('<table style="border-collapse:collapse;width:100%;margin:6px 0">'
                f'<tr>{th}</tr>{"".join(trs)}</table>')

    bucket_rows = [[b, BUCKET_LABELS[b], counts[b],
                    ", ".join(filter(None, d["classification_distribution"]["review_ids"][b])) or "—"]
                   for b in ("B1", "B2", "B3", "B4")]

    cov_rows = [[src, r.get(VERIFIED, 0), r.get(NOT_FOUND, 0), r.get(UNAVAILABLE, 0),
                 r.get(NOT_CHECKED, 0), r.get(FAILED, 0)]
                for src, r in sorted(d["verification_coverage"].items())]

    lim = "".join(f'<li style="margin-bottom:4px">{_esc(x)}</li>'
                  for x in d["limitations"])
    cfg_rows = [[k, v] for k, v in d["configuration"].items()]

    rate_txt = ("n/a" if ci["rate"] is None
                else f'{ci["rate"]*100:.1f}%  (95% CI '
                     f'{ci["lower"]*100:.1f}%–{ci["upper"]*100:.1f}%)')

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>{_esc(report_id)} — TEFCA ARC Review</title></head>
<body style="margin:0;background:#eef1f6;font-family:'Segoe UI',Arial,sans-serif;color:#222">
<div style="max-width:900px;margin:0 auto;background:#fff">
  <div style="background:{NAVY};color:#fff;padding:22px 30px">
    <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase">Alliance Global Tech, Inc.</div>
    <div style="font-size:22px;font-weight:bold;margin-top:4px">TEFCA ARC — {_esc(d['report_type'].title())} Review Report</div>
    <div style="font-size:12px;color:#d7e8ff;margin-top:6px">
      {_esc(report_id)} &nbsp;|&nbsp; Contract {_esc(d['contract'])} &nbsp;|&nbsp;
      Period {_esc(d['period']['start'])} to {_esc(d['period']['end'])}
    </div>
  </div>
  <div style="padding:20px 30px 40px">
  {section("1. Executive Summary", table(
      ["Metric", "Value"],
      [["Entities reviewed", ex["entities_reviewed"]],
       ["Discrepancies found", ex["discrepancies_found"]],
       ["Discrepancy rate", rate_txt],
       ["B3 pending manual review", ex["b3_pending_manual_review"]],
       ["B4 requiring action", ex["b4_requiring_action"]]]))}
  {section("2. Sampling Summary", table(["Parameter", "Value"],
      [[k, v] for k, v in (d["sampling_summary"] or {}).items()]))}
  {section("3. Classification Distribution (B1–B4)",
      table(["Bucket", "Definition", "Count", "Review IDs"], bucket_rows))}
  {section("4. Discrepancy Rate", table(["Measure", "Value"],
      [["Rate", rate_txt], ["Method", ci["method"]],
       ["Reviewed (n)", ci["n"]], ["Confidence", ci["confidence"]]]))}
  {section("5. Verification Coverage by Source",
      table(["Source", "Verified", "Not found", "Unavailable", "Not checked", "Failed"],
            cov_rows) if cov_rows else "<p style='font-size:12px'>No verification records in this period.</p>")}
  {section("6. Outstanding Items", table(["Item", "Count", "Review IDs"],
      [["B3 pending manual review", d["outstanding_items"]["b3_pending_manual_review"]["count"],
        ", ".join(filter(None, d["outstanding_items"]["b3_pending_manual_review"]["review_ids"])) or "—"],
       ["B4 requiring action", d["outstanding_items"]["b4_requiring_action"]["count"],
        ", ".join(filter(None, d["outstanding_items"]["b4_requiring_action"]["review_ids"])) or "—"],
       ["Resolved this period", d["outstanding_items"]["resolved_this_period"], "—"]]))}
  {section("7. Data Sources Used",
      "<p style='font-size:12px'>" + (_esc(", ".join(d["data_sources_used"])) or "None") + "</p>")}
  {section("8. Methodology Notes", table(["Note", "Detail"],
      [[k.replace("_", " ").title(), v] for k, v in d["methodology"].items()
       if not isinstance(v, dict)]))}
  {section("9. Limitations and Exceptions",
      f'<div style="background:#FFF8E1;border-left:4px solid {BLUE};padding:10px 14px">'
      f'<ul style="margin:0;padding-left:18px;font-size:12px">{lim}</ul></div>')}
  {section("10. Configuration Used", table(["Parameter", "Value"], cfg_rows))}
  </div>
  <div style="background:{NAVY};color:#a8c8f0;padding:12px 30px;font-size:10px;text-align:center">
    Prepared by Alliance Global Tech, Inc. &nbsp;|&nbsp; Contract {_esc(d['contract'])}
    &nbsp;|&nbsp; Generated {_esc(d['generated_at'])}
  </div>
</div></body></html>"""
