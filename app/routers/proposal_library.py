from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import ProposalLibraryItem, ProposalCategory
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/proposal-library", tags=["Proposal Library"], dependencies=[Depends(require_role("contributor"))])
class LibraryItemCreate(BaseModel):
    title: str
    category: ProposalCategory = ProposalCategory.FULL_PROPOSAL
    agency: str | None = None
    solicitation_number: str | None = None
    contract_type: str | None = None
    naics_code: str | None = None
    keywords: str | None = None
    content: str
    outcome: str | None = None
    notes: str | None = None


class LibraryItemResponse(BaseModel):
    id: UUID
    title: str
    category: ProposalCategory
    agency: str | None
    solicitation_number: str | None
    contract_type: str | None
    naics_code: str | None
    keywords: str | None
    content: str
    outcome: str | None
    file_name: str | None
    notes: str | None
    created_at: str | None = None

    class Config:
        from_attributes = True


@router.post("", response_model=LibraryItemResponse, status_code=201)
async def add_library_item(
    payload: LibraryItemCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = ProposalLibraryItem(**payload.model_dump())
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


@router.post("/upload")
async def upload_proposal_file(
    file: UploadFile = File(...),
    title: str = Form(...),
    category: str = Form("Full Proposal"),
    agency: str = Form(""),
    outcome: str = Form(""),
    keywords: str = Form(""),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a past proposal document (PDF, Word, text)."""
    content_bytes = await file.read()
    if len(content_bytes) > 20_000_000:
        raise HTTPException(400, "File too large. Max 20MB.")

    from app.services.doc_extract import extract_text
    try:
        text = extract_text(content_bytes, file.filename or "file.txt")
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {str(e)}")

    if not text or len(text.strip()) < 20:
        raise HTTPException(400, "Could not extract text from file.")

    # Map category string
    cat_map = {
        "Technical": ProposalCategory.TECHNICAL,
        "Management": ProposalCategory.MANAGEMENT,
        "Past Performance": ProposalCategory.PAST_PERFORMANCE,
        "Pricing": ProposalCategory.PRICING,
        "Compliance": ProposalCategory.COMPLIANCE,
        "Cover Letter": ProposalCategory.COVER_LETTER,
        "Executive Summary": ProposalCategory.EXECUTIVE_SUMMARY,
        "Full Proposal": ProposalCategory.FULL_PROPOSAL,
        "Template": ProposalCategory.TEMPLATE,
    }

    item = ProposalLibraryItem(
        title=title,
        category=cat_map.get(category, ProposalCategory.FULL_PROPOSAL),
        agency=agency or None,
        keywords=keywords or None,
        content=text,
        outcome=outcome or None,
        file_name=file.filename,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)

    return {
        "id": str(item.id),
        "title": item.title,
        "file_name": item.file_name,
        "content_length": len(text),
        "status": "uploaded"
    }


@router.get("", response_model=list[LibraryItemResponse])
async def list_library(
    category: str | None = None,
    search: str | None = None,
    outcome: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(ProposalLibraryItem).order_by(ProposalLibraryItem.created_at.desc())
    if category:
        q = q.where(ProposalLibraryItem.category == category)
    if outcome:
        q = q.where(ProposalLibraryItem.outcome == outcome)
    if search:
        q = q.where(or_(
            ProposalLibraryItem.title.ilike(f"%{search}%"),
            ProposalLibraryItem.keywords.ilike(f"%{search}%"),
            ProposalLibraryItem.agency.ilike(f"%{search}%"),
        ))
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{item_id}", response_model=LibraryItemResponse)
async def get_library_item(item_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProposalLibraryItem).where(ProposalLibraryItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Library item not found")
    return item


@router.delete("/{item_id}")
async def delete_library_item(item_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProposalLibraryItem).where(ProposalLibraryItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404)
    await db.delete(item)
    await db.flush()
    return {"status": "deleted"}


@router.get("/search/relevant")
async def find_relevant_proposals(
    keywords: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Find proposals relevant to given keywords for AI context."""
    terms = keywords.split()
    conditions = []
    for term in terms[:5]:
        conditions.append(ProposalLibraryItem.keywords.ilike(f"%{term}%"))
        conditions.append(ProposalLibraryItem.title.ilike(f"%{term}%"))
        conditions.append(ProposalLibraryItem.content.ilike(f"%{term}%"))

    q = select(ProposalLibraryItem).where(or_(*conditions)).limit(5)
    result = await db.execute(q)
    items = result.scalars().all()

    return [{
        "id": str(i.id),
        "title": i.title,
        "category": i.category,
        "agency": i.agency,
        "outcome": i.outcome,
        "content_preview": i.content[:500] + "..." if len(i.content) > 500 else i.content,
    } for i in items]
