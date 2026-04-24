from pydantic import BaseModel, Field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from typing import Optional
from app.models import (
    RFQStatus, CustomerType, SetAsideType, ReviewStatus,
    SupplierTier, LifecycleStatus, QuoteStatus, DealRegStatus
)


# ── RFQ ──────────────────────────────────────────────────────────────────────

class RFQCreate(BaseModel):
    title: str
    source: str | None = "Manual"
    solicitation_number: str | None = None
    agency: str | None = None
    naics_code: str | None = None
    set_aside_type: SetAsideType = SetAsideType.NONE
    due_date: date | None = None
    estimated_value: Decimal | None = None
    assigned_to: str | None = None
    customer_type: CustomerType = CustomerType.GOVERNMENT
    is_taxable: bool = False
    customer_id: UUID | None = None
    contract_officer_name: str | None = None
    contract_officer_email: str | None = None
    contract_officer_phone: str | None = None
    department: str | None = None
    ship_to_address: str | None = None
    ship_to_city: str | None = None
    ship_to_state: str | None = None
    ship_to_zip: str | None = None
    shipping_method: str | None = None


class RFQResponse(RFQCreate):
    id: UUID
    status: RFQStatus
    priority_score: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


# ── BOM ──────────────────────────────────────────────────────────────────────

class BOMItemCreate(BaseModel):
    line_number: int
    manufacturer: str | None = None
    part_number: str | None = None
    description: str | None = None
    quantity: int = 1
    unit_of_measure: str = "Each"
    clin: str | None = None


class BOMItemResponse(BOMItemCreate):
    id: UUID
    rfq_id: UUID
    ai_confidence: int | None = None
    review_status: ReviewStatus
    version: int

    class Config:
        from_attributes = True


class BOMUpload(BaseModel):
    items: list[BOMItemCreate]


# ── Supplier ─────────────────────────────────────────────────────────────────

class SupplierCreate(BaseModel):
    name: str
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    payment_terms: str | None = None
    preferred_tier: SupplierTier = SupplierTier.APPROVED
    country: str | None = "US"
    categories: dict | None = None
    supplier_type: str | None = None
    manufacturer_focus: str | None = None
    website: str | None = None
    notes: str | None = None


class SupplierResponse(SupplierCreate):
    id: UUID
    reliability_score: int | None = None
    is_active: bool | None = True
    created_at: datetime

    class Config:
        from_attributes = True


# ── Pricing ──────────────────────────────────────────────────────────────────

class PricingLineInput(BaseModel):
    bom_item_id: UUID | None = None
    supplier_id: UUID | None = None
    part_number: str | None = None
    quantity: int = 1
    unit_cost: Decimal
    inbound_freight: Decimal = Decimal("0")
    duty_rate: float = 0.0
    handling_fee: Decimal = Decimal("0")
    target_margin_pct: float = 20.0
    forex_buffer_pct: float = 0.0
    is_international: bool = False
    deal_registration_id: UUID | None = None
    override_sell_price: Decimal | None = None
    override_justification: str | None = None


class PricingRequest(BaseModel):
    rfq_id: UUID
    is_taxable: bool = False
    ship_to_zip: str | None = None
    lines: list[PricingLineInput]
    min_margin_pct: float | None = None
    payment_method: str | None = "auto"  # auto, credit_card, direct, ach


class PricingLineResult(BaseModel):
    bom_item_id: UUID | None = None
    unit_cost: Decimal
    landed_cost_per_unit: Decimal
    cost_basis: Decimal
    sell_price_per_unit: Decimal
    sell_price_total: Decimal
    margin_pct: float
    gross_profit_per_unit: Decimal
    tax_per_unit: Decimal = Decimal("0")
    is_override: bool = False
    guardrail_status: str  # "PASS", "WARNING", "BLOCKED"
    guardrail_message: str | None = None
    deal_reg_applied: bool = False


class PricingResponse(BaseModel):
    rfq_id: UUID
    lines: list[PricingLineResult]
    total_cost: Decimal
    total_sell: Decimal
    total_tax: Decimal
    blended_margin_pct: float
    any_blocked: bool


# ── Quote ────────────────────────────────────────────────────────────────────

class QuoteCreateRequest(BaseModel):
    rfq_id: UUID
    lines: list[PricingLineInput]
    min_margin_pct: float | None = None


class QuoteLineItemResponse(BaseModel):
    id: UUID
    bom_item_id: UUID | None
    supplier_id: UUID | None
    quantity: int
    unit_cost: Decimal
    landed_cost: Decimal
    sell_price: Decimal
    margin_pct: float
    tax_amount: Decimal
    is_override: bool
    override_justification: str | None

    class Config:
        from_attributes = True


class QuoteResponse(BaseModel):
    id: UUID
    rfq_id: UUID
    quote_number: str | None = None
    version: int
    status: QuoteStatus
    total_sell_price: Decimal | None
    total_cost: Decimal | None
    overall_margin_pct: float | None
    total_tax: Decimal | None
    shipping_cost: Decimal | None = None
    is_locked: bool
    created_at: datetime
    line_items: list[QuoteLineItemResponse] = []

    class Config:
        from_attributes = True


# ── Deal Registration ────────────────────────────────────────────────────────

class DealRegCreate(BaseModel):
    oem: str
    registration_id: str
    rfq_id: UUID | None = None
    sku_list: dict | None = None
    discount_pct: float | None = None
    special_unit_price: Decimal | None = None
    expiration_date: date | None = None


class DealRegResponse(DealRegCreate):
    id: UUID
    status: DealRegStatus
    created_at: datetime

    class Config:
        from_attributes = True


# ── Customer ─────────────────────────────────────────────────────────────────

class CustomerCreate(BaseModel):
    name: str
    customer_type: CustomerType = CustomerType.GOVERNMENT
    tax_exempt_id: str | None = None
    credit_limit: Decimal | None = None
    payment_terms: str | None = None
    billing_address: str | None = None
    ship_to_zip: str | None = None


class CustomerResponse(CustomerCreate):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True
