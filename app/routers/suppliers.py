from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import Supplier
from app.schemas import SupplierCreate, SupplierResponse
import csv
import io

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/suppliers", tags=["Suppliers"], dependencies=[Depends(require_role("contributor"))])
# ── CREATE ──
@router.post("", response_model=SupplierResponse, status_code=201)
async def create_supplier(payload: SupplierCreate, db: AsyncSession = Depends(get_db)):
    supplier = Supplier(**payload.model_dump())
    db.add(supplier)
    await db.flush()
    await db.refresh(supplier)
    return supplier


# ── LIST (with search/filter) ──
@router.get("", response_model=list[SupplierResponse])
async def list_suppliers(
    search: str | None = None,
    supplier_type: str | None = None,
    manufacturer: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    q = select(Supplier).order_by(Supplier.name).limit(limit)
    if search:
        q = q.where(or_(
            Supplier.name.ilike(f"%{search}%"),
            Supplier.contact_name.ilike(f"%{search}%"),
            Supplier.contact_email.ilike(f"%{search}%"),
            Supplier.manufacturer_focus.ilike(f"%{search}%"),
            Supplier.notes.ilike(f"%{search}%"),
        ))
    if supplier_type:
        q = q.where(Supplier.supplier_type.ilike(f"%{supplier_type}%"))
    if manufacturer:
        q = q.where(Supplier.manufacturer_focus.ilike(f"%{manufacturer}%"))
    result = await db.execute(q)
    return result.scalars().all()


# ── SEED (must be before /{supplier_id}) ──
SEED_SUPPLIERS = [
    {"name": "TD SYNNEX", "type": "Distributor", "mfr": "All", "phone": "800-756-9888", "web": "https://www.tdsynnex.com", "notes": "Primary distributor"},
    {"name": "Ingram Micro", "type": "Distributor", "mfr": "All", "phone": "800-456-8000", "web": "https://www.ingrammicro.com", "notes": "Primary distributor"},
    {"name": "D&H Distributing", "type": "Distributor", "mfr": "All", "phone": "800-340-1001", "web": "https://www.dandh.com", "notes": "SMB distributor"},
    {"name": "Arrow Electronics", "type": "Distributor", "mfr": "All", "phone": "303-824-4000", "web": "https://www.arrow.com", "notes": "Enterprise distributor"},
    {"name": "Carahsoft", "type": "Distributor", "mfr": "Software/IT", "phone": "888-662-2724", "web": "https://www.carahsoft.com", "notes": "Gov distributor"},
    {"name": "ASI Corp", "type": "Distributor", "mfr": "All", "phone": "510-226-8000", "web": "https://www.asi.com", "notes": "IT hardware distributor"},
    {"name": "Exertis Almo", "type": "Distributor", "mfr": "AV/IT", "phone": "888-420-2566", "web": "https://www.exertisalmo.com", "notes": "AV + IT distributor"},
    {"name": "ScanSource", "type": "Distributor", "mfr": "Specialty Tech", "phone": "800-944-2432", "web": "https://www.scansource.com", "notes": "Specialty distributor"},
    {"name": "CDW Government", "type": "Reseller", "mfr": "Cisco/Dell/HP", "phone": "800-808-4239", "web": "https://www.cdwg.com", "notes": "Federal reseller"},
    {"name": "SHI International", "type": "Reseller", "mfr": "All", "phone": "888-764-8888", "web": "https://www.shi.com", "notes": "Large enterprise reseller"},
    {"name": "Insight Enterprises", "type": "Reseller", "mfr": "All", "phone": "800-467-4448", "web": "https://www.insight.com", "notes": "Enterprise IT provider"},
    {"name": "Zones LLC", "type": "Reseller", "mfr": "All", "phone": "800-408-9663", "web": "https://www.zones.com", "notes": "IT procurement"},
    {"name": "Connection Public Sector", "type": "Reseller", "mfr": "All", "phone": "800-800-0019", "web": "https://www.connection.com", "notes": "Gov IT supplier"},
    {"name": "World Wide Technology", "type": "Integrator", "mfr": "Cisco/Enterprise", "phone": "314-919-3700", "web": "https://www.wwt.com", "notes": "Large integrator"},
    {"name": "Presidio", "type": "Integrator", "mfr": "Cisco/Cloud", "phone": "800-644-8989", "web": "https://www.presidio.com", "notes": "IT solutions"},
    {"name": "ePlus", "type": "Integrator", "mfr": "All", "phone": "888-482-1122", "web": "https://www.eplus.com", "notes": "Tech solutions"},
    {"name": "Trace3", "type": "VAR", "mfr": "Cisco/Cloud", "phone": "949-333-2300", "web": "https://www.trace3.com", "notes": "Cisco partner"},
    {"name": "Black Box", "type": "VAR", "mfr": "Networking", "phone": "800-335-0228", "web": "https://www.blackbox.com", "notes": "Infra provider"},
    {"name": "ConvergeOne", "type": "VAR", "mfr": "Cisco", "phone": "888-334-1515", "web": "https://www.convergeone.com", "notes": "Cisco integrator"},
    {"name": "Sterling Computers", "type": "Reseller", "mfr": "Dell/HP", "phone": "800-544-9727", "web": "https://www.sterling.com", "notes": "Federal IT"},
    {"name": "GovConnection", "type": "Reseller", "mfr": "All", "phone": "800-800-0019", "web": "https://www.govconnection.com", "notes": "Public sector"},
    {"name": "Sirius Computer Solutions", "type": "Integrator", "mfr": "All", "phone": "800-460-1237", "web": "https://www.siriuscom.com", "notes": "Enterprise IT"},
    {"name": "Technologent", "type": "Reseller", "mfr": "All", "phone": "949-453-4800", "web": "https://www.technologent.com", "notes": "Fast response"},
    {"name": "DynTek", "type": "Reseller", "mfr": "All", "phone": "800-674-0888", "web": "https://www.dyntek.com", "notes": "IT solutions"},
    {"name": "Provantage", "type": "Reseller", "mfr": "Hardware", "phone": "800-336-1166", "web": "https://www.provantage.com", "notes": "Fast quoting"},
    {"name": "Newegg Business", "type": "Reseller", "mfr": "Hardware", "phone": "888-482-6678", "web": "https://www.neweggbusiness.com", "notes": "Online supplier"},
    {"name": "iGov", "type": "Reseller", "mfr": "Federal IT", "phone": "813-374-3390", "web": "https://www.igov.com", "notes": "Gov supplier"},
    {"name": "B&H Business", "type": "Reseller", "mfr": "Electronics", "phone": "800-606-6969", "web": "https://www.bhphotovideo.com", "notes": "Electronics supplier"},
]


@router.post("/seed")
async def seed_suppliers(db: AsyncSession = Depends(get_db)):
    """Load 28 standard IT suppliers. Skips duplicates."""
    imported = 0
    skipped = 0
    for s in SEED_SUPPLIERS:
        existing = await db.execute(select(Supplier).where(func.lower(Supplier.name) == s['name'].lower()))
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        supplier = Supplier(
            name=s['name'], supplier_type=s['type'], manufacturer_focus=s['mfr'],
            contact_phone=s['phone'], website=s['web'], notes=s['notes'],
            preferred_tier='Preferred' if s['type'] == 'Distributor' else 'Approved',
            country='US', payment_terms='Net 30', is_active=True,
        )
        db.add(supplier)
        imported += 1
    await db.flush()
    return {"imported": imported, "skipped": skipped, "total_in_seed": len(SEED_SUPPLIERS)}


# ── STATS ──
@router.get("/stats/summary")
async def supplier_stats(db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(Supplier.id)))
    by_type = await db.execute(select(Supplier.supplier_type, func.count(Supplier.id)).group_by(Supplier.supplier_type))
    return {"total_suppliers": total.scalar(), "by_type": {row[0] or 'Unknown': row[1] for row in by_type.all()}}


# ── SEARCH ──
@router.get("/search/{query}")
async def search_suppliers_by_query(query: str, db: AsyncSession = Depends(get_db)):
    """Auto-search for BOM page supplier lookup."""
    q = select(Supplier).where(or_(
        Supplier.name.ilike(f"%{query}%"),
        Supplier.manufacturer_focus.ilike(f"%{query}%"),
        Supplier.contact_name.ilike(f"%{query}%"),
        Supplier.notes.ilike(f"%{query}%"),
    )).order_by(Supplier.preferred_tier).limit(10)
    result = await db.execute(q)
    return [{
        "id": str(s.id), "name": s.name,
        "supplier_type": getattr(s, 'supplier_type', None) or '',
        "manufacturer_focus": getattr(s, 'manufacturer_focus', None) or '',
        "contact_name": s.contact_name or '', "contact_email": s.contact_email or '',
        "contact_phone": s.contact_phone or '', "website": getattr(s, 'website', None) or '',
        "preferred_tier": s.preferred_tier, "payment_terms": s.payment_terms or '',
        "notes": getattr(s, 'notes', None) or '',
    } for s in result.scalars().all()]


# ── MANUFACTURER LOOKUP ──
@router.get("/lookup/manufacturer/{manufacturer}")
async def lookup_by_manufacturer(manufacturer: str, db: AsyncSession = Depends(get_db)):
    """Find suppliers that carry a specific manufacturer."""
    q = select(Supplier).where(or_(
        Supplier.manufacturer_focus.ilike(f"%{manufacturer}%"),
        Supplier.name.ilike(f"%{manufacturer}%"),
    )).order_by(Supplier.preferred_tier).limit(20)
    result = await db.execute(q)
    suppliers = result.scalars().all()
    return {
        "manufacturer": manufacturer, "count": len(suppliers),
        "suppliers": [{
            "id": str(s.id), "name": s.name, "type": getattr(s, 'supplier_type', None),
            "manufacturer_focus": getattr(s, 'manufacturer_focus', None),
            "contact_name": s.contact_name, "contact_email": s.contact_email,
            "contact_phone": s.contact_phone, "website": getattr(s, 'website', None),
            "tier": s.preferred_tier,
        } for s in suppliers]
    }


# ── CSV IMPORT ──
@router.post("/import-csv")
async def import_csv(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Import suppliers from CSV. Updates existing by name, inserts new."""
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
            name = (row.get('Company', '') or row.get('name', '') or row.get('supplier_name', '') or row.get('company', '')).strip()
            if not name:
                skipped += 1
                continue

            phone = (row.get('Phone', '') or row.get('phone', '') or row.get('contact_phone', '')).strip()
            supplier_type = (row.get('Category', '') or row.get('category', '') or row.get('supplier_type', '')).strip()
            mfr_focus = (row.get('Manufacturer Focus', '') or row.get('manufacturer_focus', '') or row.get('manufacturer', '')).strip()
            website = (row.get('Website', '') or row.get('website', '')).strip()
            email = (row.get('General Email', '') or row.get('email', '') or row.get('contact_email', '')).strip()
            contact_name = (row.get('Contact Name', '') or row.get('contact_name', '') or row.get('contact', '')).strip()
            notes = (row.get('Notes', '') or row.get('notes', '')).strip()

            existing = await db.execute(select(Supplier).where(func.lower(Supplier.name) == name.lower()))
            ex = existing.scalar_one_or_none()

            if ex:
                if phone: ex.contact_phone = phone
                if email: ex.contact_email = email
                if contact_name: ex.contact_name = contact_name
                if supplier_type: ex.supplier_type = supplier_type
                if mfr_focus: ex.manufacturer_focus = mfr_focus
                if website: ex.website = website
                if notes: ex.notes = notes
                ex.is_active = True
                updated += 1
            else:
                tier = 'Preferred' if supplier_type == 'Distributor' else 'Approved'
                db.add(Supplier(
                    name=name, contact_name=contact_name or None, contact_email=email or None,
                    contact_phone=phone or None, supplier_type=supplier_type or None,
                    manufacturer_focus=mfr_focus or None, website=website or None,
                    notes=notes or None, preferred_tier=tier, country='US',
                    payment_terms='Net 30', is_active=True,
                ))
                imported += 1
        except Exception as e:
            errors.append(f"Row {i+1}: {str(e)}")

    await db.flush()
    return {"imported": imported, "updated": updated, "skipped": skipped, "errors": errors[:10], "total_processed": imported + updated + skipped}


# ── SUPPLIER CONTACTS (multi-contact per supplier) ──

@router.get("/contacts/by-supplier/{supplier_id}")
async def list_supplier_contacts(supplier_id: UUID, db: AsyncSession = Depends(get_db)):
    from app.models import SupplierContact
    result = await db.execute(
        select(SupplierContact).where(SupplierContact.supplier_id == supplier_id)
        .order_by(SupplierContact.is_primary.desc(), SupplierContact.contact_name)
    )
    return [{
        "id": str(c.id), "supplier_id": str(c.supplier_id),
        "contact_name": c.contact_name, "title": c.title,
        "email": c.email, "phone": c.phone,
        "is_primary": c.is_primary, "notes": c.notes,
    } for c in result.scalars().all()]


@router.post("/contacts")
async def add_supplier_contact(payload: dict, db: AsyncSession = Depends(get_db)):
    from app.models import SupplierContact
    contact = SupplierContact(
        supplier_id=payload['supplier_id'],
        contact_name=payload['contact_name'],
        title=payload.get('title'),
        email=payload.get('email'),
        phone=payload.get('phone'),
        is_primary=payload.get('is_primary', False),
        notes=payload.get('notes'),
    )
    db.add(contact)
    await db.flush()
    return {"id": str(contact.id), "status": "created"}


@router.patch("/contacts/{contact_id}")
async def update_supplier_contact(contact_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)):
    from app.models import SupplierContact
    result = await db.execute(select(SupplierContact).where(SupplierContact.id == contact_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    for k, v in payload.items():
        if hasattr(c, k) and k != 'id':
            setattr(c, k, v)
    await db.flush()
    return {"status": "updated"}


@router.delete("/contacts/{contact_id}")
async def delete_supplier_contact(contact_id: UUID, db: AsyncSession = Depends(get_db)):
    from app.models import SupplierContact
    result = await db.execute(select(SupplierContact).where(SupplierContact.id == contact_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    await db.delete(c)
    await db.flush()
    return {"status": "deleted"}


# ── GET BY ID (MUST BE LAST — catches all /{path}) ──
@router.get("/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(supplier_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Supplier not found")
    return s
