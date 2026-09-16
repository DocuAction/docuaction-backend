"""Shared synthetic fixtures for the 2026-09-17 delivery-traceability tests.

Every test that imports this runs inside an OUTER transaction that is rolled
back, with the session joined via `join_transaction_mode="create_savepoint"`,
so the many internal `db.commit()` calls in the pipeline commit savepoints and
nothing reaches disk. Fixture data is synthetic throughout: OIDs live under the
unassigned `9.99.777` arc, names are prefixed, and no delivered Government row
is used. NPIs are either Luhn-valid synthetic values built from
`make_valid_npi` or deliberately invalid ones named by the contract.

Not a test module (no `test_` prefix); imported by the test files.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint

SYN = "SYNTHETIC-TRACE"
ARC = "9.99.777"
#: An OBSERVED QHIN OID (a QHIN is an external referent, not a delivered
#: record), so INT-002 does not flag every synthetic partOf as unresolved.
QHIN_OID = "2.16.840.1.113883.4.391.1000"

#: Contract Scenario B values: 1982916079 fails the CMS check digit (the
#: correct digit for 198291607 is 8); 1982916078 is the registered value.
NPI_BAD_CHECKSUM = "1982916079"
NPI_REGISTERED = "1982916078"
#: A different, Luhn-valid synthetic NPI.
NPI_VALID_OTHER = "1234567893"


def base_row(**over) -> Dict[str, str]:
    values = {f: "" for f in RCE_FIELDS}
    values.update({
        "domains": "RCE", "orgManagingOrg": QHIN_OID, "purposesofuse": "T-TRTMNT",
        "active": "1", "sequoiaorgtype": "Participant",
        "address_line": "1 Synthetic Way", "address_city": "Testville",
        "address_state": "MA", "address_postalCode": "02101",
        "address_country": "USA", "partOf": QHIN_OID,
    })
    values.update(over)
    return values


def make_rows(n: int, *, arc: str = f"{ARC}.1", **over) -> List[Dict[str, str]]:
    rows = []
    for i in range(1, n + 1):
        r = base_row(**over)
        r["id"] = f"{arc}.{i}"
        r["TEFCAID"] = f"{SYN}-{arc}-TEFCAID-{i:04d}"
        r["HCID"] = f"urn:oid:{arc}.{i}"
        r["name"] = f"{SYN} {arc} ORG {i}"
        rows.append(r)
    return rows


@pytest.fixture
async def rolled_back_db(db_required):
    """Dedicated engine + outer transaction rolled back at the end."""
    from app.core.database import _normalize_url

    engine = create_async_engine(
        _normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    connection = await engine.connect()
    outer = await connection.begin()
    session = AsyncSession(bind=connection,
                           join_transaction_mode="create_savepoint",
                           expire_on_commit=False)
    # Hermetic against rows other modules committed to the shared database:
    # the synthetic contract NPIs must not pre-exist on any entity (the unique
    # index spans every status, and the create path skips a shared value).
    # Removed inside the outer transaction, so it is rolled back with the rest.
    from sqlalchemy import delete as _delete
    await session.execute(_delete(reg.TefcaEntityIdentifier).where(
        reg.TefcaEntityIdentifier.identifier_type == "npi",
        reg.TefcaEntityIdentifier.identifier_value.in_(
            (NPI_REGISTERED, NPI_VALID_OTHER, NPI_BAD_CHECKSUM))))
    await session.flush()
    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()
        await connection.close()
        await engine.dispose()


async def seed_intake(db, rows: List[Dict[str, str]], *, with_job: bool = True,
                      parse_status: Optional[Dict[int, str]] = None):
    """One synthetic intake + Area 1 rows (+ a RUNNING job bound to it).

    `parse_status` maps a 1-based row index to a non-ok parse status so a test
    can seed a REJECTED line. Returns (intake_id, job or None).
    """
    blob = ("\r\n".join(["|".join(RCE_FIELDS)]
                        + ["|".join(r[f] for f in RCE_FIELDS) for r in rows])
            + "\r\n").encode("utf-8")
    sha = hashlib.sha256(blob).hexdigest()
    intake_id = uuid.uuid4()
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=f"{SYN}-INTAKE",
        original_filename="synthetic.csv", storage_path="(synthetic)",
        sha256=sha, file_size_bytes=len(blob),
        delimiter="|", encoding="utf-8", line_terminator="CRLF",
        headers=list(RCE_FIELDS),
        schema_fingerprint=schema_fingerprint(list(RCE_FIELDS)),
        record_count=len(rows), received_at=datetime.utcnow(), received_by=SYN,
        status="PARSED", source_metadata={"origin": "synthetic test fixture"}))
    await db.flush()
    for index, r in enumerate(rows, start=1):
        raw = "|".join(r[f] for f in RCE_FIELDS)
        status = (parse_status or {}).get(index, "ok")
        db.add(m.RceSourceRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, line_number=index + 1,
            raw_line=raw, parsed=(r if status == "ok" else {}),
            record_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            source_rce_id=r["id"], tefcaid=r["TEFCAID"], hcid=r["HCID"],
            npi=(r.get("NPI") or None),
            field_count=len(RCE_FIELDS) if status == "ok" else len(RCE_FIELDS) - 1,
            parse_status=status, promotion_status="pending"))
    job = None
    if with_job:
        job = RceDeliveryJob(
            id=uuid.uuid4(), identity=hashlib.sha256(
                f"{sha}|{SYN}".encode()).hexdigest(),
            delivery_label=f"{SYN}-INTAKE", original_filename="synthetic.csv",
            storage_path="(synthetic)", sha256=sha, file_size_bytes=len(blob),
            state=RceDeliveryJob.STATE_RUNNING, stage=RceDeliveryJob.STAGE_QUALITY,
            active_marker=True, registered_by=SYN, created_at=datetime.utcnow(),
            started_at=datetime.utcnow(), heartbeat_at=datetime.utcnow(),
            attempt_count=1, stage_detail={}, source_intake_id=intake_id,
            records_received=len(rows))
        db.add(job)
    await db.commit()
    return intake_id, job


async def seed_entity(db, *, oid: str, name: str, npi: Optional[str] = None,
                      tefcaid: Optional[str] = None, **columns) -> uuid.UUID:
    """A registry entity that an earlier delivery would have created."""
    entity_id = uuid.uuid4()
    db.add(reg.TefcaRegEntity(
        id=entity_id, name=name, display_name=name,
        entity_level=columns.pop("entity_level", "participant"),
        entity_type="provider",
        operational_status=columns.pop("operational_status", "active"),
        verification_status="not_verified", current_version=1,
        is_active=columns.pop("is_active", True),
        state=columns.pop("state", "MA"), city=columns.pop("city", "Testville"),
        zip=columns.pop("zip", "02101"),
        address=columns.pop("address", "1 Synthetic Way"),
        exchange_purposes={"purposes": columns.pop("purposes", ["T-TRTMNT"])},
        rce_org_oid=oid, rce_tefcaid=tefcaid,
        sequoia_org_type=columns.pop("sequoia_org_type", "Participant"),
        org_managing_org=columns.pop("org_managing_org", QHIN_OID),
        is_test_record=False, rce_attributes={}))
    await db.flush()
    db.add(reg.TefcaEntityIdentifier(
        id=uuid.uuid4(), entity_id=entity_id, identifier_type="rce_org_oid",
        identifier_value=oid,
        system_uri="urn:docuaction:tefca/identifier/rce-org-oid",
        is_primary=True, identifier_status="active"))
    if npi:
        # Hermetic against rows left in a shared database (the acceptance
        # harness persists the contract NPI, and idx_tefca_ident_unique spans
        # every status): remove any row with this value inside the test
        # transaction, which is rolled back afterwards.
        from sqlalchemy import select as _select
        for row in (await db.execute(_select(reg.TefcaEntityIdentifier).where(
                reg.TefcaEntityIdentifier.identifier_type == "npi",
                reg.TefcaEntityIdentifier.identifier_value == npi))).scalars():
            await db.delete(row)
        await db.flush()
        db.add(reg.TefcaEntityIdentifier(
            id=uuid.uuid4(), entity_id=entity_id, identifier_type="npi",
            identifier_value=npi, system_uri="http://hl7.org/fhir/sid/us-npi",
            is_primary=False, identifier_status="active"))
    db.add(reg.TefcaEntityVersion(
        id=uuid.uuid4(), entity_id=entity_id, version_number=1,
        snapshot_data={"name": name, "npi": npi}, change_reason="initial_import"))
    await db.commit()
    return entity_id


async def run_quality_and_curation(db, intake_id):
    from app.tefca_registry.rce.curation import curate_delivery
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    quality = await run_quality_engine(db, intake_id, executed_by=SYN)
    curated = await curate_delivery(db, intake_id, curated_by=SYN)
    return quality, curated


async def curated_by_oid(db, intake_id, oid: str) -> m.RceCuratedRecord:
    return (await db.execute(
        select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_intake_id == intake_id,
            m.RceCuratedRecord.rce_org_oid == oid))).scalar_one()


async def source_by_oid(db, intake_id, oid: str) -> m.RceSourceRecord:
    return (await db.execute(
        select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id,
            m.RceSourceRecord.source_rce_id == oid))).scalar_one()


async def issues_for(db, source_record_id, *, rule_id: Optional[str] = None
                     ) -> List[m.RceIssue]:
    stmt = select(m.RceIssue).where(m.RceIssue.source_record_id == source_record_id)
    if rule_id:
        stmt = stmt.where(m.RceIssue.rule_id == rule_id)
    return list((await db.execute(stmt.order_by(m.RceIssue.created_at))).scalars().all())


async def active_npi_rows(db, entity_id) -> List[reg.TefcaEntityIdentifier]:
    return list((await db.execute(
        select(reg.TefcaEntityIdentifier).where(
            reg.TefcaEntityIdentifier.entity_id == entity_id,
            reg.TefcaEntityIdentifier.identifier_type == "npi")
        .order_by(reg.TefcaEntityIdentifier.created_at))).scalars().all())


async def curated_direct(db, intake_id, rows: List[Dict[str, str]], *,
                         statuses: Optional[Dict[int, str]] = None,
                         overrides: Optional[Dict[int, Dict[str, Any]]] = None
                         ) -> Dict[str, uuid.UUID]:
    """Seed Area 2 rows directly (no quality run), one per source record.

    Used where a test needs a curated status or value the rules would not
    produce on their own — a CLEAN row with an invalid NPI, a CLEAN row with
    no name. Returns oid -> curated id.
    """
    out: Dict[str, uuid.UUID] = {}
    for index, r in enumerate(rows, start=1):
        src = await source_by_oid(db, intake_id, r["id"])
        status = (statuses or {}).get(index, "CLEAN")
        extra = (overrides or {}).get(index, {})
        row = dict(
            id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=src.id,
            record_status=status, issue_count=0, correction_count=0,
            status_reason=None, rce_org_oid=r["id"], tefcaid=r["TEFCAID"],
            hcid=r["HCID"], aaid=(r.get("AAID") or None), npi=(r.get("NPI") or None),
            name=r["name"], entity_level="participant",
            sequoia_org_type=r["sequoiaorgtype"], operational_status="active",
            is_active=True, address_line=r["address_line"],
            address_city=r["address_city"], address_state=r["address_state"],
            address_postal_code=r["address_postalCode"],
            address_country=r["address_country"], exchange_purposes=["T-TRTMNT"],
            part_of=r["partOf"], org_managing_org=QHIN_OID, contact={},
            rce_attributes={}, is_test_record=False,
            transformation_version="test-1.0.0")
        row.update(extra)
        db.add(m.RceCuratedRecord(**row))
        out[r["id"]] = row["id"]
    await db.commit()
    return out
