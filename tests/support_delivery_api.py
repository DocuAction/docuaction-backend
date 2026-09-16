"""Seeding helpers for the delivery-API tests (lane A, 2026-09-17 remediation).

Not a test module (the name does not match `test_*.py`), so pytest never
collects it. It exists because three test files need the same things - a real
user row per role in the isolated database, a signed token for it, and a
minimal delivery (job + intake + records + issues) to point the new endpoints
at - and duplicating that in each would be the drift this remediation is
about.

Everything here writes ONLY to the isolated test database named by
DATABASE_URL. Rows are namespaced with a per-process tag so concurrent lanes
running against the same database do not collide.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.security import create_access_token

TAG = uuid.uuid4().hex[:8]

#: Everything this process has committed, so `cleanup()` can remove it again.
#: The isolated database is shared by every test module in a run; several
#: pre-existing suites assert on the WHOLE estate (an empty supervisor board,
#: a fixed legacy population), so seeded rows must not outlive the module that
#: needed them.
_SEEDED: Dict[str, list] = {"jobs": [], "intakes": [], "records": [],
                            "review_records": []}


def register_review_record(review_record_id) -> None:
    """A test that commits its own review_records row asks to have it removed."""
    _SEEDED["review_records"].append(str(review_record_id))


def _database_available() -> bool:
    """The same probe result tests/conftest.py computed at import time."""
    try:
        import conftest  # tests/ is on sys.path under pytest
        return bool(getattr(conftest, "DB_AVAILABLE", False))
    except Exception:  # noqa: BLE001 - outside pytest, let the connection decide
        return True


def run(coro):
    """Run a coroutine from a synchronous test. NullPool (conftest) keeps a
    connection inside the loop that opened it, so this is safe per call.

    Skips, rather than errors, when no database is reachable: these helpers
    seed real rows, and a module-scoped fixture that raised ConnectionRefused
    turned every test in the module into a setup ERROR on CI runners without
    PostgreSQL (2026-09-16). The skip reason names the condition exactly.
    """
    if not _database_available():
        import pytest
        coro.close()
        pytest.skip("No database reachable at DATABASE_URL; this module seeds "
                    "real delivery rows and needs PostgreSQL (see tests/conftest.py)")
    return asyncio.run(coro)


# -- users -------------------------------------------------------------------

async def _ensure_user(role: str):
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models.database import User

    email = f"lane-a-{role}-{TAG}@test.local"
    async with async_session_maker() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(id=uuid.uuid4(), email=email, password_hash="x" * 60,
                        full_name=f"Lane A {role}", company="test", role=role,
                        plan="enterprise", allowed_modules=[], is_active=True,
                        is_verified=True, status="active")
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return {"id": str(user.id), "email": user.email, "role": user.role}


_USERS: Dict[str, Dict[str, Any]] = {}


def user_for(role: str) -> Dict[str, Any]:
    if role not in _USERS:
        _USERS[role] = run(_ensure_user(role))
    return _USERS[role]


def headers_for(role: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    user = user_for(role)
    token = create_access_token({"sub": user["id"], "role": role},
                                is_admin=(role == "admin"))
    out = {"Authorization": f"Bearer {token}"}
    if extra:
        out.update(extra)
    return out


# -- deliveries --------------------------------------------------------------

def _delivery_bytes(rows: int = 3) -> bytes:
    from app.tefca_registry.rce import field_map as fm

    header = "|".join(fm.RCE_FIELDS)
    lines = [header]
    for i in range(rows):
        values = {f: "" for f in fm.RCE_FIELDS}
        values.update({
            "id": f"2.16.840.1.113883.3.{TAG}.{i}",
            "domains": "RCE", "orgManagingOrg": "2.16.840.1.113883.3.9960",
            "purposesofuse": "T-TRTMNT", "NPI": "1881659506",
            "HCID": f"urn:oid:2.16.840.1.113883.3.{TAG}.{i}",
            "TEFCAID": f"urn:uuid:{uuid.uuid4()}", "active": "1",
            "sequoiaorgtype": "Participant", "name": f"Lane A Org {TAG} {i}",
            "address_line": "1 Main St", "address_city": "Buffalo",
            "address_state": "NY", "address_postalCode": "14203",
            "address_country": "US", "partOf": "2.16.840.1.113883.3.9960",
        })
        lines.append("|".join(values[f] for f in fm.RCE_FIELDS))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


async def _seed_delivery(*, state: str = "SUCCEEDED", with_intake: bool = True,
                         stage: Optional[str] = None, error_reason: Optional[str] = None,
                         issues: int = 2, jobs_for_intake: int = 1) -> Dict[str, Any]:
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
    from app.tefca_registry.rce.intake import ingest_delivery

    os.environ.setdefault("UPLOAD_DIR", os.path.join(
        os.environ.get("TEMP", os.getcwd()), f"lane-a-uploads-{TAG}"))
    raw = _delivery_bytes()  # TEFCAID carries a uuid4 per row, so each seed is unique bytes
    sha = hashlib.sha256(raw).hexdigest()
    out: Dict[str, Any] = {"job_ids": [], "issue_ids": [], "record_ids": []}

    async with async_session_maker() as db:
        intake_id = None
        if with_intake:
            result = await ingest_delivery(db, raw, filename=f"lane-a-{TAG}.txt",
                                           delivery_label=f"Lane A {TAG}",
                                           received_by="lane-a@test.local")
            intake_id = uuid.UUID(str(result["intake_id"] if isinstance(result, dict)
                                      else result.id))
            records = (await db.execute(
                select(m.RceSourceRecord)
                .where(m.RceSourceRecord.source_intake_id == intake_id)
                .order_by(m.RceSourceRecord.line_number))).scalars().all()
            out["record_ids"] = [str(r.id) for r in records]
            _SEEDED["intakes"].append(str(intake_id))
            _SEEDED["records"].extend(out["record_ids"])
            run_row = m.RceIngestionRun(source_intake_id=intake_id, rule_set_version="1.2.0",
                                        rule_config_hash="0" * 64, run_status="COMPLETE",
                                        completed_at=datetime.utcnow(),  # a CURRENT run
                                        records_evaluated=len(records),
                                        issues_generated=issues, executed_by="lane-a")
            db.add(run_row)
            await db.flush()
            for i in range(min(issues, len(records))):
                rec = records[i]
                issue = m.RceIssue(
                    issue_code=f"DQ-{TAG}-{uuid.uuid4().hex[:6]}",
                    source_intake_id=intake_id, source_record_id=rec.id, run_id=run_row.id,
                    rule_id="NPI-003" if i % 2 == 0 else "NPI-008", rule_version="1.2.0",
                    issue_type=("NPI_CHECKSUM_INVALID" if i % 2 == 0
                                else "NPI_EXISTING_VALUE_CONFLICT"),
                    severity="HIGH", field_name="NPI", original_value=rec.npi,
                    correction_authority="HUMAN_REQUIRED",
                    description="lane A seeded finding")
                db.add(issue)
                await db.flush()
                out["issue_ids"].append(str(issue.id))
                curated = m.RceCuratedRecord(
                    source_intake_id=intake_id, source_record_id=rec.id,
                    record_status="HELD", issue_count=1, rce_org_oid=rec.source_rce_id,
                    npi=rec.npi, name=f"Lane A Org {TAG} {i}",
                    transformation_version="lane-a-seed")
                db.add(curated)
            await db.commit()
        out["intake_id"] = str(intake_id) if intake_id else None

        now = datetime.utcnow()
        for n in range(jobs_for_intake):
            job = RceDeliveryJob(
                identity=hashlib.sha256(f"{sha}|{n}".encode()).hexdigest(),
                delivery_label=f"Lane A {TAG}", original_filename=f"lane-a-{TAG}.txt",
                storage_path="(test)", sha256=sha, file_size_bytes=len(raw),
                state=state, stage=stage or ("READY_FOR_REVIEW" if state == "SUCCEEDED"
                                             else "PARSING"),
                active_marker=None if state in ("SUCCEEDED", "FAILED") else True,
                registered_by="lane-a@test.local", created_at=now, heartbeat_at=now,
                started_at=now, completed_at=now if state == "SUCCEEDED" else None,
                failed_at=now if state == "FAILED" else None,
                attempt_count=1, error_reason=error_reason,
                source_intake_id=intake_id, records_received=3 if intake_id else None,
                reconciliation_passed=(True if state == "SUCCEEDED" else None),
                stage_detail={})
            db.add(job)
            await db.flush()
            out["job_ids"].append(str(job.id))
            _SEEDED["jobs"].append(str(job.id))
        await db.commit()
    out["job_id"] = out["job_ids"][0] if out["job_ids"] else None
    return out


def seed_delivery(**kw) -> Dict[str, Any]:
    return run(_seed_delivery(**kw))


async def _add_stage_events(job_id: str, intake_id: Optional[str], failed_stage: Optional[str]):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import stage_events

    async with async_session_maker() as db:
        j = uuid.UUID(job_id)
        i = uuid.UUID(intake_id) if intake_id else None
        for stage in ("REGISTERED", "RECEIPT_PRESERVED", "SHA256"):
            await stage_events.record_instant(db, j, stage, intake_id=i)
        if failed_stage:
            ev = await stage_events.open_stage(db, j, failed_stage, intake_id=i)
            await stage_events.close_stage(db, ev, "FAILED",
                                           failure_reason="seeded failure")


def add_stage_events(job_id: str, intake_id: Optional[str] = None,
                     failed_stage: Optional[str] = None) -> None:
    run(_add_stage_events(job_id, intake_id, failed_stage))


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- cleanup -----------------------------------------------------------------

async def _cleanup():
    """Delete what this process seeded, children first.

    Runs as the test superuser, so the append-only grants on the traceability
    tables and the Area 1 triggers do not stop it (the Area 1 delete trigger
    records the deletion in area1_mutation_log, which is exactly what it is
    for). Nothing outside the seeded ids is touched.
    """
    from sqlalchemy import text

    from app.core.database import async_session_maker

    jobs, intakes, records, reviews = (_SEEDED["jobs"], _SEEDED["intakes"],
                                       _SEEDED["records"], _SEEDED["review_records"])
    if not (jobs or intakes or records or reviews):
        return
    p = {"jobs": jobs or [None], "intakes": intakes or [None],
         "records": records or [None], "reviews": reviews or [None]}
    statements = [
        "DELETE FROM review_decision_events WHERE review_record_id IN "
        "(SELECT id FROM review_records WHERE id::text = ANY(:reviews) "
        " OR source_record_id::text = ANY(:records))",
        "DELETE FROM review_records WHERE id::text = ANY(:reviews) "
        "OR source_record_id::text = ANY(:records) "
        "OR verification_results->>'source_intake_id' = ANY(:intakes)",
        "DELETE FROM rce_delivery_report_links WHERE job_id::text = ANY(:jobs) "
        "OR intake_id::text = ANY(:intakes)",
        "DELETE FROM rce_reconciliation_snapshots WHERE job_id::text = ANY(:jobs) "
        "OR intake_id::text = ANY(:intakes)",
        "DELETE FROM tefca_identifier_decision_events WHERE intake_id::text = ANY(:intakes) "
        "OR source_record_id::text = ANY(:records)",
        "DELETE FROM rce_disposition_events WHERE intake_id::text = ANY(:intakes) "
        "OR job_id::text = ANY(:jobs)",
        "DELETE FROM rce_delivery_stage_events WHERE job_id::text = ANY(:jobs) "
        "OR intake_id::text = ANY(:intakes)",
        "DELETE FROM rce_correction_details WHERE curated_record_id IN "
        "(SELECT id FROM rce_curated_records WHERE source_intake_id::text = ANY(:intakes))",
        "DELETE FROM rce_issues WHERE source_intake_id::text = ANY(:intakes)",
        "DELETE FROM rce_curated_records WHERE source_intake_id::text = ANY(:intakes)",
        "DELETE FROM rce_rule_execution_history WHERE run_id IN "
        "(SELECT id FROM rce_ingestion_runs WHERE source_intake_id::text = ANY(:intakes))",
        "DELETE FROM rce_ingestion_runs WHERE source_intake_id::text = ANY(:intakes)",
        "DELETE FROM rce_source_records WHERE source_intake_id::text = ANY(:intakes)",
        "DELETE FROM rce_source_intakes WHERE id::text = ANY(:intakes)",
        "DELETE FROM rce_delivery_jobs WHERE id::text = ANY(:jobs)",
    ]
    async with async_session_maker() as db:
        for sql in statements:
            try:
                await db.execute(text(sql), p)
            except Exception:  # noqa: BLE001 - a missing optional table must not stop cleanup
                await db.rollback()
                continue
        await db.commit()
    for key in _SEEDED:
        _SEEDED[key].clear()


def cleanup() -> None:
    run(_cleanup())
