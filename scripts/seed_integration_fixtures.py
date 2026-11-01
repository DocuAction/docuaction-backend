"""Deterministic synthetic fixtures for the API/UI integration tests.

Stands up a KNOWN state in whatever database DATABASE_URL points at, so the
Postman collection and the Playwright real-backend smoke both assert against
the same facts:

  * five synthetic accounts, one per role the authorization tests exercise
    (viewer / reviewer / qalead / program_manager / admin), created by direct
    insert — there is no self-registration endpoint, the same pattern the
    journey tests use;
  * one promoted synthetic delivery (ingest -> quality -> curate -> promote),
    reusing the real pipeline so reconciliation is genuine;
  * one deterministic SAM public-extract CANDIDATE: a single
    SAM_EXTRACT_SUPPLEMENTARY / REVIEW evidence row inserted directly for one
    promoted entity. This is a CONTROLLED source response at the data layer —
    no external GSA call, no dependence on whether the extract cache is
    loaded — so the candidate-enumeration assertions are deterministic.

Everything here is synthetic (names, NPIs, addresses are fabricated). It never
touches the October delivery or any real data. Idempotent: re-running reuses
existing accounts and appends a fresh synthetic delivery.

Writes a JSON manifest (default: integration-fixtures.json) with the ids and
credentials the test runners consume. Run with the backend venv active and
DATABASE_URL set to the ISOLATED instance, e.g.

  DATABASE_URL=postgresql+asyncpg://docuaction_owner:x@127.0.0.1:5432/itest \\
    python scripts/seed_integration_fixtures.py --out integration-fixtures.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime

# Importable whether invoked as `python scripts/seed_integration_fixtures.py`
# (which puts scripts/ on the path, not the repo root) or `python -m`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Synthetic accounts — one per role the authorization matrix needs. The
# passwords are synthetic test credentials for an isolated instance only;
# they are never real and never a production secret.
ACCOUNTS = [
    ("integration-viewer@synthetic-test.docuaction.invalid", "ItestViewer!2026", "viewer"),
    ("integration-reviewer@synthetic-test.docuaction.invalid", "ItestReviewer!2026", "reviewer"),
    ("integration-qalead@synthetic-test.docuaction.invalid", "ItestQALead!2026", "qalead"),
    ("integration-pm@synthetic-test.docuaction.invalid", "ItestPM!2026", "program_manager"),
    ("integration-admin@synthetic-test.docuaction.invalid", "ItestAdmin!2026", "admin"),
]

def _valid_npi(seed: int) -> str:
    """A 10-digit NPI whose 80840-prefixed Luhn checksum is valid (the same
    scheme the pipeline's NPI validator enforces)."""
    base = f"{1_000_000_000 + (seed % 900_000_000):09d}"[-9:]
    for d in range(10):
        candidate = base + str(d)
        digits = "80840" + candidate
        total, alt = 0, False
        for ch in reversed(digits):
            x = int(ch)
            if alt:
                x *= 2
                if x > 9:
                    x -= 9
            total += x
            alt = not alt
        if total % 10 == 0:
            return candidate
    return base + "0"


def _synthetic_delivery_bytes(run_tag: str, n: int) -> bytes:
    """Mirror the proven e2e synthetic delivery (test_sam_e2e_delivery_path):
    the header is the full locked RCE_FIELDS schema and the required
    org-hierarchy fields are present, so promotion's schema-match gate
    accepts it."""
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    header = list(RCE_FIELDS)
    qhin = f"9.9.9.9.{run_tag}"
    rows = []
    for i in range(n):
        # The first row carries a CHECKSUM-INVALID NPI on purpose, so the
        # delivery has a real data-quality finding (an NPI code) in its
        # exception ledger — the shape the live-smoke UI spec asserts on.
        # The rest are valid. The bad NPI is flagged, not fabricated clean.
        if i == 0:
            good = _valid_npi(i + uuid.uuid4().int % 800_000_000)
            npi = good[:-1] + str((int(good[-1]) + 1) % 10)  # wrong check digit
        else:
            npi = _valid_npi(i + uuid.uuid4().int % 800_000_000)
        values = {
            "id": f"itest.{run_tag}.{i:04d}",
            "orgManagingOrg": qhin, "sequoiaorgtype": "Participant",
            "organizationNodeType": "initiating-node",
            "NPI": npi,
            "TEFCAID": f"TEFCA-ITEST-{run_tag}-{i:04d}",
            "HCID": f"HCID-ITEST-{run_tag}-{i:04d}", "active": "true",
            "hl7orgrole": "provider",
            "name": f"SYNTHETIC ITEST Org {run_tag} {i:04d}",
            "partOf": qhin, "address_text": "Primary",
            "address_line": f"{100 + i} Integration Way",
            "address_city": "Testville", "address_state": "TX",
            "address_postalCode": f"{75000 + i}", "address_country": "US",
            "phone": "512-555-0100",
            "email": f"itest{i}@synthetic-test.docuaction.invalid",
            "purposesofuse": "T-TRTMNT", "stateofoperation": "TX",
            "doa": "2026-01-01", "transaction": "A",
        }
        rows.append([str(values.get(f, "")) for f in header])
    body = "\n".join("|".join(r) for r in rows)
    return ("|".join(header) + "\n" + body + "\n").encode("utf-8")


async def _ensure_accounts():
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.core.security import hash_password
    from app.models.database import User

    created = []
    async with async_session_maker() as db:
        for email, password, role in ACCOUNTS:
            existing = (await db.execute(
                select(User).where(User.email == email))).scalars().first()
            if existing is not None:
                # Keep the role/active state deterministic across re-runs.
                existing.role = role
                existing.is_active = True
                existing.is_verified = True
                existing.status = "active"
                continue
            db.add(User(
                id=uuid.uuid4(), tenant_id="synthetic-integration", email=email,
                password_hash=hash_password(password),
                full_name=f"SYNTHETIC integration {role}",
                role=role, is_active=True, is_verified=True, status="active",
                allowed_modules=[]))
            created.append(email)
        await db.commit()
    return created


async def _seed_promoted_delivery(n: int) -> str:
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.curation import curate_delivery
    from app.tefca_registry.rce.intake import ingest_delivery
    from app.tefca_registry.rce.promotion import promote_delivery
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    run_tag = uuid.uuid4().hex[:10]
    raw = _synthetic_delivery_bytes(run_tag, n=n)
    async with async_session_maker() as db:
        result = await ingest_delivery(
            db, raw, filename=f"SYNTHETIC-ITEST-{run_tag}.psv",
            delivery_label=f"SYNTHETIC-ITEST-{run_tag}",
            declared_delimiter="|",
            received_by="integration-seed@synthetic-test.docuaction.invalid")
        intake_id = result["intake_id"]
    async with async_session_maker() as db:
        await run_quality_engine(db, intake_id, executed_by="integration-seed")
    async with async_session_maker() as db:
        await curate_delivery(db, intake_id, curated_by="integration-seed")
    async with async_session_maker() as db:
        await promote_delivery(db, intake_id, actor="integration-seed")
    return str(intake_id)


async def _ensure_job(intake_id: str) -> str:
    """Bind a SUCCEEDED RceDeliveryJob to the intake so it appears in
    `GET /api/tefca/rce/delivery-jobs` (what the frontend lists). Raw
    ingest_delivery does not create one — the official registration flow
    does — so the seed creates a terminal job row directly, the same shape
    the test harness's `seed_intake` uses."""
    import hashlib

    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    async with async_session_maker() as db:
        existing = await jobs.job_for_intake(db, uuid.UUID(intake_id))
        if existing is not None:
            return str(existing.id)
        intake = (await db.execute(
            select(m.RceSourceIntake).where(
                m.RceSourceIntake.id == uuid.UUID(intake_id)))).scalars().first()
        now = datetime.utcnow()
        job = RceDeliveryJob(
            id=uuid.uuid4(),
            identity=hashlib.sha256(f"itest-{intake_id}".encode()).hexdigest(),
            delivery_label=getattr(intake, "delivery_label", "SYNTHETIC-ITEST"),
            original_filename=getattr(intake, "original_filename", "synthetic.psv"),
            storage_path="(synthetic)",
            sha256=getattr(intake, "sha256", hashlib.sha256(intake_id.encode()).hexdigest()),
            file_size_bytes=int(getattr(intake, "file_size_bytes", 0) or 0),
            state=RceDeliveryJob.STATE_SUCCEEDED,
            stage=RceDeliveryJob.STAGE_READY,
            active_marker=None,
            created_at=now, started_at=now, heartbeat_at=now, completed_at=now,
            attempt_count=1, registered_by="integration-seed",
            source_intake_id=uuid.UUID(intake_id),
            records_received=int(getattr(intake, "record_count", 0) or 0),
            records_processed=int(getattr(intake, "record_count", 0) or 0),
            reconciliation_passed=False, stage_detail={})
        db.add(job)
        await db.commit()
        return str(job.id)


async def _first_entity(intake_id: str):
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        entity = (await db.execute(
            select(m.RceCuratedRecord.canonical_entity_id)
            .where(m.RceCuratedRecord.source_intake_id == uuid.UUID(intake_id),
                   m.RceCuratedRecord.canonical_entity_id.isnot(None))
            .order_by(m.RceCuratedRecord.canonical_entity_id)
            .limit(1))).scalars().first()
    return str(entity) if entity else None


async def _insert_candidate_evidence(entity_id: str) -> None:
    """One deterministic SAM public-extract CANDIDATE (disposition REVIEW) for
    a promoted entity — inserted directly, a controlled source response at the
    data layer. No external GSA call; independent of the extract cache."""
    from sqlalchemy import text

    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        await db.execute(text("""
            INSERT INTO tefca_dimension_evidence
                (id, entity_id, evidence_dimension, source, disposition,
                 rule_applied, dataset_version_anchor, query_timestamp,
                 original_values, generation_timestamp, created_at, note)
            VALUES
                (:id, :eid, 'EXCLUSION_REVOCATION', 'SAM_EXTRACT_SUPPLEMENTARY',
                 'REVIEW', 'SAM_PUBLIC_EXTRACT_SUPPLEMENTARY_SCREEN',
                 'GSA-Public-Extract-V2', :ts,
                 CAST(:vals AS jsonb), :ts, now(),
                 'SYNTHETIC integration candidate — controlled data-layer seed, not a real exclusion')
        """), {
            "id": str(uuid.uuid4()), "eid": entity_id,
            "ts": datetime.utcnow().isoformat(),
            "vals": json.dumps({
                "candidate_match": True, "candidate_count": 1,
                "publication_date": "2026-10-29",
                "extract_sha256": "faa40b80" + "0" * 56,
                "match_methods": ["normalized_business_name"],
                "source_dataset": "SAM_Exclusions_Public_Extract_V2",
            }),
        })
        await db.commit()


async def main_async(args) -> int:
    created = await _ensure_accounts()
    intake_id = await _seed_promoted_delivery(n=args.records)
    job_id = await _ensure_job(intake_id)
    entity_id = await _first_entity(intake_id)
    candidate_entity = None
    if entity_id and not args.no_candidate:
        await _insert_candidate_evidence(entity_id)
        candidate_entity = entity_id

    manifest = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "synthetic": True,
        "intake_id": intake_id,
        "job_id": job_id,
        "candidate_entity_id": candidate_entity,
        "records": args.records,
        "accounts_created": created,
        "accounts": [
            {"email": e, "password": p, "role": r} for e, p, r in ACCOUNTS
        ],
        "note": ("All synthetic. Passwords are test credentials for an "
                 "isolated instance only."),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    # Print the ids (not the passwords) for the CI log.
    print(json.dumps({"intake_id": intake_id, "job_id": job_id,
                      "candidate_entity_id": candidate_entity,
                      "accounts": [a[0] for a in ACCOUNTS],
                      "out": args.out}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="integration-fixtures.json",
                        help="Path to write the fixture manifest JSON.")
    parser.add_argument("--records", type=int, default=3,
                        help="How many synthetic entities to promote.")
    parser.add_argument("--no-candidate", action="store_true",
                        help="Skip the deterministic candidate evidence row.")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
