"""
DocuAction Bulletin Intelligence — Download Routes
Generates downloadable Word documents for FCC bulletins.
Supports: 1, 2, 3, 4, 5, 7, 30 day ranges.

Add to existing routes.py or register separately.
"""
import io
import re
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
try:
    from docx.oxml.ns import qn
except ImportError:
    qn = None  # python-docx not installed — download endpoint will return 500

logger = logging.getLogger("docuaction.bulletin.download")
router = APIRouter(prefix="/api/v1/bulletin", tags=["Bulletin Downloads"])

ALLOWED_DAYS = [1, 2, 3, 4, 5, 7, 30]


@router.get("/download/{agency_id}")
async def download_bulletin(
    agency_id: str,
    days: int = Query(1, description="Number of days: 1, 2, 3, 4, 5, 7, or 30"),
):
    """
    Download a formatted Word document bulletin for the specified time period.
    Articles are organized by topic with clickable links to source URLs.
    """
    if days not in ALLOWED_DAYS:
        raise HTTPException(400, f"Invalid days parameter. Allowed: {ALLOWED_DAYS}")

    # Get articles from the bulletin engine
    try:
        from app.bulletin_intelligence.engine import _articles, _agencies
    except ImportError:
        raise HTTPException(500, "Bulletin engine not available")

    agency = _agencies.get(agency_id)
    if not agency:
        raise HTTPException(404, f"Agency {agency_id} not found")

    # Filter articles by date range
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered = []
    for art in _articles.values():
        if art.agency_id != agency_id:
            continue
        try:
            # Try parsing the article date
            art_date = None
            if hasattr(art, 'ingested_at') and art.ingested_at:
                if isinstance(art.ingested_at, datetime):
                    art_date = art.ingested_at
                elif isinstance(art.ingested_at, str):
                    art_date = datetime.fromisoformat(art.ingested_at.replace('Z', '+00:00'))

            if art_date and art_date.tzinfo is None:
                art_date = art_date.replace(tzinfo=timezone.utc)

            if art_date and art_date >= cutoff:
                filtered.append(art)
            elif not art_date:
                # Include if we can't parse the date (better to include than miss)
                filtered.append(art)
        except Exception:
            filtered.append(art)

    if not filtered:
        raise HTTPException(404, f"No articles found for the last {days} day(s)")

    # Organize by topic
    topics = {}
    topic_labels = {
        "fcc_news_events": "FCC News & Events",
        "consumers_advocacy": "Consumers & Advocacy",
        "media_broadcasting": "Media & Broadcasting",
        "public_safety_emergency": "Public Safety & Emergency",
        "wireless_mobile": "Wireless & Mobile",
        "ai_emerging_tech": "AI & Emerging Technology",
        "business_industry": "Business & Industry",
        "international_affairs": "International Affairs",
        "space_communications": "Space & Communications",
        "spectrum_policy": "Spectrum Policy",
    }
    topic_colors = {
        "fcc_news_events": "2563EB",
        "consumers_advocacy": "D97706",
        "media_broadcasting": "DC2626",
        "public_safety_emergency": "EF4444",
        "wireless_mobile": "8B5CF6",
        "ai_emerging_tech": "0D9488",
        "business_industry": "0F172A",
        "international_affairs": "6366F1",
        "space_communications": "3B82F6",
        "spectrum_policy": "7C3AED",
    }

    for art in filtered:
        topic = getattr(art, 'topic', 'other') or 'other'
        if topic not in topics:
            topics[topic] = []
        topics[topic].append(art)

    # Sort each topic by relevance score (highest first)
    for topic in topics:
        topics[topic].sort(key=lambda a: getattr(a, 'relevance_score', 0) or 0, reverse=True)

    # Generate Word document
    try:
        from docx import Document as DocxDocument
        from docx.shared import Inches, Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT
    except ImportError:
        raise HTTPException(500, "python-docx not installed. Add 'python-docx' to requirements.txt")

    doc = DocxDocument()

    # Page margins
    for section in doc.sections:
        section.top_margin = Cm(1.5)
        section.bottom_margin = Cm(1.5)
        section.left_margin = Cm(2)
        section.right_margin = Cm(2)

    # Title
    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title_para.add_run("FCC DAILY INTELLIGENCE BRIEFING")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x25, 0x63, 0xEB)
    run.font.bold = True

    title2 = doc.add_paragraph()
    title2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run2 = title2.add_run(agency.name)
    run2.font.size = Pt(22)
    run2.font.bold = True
    run2.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    # Date range
    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    now = datetime.now(timezone.utc)
    if days == 1:
        date_text = f"{now.strftime('%B %d, %Y')} | Last 24 Hours"
    else:
        start = (now - timedelta(days=days)).strftime('%B %d')
        end = now.strftime('%B %d, %Y')
        date_text = f"{start} — {end} | Last {days} Days"
    run3 = date_para.add_run(date_text)
    run3.font.size = Pt(10)
    run3.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

    # Stats line
    stats = doc.add_paragraph()
    stats.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run4 = stats.add_run(f"{len(filtered)} Articles | {len(topics)} Topics | AI-Classified")
    run4.font.size = Pt(8)
    run4.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
    run4.font.italic = True

    doc.add_paragraph()

    # Topic Index
    idx_title = doc.add_paragraph()
    run_idx = idx_title.add_run("TOPIC INDEX")
    run_idx.font.size = Pt(12)
    run_idx.font.bold = True
    run_idx.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    for topic_key, articles in sorted(topics.items(), key=lambda x: len(x[1]), reverse=True):
        label = topic_labels.get(topic_key, topic_key.replace('_', ' ').title())
        idx_line = doc.add_paragraph()
        idx_line.paragraph_format.space_after = Pt(2)
        run_dot = idx_line.add_run("■ ")
        color_hex = topic_colors.get(topic_key, "333333")
        run_dot.font.color.rgb = RGBColor(int(color_hex[:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16))
        run_dot.font.size = Pt(10)
        run_label = idx_line.add_run(f"{label} ({len(articles)} articles)")
        run_label.font.size = Pt(10)
        run_label.font.bold = True
        run_label.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    doc.add_page_break()

    # Each topic section
    for topic_key, articles in sorted(topics.items(), key=lambda x: len(x[1]), reverse=True):
        label = topic_labels.get(topic_key, topic_key.replace('_', ' ').title())
        color_hex = topic_colors.get(topic_key, "333333")

        # Topic header
        header_para = doc.add_paragraph()
        header_para.paragraph_format.space_before = Pt(12)
        header_para.paragraph_format.space_after = Pt(8)
        run_h = header_para.add_run(f"━━━  {label.upper()}  ━━━")
        run_h.font.size = Pt(13)
        run_h.font.bold = True
        run_h.font.color.rgb = RGBColor(int(color_hex[:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16))

        # Articles
        for i, art in enumerate(articles):
            title_text = getattr(art, 'title', '') or 'Untitled'
            url = getattr(art, 'url', '') or ''
            outlet = getattr(art, 'outlet', '') or ''
            summary = getattr(art, 'summary', '') or ''
            pub_date = getattr(art, 'published_at', '') or ''
            relevance = getattr(art, 'relevance_score', 0) or 0
            sentiment = getattr(art, 'sentiment', '') or ''

            # Clean title
            title_text = title_text.split(' - ')[0].split(' | ')[0].strip()
            if len(title_text) > 120:
                title_text = title_text[:117] + '...'

            # Clean summary - take first 2-3 sentences
            if summary:
                summary = summary.replace('\n', ' ').strip()
                # Remove HTML tags
                summary = re.sub(r'<[^>]+>', '', summary)
                # Take first 300 chars
                if len(summary) > 300:
                    # Find last sentence break
                    cut = summary[:300].rfind('.')
                    if cut > 100:
                        summary = summary[:cut + 1]
                    else:
                        summary = summary[:300] + '...'

            # Article number + title as hyperlink
            art_para = doc.add_paragraph()
            art_para.paragraph_format.space_before = Pt(6)
            art_para.paragraph_format.space_after = Pt(2)
            run_num = art_para.add_run(f"{i + 1}. ")
            run_num.font.size = Pt(10)
            run_num.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

            # Add hyperlink
            if url:
                # Create hyperlink using python-docx
                hyperlink = _add_hyperlink(art_para, url, title_text)
            else:
                run_title = art_para.add_run(title_text)
                run_title.font.size = Pt(10)
                run_title.font.bold = True
                run_title.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

            # Source line
            if outlet or pub_date:
                source_para = doc.add_paragraph()
                source_para.paragraph_format.space_after = Pt(2)
                source_para.paragraph_format.left_indent = Cm(0.5)
                source_text = f"{outlet}"
                if pub_date:
                    # Clean date
                    try:
                        if 'T' in str(pub_date):
                            dt = datetime.fromisoformat(str(pub_date).replace('Z', '+00:00'))
                            source_text += f" | {dt.strftime('%b %d, %Y')}"
                        else:
                            source_text += f" | {pub_date}"
                    except Exception:
                        source_text += f" | {pub_date}"

                if relevance > 0:
                    source_text += f" | Relevance: {int(relevance * 100)}%"

                run_src = source_para.add_run(source_text)
                run_src.font.size = Pt(8)
                run_src.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
                run_src.font.italic = True

            # Summary
            if summary:
                sum_para = doc.add_paragraph()
                sum_para.paragraph_format.space_after = Pt(8)
                sum_para.paragraph_format.left_indent = Cm(0.5)
                run_sum = sum_para.add_run(summary)
                run_sum.font.size = Pt(9)
                run_sum.font.color.rgb = RGBColor(0x33, 0x41, 0x55)

        doc.add_paragraph()  # Space between topics

    # Footer
    footer_para = doc.add_paragraph()
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_para.paragraph_format.space_before = Pt(20)
    run_f1 = footer_para.add_run("Prepared by Bulletin Intelligence — Powered by DocuAction AI\n")
    run_f1.font.size = Pt(8)
    run_f1.font.bold = True
    run_f1.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)
    run_f2 = footer_para.add_run("Alliance Global Tech, Inc. | AI-Generated Classification | Human Review Required")
    run_f2.font.size = Pt(7)
    run_f2.font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)

    # Save to buffer
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    # Generate filename
    if days == 1:
        fname = f"FCC_Briefing_{now.strftime('%Y%m%d')}.docx"
    else:
        fname = f"FCC_Briefing_{days}day_{now.strftime('%Y%m%d')}.docx"

    logger.info(f"Bulletin download: agency={agency_id} days={days} articles={len(filtered)} topics={len(topics)}")

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.get("/download-options/{agency_id}")
async def download_options(agency_id: str):
    """Show available download options with article counts per time period."""
    try:
        from app.bulletin_intelligence.engine import _articles, _agencies
    except ImportError:
        raise HTTPException(500, "Bulletin engine not available")

    agency = _agencies.get(agency_id)
    if not agency:
        raise HTTPException(404, f"Agency {agency_id} not found")

    now = datetime.now(timezone.utc)
    options = []

    for d in ALLOWED_DAYS:
        cutoff = now - timedelta(days=d)
        count = 0
        for art in _articles.values():
            if art.agency_id != agency_id:
                continue
            try:
                art_date = None
                if hasattr(art, 'ingested_at') and art.ingested_at:
                    if isinstance(art.ingested_at, datetime):
                        art_date = art.ingested_at
                    elif isinstance(art.ingested_at, str):
                        art_date = datetime.fromisoformat(art.ingested_at.replace('Z', '+00:00'))
                if art_date and art_date.tzinfo is None:
                    art_date = art_date.replace(tzinfo=timezone.utc)
                if art_date and art_date >= cutoff:
                    count += 1
                elif not art_date:
                    count += 1
            except Exception:
                count += 1

        label = f"Last 24 Hours" if d == 1 else f"Last {d} Days"
        options.append({
            "days": d,
            "label": label,
            "article_count": count,
            "download_url": f"/api/v1/bulletin/download/{agency_id}?days={d}",
        })

    return {
        "agency_id": agency_id,
        "agency_name": agency.name,
        "options": options,
        "total_articles_in_archive": len([a for a in _articles.values() if a.agency_id == agency_id]),
    }


def _add_hyperlink(paragraph, url, text):
    """Add a clickable hyperlink to a python-docx paragraph."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    part = paragraph.part
    r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)

    hyperlink = paragraph._element.makeelement(qn('w:hyperlink'), {qn('r:id'): r_id})

    new_run = paragraph._element.makeelement(qn('w:r'), {})
    rPr = paragraph._element.makeelement(qn('w:rPr'), {})

    # Color
    color_elem = paragraph._element.makeelement(qn('w:color'), {qn('w:val'): '2563EB'})
    rPr.append(color_elem)

    # Underline
    u_elem = paragraph._element.makeelement(qn('w:u'), {qn('w:val'): 'single'})
    rPr.append(u_elem)

    # Bold
    b_elem = paragraph._element.makeelement(qn('w:b'), {})
    rPr.append(b_elem)

    # Font size 10pt = 20 half-points
    sz_elem = paragraph._element.makeelement(qn('w:sz'), {qn('w:val'): '20'})
    rPr.append(sz_elem)

    # Font name
    font_elem = paragraph._element.makeelement(qn('w:rFonts'), {qn('w:ascii'): 'Arial', qn('w:hAnsi'): 'Arial'})
    rPr.append(font_elem)

    new_run.append(rPr)

    text_elem = paragraph._element.makeelement(qn('w:t'), {})
    text_elem.text = text
    new_run.append(text_elem)

    hyperlink.append(new_run)
    paragraph._element.append(hyperlink)

    return hyperlink
