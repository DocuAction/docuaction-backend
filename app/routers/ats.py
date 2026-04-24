"""
ATS (Applicant Tracking System) — Enterprise Module
Separate from procurement. Role-based access.
Includes: Dashboard, Jobs, Candidates, Pipeline, Bench Sales, Activities
"""
from uuid import UUID
from datetime import datetime, date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import (
    JobPosting, Candidate, Application, BenchCandidate, ATSActivity,
    JobStatus, ApplicationStatus, BenchStatus, User
)
from app.services.auth import get_current_user
import csv
import io

router = APIRouter(prefix="/ats", tags=["ATS"])

# Allowed roles for ATS access
ATS_ROLES = {"Admin", "Manager", "Staffing Manager", "Recruiter", "Sales"}


async def require_ats_access(user=Depends(get_current_user)):
    """Only staffing team can access ATS."""
    if user.role not in ATS_ROLES:
        raise HTTPException(403, "ATS access requires staffing role (Admin, Manager, Recruiter, Sales)")
    return user


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def ats_dashboard(user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    """Executive dashboard with all key metrics."""
    # Jobs
    open_jobs = await db.execute(select(func.count(JobPosting.id)).where(JobPosting.status == JobStatus.OPEN))
    total_jobs = await db.execute(select(func.count(JobPosting.id)))

    # Candidates
    total_candidates = await db.execute(select(func.count(Candidate.id)))

    # Pipeline counts
    pipeline = {}
    for s in ["Applied", "Screening", "Interview", "Submitted to Client", "Offered", "Hired", "Rejected"]:
        c = await db.execute(select(func.count(Application.id)).where(Application.status == s))
        pipeline[s] = c.scalar()

    total_apps = sum(pipeline.values())

    # Bench
    bench_available = await db.execute(select(func.count(BenchCandidate.id)).where(BenchCandidate.status == BenchStatus.AVAILABLE))
    bench_submitted = await db.execute(select(func.count(BenchCandidate.id)).where(BenchCandidate.status == BenchStatus.SUBMITTED))

    # Recent activity
    recent = await db.execute(select(ATSActivity).order_by(desc(ATSActivity.created_at)).limit(10))

    return {
        "jobs": {"open": open_jobs.scalar(), "total": total_jobs.scalar()},
        "candidates": {"total": total_candidates.scalar()},
        "pipeline": pipeline,
        "total_applications": total_apps,
        "bench": {"available": bench_available.scalar(), "submitted": bench_submitted.scalar()},
        "recent_activities": [{
            "id": str(a.id), "type": a.activity_type, "description": a.description,
            "user": a.user_name, "created_at": str(a.created_at) if a.created_at else None,
        } for a in recent.scalars().all()],
        "conversion": {
            "applied_to_interview": round(pipeline.get("Interview", 0) / max(pipeline.get("Applied", 0), 1) * 100, 1),
            "interview_to_offer": round(pipeline.get("Offered", 0) / max(pipeline.get("Interview", 0), 1) * 100, 1),
            "offer_to_hired": round(pipeline.get("Hired", 0) / max(pipeline.get("Offered", 0), 1) * 100, 1),
        }
    }


# ═══════════════════════════════════════════════════════════════════════════
# JOBS
# ═══════════════════════════════════════════════════════════════════════════

class JobCreate(BaseModel):
    title: str
    description: str | None = None
    location: str | None = "Remote"
    employment_type: str | None = "Full-time"
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    clearance_required: str | None = "None"
    skills_required: str | None = None
    contract_name: str | None = None
    assigned_recruiter: str | None = None
    assigned_account_manager: str | None = None
    client_name: str | None = None
    priority: str | None = "Normal"


@router.post("/jobs", status_code=201)
async def create_job(payload: JobCreate, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    job = JobPosting(**{k: v for k, v in payload.model_dump().items() if hasattr(JobPosting, k)})
    db.add(job)
    await db.flush()
    # Log activity
    db.add(ATSActivity(activity_type="Job Created", description=f"Job '{payload.title}' posted", job_id=job.id, user_name=user.full_name))
    await db.flush()
    await db.refresh(job)
    return _job_dict(job)


@router.get("/jobs")
async def list_jobs(status: str | None = None, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    q = select(JobPosting).options(selectinload(JobPosting.applications)).order_by(desc(JobPosting.created_at))
    if status:
        q = q.where(JobPosting.status == status)
    result = await db.execute(q)
    return [_job_dict(j) for j in result.scalars().unique().all()]


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id).options(selectinload(JobPosting.applications)))
    j = result.scalar_one_or_none()
    if not j:
        raise HTTPException(404)
    return _job_dict(j)


@router.patch("/jobs/{job_id}")
async def update_job(job_id: UUID, payload: dict, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    j = result.scalar_one_or_none()
    if not j:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(j, k) and k != 'id':
            setattr(j, k, v)
    await db.flush()
    return {"status": "updated"}


def _job_dict(j):
    app_counts = {}
    if j.applications:
        for a in j.applications:
            app_counts[a.status] = app_counts.get(a.status, 0) + 1
    return {
        "id": str(j.id), "title": j.title, "description": j.description,
        "location": j.location, "employment_type": j.employment_type,
        "salary_min": float(j.salary_min) if j.salary_min else None,
        "salary_max": float(j.salary_max) if j.salary_max else None,
        "clearance_required": j.clearance_required, "skills_required": j.skills_required,
        "contract_name": j.contract_name, "status": j.status,
        "applicant_count": len(j.applications) if j.applications else 0,
        "pipeline": app_counts,
        "created_at": str(j.created_at) if j.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════

class CandidateCreate(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    skills: str | None = None
    years_experience: int | None = None
    clearance_level: str | None = "None"
    source: str | None = "Internal"
    notes: str | None = None


@router.post("/candidates", status_code=201)
async def create_candidate(payload: CandidateCreate, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    # Check duplicate
    existing = await db.execute(select(Candidate).where(func.lower(Candidate.email) == payload.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Candidate with email {payload.email} already exists")
    c = Candidate(**payload.model_dump())
    db.add(c)
    await db.flush()
    db.add(ATSActivity(activity_type="Candidate Added", description=f"{payload.first_name} {payload.last_name} added", candidate_id=c.id, user_name=user.full_name))
    await db.flush()
    await db.refresh(c)
    return _cand_dict(c)


@router.get("/candidates")
async def list_candidates(
    search: str | None = None, clearance: str | None = None,
    skill: str | None = None, min_exp: int | None = None,
    source: str | None = None, page: int = 1, limit: int = 50,
    user=Depends(require_ats_access), db: AsyncSession = Depends(get_db),
):
    q = select(Candidate).options(selectinload(Candidate.applications))
    if search:
        q = q.where(or_(
            Candidate.first_name.ilike(f"%{search}%"), Candidate.last_name.ilike(f"%{search}%"),
            Candidate.email.ilike(f"%{search}%"), Candidate.skills.ilike(f"%{search}%"),
        ))
    if clearance and clearance != 'Any':
        q = q.where(Candidate.clearance_level == clearance)
    if skill:
        q = q.where(Candidate.skills.ilike(f"%{skill}%"))
    if min_exp:
        q = q.where(Candidate.years_experience >= min_exp)
    if source:
        q = q.where(Candidate.source.ilike(f"%{source}%"))

    # Count
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar()

    q = q.order_by(desc(Candidate.created_at)).offset((page - 1) * limit).limit(limit)
    result = await db.execute(q)
    return {
        "candidates": [_cand_dict(c) for c in result.scalars().unique().all()],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
    }


@router.get("/candidates/{cid}")
async def get_candidate(cid: UUID, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Candidate).where(Candidate.id == cid).options(selectinload(Candidate.applications)))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    d = _cand_dict(c)
    d["resume_preview"] = c.resume_text[:2000] if c.resume_text else None
    # Get activities
    acts = await db.execute(select(ATSActivity).where(ATSActivity.candidate_id == cid).order_by(desc(ATSActivity.created_at)).limit(20))
    d["activities"] = [{"type": a.activity_type, "description": a.description, "user": a.user_name, "date": str(a.created_at)} for a in acts.scalars().all()]
    return d


@router.patch("/candidates/{cid}")
async def update_candidate(cid: UUID, payload: dict, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Candidate).where(Candidate.id == cid))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(c, k) and k != 'id':
            setattr(c, k, v)
    await db.flush()
    return {"status": "updated"}


def _cand_dict(c):
    latest = None
    apps = []
    if c.applications:
        for a in c.applications:
            apps.append({"id": str(a.id), "job_id": str(a.job_id), "status": a.status})
            latest = a.status
    return {
        "id": str(c.id), "first_name": c.first_name, "last_name": c.last_name,
        "email": c.email, "phone": c.phone, "location": c.location,
        "linkedin_url": c.linkedin_url, "skills": c.skills,
        "years_experience": c.years_experience, "clearance_level": c.clearance_level,
        "resume_filename": c.resume_filename, "source": c.source, "notes": c.notes,
        "application_count": len(apps), "latest_status": latest,
        "applications": apps,
        "created_at": str(c.created_at) if c.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATIONS / PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/applications", status_code=201)
async def create_application(
    candidate_id: UUID, job_id: UUID,
    user=Depends(require_ats_access), db: AsyncSession = Depends(get_db),
):
    """Submit a candidate to a job."""
    app = Application(candidate_id=candidate_id, job_id=job_id, status=ApplicationStatus.APPLIED)
    db.add(app)
    await db.flush()
    db.add(ATSActivity(activity_type="Application", description=f"Candidate submitted to job", candidate_id=candidate_id, job_id=job_id, user_name=user.full_name))
    await db.flush()
    return {"id": str(app.id), "status": "Applied"}


@router.get("/applications")
async def list_applications(
    status: str | None = None, job_id: UUID | None = None,
    user=Depends(require_ats_access), db: AsyncSession = Depends(get_db),
):
    q = select(Application).options(selectinload(Application.candidate), selectinload(Application.job)).order_by(desc(Application.created_at))
    if status:
        q = q.where(Application.status == status)
    if job_id:
        q = q.where(Application.job_id == job_id)
    result = await db.execute(q)
    return [{
        "id": str(a.id), "candidate_id": str(a.candidate_id), "job_id": str(a.job_id),
        "candidate_name": f"{a.candidate.first_name} {a.candidate.last_name}",
        "candidate_email": a.candidate.email,
        "candidate_clearance": a.candidate.clearance_level,
        "job_title": a.job.title,
        "status": a.status, "notes": a.notes,
        "created_at": str(a.created_at) if a.created_at else None,
    } for a in result.scalars().unique().all()]


@router.patch("/applications/{app_id}/status")
async def update_app_status(app_id: UUID, status: str, notes: str | None = None, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(404)
    old_status = app.status
    app.status = status
    if notes:
        app.notes = (app.notes or "") + f"\n[{datetime.now().strftime('%m/%d %H:%M')}] {notes}"
    db.add(ATSActivity(activity_type="Status Change", description=f"{old_status} → {status}", candidate_id=app.candidate_id, job_id=app.job_id, user_name=user.full_name))
    await db.flush()
    return {"status": status}


# ═══════════════════════════════════════════════════════════════════════════
# BENCH SALES
# ═══════════════════════════════════════════════════════════════════════════

class BenchAdd(BaseModel):
    candidate_id: UUID
    available_date: date | None = None
    desired_rate: Decimal | None = None
    visa_status: str | None = None
    relocation: bool = False
    notes: str | None = None


@router.post("/bench", status_code=201)
async def add_to_bench(payload: BenchAdd, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(BenchCandidate).where(BenchCandidate.candidate_id == payload.candidate_id))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Candidate already on bench")
    bench = BenchCandidate(**payload.model_dump())
    db.add(bench)
    await db.flush()
    return {"id": str(bench.id), "status": "Added to bench"}


@router.get("/bench")
async def list_bench(status: str | None = None, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    q = select(BenchCandidate).order_by(desc(BenchCandidate.created_at))
    if status:
        q = q.where(BenchCandidate.status == status)
    result = await db.execute(q)
    benches = result.scalars().all()

    output = []
    for b in benches:
        cand = await db.execute(select(Candidate).where(Candidate.id == b.candidate_id))
        c = cand.scalar_one_or_none()
        output.append({
            "id": str(b.id), "candidate_id": str(b.candidate_id),
            "name": f"{c.first_name} {c.last_name}" if c else "Unknown",
            "email": c.email if c else "", "phone": c.phone if c else "",
            "skills": c.skills if c else "", "clearance": c.clearance_level if c else "",
            "status": b.status, "available_date": str(b.available_date) if b.available_date else None,
            "desired_rate": float(b.desired_rate) if b.desired_rate else None,
            "visa_status": b.visa_status, "relocation": b.relocation,
            "vendor_submissions": b.vendor_submissions, "notes": b.notes,
        })
    return output


@router.patch("/bench/{bench_id}")
async def update_bench(bench_id: UUID, payload: dict, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BenchCandidate).where(BenchCandidate.id == bench_id))
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(b, k) and k != 'id':
            setattr(b, k, v)
    await db.flush()
    return {"status": "updated"}


# ═══════════════════════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/activities")
async def list_activities(limit: int = 50, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ATSActivity).order_by(desc(ATSActivity.created_at)).limit(limit))
    return [{
        "id": str(a.id), "type": a.activity_type, "description": a.description,
        "user": a.user_name, "created_at": str(a.created_at) if a.created_at else None,
    } for a in result.scalars().all()]


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API (no auth — for agtbi.com)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/public/jobs")
async def public_jobs(db: AsyncSession = Depends(get_db)):
    q = select(JobPosting).where(JobPosting.status == JobStatus.OPEN).order_by(desc(JobPosting.created_at))
    result = await db.execute(q)
    return [{"id": str(j.id), "title": j.title, "description": j.description,
             "location": j.location, "employment_type": j.employment_type,
             "clearance_required": j.clearance_required, "skills_required": j.skills_required,
             } for j in result.scalars().all()]


@router.post("/public/apply")
async def public_apply(
    first_name: str = Form(...), last_name: str = Form(...),
    email: str = Form(...), phone: str = Form(""),
    job_id: str = Form(...), location: str = Form(""),
    skills: str = Form(""), clearance_level: str = Form("None"),
    resume: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one_or_none()
    if not job or job.status != JobStatus.OPEN:
        raise HTTPException(400, "Position not available")

    # Check existing
    existing = await db.execute(select(Candidate).where(func.lower(Candidate.email) == email.lower()))
    candidate = existing.scalar_one_or_none()

    resume_text = resume_filename = None
    if resume:
        content = await resume.read()
        resume_filename = resume.filename
        try:
            from app.services.doc_extract import extract_text
            resume_text = extract_text(content, resume.filename or "resume.pdf")
        except:
            resume_text = "[Uploaded]"

    if candidate:
        if phone: candidate.phone = phone
        if skills: candidate.skills = skills
        if resume_text: candidate.resume_text = resume_text
        if resume_filename: candidate.resume_filename = resume_filename
    else:
        candidate = Candidate(
            first_name=first_name, last_name=last_name, email=email,
            phone=phone or None, location=location or None, skills=skills or None,
            clearance_level=clearance_level or None, resume_text=resume_text,
            resume_filename=resume_filename, source="Website",
        )
        db.add(candidate)
        await db.flush()

    # Check dup application
    dup = await db.execute(select(Application).where(Application.candidate_id == candidate.id, Application.job_id == job.id))
    if dup.scalar_one_or_none():
        return {"status": "already_applied", "message": f"You already applied for {job.title}"}

    app = Application(candidate_id=candidate.id, job_id=job.id, status=ApplicationStatus.APPLIED)
    db.add(app)
    db.add(ATSActivity(activity_type="Website Application", description=f"{first_name} {last_name} applied for {job.title}", candidate_id=candidate.id, job_id=job.id))
    await db.flush()

    return {"status": "success", "message": f"Thank you {first_name}! Application for '{job.title}' received."}


# ═══════════════════════════════════════════════════════════════════════════
# OORWIN IMPORT
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/import/oorwin")
async def import_oorwin(file: UploadFile = File(...), user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    content = await file.read()
    try:
        text = content.decode('utf-8')
    except:
        text = content.decode('latin-1')

    reader = csv.DictReader(io.StringIO(text))
    imported = updated = skipped = 0
    errors = []

    for i, row in enumerate(reader):
        try:
            fn = row.get('First Name', '') or row.get('first_name', '') or ''
            ln = row.get('Last Name', '') or row.get('last_name', '') or ''
            if not fn:
                full = row.get('Candidate Name', '') or row.get('Name', '') or row.get('name', '') or ''
                parts = full.split(None, 1)
                fn = parts[0] if parts else ''
                ln = parts[1] if len(parts) > 1 else ''
            fn, ln = fn.strip(), ln.strip()

            email = (row.get('Email', '') or row.get('email', '') or row.get('Email Address', '')).strip()
            if not fn or not email:
                skipped += 1
                continue

            phone = (row.get('Phone', '') or row.get('Mobile', '') or row.get('phone', '')).strip()
            city = row.get('City', '').strip()
            state = row.get('State', '').strip()
            location = row.get('Location', '').strip() or (f"{city}, {state}" if city else state)
            skills = (row.get('Skills', '') or row.get('Key Skills', '') or row.get('Technical Skills', '')).strip()
            exp_str = (row.get('Experience', '') or row.get('Years of Experience', '')).strip()
            years_exp = None
            if exp_str:
                try: years_exp = int(float(exp_str.replace('+', '').replace('years', '').replace('yrs', '').strip()))
                except: pass

            clearance = (row.get('Clearance', '') or row.get('Security Clearance', '')).strip()
            cl = clearance.lower() if clearance else ''
            if 'ts/sci' in cl: clearance = 'TS/SCI'
            elif 'top secret' in cl: clearance = 'Top Secret'
            elif 'secret' in cl: clearance = 'Secret'
            elif 'public' in cl or 'trust' in cl: clearance = 'Public Trust'
            elif not clearance: clearance = 'None'

            linkedin = (row.get('LinkedIn', '') or row.get('LinkedIn URL', '')).strip()
            source = (row.get('Source', '') or row.get('Candidate Source', '') or 'Oorwin').strip()
            notes_parts = []
            for field in ['Notes', 'Comments', 'Status', 'Title', 'Job Title']:
                v = row.get(field, '').strip()
                if v: notes_parts.append(f"{field}: {v}")
            notes = '. '.join(notes_parts) if notes_parts else None

            existing = await db.execute(select(Candidate).where(func.lower(Candidate.email) == email.lower()))
            cand = existing.scalar_one_or_none()
            if cand:
                if phone and not cand.phone: cand.phone = phone
                if location: cand.location = location
                if skills: cand.skills = skills
                if years_exp: cand.years_experience = years_exp
                if clearance != 'None': cand.clearance_level = clearance
                if linkedin: cand.linkedin_url = linkedin
                if notes: cand.notes = (cand.notes or '') + '\n' + notes
                cand.source = source
                updated += 1
            else:
                db.add(Candidate(
                    first_name=fn, last_name=ln, email=email, phone=phone or None,
                    location=location or None, skills=skills or None, years_experience=years_exp,
                    clearance_level=clearance or None, linkedin_url=linkedin or None,
                    notes=notes, source=source,
                ))
                imported += 1
        except Exception as e:
            errors.append(f"Row {i+1}: {str(e)}")

    await db.flush()
    db.add(ATSActivity(activity_type="Oorwin Import", description=f"Imported {imported} new, updated {updated}", user_name=user.full_name))
    await db.flush()
    return {"imported": imported, "updated": updated, "skipped": skipped, "errors": errors[:10]}


# ═══════════════════════════════════════════════════════════════════════════
# ACCESS CHECK
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/access-check")
async def check_ats_access(user=Depends(get_current_user)):
    """Check if current user has ATS access. Used by frontend to show/hide menu."""
    return {"has_access": user.role in ATS_ROLES, "role": user.role}


# ═══════════════════════════════════════════════════════════════════════════
# SUBMISSIONS TRACKING
# ═══════════════════════════════════════════════════════════════════════════

class SubmissionCreate(BaseModel):
    candidate_id: UUID
    job_id: UUID | None = None
    client_name: str
    vendor_name: str | None = None
    submission_type: str = "Direct"
    bill_rate: Decimal | None = None
    pay_rate: Decimal | None = None
    notes: str | None = None


@router.post("/submissions", status_code=201)
async def create_submission(payload: SubmissionCreate, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    from app.models import Submission, SubmissionStatus
    sub = Submission(
        **payload.model_dump(),
        status=SubmissionStatus.SUBMITTED,
        submitted_by=user.full_name,
    )
    db.add(sub)
    db.add(ATSActivity(activity_type="Submission", description=f"Candidate submitted to {payload.client_name}", candidate_id=payload.candidate_id, job_id=payload.job_id, user_name=user.full_name))
    await db.flush()
    await db.refresh(sub)
    return {"id": str(sub.id), "status": "Submitted"}


@router.get("/submissions")
async def list_submissions(status: str | None = None, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    from app.models import Submission
    q = select(Submission).order_by(desc(Submission.created_at))
    if status:
        q = q.where(Submission.status == status)
    result = await db.execute(q)
    subs = result.scalars().all()

    output = []
    for s in subs:
        cand = await db.execute(select(Candidate).where(Candidate.id == s.candidate_id))
        c = cand.scalar_one_or_none()
        output.append({
            "id": str(s.id), "candidate_id": str(s.candidate_id),
            "candidate_name": f"{c.first_name} {c.last_name}" if c else "Unknown",
            "candidate_email": c.email if c else "",
            "client_name": s.client_name, "vendor_name": s.vendor_name,
            "submission_type": s.submission_type,
            "bill_rate": float(s.bill_rate) if s.bill_rate else None,
            "pay_rate": float(s.pay_rate) if s.pay_rate else None,
            "status": s.status, "submitted_by": s.submitted_by,
            "feedback": s.feedback, "notes": s.notes,
            "interview_date": str(s.interview_date) if s.interview_date else None,
            "created_at": str(s.created_at) if s.created_at else None,
        })
    return output


@router.patch("/submissions/{sub_id}")
async def update_submission(sub_id: UUID, payload: dict, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    from app.models import Submission
    result = await db.execute(select(Submission).where(Submission.id == sub_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404)
    old_status = s.status
    for k, v in payload.items():
        if hasattr(s, k) and k != 'id':
            setattr(s, k, v)
    if payload.get('status') and payload['status'] != old_status:
        db.add(ATSActivity(activity_type="Submission Update", description=f"Submission {old_status} → {payload['status']} for {s.client_name}", candidate_id=s.candidate_id, user_name=user.full_name))
    await db.flush()
    return {"status": "updated"}


# ═══════════════════════════════════════════════════════════════════════════
# REPORTING & KPIs
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/reports/recruiter-performance")
async def recruiter_performance(user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    """Recruiter performance metrics."""
    from app.models import Submission

    # Applications by status
    pipeline = {}
    for s in ["Applied", "Screening", "Interview", "Submitted to Client", "Offered", "Hired", "Rejected"]:
        c = await db.execute(select(func.count(Application.id)).where(Application.status == s))
        pipeline[s] = c.scalar()

    # Submissions by status
    sub_pipeline = {}
    for s in ["Submitted", "Client Review", "Interview Scheduled", "Selected", "Rejected"]:
        c = await db.execute(select(func.count(Submission.id)).where(Submission.status == s))
        sub_pipeline[s] = c.scalar()

    # Monthly stats (last 30 days)
    from datetime import timedelta
    thirty_days = datetime.now() - timedelta(days=30)
    new_candidates = await db.execute(select(func.count(Candidate.id)).where(Candidate.created_at >= thirty_days))
    new_apps = await db.execute(select(func.count(Application.id)).where(Application.created_at >= thirty_days))
    new_subs = await db.execute(select(func.count(Submission.id)).where(Submission.created_at >= thirty_days))

    total_hired = pipeline.get("Hired", 0)
    total_apps = sum(pipeline.values())
    total_subs = sum(sub_pipeline.values())

    return {
        "recruitment_pipeline": pipeline,
        "submission_pipeline": sub_pipeline,
        "last_30_days": {
            "new_candidates": new_candidates.scalar(),
            "new_applications": new_apps.scalar(),
            "new_submissions": new_subs.scalar(),
        },
        "kpis": {
            "total_applications": total_apps,
            "total_submissions": total_subs,
            "total_hired": total_hired,
            "hire_rate": round(total_hired / max(total_apps, 1) * 100, 1),
            "submission_to_interview": round(sub_pipeline.get("Interview Scheduled", 0) / max(total_subs, 1) * 100, 1),
            "submission_to_select": round(sub_pipeline.get("Selected", 0) / max(total_subs, 1) * 100, 1),
        }
    }


@router.get("/reports/sales-performance")
async def sales_performance(user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    """Bench sales performance metrics."""
    from app.models import Submission

    bench_avail = await db.execute(select(func.count(BenchCandidate.id)).where(BenchCandidate.status == BenchStatus.AVAILABLE))
    bench_submitted = await db.execute(select(func.count(BenchCandidate.id)).where(BenchCandidate.status == BenchStatus.SUBMITTED))
    bench_placed = await db.execute(select(func.count(BenchCandidate.id)).where(BenchCandidate.status == BenchStatus.PLACED))

    # Revenue potential from submissions
    subs = await db.execute(select(Submission).where(Submission.bill_rate != None))
    total_potential = sum(float(s.bill_rate or 0) * 2080 for s in subs.scalars().all())

    return {
        "bench": {
            "available": bench_avail.scalar(),
            "submitted": bench_submitted.scalar(),
            "placed": bench_placed.scalar(),
        },
        "revenue_potential_annual": round(total_potential, 2),
    }


# ── Resume AI Parsing ──

@router.post("/candidates/{cid}/parse-resume")
async def parse_resume_ai(cid: UUID, user=Depends(require_ats_access), db: AsyncSession = Depends(get_db)):
    """Use AI to extract skills, experience, and clearance from stored resume text."""
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "ANTHROPIC_API_KEY not set")

    result = await db.execute(select(Candidate).where(Candidate.id == cid))
    c = result.scalar_one_or_none()
    if not c or not c.resume_text:
        raise HTTPException(400, "No resume text available for this candidate")

    from anthropic import Anthropic
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model="claude-sonnet-4-20250514", max_tokens=1000,
        messages=[{"role": "user", "content": f"""Extract from this resume and return ONLY JSON:
{{"skills": "comma-separated skills", "years_experience": number, "clearance": "None/Public Trust/Secret/Top Secret/TS/SCI", "title": "current job title", "summary": "2 sentence summary"}}

Resume:
{c.resume_text[:4000]}"""}]
    )

    import json
    try:
        text = msg.content[0].text
        start = text.find('{')
        end = text.rfind('}') + 1
        parsed = json.loads(text[start:end])

        if parsed.get('skills') and not c.skills:
            c.skills = parsed['skills']
        if parsed.get('years_experience') and not c.years_experience:
            c.years_experience = int(parsed['years_experience'])
        if parsed.get('clearance') and parsed['clearance'] != 'None' and c.clearance_level in (None, 'None'):
            c.clearance_level = parsed['clearance']
        if parsed.get('summary'):
            c.notes = (c.notes or '') + f"\nAI Summary: {parsed['summary']}"

        db.add(ATSActivity(activity_type="AI Parse", description=f"Resume parsed for {c.first_name} {c.last_name}", candidate_id=cid, user_name=user.full_name))
        await db.flush()

        return {"parsed": parsed, "status": "success", "updated_fields": True}
    except Exception as e:
        return {"parsed": None, "raw": msg.content[0].text, "error": str(e)}
