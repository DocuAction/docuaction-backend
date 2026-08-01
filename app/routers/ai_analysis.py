from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import ProposalLibraryItem
from app.services.auth import get_current_user
import os
import httpx

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/ai", tags=["AI Analysis"], dependencies=[Depends(require_role("contributor"))])
class TextAnalysisRequest(BaseModel):
    text: str
    project_id: str | None = None
    use_library: bool = True


def _check_key():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(400, "ANTHROPIC_API_KEY not set in Railway Variables.")
    if not key.startswith("sk-ant-"):
        raise HTTPException(400, f"Invalid API key format.")


@router.get("/check-key")
async def check_api_key():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return {"status": "missing"}
    if not key.startswith("sk-ant-"):
        return {"status": "invalid_format", "message": f"Starts with '{key[:10]}...'"}
    try:
        from anthropic import Anthropic
        c = Anthropic(api_key=key)
        msg = c.messages.create(model="claude-sonnet-4-20250514", max_tokens=10, messages=[{"role": "user", "content": "OK"}])
        return {"status": "valid", "message": "Working", "response": msg.content[0].text}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def _get_library_context(db: AsyncSession, keywords: str = "") -> str:
    """Get relevant past proposals for AI context."""
    try:
        if keywords:
            terms = keywords.split()[:3]
            conditions = []
            for t in terms:
                conditions.append(ProposalLibraryItem.keywords.ilike(f"%{t}%"))
                conditions.append(ProposalLibraryItem.title.ilike(f"%{t}%"))
            q = select(ProposalLibraryItem).where(or_(*conditions)).limit(3)
        else:
            q = select(ProposalLibraryItem).where(
                ProposalLibraryItem.outcome == "Won"
            ).order_by(ProposalLibraryItem.created_at.desc()).limit(3)

        result = await db.execute(q)
        items = result.scalars().all()
        if not items:
            return ""

        context_parts = []
        for item in items:
            preview = item.content[:2000] if item.content else ""
            context_parts.append(f"[{item.category} - {item.title}]\n{preview}")

        return "\n\n---\n\n".join(context_parts)
    except Exception:
        return ""


# ── ANALYZE TEXT ──
@router.post("/analyze-text")
async def analyze_text(req: TextAnalysisRequest, user=Depends(get_current_user)):
    _check_key()
    from app.services.ai_analysis import analyze_rfq
    try:
        return {"analysis": analyze_rfq(req.text), "status": "success"}
    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)}")


# ── UPLOAD FILE + ANALYZE ──
@router.post("/analyze-file")
async def analyze_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    _check_key()
    content = await file.read()
    if len(content) > 10_000_000:
        raise HTTPException(400, "File too large (max 10MB)")

    from app.services.doc_extract import extract_text
    try:
        text = extract_text(content, file.filename or "file.txt")
    except Exception as e:
        raise HTTPException(400, f"Cannot read file: {str(e)}")

    if not text or len(text.strip()) < 50:
        raise HTTPException(400, "Not enough text extracted. Try pasting content instead.")

    from app.services.ai_analysis import analyze_rfq
    try:
        analysis = analyze_rfq(text)
    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)}")

    return {"analysis": analysis, "extracted_text_length": len(text), "filename": file.filename, "status": "success"}


# ── GENERATE RESPONSE (text only) ──
@router.post("/generate-response")
async def generate_response(req: TextAnalysisRequest, user=Depends(get_current_user)):
    _check_key()
    from app.services.ai_analysis import analyze_rfq, generate_response_draft
    try:
        analysis = analyze_rfq(req.text)
        draft = generate_response_draft(analysis)
    except Exception as e:
        raise HTTPException(500, f"Failed: {str(e)}")
    return {"analysis": analysis, "response_draft": draft, "status": "success"}


# ── QUICK WORD PROPOSAL (template + single AI call) ──
@router.post("/generate-proposal-quick")
async def generate_proposal_quick(req: TextAnalysisRequest, user=Depends(get_current_user)):
    """Quick proposal: 1 AI call + Word template = 16-20 page .docx in ~30 seconds."""
    _check_key()
    from app.services.ai_analysis import analyze_rfq
    from app.services.proposal_generator import generate_proposal_docx

    try:
        analysis = analyze_rfq(req.text)
    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)}")

    try:
        docx_bytes = generate_proposal_docx(analysis)
    except Exception as e:
        raise HTTPException(500, f"Document generation failed: {str(e)}")

    title = analysis.get('title', analysis.get('solicitation_number', 'RFQ'))
    safe = ''.join(c for c in str(title) if c.isalnum() or c in '-_ ')[:40]

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="AGT-Proposal-{safe}.docx"'}
    )


# ── DEEP WORD PROPOSAL (multi-section AI + library context) ──
@router.post("/generate-proposal-deep")
async def generate_proposal_deep(
    req: TextAnalysisRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deep proposal: 7 AI calls + library context = 30-40 page .docx in ~3-5 minutes."""
    _check_key()
    from app.services.ai_analysis import analyze_rfq, generate_full_proposal
    from app.services.proposal_generator import generate_proposal_docx

    try:
        analysis = analyze_rfq(req.text)
    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)}")

    # Get library context
    library_context = ""
    if req.use_library:
        keywords = f"{analysis.get('agency', '')} {analysis.get('naics_code', '')} {analysis.get('title', '')}"
        library_context = await _get_library_context(db, keywords)

    try:
        sections = generate_full_proposal(analysis, library_context)
    except Exception as e:
        raise HTTPException(500, f"Proposal generation failed: {str(e)}")

    try:
        docx_bytes = generate_proposal_docx(analysis, sections=sections)
    except Exception as e:
        raise HTTPException(500, f"Document assembly failed: {str(e)}")

    title = analysis.get('title', 'RFQ')
    safe = ''.join(c for c in str(title) if c.isalnum() or c in '-_ ')[:40]

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="AGT-Proposal-Deep-{safe}.docx"'}
    )


# ── UPLOAD FILE + QUICK PROPOSAL ──
@router.post("/upload-and-propose")
async def upload_and_propose(file: UploadFile = File(...), user=Depends(get_current_user)):
    _check_key()
    content = await file.read()
    from app.services.doc_extract import extract_text
    try:
        text = extract_text(content, file.filename or "file.txt")
    except Exception as e:
        raise HTTPException(400, f"Cannot read file: {str(e)}")

    if not text or len(text.strip()) < 50:
        raise HTTPException(400, "Not enough text extracted.")

    from app.services.ai_analysis import analyze_rfq
    from app.services.proposal_generator import generate_proposal_docx
    try:
        analysis = analyze_rfq(text)
        docx_bytes = generate_proposal_docx(analysis)
    except Exception as e:
        raise HTTPException(500, f"Failed: {str(e)}")

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="AGT-Proposal.docx"'}
    )


# ── EXPORT PDF ──
@router.post("/export-pdf")
async def export_pdf(req: TextAnalysisRequest, user=Depends(get_current_user)):
    _check_key()
    from app.services.ai_analysis import analyze_rfq, generate_response_draft
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER

    try:
        analysis = analyze_rfq(req.text)
        draft = generate_response_draft(analysis)
    except Exception as e:
        raise HTTPException(500, f"Failed: {str(e)}")

    def clean(t): return str(t).replace('&', '&amp;').replace('<', '&lt;')

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=0.75*inch, rightMargin=0.75*inch, topMargin=0.6*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='B3', fontSize=18, fontName='Helvetica-Bold', textColor=colors.HexColor("#0F1B2D"), spaceAfter=4))
    styles.add(ParagraphStyle(name='S3', fontSize=13, fontName='Helvetica-Bold', textColor=colors.HexColor("#0078D4"), spaceBefore=16, spaceAfter=8))
    styles.add(ParagraphStyle(name='P3', fontSize=10, spaceAfter=6, leading=14))
    styles.add(ParagraphStyle(name='F3', fontSize=7, textColor=colors.gray, alignment=TA_CENTER, spaceBefore=20))

    el = [Paragraph("Alliance Global Tech, Inc.", styles['B3']),
          HRFlowable(width="100%", thickness=2, color=colors.HexColor("#0078D4"), spaceAfter=12)]
    el.append(Paragraph(clean(analysis.get('summary', '')), styles['P3']))
    for line in draft.split('\n'):
        line = line.strip()
        if line:
            el.append(Paragraph(clean(line.lstrip('#').strip()) if line.startswith('#') else clean(line),
                                styles['S3'] if line.startswith('#') else styles['P3']))
    el.append(Spacer(1, 20))
    el.append(Paragraph("Alliance Global Tech, Inc. | SBA 8(a) | CAGE: 8ERE8 | www.agtbi.com", styles['F3']))

    doc.build(el)
    buffer.seek(0)
    return Response(content=buffer.read(), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="AGT-Response.pdf"'})


# ── SAM.GOV SEARCH ──
@router.get("/sam-search")
async def search_sam(keywords: str, user=Depends(get_current_user)):
    sam_key = os.getenv("SAM_GOV_API_KEY", "")
    if sam_key:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get("https://api.sam.gov/opportunities/v2/search",
                    params={"api_key": sam_key, "keyword": keywords, "postedFrom": "01/01/2026", "limit": 10})
                if resp.status_code == 200:
                    data = resp.json()
                    opps = data.get("opportunitiesData", [])
                    return {"source": "SAM.gov API", "count": len(opps), "results": [{
                        "title": o.get("title", ""), "solicitation_number": o.get("solicitationNumber", ""),
                        "agency": o.get("fullParentPathName", ""), "posted_date": o.get("postedDate", ""),
                        "response_deadline": o.get("responseDeadLine", ""), "naics": o.get("naicsCode", ""),
                        "set_aside": o.get("typeOfSetAside", ""),
                        "link": f"https://sam.gov/opp/{o.get('noticeId', '')}/view",
                    } for o in opps[:10]]}
        except Exception as e:
            return {"source": "SAM.gov API", "error": str(e), "count": 0, "results": []}

    return {
        "source": "SAM.gov Search Link", "count": 0, "results": [],
        "search_url": f"https://sam.gov/search?keywords={keywords}&sort=-modifiedDate&index=opp&is_active=true",
        "message": "Add SAM_GOV_API_KEY to Railway for live search. Get free key at https://api.sam.gov"
    }


# ── LABOR CATEGORIES ──
LABOR_CATEGORIES = [
    {"title": "Program Manager", "level": "Senior", "min_rate": 140, "max_rate": 185, "gsa_rate": 165},
    {"title": "Project Manager", "level": "Senior", "min_rate": 125, "max_rate": 170, "gsa_rate": 150},
    {"title": "Senior Solutions Architect", "level": "Senior", "min_rate": 155, "max_rate": 210, "gsa_rate": 185},
    {"title": "Cloud Architect (Azure/AWS)", "level": "Senior", "min_rate": 150, "max_rate": 200, "gsa_rate": 175},
    {"title": "Senior Software Developer", "level": "Senior", "min_rate": 130, "max_rate": 180, "gsa_rate": 160},
    {"title": "Software Developer", "level": "Mid", "min_rate": 95, "max_rate": 140, "gsa_rate": 120},
    {"title": "Junior Developer", "level": "Junior", "min_rate": 65, "max_rate": 95, "gsa_rate": 80},
    {"title": "Data Engineer", "level": "Senior", "min_rate": 140, "max_rate": 190, "gsa_rate": 170},
    {"title": "Data Analyst", "level": "Mid", "min_rate": 90, "max_rate": 130, "gsa_rate": 110},
    {"title": "Business Analyst", "level": "Mid", "min_rate": 85, "max_rate": 125, "gsa_rate": 105},
    {"title": "QA/Test Engineer", "level": "Mid", "min_rate": 85, "max_rate": 125, "gsa_rate": 105},
    {"title": "DevOps Engineer", "level": "Senior", "min_rate": 130, "max_rate": 175, "gsa_rate": 155},
    {"title": "Cybersecurity Analyst", "level": "Senior", "min_rate": 135, "max_rate": 180, "gsa_rate": 160},
    {"title": "Help Desk Tier I", "level": "Junior", "min_rate": 45, "max_rate": 65, "gsa_rate": 55},
    {"title": "Help Desk Tier II", "level": "Mid", "min_rate": 65, "max_rate": 90, "gsa_rate": 75},
    {"title": "System Administrator", "level": "Mid", "min_rate": 90, "max_rate": 130, "gsa_rate": 110},
    {"title": "Database Administrator", "level": "Senior", "min_rate": 120, "max_rate": 165, "gsa_rate": 145},
    {"title": "Network Engineer", "level": "Mid", "min_rate": 95, "max_rate": 140, "gsa_rate": 120},
    {"title": "Technical Writer", "level": "Mid", "min_rate": 70, "max_rate": 100, "gsa_rate": 85},
    {"title": "Scrum Master", "level": "Mid", "min_rate": 110, "max_rate": 150, "gsa_rate": 130},
]

@router.get("/labor-categories")
async def get_labor_categories():
    return {"categories": LABOR_CATEGORIES}
