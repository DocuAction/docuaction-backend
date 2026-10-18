"""Reconstructed Round 27 (R27-3) live-browser seed for Part A/B.

The ORIGINAL seeding script used to produce SEED_JSON for
tests/e2e/live-partb.spec.mjs was never committed to this repository and
no copy survived into this session. This script rebuilds an equivalent,
deterministic seed using the SAME real pipeline and the SAME committed
corpus (tests/fixtures/seeded/manifest_b.json) that
tests/test_seeded_corpus_b_2026_10_04.py already exercises -- it reuses
that test module's own helper functions directly rather than duplicating
them, so there is exactly one place that builds this corpus.

WHAT THIS PRODUCES, against whichever DATABASE_URL is set when it runs
(a disposable database -- never production, never a shared environment):

  1. One 12-row delivery (tests/fixtures/seeded/manifest_b.json's
     `pipeline_seeds`, B01..B12) through the REAL pipeline: ingest ->
     quality -> curate -> promote -> verify_and_classify, with every
     external source (NPPES, OIG LEIE, SAM.gov, CMS PPEF/revocation)
     replaced by a deterministic, keyed-on-synthetic-name fake -- no
     network, no real identifier, no EIN/TIN/SSN (see the manifest's own
     header). Three of the twelve (B04, B05, B06) are SAM.gov SOURCE
     FAULTS (an error body, a 429, a timeout), each one producing
     "Verified -- checks incomplete" under the active rules, matching the
     documented, unapproved-P1 finding from every prior round.
  2. A 13th row, in the SAME delivery file but outside the 12-seed
     corpus (not part of `manifest_b.json`, never fed to
     verify_and_classify), carrying a checksum-INVALID NPI purely so the
     delivery's own file-level preflight check (app/tefca_registry/rce/
     preflight.py) finds a real, OPEN finding and the delivery reads
     "Clear with findings" rather than plain "Clear" -- the file-shape
     check and the entity verification check are two different systems,
     and this keeps them genuinely independent rather than faking one
     from the other.
  3. A recheck job for the SAM.gov source fault, REQUESTED by the
     Analyst account and left at PENDING_APPROVAL -- not approved, not
     run. The live browser journey itself drives "Approve recheck" (QA
     Lead) and "Run next batch" from there; nothing about the approval or
     the run is pre-recorded here, so neither is a simulated policy
     decision.
  4. A SEPARATE two-cycle entity (test_prior_risk_not_cleared_2026_10_04.
     py's own `_seed_one`/`_cycle` helpers, reused directly): cycle 1 is a
     confirmed SAM.gov exclusion, left un-adjudicated; cycle 2, on the
     SAME entity, is clean. The guard holds the entity `in_review` and
     the SECOND review record is the one with the "earlier concern has
     not been cleared" banner -- `b02_second_review` in the written seed.

No account password, API key or confidential value is written to the
output file or to this script; every account used is one of the fixed
`journey-*@synthetic-test.docuaction.invalid` identities already created
by test_sam_e2e_delivery_path._ensure_journey_users(), called here the
same way every other live-journey seed in this repository calls it.

USAGE (see tests/fixtures/seeded/LIVE-PARTB-RECIPE-2026-10-04.md for the
full startup/teardown recipe this script is one step of):

    SECRET_KEY=<64 chars> ALLOWED_HOSTS=* \
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5534/<disposable db> \
    python tests/fixtures/seeded/seed_live_partb.py <output_seed.json>
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid

sys.path.insert(0, ".")
sys.path.insert(0, "tests")


async def main(out_path: str) -> None:
    import pytest
    from sqlalchemy import select

    import test_seeded_corpus_b_2026_10_04 as corpus_b
    import test_prior_risk_not_cleared_2026_10_04 as prior_risk_mod
    import test_sam_e2e_delivery_path as sam

    from app.core.database import async_session_maker
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    await sam._ensure_journey_users()

    # ── 1 & 2. the 12-seed corpus + one preflight-only row ──────────────────
    tag = uuid.uuid4().hex[:8]
    raw, npi_to_seed, names = corpus_b._delivery(tag)

    # Append a 13th row with a checksum-invalid NPI, same shape as the
    # preflight suite's own fixture (tests/test_preflight.py), so the
    # delivery's file-level check has one real OPEN finding to report.
    bad_npi = "1982916079"  # same known-bad checksum used in test_preflight.py
    extra = {
        "id": f"9.99.777.corpus.{tag}.PF", "orgManagingOrg": f"9.99.777.{tag}",
        "sequoiaorgtype": "Participant", "organizationNodeType": "initiating-node",
        "NPI": bad_npi, "TEFCAID": f"TEFCA-CORPUS-{tag}-PF",
        "HCID": f"urn:oid:9.99.777.corpus.{tag}.PF", "active": "true",
        "hl7orgrole": "provider", "name": f"SYNTHETIC-TRACE CORPUS {tag} PREFLIGHT-ONLY",
        "partOf": f"9.99.777.{tag}", "address_text": "Primary",
        "address_line": "999 Synthetic Corpus Way", "address_city": "Testville",
        "address_state": "TX", "address_postalCode": "75099", "address_country": "US",
        "purposesofuse": "T-TRTMNT", "domains": "RCE",
    }
    extra_line = "|".join(str(extra.get(f, "")) for f in RCE_FIELDS)
    raw = raw.rstrip(b"\n") + b"\n" + extra_line.encode("utf-8") + b"\n"

    sources = corpus_b._Sources(npi_to_seed, names)
    mp = pytest.MonkeyPatch()
    sources.install(mp)
    try:
        intake_id = await corpus_b._promote(raw, tag)
        refs = await corpus_b._refs(intake_id)
        # Only the 12 real corpus seeds go to verify_and_classify; the
        # preflight-only row is deliberately excluded from this list so it
        # cannot affect classification, exactly as it never does for a real
        # delivery that happens to carry one bad identifier on an unrelated
        # organisation.
        corpus_refs = [r for r in refs if corpus_b._seed_of(
            await _name_of(r, intake_id)) is not None]
        by_seed = await corpus_b._cycle(corpus_refs, intake_id)
    finally:
        mp.undo()

    # ── attach a matching, already-SUCCEEDED delivery-job row ──────────────
    # ingest_delivery()/_promote() (the same call
    # test_seeded_corpus_b_2026_10_04.py itself uses) create an
    # rce_source_intakes row directly and never create an rce_delivery_jobs
    # row. The deliveries LIST page and the job-detail route's "no delivery
    # job or intake with this id" lookup both key off rce_delivery_jobs, so
    # a bare intake with zero matching jobs cannot be opened by id at all --
    # not a one-field miss, a hard requirement. Build the job row that a
    # real registration of these exact bytes would have produced, pointed at
    # the intake already built above, in its real terminal state (the run
    # already completed, synchronously, above).
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce import models as rcem
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    async with async_session_maker() as db:
        intake = await db.get(rcem.RceSourceIntake, intake_id)
        identity = jobs.job_identity(sha256=intake.sha256, delivery_label=intake.delivery_label,
                                     received_date=intake.received_at)
        job_row = await jobs.request_job(
            db, identity=identity, original_filename=intake.original_filename,
            storage_path=intake.storage_path, sha256=intake.sha256,
            file_size_bytes=intake.file_size_bytes,
            registered_by="seed-live-partb@synthetic-test.docuaction.invalid",
            delivery_label=intake.delivery_label, declared_delimiter=intake.delimiter,
            received_date=intake.received_at)
    async with async_session_maker() as db:
        job_row = await db.get(RceDeliveryJob, job_row.id)
        job_row.source_intake_id = intake_id
        job_row.state = RceDeliveryJob.STATE_SUCCEEDED
        job_row.stage = RceDeliveryJob.STAGE_READY
        job_row.active_marker = None  # terminal: NULL, never False -- see the model's own comment
        job_row.started_at = intake.received_at
        job_row.completed_at = intake.received_at
        job_row.records_received = intake.record_count
        job_row.records_processed = intake.record_count
        job_row.reconciliation_passed = True
        await db.commit()
        job_id_for_ui = job_row.id
    print(f"attached delivery job job_id={job_id_for_ui} -> intake {intake_id}")

    # ── preflight: produce the file-level "Clear with findings" state ──────
    from app.tefca_registry.rce import preflight as pf

    async with async_session_maker() as db:
        run = await pf.run_preflight(db, intake_id, actor="seed-live-partb")
    print(f"preflight gate={run['classification_gate']} run_id={run['run_id']}")

    # ── 3. request (never approve, never run) a SAM.gov recovery recheck ───
    from app.Tefca import source_policy as sp
    from app.tefca_registry.rce import recheck_models as rm
    from app.tefca_registry.rce import rechecks

    class _AnalystUser:
        def __init__(self):
            self.id = uuid.uuid4()
            self.role = "reviewer"
            self.email = sam.JOURNEY_ANALYST_EMAIL

    async with async_session_maker() as db:
        job = await rechecks.request_recheck(
            db, intake_id, trigger_kind=rm.TRIGGER_SOURCE_RECOVERY,
            source_id=sp.SAM_GOV,
            trigger_ref="SEED-LIVE-PARTB-2026-10-04 SAM recovery requested",
            rationale="Round 27 live-browser seed: requested by the Analyst; "
                      "left for a QA Lead to approve and run in the browser.",
            user=_AnalystUser())
    print(f"recheck job_id={job['job_id']} state={job['state']}")

    # ── 4. the second-cycle, uncleared-concern entity ───────────────────────
    # ONE monkeypatch for both cycles: _seed_one patches NPPES/LEIE clean,
    # _cycle patches only SAM per call -- both must stay active together
    # across cycle 1 AND cycle 2, exactly as the original test function
    # (a single pytest `monkeypatch` fixture, undone once at test end) does.
    mp2 = pytest.MonkeyPatch()
    try:
        intake2, refs2 = await prior_risk_mod._seed_one(mp2)
        first = (await prior_risk_mod._cycle(
            mp2, refs2, intake2, excluded=True))["outcomes"][0]
        # Left un-adjudicated on purpose (the whole point of the guard): no
        # reviewer_resolution, no QA approval, nothing simulated as a real
        # decision.
        second = (await prior_risk_mod._cycle(
            mp2, refs2, intake2, excluded=False))["outcomes"][0]
    finally:
        mp2.undo()
    print(f"b02_second_review={second['review_id']} "
          f"prior_risk_not_cleared={second.get('prior_risk_not_cleared')}")

    # ── find a SAM-fault seed's review_id for direct workspace navigation ──
    b05 = by_seed.get("B05") or next(iter(by_seed.values()))

    seed = {
        "job_id": str(job_id_for_ui),
        "intake_id": str(intake_id),
        "reviews": {sid: o["review_id"] for sid, o in by_seed.items()},
        "b02_second_review": second["review_id"],
        "recheck_job_id": job["job_id"],
        "preflight_run_id": run["run_id"],
        "preflight_gate": run["classification_gate"],
        "sam_fault_seeds": ["B04", "B05", "B06"],
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(seed, fh, indent=2)
    print(f"wrote {out_path}")


async def _name_of(curated_oid: str, intake_id) -> str:
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        row = (await db.execute(
            select(m.RceCuratedRecord.name)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.rce_org_oid == curated_oid))).scalar_one_or_none()
    return row or ""


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
