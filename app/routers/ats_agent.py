"""
ATS AI Agent v2 — Enhanced Recruitment Intelligence
Isolated from procurement. Only accesses ATS data.
Features: Match explainability, confidence indicators, submission packages,
rate intelligence, job priority, vendor targeting, learning, tasks, comparison, memory.
"""
import os
import json
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import (
    Candidate, JobPosting, Application, Submission, BenchCandidate,
    JobStatus, ApplicationStatus, Task, TaskStatus,
    AIMemory, PlacementOutcome
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/ats/ai-agent", tags=["ATS AI Agent"])
ATS_ROLES = {"Admin", "Manager", "Staffing Manager", "Recruiter", "Sales"}


async def require_ats(user=Depends(get_current_user)):
    if user.role not in ATS_ROLES:
        raise HTTPException(403, "ATS AI Agent requires staffing role")
    return user


def _claude(system: str, prompt: str, tokens: int = 3000) -> str:
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
# FULL ANALYSIS — Enhanced with all 10 features
# ══════════════════════════════════════════════════════════════

@router.post("/analyze-resume")
async def analyze_resume(
    resume: UploadFile | None = File(None),
    resume_text: str = Form(""),
    candidate_id: str = Form(""),
    user=Depends(require_ats),
    db: AsyncSession = Depends(get_db),
):
    # ── Get resume text ──
    text = resume_text.strip()
    if resume and not text:
        content = await resume.read()
        if resume.size and resume.size > 10_000_000:
            raise HTTPException(400, "File too large (max 10MB)")
        try:
            from app.services.doc_extract import extract_text
            text = extract_text(content, resume.filename or "resume.pdf")
        except Exception as e:
            raise HTTPException(400, f"Parse error: {e}. Try pasting text directly.")

    if not text and candidate_id:
        r = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        c = r.scalar_one_or_none()
        if c and c.resume_text:
            text = c.resume_text
        elif c:
            text = f"Name: {c.first_name} {c.last_name}\nSkills: {c.skills}\nExperience: {c.years_experience} years\nClearance: {c.clearance_level}\nLocation: {c.location}"

    if not text:
        raise HTTPException(400, "No resume provided. Upload PDF, paste text, or select a candidate.")

    # ── Load ATS jobs ──
    jobs_r = await db.execute(select(JobPosting).where(JobPosting.status == JobStatus.OPEN).order_by(desc(JobPosting.created_at)))
    open_jobs = jobs_r.scalars().all()
    jobs_ctx = "\n".join([
        f"JOB[{i}]: {j.title} | Loc: {j.location} | Clearance: {j.clearance_required} | Skills: {j.skills_required} | Type: {j.employment_type} | Contract: {j.contract_name} | Salary: {j.salary_min}-{j.salary_max}"
        for i, j in enumerate(open_jobs)
    ]) or "No open positions."

    # ── Load past outcomes for learning ──
    outcomes_r = await db.execute(select(PlacementOutcome).order_by(desc(PlacementOutcome.created_at)).limit(20))
    outcomes = outcomes_r.scalars().all()
    learning_ctx = ""
    if outcomes:
        learning_ctx = "\nPAST PLACEMENT DATA (use to improve matching):\n"
        for o in outcomes:
            learning_ctx += f"- Outcome: {o.outcome}, Score: {o.match_score}, Bill: ${o.actual_bill_rate}, Pay: ${o.actual_pay_rate}, Feedback: {o.feedback}\n"

    # ── Enhanced AI prompt ──
    system = """You are an expert ATS recruitment AI agent for Alliance Global Tech (AGT), a federal IT staffing company.

STRICT DATA ISOLATION: You ONLY work with candidate/job/staffing data. NEVER reference procurement, hardware, suppliers, RFQs, or quotes.

Return ONLY valid JSON with this structure:
{
  "profile": {
    "skills": [{"name": "Python", "confidence": 95}, {"name": "AWS", "confidence": 80}],
    "years_experience": {"value": 8, "confidence": 90},
    "job_titles": ["Senior Developer", "Tech Lead"],
    "certifications": ["AWS Solutions Architect", "PMP"],
    "clearance_level": {"value": "Secret", "confidence": 85, "flag": null},
    "education": "BS Computer Science, University of Maryland",
    "location": "Columbia, MD"
  },
  "summary": "3-5 line recruiter-ready summary. Specific. No filler.",
  "submission_package": "Ready-to-send candidate write-up for client submission. 5-8 lines. Professional tone. Include key strengths, clearance, availability.",
  "job_matches": [
    {
      "job_title": "Senior Cloud Engineer",
      "match_score": 85,
      "match_reasons": ["8 years AWS experience matches requirement", "Active Secret clearance matches"],
      "skill_gaps": ["Kubernetes not listed but required"],
      "priority": "HIGH",
      "priority_reason": "Urgent fill, high bill rate contract",
      "confidence": 82
    }
  ],
  "submission_recommendations": [
    {"job_title": "...", "priority": 1, "justification": "Best skill alignment + clearance match"}
  ],
  "rate_intelligence": {
    "suggested_pay_range": {"min": 55, "max": 70, "basis": "8 yrs exp, Secret clearance, Azure/AWS"},
    "suggested_bill_range": {"min": 95, "max": 120, "basis": "Federal IT rate cards for this skill level"},
    "margin_estimate": "35-42%"
  },
  "vendor_targeting": [
    {"company": "Booz Allen Hamilton", "reason": "Active federal cloud contracts, hires this profile"},
    {"company": "Leidos", "reason": "DoD cloud modernization programs"}
  ],
  "bench_sales": {
    "needed": false,
    "target_job_types": [],
    "target_industries": [],
    "positioning_strategy": ""
  }
}

IMPORTANT:
- confidence is 0-100 for each extracted field
- If clearance is ambiguous, set flag to a warning string
- priority is HIGH/MEDIUM/LOW based on urgency and bill rate potential
- Rate intelligence should reflect federal IT market rates
- Vendor targeting: suggest 3-5 real federal IT companies that hire this profile
- If no matches above 60, set bench_sales.needed=true"""

    prompt = f"""Analyze this resume and match against our open positions.

RESUME:
{text[:6000]}

OPEN POSITIONS:
{jobs_ctx}
{learning_ctx}

Return ONLY the JSON object."""

    try:
        response = _claude(system, prompt, 4000)
        analysis = _json(response)
    except Exception as e:
        raise HTTPException(500, f"AI analysis failed: {e}")

    if not analysis:
        raise HTTPException(500, "AI returned invalid response. Try again.")

    # ── Auto-trigger bench sales ──
    matches = analysis.get('job_matches', [])
    if not matches or all(m.get('match_score', 0) < 60 for m in matches):
        if 'bench_sales' in analysis:
            analysis['bench_sales']['needed'] = True

    # ── Save to AI Memory ──
    try:
        cand_name = None
        if candidate_id:
            cr = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
            c = cr.scalar_one_or_none()
            if c:
                cand_name = f"{c.first_name} {c.last_name}"
                profile = analysis.get('profile', {})
                skills_list = [s['name'] if isinstance(s, dict) else s for s in profile.get('skills', [])]
                if skills_list and not c.skills:
                    c.skills = ', '.join(skills_list)
                yrs = profile.get('years_experience', {})
                yrs_val = yrs.get('value') if isinstance(yrs, dict) else yrs
                if yrs_val and not c.years_experience:
                    c.years_experience = int(yrs_val)
                cl = profile.get('clearance_level', {})
                cl_val = cl.get('value') if isinstance(cl, dict) else cl
                if cl_val and cl_val != 'None' and c.clearance_level in (None, 'None'):
                    c.clearance_level = cl_val

        skills_str = ', '.join([s['name'] if isinstance(s, dict) else s for s in analysis.get('profile', {}).get('skills', [])])
        cl_data = analysis.get('profile', {}).get('clearance_level', {})
        yrs_data = analysis.get('profile', {}).get('years_experience', {})
        mem = AIMemory(
            candidate_id=candidate_id if candidate_id else None,
            candidate_name=cand_name,
            run_type="resume_analysis",
            summary=analysis.get('summary', ''),
            skills=skills_str,
            clearance=cl_data.get('value') if isinstance(cl_data, dict) else str(cl_data or ''),
            years_experience=int(yrs_data.get('value')) if isinstance(yrs_data, dict) and yrs_data.get('value') else (int(yrs_data) if isinstance(yrs_data, (int, float)) else None),
            match_data=json.dumps(analysis.get('job_matches', []))[:2000],
            submission_package=analysis.get('submission_package', ''),
            full_result=json.dumps(analysis)[:10000],
        )
        db.add(mem)
        await db.flush()
    except:
        pass

    analysis['open_jobs_count'] = len(open_jobs)
    return analysis


# ══════════════════════════════════════════════════════════════
# CANDIDATE COMPARISON
# ══════════════════════════════════════════════════════════════

@router.post("/compare")
async def compare_candidates(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Compare 2-4 candidates side by side for a specific job."""
    ids = payload.get('candidate_ids', [])
    job_title = payload.get('job_title', '')
    if len(ids) < 2:
        raise HTTPException(400, "Provide at least 2 candidate IDs")

    candidates = []
    for cid in ids[:4]:
        r = await db.execute(select(Candidate).where(Candidate.id == cid))
        c = r.scalar_one_or_none()
        if c:
            candidates.append({
                "id": str(c.id), "name": f"{c.first_name} {c.last_name}",
                "skills": c.skills or '', "experience": c.years_experience or 0,
                "clearance": c.clearance_level or 'None', "location": c.location or '',
            })

    if len(candidates) < 2:
        raise HTTPException(400, "Need at least 2 valid candidates")

    cand_text = "\n".join([f"CANDIDATE {i+1}: {c['name']} | Skills: {c['skills']} | Exp: {c['experience']}yr | Clearance: {c['clearance']}" for i, c in enumerate(candidates)])

    system = """You are a recruitment comparison AI. Return ONLY JSON:
{"comparison": [{"name": "...", "strengths": ["..."], "weaknesses": ["..."], "fit_score": 85, "recommendation": "..."}], "winner": "name", "winner_reason": "..."}"""

    prompt = f"Compare these candidates{' for: ' + job_title if job_title else ''}:\n{cand_text}"

    try:
        result = _json(_claude(system, prompt, 2000))
    except:
        result = {}

    result['candidates'] = candidates
    return result


# ══════════════════════════════════════════════════════════════
# QUICK MATCH — existing candidate against jobs
# ══════════════════════════════════════════════════════════════

@router.get("/quick-match/{candidate_id}")
async def quick_match(candidate_id: UUID, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(404)

    jobs_r = await db.execute(select(JobPosting).where(JobPosting.status == JobStatus.OPEN))
    jobs = jobs_r.scalars().all()
    if not jobs:
        return {"matches": [], "bench_sales_needed": True}

    profile = f"{c.first_name} {c.last_name} | Skills: {c.skills} | Exp: {c.years_experience}yr | Clearance: {c.clearance_level}"
    if c.resume_text:
        profile += f"\n{c.resume_text[:2000]}"

    jobs_text = "\n".join([f"- {j.title} | {j.clearance_required} | {j.skills_required}" for j in jobs])

    system = 'Return ONLY JSON: {"matches": [{"job_title": "...", "score": 85, "reasons": ["..."], "gaps": ["..."]}]}'
    try:
        result = _json(_claude(system, f"Candidate:\n{profile}\n\nJobs:\n{jobs_text}", 1500))
    except:
        result = {"matches": []}

    return {"candidate": f"{c.first_name} {c.last_name}", **result, "bench_sales_needed": not result.get('matches') or all(m.get('score', 0) < 60 for m in result.get('matches', []))}


# ══════════════════════════════════════════════════════════════
# SUBMISSION PACKAGE GENERATOR
# ══════════════════════════════════════════════════════════════

@router.post("/submission-package/{candidate_id}")
async def generate_submission_package(candidate_id: UUID, payload: dict = {}, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Generate a client-ready submission write-up."""
    r = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(404)

    job_title = payload.get('job_title', 'Federal IT Position')
    client = payload.get('client', '')

    profile = f"Name: {c.first_name} {c.last_name}\nSkills: {c.skills}\nExperience: {c.years_experience} years\nClearance: {c.clearance_level}\nLocation: {c.location}"
    if c.resume_text:
        profile += f"\nResume: {c.resume_text[:3000]}"

    system = """You are a staffing sales professional. Write a client submission package.
Return ONLY JSON: {"subject_line": "Candidate Submission: [Name] for [Role]", "body": "Professional 8-12 line write-up suitable to email to a client or vendor. Include key qualifications, clearance, availability, and why this candidate is a strong fit.", "key_highlights": ["highlight 1", "highlight 2", "highlight 3"]}"""

    prompt = f"Generate submission for:\n{profile}\n\nTarget position: {job_title}\nClient: {client or 'General'}"

    try:
        result = _json(_claude(system, prompt, 1500))
    except:
        result = {"body": "Generation failed", "subject_line": "", "key_highlights": []}

    return result


# ══════════════════════════════════════════════════════════════
# BATCH SUMMARY
# ══════════════════════════════════════════════════════════════

@router.post("/batch-summary")
async def batch_summary(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    ids = payload.get('candidate_ids', [])
    summaries = []
    for cid in ids[:10]:
        r = await db.execute(select(Candidate).where(Candidate.id == cid))
        c = r.scalar_one_or_none()
        if not c:
            continue
        try:
            resp = _claude("Write a 2-3 sentence professional summary. Specific, no filler. Return ONLY the summary.", f"{c.first_name} {c.last_name}, Skills: {c.skills}, Exp: {c.years_experience}yr, Clearance: {c.clearance_level}", 200)
            summaries.append({"id": str(c.id), "name": f"{c.first_name} {c.last_name}", "summary": resp.strip()})
        except:
            summaries.append({"id": str(c.id), "name": f"{c.first_name} {c.last_name}", "summary": "Failed"})
    return {"summaries": summaries}


# ══════════════════════════════════════════════════════════════
# TASK CREATION FROM AI RESULTS
# ══════════════════════════════════════════════════════════════

@router.post("/create-task")
async def create_task_from_ai(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Create follow-up task from AI analysis results."""
    task = Task(
        title=payload.get('title', 'Follow up on AI recommendation'),
        description=payload.get('description', ''),
        assigned_to=payload.get('assigned_to', user.full_name),
        due_date=payload.get('due_date'),
        task_type='ai_recommendation',
        rfq_id=None,
    )
    db.add(task)
    await db.flush()
    return {"id": str(task.id), "status": "created"}


# ══════════════════════════════════════════════════════════════
# OUTCOME TRACKING (Learning System)
# ══════════════════════════════════════════════════════════════

@router.post("/outcomes")
async def record_outcome(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Record interview/placement outcome to improve future matching."""
    outcome = PlacementOutcome(
        candidate_id=payload['candidate_id'],
        job_id=payload.get('job_id'),
        outcome=payload['outcome'],
        match_score=payload.get('match_score'),
        actual_bill_rate=payload.get('bill_rate'),
        actual_pay_rate=payload.get('pay_rate'),
        feedback=payload.get('feedback'),
        placed_date=payload.get('placed_date'),
    )
    db.add(outcome)
    await db.flush()
    return {"id": str(outcome.id), "status": "recorded"}


@router.get("/outcomes")
async def list_outcomes(user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(PlacementOutcome).order_by(desc(PlacementOutcome.created_at)).limit(50))
    outcomes = []
    for o in r.scalars().all():
        c = await db.execute(select(Candidate).where(Candidate.id == o.candidate_id))
        cand = c.scalar_one_or_none()
        outcomes.append({
            "id": str(o.id), "candidate": f"{cand.first_name} {cand.last_name}" if cand else "Unknown",
            "outcome": o.outcome, "score": o.match_score,
            "bill_rate": float(o.actual_bill_rate) if o.actual_bill_rate else None,
            "pay_rate": float(o.actual_pay_rate) if o.actual_pay_rate else None,
            "feedback": o.feedback, "date": str(o.placed_date) if o.placed_date else None,
        })
    return outcomes


@router.get("/learning-stats")
async def learning_stats(user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Aggregate learning data for AI improvement."""
    total = await db.execute(select(func.count(PlacementOutcome.id)))
    placed = await db.execute(select(func.count(PlacementOutcome.id)).where(PlacementOutcome.outcome == 'Placed'))
    rejected = await db.execute(select(func.count(PlacementOutcome.id)).where(PlacementOutcome.outcome == 'Rejected'))
    avg_bill = await db.execute(select(func.avg(PlacementOutcome.actual_bill_rate)).where(PlacementOutcome.actual_bill_rate != None))
    avg_pay = await db.execute(select(func.avg(PlacementOutcome.actual_pay_rate)).where(PlacementOutcome.actual_pay_rate != None))
    avg_score = await db.execute(select(func.avg(PlacementOutcome.match_score)).where(PlacementOutcome.outcome == 'Placed'))
    return {
        "total_outcomes": total.scalar(),
        "placed": placed.scalar(), "rejected": rejected.scalar(),
        "avg_bill_rate": round(float(avg_bill.scalar() or 0), 2),
        "avg_pay_rate": round(float(avg_pay.scalar() or 0), 2),
        "avg_winning_score": round(float(avg_score.scalar() or 0), 1),
    }


# ══════════════════════════════════════════════════════════════
# AI MEMORY — Searchable summaries
# ══════════════════════════════════════════════════════════════

@router.get("/memory")
async def search_memory(q: str | None = None, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    query = select(AIMemory).order_by(desc(AIMemory.created_at)).limit(30)
    if q:
        query = query.where(or_(
            AIMemory.candidate_name.ilike(f"%{q}%"),
            AIMemory.summary.ilike(f"%{q}%"),
            AIMemory.skills.ilike(f"%{q}%"),
        ))
    r = await db.execute(query)
    return [{
        "id": str(m.id), "candidate_name": m.candidate_name,
        "run_type": getattr(m, 'run_type', None) or 'resume_analysis',
        "summary": m.summary, "skills": m.skills, "clearance": m.clearance,
        "years_experience": m.years_experience,
        "match_data": m.match_data,
        "submission_package": m.submission_package,
        "full_result": getattr(m, 'full_result', None),
        "date": str(m.created_at) if m.created_at else None,
    } for m in r.scalars().all()]


@router.get("/memory/{memory_id}")
async def get_memory_detail(memory_id: UUID, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Get full details of a past AI run."""
    r = await db.execute(select(AIMemory).where(AIMemory.id == memory_id))
    m = r.scalar_one_or_none()
    if not m:
        raise HTTPException(404)
    full = None
    try:
        fr = getattr(m, 'full_result', None)
        if fr:
            full = json.loads(fr)
    except:
        pass
    return {
        "id": str(m.id), "candidate_name": m.candidate_name,
        "run_type": getattr(m, 'run_type', None) or 'resume_analysis',
        "summary": m.summary, "skills": m.skills, "clearance": m.clearance,
        "years_experience": m.years_experience,
        "submission_package": m.submission_package,
        "full_result": full,
        "date": str(m.created_at) if m.created_at else None,
    }


# ══════════════════════════════════════════════════════════════
# SCOPE GUARD
# ══════════════════════════════════════════════════════════════

@router.get("/scope")
async def check_scope():
    return {
        "agent": "ATS AI Agent v2", "version": "2.0",
        "allowed_data": ["Candidates", "Resumes", "Jobs", "Applications", "Submissions", "Outcomes"],
        "blocked_data": ["RFQs", "Quotes", "Suppliers", "Products", "Procurement", "Hardware", "Finance"],
        "capabilities": [
            "Resume parsing with confidence scores", "Job matching with explainability",
            "Submission package generation", "Rate intelligence",
            "Vendor targeting", "Candidate comparison", "Outcome learning",
            "Searchable AI memory", "Task integration",
        ]
    }


# ══════════════════════════════════════════════════════════════
# C2C JOB SEARCH — Web search for contract positions
# ══════════════════════════════════════════════════════════════

@router.post("/job-search")
async def search_c2c_jobs(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """
    Search the web for C2C/contract jobs matching a candidate's resume.
    Uses Claude with web search to find real postings from job boards.
    """
    candidate_id = payload.get('candidate_id') or ''
    skills = str(payload.get('skills') or '')
    clearance = str(payload.get('clearance') or '')
    location = str(payload.get('location') or '')
    job_title = str(payload.get('job_title') or '')
    custom_query = str(payload.get('custom_query') or '')

    # Build search context from candidate if provided
    if candidate_id and not skills:
        try:
            r = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
            c = r.scalar_one_or_none()
            if c:
                skills = str(c.skills or '')
                clearance = str(c.clearance_level or '')
                location = str(c.location or '')
        except:
            pass

    if not skills and not custom_query and not job_title:
        raise HTTPException(400, "Provide skills, job_title, or custom_query")

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured")

    from anthropic import Anthropic
    client = Anthropic(api_key=key)

    # Build focused search queries — all values guaranteed to be strings
    top_skills = ', '.join([s.strip() for s in skills.split(',')[:5] if s.strip()]) if skills else ''
    search_term = top_skills or job_title or 'IT'
    search_context = custom_query if custom_query else f"{search_term} C2C contract"
    if clearance and clearance not in ('None', '', 'null'):
        search_context = search_context + " " + clearance + " clearance"
    if location and location not in ('', 'null'):
        search_context = search_context + " " + location

    skills_display = skills if skills else 'General IT'
    clearance_display = clearance if clearance and clearance not in ('None', '', 'null') else 'None'
    location_display = location if location and location not in ('', 'null') else 'Remote/Anywhere'
    title_display = job_title if job_title else 'IT Contractor'

    system_prompt = f"""You are an expert IT staffing recruiter searching for C2C (Corp-to-Corp) contract positions.

The candidate has these qualifications:
- Skills: {skills_display}
- Clearance: {clearance_display}
- Location: {location_display}
- Target role: {title_display}

Search the web for REAL, CURRENT C2C contract job postings that match this profile.
Look across job boards: Indeed, Dice, LinkedIn, ZipRecruiter, Clearance Jobs, and vendor/staffing company sites.

Return ONLY valid JSON with this structure:
{{
  "jobs": [
    {{
      "title": "exact job title from posting",
      "company": "hiring company or staffing firm",
      "location": "city, state or Remote",
      "contract_type": "C2C / W2 / 1099",
      "rate": "$XX-XX/hr or listed rate",
      "clearance_required": "clearance level if mentioned",
      "key_skills": ["skill1", "skill2"],
      "match_score": 85,
      "match_reason": "why this matches the candidate",
      "source": "Indeed / Dice / LinkedIn / etc",
      "posting_date": "approximate date",
      "contact": {{
        "company_name": "staffing firm name",
        "recruiter_name": "name if available",
        "email": "email if found",
        "phone": "phone if found",
        "apply_method": "how to apply"
      }},
      "url": "link to the job posting if found"
    }}
  ],
  "search_summary": "Brief summary of the C2C market for this skill set",
  "market_rate": "Typical C2C rate range for this profile",
  "total_found": 10,
  "search_terms_used": ["term1", "term2"]
}}

IMPORTANT RULES:
- Only include REAL jobs you find via web search
- Include actual contact information when publicly available
- Flag if contact info could not be found
- Sort by match_score descending
- Include the source website for each job
- Do NOT fabricate job listings or contact details"""

    user_prompt = f"Search for current C2C contract jobs matching: {search_context}"

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system_prompt,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract text from response (may have multiple content blocks)
        full_text = ""
        for block in msg.content:
            if hasattr(block, 'text'):
                full_text += block.text

        result = _json(full_text)

        if not result or not result.get('jobs'):
            # If JSON extraction failed, try to get any structured data
            result = {
                "jobs": [],
                "search_summary": full_text[:500] if full_text else "Search completed but no structured results returned.",
                "raw_response": full_text[:2000] if full_text else None,
                "total_found": 0,
            }

    except Exception as e:
        raise HTTPException(500, f"Job search failed: {str(e)}")

    # Save search to memory for future reference
    try:
        mem = AIMemory(
            candidate_id=candidate_id if candidate_id and candidate_id != '' else None,
            run_type="job_search",
            summary=f"C2C Job Search: {search_context}. Found {result.get('total_found', len(result.get('jobs', [])))} positions.",
            skills=skills or None,
            clearance=clearance if clearance and clearance not in ('None', '', 'null') else None,
            full_result=json.dumps(result)[:10000],
        )
        db.add(mem)
        await db.flush()
    except:
        pass

    result['candidate_skills'] = skills or ''
    result['candidate_clearance'] = clearance or ''
    result['candidate_location'] = location or ''
    return result


@router.post("/job-search/save")
async def save_job_lead(payload: dict, user=Depends(require_ats), db: AsyncSession = Depends(get_db)):
    """Save an interesting job lead from search results for tracking."""
    from app.models import OutreachLog, OutreachStatus

    log = OutreachLog(
        candidate_id=payload.get('candidate_id') or None,
        target_company=str(payload.get('company') or 'Unknown'),
        subject=f"C2C Opportunity: {str(payload.get('title') or 'Position')}",
        email_content=json.dumps({
            "title": payload.get('title') or '',
            "company": payload.get('company') or '',
            "rate": payload.get('rate') or '',
            "location": payload.get('location') or '',
            "source": payload.get('source') or '',
            "contact": payload.get('contact') or {},
            "url": payload.get('url') or '',
        }),
        status=OutreachStatus.DRAFT,
        sent_by=user.full_name,
    )
    db.add(log)
    await db.flush()
    return {"id": str(log.id), "status": "saved"}
