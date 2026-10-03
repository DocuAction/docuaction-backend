"""The SAM.gov asymmetry between the two real callers of
`BucketClassifier.classify()` -- stated precisely, after the 2026-10-03
challenge "zero connector calls alone does not establish a defect if
appropriate persisted evidence is consumed":

  * `arc_pipeline.verify_and_classify` (bulk/RCE path) queries SAM.gov
    (`connectors.query_all_sources` -> `SAMGovConnector.verify()`), persists
    the answer as `tefca_dimension_evidence` (EXCLUSION_REVOCATION / SAM_GOV),
    and a confirmed-or-pending exclusion disqualifies the entity (v3 rules).
  * `review_service.run_review` / `probe_sources` (manual single-entity path)
    neither queries SAM.gov NOR reads that persisted evidence. It builds its
    classifier input from live connector calls only (nppes/pecos/oig_leie),
    seeds `sources["sam_gov"]` from the static `NO_CONNECTOR` stub, and never
    imports or selects `TEFCADimensionEvidence` at all (asserted below from
    the module source). So the challenge's "unless persisted evidence is
    consumed" branch does not apply: nothing is consumed.

Three cases, each with its own test:
  (a) persisted bulk-path SAM exclusion exists -> the manual path still
      writes a NEW ReviewRecord that does not reflect it (REAL DEFECT: two
      contradictory determinations for one entity, the newer one clean);
  (b) no persisted evidence at all -> no SAM signal either way (the only
      case that is merely "unprotected", not contradictory);
  (c) staleness -> moot: there is no read to be stale, and no re-query.

Not fixed here. Wiring SAM (live or persisted) into the manual path is a
behavior change needing its own authorization; the smallest fix is named in
SAM_MANUAL_REVIEW_ASYMMETRY.md.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

pytestmark = pytest.mark.asyncio


class _FakeResult:
    """Mimics SourceResult: success means the query completed, not the answer."""
    def __init__(self, success=True, data=None, error=None):
        self.success, self.data, self.error = success, data, error


def _valid_npi() -> str:
    """A structurally valid (Luhn-passing) NPI, so probe_sources' own
    pre-connector validity gate does not short-circuit every source to
    not_checked before the SAM question is even reached."""
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
    positive result -- isolates the test to the SAM effect, exactly like the
    existing e2e test's `_clean_nppes_leie` helper."""

    class _Conn:
        async def lookup_by_npi(self, npi):
            return _FakeResult(success=True, data={
                "found": True, "excluded": False, "legal_name": "Test Org",
                "enumeration_type": "NPI-2", "status": "A",
            })

    class _Mgr:
        nppes = pecos = leie = _Conn()
        # A real SAM-aware manager WOULD also expose `.sam_gov` here -- but
        # review_service.probe_sources's connector loop
        # (`for key, attr in (("nppes","nppes"),("pecos","pecos"),
        # ("oig_leie","leie"))`) never looks it up, so even attaching one
        # here (see below) proves it is never consulted.
        sam_gov = _Conn()

    return _Mgr()


async def test_manual_review_never_queries_sam_even_when_a_connector_exists(monkeypatch):
    """The core proof. Attach a SAM connector to the manager that WOULD report
    a debarment if it were ever called -- probe_sources must still return the
    static not_checked stub, never touching it."""
    from app.tefca_registry import review_service as svc
    import app.Tefca.connectors as conns

    calls = {"sam_gov": 0}

    class _SamThatWouldFlag:
        async def verify(self, uei="", legal_name=""):
            # If this is ever called, the test below would fail on the call
            # count assertion -- this method intentionally reports the worst
            # possible real-world finding, to prove the asymmetry is not an
            # artifact of a lenient mock.
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

    assert calls["sam_gov"] == 0, (
        "probe_sources invoked the SAM connector -- the asymmetry this test "
        "guards against has been fixed; update this test's purpose, do not "
        "just relax this assertion")
    assert sources["sam_gov"]["status"] == svc.NOT_CHECKED
    assert "sam_gov" not in ("nppes", "pecos", "oig_leie")  # sanity: distinct key
    # The real NPPES/PECOS/LEIE results DID come through -- proves the gap is
    # specific to sam_gov, not a general connector failure.
    assert sources["nppes"]["status"] in (svc.VERIFIED, "clear")
    assert sources["oig_leie"]["status"] == "clear"


async def test_classification_on_the_manual_path_is_unaffected_by_what_sam_would_say(monkeypatch):
    """Feeds probe_sources' output into the REAL v3 classifier (the same one
    the RCE/bulk path uses) and shows the result is identical whether SAM
    would have said "debarred" or said nothing at all -- because the manual
    path never lets the classifier see a SAM answer either way."""
    from app.tefca_registry import review_service as svc
    from app.tefca_registry.bucket_classifier import BucketClassifier, SEED_RULES_V3
    import app.Tefca.connectors as conns

    classifier = BucketClassifier(rules=SEED_RULES_V3)

    async def _classify_with_sam_mock(sam_would_flag: bool):
        mgr = _clean_nppes_pecos_leie_mgr()

        class _Sam:
            async def verify(self, uei="", legal_name=""):
                if sam_would_flag:
                    return _FakeResult(success=True, data={"excluded": True, "debarred": True})
                return _FakeResult(success=True, data={"excluded": False, "debarred": False})
        mgr.sam_gov = _Sam()
        monkeypatch.setattr(conns, "SourceConnectorManager", lambda: mgr)

        db = _FakeDB(npi=_valid_npi())
        sources = await svc.probe_sources(db, "entity")
        results = {"sources": sources, "fields": {}, "confidence_score": None}
        return classifier.classify(results)

    result_if_sam_would_flag = await _classify_with_sam_mock(sam_would_flag=True)
    result_if_sam_silent = await _classify_with_sam_mock(sam_would_flag=False)

    assert result_if_sam_would_flag.bucket == result_if_sam_silent.bucket, (
        "Expected IDENTICAL classification regardless of the SAM mock's "
        "answer -- this is the asymmetry: the manual path's bucket cannot "
        "depend on SAM at all today, because SAM is never actually queried.")
    assert result_if_sam_would_flag.rule_code != "RULE-005", (
        "A confirmed debarment did NOT disqualify this entity on the manual "
        "path -- exactly the gap this test documents, not fixed here.")


# ── Persisted-evidence cases (2026-10-03) ────────────────────────────────────
# Seeding helpers kept local so this file stays self-contained; they mirror
# test_sam_e2e_delivery_path.py (same synthetic shape, same connector-method
# patching, no network).

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
            "NPI": _e2e_valid_npi(i + hash(run_tag) % 100_000),
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


async def _promoted_refs(db, intake_id, limit):
    from sqlalchemy import select

    from app.tefca_registry.rce import models as m

    return list((await db.execute(
        select(m.RceCuratedRecord.rce_org_oid)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None))
        .order_by(m.RceCuratedRecord.rce_org_oid).limit(limit))).scalars().all())


def _patch_bulk_connectors_sam_excluded(monkeypatch):
    """Bulk path: NPPES/LEIE clean, SAM a CONFIRMED exclusion. Patched class
    methods on the real connectors -- never the HTTP path."""
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
            "found": True, "matched_by": "uei", "excluded": True,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True,
        }, {"uei": uei})

    monkeypatch.setattr(NPPESConnector, "lookup_by_npi", fake_nppes)
    monkeypatch.setattr(OIGLEIEConnector, "lookup_by_npi", fake_leie)
    monkeypatch.setattr(SAMGovConnector, "verify", fake_sam_verify)


async def test_review_service_never_reads_persisted_dimension_evidence():
    """Cases (b)/(c), executable: the manual path has NO code path that
    consults persisted evidence, so there is nothing to be stale and
    nothing to be consumed. If someone wires one in, this fails and the
    asymmetry claim must be re-examined -- which is the point."""
    from app.tefca_registry import review_service as svc

    src = inspect.getsource(svc)
    assert "TEFCADimensionEvidence" not in src
    assert "tefca_dimension_evidence" not in src
    assert "sources = await probe_sources(db, entity.id)" in src


async def test_persisted_bulk_sam_exclusion_is_not_consumed_by_the_manual_path(
        db_required, monkeypatch):
    """Case (a), the real defect. The REAL bulk pipeline runs with a
    confirmed SAM exclusion, so `tefca_dimension_evidence` holds a truthful
    REVIEW row and a non-B1 ReviewRecord for the entity. The REAL manual
    path (`run_review`) then runs on the SAME entity with clean
    NPPES/PECOS/LEIE answers and no SAM connector at all.

    If the "persisted evidence is consumed" branch applied, the manual review
    would reflect the exclusion (B4 / RULE-005). It does not: it writes a
    SECOND, newer ReviewRecord with `sam_gov` still the static NOT_CHECKED
    stub. Two contradictory determinations now exist for one entity, and the
    newer one is the clean one."""
    from sqlalchemy import select

    from app.Tefca.models import TEFCADimensionEvidence
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry import review_service as svc
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    import app.Tefca.connectors as conns

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    _patch_bulk_connectors_sam_excluded(monkeypatch)

    intake_id = await _seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await _promoted_refs(db, intake_id, 1)
    assert len(refs) == 1
    async with async_session_maker() as db:
        bulk = await verify_and_classify(db, refs, intake_id=intake_id,
                                         actor="pytest-sam-asym")
    outcome = bulk["outcomes"][0]
    entity_id = uuid.UUID(outcome["entity_id"])
    assert outcome["bucket"] != "B1", f"bulk precondition failed: {outcome}"

    async with async_session_maker() as db:
        sam_rows = [r for r in (await db.execute(select(TEFCADimensionEvidence).where(
            TEFCADimensionEvidence.entity_id == str(entity_id),
            TEFCADimensionEvidence.evidence_dimension == "EXCLUSION_REVOCATION"))
        ).scalars().all() if r.source == "SAM_GOV"]
    assert sam_rows and sam_rows[-1].disposition == "REVIEW"
    assert sam_rows[-1].original_values.get("excluded") is True

    mgr = _clean_nppes_pecos_leie_mgr()
    mgr.sam_gov = None   # no SAM connector of any kind on the manual path
    monkeypatch.setattr(conns, "SourceConnectorManager", lambda: mgr)

    async with async_session_maker() as db:
        entity = await db.get(reg.TefcaRegEntity, entity_id)
        assert entity is not None
        manual = await svc.run_review(db, entity, trigger="manual")

    assert manual["verification"]["sam_gov"]["status"] == svc.NOT_CHECKED, (
        "the manual path produced a SAM status other than the static stub -- "
        "persisted evidence or a connector is now consumed; re-examine the "
        "asymmetry claim rather than relaxing this test")
    assert manual["classification"]["bucket"] != "B4"
    assert manual["classification"]["rule_code"] != "RULE-005", (
        "a persisted, confirmed SAM exclusion WAS reflected by the manual "
        "path -- the asymmetry has been closed; update this file's purpose")

    async with async_session_maker() as db:
        records = (await db.execute(
            select(reg.ReviewRecord).where(reg.ReviewRecord.entity_id == entity_id)
            .order_by(reg.ReviewRecord.created_at))).scalars().all()
    assert len(records) == 2, [r.review_id for r in records]
    assert records[0].classification_bucket == outcome["bucket"]
    assert records[1].review_id == manual["review_id"]
    assert records[1].classification_bucket != "B4"
    print(f"[asymmetry] bulk={records[0].classification_bucket}/{records[0].classification_rule} "
          f"manual={records[1].classification_bucket}/{records[1].classification_rule}")
