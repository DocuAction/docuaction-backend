import uuid
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, Date,
    Numeric, ForeignKey, Enum as SAEnum, JSON, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base
import enum


# ── Enums ────────────────────────────────────────────────────────────────────

class RFQStatus(str, enum.Enum):
    NEW = "New"
    IN_PROGRESS = "In Progress"
    QUOTED = "Quoted"
    SUBMITTED = "Submitted"
    WON = "Won"
    LOST = "Lost"
    CANCELLED = "Cancelled"


class CustomerType(str, enum.Enum):
    GOVERNMENT = "Government"
    COMMERCIAL = "Commercial"


class SetAsideType(str, enum.Enum):
    NONE = "None"
    SB = "SB"
    EIGHT_A = "8(a)"
    WOSB = "WOSB"
    HUBZONE = "HUBZone"
    SDVOSB = "SDVOSB"


class ReviewStatus(str, enum.Enum):
    PENDING = "Pending"
    CONFIRMED = "Confirmed"
    CORRECTED = "Corrected"


class SupplierTier(str, enum.Enum):
    PREFERRED = "Preferred"
    APPROVED = "Approved"
    BACKUP = "Backup"


class LifecycleStatus(str, enum.Enum):
    ACTIVE = "Active"
    END_OF_SALE = "End-of-Sale"
    END_OF_LIFE = "End-of-Life"


class QuoteStatus(str, enum.Enum):
    DRAFT = "Draft"
    FINAL = "Final"
    SUBMITTED = "Submitted"
    SUPERSEDED = "Superseded"


class DealRegStatus(str, enum.Enum):
    ACTIVE = "Active"
    EXPIRED = "Expired"
    USED = "Used"


class FinancialStage(str, enum.Enum):
    QUOTED = "Quoted"
    AWARDED = "Awarded"
    ORDERED = "Ordered"
    INVOICED = "Invoiced"
    PAID = "Paid"


# ── Mixin ────────────────────────────────────────────────────────────────────

class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


# ── Models ───────────────────────────────────────────────────────────────────

class Customer(AuditMixin, Base):
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(500))
    customer_type: Mapped[CustomerType] = mapped_column(SAEnum(CustomerType), default=CustomerType.GOVERNMENT)
    # Organization
    division: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department: Mapped[str | None] = mapped_column(String(500), nullable=True)
    agency_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Primary Contact
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Secondary Contact
    contact2_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact2_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact2_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact2_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Billing Address
    billing_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    billing_zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Shipping Address
    shipping_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    shipping_city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shipping_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    shipping_zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ship_to_zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Mailing Address
    mailing_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mailing_city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mailing_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mailing_zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Government IDs
    cage_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uei_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duns_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Financial
    tax_exempt_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contract_vehicle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True, default="Active")
    rfqs: Mapped[list["RFQ"]] = relationship(back_populates="customer")


class RFQ(AuditMixin, Base):
    __tablename__ = "rfqs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    solicitation_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(1000))
    agency: Mapped[str | None] = mapped_column(String(500), nullable=True)
    naics_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    set_aside_type: Mapped[SetAsideType] = mapped_column(SAEnum(SetAsideType), default=SetAsideType.NONE)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[RFQStatus] = mapped_column(SAEnum(RFQStatus), default=RFQStatus.NEW)
    priority_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_type: Mapped[CustomerType] = mapped_column(SAEnum(CustomerType), default=CustomerType.GOVERNMENT)
    is_taxable: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    raw_document_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Contract officer
    contract_officer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contract_officer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contract_officer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Shipping
    ship_to_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    ship_to_city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ship_to_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    ship_to_zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    shipping_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer: Mapped[Customer | None] = relationship(back_populates="rfqs")
    bom_items: Mapped[list["BOMItem"]] = relationship(back_populates="rfq", cascade="all, delete-orphan")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="rfq", cascade="all, delete-orphan")
    deal_registrations: Mapped[list["DealRegistration"]] = relationship(back_populates="rfq")


class Supplier(AuditMixin, Base):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(500))
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reliability_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_tier: Mapped[SupplierTier] = mapped_column(SAEnum(SupplierTier), default=SupplierTier.APPROVED)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    categories: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # New fields
    supplier_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # Distributor, Reseller, VAR, Integrator
    manufacturer_focus: Mapped[str | None] = mapped_column(Text, nullable=True)  # Comma-separated: Cisco, Dell, HP
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(AuditMixin, Base):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manufacturer: Mapped[str] = mapped_column(String(255))
    part_number: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    msrp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    lifecycle_status: Mapped[LifecycleStatus] = mapped_column(SAEnum(LifecycleStatus), default=LifecycleStatus.ACTIVE)
    replacement_product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True)
    alt_part_numbers: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class BOMItem(AuditMixin, Base):
    __tablename__ = "bom_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rfq_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"))
    line_number: Mapped[int] = mapped_column(Integer)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    part_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_of_measure: Mapped[str] = mapped_column(String(50), default="Each")
    clin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_status: Mapped[ReviewStatus] = mapped_column(SAEnum(ReviewStatus), default=ReviewStatus.PENDING)
    canonical_product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, doc="Optimistic lock version")
    rfq: Mapped[RFQ] = relationship(back_populates="bom_items")


class Quote(AuditMixin, Base):
    __tablename__ = "quotes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rfq_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"))
    quote_number: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[QuoteStatus] = mapped_column(SAEnum(QuoteStatus), default=QuoteStatus.DRAFT)
    total_sell_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    overall_margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True, default=0)
    shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True, default=0)
    document_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rfq: Mapped[RFQ] = relationship(back_populates="quotes")
    line_items: Mapped[list["QuoteLineItem"]] = relationship(back_populates="quote", cascade="all, delete-orphan")


class QuoteLineItem(AuditMixin, Base):
    __tablename__ = "quote_line_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quotes.id"))
    bom_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bom_items.id"), nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    part_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    inbound_freight: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    duty_rate: Mapped[float] = mapped_column(Float, default=0.0)
    handling_fee: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    forex_buffer_pct: Mapped[float] = mapped_column(Float, default=0.0)
    landed_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    margin_pct: Mapped[float] = mapped_column(Float, default=0.0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    override_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    deal_registration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("deal_registrations.id"), nullable=True)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier_price_snapshots.id"), nullable=True)
    quote: Mapped[Quote] = relationship(back_populates="line_items")


class DealRegistration(AuditMixin, Base):
    __tablename__ = "deal_registrations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    oem: Mapped[str] = mapped_column(String(100))
    registration_id: Mapped[str] = mapped_column(String(200))
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    sku_list: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    discount_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    special_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[DealRegStatus] = mapped_column(SAEnum(DealRegStatus), default=DealRegStatus.ACTIVE)
    rfq: Mapped[RFQ | None] = relationship(back_populates="deal_registrations")


class SupplierPriceSnapshot(AuditMixin, Base):
    __tablename__ = "supplier_price_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    part_number: Mapped[str] = mapped_column(String(255))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)


class TaxJurisdiction(AuditMixin, Base):
    __tablename__ = "tax_jurisdictions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state: Mapped[str] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)
    jurisdiction_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Financial(AuditMixin, Base):
    __tablename__ = "financials"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rfq_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"))
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("quotes.id"), nullable=True)
    stage: Mapped[FinancialStage] = mapped_column(SAEnum(FinancialStage), default=FinancialStage.QUOTED)
    po_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    invoice_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_name: Mapped[str] = mapped_column(String(100))
    record_id: Mapped[str] = mapped_column(String(100))
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(String(20))  # INSERT, UPDATE, DELETE
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRole(str, enum.Enum):
    ADMIN = "Admin"
    MANAGER = "Manager"
    SALES = "Sales"
    PRICING = "Pricing"
    EXECUTIVE = "Executive"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.SALES)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectType(str, enum.Enum):
    DEVELOPMENT = "Development"
    CONSULTING = "Consulting"


class ProjectStage(str, enum.Enum):
    INTAKE = "Intake"
    REVIEW = "Review"
    PROPOSAL = "Proposal"
    SUBMITTED = "Submitted"
    AWARDED = "Awarded"
    ACTIVE = "Active"
    COMPLETED = "Completed"
    LOST = "Lost"
    EXPIRED = "Expired"


class RFQSource(str, enum.Enum):
    GSA_EBUY = "GSA eBuy"
    SAM_GOV = "SAM.gov"
    STATE = "State"
    DIRECT_CLIENT = "Direct Client"
    EMAIL = "Email"
    OTHER = "Other"


class DevProject(AuditMixin, Base):
    __tablename__ = "dev_projects"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(1000))
    project_type: Mapped[ProjectType] = mapped_column(SAEnum(ProjectType), default=ProjectType.DEVELOPMENT)
    agency: Mapped[str | None] = mapped_column(String(500), nullable=True)
    solicitation_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    stage: Mapped[ProjectStage] = mapped_column(SAEnum(ProjectStage), default=ProjectStage.INTAKE)
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contract_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount_invoiced: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True, default=0)
    amount_received: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True, default=0)
    document_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class InvoiceStatus(str, enum.Enum):
    DRAFT = "Draft"
    SENT = "Sent"
    PAID = "Paid"
    OVERDUE = "Overdue"
    CANCELLED = "Cancelled"


class Invoice(AuditMixin, Base):
    __tablename__ = "invoices"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[InvoiceStatus] = mapped_column(SAEnum(InvoiceStatus), default=InvoiceStatus.DRAFT)
    # Client info
    client_name: Mapped[str] = mapped_column(String(500))
    client_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Reference
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("dev_projects.id"), nullable=True)
    contract_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    consultant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Totals
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    other_charges: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    # Payment
    payment_terms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id"))
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=1)
    unit: Mapped[str] = mapped_column(String(50), default="Hours")
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    invoice: Mapped[Invoice] = relationship(back_populates="line_items")


class AgencyContact(AuditMixin, Base):
    __tablename__ = "agency_contacts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agency_name: Mapped[str] = mapped_column(String(500))
    contact_name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)

# ── ERP / Finance Models ────────────────────────────────────────────────────

class ContractStatus(str, enum.Enum):
    ACTIVE = "Active"
    COMPLETED = "Completed"
    TERMINATED = "Terminated"
    PENDING = "Pending"


class EmployeeStatus(str, enum.Enum):
    ACTIVE = "Active"
    BENCH = "Bench"
    TERMINATED = "Terminated"
    ONBOARDING = "Onboarding"


class ExpenseCategory(str, enum.Enum):
    SALARY = "Salary"
    BENEFITS = "Benefits"
    IMMIGRATION = "Immigration"
    RENT = "Rent"
    UTILITIES = "Utilities"
    SOFTWARE = "Software"
    TRAVEL = "Travel"
    EQUIPMENT = "Equipment"
    INSURANCE = "Insurance"
    OTHER = "Other"


class Contract(AuditMixin, Base):
    __tablename__ = "contracts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_number: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(1000))
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("dev_projects.id"), nullable=True)
    client_name: Mapped[str] = mapped_column(String(500))
    agency: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contract_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    contract_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[ContractStatus] = mapped_column(SAEnum(ContractStatus), default=ContractStatus.PENDING)
    total_invoiced: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total_received: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total_expenses: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    staffing: Mapped[list["ContractStaffing"]] = relationship(back_populates="contract", cascade="all, delete-orphan")


class Employee(AuditMixin, Base):
    __tablename__ = "employees"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salary: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    billing_rate: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    benefits_cost_monthly: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    immigration_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    immigration_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[EmployeeStatus] = mapped_column(SAEnum(EmployeeStatus), default=EmployeeStatus.ACTIVE)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    utilization_pct: Mapped[float] = mapped_column(Float, default=0.0)
    assignments: Mapped[list["ContractStaffing"]] = relationship(back_populates="employee")


class ContractStaffing(AuditMixin, Base):
    __tablename__ = "contract_staffing"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"))
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing_rate: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    hours_monthly: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=160)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    contract: Mapped[Contract] = relationship(back_populates="staffing")
    employee: Mapped[Employee] = relationship(back_populates="assignments")


class Expense(AuditMixin, Base):
    __tablename__ = "expenses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[ExpenseCategory] = mapped_column(SAEnum(ExpenseCategory), default=ExpenseCategory.OTHER)
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    expense_date: Mapped[date] = mapped_column(Date)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"), nullable=True)
    is_corporate: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Proposal Library ─────────────────────────────────────────────────────────

class ProposalCategory(str, enum.Enum):
    TECHNICAL = "Technical"
    MANAGEMENT = "Management"
    PAST_PERFORMANCE = "Past Performance"
    PRICING = "Pricing"
    COMPLIANCE = "Compliance"
    COVER_LETTER = "Cover Letter"
    EXECUTIVE_SUMMARY = "Executive Summary"
    FULL_PROPOSAL = "Full Proposal"
    TEMPLATE = "Template"


class ProposalLibraryItem(AuditMixin, Base):
    __tablename__ = "proposal_library"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(1000))
    category: Mapped[ProposalCategory] = mapped_column(SAEnum(ProposalCategory), default=ProposalCategory.FULL_PROPOSAL)
    agency: Mapped[str | None] = mapped_column(String(500), nullable=True)
    solicitation_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contract_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    naics_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Won, Lost, Pending
    file_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Supplier Quote Storage ────────────────────────────────────────────────────

class SupplierQuoteFile(AuditMixin, Base):
    __tablename__ = "supplier_quote_files"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rfq_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    file_name: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_quoted: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    quote_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Staffing / ATS Models ─────────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    OPEN = "Open"
    CLOSED = "Closed"
    ON_HOLD = "On Hold"

class ApplicationStatus(str, enum.Enum):
    APPLIED = "Applied"
    SCREENING = "Screening"
    INTERVIEW = "Interview"
    SUBMITTED_TO_CLIENT = "Submitted to Client"
    OFFERED = "Offered"
    HIRED = "Hired"
    REJECTED = "Rejected"

class ClearanceLevel(str, enum.Enum):
    NONE = "None"
    PUBLIC_TRUST = "Public Trust"
    SECRET = "Secret"
    TOP_SECRET = "Top Secret"
    TS_SCI = "TS/SCI"

class JobPosting(AuditMixin, Base):
    __tablename__ = "job_postings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    clearance_required: Mapped[str | None] = mapped_column(String(50), nullable=True)
    skills_required: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus), default=JobStatus.OPEN)
    applications: Mapped[list["Application"]] = relationship(back_populates="job", cascade="all, delete-orphan")

class Candidate(AuditMixin, Base):
    __tablename__ = "candidates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    skills: Mapped[str | None] = mapped_column(Text, nullable=True)
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clearance_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    applications: Mapped[list["Application"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")

class Application(AuditMixin, Base):
    __tablename__ = "applications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id"))
    status: Mapped[ApplicationStatus] = mapped_column(SAEnum(ApplicationStatus), default=ApplicationStatus.APPLIED)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate: Mapped[Candidate] = relationship(back_populates="applications")
    job: Mapped[JobPosting] = relationship(back_populates="applications")


# ── ATS Extended Models ───────────────────────────────────────────────────────

class BenchStatus(str, enum.Enum):
    AVAILABLE = "Available"
    SUBMITTED = "Submitted"
    INTERVIEWING = "Interviewing"
    PLACED = "Placed"
    NOT_AVAILABLE = "Not Available"

class BenchCandidate(AuditMixin, Base):
    __tablename__ = "bench_candidates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"))
    status: Mapped[BenchStatus] = mapped_column(SAEnum(BenchStatus), default=BenchStatus.AVAILABLE)
    available_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    desired_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    visa_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    relocation: Mapped[bool] = mapped_column(Boolean, default=False)
    vendor_submissions: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class ATSActivity(AuditMixin, Base):
    __tablename__ = "ats_activities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


# ── Submission Tracking ───────────────────────────────────────────────────────

class SubmissionStatus(str, enum.Enum):
    SUBMITTED = "Submitted"
    CLIENT_REVIEW = "Client Review"
    INTERVIEW_SCHEDULED = "Interview Scheduled"
    FEEDBACK_PENDING = "Feedback Pending"
    SELECTED = "Selected"
    REJECTED = "Rejected"

class Submission(AuditMixin, Base):
    __tablename__ = "submissions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=True)
    client_name: Mapped[str] = mapped_column(String(500))
    vendor_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submission_type: Mapped[str] = mapped_column(String(50), default="Direct")
    bill_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    pay_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[SubmissionStatus] = mapped_column(SAEnum(SubmissionStatus), default=SubmissionStatus.SUBMITTED)
    submitted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Supplier Quote Tracking ───────────────────────────────────────────────────

class SupplierQuoteStatus(str, enum.Enum):
    PENDING = "Pending"
    RECEIVED = "Received"
    DELAYED = "Delayed"
    NOT_NEEDED = "Not Needed"

class SupplierQuoteRequest(AuditMixin, Base):
    __tablename__ = "supplier_quote_requests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("dev_projects.id"), nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    supplier_name: Mapped[str] = mapped_column(String(500))
    requested_date: Mapped[date] = mapped_column(Date)
    received: Mapped[bool] = mapped_column(Boolean, default=False)
    received_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[SupplierQuoteStatus] = mapped_column(SAEnum(SupplierQuoteStatus), default=SupplierQuoteStatus.PENDING)
    quoted_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Supplier Contacts ─────────────────────────────────────────────────────────

class SupplierContact(AuditMixin, Base):
    __tablename__ = "supplier_contacts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    contact_name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE LAYER — Product Catalog, Price History, Supplier Metrics
# ══════════════════════════════════════════════════════════════════════════════

class ProductCatalog(AuditMixin, Base):
    __tablename__ = "product_catalog"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_number: Mapped[str] = mapped_column(String(255), index=True)
    manufacturer: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    msrp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    last_known_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    taa_compliant: Mapped[bool] = mapped_column(Boolean, default=True)
    lifecycle: Mapped[str | None] = mapped_column(String(50), nullable=True, default="Active")


class PriceHistory(Base):
    __tablename__ = "price_history"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_part_number: Mapped[str] = mapped_column(String(255), index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    sell_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    date_quoted: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    agency: Mapped[str | None] = mapped_column(String(500), nullable=True)
    won: Mapped[bool] = mapped_column(Boolean, default=False)


class SupplierMetric(AuditMixin, Base):
    __tablename__ = "supplier_metrics"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    total_quotes_requested: Mapped[int] = mapped_column(Integer, default=0)
    total_quotes_received: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_days: Mapped[float] = mapped_column(Float, default=0.0)
    total_deals_won: Mapped[int] = mapped_column(Integer, default=0)
    total_deals_lost: Mapped[int] = mapped_column(Integer, default=0)
    win_rate_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_margin_pct: Mapped[float] = mapped_column(Float, default=0.0)
    authorized_brands: Mapped[str | None] = mapped_column(Text, nullable=True)
    reliability_score: Mapped[int] = mapped_column(Integer, default=50)


class TechnicalLibrary(AuditMixin, Base):
    __tablename__ = "technical_library"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(1000))
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(50), default="technical_description")
    warranty_terms: Mapped[str | None] = mapped_column(Text, nullable=True)


class DealStatus(str, enum.Enum):
    INTAKE = "Intake"
    QUOTED = "Quoted"
    SUBMITTED = "Submitted"
    WON = "Won"
    LOST = "Lost"
    ORDERED = "Ordered"
    SHIPPED = "Shipped"
    DELIVERED = "Delivered"
    CANCELLED = "Cancelled"


class PurchaseOrder(AuditMixin, Base):
    __tablename__ = "purchase_orders"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_number: Mapped[str] = mapped_column(String(100))
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("quotes.id"), nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_sell: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[DealStatus] = mapped_column(SAEnum(DealStatus), default=DealStatus.ORDERED)
    ordered_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shipped_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivered_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# DEAL WORKSPACE — Communication Log, Tasks, Agency Metrics
# ══════════════════════════════════════════════════════════════════════════════

class CommunicationLog(AuditMixin, Base):
    __tablename__ = "communication_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), default="outbound")
    comm_type: Mapped[str] = mapped_column(String(20), default="email")
    recipient_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="sent")
    sent_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TaskStatus(str, enum.Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    OVERDUE = "Overdue"

class Task(AuditMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(SAEnum(TaskStatus), default=TaskStatus.PENDING)
    task_type: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AgencyMetric(AuditMixin, Base):
    __tablename__ = "agency_metrics"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agency_name: Mapped[str] = mapped_column(String(500), unique=True)
    total_rfqs: Mapped[int] = mapped_column(Integer, default=0)
    total_won: Mapped[int] = mapped_column(Integer, default=0)
    total_lost: Mapped[int] = mapped_column(Integer, default=0)
    total_quoted_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True, default=0)
    total_won_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True, default=0)
    avg_margin_pct: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate_pct: Mapped[float] = mapped_column(Float, default=0.0)


# ── ATS AI Memory & Outcome Tracking ─────────────────────────────────────────

class AIMemory(AuditMixin, Base):
    __tablename__ = "ai_memory"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=True)
    candidate_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    run_type: Mapped[str | None] = mapped_column(String(50), nullable=True, default="resume_analysis")
    summary: Mapped[str] = mapped_column(Text)
    skills: Mapped[str | None] = mapped_column(Text, nullable=True)
    clearance: Mapped[str | None] = mapped_column(String(50), nullable=True)
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    submission_package: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_result: Mapped[str | None] = mapped_column(Text, nullable=True)

class PlacementOutcome(AuditMixin, Base):
    __tablename__ = "placement_outcomes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(50))
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_bill_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    actual_pay_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    placed_date: Mapped[date | None] = mapped_column(Date, nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# BENCH SALES COMMAND CENTER
# ══════════════════════════════════════════════════════════════════════════════

class OutreachStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    REPLIED = "replied"

class OutreachLog(AuditMixin, Base):
    __tablename__ = "outreach_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"))
    target_company: Mapped[str] = mapped_column(String(500))
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    email_content: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[OutreachStatus] = mapped_column(SAEnum(OutreachStatus), default=OutreachStatus.DRAFT)
    sent_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

class FollowUpStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"

class FollowUpQueue(AuditMixin, Base):
    __tablename__ = "follow_up_queue"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"))
    candidate_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    target_company: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_follow_up_date: Mapped[date] = mapped_column(Date)
    status: Mapped[FollowUpStatus] = mapped_column(SAEnum(FollowUpStatus), default=FollowUpStatus.PENDING)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Support Tickets ───────────────────────────────────────────────────────────

class TicketStatus(str, enum.Enum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"

class TicketPriority(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"

class SupportTicket(AuditMixin, Base):
    __tablename__ = "support_tickets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_number: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), default="General")
    priority: Mapped[TicketPriority] = mapped_column(SAEnum(TicketPriority), default=TicketPriority.MEDIUM)
    status: Mapped[TicketStatus] = mapped_column(SAEnum(TicketStatus), default=TicketStatus.OPEN)
    submitted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
"""
═══════════════════════════════════════════════════════════════════════════════
v7.0 MODEL ADDITIONS — Append these to backend/app/models/__init__.py
═══════════════════════════════════════════════════════════════════════════════
"""

# ══════════════════════════════════════════════════════════════════════════════
# COMPANY PROFILE — NAICS, SINs, Certifications, Capabilities
# ══════════════════════════════════════════════════════════════════════════════

class CompanyProfile(AuditMixin, Base):
    __tablename__ = "company_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str] = mapped_column(String(500))
    dba_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cage_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uei_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duns_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sam_registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sam_expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # NAICS codes (JSON array of {code, description, is_primary, size_standard})
    naics_codes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # GSA SINs (JSON array of {sin, description, contract_number})
    gsa_sins: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # PSC codes (JSON array of {code, description})
    psc_codes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Certifications (JSON array of {type, status, expiration_date})
    certifications: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Capabilities
    capabilities_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    core_competencies: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # List of strings
    past_performance_keywords: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Contract vehicles
    contract_vehicles: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Small business info
    business_size: Mapped[str | None] = mapped_column(String(50), nullable=True)  # Small, Large, Other
    socioeconomic_categories: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 8a, HUBZone, SDVOSB, WOSB, etc
    # Geographic
    primary_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    service_states: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # List of state codes
    # Matching preferences
    min_contract_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    max_contract_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    target_agencies: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Preferred agencies list
    excluded_keywords: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Keywords to skip
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ══════════════════════════════════════════════════════════════════════════════
# OPPORTUNITIES — Federal, State, Local opportunity tracking
# ══════════════════════════════════════════════════════════════════════════════

class OpportunitySource(str, enum.Enum):
    SAM_GOV = "SAM.gov"
    STATE = "State"
    LOCAL = "Local"
    GSA_EBUY = "GSA eBuy"
    GRANTS_GOV = "Grants.gov"
    MANUAL = "Manual"

class OpportunityType(str, enum.Enum):
    PRESOLICITATION = "Presolicitation"
    SOLICITATION = "Combined Synopsis/Solicitation"
    SOURCES_SOUGHT = "Sources Sought"
    AWARD = "Award Notice"
    SPECIAL = "Special Notice"
    MODIFICATION = "Modification"
    JUSTIFICATION = "Justification"

class OpportunityStatus(str, enum.Enum):
    NEW = "New"
    REVIEWING = "Reviewing"
    MATCHED = "Matched"
    PURSUING = "Pursuing"
    BID_SUBMITTED = "Bid Submitted"
    WON = "Won"
    LOST = "Lost"
    NO_BID = "No Bid"
    EXPIRED = "Expired"


class Opportunity(AuditMixin, Base):
    __tablename__ = "opportunities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # External IDs
    notice_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    solicitation_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Core info
    title: Mapped[str] = mapped_column(String(2000))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[OpportunitySource] = mapped_column(SAEnum(OpportunitySource), default=OpportunitySource.SAM_GOV)
    opportunity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Agency
    department: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sub_tier: Mapped[str | None] = mapped_column(String(500), nullable=True)
    office: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Classification
    naics_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    classification_code: Mapped[str | None] = mapped_column(String(20), nullable=True)  # PSC code
    set_aside: Mapped[str | None] = mapped_column(String(200), nullable=True)
    set_aside_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Dates
    posted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    response_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Value
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Award info
    award_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    award_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    awardee_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    awardee_uei: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Contact
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Location
    place_of_performance_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    place_of_performance_city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    place_of_performance_zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Links
    sam_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    resource_links: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Internal tracking
    status: Mapped[OpportunityStatus] = mapped_column(SAEnum(OpportunityStatus), default=OpportunityStatus.NEW)
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    match_reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Why it matched
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Link to RFQ if pursued
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=True)
    # Full JSON from SAM.gov for reference
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SavedSearch(AuditMixin, Base):
    """Saved opportunity search filters for alerts"""
    __tablename__ = "saved_searches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(500))
    search_type: Mapped[str] = mapped_column(String(50), default="federal")  # federal, state, local
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    naics_codes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    set_aside_types: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agencies: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    states: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
