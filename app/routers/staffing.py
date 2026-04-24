"""
Staffing / Applicant Tracking System (ATS)
- Public API: /staffing/apply (for agtbi.com careers page)
- Internal API: candidates, jobs, applications (auth required)
"""
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import (
    JobPosting, Candidate, Application,
    JobStatus, ApplicationStatus
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/staffing", tags=["Staffing / ATS"])


# ── Schemas ──

class JobCreate(BaseModel):
    title: str
    description: str | None = None
    location: str | None = None
    employment_type: str | None = "Full-time"
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    clearance_required: str | None = None
    skills_required: str | None = None
    contract_name: str | None = None


class CandidateCreate(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    skills: str | None = None
    years_experience: int | None = None
    clearance_level: str | None = None
    source: str | None = "Website"


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — No auth required (for agtbi.com integration)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/public/jobs")
async def public_list_jobs(db: AsyncSession = Depends(get_db)):
    """Public endpoint — returns open job postings for careers page."""
    q = select(JobPosting).where(JobPosting.status == JobStatus.OPEN).order_by(JobPosting.created_at.desc())
    result = await db.execute(q)
    jobs = result.scalars().all()
    return [{
        "id": str(j.id), "title": j.title, "description": j.description,
        "location": j.location, "employment_type": j.employment_type,
        "salary_min": float(j.salary_min) if j.salary_min else None,
        "salary_max": float(j.salary_max) if j.salary_max else None,
        "clearance_required": j.clearance_required,
        "skills_required": j.skills_required,
        "contract_name": j.contract_name,
        "posted_date": str(j.created_at) if j.created_at else None,
    } for j in jobs]


@router.get("/public/jobs/{job_id}")
async def public_get_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Public endpoint — get single job details."""
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    j = result.scalar_one_or_none()
    if not j:
        raise HTTPException(404, "Job not found")
    return {
        "id": str(j.id), "title": j.title, "description": j.description,
        "location": j.location, "employment_type": j.employment_type,
        "salary_min": float(j.salary_min) if j.salary_min else None,
        "salary_max": float(j.salary_max) if j.salary_max else None,
        "clearance_required": j.clearance_required,
        "skills_required": j.skills_required,
    }


@router.post("/public/apply")
async def public_apply(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    job_id: str = Form(...),
    location: str = Form(""),
    linkedin_url: str = Form(""),
    skills: str = Form(""),
    years_experience: int = Form(0),
    clearance_level: str = Form("None"),
    resume: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
):
    """
    PUBLIC endpoint — receives applications from agtbi.com careers page.
    No authentication required. Creates candidate + application.

    Usage from agtbi.com:
    ```html
    <form action="https://govcon-platform-production.up.railway.app/api/staffing/public/apply"
          method="POST" enctype="multipart/form-data">
      <input name="first_name" required>
      <input name="last_name" required>
      <input name="email" type="email" required>
      <input name="phone">
      <input name="job_id" type="hidden" value="JOB_UUID_HERE">
      <input name="resume" type="file" accept=".pdf,.docx,.doc">
      <button type="submit">Apply Now</button>
    </form>
    ```
    """
    # Validate job exists
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job posting not found")
    if job.status != JobStatus.OPEN:
        raise HTTPException(400, "This position is no longer accepting applications")

    # Check for existing candidate by email
    existing = await db.execute(select(Candidate).where(func.lower(Candidate.email) == email.lower()))
    candidate = existing.scalar_one_or_none()

    resume_text = None
    resume_filename = None
    if resume:
        if resume.size and resume.size > 10_000_000:
            raise HTTPException(400, "Resume too large (max 10MB)")
        content = await resume.read()
        resume_filename = resume.filename
        # Extract text from resume
        try:
            from app.services.doc_extract import extract_text
            resume_text = extract_text(content, resume.filename or "resume.pdf")
        except:
            resume_text = "[Resume uploaded but text extraction failed]"

    if candidate:
        # Update existing candidate
        if phone: candidate.phone = phone
        if location: candidate.location = location
        if linkedin_url: candidate.linkedin_url = linkedin_url
        if skills: candidate.skills = skills
        if years_experience: candidate.years_experience = years_experience
        if clearance_level: candidate.clearance_level = clearance_level
        if resume_text: candidate.resume_text = resume_text
        if resume_filename: candidate.resume_filename = resume_filename
    else:
        # Create new candidate
        candidate = Candidate(
            first_name=first_name, last_name=last_name, email=email,
            phone=phone or None, location=location or None,
            linkedin_url=linkedin_url or None, skills=skills or None,
            years_experience=years_experience, clearance_level=clearance_level or None,
            resume_text=resume_text, resume_filename=resume_filename,
            source="Website",
        )
        db.add(candidate)
        await db.flush()

    # Check if already applied to this job
    existing_app = await db.execute(
        select(Application).where(Application.candidate_id == candidate.id, Application.job_id == job.id)
    )
    if existing_app.scalar_one_or_none():
        return {"status": "already_applied", "message": f"You have already applied for {job.title}. We will review your application."}

    # Create application
    application = Application(
        candidate_id=candidate.id, job_id=job.id,
        status=ApplicationStatus.APPLIED,
    )
    db.add(application)
    await db.flush()

    return {
        "status": "success",
        "message": f"Thank you {first_name}! Your application for '{job.title}' has been received. We will review and contact you soon.",
        "application_id": str(application.id),
    }


# ═══════════════════════════════════════════════════════════════════════════
# INTERNAL API — Auth required
# ═══════════════════════════════════════════════════════════════════════════

# ── Jobs ──

@router.post("/jobs", status_code=201)
async def create_job(payload: JobCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    job = JobPosting(**payload.model_dump())
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return _job_dict(job)


@router.get("/jobs")
async def list_jobs(status: str | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(JobPosting).options(selectinload(JobPosting.applications)).order_by(JobPosting.created_at.desc())
    if status:
        q = q.where(JobPosting.status == status)
    result = await db.execute(q)
    return [_job_dict(j) for j in result.scalars().unique().all()]


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id).options(selectinload(JobPosting.applications)))
    j = result.scalar_one_or_none()
    if not j:
        raise HTTPException(404)
    return _job_dict(j)


@router.patch("/jobs/{job_id}")
async def update_job(job_id: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    j = result.scalar_one_or_none()
    if not j:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(j, k) and k != 'id':
            setattr(j, k, v)
    await db.flush()
    await db.refresh(j)
    return _job_dict(j)


def _job_dict(j):
    return {
        "id": str(j.id), "title": j.title, "description": j.description,
        "location": j.location, "employment_type": j.employment_type,
        "salary_min": float(j.salary_min) if j.salary_min else None,
        "salary_max": float(j.salary_max) if j.salary_max else None,
        "clearance_required": j.clearance_required, "skills_required": j.skills_required,
        "contract_name": j.contract_name, "status": j.status,
        "applicant_count": len(j.applications) if j.applications else 0,
        "created_at": str(j.created_at) if j.created_at else None,
    }


# ── Candidates ──

@router.post("/candidates", status_code=201)
async def create_candidate(payload: CandidateCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    candidate = Candidate(**payload.model_dump())
    db.add(candidate)
    await db.flush()
    await db.refresh(candidate)
    return _candidate_dict(candidate)


@router.get("/candidates")
async def list_candidates(
    search: str | None = None,
    clearance: str | None = None,
    skill: str | None = None,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    q = select(Candidate).options(selectinload(Candidate.applications)).order_by(Candidate.created_at.desc())
    if search:
        q = q.where(or_(
            Candidate.first_name.ilike(f"%{search}%"),
            Candidate.last_name.ilike(f"%{search}%"),
            Candidate.email.ilike(f"%{search}%"),
            Candidate.skills.ilike(f"%{search}%"),
        ))
    if clearance:
        q = q.where(Candidate.clearance_level == clearance)
    if skill:
        q = q.where(Candidate.skills.ilike(f"%{skill}%"))
    result = await db.execute(q)
    return [_candidate_dict(c) for c in result.scalars().unique().all()]


@router.get("/candidates/{cid}")
async def get_candidate(cid: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Candidate).where(Candidate.id == cid).options(selectinload(Candidate.applications))
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    d = _candidate_dict(c)
    d["resume_text"] = c.resume_text[:2000] if c.resume_text else None
    return d


def _candidate_dict(c):
    latest_status = None
    if c.applications:
        latest_status = c.applications[-1].status
    return {
        "id": str(c.id), "first_name": c.first_name, "last_name": c.last_name,
        "email": c.email, "phone": c.phone, "location": c.location,
        "linkedin_url": c.linkedin_url, "skills": c.skills,
        "years_experience": c.years_experience, "clearance_level": c.clearance_level,
        "resume_filename": c.resume_filename, "source": c.source,
        "application_count": len(c.applications) if c.applications else 0,
        "latest_status": latest_status,
        "created_at": str(c.created_at) if c.created_at else None,
    }


# ── Applications / Pipeline ──

@router.get("/applications")
async def list_applications(
    status: str | None = None,
    job_id: UUID | None = None,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    q = select(Application).options(
        selectinload(Application.candidate), selectinload(Application.job)
    ).order_by(Application.created_at.desc())
    if status:
        q = q.where(Application.status == status)
    if job_id:
        q = q.where(Application.job_id == job_id)
    result = await db.execute(q)
    return [{
        "id": str(a.id),
        "candidate_name": f"{a.candidate.first_name} {a.candidate.last_name}",
        "candidate_email": a.candidate.email,
        "candidate_id": str(a.candidate_id),
        "job_title": a.job.title,
        "job_id": str(a.job_id),
        "status": a.status,
        "notes": a.notes,
        "created_at": str(a.created_at) if a.created_at else None,
    } for a in result.scalars().unique().all()]


@router.patch("/applications/{app_id}/status")
async def update_application_status(
    app_id: UUID,
    status: str,
    notes: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(404)
    app.status = status
    if notes:
        app.notes = (app.notes or "") + f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {status}: {notes}"
    await db.flush()
    return {"status": app.status, "id": str(app.id)}


# ── Pipeline Stats ──

@router.get("/pipeline")
async def pipeline_stats(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get hiring pipeline counts by status."""
    statuses = ["Applied", "Screening", "Interview", "Submitted to Client", "Offered", "Hired", "Rejected"]
    pipeline = {}
    for s in statuses:
        count = await db.execute(select(func.count(Application.id)).where(Application.status == s))
        pipeline[s] = count.scalar()

    total_candidates = await db.execute(select(func.count(Candidate.id)))
    open_jobs = await db.execute(select(func.count(JobPosting.id)).where(JobPosting.status == JobStatus.OPEN))

    return {
        "pipeline": pipeline,
        "total_candidates": total_candidates.scalar(),
        "open_jobs": open_jobs.scalar(),
    }

# ═══════════════════════════════════════════════════════════════════════════
# OORWIN DATA MIGRATION + ADVANCED SEARCH
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/import/oorwin")
async def import_oorwin_csv(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Import candidates from Oorwin ATS CSV export.
    Oorwin typical columns: First Name, Last Name, Email, Phone, Location,
    Skills, Experience, Clearance, LinkedIn, Status, Source, Notes.
    Also handles: Candidate Name (split), Mobile, City, State.
    Deduplicates by email — updates existing, inserts new.
    """
    import csv
    import io

    content = await file.read()
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        text = content.decode('latin-1')

    reader = csv.DictReader(io.StringIO(text))
    imported = 0
    updated = 0
    skipped = 0
    errors = []

    for i, row in enumerate(reader):
        try:
            # Extract name — handle "Candidate Name" (full) or First/Last separate
            first_name = (
                row.get('First Name', '').strip() or
                row.get('first_name', '').strip() or
                row.get('FirstName', '').strip()
            )
            last_name = (
                row.get('Last Name', '').strip() or
                row.get('last_name', '').strip() or
                row.get('LastName', '').strip()
            )

            # If full name in single field
            if not first_name:
                full_name = (
                    row.get('Candidate Name', '').strip() or
                    row.get('Name', '').strip() or
                    row.get('name', '').strip() or
                    row.get('Full Name', '').strip()
                )
                if full_name:
                    parts = full_name.split(None, 1)
                    first_name = parts[0] if parts else ''
                    last_name = parts[1] if len(parts) > 1 else ''

            if not first_name:
                skipped += 1
                continue

            # Email
            email = (
                row.get('Email', '').strip() or
                row.get('email', '').strip() or
                row.get('Email Address', '').strip() or
                row.get('EmailAddress', '').strip()
            )
            if not email:
                skipped += 1
                continue

            # Phone
            phone = (
                row.get('Phone', '').strip() or
                row.get('phone', '').strip() or
                row.get('Mobile', '').strip() or
                row.get('Cell Phone', '').strip() or
                row.get('Phone Number', '').strip()
            )

            # Location
            city = row.get('City', '').strip() or row.get('city', '').strip()
            state = row.get('State', '').strip() or row.get('state', '').strip()
            location = (
                row.get('Location', '').strip() or
                row.get('location', '').strip() or
                (f"{city}, {state}" if city and state else city or state or '')
            )

            # Skills
            skills = (
                row.get('Skills', '').strip() or
                row.get('skills', '').strip() or
                row.get('Key Skills', '').strip() or
                row.get('Technical Skills', '').strip() or
                row.get('Skill Set', '').strip()
            )

            # Experience
            exp_str = (
                row.get('Experience', '').strip() or
                row.get('experience', '').strip() or
                row.get('Years of Experience', '').strip() or
                row.get('Total Experience', '').strip() or
                row.get('YearsExperience', '').strip()
            )
            years_exp = None
            if exp_str:
                try:
                    years_exp = int(float(exp_str.replace('+', '').replace('years', '').replace('yrs', '').strip()))
                except:
                    pass

            # Clearance
            clearance = (
                row.get('Clearance', '').strip() or
                row.get('clearance', '').strip() or
                row.get('Security Clearance', '').strip() or
                row.get('Clearance Level', '').strip()
            )
            if clearance and clearance not in ('None', 'Public Trust', 'Secret', 'Top Secret', 'TS/SCI'):
                cl = clearance.lower()
                if 'ts/sci' in cl or 'ts_sci' in cl:
                    clearance = 'TS/SCI'
                elif 'top secret' in cl:
                    clearance = 'Top Secret'
                elif 'secret' in cl:
                    clearance = 'Secret'
                elif 'public' in cl or 'trust' in cl:
                    clearance = 'Public Trust'
                else:
                    clearance = 'None'

            # LinkedIn
            linkedin = (
                row.get('LinkedIn', '').strip() or
                row.get('linkedin', '').strip() or
                row.get('LinkedIn URL', '').strip() or
                row.get('LinkedIn Profile', '').strip()
            )

            # Source
            source = (
                row.get('Source', '').strip() or
                row.get('source', '').strip() or
                row.get('Candidate Source', '').strip() or
                'Oorwin Import'
            )

            # Notes
            notes = (
                row.get('Notes', '').strip() or
                row.get('notes', '').strip() or
                row.get('Comments', '').strip() or
                row.get('Remarks', '').strip()
            )

            # Status from Oorwin
            oorwin_status = (
                row.get('Status', '').strip() or
                row.get('status', '').strip() or
                row.get('Candidate Status', '').strip()
            )
            if oorwin_status and notes:
                notes = f"Oorwin Status: {oorwin_status}. {notes}"
            elif oorwin_status:
                notes = f"Oorwin Status: {oorwin_status}"

            # Title/Position
            title = (
                row.get('Title', '').strip() or
                row.get('Job Title', '').strip() or
                row.get('Current Title', '').strip() or
                row.get('Position', '').strip()
            )
            if title and notes:
                notes = f"Title: {title}. {notes}"
            elif title:
                notes = f"Title: {title}"

            # Check for duplicate by email
            existing = await db.execute(
                select(Candidate).where(func.lower(Candidate.email) == email.lower())
            )
            candidate = existing.scalar_one_or_none()

            if candidate:
                # Update existing
                if phone and not candidate.phone: candidate.phone = phone
                if location and not candidate.location: candidate.location = location
                if skills: candidate.skills = skills
                if years_exp and not candidate.years_experience: candidate.years_experience = years_exp
                if clearance and clearance != 'None': candidate.clearance_level = clearance
                if linkedin and not candidate.linkedin_url: candidate.linkedin_url = linkedin
                if notes: candidate.notes = (candidate.notes or '') + '\n' + notes
                if source: candidate.source = source
                updated += 1
            else:
                # Insert new
                candidate = Candidate(
                    first_name=first_name, last_name=last_name or '', email=email,
                    phone=phone or None, location=location or None,
                    skills=skills or None, years_experience=years_exp,
                    clearance_level=clearance or None, linkedin_url=linkedin or None,
                    notes=notes or None, source=source,
                )
                db.add(candidate)
                imported += 1

        except Exception as e:
            errors.append(f"Row {i+1}: {str(e)}")

    await db.flush()

    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:10],
        "total_processed": imported + updated + skipped,
        "source": "Oorwin CSV",
    }


# ── Advanced Candidate Search ──

@router.get("/search/advanced")
async def advanced_search(
    q: str | None = None,
    clearance: str | None = None,
    skill: str | None = None,
    min_experience: int | None = None,
    location: str | None = None,
    source: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Advanced candidate search with multiple filters.
    Used for finding candidates for specific positions.
    """
    query = select(Candidate).options(selectinload(Candidate.applications))

    if q:
        query = query.where(or_(
            Candidate.first_name.ilike(f"%{q}%"),
            Candidate.last_name.ilike(f"%{q}%"),
            Candidate.email.ilike(f"%{q}%"),
            Candidate.skills.ilike(f"%{q}%"),
            Candidate.notes.ilike(f"%{q}%"),
        ))
    if clearance and clearance != 'Any':
        query = query.where(Candidate.clearance_level == clearance)
    if skill:
        query = query.where(Candidate.skills.ilike(f"%{skill}%"))
    if min_experience:
        query = query.where(Candidate.years_experience >= min_experience)
    if location:
        query = query.where(Candidate.location.ilike(f"%{location}%"))
    if source:
        query = query.where(Candidate.source.ilike(f"%{source}%"))

    query = query.order_by(Candidate.created_at.desc()).limit(100)
    result = await db.execute(query)

    return [_candidate_dict(c) for c in result.scalars().unique().all()]


# ── Stats ──

@router.get("/stats")
async def staffing_stats(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(Candidate.id)))
    by_clearance = await db.execute(
        select(Candidate.clearance_level, func.count(Candidate.id))
        .group_by(Candidate.clearance_level)
    )
    by_source = await db.execute(
        select(Candidate.source, func.count(Candidate.id))
        .group_by(Candidate.source)
    )
    return {
        "total_candidates": total.scalar(),
        "by_clearance": {row[0] or 'Unknown': row[1] for row in by_clearance.all()},
        "by_source": {row[0] or 'Unknown': row[1] for row in by_source.all()},
    }
