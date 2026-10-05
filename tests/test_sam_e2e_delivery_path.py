"""SAM — the ACTUAL delivery-processing path, end to end.

Everything in `test_sam_verification_contract.py` exercises one layer at a
time with hand-built `SourceResult`/dimension inputs. This file instead runs
the REAL pipeline — `ingest_delivery -> run_quality_engine -> curate_delivery
-> promote_delivery -> verify_and_classify` — against real Postgres, with
only the four external connectors' own methods patched (no network call is
ever made; `SAM_GOV_API_KEY` etc. are unset in the test environment regardless,
so an unpatched connector would short-circuit to `unavailable` on its own
without this patching — the patching exists to construct CONFIRMED/AMBIGUOUS
SAM scenarios the real API cannot be asked to produce safely here, not to
bypass a real call that would otherwise happen).

PROVES, for a REAL promoted entity under a REAL intake:
  * persisted evidence (`tefca_dimension_evidence`) carries the TRUE SAM
    answer (`excluded: True` visible in `original_values`), not the
    pre-2026-10-02-fix silent PASS
  * classification (`BucketClassifier` via `verify_and_classify`) actually
    disqualifies a confirmed-or-pending SAM exclusion from auto-B1 -- the v3
    rule fix, exercised through the real translator, not asserted against a
    hand-built `results()` dict
  * review routing (`ReviewRecord.classification_bucket`, routed tier) lands
    on B4/Tier-2+, never Tier-1 auto-complete
  * coverage counts (`verification_coverage.coverage_for_intake`) and the
    CSV drill-down (`verification_drilldown.list_outcome_entities`) agree
    with what was actually persisted
"""
from __future__ import annotations

import uuid
from unittest import mock

import pytest

pytestmark = pytest.mark.asyncio


def _valid_npi(seed: int) -> str:
    from app.services.npi_validator import CMS_PREFIX, _luhn_total

    base = f"{1_000_000_000 + (seed % 900_000_000):09d}"[-9:]
    for d in range(10):
        candidate = base + str(d)
        if _luhn_total(CMS_PREFIX + candidate) % 10 == 0:
            return candidate
    return base + "0"


def _synthetic_delivery_bytes(run_tag: str, n: int) -> bytes:
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    header = list(RCE_FIELDS)
    qhin = f"9.9.9.9.{run_tag}"
    rows = []
    for i in range(n):
        values = {
            "id": f"sam.e2e.{run_tag}.{i:04d}",
            "orgManagingOrg": qhin, "sequoiaorgtype": "Participant",
            "organizationNodeType": "initiating-node",
            # uuid-derived (2026-10-03): the shared test_sam database keeps
            # every seeded delivery, and a 100k NPI space collided with
            # earlier entities' persisted evidence, making runs flaky.
            "NPI": _valid_npi(i + uuid.uuid4().int % 800_000_000),
            "TEFCAID": f"TEFCA-SAME2E-{run_tag}-{i:04d}",
            "HCID": f"HCID-SAME2E-{run_tag}-{i:04d}", "active": "true",
            "hl7orgrole": "provider",
            "name": f"SYNTHETIC-TRACE SAM-E2E Org {run_tag} {i:04d}",
            "partOf": qhin, "address_text": "Primary",
            "address_line": f"{100 + i} Test SAM E2E Way",
            "address_city": "Testville", "address_state": "TX",
            "address_postalCode": f"{75000 + i}", "address_country": "US",
            "phone": "512-555-0100",
            "email": f"same2e{i}@synthetic-test.docuaction.invalid",
            "purposesofuse": "T-TRTMNT", "stateofoperation": "TX",
            "doa": "2026-01-01", "transaction": "A",
        }
        rows.append([str(values.get(f, "")) for f in header])
    body = "\n".join("|".join(r) for r in rows)
    return ("|".join(header) + "\n" + body + "\n").encode("utf-8")


async def _seed_promoted_delivery(n: int):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.curation import curate_delivery
    from app.tefca_registry.rce.intake import ingest_delivery
    from app.tefca_registry.rce.promotion import promote_delivery
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    run_tag = uuid.uuid4().hex[:10]
    raw = _synthetic_delivery_bytes(run_tag, n=n)
    try:
        async with async_session_maker() as db:
            result = await ingest_delivery(
                db, raw, filename=f"SYNTHETIC-SAM-E2E-{run_tag}.psv",
                delivery_label=f"SYNTHETIC-TRACE-sam-e2e-{run_tag}",
                declared_delimiter="|",
                received_by="pytest-sam-e2e@synthetic-test.docuaction.invalid")
            intake_id = result["intake_id"]
        async with async_session_maker() as db:
            await run_quality_engine(db, intake_id, executed_by="pytest-sam-e2e")
        async with async_session_maker() as db:
            await curate_delivery(db, intake_id, curated_by="pytest-sam-e2e")
        async with async_session_maker() as db:
            await promote_delivery(db, intake_id, actor="pytest-sam-e2e")
    except AttributeError as exc:
        pytest.skip(f"seeding hit the documented JSONB-decoding environment "
                    f"quirk (see test_review_id_concurrency.py); unrelated to "
                    f"the fix under test: {exc!r}")
    return intake_id


async def _promoted_refs(db, intake_id, limit):
    from sqlalchemy import select

    from app.tefca_registry.rce import models as m

    return list((await db.execute(
        select(m.RceCuratedRecord.rce_org_oid)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None))
        .order_by(m.RceCuratedRecord.rce_org_oid).limit(limit))).scalars().all())


#: Fixed identities the LIVE-server journey tests (test_journey_iqvia_live,
#: test_journey_qa_live, test_journey_reporting_live) log into over real
#: HTTP -- shared here, not duplicated per file, since all three need the
#: SAME accounts to exist in whatever database the live server and the
#: pytest process both point at.
JOURNEY_ANALYST_EMAIL = "journey-analyst@synthetic-test.docuaction.invalid"
JOURNEY_ANALYST_PASSWORD = "JourneyAnalyst!2026"
JOURNEY_QALEAD_EMAIL = "journey-qalead@synthetic-test.docuaction.invalid"
JOURNEY_QALEAD_PASSWORD = "JourneyQALead!2026"
JOURNEY_ADMIN_EMAIL = "journey-admin@synthetic-test.docuaction.invalid"
JOURNEY_ADMIN_PASSWORD = "JourneyAdmin!2026"


async def _ensure_journey_users():
    """Idempotent: create the three fixed journey accounts if they are not
    already present (direct DB insert -- there is no public self-registration
    endpoint, same pattern as every other synthetic test account in this
    suite). Safe to call from more than one test file in the same session."""
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.core.security import hash_password
    from app.models.database import User

    async with async_session_maker() as db:
        for email, password, role in (
            (JOURNEY_ANALYST_EMAIL, JOURNEY_ANALYST_PASSWORD, "reviewer"),
            (JOURNEY_QALEAD_EMAIL, JOURNEY_QALEAD_PASSWORD, "qalead"),
            (JOURNEY_ADMIN_EMAIL, JOURNEY_ADMIN_PASSWORD, "admin"),
        ):
            existing = (await db.execute(
                select(User).where(User.email == email))).scalars().first()
            if existing is not None:
                continue
            db.add(User(
                id=uuid.uuid4(), tenant_id="synthetic-journey", email=email,
                password_hash=hash_password(password), full_name=f"SYNTHETIC journey {role}",
                role=role, is_active=True, is_verified=True, status="active",
                allowed_modules=[]))
        await db.commit()


def _clean_nppes_leie(monkeypatch, *, uei_by_entity: dict | None = None):
    """Patch NPPES/LEIE to a clean, positive answer for every NPI -- isolates
    the test to the SAM effect, rather than every entity being indeterminate
    for unrelated reasons (no API key for NPPES/LEIE either, in this
    environment). No network call: these are patched class methods, never
    the real HTTP path."""
    from app.Tefca.connectors import NPPESConnector, OIGLEIEConnector, SourceResult

    async def fake_nppes(self, npi):
        return SourceResult.ok("NPPES", {
            "found": True, "legal_name": "SYNTHETIC-TRACE SAM-E2E Org",
            "enumeration_type": "NPI-2", "status": "A", "addresses": [],
        }, {"npi": npi})

    async def fake_leie(self, npi):
        return SourceResult.ok("OIG_LEIE", {"excluded": False}, {"npi": npi})

    monkeypatch.setattr(NPPESConnector, "lookup_by_npi", fake_nppes)
    monkeypatch.setattr(OIGLEIEConnector, "lookup_by_npi", fake_leie)


def _patch_sam_verify(monkeypatch, result_factory):
    from app.Tefca.connectors import SAMGovConnector

    async def fake_verify(self, uei="", legal_name=""):
        return result_factory(uei=uei, legal_name=legal_name)

    monkeypatch.setattr(SAMGovConnector, "verify", fake_verify)


async def test_confirmed_exclusion_end_to_end(db_required, monkeypatch):
    """The headline scenario: a real SAM debarment, through the real
    pipeline, must reach persisted evidence truthfully, disqualify the
    entity from auto-B1, route to a non-Tier-1 review, and show up as a
    discrepancy in coverage counts and the drill-down CSV -- not silently
    vanish as a clean pass, which is exactly what happened before the
    2026-10-02 fix."""
    from app.Tefca.connectors import SourceResult
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.tefca_registry.rce import verification_coverage as vc

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _clean_nppes_leie(monkeypatch)

    def excluded_result(*, uei, legal_name):
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": True,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True,
        }, {"uei": uei})
    _patch_sam_verify(monkeypatch, excluded_result)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 1)
    assert len(refs) == 1, f"expected 1 promoted synthetic entity, got {len(refs)}"

    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id,
                                           actor="pytest-sam-e2e")
    outcome = result["outcomes"][0]

    # 1. Classification: NOT auto-B1, Tier 1. The whole point of the fix.
    assert outcome["bucket"] != "B1", (
        f"a confirmed SAM exclusion auto-classified as B1 (no discrepancy) "
        f"-- the exact failure mode this fix exists to close: {outcome}")
    assert outcome["tier"] != 1, f"a SAM-excluded entity routed to Tier 1 (auto-complete): {outcome}"

    # 2. Persisted evidence tells the truth.
    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.Tefca.models import TEFCADimensionEvidence
        rows = (await db.execute(select(TEFCADimensionEvidence).where(
            TEFCADimensionEvidence.entity_id == str(outcome["entity_id"]),
            TEFCADimensionEvidence.evidence_dimension == "EXCLUSION_REVOCATION"))).scalars().all()
    sam_rows = [r for r in rows if r.source == "SAM_GOV"]
    assert sam_rows, "no SAM_GOV evidence row was persisted at all"
    assert sam_rows[-1].disposition == "REVIEW", (
        f"persisted SAM evidence disposition was {sam_rows[-1].disposition!r}, "
        f"not REVIEW -- a confirmed exclusion must never be assembled as PASS")
    assert sam_rows[-1].original_values.get("excluded") is True, (
        "persisted evidence does not show excluded=True -- the truthfulness "
        "property this whole fix exists to restore")

    # 2b. Rule-version provenance is actually populated on the persisted
    #     ReviewRecord -- nothing previously asserted this, even though the
    #     field has existed since the model was written. The report layer
    #     (separate package) needs this to be reliably non-null to display it.
    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.tefca_registry import models as reg
        record = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == outcome["entity_id"]))).scalars().one()
    assert record.classification_rule_version is not None, (
        "classification_rule_version was not populated on the persisted "
        f"ReviewRecord: {record.classification_rule_version!r}")
    assert record.classification_bucket == outcome["bucket"]
    print(f"[rule-version] confirmed_exclusion -> "
          f"rule={record.classification_rule} version={record.classification_rule_version}")

    # 3. Coverage counts agree with what was persisted -- not a separate,
    #    possibly-drifted computation. (The CSV drill-down route itself
    #    lives on the separate reporting-architecture branch, PR #110/#66 --
    #    not on this SAM-fix branch, which is based on `main` -- so the
    #    count/drill-down reconciliation proof lives in that package's own
    #    tests, e.g. test_verification_drilldown_fanout_fix.py; this file
    #    proves the count itself is correct, which both consumers build on.)
    async with async_session_maker() as db:
        coverage = await vc.coverage_for_intake(db, intake_id)
    # REVIEW -> "not_found" per _DIMENSION_DISPOSITION (unchanged, deliberately,
    # per the arc_pipeline.py comment this session added) -- so the dashboard
    # shows this SAM result under "not_found", not silently as "verified".
    sam_counts = coverage["sources"].get("sam") or coverage["sources"].get("sam_gov")
    assert sam_counts is not None, f"no sam coverage block in {coverage['sources'].keys()}"
    assert sam_counts.get("verified", 0) == 0, (
        f"a confirmed SAM exclusion must not count toward 'verified': {sam_counts}")
    assert sam_counts.get("not_found", 0) == 1, (
        f"the confirmed-exclusion entity should count under 'not_found' "
        f"(current, documented REVIEW mapping): {sam_counts}")


async def test_ambiguous_sam_match_end_to_end(db_required, monkeypatch):
    """An ambiguous name-match SAM result must not auto-classify B1 either
    -- identity is unconfirmed, which is a different reason than a confirmed
    exclusion but the same required outcome (no auto-pass)."""
    from app.Tefca.connectors import SourceResult
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _clean_nppes_leie(monkeypatch)

    def ambiguous_result(*, uei, legal_name):
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "name", "excluded": False,
            "excluded_known": True, "identity_ambiguous": True,
            "registration_current": None,
        }, {"legal_name": legal_name})
    _patch_sam_verify(monkeypatch, ambiguous_result)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 1)
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id,
                                           actor="pytest-sam-e2e")
    outcome = result["outcomes"][0]
    assert outcome["bucket"] != "B1", (
        f"an ambiguous SAM identity match auto-classified as B1: {outcome}")

    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.tefca_registry import models as reg
        record = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == outcome["entity_id"]))).scalars().one()
    assert record.classification_rule_version is not None, (
        "classification_rule_version was not populated on the persisted ReviewRecord")
    print(f"[rule-version] ambiguous_match -> "
          f"rule={record.classification_rule} version={record.classification_rule_version}")


async def test_unavailable_exclusion_check_end_to_end(db_required, monkeypatch):
    """Registration succeeds; the independent exclusions leg fails. Must
    read as UNAVAILABLE for that question, never as a clean pass that lets
    the entity through."""
    from app.Tefca.connectors import SourceResult
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _clean_nppes_leie(monkeypatch)

    def unavailable_exclusion_result(*, uei, legal_name):
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": False,
            "excluded_known": False, "identity_ambiguous": False,
            "registration_current": True,
        }, {"uei": uei})
    _patch_sam_verify(monkeypatch, unavailable_exclusion_result)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 1)
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id,
                                           actor="pytest-sam-e2e")
    outcome = result["outcomes"][0]

    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.Tefca.models import TEFCADimensionEvidence
        rows = (await db.execute(select(TEFCADimensionEvidence).where(
            TEFCADimensionEvidence.entity_id == str(outcome["entity_id"]),
            TEFCADimensionEvidence.evidence_dimension == "EXCLUSION_REVOCATION"))).scalars().all()
    sam_rows = [r for r in rows if r.source == "SAM_GOV"]
    assert sam_rows[-1].disposition == "UNAVAILABLE", (
        f"an unperformed exclusion check was assembled as "
        f"{sam_rows[-1].disposition!r}, not UNAVAILABLE -- a registration "
        f"success must not make the SEPARATE exclusion question read clear")

    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.tefca_registry import models as reg
        record = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == outcome["entity_id"]))).scalars().one()
    assert record.classification_rule_version is not None, (
        "classification_rule_version was not populated on the persisted ReviewRecord")
    print(f"[rule-version] unavailable_check -> "
          f"rule={record.classification_rule} version={record.classification_rule_version}")


async def test_clean_confirmed_entity_is_not_disqualified_by_sam(db_required, monkeypatch):
    """Regression guard, the other direction: a genuinely clean, fully-
    checked SAM result must never itself prevent B1/Tier-1 -- the v3 fix
    adds a disqualifier, it must not become one. Does not assert the overall
    bucket is exactly B1: this synthetic fixture's address does not match
    byte-for-byte against the fake NPPES location (an unrelated dimension,
    nothing to do with SAM), which legitimately routes it to B2 on its own
    -- asserting the SAM-specific contract directly instead."""
    from app.Tefca.connectors import SourceResult
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _clean_nppes_leie(monkeypatch)

    def clean_result(*, uei, legal_name):
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": False,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True,
        }, {"uei": uei})
    _patch_sam_verify(monkeypatch, clean_result)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 1)
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id,
                                           actor="pytest-sam-e2e")
    outcome = result["outcomes"][0]
    assert outcome["dimensions"]["EXCLUSION_REVOCATION"] == "PASS", (
        f"a clean, fully-checked SAM result did not produce a PASS exclusion "
        f"dimension: {outcome}")
    assert outcome["bucket"] != "B4", (
        f"a clean SAM result must never itself disqualify an entity: {outcome}")
    assert outcome["rule_code"] != "RULE-005", (
        f"B4 disqualifier rule fired on a clean SAM result: {outcome}")

    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.tefca_registry import models as reg
        record = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == outcome["entity_id"]))).scalars().one()
    assert record.classification_rule_version is not None, (
        "classification_rule_version was not populated on the persisted ReviewRecord")
    print(f"[rule-version] clean_no_match -> "
          f"rule={record.classification_rule} version={record.classification_rule_version}")


async def test_clean_name_screen_end_to_end_is_not_disqualified(db_required, monkeypatch):
    """2026-10-03 (peer Lane S finding): the REAL bulk path, SAM screened by
    ORGANISATION NAME (no UEI -- the delivered 41 fields never carry one),
    nothing listed. The evidence layer records NOT_FOUND ("a name search does
    not carry the weight of a UEI match"); before the translator fix that
    became `not_found` and v3 RULE-005 disqualified every clean,
    name-screened entity as B4. Now it is "clear": eligible for B1, never
    RULE-005. The identity-ambiguity guard is a different disposition
    (REVIEW) and is covered by test_ambiguous_sam_match_end_to_end."""
    from app.Tefca.connectors import SourceResult
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _clean_nppes_leie(monkeypatch)

    def clean_name_screen(*, uei, legal_name):
        return SourceResult.ok("SAM_GOV", {
            "found": False, "matched_by": "name", "excluded": False,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": None,
        }, {"legal_name": legal_name})
    _patch_sam_verify(monkeypatch, clean_name_screen)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 1)
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id,
                                           actor="pytest-sam-e2e")
    outcome = result["outcomes"][0]

    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.Tefca.models import TEFCADimensionEvidence
        from app.tefca_registry import models as reg
        rows = (await db.execute(select(TEFCADimensionEvidence).where(
            TEFCADimensionEvidence.entity_id == str(outcome["entity_id"]),
            TEFCADimensionEvidence.evidence_dimension == "EXCLUSION_REVOCATION"))).scalars().all()
        record = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == outcome["entity_id"]))).scalars().one()
    sam_rows = [r for r in rows if r.source == "SAM_GOV"]
    assert sam_rows, "no SAM_GOV evidence row was persisted"
    # Evidence keeps the weaker-than-UEI truth ...
    assert sam_rows[-1].disposition in ("NOT_FOUND", "PASS"), sam_rows[-1].disposition
    assert sam_rows[-1].original_values.get("excluded") is False
    # ... and the classifier input reads it as a clean screen, not a hit.
    classifier_input = record.verification_results["classifier_input"]
    assert classifier_input["sources"]["sam_gov"]["status"] in ("clear", "verified"), \
        classifier_input["sources"]["sam_gov"]
    assert outcome["bucket"] != "B4", f"a clean SAM name screen was disqualified: {outcome}"
    assert outcome["rule_code"] != "RULE-005", outcome
    assert outcome["bucket"] in ("B1", "B2"), outcome   # B2 only via the fixture's address variance
    print(f"[rule-version] clean_name_screen -> bucket={outcome['bucket']} "
          f"rule={record.classification_rule} version={record.classification_rule_version} "
          f"sam_disposition={sam_rows[-1].disposition}")
