from fastapi import APIRouter, Depends
from app.core.security import require_role
from app.schemas import PricingRequest, PricingResponse
from app.services.pricing import price_request

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/pricing", tags=["Pricing"], dependencies=[Depends(require_role("contributor"))])
@router.post("/calculate", response_model=PricingResponse)
async def calculate_pricing(req: PricingRequest):
    """
    Calculate pricing for all line items using the full pricing waterfall:
    Landed Cost → Forex Buffer → Deal Reg → Sell Price → Guardrail → Tax
    """
    return price_request(req)
