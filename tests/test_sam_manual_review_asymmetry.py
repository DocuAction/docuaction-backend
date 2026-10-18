"""The SAM.gov asymmetry between the two real callers of
`BucketClassifier.classify()` -- proven, then FIXED (2026-10-03).

  * `arc_pipeline.verify_and_classify` (bulk/RCE path) queries SAM.gov,
    persists the answer as `tefca_dimension_evidence` (EXCLUSION_REVOCATION /
    SAM_GOV), and a confirmed-or-pending exclusion disqualifies the entity.
  * `review_service.run_review` (manual single-entity path) never queries
    SAM.gov live (`probe_sources` stubs `sam_gov` as NOT_CHECKED). Until the
    fix it also never READ the persisted evidence, so a confirmed exclusion
    persisted at B4 was followed by a newer manual ReviewRecord at B1.
    `run_review` now consumes the entity's most recent persisted
    exclusion/revocation evidence through the bulk path's own
    `_DISPOSITION_TO_STATE` (`review_service.apply_persisted_exclusion_evidence`).

Three cases, each with its own test:
  (a) persisted bulk SAM exclusion exists -> manual path now B4/RULE-005; the
      contradiction is gone; the historical bulk record is untouched;
  (b) no persisted evidence at all -> the NOT_CHECKED stub stands and the
      v3 rule set classifies clean live sources as B1/RULE-001 -- the SAME
      outcome the bulk path produces when SAM is unavailable, by the v2
      design decision "SAM is a disqualifier, never a requirement"
      (bucket_classifier._v2_rules docstring). Asserted explicitly; changing
      it is a rule-set (policy) decision, not this fix's;
  (c) staleness -> the evidence generation timestamp and age are carried in
      the rationale; no cutoff is applied (policy, proposed in the doc).

The live probe set (`IMPLEMENTED_SOURCES`) is unchanged and no connector
call is added -- the first two tests still prove that.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio


class _FakeResult:
    """Mimics SourceResult: success means the query completed, not the answer."""
    def __init__(self, success=True, data=None, error=None):
        self.success, self.data, self.error = success, data, error


def _valid_npi() -> str:
    from app.services.npi_validator import CMS_PREFIX, _luhn_total

    base = "120588014"
    for d in range(10):
        candidate = base + str(d)
        if _luhn_total(CMS_PREFIX + candidate) % 10 == 0:
            return candidate
    raise AssertionError("no valid check digit found — fixture bug")


class _FakeDB:
    """Enough of an AsyncSession for probe_sources: the only query it issues
    before the connector loop is 'does this entity have an NPI identifier'."""
    def __init__(self, npi: str):
        self._npi = npi

    async def execute(self, *_a, **_k):
        class R:
            def __init__(self, npi):
                self._npi = npi

            def scalar_one_or_none(self):
                return self._npi
        return R(self._npi)


def _clean_nppes_pecos_leie_mgr():
    """A connector manager whose nppes/pecos/leie all report a clean,
    positive result -- isolates the test to the SAM effect."""

    class _Conn:
        async def lookup_by_npi(self, npi):
            return _FakeResult(success=True, data={
                "found": True, "excluded": False, "legal_name": "Test Org",
                "enumeration_type": "NPI-2", "status": "A",
            })

    class _Mgr:
        nppes = pecos = leie = _Conn()
        sam_gov = _Conn()

    return _Mgr()


async def test_manual_review_never_queries_sam_live_even_when_a_connector_exists(monkeypatch):
    """Still true after the fix: no LIVE SAM call is ever made by the manual
    path. Attach a SAM connector that WOULD report a debarment -- it is never
    invoked."""
    from app.tefca_registry import review_service as svc
    import app.Tefca.connectors as conns

    calls = {"sam_gov": 0}

    class _SamThatWouldFlag:
        async def verify(self, uei="", legal_name=""):
            calls["sam_gov"] += 1
            return _FakeResult(success=True, data={
                "excluded": True, "debarred": True, "matched_by": "uei",
                "identity_ambiguous": False,
            })

    mgr = _clean_nppes_pecos_leie_mgr()
    mgr.sam_gov = _SamThatWouldFlag()
    monkeypatch.setattr(conns, "SourceConnectorManager", lambda: mgr)

    db = _FakeDB(npi=_valid_npi())
    sources = await svc.probe_sources(db, "entity-would-be-excluded")

    assert calls["sam_gov"] == 0
    assert sources["sam_gov"]["status"] == svc.NOT_CHECKED
    assert sources["nppes"]["status"] in (svc.VERIFIED, "clear")
    assert sources["oig_leie"]["status"] == "clear"
    assert svc.IMPLEMENTED_SOURCES == ("nppes", "pecos", "oig_leie")


async def test_probe_output_alone_is_unaffected_by_what_sam_would_say(monkeypatch):
    """`probe_sources` output (no persisted evidence folded in) classifies
    identically whatever a live SAM mock would have said -- the fix lives in
    `run_review`, not in the probe."""
    from app.tefca_registry import review_service as svc
    from app.tefca_registry.bucket_classifier import BucketClassifier, SEED_RULES_V3
    import app.Tefca.connectors as conns

    classifier = BucketClassifier(rules=SEED_RULES_V3)

    async def _classify_with_sam_mock(sam_would_flag: bool):
        mgr = _clean_nppes_pecos_leie_mgr()

        class _Sam:
            async def verify(self, uei="", legal_name=""):
                return _FakeResult(success=True, data={"excluded": sam_would_flag,
                                                       "debarred": sam_would_flag})
        mgr.sam_gov = _Sam()
        monkeypatch.setattr(conns, "SourceConnectorManager", lambda: mgr)

        sources = await svc.probe_sources(_FakeDB(npi=_valid_npi()), "entity")
        return classifier.classify({"sources": sources, "fields": {}, "confidence_score": None})

    assert ((await _classify_with_sam_mock(True)).bucket
            == (await _classify_with_sam_mock(False)).bucket)


# ── The fix, unit level: precedence between live and persisted ───────────────

def _row(source, disposition, stamp="2026-10-02T12:00:00+00:00"):
    return SimpleNamespace(id=uuid.uuid4(), source=source, disposition=disposition,
                           generation_timestamp=stamp, review_id="REV-2026-000001",
                           rule_applied="D3", query_identifier="UEI-X")


def test_persisted_exclusion_overrides_the_static_stub():
    from app.tefca_registry import review_service as svc

    sources = {"sam_gov": {"status": svc.NOT_CHECKED, "reason": "stub"}}
    used = svc.apply_persisted_exclusion_evidence(sources, {"SAM_GOV": _row("SAM_GOV", "REVIEW")})
    assert sources["sam_gov"]["status"] == svc.NOT_FOUND
    assert sources["sam_gov"]["persisted_evidence"]["disposition"] == "REVIEW"
    assert used and used[0]["source"] == "SAM_GOV" and used[0]["age_days"] is not None
    assert "generated 2026-10-02T12:00:00+00:00" in svc.persisted_evidence_rationale(used)


def test_persisted_clean_pass_becomes_verified_and_unavailable_stays_unavailable():
    from app.tefca_registry import review_service as svc

    sources = {"sam_gov": {"status": svc.NOT_CHECKED}}
    svc.apply_persisted_exclusion_evidence(sources, {"SAM_GOV": _row("SAM_GOV", "PASS")})
    assert sources["sam_gov"]["status"] == svc.VERIFIED

    sources = {"sam_gov": {"status": svc.NOT_CHECKED}}
    svc.apply_persisted_exclusion_evidence(sources, {"SAM_GOV": _row("SAM_GOV", "UNAVAILABLE")})
    assert sources["sam_gov"]["status"] == svc.UNAVAILABLE


def test_a_worse_live_answer_is_kept_and_a_worse_persisted_one_wins():
    from app.tefca_registry import review_service as svc

    # live LEIE says excluded; persisted says REVIEW (-> not_found): keep live.
    sources = {"oig_leie": {"status": "excluded"}}
    used = svc.apply_persisted_exclusion_evidence(sources, {"OIG_LEIE": _row("OIG_LEIE", "REVIEW")})
    assert sources["oig_leie"]["status"] == "excluded" and used == []
    assert sources["oig_leie"]["persisted_evidence_seen"]["disposition"] == "REVIEW"

    # live LEIE says clear; persisted says REVIEW: the persisted (worse) wins.
    sources = {"oig_leie": {"status": "clear"}}
    used = svc.apply_persisted_exclusion_evidence(sources, {"OIG_LEIE": _row("OIG_LEIE", "REVIEW")})
    assert sources["oig_leie"]["status"] == svc.NOT_FOUND
    assert used[0]["superseded_live_status"] == "clear"

    # live LEIE clear; persisted PASS: nothing to override, nothing invented.
    sources = {"oig_leie": {"status": "clear"}}
    assert svc.apply_persisted_exclusion_evidence(
        sources, {"OIG_LEIE": _row("OIG_LEIE", "PASS")}) == []
    assert sources["oig_leie"]["status"] == "clear"


async def test_review_service_reads_persisted_evidence_only_through_the_helper():
    """The one sanctioned read. Anything else touching the evidence table
    from this module should be reviewed, not assumed."""
    from app.tefca_registry import review_service as svc

    src = inspect.getsource(svc)
    assert src.count("TEFCADimensionEvidence") == 1
    assert "sources = await probe_sources(db, entity.id)" in src
    assert "latest_persisted_exclusion_evidence(db, entity.id)" in src


# ── Persisted-evidence cases, end to end on real Postgres ────────────────────
# Seeding helpers mirror test_sam_e2e_delivery_path.py (same synthetic shape,
# same connector-method patching, no network).

def _e2e_valid_npi(seed: int) -> str:
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
            "id": f"sam.asym.{run_tag}.{i:04d}",
            "orgManagingOrg": qhin, "sequoiaorgtype": "Participant",
            "organizationNodeType": "initiating-node",
            # uuid-derived, not hash(run_tag) % 100_000: the shared test_sam
            # database accumulates deliveries across sessions, and a 100k
            # NPI space collides with earlier entities' persisted evidence.
            "NPI": _e2e_valid_npi(i + uuid.uuid4().int % 800_000_000),
            "TEFCAID": f"TEFCA-ASYM-{run_tag}-{i:04d}",
            "HCID": f"HCID-ASYM-{run_tag}-{i:04d}", "active": "true",
            "hl7orgrole": "provider",
            "name": f"SYNTHETIC-TRACE SAM-ASYM Org {run_tag} {i:04d}",
            "partOf": qhin, "address_text": "Primary",
            "address_line": f"{100 + i} Test SAM Asym Way",
            "address_city": "Testville", "address_state": "TX",
            "address_postalCode": f"{75000 + i}", "address_country": "US",
            "phone": "512-555-0100",
            "email": f"samasym{i}@synthetic-test.docuaction.invalid",
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
                db, raw, filename=f"SYNTHETIC-SAM-ASYM-{run_tag}.psv",
                delivery_label=f"SYNTHETIC-TRACE-sam-asym-{run_tag}",
                declared_delimiter="|",
                received_by="pytest-sam-asym@synthetic-test.docuaction.invalid")
            intake_id = result["intake_id"]
        async with async_session_maker() as db:
            await run_quality_engine(db, intake_id, executed_by="pytest-sam-asym")
        async with async_session_maker() as db:
            await curate_delivery(db, intake_id, curated_by="pytest-sam-asym")
        async with async_session_maker() as db:
            await promote_delivery(db, intake_id, actor="pytest-sam-asym")
    except AttributeError as exc:
        pytest.skip(f"seeding hit the documented JSONB-decoding environment "
                    f"quirk (see test_review_id_concurrency.py): {exc!r}")
    return intake_id


async def _promoted(db, intake_id):
    """(rce_org_oid, canonical_entity_id) of the one promoted synthetic record."""
    from sqlalchemy import select

    from app.tefca_registry.rce import models as m

    row = (await db.execute(
        select(m.RceCuratedRecord.rce_org_oid, m.RceCuratedRecord.canonical_entity_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None)))).first()
    assert row is not None, "no promoted synthetic entity"
    return row[0], row[1]


def _patch_bulk_connectors(monkeypatch, *, sam_excluded: bool):
    """Bulk path: NPPES/LEIE clean, SAM either a CONFIRMED exclusion or a
    clean UEI match. Patched class methods on the real connectors -- never
    the HTTP path."""
    from app.Tefca.connectors import (NPPESConnector, OIGLEIEConnector,
                                      SAMGovConnector, SourceResult)

    async def fake_nppes(self, npi):
        return SourceResult.ok("NPPES", {
            "found": True, "legal_name": "SYNTHETIC-TRACE SAM-ASYM Org",
            "enumeration_type": "NPI-2", "status": "A", "addresses": [],
        }, {"npi": npi})

    async def fake_leie(self, npi):
        return SourceResult.ok("OIG_LEIE", {"excluded": False}, {"npi": npi})

    async def fake_sam_verify(self, uei="", legal_name=""):
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": sam_excluded,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True,
        }, {"uei": uei})

    # 2026-10-04: the two CMS connectors were left unpatched, so these tests
    # queried the live CMS data API for a synthetic NPI and their result
    # depended on the Internet (offline: EXCLUSION_REVOCATION = UNAVAILABLE and
    # the "clean entity" test failed). Deterministic answers, same as the
    # live API gives a synthetic NPI: no revocation record; enrolment not
    # exercised.
    from app.Tefca import cms_ppef

    async def fake_revocation(self, npi):
        return SourceResult.ok(self.SOURCE_NAME, {
            "checked": True, "matches": [],
            "result": "NO_ACTIVE_REVOCATION_RECORD_FOUND"}, {"npi": npi})

    async def fake_ppef(self, npi):
        return SourceResult.unavailable(
            self.SOURCE_NAME, "synthetic: CMS enrolment API not exercised by this test",
            {"npi": npi})

    monkeypatch.setattr(NPPESConnector, "lookup_by_npi", fake_nppes)
    monkeypatch.setattr(OIGLEIEConnector, "lookup_by_npi", fake_leie)
    monkeypatch.setattr(SAMGovConnector, "verify", fake_sam_verify)
    monkeypatch.setattr(cms_ppef.CMSRevocationConnector, "lookup_by_npi", fake_revocation)
    monkeypatch.setattr(cms_ppef.PPEFEnrollmentConnector, "lookup_by_npi", fake_ppef)


def _manual_path_clean_live(monkeypatch):
    """Manual path: clean live NPPES/PECOS/LEIE, no SAM connector at all."""
    import app.Tefca.connectors as conns

    mgr = _clean_nppes_pecos_leie_mgr()
    mgr.sam_gov = None
    monkeypatch.setattr(conns, "SourceConnectorManager", lambda: mgr)


async def _review_records(entity_id):
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    async with async_session_maker() as db:
        return (await db.execute(
            select(reg.ReviewRecord).where(reg.ReviewRecord.entity_id == entity_id)
            .order_by(reg.ReviewRecord.created_at))).scalars().all()


async def test_case_a_persisted_bulk_exclusion_now_disqualifies_on_the_manual_path(
        db_required, monkeypatch):
    """Case (a), fixed. The REAL bulk pipeline persists a confirmed SAM
    exclusion (REVIEW, excluded=True) and a B4 ReviewRecord. The REAL manual
    path on the SAME entity, with clean live answers and no SAM connector,
    must now consume that evidence: sam_gov -> not_found, B4 / RULE-005, the
    evidence timestamp in the rationale, the bulk record byte-for-byte
    untouched."""
    from sqlalchemy import select

    from app.Tefca.models import TEFCADimensionEvidence
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry import review_service as svc
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _patch_bulk_connectors(monkeypatch, sam_excluded=True)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        ref, entity_id = await _promoted(db, intake_id)
    async with async_session_maker() as db:
        bulk = await verify_and_classify(db, [ref], intake_id=intake_id,
                                         actor="pytest-sam-asym")
    outcome = bulk["outcomes"][0]
    assert outcome["bucket"] == "B4" and outcome["rule_code"] == "RULE-005", outcome

    async with async_session_maker() as db:
        sam_rows = [r for r in (await db.execute(select(TEFCADimensionEvidence).where(
            TEFCADimensionEvidence.entity_id == str(entity_id),
            TEFCADimensionEvidence.evidence_dimension == "EXCLUSION_REVOCATION"))
        ).scalars().all() if r.source == "SAM_GOV"]
    assert sam_rows and sam_rows[-1].disposition == "REVIEW"
    stamp = sam_rows[-1].generation_timestamp
    before = [(r.review_id, r.classification_bucket, r.classification_rule,
               r.classification_rule_version, r.classification_rationale)
              for r in await _review_records(entity_id)]
    assert len(before) == 1

    _manual_path_clean_live(monkeypatch)
    async with async_session_maker() as db:
        entity = await db.get(reg.TefcaRegEntity, entity_id)
        manual = await svc.run_review(db, entity, trigger="manual")

    assert manual["verification"]["sam_gov"]["status"] == svc.NOT_FOUND
    assert manual["verification"]["sam_gov"]["persisted_evidence"]["disposition"] == "REVIEW"
    assert manual["classification"]["bucket"] == "B4"
    assert manual["classification"]["rule_code"] == "RULE-005"
    assert "SAM_GOV" in {u["source"] for u in manual["persisted_evidence"]}
    assert "Persisted exclusion/revocation evidence consumed" in manual["classification"]["rationale"]
    assert str(stamp) in manual["classification"]["rationale"]

    after = await _review_records(entity_id)
    assert len(after) == 2
    # Historical record untouched.
    assert (after[0].review_id, after[0].classification_bucket, after[0].classification_rule,
            after[0].classification_rule_version, after[0].classification_rationale) == before[0]
    # New manual record agrees with the evidence; no contradiction persisted.
    assert after[1].review_id == manual["review_id"]
    assert after[1].classification_bucket == "B4"
    assert after[1].classification_rule == "RULE-005"
    assert str(stamp) in after[1].classification_rationale
    persisted = {u["source"]: u for u in after[1].verification_results["persisted_evidence"]}
    assert persisted["SAM_GOV"]["disposition"] == "REVIEW"
    assert persisted["SAM_GOV"]["state"] == "not_found"
    print(f"[asymmetry] bulk={after[0].classification_bucket}/{after[0].classification_rule} "
          f"manual={after[1].classification_bucket}/{after[1].classification_rule}")


async def test_case_b_no_persisted_evidence_keeps_the_disclosed_stub_and_classifies_b1(
        db_required, monkeypatch):
    """Case (b), made explicit. An entity never bulk-processed has no
    persisted evidence; clean live NPPES/PECOS/LEIE and the NOT_CHECKED SAM
    stub classify as B1 / RULE-001 under v3. This is the bulk path's own
    outcome when SAM is unavailable (RULE-001 has no positive SAM
    requirement by the v2 design decision). The stub's reason is disclosed
    on the review; nothing is invented. Changing this bucket is a rule-set
    policy decision -- see SAM_MANUAL_REVIEW_ASYMMETRY.md §4."""
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry import review_service as svc

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        _ref, entity_id = await _promoted(db, intake_id)
    assert await _review_records(entity_id) == []

    _manual_path_clean_live(monkeypatch)
    async with async_session_maker() as db:
        entity = await db.get(reg.TefcaRegEntity, entity_id)
        manual = await svc.run_review(db, entity, trigger="manual")

    assert manual["persisted_evidence"] == []
    assert manual["verification"]["sam_gov"]["status"] == svc.NOT_CHECKED
    assert manual["verification"]["sam_gov"]["reason"] == svc.NO_CONNECTOR["sam_gov"]
    assert manual["classification"]["bucket"] == "B1"
    assert manual["classification"]["rule_code"] == "RULE-001"
    assert "Persisted exclusion/revocation evidence consumed" not in manual["classification"]["rationale"]


async def test_regression_clean_fully_evidenced_entity_still_classifies_b1_on_the_manual_path(
        db_required, monkeypatch):
    """A genuinely clean entity -- bulk path persisted SAM PASS -- must still
    be B1 on the manual path, now with sam_gov=verified from persisted
    evidence rather than the stub."""
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry import review_service as svc
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _patch_bulk_connectors(monkeypatch, sam_excluded=False)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        ref, entity_id = await _promoted(db, intake_id)
    async with async_session_maker() as db:
        bulk = await verify_and_classify(db, [ref], intake_id=intake_id,
                                         actor="pytest-sam-asym")
    assert bulk["outcomes"][0]["dimensions"]["EXCLUSION_REVOCATION"] == "PASS", \
        bulk["outcomes"][0]["dimensions"]

    _manual_path_clean_live(monkeypatch)
    async with async_session_maker() as db:
        entity = await db.get(reg.TefcaRegEntity, entity_id)
        manual = await svc.run_review(db, entity, trigger="manual")

    assert manual["verification"]["sam_gov"]["status"] == svc.VERIFIED
    assert manual["verification"]["sam_gov"]["persisted_evidence"]["disposition"] == "PASS"
    assert manual["classification"]["bucket"] == "B1"
    assert manual["classification"]["rule_code"] == "RULE-001"
    assert "SAM_GOV PASS -> verified" in manual["classification"]["rationale"]
