"""Opportunities — Federal (SAM.gov), State, Local opportunity discovery, matching, and tracking."""
import os
import httpx
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, or_, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import Opportunity, OpportunitySource, OpportunityStatus, CompanyProfile, SavedSearch, RFQ
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/opportunities", tags=["Opportunities"], dependencies=[Depends(require_role("contributor"))])
SAM_API_KEY = os.getenv("SAM_GOV_API_KEY", "")
SAM_API_URL = "https://api.sam.gov/opportunities/v2/search"
USASPENDING_API_URL = "https://api.usaspending.gov/api/v2"


# ── SAM.gov Search ───────────────────────────────────────────────────────────

@router.get("/search/federal")
async def search_federal(
    keyword: str = "",
    naics: str = "",
    set_aside: str = "",
    state: str = "",
    agency: str = "",
    posted_from: str = "",
    posted_to: str = "",
    ptype: str = "",  # o=Solicitation, p=Presolicitation, k=Combined, etc
    limit: int = 25,
    offset: int = 0,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search SAM.gov for federal opportunities. Falls back to local DB if no API key."""
    if SAM_API_KEY:
        return await _search_sam_gov(keyword, naics, set_aside, state, agency,
                                      posted_from, posted_to, ptype, limit, offset)
    else:
        # Search local database (opportunities previously imported or manually added)
        return await _search_local_opportunities(
            db, keyword, naics, set_aside, state, agency, "SAM.gov", limit, offset
        )


async def _search_sam_gov(keyword, naics, set_aside, state, agency,
                           posted_from, posted_to, ptype, limit, offset):
    """Live SAM.gov API search."""
    params = {
        "api_key": SAM_API_KEY,
        "limit": min(limit, 100),
        "offset": offset,
    }
    if keyword:
        params["title"] = keyword
    if naics:
        params["ncode"] = naics
    if set_aside:
        params["typeOfSetAside"] = set_aside
    if state:
        params["state"] = state
    if ptype:
        params["ptype"] = ptype
    if posted_from:
        params["postedFrom"] = posted_from
    if posted_to:
        params["postedTo"] = posted_to

    # Date range defaults: last 90 days
    if not posted_from:
        params["postedFrom"] = (datetime.now() - timedelta(days=90)).strftime("%m/%d/%Y")
    if not posted_to:
        params["postedTo"] = datetime.now().strftime("%m/%d/%Y")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(SAM_API_URL, params=params)
            if resp.status_code != 200:
                return {"error": f"SAM.gov API returned {resp.status_code}", "opportunities": [], "total": 0}
            data = resp.json()
            opps = data.get("opportunitiesData", [])
            total = data.get("totalRecords", 0)

            results = []
            for opp in opps:
                results.append({
                    "notice_id": opp.get("noticeId"),
                    "solicitation_number": opp.get("solicitationNumber", "").strip(),
                    "title": opp.get("title", "").strip(),
                    "department": opp.get("department", ""),
                    "sub_tier": opp.get("subTier", ""),
                    "office": opp.get("office", ""),
                    "posted_date": opp.get("postedDate"),
                    "response_deadline": opp.get("responseDeadLine"),
                    "opportunity_type": opp.get("type", ""),
                    "base_type": opp.get("baseType", ""),
                    "set_aside": opp.get("typeOfSetAside"),
                    "set_aside_description": opp.get("typeOfSetAsideDescription"),
                    "naics_code": opp.get("naicsCode"),
                    "classification_code": opp.get("classificationCode"),
                    "active": opp.get("active"),
                    "sam_url": f"https://sam.gov/opp/{opp.get('noticeId', '')}/view",
                    "award": opp.get("award"),
                    "contact": opp.get("pointOfContact", [{}])[0] if opp.get("pointOfContact") else {},
                    "source": "SAM.gov",
                })
            return {"opportunities": results, "total": total, "source": "SAM.gov Live"}
    except Exception as e:
        return {"error": str(e), "opportunities": [], "total": 0}


# ── State / Free Search ──────────────────────────────────────────────────────

@router.get("/search/state")
async def search_state(
    keyword: str = "",
    state: str = "",
    category: str = "",
    limit: int = 25,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search state procurement portals (free sources). Returns locally stored + web-scraped results."""
    # Search local DB for state opportunities
    return await _search_local_opportunities(db, keyword, "", "", state, "", "State", limit, 0)


STATE_PROCUREMENT_PORTALS = {
    "TX": {"name": "Texas SmartBuy / ESBD", "url": "https://www.txsmartbuy.com/sp", "api": None},
    "CA": {"name": "Cal eProcure", "url": "https://caleprocure.ca.gov/pages/public-search.aspx", "api": None},
    "VA": {"name": "eVA", "url": "https://eva.virginia.gov", "api": None},
    "MD": {"name": "eMaryland Marketplace Advantage", "url": "https://emma.maryland.gov", "api": None},
    "FL": {"name": "MyFloridaMarketPlace", "url": "https://vendor.myfloridamarketplace.com", "api": None},
    "NY": {"name": "NY State Contract Reporter", "url": "https://www.nyscr.ny.gov", "api": None},
    "GA": {"name": "Georgia Procurement Registry", "url": "https://ssl.doas.state.ga.us/PRSapp/PR_index.jsp", "api": None},
    "PA": {"name": "PA eMarketplace", "url": "https://www.emarketplace.state.pa.us", "api": None},
    "OH": {"name": "Ohio Procurement", "url": "https://procure.ohio.gov", "api": None},
    "IL": {"name": "Illinois BidBuy", "url": "https://www.bidbuy.illinois.gov", "api": None},
    "NC": {"name": "NC eProcurement", "url": "https://www.ips.state.nc.us/ips/", "api": None},
    "NJ": {"name": "NJ eProcurement", "url": "https://www.njstart.gov", "api": None},
    "WA": {"name": "WEBS", "url": "https://fortress.wa.gov/ga/webs/", "api": None},
    "CO": {"name": "Colorado BIDS", "url": "https://www.bidscolorado.com", "api": None},
    "AZ": {"name": "Arizona Procurement Portal", "url": "https://spo.az.gov", "api": None},
}


@router.get("/state-portals")
async def list_state_portals(user=Depends(get_current_user)):
    """List available state procurement portal links."""
    return STATE_PROCUREMENT_PORTALS


# ── USAspending Search ───────────────────────────────────────────────────────

@router.get("/search/usaspending")
async def search_usaspending(
    keyword: str = "",
    naics: str = "",
    agency: str = "",
    recipient: str = "",
    fiscal_year: int = 2025,
    limit: int = 25,
    user=Depends(get_current_user),
):
    """Search USAspending.gov for contract awards (free, no API key needed)."""
    try:
        payload = {
            "filters": {
                "time_period": [{"start_date": f"{fiscal_year}-10-01", "end_date": f"{fiscal_year + 1}-09-30"}],
                "award_type_codes": ["A", "B", "C", "D"],  # Contracts only
            },
            "fields": [
                "Award ID", "Recipient Name", "Award Amount",
                "Awarding Agency", "Awarding Sub Agency", "Award Type",
                "Start Date", "End Date", "Description", "NAICS Code",
                "generated_internal_id",
            ],
            "page": 1,
            "limit": min(limit, 100),
            "sort": "Award Amount",
            "order": "desc",
        }
        if keyword:
            payload["filters"]["keywords"] = [keyword]
        if naics:
            payload["filters"]["naics_codes"] = {"require": [naics]}
        if agency:
            payload["filters"]["agencies"] = [{"type": "awarding", "tier": "toptier", "name": agency}]
        if recipient:
            payload["filters"]["recipient_search_text"] = [recipient]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{USASPENDING_API_URL}/search/spending_by_award/", json=payload)
            if resp.status_code != 200:
                return {"error": f"USAspending returned {resp.status_code}", "awards": [], "total": 0}
            data = resp.json()
            awards = []
            for r in data.get("results", []):
                awards.append({
                    "award_id": r.get("Award ID"),
                    "recipient_name": r.get("Recipient Name"),
                    "award_amount": r.get("Award Amount"),
                    "awarding_agency": r.get("Awarding Agency"),
                    "awarding_sub_agency": r.get("Awarding Sub Agency"),
                    "award_type": r.get("Award Type"),
                    "start_date": r.get("Start Date"),
                    "end_date": r.get("End Date"),
                    "description": r.get("Description"),
                    "naics_code": r.get("NAICS Code"),
                    "usaspending_url": f"https://www.usaspending.gov/award/{r.get('generated_internal_id', '')}",
                })
            return {"awards": awards, "total": data.get("page_metadata", {}).get("total", 0), "source": "USAspending.gov"}
    except Exception as e:
        return {"error": str(e), "awards": [], "total": 0}


# ── Matching Engine ──────────────────────────────────────────────────────────

@router.post("/match")
async def match_opportunity(
    opportunity: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Score an opportunity against the company profile."""
    result = await db.execute(select(CompanyProfile).where(CompanyProfile.is_active == True).limit(1))
    profile = result.scalar_one_or_none()
    if not profile:
        return {"score": 0, "reasons": ["No company profile configured. Go to Company Profile to set up NAICS codes and capabilities."]}

    score = 0
    reasons = []
    opp_naics = opportunity.get("naics_code", "")
    opp_set_aside = (opportunity.get("set_aside_description") or opportunity.get("set_aside") or "").lower()
    opp_title = (opportunity.get("title") or "").lower()
    opp_desc = (opportunity.get("description") or "").lower()
    opp_state = (opportunity.get("place_of_performance_state") or "").upper()
    opp_dept = (opportunity.get("department") or "").lower()

    # NAICS match (strongest signal — 35 points)
    company_naics = [n.get("code", "") for n in (profile.naics_codes or [])]
    if opp_naics and opp_naics in company_naics:
        score += 35
        reasons.append(f"NAICS {opp_naics} matches your registered codes")
    elif opp_naics and any(opp_naics[:4] == cn[:4] for cn in company_naics):
        score += 20
        reasons.append(f"NAICS {opp_naics} partially matches (same 4-digit group)")

    # Set-aside match (20 points)
    company_certs = [c.lower() for c in (profile.socioeconomic_categories or [])]
    if opp_set_aside:
        for cert in company_certs:
            if cert in opp_set_aside or opp_set_aside in cert:
                score += 20
                reasons.append(f"Set-aside '{opp_set_aside}' matches your {cert} certification")
                break
        if not any(cert in opp_set_aside for cert in company_certs):
            if "unrestricted" in opp_set_aside or "full and open" in opp_set_aside:
                score += 10
                reasons.append("Full and open competition — eligible")

    # Keyword/competency match (20 points)
    competencies = [c.lower() for c in (profile.core_competencies or [])]
    pp_keywords = [k.lower() for k in (profile.past_performance_keywords or [])]
    all_keywords = competencies + pp_keywords
    matched_keywords = [kw for kw in all_keywords if kw in opp_title or kw in opp_desc]
    if matched_keywords:
        kw_score = min(20, len(matched_keywords) * 5)
        score += kw_score
        reasons.append(f"Keywords match: {', '.join(matched_keywords[:5])}")

    # Agency preference (10 points)
    target_agencies = [a.lower() for a in (profile.target_agencies or [])]
    if target_agencies and any(ta in opp_dept for ta in target_agencies):
        score += 10
        reasons.append(f"Target agency match: {opp_dept}")

    # Geographic match (10 points)
    service_states = profile.service_states or []
    if opp_state and (opp_state in service_states or not service_states):
        score += 10
        reasons.append(f"Service area includes {opp_state}" if opp_state in service_states else "No geographic restriction")

    # Excluded keywords (negative)
    excluded = [e.lower() for e in (profile.excluded_keywords or [])]
    for ex in excluded:
        if ex in opp_title or ex in opp_desc:
            score = max(0, score - 20)
            reasons.append(f"Contains excluded keyword: {ex}")

    # Cap at 100
    score = min(100, score)

    return {"score": score, "reasons": reasons, "grade": _grade(score)}


def _grade(score):
    if score >= 80:
        return "A"
    elif score >= 60:
        return "B"
    elif score >= 40:
        return "C"
    elif score >= 20:
        return "D"
    return "F"


# ── CRUD — Save/Track Opportunities ─────────────────────────────────────────

@router.get("/")
async def list_opportunities(
    status: str = "",
    source: str = "",
    limit: int = 50,
    offset: int = 0,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List tracked/saved opportunities."""
    query = select(Opportunity).where(Opportunity.is_active == True)
    if status:
        query = query.where(Opportunity.status == status)
    if source:
        query = query.where(Opportunity.source == source)
    query = query.order_by(desc(Opportunity.created_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    opps = result.scalars().all()

    count_q = select(func.count(Opportunity.id)).where(Opportunity.is_active == True)
    total = (await db.execute(count_q)).scalar()

    return {
        "opportunities": [_opp_to_dict(o) for o in opps],
        "total": total,
    }


@router.post("/save")
async def save_opportunity(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Save an opportunity from search results to track it."""
    # Check if already saved
    notice_id = payload.get("notice_id")
    if notice_id:
        existing = await db.execute(select(Opportunity).where(Opportunity.notice_id == notice_id))
        if existing.scalar_one_or_none():
            return {"status": "already_saved", "message": "This opportunity is already in your tracker."}

    from datetime import datetime as dt
    opp = Opportunity(
        notice_id=notice_id,
        solicitation_number=payload.get("solicitation_number"),
        title=payload.get("title", "Untitled"),
        description=payload.get("description"),
        source=payload.get("source", "SAM.gov"),
        opportunity_type=payload.get("opportunity_type") or payload.get("base_type"),
        department=payload.get("department"),
        sub_tier=payload.get("sub_tier"),
        office=payload.get("office"),
        naics_code=payload.get("naics_code"),
        classification_code=payload.get("classification_code"),
        set_aside=payload.get("set_aside"),
        set_aside_description=payload.get("set_aside_description"),
        posted_date=_parse_date(payload.get("posted_date")),
        estimated_value=payload.get("estimated_value"),
        contact_name=payload.get("contact", {}).get("fullName") if isinstance(payload.get("contact"), dict) else payload.get("contact_name"),
        contact_email=payload.get("contact", {}).get("email") if isinstance(payload.get("contact"), dict) else payload.get("contact_email"),
        contact_phone=payload.get("contact", {}).get("phone") if isinstance(payload.get("contact"), dict) else payload.get("contact_phone"),
        place_of_performance_state=payload.get("place_of_performance_state"),
        sam_url=payload.get("sam_url"),
        status=OpportunityStatus.NEW,
        match_score=payload.get("match_score"),
        match_reasons=payload.get("match_reasons"),
        raw_data=payload,
    )
    # Parse response deadline
    deadline = payload.get("response_deadline")
    if deadline:
        try:
            opp.response_deadline = dt.fromisoformat(deadline.replace("Z", "+00:00")) if isinstance(deadline, str) else deadline
        except (ValueError, TypeError):
            pass

    db.add(opp)
    await db.flush()
    return {"status": "saved", "id": str(opp.id)}


@router.patch("/{opp_id}")
async def update_opportunity(opp_id: str, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Update opportunity status, notes, assignment."""
    result = await db.execute(select(Opportunity).where(Opportunity.id == opp_id))
    opp = result.scalar_one_or_none()
    if not opp:
        raise HTTPException(404)
    for field in ("status", "notes", "assigned_to", "match_score"):
        if field in payload:
            setattr(opp, field, payload[field])
    await db.flush()
    return {"status": "updated"}


@router.post("/{opp_id}/create-rfq")
async def create_rfq_from_opportunity(opp_id: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Create an RFQ from a tracked opportunity."""
    result = await db.execute(select(Opportunity).where(Opportunity.id == opp_id))
    opp = result.scalar_one_or_none()
    if not opp:
        raise HTTPException(404)

    rfq = RFQ(
        title=opp.title,
        agency=opp.department or opp.sub_tier,
        solicitation_number=opp.solicitation_number,
        naics_code=opp.naics_code,
        due_date=opp.response_deadline.date() if opp.response_deadline else None,
        estimated_value=opp.estimated_value,
        source=opp.source.value if opp.source else "SAM.gov",
        contract_officer_name=opp.contact_name,
        contract_officer_email=opp.contact_email,
        contract_officer_phone=opp.contact_phone,
    )
    if opp.set_aside:
        sa_map = {"SBA": "SB", "8(a)": "8(a)", "WOSB": "WOSB", "HUBZone": "HUBZone", "SDVOSB": "SDVOSB"}
        for key, val in sa_map.items():
            if key.lower() in (opp.set_aside or "").lower():
                rfq.set_aside_type = val
                break

    db.add(rfq)
    await db.flush()

    opp.rfq_id = rfq.id
    opp.status = OpportunityStatus.PURSUING
    await db.flush()

    return {"status": "rfq_created", "rfq_id": str(rfq.id), "opp_id": str(opp.id)}


@router.delete("/{opp_id}")
async def delete_opportunity(opp_id: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Opportunity).where(Opportunity.id == opp_id))
    opp = result.scalar_one_or_none()
    if not opp:
        raise HTTPException(404)
    opp.is_active = False
    await db.flush()
    return {"status": "deleted"}


# ── Saved Searches ───────────────────────────────────────────────────────────

@router.get("/saved-searches")
async def list_saved_searches(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SavedSearch).where(SavedSearch.is_active == True).order_by(desc(SavedSearch.created_at)))
    return [{"id": str(s.id), "name": s.name, "search_type": s.search_type,
             "keywords": s.keywords, "naics_codes": s.naics_codes,
             "set_aside_types": s.set_aside_types, "agencies": s.agencies,
             "states": s.states} for s in result.scalars().all()]


@router.post("/saved-searches")
async def create_saved_search(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    search = SavedSearch(
        name=payload.get("name", "Untitled Search"),
        search_type=payload.get("search_type", "federal"),
        keywords=payload.get("keywords"),
        naics_codes=payload.get("naics_codes"),
        set_aside_types=payload.get("set_aside_types"),
        agencies=payload.get("agencies"),
        states=payload.get("states"),
        min_value=payload.get("min_value"),
        max_value=payload.get("max_value"),
        user_id=user.id,
    )
    db.add(search)
    await db.flush()
    return {"status": "saved", "id": str(search.id)}


# ── Dashboard Stats ──────────────────────────────────────────────────────────

@router.get("/stats")
async def opportunity_stats(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Opportunity.id)).where(Opportunity.is_active == True))).scalar()
    new = (await db.execute(select(func.count(Opportunity.id)).where(Opportunity.status == "New"))).scalar()
    pursuing = (await db.execute(select(func.count(Opportunity.id)).where(Opportunity.status == "Pursuing"))).scalar()
    submitted = (await db.execute(select(func.count(Opportunity.id)).where(Opportunity.status == "Bid Submitted"))).scalar()
    won = (await db.execute(select(func.count(Opportunity.id)).where(Opportunity.status == "Won"))).scalar()
    high_match = (await db.execute(select(func.count(Opportunity.id)).where(
        and_(Opportunity.is_active == True, Opportunity.match_score >= 70)))).scalar()
    return {
        "total": total, "new": new, "pursuing": pursuing,
        "submitted": submitted, "won": won, "high_match": high_match,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _search_local_opportunities(db, keyword, naics, set_aside, state, agency, source_filter, limit, offset):
    query = select(Opportunity).where(Opportunity.is_active == True)
    if keyword:
        query = query.where(or_(
            Opportunity.title.ilike(f"%{keyword}%"),
            Opportunity.description.ilike(f"%{keyword}%"),
        ))
    if naics:
        query = query.where(Opportunity.naics_code == naics)
    if set_aside:
        query = query.where(Opportunity.set_aside.ilike(f"%{set_aside}%"))
    if state:
        query = query.where(Opportunity.place_of_performance_state == state.upper())
    if agency:
        query = query.where(Opportunity.department.ilike(f"%{agency}%"))
    if source_filter:
        query = query.where(Opportunity.source == source_filter)

    query = query.order_by(desc(Opportunity.posted_date)).limit(limit).offset(offset)
    result = await db.execute(query)
    opps = result.scalars().all()
    return {"opportunities": [_opp_to_dict(o) for o in opps], "total": len(opps), "source": "Local DB"}


def _opp_to_dict(o):
    return {
        "id": str(o.id),
        "notice_id": o.notice_id,
        "solicitation_number": o.solicitation_number,
        "title": o.title,
        "description": (o.description or "")[:300],
        "source": o.source.value if isinstance(o.source, OpportunitySource) else o.source,
        "opportunity_type": o.opportunity_type,
        "department": o.department,
        "sub_tier": o.sub_tier,
        "office": o.office,
        "naics_code": o.naics_code,
        "classification_code": o.classification_code,
        "set_aside": o.set_aside,
        "set_aside_description": o.set_aside_description,
        "posted_date": str(o.posted_date) if o.posted_date else None,
        "response_deadline": str(o.response_deadline) if o.response_deadline else None,
        "estimated_value": float(o.estimated_value) if o.estimated_value else None,
        "contact_name": o.contact_name,
        "contact_email": o.contact_email,
        "contact_phone": o.contact_phone,
        "place_of_performance_state": o.place_of_performance_state,
        "sam_url": o.sam_url,
        "status": o.status.value if isinstance(o.status, OpportunityStatus) else o.status,
        "match_score": o.match_score,
        "match_reasons": o.match_reasons,
        "assigned_to": o.assigned_to,
        "notes": o.notes,
        "rfq_id": str(o.rfq_id) if o.rfq_id else None,
        "created_at": str(o.created_at) if o.created_at else None,
    }


def _parse_date(val):
    if not val:
        return None
    try:
        if isinstance(val, date):
            return val
        return date.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None
