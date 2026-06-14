"""
DocuAction Bulletin Intelligence — Executive PDF Generator
Produces the executive PDF attachment that mirrors the HTML email content:
professional cover, table of contents, section anchors, story summaries with
clustered "Similar stories", social summary, page numbers, and AGT footer.

Subscription stories render as HEADLINE + [SUBSCRIPTION REQUIRED] with NO
summary (matches the FCC gold standard). No raw URLs in body, no screenshots.

Uses ReportLab (already in the platform stack). If ReportLab is unavailable,
raises ImportError so the caller can fall back to HTML-only delivery.
"""

from datetime import datetime
from typing import List, Any, Optional, Dict

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, HRFlowable
)
from reportlab.lib import colors

FCC_NAVY = HexColor("#0B3C5D")
FCC_BLUE = HexColor("#0078D4")
GREY = HexColor("#666666")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("CoverTitle", parent=ss["Title"], fontSize=28,
                          textColor=FCC_NAVY, alignment=TA_CENTER, spaceAfter=8))
    ss.add(ParagraphStyle("CoverSub", parent=ss["Normal"], fontSize=14,
                          textColor=GREY, alignment=TA_CENTER, spaceAfter=4))
    ss.add(ParagraphStyle("SectionH", parent=ss["Heading2"], fontSize=14,
                          textColor=FCC_NAVY, spaceBefore=14, spaceAfter=6))
    ss.add(ParagraphStyle("StoryHead", parent=ss["Normal"], fontSize=11,
                          textColor=colors.black, spaceAfter=2, leading=14,
                          fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle("StoryBody", parent=ss["Normal"], fontSize=10,
                          textColor=colors.black, spaceAfter=6, leading=13))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], fontSize=9,
                          textColor=GREY, fontName="Helvetica-Oblique", spaceAfter=6))
    ss.add(ParagraphStyle("Similar", parent=ss["Normal"], fontSize=9,
                          textColor=GREY, leftIndent=14, spaceAfter=1))
    ss.add(ParagraphStyle("TOC", parent=ss["Normal"], fontSize=10,
                          textColor=FCC_NAVY, spaceAfter=2))
    return ss


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(0.75 * inch, 0.5 * inch,
                      "DocuAction AI — Alliance Global Tech, Inc. · CONFIDENTIAL")
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch,
                           f"Page {doc.page}")
    canvas.restoreState()


def _is_paywalled(a: Any) -> bool:
    return bool(getattr(a, "is_paywalled", False))


def generate_pdf(out_path: str,
                 agency_name: str,
                 agency_short: str,
                 briefing_date: str,
                 sections: List[str],
                 section_labels: Dict[str, str],
                 articles_by_section: Dict[str, List[Any]],
                 similar_map: Dict[str, List[Any]],
                 social_summary_html: str = "",
                 distribution_email: str = "") -> str:
    """
    Build the executive PDF. `articles_by_section` maps section_id → [primary
    articles]; `similar_map` maps primary.article_id → [similar articles].
    Returns out_path.
    """
    ss = _styles()
    doc = SimpleDocTemplate(out_path, pagesize=LETTER,
                            topMargin=0.9 * inch, bottomMargin=0.8 * inch,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            title=f"{agency_short} Daily News Briefing — {briefing_date}")
    flow: List[Any] = []

    # ── Cover ──
    flow.append(Spacer(1, 2.0 * inch))
    flow.append(Paragraph(f"{agency_short} Daily News Briefing", ss["CoverTitle"]))
    flow.append(Paragraph(briefing_date, ss["CoverSub"]))
    flow.append(Spacer(1, 0.3 * inch))
    flow.append(HRFlowable(width="60%", color=FCC_BLUE, thickness=2))
    flow.append(Spacer(1, 0.3 * inch))
    flow.append(Paragraph(agency_name, ss["CoverSub"]))
    flow.append(Paragraph("Prepared by DocuAction AI · Alliance Global Tech, Inc.", ss["CoverSub"]))
    flow.append(Paragraph("CONFIDENTIAL — Executive Distribution", ss["Sub"]))
    flow.append(PageBreak())

    # ── Table of Contents ──
    flow.append(Paragraph("Table of Contents", ss["SectionH"]))
    idx = 1
    for sec in sections:
        arts = articles_by_section.get(sec, [])
        if not arts:
            continue
        label = section_labels.get(sec, sec)
        flow.append(Paragraph(f"{idx}. {label} ({len(arts)})", ss["TOC"]))
        idx += 1
    if social_summary_html:
        flow.append(Paragraph(f"{idx}. Social Media Summary", ss["TOC"]))
    flow.append(PageBreak())

    # ── Sections ──
    for sec in sections:
        arts = articles_by_section.get(sec, [])
        if not arts:
            continue
        flow.append(Paragraph(section_labels.get(sec, sec), ss["SectionH"]))
        flow.append(HRFlowable(width="100%", color=HexColor("#dddddd"), thickness=0.5))
        for a in arts:
            outlet = (getattr(a, "outlet", "") or "News").upper()
            title = getattr(a, "title", "") or ""
            if _is_paywalled(a):
                flow.append(Paragraph(f"{outlet}: {title}", ss["StoryHead"]))
                flow.append(Paragraph("[SUBSCRIPTION REQUIRED]", ss["Sub"]))
            else:
                flow.append(Paragraph(f"{outlet}: {title}", ss["StoryHead"]))
                summ = (getattr(a, "summary", "") or "").strip()[:600]
                if summ:
                    flow.append(Paragraph(summ, ss["StoryBody"]))
            # Similar stories
            sims = similar_map.get(getattr(a, "article_id", ""), [])
            if sims:
                flow.append(Paragraph("Similar stories:", ss["Similar"]))
                for s in sims[:6]:
                    so = (getattr(s, "outlet", "") or "News").upper()
                    flow.append(Paragraph(f"• {so}: {getattr(s,'title','')}", ss["Similar"]))
            flow.append(Spacer(1, 0.08 * inch))

    # ── Social Media Summary ──
    if social_summary_html:
        flow.append(PageBreak())
        flow.append(Paragraph("Social Media Summary", ss["SectionH"]))
        # social_summary_html may contain simple tags; strip to text paragraphs
        import re as _re
        text = _re.sub(r"<[^>]+>", " ", social_summary_html)
        text = _re.sub(r"\s+", " ", text).strip()
        for para in text.split("  "):
            if para.strip():
                flow.append(Paragraph(para.strip(), ss["StoryBody"]))

    # ── Footer note ──
    flow.append(Spacer(1, 0.3 * inch))
    flow.append(HRFlowable(width="100%", color=HexColor("#dddddd"), thickness=0.5))
    foot = f"Generated by DocuAction AI — Alliance Global Tech, Inc."
    if distribution_email:
        foot += f" · {distribution_email}"
    flow.append(Paragraph(foot, ss["Sub"]))

    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return out_path
