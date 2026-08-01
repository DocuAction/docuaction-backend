from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import (
    Contract, Employee, ContractStaffing, Expense,
    ContractStatus, EmployeeStatus, ExpenseCategory, RFQ
)
from app.services.auth import get_current_user, require_role

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/finance", tags=["Finance & ERP"], dependencies=[Depends(require_role("contributor"))])
# ── Schemas ──

class ContractCreate(BaseModel):
    title: str
    client_name: str
    rfq_id: UUID | None = None
    project_id: UUID | None = None
    agency: str | None = None
    contract_value: Decimal = Decimal("0")
    contract_type: str | None = "FFP"
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = None

class EmployeeCreate(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    title: str | None = None
    department: str | None = None
    salary: Decimal = Decimal("0")
    billing_rate: Decimal = Decimal("0")
    benefits_cost_monthly: Decimal = Decimal("0")
    immigration_cost: Decimal = Decimal("0")
    immigration_type: str | None = None
    start_date: date | None = None

class StaffingCreate(BaseModel):
    contract_id: UUID
    employee_id: UUID
    role: str | None = None
    billing_rate: Decimal = Decimal("0")
    hours_monthly: Decimal = Decimal("160")
    start_date: date | None = None
    end_date: date | None = None

class ExpenseCreate(BaseModel):
    category: ExpenseCategory = ExpenseCategory.OTHER
    description: str
    amount: Decimal = Decimal("0")
    expense_date: date
    contract_id: UUID | None = None
    employee_id: UUID | None = None
    is_corporate: bool = False
    notes: str | None = None


# ── Contracts ──

@router.post("/contracts", status_code=201)
async def create_contract(payload: ContractCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    year = datetime.now().year
    count = await db.execute(select(func.count(Contract.id)))
    seq = count.scalar() + 1
    contract_number = f"AGT-C-{year}-{str(seq).zfill(4)}"

    contract = Contract(contract_number=contract_number, **payload.model_dump())
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return {"id": str(contract.id), "contract_number": contract_number, **payload.model_dump(mode="json")}


@router.get("/contracts")
async def list_contracts(status: str | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(Contract).options(selectinload(Contract.staffing)).order_by(Contract.created_at.desc())
    if status:
        q = q.where(Contract.status == status)
    result = await db.execute(q)
    contracts = result.scalars().unique().all()
    return [_contract_dict(c) for c in contracts]


@router.get("/contracts/{cid}")
async def get_contract(cid: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contract).where(Contract.id == cid).options(selectinload(Contract.staffing)))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contract not found")
    return _contract_dict(c)


@router.patch("/contracts/{cid}")
async def update_contract(cid: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contract).where(Contract.id == cid))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(c, k) and k not in ('id', 'contract_number'):
            setattr(c, k, v)
    await db.flush()
    await db.refresh(c)
    return {"status": "updated", "contract_number": c.contract_number}


def _contract_dict(c):
    return {
        "id": str(c.id), "contract_number": c.contract_number, "title": c.title,
        "client_name": c.client_name, "agency": c.agency,
        "contract_value": float(c.contract_value), "contract_type": c.contract_type,
        "start_date": str(c.start_date) if c.start_date else None,
        "end_date": str(c.end_date) if c.end_date else None,
        "status": c.status, "total_invoiced": float(c.total_invoiced),
        "total_received": float(c.total_received), "total_expenses": float(c.total_expenses),
        "notes": c.notes, "created_at": str(c.created_at) if c.created_at else None,
        "staff_count": len(c.staffing) if c.staffing else 0,
    }


# ── Employees ──

@router.post("/employees", status_code=201)
async def create_employee(payload: EmployeeCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    emp = Employee(**payload.model_dump())
    db.add(emp)
    await db.flush()
    await db.refresh(emp)
    return _emp_dict(emp)


@router.get("/employees")
async def list_employees(status: str | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(Employee).order_by(Employee.full_name)
    if status:
        q = q.where(Employee.status == status)
    result = await db.execute(q)
    return [_emp_dict(e) for e in result.scalars().all()]


@router.get("/employees/{eid}")
async def get_employee(eid: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Employee).where(Employee.id == eid))
    e = result.scalar_one_or_none()
    if not e:
        raise HTTPException(404)
    return _emp_dict(e)


@router.patch("/employees/{eid}")
async def update_employee(eid: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Employee).where(Employee.id == eid))
    e = result.scalar_one_or_none()
    if not e:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(e, k) and k != 'id':
            setattr(e, k, v)
    await db.flush()
    await db.refresh(e)
    return _emp_dict(e)


def _emp_dict(e):
    monthly_cost = float(e.salary) / 12 + float(e.benefits_cost_monthly)
    annual_cost = float(e.salary) + float(e.benefits_cost_monthly) * 12 + float(e.immigration_cost)
    return {
        "id": str(e.id), "full_name": e.full_name, "email": e.email, "phone": e.phone,
        "title": e.title, "department": e.department,
        "salary": float(e.salary), "billing_rate": float(e.billing_rate),
        "benefits_cost_monthly": float(e.benefits_cost_monthly),
        "immigration_cost": float(e.immigration_cost), "immigration_type": e.immigration_type,
        "status": e.status, "start_date": str(e.start_date) if e.start_date else None,
        "utilization_pct": e.utilization_pct,
        "monthly_cost": round(monthly_cost, 2), "annual_cost": round(annual_cost, 2),
        "created_at": str(e.created_at) if e.created_at else None,
    }


# ── Staffing Assignments ──

@router.post("/staffing", status_code=201)
async def assign_staff(payload: StaffingCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    assignment = ContractStaffing(**payload.model_dump())
    db.add(assignment)
    await db.flush()
    # Update employee utilization
    emp_result = await db.execute(select(Employee).where(Employee.id == payload.employee_id))
    emp = emp_result.scalar_one_or_none()
    if emp:
        active_count = await db.execute(
            select(func.count(ContractStaffing.id)).where(
                ContractStaffing.employee_id == payload.employee_id,
                ContractStaffing.is_active == True
            )
        )
        emp.utilization_pct = min(float(active_count.scalar()) * 100, 100)
        emp.status = EmployeeStatus.ACTIVE
        await db.flush()
    return {"status": "assigned", "id": str(assignment.id)}


@router.get("/staffing")
async def list_staffing(contract_id: UUID | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(ContractStaffing)
    if contract_id:
        q = q.where(ContractStaffing.contract_id == contract_id)
    result = await db.execute(q)
    return [{
        "id": str(s.id), "contract_id": str(s.contract_id), "employee_id": str(s.employee_id),
        "role": s.role, "billing_rate": float(s.billing_rate), "hours_monthly": float(s.hours_monthly),
        "start_date": str(s.start_date) if s.start_date else None, "is_active": s.is_active,
    } for s in result.scalars().all()]


# ── Expenses ──

@router.post("/expenses", status_code=201)
async def create_expense(payload: ExpenseCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    exp = Expense(**payload.model_dump())
    db.add(exp)
    await db.flush()
    await db.refresh(exp)
    return {"id": str(exp.id), "category": exp.category, "amount": float(exp.amount)}


@router.get("/expenses")
async def list_expenses(
    contract_id: UUID | None = None,
    is_corporate: bool | None = None,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    q = select(Expense).order_by(Expense.expense_date.desc())
    if contract_id:
        q = q.where(Expense.contract_id == contract_id)
    if is_corporate is not None:
        q = q.where(Expense.is_corporate == is_corporate)
    result = await db.execute(q)
    return [{
        "id": str(e.id), "category": e.category, "description": e.description,
        "amount": float(e.amount), "expense_date": str(e.expense_date),
        "contract_id": str(e.contract_id) if e.contract_id else None,
        "is_corporate": e.is_corporate, "notes": e.notes,
    } for e in result.scalars().all()]


# ── Profit Engine ──

@router.get("/profit-summary")
async def profit_summary(admin=Depends(require_role("Admin")), db: AsyncSession = Depends(get_db)):
    """3-Level Profit Calculation"""
    # Level 1: Gross Margin (from quotes)
    from app.models import Quote
    quote_sell = await db.execute(select(func.sum(Quote.total_sell_price)))
    quote_cost = await db.execute(select(func.sum(Quote.total_cost)))
    total_sell = float(quote_sell.scalar() or 0)
    total_cost = float(quote_cost.scalar() or 0)
    gross_margin = total_sell - total_cost

    # Level 2: Project Profit
    contract_revenue = await db.execute(select(func.sum(Contract.total_received)))
    direct_expenses = await db.execute(select(func.sum(Expense.amount)).where(Expense.is_corporate == False))
    project_revenue = float(contract_revenue.scalar() or 0)
    project_expenses = float(direct_expenses.scalar() or 0)
    project_profit = project_revenue - project_expenses

    # Level 3: Net Corporate Profit
    corporate_expenses = await db.execute(select(func.sum(Expense.amount)).where(Expense.is_corporate == True))
    bench_employees = await db.execute(select(Employee).where(Employee.status == EmployeeStatus.BENCH))
    bench_cost = sum(float(e.salary) / 12 + float(e.benefits_cost_monthly) for e in bench_employees.scalars().all())
    corp_exp = float(corporate_expenses.scalar() or 0)
    net_profit = project_profit - bench_cost - corp_exp

    # Counts
    active_contracts = await db.execute(select(func.count(Contract.id)).where(Contract.status == ContractStatus.ACTIVE))
    total_employees = await db.execute(select(func.count(Employee.id)))
    bench_count = await db.execute(select(func.count(Employee.id)).where(Employee.status == EmployeeStatus.BENCH))
    active_emp = await db.execute(select(func.count(Employee.id)).where(Employee.status == EmployeeStatus.ACTIVE))

    return {
        "level_1_gross_margin": {"total_sell": total_sell, "total_cost": total_cost, "gross_margin": round(gross_margin, 2)},
        "level_2_project_profit": {"contract_revenue": project_revenue, "direct_expenses": project_expenses, "project_profit": round(project_profit, 2)},
        "level_3_net_profit": {"project_profit": round(project_profit, 2), "bench_cost_monthly": round(bench_cost, 2), "corporate_expenses": corp_exp, "net_profit": round(net_profit, 2)},
        "counts": {
            "active_contracts": active_contracts.scalar(),
            "total_employees": total_employees.scalar(),
            "active_employees": active_emp.scalar(),
            "bench_employees": bench_count.scalar(),
        }
    }


# ── Auto-create contract from Won RFQ ──

@router.post("/contracts/from-rfq/{rfq_id}", status_code=201)
async def create_contract_from_rfq(rfq_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RFQ).where(RFQ.id == rfq_id))
    rfq = result.scalar_one_or_none()
    if not rfq:
        raise HTTPException(404, "RFQ not found")

    year = datetime.now().year
    count = await db.execute(select(func.count(Contract.id)))
    seq = count.scalar() + 1

    contract = Contract(
        contract_number=f"AGT-C-{year}-{str(seq).zfill(4)}",
        title=rfq.title,
        rfq_id=rfq.id,
        client_name=rfq.agency or "TBD",
        agency=rfq.agency,
        contract_value=rfq.estimated_value or 0,
        contract_type="FFP",
        status=ContractStatus.ACTIVE,
    )
    db.add(contract)
    rfq.status = "Won"
    await db.flush()
    await db.refresh(contract)
    return {"contract_number": contract.contract_number, "id": str(contract.id), "status": "Created from RFQ"}
