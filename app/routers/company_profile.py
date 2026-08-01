"""Company Profile — NAICS codes, SINs, certifications, capabilities for opportunity matching."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import CompanyProfile
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/company-profile", tags=["Company Profile"], dependencies=[Depends(require_role("contributor"))])
class ProfileReq(BaseModel):
    company_name: str
    dba_name: str | None = None
    cage_code: str | None = None
    uei_number: str | None = None
    duns_number: str | None = None
    sam_registration_date: str | None = None
    sam_expiration_date: str | None = None
    naics_codes: list | None = None
    gsa_sins: list | None = None
    psc_codes: list | None = None
    certifications: list | None = None
    capabilities_narrative: str | None = None
    core_competencies: list | None = None
    past_performance_keywords: list | None = None
    contract_vehicles: list | None = None
    business_size: str | None = None
    socioeconomic_categories: list | None = None
    primary_state: str | None = None
    service_states: list | None = None
    min_contract_value: float | None = None
    max_contract_value: float | None = None
    target_agencies: list | None = None
    excluded_keywords: list | None = None


@router.get("/")
async def get_profile(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get the company profile (singleton — one per platform)."""
    result = await db.execute(select(CompanyProfile).where(CompanyProfile.is_active == True).limit(1))
    profile = result.scalar_one_or_none()
    if not profile:
        return None
    return {
        "id": str(profile.id),
        "company_name": profile.company_name,
        "dba_name": profile.dba_name,
        "cage_code": profile.cage_code,
        "uei_number": profile.uei_number,
        "duns_number": profile.duns_number,
        "sam_registration_date": str(profile.sam_registration_date) if profile.sam_registration_date else None,
        "sam_expiration_date": str(profile.sam_expiration_date) if profile.sam_expiration_date else None,
        "naics_codes": profile.naics_codes or [],
        "gsa_sins": profile.gsa_sins or [],
        "psc_codes": profile.psc_codes or [],
        "certifications": profile.certifications or [],
        "capabilities_narrative": profile.capabilities_narrative,
        "core_competencies": profile.core_competencies or [],
        "past_performance_keywords": profile.past_performance_keywords or [],
        "contract_vehicles": profile.contract_vehicles or [],
        "business_size": profile.business_size,
        "socioeconomic_categories": profile.socioeconomic_categories or [],
        "primary_state": profile.primary_state,
        "service_states": profile.service_states or [],
        "min_contract_value": float(profile.min_contract_value) if profile.min_contract_value else None,
        "max_contract_value": float(profile.max_contract_value) if profile.max_contract_value else None,
        "target_agencies": profile.target_agencies or [],
        "excluded_keywords": profile.excluded_keywords or [],
    }


@router.post("/")
async def save_profile(req: ProfileReq, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Create or update the company profile."""
    if user.role != "Admin":
        raise HTTPException(403, "Admin only")

    result = await db.execute(select(CompanyProfile).where(CompanyProfile.is_active == True).limit(1))
    profile = result.scalar_one_or_none()

    if profile:
        # Update existing
        for field, value in req.model_dump(exclude_unset=True).items():
            if field in ("sam_registration_date", "sam_expiration_date"):
                from datetime import date as dt_date
                if value:
                    try:
                        value = dt_date.fromisoformat(value)
                    except (ValueError, TypeError):
                        value = None
            setattr(profile, field, value)
    else:
        profile = CompanyProfile(**{k: v for k, v in req.model_dump().items()
                                     if k not in ("sam_registration_date", "sam_expiration_date")})
        profile.is_active = True
        db.add(profile)

    await db.flush()
    return {"status": "saved", "id": str(profile.id)}


@router.get("/naics-summary")
async def naics_summary(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get just the NAICS codes for matching display."""
    result = await db.execute(select(CompanyProfile).where(CompanyProfile.is_active == True).limit(1))
    profile = result.scalar_one_or_none()
    if not profile:
        return {"naics_codes": [], "gsa_sins": [], "set_asides": []}
    return {
        "naics_codes": profile.naics_codes or [],
        "gsa_sins": profile.gsa_sins or [],
        "set_asides": profile.socioeconomic_categories or [],
        "core_competencies": profile.core_competencies or [],
    }
