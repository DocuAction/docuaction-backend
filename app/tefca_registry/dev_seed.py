"""Development seed data for the TEFCA registry.

The dev registry holds zero entities, which blocked 10 functional test cases in
the 2026-07-30 assessment and makes the module impossible to demo. This builds a
small, deliberately varied set through the ordinary CSV import path — not by
INSERTing rows — so seeding exercises the same parser, NPI validation and audit
writes that a real import does. A seed that bypassed the import path could pass
while the import path was broken.

The data is synthetic and labelled as such. Two NPIs fail the CMS check digit ON
PURPOSE, so the flag-don't-reject behaviour has something to flag and a reviewer
can see what a flagged entity looks like.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.npi_validator import make_valid_npi
from app.tefca_registry import models as reg

logger = logging.getLogger(__name__)


def _rows() -> list[dict]:
    """(TEFCAID, HCID, EntityName, EntityLevel, NPI, State, City, status)

    NPIs are generated from a 9-digit base with a computed check digit, so they
    are structurally valid without colliding with a real provider's number. The
    two marked INVALID keep a wrong digit on purpose.
    """
    v = make_valid_npi
    return [
        # QHINs
        dict(tefcaid="TEFCA-QHIN-001", hcid="HCID-Q001", name="Atlantic Health Exchange QHIN",
             level="qhin", npi=v("100000001"), state="VA", city="Arlington", status="active"),
        dict(tefcaid="TEFCA-QHIN-002", hcid="HCID-Q002", name="Great Lakes Interoperability QHIN",
             level="qhin", npi=v("100000002"), state="MD", city="Bethesda", status="active"),
        # Participants — health systems
        dict(tefcaid="TEFCA-PART-001", hcid="HCID-P001", name="Commonwealth Regional Health System",
             level="participant", npi=v("200000001"), state="VA", city="Richmond", status="active",
             parent="TEFCA-QHIN-001"),
        dict(tefcaid="TEFCA-PART-002", hcid="HCID-P002", name="Chesapeake Bay Medical Group",
             level="participant", npi=v("200000002"), state="MD", city="Baltimore", status="active",
             parent="TEFCA-QHIN-001"),
        dict(tefcaid="TEFCA-PART-003", hcid="HCID-P003", name="Empire State Care Alliance",
             level="participant", npi=v("200000003"), state="NY", city="Albany",
             status="pending_verification", parent="TEFCA-QHIN-002"),
        dict(tefcaid="TEFCA-PART-004", hcid="HCID-P004", name="Golden Gate Health Partners",
             level="participant", npi=v("200000004"), state="CA", city="Oakland",
             status="draft", parent="TEFCA-QHIN-002"),
        dict(tefcaid="TEFCA-PART-005", hcid="HCID-P005", name="Lone Star Integrated Health",
             level="participant", npi=v("200000005"), state="TX", city="Austin",
             status="active", parent="TEFCA-QHIN-002"),
        # INVALID NPI on purpose — exercises flag-don't-reject.
        dict(tefcaid="TEFCA-PART-006", hcid="HCID-P006", name="Blue Ridge Community Health (bad NPI)",
             level="participant", npi="1234567890", state="VA", city="Roanoke",
             status="draft", parent="TEFCA-QHIN-001"),
        # Sub-participants — hospitals and clinics
        dict(tefcaid="TEFCA-SUB-001", hcid="HCID-S001", name="Richmond General Hospital",
             level="sub_participant", npi=v("300000001"), state="VA", city="Richmond",
             status="active", parent="TEFCA-PART-001"),
        dict(tefcaid="TEFCA-SUB-002", hcid="HCID-S002", name="Tidewater Children's Clinic",
             level="sub_participant", npi=v("300000002"), state="VA", city="Norfolk",
             status="active", parent="TEFCA-PART-001"),
        dict(tefcaid="TEFCA-SUB-003", hcid="HCID-S003", name="Johns Creek Family Practice",
             level="sub_participant", npi=v("300000003"), state="MD", city="Rockville",
             status="suspended", parent="TEFCA-PART-002"),
        dict(tefcaid="TEFCA-SUB-004", hcid="HCID-S004", name="Hudson Valley Urgent Care",
             level="sub_participant", npi=v("300000004"), state="NY", city="Poughkeepsie",
             status="active", parent="TEFCA-PART-003"),
        dict(tefcaid="TEFCA-SUB-005", hcid="HCID-S005", name="Bay Area Oncology Associates",
             level="sub_participant", npi=v("300000005"), state="CA", city="San Jose",
             status="draft", parent="TEFCA-PART-004"),
        dict(tefcaid="TEFCA-SUB-006", hcid="HCID-S006", name="Austin Cardiology Institute",
             level="sub_participant", npi=v("300000006"), state="TX", city="Austin",
             status="active", parent="TEFCA-PART-005"),
        # Second INVALID NPI — wrong check digit on an otherwise plausible number.
        dict(tefcaid="TEFCA-SUB-007", hcid="HCID-S007", name="Gulf Coast Rehab Center (bad NPI)",
             level="sub_participant", npi="9876543210", state="TX", city="Houston",
             status="draft", parent="TEFCA-PART-005"),
        dict(tefcaid="TEFCA-SUB-008", hcid="HCID-S008", name="Shenandoah Behavioral Health",
             level="sub_participant", npi=v("300000008"), state="VA", city="Harrisonburg",
             status="inactive", parent="TEFCA-PART-001"),
        dict(tefcaid="TEFCA-SUB-009", hcid="HCID-S009", name="Capital District Imaging",
             level="sub_participant", npi=v("300000009"), state="NY", city="Schenectady",
             status="active", parent="TEFCA-PART-003"),
        dict(tefcaid="TEFCA-SUB-010", hcid="HCID-S010", name="Pacific Coast Dialysis Network",
             level="sub_participant", npi=v("300000010"), state="CA", city="Sacramento",
             status="pending_verification", parent="TEFCA-PART-004"),
    ]


def build_csv() -> str:
    """The seed as CSV text, in the column shape csv_import expects."""
    header = ("TEFCAID,HCID,EntityName,EntityLevel,ParentTEFCAID,NPI,State,City,"
              "OperationalStatus\n")
    lines = [header]
    for r in _rows():
        lines.append(
            f"{r['tefcaid']},{r['hcid']},\"{r['name']}\",{r['level']},"
            f"{r.get('parent','')},{r['npi']},{r['state']},{r['city']},{r['status']}\n")
    return "".join(lines)


async def entity_count(session: AsyncSession) -> int:
    return int((await session.execute(
        select(func.count()).select_from(reg.TefcaRegEntity))).scalar() or 0)


async def seed(session: AsyncSession, *, force: bool = False,
               actor_id=None, actor_email: Optional[str] = None,
               ip_address: Optional[str] = None) -> dict:
    """Load the seed through the real CSV importer.

    Refuses when the registry is already populated unless `force` is set — this
    is reachable as an endpoint, and silently doubling a populated registry is
    worse than declining. The importer itself skips duplicate TEFCAID/HCID, so
    a forced re-run is idempotent rather than additive.
    """
    from app.tefca_registry.csv_import import import_csv

    existing = await entity_count(session)
    if existing and not force:
        return {"seeded": False, "reason": "registry already has entities",
                "existing_entities": existing,
                "hint": "pass force=true to import anyway (duplicates are skipped)"}

    result = await import_csv(
        session, build_csv(), filename="dev_seed.csv",
        actor_id=actor_id, actor_email=actor_email, ip_address=ip_address)
    result["seeded"] = True
    result["existing_before"] = existing
    return result
