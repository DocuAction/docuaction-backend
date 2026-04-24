from fastapi import APIRouter
from app.schemas import PricingRequest, PricingResponse
from app.services.pricing import price_request

router = APIRouter(prefix="/pricing", tags=["Pricing"])


@router.post("/calculate", response_model=PricingResponse)
async def calculate_pricing(req: PricingRequest):
    """
    Calculate pricing for all line items using the full pricing waterfall:
    Landed Cost → Forex Buffer → Deal Reg → Sell Price → Guardrail → Tax
    """
    return price_request(req)
