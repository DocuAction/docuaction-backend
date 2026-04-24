"""
Bench Sales Command Center
Discovery, outreach generation, submission pipeline, automated follow-ups.
Isolated to ATS data only — no procurement access.
"""
import os
import json
from uuid import UUID
from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import (
    Candidate, JobPosting, JobStatus, BenchCandidate, BenchStatus,
    Submission, SubmissionStatus, OutreachLog, OutreachStatus,
    FollowUpQueue, FollowUpStatus, ATSActivity
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/ats/bench", tags=["Bench Sales"])
ATS_ROLES = {"Admin", "Manager", "Staffing Manager", "Recruiter", "Sales"}

VALID_TRANSITIONS = {
    "Submitted": ["Interview Scheduled", "Client Review", "Rejected", "No Response"],
    "Client Review": ["Interview Scheduled", "Rejected", "No Response"],
    "Interview Scheduled": ["Feedback Pending", "Selected", "Rejected"],
    "Feedback Pending": ["Selected", "Rejected"],
    "Selected": [],
    "Rejected": [],
}


async def require_ats(user=Depends(get_current_user)):
    if user.role not in ATS_ROLES:
        raise HTTPException(403, "Bench Sales requires staffing role")
    return user


def _claude(system: str, prompt: str, tokens: int = 2000) -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured")
    from anthropic import Anthropic
    return Anthropic(api_key=key).messages.create(
        model="claude-sonnet-4-20250514", max_tokens=tokens,
        system=system, messages=[{"role": "user", "content": prompt}],
    ).content[0].text


def _json(text: str) -> dict:
    try:
        s, e = text.find('{'), text.rfind('}') + 1
        if s >= 0 and e > s:
            return json.loads(text[s:e])
    except:
        pass
    return {}


# ══════════════════════════════════════════════════════════════
# DASHBOARD KPIs
# ══════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def bench_dashboard(user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """KPIs, pipeline counts, recent activity."""
    # Bench candidates
    bench_total = await db.execute(select(func.count(BenchCandidate.id)))
    bench_avail = await db.execute(select(func.count(BenchCandidate.id)).where(BenchCandidate.status == BenchStatus.AVAILABLE))

    # Submission pipeline
    pipeline = {}
    for s in ["Submitted", "Client Review", "Interview Scheduled", "Feedback Pending", "Selected", "Rejected"]:
        c = await db.execute(select(func.count(Submission.id)).where(Submission.status == s))
        pipeline[s] = c.scalar()

    total_subs = sum(pipeline.values())
    interviews = pipeline.get("Interview Scheduled", 0) + pipeline.get("Feedback Pending", 0)
    responses = interviews + pipeline.get("Selected", 0) + pipeline.get("Rejected", 0)
    response_rate = round(responses / max(total_subs, 1) * 100, 1)

    # Pending follow-ups
    pending_fups = await db.execute(select(func.count(FollowUpQueue.id)).where(FollowUpQueue.status == FollowUpStatus.PENDING))

    # Outreach stats
    total_outreach = await db.execute(select(func.count(OutreachLog.id)))
    drafts = await db.execute(select(func.count(OutreachLog.id)).where(OutreachLog.status == OutreachStatus.DRAFT))

    # Recent activity
    recent_subs = await db.execute(select(Submission).order_by(desc(Submission.created_at)).limit(5))
    recent_outreach = await db.execute(select(OutreachLog).order_by(desc(OutreachLog.created_at)).limit(5))

    sub_list = []
    for s in recent_subs.scalars().all():
        c = await db.execute(select(Candidate).where(Candidate.id == s.candidate_id))
        cand = c.scalar_one_or_none()
        sub_list.append({
            "id": str(s.id), "candidate": f"{cand.first_name} {cand.last_name}" if cand else "Unknown",
            "client": s.client_name, "status": str(s.status),
            "bill_rate": float(s.bill_rate) if s.bill_rate else None,
            "date": str(s.created_at)[:10] if s.created_at else None,
        })

    out_list = []
    for o in recent_outreach.scalars().all():
        c = await db.execute(select(Candidate).where(Candidate.id == o.candidate_id))
        cand = c.scalar_one_or_none()
        out_list.append({
            "id": str(o.id), "candidate": f"{cand.first_name} {cand.last_name}" if cand else "Unknown",
            "company": o.target_company, "status": str(o.status),
            "date": str(o.created_at)[:10] if o.created_at else None,
        })

    return {
        "kpis": {
            "bench_total": bench_total.scalar(),
            "bench_available": bench_avail.scalar(),
            "submissions_sent": total_subs,
            "interviews": interviews,
            "response_rate": response_rate,
            "pending_follow_ups": pending_fups.scalar(),
            "placed": pipeline.get("Selected", 0),
        },
        "pipeline": pipeline,
        "outreach": {"total": total_outreach.scalar(), "drafts": drafts.scalar()},
        "recent_submissions": sub_list,
        "recent_outreach": out_list,
    }


# ══════════════════════════════════════════════════════════════
# DISCOVERY — AI-powered opportunity matching
# ══════════════════════════════════════════════════════════════

@router.post("/discovery")
async def discovery(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Analyze bench candidates, match to jobs, find shared opportunities."""
    candidate_ids = payload.get('candidate_ids', [])
    if not candidate_ids:
        raise HTTPException(400, "Provide candidate_ids[]")

    # Load candidates (ATS data only)
    cands = []
    for cid in candidate_ids[:10]:
        r = await db.execute(select(Candidate).where(Candidate.id == cid))
        c = r.scalar_one_or_none()
        if c:
            cands.append({
                "id": str(c.id), "name": f"{c.first_name} {c.last_name}",
                "skills": c.skills or '', "experience": c.years_experience or 0,
                "clearance": c.clearance_level or 'None', "location": c.location or '',
            })
    if not cands:
        raise HTTPException(400, "No valid candidates found")

    # Load open jobs (ATS data only)
    jobs_r = await db.execute(select(JobPosting).where(JobPosting.status == JobStatus.OPEN))
    jobs = [{"title": j.title, "skills": j.skills_required, "clearance": j.clearance_required, "location": j.location, "contract": j.contract_name} for j in jobs_r.scalars().all()]

    cands_text = "\n".join([f"CANDIDATE: {c['name']} | Skills: {c['skills']} | Exp: {c['experience']}yr | Clearance: {c['clearance']} | Loc: {c['location']}" for c in cands])
    jobs_text = "\n".join([f"JOB: {j['title']} | Skills: {j['skills']} | Clearance: {j['clearance']}" for j in jobs]) or "No internal jobs posted."

    system = """You are a bench sales strategist for a federal IT staffing firm. Analyze candidates and find opportunities.
Return ONLY JSON:
{
  "candidates": [
    {"name": "...", "top_opportunities": [{"type": "internal_job|external_target", "title": "...", "company": "...", "match_score": 85, "rationale": "..."}], "priority": "HIGH|MEDIUM|LOW", "priority_reason": "..."}
  ],
  "shared_opportunities": [{"title": "...", "candidates": ["name1", "name2"], "rationale": "Both have AWS + clearance"}],
  "market_insights": "Brief paragraph on market demand for these skill profiles"
}
Focus on federal IT contracting market. Suggest real companies (Booz Allen, Leidos, SAIC, Peraton, CGI, etc)."""

    try:
        result = _json(_claude(system, f"Candidates:\n{cands_text}\n\nInternal Jobs:\n{jobs_text}", 3000))
    except Exception as e:
        raise HTTPException(500, f"Discovery failed: {e}")

    result['candidates_analyzed'] = len(cands)
    result['internal_jobs_checked'] = len(jobs)
    return result


# ══════════════════════════════════════════════════════════════
# OUTREACH — Generate and store email drafts
# ══════════════════════════════════════════════════════════════

@router.post("/outreach")
async def generate_outreach(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Generate gov-contractor pitch emails for bench candidates."""
    candidate_ids = payload.get('candidate_ids', [])
    target_companies = payload.get('target_companies', [])
    if not candidate_ids:
        raise HTTPException(400, "Provide candidate_ids[]")

    cands = []
    for cid in candidate_ids[:5]:
        r = await db.execute(select(Candidate).where(Candidate.id == cid))
        c = r.scalar_one_or_none()
        if c:
            cands.append({"id": str(c.id), "name": f"{c.first_name} {c.last_name}", "skills": c.skills or '', "experience": c.years_experience or 0, "clearance": c.clearance_level or 'None', "location": c.location or ''})

    if not cands:
        raise HTTPException(400, "No valid candidates")

    targets = target_companies or ["Booz Allen Hamilton", "Leidos", "SAIC", "Peraton", "CGI Federal"]
    cands_text = "\n".join([f"- {c['name']}: {c['skills']}, {c['experience']}yr exp, {c['clearance']} clearance, {c['location']}" for c in cands])

    system = """You are a staffing sales professional at Alliance Global Tech (AGT), an SBA 8(a) federal IT firm.
Write professional outreach emails to government contractors about available bench candidates.

Return ONLY JSON:
{"emails": [{"target_company": "...", "subject": "Available IT Talent — [Skills] — [Clearance]", "body": "Professional 8-12 line email. Include: AGT intro, candidate summary (no full name — use 'our consultant'), key skills, clearance, availability, call-to-action. Sign as AGT Staffing Team."}]}

Tone: Professional, concise, value-focused. Mention 8(a) certification as a differentiator."""

    prompt = f"Generate outreach emails to these companies: {', '.join(targets)}\n\nAvailable candidates:\n{cands_text}"

    try:
        result = _json(_claude(system, prompt, 3000))
    except Exception as e:
        raise HTTPException(500, f"Outreach generation failed: {e}")

    # Save all drafts to outreach_logs
    saved = []
    for email in result.get('emails', []):
        for cand in cands:
            log = OutreachLog(
                candidate_id=cand['id'],
                target_company=email.get('target_company', ''),
                subject=email.get('subject', ''),
                email_content=email.get('body', ''),
                status=OutreachStatus.DRAFT,
                sent_by=user.full_name,
            )
            db.add(log)
            await db.flush()
            saved.append({"id": str(log.id), "company": email.get('target_company'), "candidate": cand['name']})

    db.add(ATSActivity(activity_type="Outreach Generated", description=f"Generated {len(saved)} outreach emails for {len(cands)} candidates", user_name=user.full_name))
    await db.flush()

    return {"emails": result.get('emails', []), "saved_drafts": saved, "count": len(saved)}


# ══════════════════════════════════════════════════════════════
# OUTREACH CRUD
# ══════════════════════════════════════════════════════════════

@router.get("/outreach")
async def list_outreach(status: str | None = None, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    q = select(OutreachLog).order_by(desc(OutreachLog.created_at)).limit(50)
    if status:
        q = q.where(OutreachLog.status == status)
    r = await db.execute(q)
    results = []
    for o in r.scalars().all():
        c = await db.execute(select(Candidate).where(Candidate.id == o.candidate_id))
        cand = c.scalar_one_or_none()
        results.append({
            "id": str(o.id), "candidate": f"{cand.first_name} {cand.last_name}" if cand else "Unknown",
            "company": o.target_company, "subject": o.subject,
            "content": o.email_content[:200], "status": str(o.status),
            "sent_by": o.sent_by, "date": str(o.created_at)[:16] if o.created_at else None,
        })
    return results


@router.patch("/outreach/{log_id}")
async def update_outreach(log_id: UUID, payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(OutreachLog).where(OutreachLog.id == log_id))
    log = r.scalar_one_or_none()
    if not log:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(log, k) and k != 'id':
            setattr(log, k, v)
    if payload.get('status') == 'sent' and not log.sent_at:
        log.sent_at = datetime.now()
    await db.flush()
    return {"status": "updated"}


# ══════════════════════════════════════════════════════════════
# SUBMISSION PIPELINE (with status transition validation)
# ══════════════════════════════════════════════════════════════

@router.get("/submissions")
async def list_bench_submissions(status: str | None = None, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    q = select(Submission).order_by(desc(Submission.created_at)).limit(100)
    if status:
        q = q.where(Submission.status == status)
    r = await db.execute(q)
    results = []
    for s in r.scalars().all():
        c = await db.execute(select(Candidate).where(Candidate.id == s.candidate_id))
        cand = c.scalar_one_or_none()
        results.append({
            "id": str(s.id), "candidate_id": str(s.candidate_id),
            "candidate": f"{cand.first_name} {cand.last_name}" if cand else "Unknown",
            "client": s.client_name, "vendor": s.vendor_name,
            "bill_rate": float(s.bill_rate) if s.bill_rate else None,
            "pay_rate": float(s.pay_rate) if s.pay_rate else None,
            "status": str(s.status), "feedback": s.feedback,
            "date": str(s.created_at)[:10] if s.created_at else None,
        })
    return results


@router.patch("/submissions/{sub_id}/status")
async def update_submission_status(sub_id: UUID, payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Update submission status with transition validation."""
    new_status = payload.get('status')
    if not new_status:
        raise HTTPException(400, "status required")

    r = await db.execute(select(Submission).where(Submission.id == sub_id))
    sub = r.scalar_one_or_none()
    if not sub:
        raise HTTPException(404)

    current = str(sub.status)
    allowed = VALID_TRANSITIONS.get(current, [])
    if new_status not in allowed and new_status != current:
        raise HTTPException(400, f"Invalid transition: {current} → {new_status}. Allowed: {allowed}")

    old = sub.status
    sub.status = new_status
    if payload.get('feedback'):
        sub.feedback = (sub.feedback or '') + f"\n[{datetime.now().strftime('%m/%d %H:%M')}] {payload['feedback']}"

    # Log activity
    c = await db.execute(select(Candidate).where(Candidate.id == sub.candidate_id))
    cand = c.scalar_one_or_none()
    db.add(ATSActivity(
        activity_type="Status Change",
        description=f"{cand.first_name if cand else 'Candidate'}: {old} → {new_status} at {sub.client_name}",
        candidate_id=sub.candidate_id, user_name=user.full_name,
    ))
    await db.flush()
    return {"status": new_status, "previous": str(old)}


# ══════════════════════════════════════════════════════════════
# FOLLOW-UP QUEUE & REMINDERS
# ══════════════════════════════════════════════════════════════

@router.get("/reminders")
async def generate_reminders(user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Check for stale submissions (48hrs no update) and create follow-ups."""
    cutoff = datetime.now() - timedelta(hours=48)
    stale = await db.execute(
        select(Submission).where(
            Submission.status == SubmissionStatus.SUBMITTED,
            Submission.created_at < cutoff,
        )
    )

    created = 0
    for sub in stale.scalars().all():
        # Check if follow-up already exists
        existing = await db.execute(
            select(FollowUpQueue).where(
                FollowUpQueue.submission_id == sub.id,
                FollowUpQueue.status == FollowUpStatus.PENDING,
            )
        )
        if existing.scalar_one_or_none():
            continue

        c = await db.execute(select(Candidate).where(Candidate.id == sub.candidate_id))
        cand = c.scalar_one_or_none()

        fup = FollowUpQueue(
            submission_id=sub.id,
            candidate_name=f"{cand.first_name} {cand.last_name}" if cand else "Unknown",
            target_company=sub.client_name,
            next_follow_up_date=date.today(),
        )
        db.add(fup)
        created += 1

    await db.flush()
    return {"created": created, "message": f"Generated {created} follow-up reminders"}


@router.get("/follow-ups")
async def list_follow_ups(user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(FollowUpQueue).where(FollowUpQueue.status == FollowUpStatus.PENDING)
        .order_by(FollowUpQueue.next_follow_up_date)
    )
    return [{
        "id": str(f.id), "submission_id": str(f.submission_id),
        "candidate": f.candidate_name, "company": f.target_company,
        "follow_up_date": str(f.next_follow_up_date),
        "status": str(f.status),
    } for f in r.scalars().all()]


@router.patch("/follow-ups/{fup_id}")
async def complete_follow_up(fup_id: UUID, payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(FollowUpQueue).where(FollowUpQueue.id == fup_id))
    fup = r.scalar_one_or_none()
    if not fup:
        raise HTTPException(404)
    fup.status = payload.get('status', 'completed')
    if payload.get('notes'):
        fup.notes = payload['notes']
    await db.flush()
    return {"status": "updated"}


# ══════════════════════════════════════════════════════════════
# PIPELINE VIEW (for Kanban board)
# ══════════════════════════════════════════════════════════════

@router.get("/pipeline")
async def pipeline_view(user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Get all submissions grouped by status for Kanban board."""
    columns = {
        "Submitted": [], "Client Review": [], "Interview Scheduled": [],
        "Feedback Pending": [], "Selected": [], "Rejected": [],
    }
    r = await db.execute(select(Submission).order_by(desc(Submission.created_at)).limit(200))
    for s in r.scalars().all():
        c = await db.execute(select(Candidate).where(Candidate.id == s.candidate_id))
        cand = c.scalar_one_or_none()
        status_key = str(s.status)
        if status_key in columns:
            columns[status_key].append({
                "id": str(s.id), "candidate_id": str(s.candidate_id),
                "candidate": f"{cand.first_name} {cand.last_name}" if cand else "Unknown",
                "client": s.client_name,
                "bill_rate": float(s.bill_rate) if s.bill_rate else None,
                "date": str(s.created_at)[:10] if s.created_at else None,
            })
    return columns
