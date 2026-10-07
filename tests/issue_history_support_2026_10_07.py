"""Synthetic seeding for the issue-history tests (2026-10-07).

Not a test module. Every delivery is built the way production builds one -- an
intake, Area 1 records, then the REAL quality engine -- so the persisted maps,
rule-history rows and issues are what the engine writes, not hand-made
imitations. Scenarios that need a deviation (a rule version change, a rule that
raises, an undeclared rule) apply it to the live Rule object with
`monkeypatch` for the one run, never by editing rows.

All data is synthetic: OIDs live under the unassigned 9.99.777 arc or are the
`SYN-OID-0001` handle; names carry the SYN prefix.
"""

from __future__ import annotations

import hashlib
import uuid
import zlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import RCE_FIELDS, schema_fingerprint

from rce_traceability_support import QHIN_OID, SYN, base_row  # noqa: F401
from rce_traceability_support import rolled_back_db  # noqa: F401  (fixture)

OID = "SYN-OID-0001"
GOOD_NPI = "1234567893"   # Luhn-valid synthetic
BAD_LEN_NPI = "12345"     # NPI-002 (length) finding
FEED = "SYN-RCE"


def entity_row(oid: str, *, npi: str = "", part_of: str = QHIN_OID,
               org_type: str = "Participant", tag: str = "A") -> Dict[str, str]:
    r = base_row(partOf=part_of, sequoiaorgtype=org_type, NPI=npi)
    r["id"] = oid
    r["TEFCAID"] = f"{SYN}-TEFCAID-{tag}-{zlib.crc32(oid.encode()) % 10**8:08d}"
    r["HCID"] = f"urn:oid:9.99.777.{zlib.crc32(oid.encode()) % 10**6}"
    r["name"] = f"{SYN} ORG {oid.strip()}"
    return r


def filler_row(tag: str) -> Dict[str, str]:
    """A second record so two deliveries never hash identically by accident."""
    return entity_row(f"9.99.777.9.{tag}", tag=tag)


async def seed_delivery(db, rows: List[Dict[str, str]], *, received_at: datetime,
                        feed: Optional[str] = FEED, label: Optional[str] = None,
                        status: str = "PARSED", extra_metadata: Optional[dict] = None,
                        blob_salt: str = "") -> uuid.UUID:
    """One synthetic intake + Area 1 rows. `feed=None` leaves it untagged."""
    blob = ("\r\n".join(["|".join(RCE_FIELDS)]
                        + ["|".join(r[f] for f in RCE_FIELDS) for r in rows])
            + "\r\n" + blob_salt).encode("utf-8")
    sha = hashlib.sha256(blob).hexdigest()
    intake_id = uuid.uuid4()
    metadata = {"origin": "synthetic test fixture"}
    if feed is not None:
        metadata["feed"] = feed
    metadata.update(extra_metadata or {})
    db.add(m.RceSourceIntake(
        id=intake_id, delivery_label=label or f"{SYN}-{received_at:%Y-%m-%d}",
        original_filename="synthetic.csv", storage_path="(synthetic)",
        sha256=sha, file_size_bytes=len(blob), delimiter="|", encoding="utf-8",
        line_terminator="CRLF", headers=list(RCE_FIELDS),
        schema_fingerprint=schema_fingerprint(list(RCE_FIELDS)),
        record_count=len(rows), received_at=received_at, received_by=f"{SYN}-operator",
        status=status, source_metadata=metadata))
    await db.flush()
    for index, r in enumerate(rows, start=1):
        raw = "|".join(r[f] for f in RCE_FIELDS)
        db.add(m.RceSourceRecord(
            id=uuid.uuid4(), source_intake_id=intake_id, line_number=index + 1,
            raw_line=raw, parsed=dict(r),
            record_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            source_rce_id=(r["id"] or None), tefcaid=r["TEFCAID"], hcid=r["HCID"],
            npi=(r.get("NPI") or None), field_count=len(RCE_FIELDS),
            parse_status="ok", promotion_status="pending"))
    await db.commit()
    return intake_id


async def run_engine(db, intake_id) -> Dict[str, Any]:
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    return await run_quality_engine(db, intake_id, executed_by=SYN)


async def deliver(db, month: int, *, npi: str = BAD_LEN_NPI, oid: Optional[str] = OID,
                  day: int = 5, feed: Optional[str] = FEED, process: bool = True,
                  extra_rows: Optional[List[Dict[str, str]]] = None,
                  org_type: str = "Participant", blob_salt: str = ""):
    """A monthly delivery containing the OID record (unless oid is None)."""
    rows = [filler_row(f"m{month}d{day}")] + list(extra_rows or [])
    if oid is not None:
        rows.insert(0, entity_row(oid, npi=npi, org_type=org_type))
    intake_id = await seed_delivery(db, rows, received_at=datetime(2026, month, day),
                                    feed=feed, blob_salt=blob_salt)
    if process:
        await run_engine(db, intake_id)
    return intake_id


async def record_of(db, intake_id, oid: str = OID):
    return (await db.execute(
        select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id,
            m.RceSourceRecord.source_rce_id == oid))).scalar_one()


async def issue_of(db, intake_id, rule_id: str, oid: str = OID):
    rec = await record_of(db, intake_id, oid)
    return (await db.execute(
        select(m.RceIssue).where(m.RceIssue.source_record_id == rec.id,
                                 m.RceIssue.rule_id == rule_id))).scalars().first()


def lane(entry: Dict[str, Any], rule_id: str) -> Dict[str, Any]:
    return next(l for l in entry["lanes"] if l["rule_id"] == rule_id)


def entry_of(resp: Dict[str, Any], intake_id) -> Dict[str, Any]:
    return next(e for e in resp["deliveries"] if e["delivery_id"] == str(intake_id))


def settings_for(viewer: str = FEED, reviewer: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(ISSUE_HISTORY_FEEDS_VIEWER=viewer,
                           ISSUE_HISTORY_FEEDS_REVIEWER=reviewer)


async def history(db, oid: str = OID, *, reviewer: bool = False, viewer_feeds=FEED,
                  reviewer_feeds: str = "", **kwargs):
    from app.tefca_registry.rce import issue_history as svc

    return await svc.get_issue_history(
        db, oid, reviewer_or_above=reviewer,
        settings=settings_for(viewer_feeds, reviewer_feeds), **kwargs)
