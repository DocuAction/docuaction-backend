"""Part B combined shadow proof over the seeded corpus (manifest_b.json).

ONE synthetic delivery of twelve records goes through the REAL pipeline
(ingest -> quality -> curate -> promote -> verify_and_classify -> recheck)
with every external source replaced by a deterministic fake keyed on the
seed id embedded in each synthetic name. No network, no real identifier,
no EIN/TIN/SSN.

    phase 1  OFFICIAL view            default flags, active rules
    phase 2  PROPOSED view, in shadow read from the phase-1 records; nothing written
    phase 3  PROPOSED view, enforced  same population, same fake responses,
                                      ENFORCE_COMPLETE_EXCLUSION_SCREENING on
    phase 4  source recovery          maker/checker recheck of the SAM faults

HARD GATES (asserted; the numbers are printed with -s)
    G1  zero lost seeded risk signals
    G2  zero false verification passes from seeded source faults
        -- holds in the PROPOSED view. In the OFFICIAL view the three SAM
        fault seeds ARE marked verified (active RULE-002); that is pinned
        here as the documented finding, not hidden.
    G3  every outcome that differs between the views is explained
    G4  original delivered data unchanged
    G5  genuine findings and historical evidence preserved

Corpus results, not universal accuracy.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings

_DIR = pathlib.Path(__file__).parent / "fixtures" / "seeded"
MANIFEST = json.loads((_DIR / "manifest_b.json").read_text(encoding="utf-8"))
SEEDS = {s["seed_id"]: s for s in MANIFEST["pipeline_seeds"]}
_SEED_RE = re.compile(r"\b(B\d\d)\b")


def _seed_of(text: str):
    m = _SEED_RE.search(text or "")
    return SEEDS.get(m.group(1)) if m else None


def _valid_npi(n: int) -> str:
    from app.services.npi_validator import CMS_PREFIX, _luhn_total

    base = f"{1_000_000_000 + (n % 900_000_000):09d}"[-9:]
    for d in range(10):
        if _luhn_total(CMS_PREFIX + base + str(d)) % 10 == 0:
            return base + str(d)
    return base + "0"


def _delivery(tag: str):
    """Twelve synthetic rows; returns (bytes, {npi: seed_id}, {seed_id: name})."""
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    qhin = f"9.99.777.{tag}"
    rows, npi_to_seed, names = [], {}, {}
    for i, seed in enumerate(MANIFEST["pipeline_seeds"]):
        sid = seed["seed_id"]
        name = f"SYNTHETIC-TRACE CORPUS {tag} {sid}" + (", L.L.C." if sid == "B09" else "")
        npi = _valid_npi(uuid.uuid4().int % 800_000_000 + i) if seed["has_npi"] else ""
        if npi:
            npi_to_seed[npi] = sid
        names[sid] = name
        values = {
            "id": f"9.99.777.corpus.{tag}.{i:02d}", "orgManagingOrg": qhin,
            "sequoiaorgtype": "Participant", "organizationNodeType": "initiating-node",
            "NPI": npi, "TEFCAID": f"TEFCA-CORPUS-{tag}-{sid}",
            "HCID": f"urn:oid:9.99.777.corpus.{tag}.{i:02d}", "active": "true",
            "hl7orgrole": "provider" if npi else "", "name": name, "partOf": qhin,
            "address_text": "Primary", "address_line": f"{100 + i} Synthetic Corpus Way",
            "address_city": "Testville", "address_state": "TX",
            "address_postalCode": f"{75000 + i}", "address_country": "US",
            "purposesofuse": "T-TRTMNT", "domains": "RCE",
        }
        rows.append("|".join(str(values.get(f, "")) for f in RCE_FIELDS))
    raw = ("|".join(RCE_FIELDS) + "\n" + "\n".join(rows) + "\n").encode("utf-8")
    return raw, npi_to_seed, names


class _Sources:
    """Deterministic fakes for every external source. `recovered` flips the
    SAM fault seeds to their manifest `recovery` answer."""

    def __init__(self, npi_to_seed, names):
        self.npi_to_seed, self.names = npi_to_seed, names
        self.recovered = False
        self.sam_calls = 0

    def install(self, monkeypatch):
        from app.Tefca import cms_ppef
        from app.Tefca.connectors import (NPPESConnector, OIGLEIEConnector, SAMGovConnector,
                                          SourceResult)

        src = self

        async def nppes(self, npi):
            seed = SEEDS.get(src.npi_to_seed.get(npi or ""))
            if seed is None:
                return SourceResult.unavailable("NPPES", "no NPI to look up", {"npi": npi})
            if seed["nppes"] == "fault_error_body":
                return SourceResult.unavailable(
                    "NPPES", "npi_registry_request_error: NPPES rejected the request; "
                             "not a lookup result", {"npi": npi})
            data = {"found": True, "npi": npi, "legal_name": src.names[seed["seed_id"]],
                    "enumeration_type": "NPI-2", "status": "A", "addresses": []}
            if seed["nppes"] == "deactivated":
                data.update(status="D", deactivation_date="2025-01-15")
            return SourceResult.ok("NPPES", data, {"npi": npi})

        async def leie_npi(self, npi):
            seed = SEEDS.get(src.npi_to_seed.get(npi or ""))
            if seed is None:
                return SourceResult.unavailable("OIG_LEIE", "no NPI to look up", {"npi": npi})
            if seed["leie"] == "fault_unavailable":
                return SourceResult.unavailable(
                    "OIG_LEIE", "exclusions CSV unavailable (list refused by schema "
                                "preflight)", {"npi": npi})
            hit = seed["leie"] == "excluded"
            return SourceResult.ok("OIG_LEIE", {
                "excluded": hit, "exclusion_found": hit, "exclusion_count": int(hit),
                "exclusion_type": "1128a1" if hit else None,
                "exclusion_date": "20240101" if hit else None}, {"npi": npi})

        async def leie_name(self, last, first="", org=""):
            seed = _seed_of(org)
            hit = bool(seed) and seed["leie"] == "name_candidate"
            return SourceResult.ok("OIG_LEIE", {
                "excluded": hit, "exclusion_found": hit, "exclusion_count": int(hit),
                "match_methods": ["exact_business_name", "normalized_business_name"],
                "candidates_exact": 0, "candidates_normalized_only": int(hit),
                "list_rows_searched": 5}, {"org": org})

        def _sam_answer(seed):
            mode = seed["sam"]
            if mode.startswith("fault_") and src.recovered:
                mode = seed["recovery"]
            reasons = {
                "fault_error_body": "source_error_body: SAM.gov answered HTTP 200 with an "
                                    "error body; not a search result",
                "fault_429": "HTTP 429 - SAM.gov rate limit reached (daily quota is per key)",
                "fault_timeout": "ReadTimeout: synthetic timeout",
            }
            if mode in reasons:
                return None, reasons[mode]
            return {"found": mode != "clean_name", "matched_by": "name",
                    "excluded": mode == "excluded", "excluded_known": True,
                    "identity_ambiguous": mode == "ambiguous", "ambiguous": mode == "ambiguous",
                    "registration_current": None}, None

        async def sam_verify(self, uei="", legal_name=""):
            src.sam_calls += 1
            seed = _seed_of(legal_name)
            data, fault = _sam_answer(seed)
            if fault:
                return SourceResult.unavailable("SAM_GOV", fault, {"legal_name": legal_name},
                                                "v3+v4")
            return SourceResult.ok("SAM_GOV", data, {"legal_name": legal_name})

        async def sam_name(self, legal_name):
            data, fault = _sam_answer(_seed_of(legal_name))
            if fault:
                return SourceResult.unavailable("SAM_GOV", fault, {"legal_name": legal_name})
            return SourceResult.ok("SAM_GOV", data, {"legal_name": legal_name})

        async def ppef(self, npi):
            return SourceResult.unavailable(self.SOURCE_NAME, "synthetic: CMS data API not "
                                            "exercised by this corpus", {"npi": npi})

        async def revocation(self, npi):
            if not npi:
                return SourceResult.unavailable(self.SOURCE_NAME, "no NPI to look up",
                                                {"npi": npi})
            return SourceResult.ok(self.SOURCE_NAME, {
                "checked": True, "matches": [],
                "result": "NO_ACTIVE_REVOCATION_RECORD_FOUND"}, {"npi": npi})

        monkeypatch.setattr(NPPESConnector, "lookup_by_npi", nppes)
        monkeypatch.setattr(OIGLEIEConnector, "lookup_by_npi", leie_npi)
        monkeypatch.setattr(OIGLEIEConnector, "lookup_by_name", leie_name)
        monkeypatch.setattr(SAMGovConnector, "verify", sam_verify)
        monkeypatch.setattr(SAMGovConnector, "lookup_by_name", sam_name)
        monkeypatch.setattr(cms_ppef.PPEFEnrollmentConnector, "lookup_by_npi", ppef)
        monkeypatch.setattr(cms_ppef.CMSRevocationConnector, "lookup_by_npi", revocation)


async def _promote(raw: bytes, tag: str):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.curation import curate_delivery
    from app.tefca_registry.rce.intake import ingest_delivery
    from app.tefca_registry.rce.promotion import promote_delivery
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    async with async_session_maker() as db:
        intake_id = (await ingest_delivery(
            db, raw, filename=f"SYNTHETIC-CORPUS-B-{tag}.psv", declared_delimiter="|",
            delivery_label=f"SYNTHETIC-TRACE-corpus-b-{tag}",
            received_by="pytest-corpus-b@synthetic-test.docuaction.invalid"))["intake_id"]
    async with async_session_maker() as db:
        await run_quality_engine(db, intake_id, executed_by="pytest-corpus-b")
    async with async_session_maker() as db:
        await curate_delivery(db, intake_id, curated_by="pytest-corpus-b")
    async with async_session_maker() as db:
        await promote_delivery(db, intake_id, actor="pytest-corpus-b")
    return uuid.UUID(str(intake_id))


async def _refs(intake_id):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        return list((await db.execute(
            select(m.RceCuratedRecord.rce_org_oid)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None))
            .order_by(m.RceCuratedRecord.rce_org_oid))).scalars().all())


async def _cycle(refs, intake_id):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id, actor="pytest-corpus-b")
    return {_seed_of(o["name"])["seed_id"]: o for o in result["outcomes"]}


async def _statuses(by_seed):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    async with async_session_maker() as db:
        return {sid: (await db.get(reg.TefcaRegEntity,
                                   uuid.UUID(o["entity_id"]))).verification_status
                for sid, o in by_seed.items()}


async def _records(by_seed):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    async with async_session_maker() as db:
        return {sid: (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == o["review_id"]))).scalars().one()
            for sid, o in by_seed.items()}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


async def _fingerprints(intake_id, by_seed, evidence_ids=None):
    """(original delivered data, phase-1 evidence rows, phase-1 review records)."""
    from app.Tefca.models import TEFCADimensionEvidence as DE
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    entity_ids = [o["entity_id"] for o in by_seed.values()]
    async with async_session_maker() as db:
        originals = (await db.execute(
            select(m.RceSourceRecord.line_number, m.RceSourceRecord.raw_line,
                   m.RceSourceRecord.record_sha256, m.RceSourceRecord.parsed)
            .where(m.RceSourceRecord.source_intake_id == intake_id)
            .order_by(m.RceSourceRecord.line_number))).all()
        ev_stmt = select(DE.id, DE.source, DE.disposition, DE.original_values).where(
            DE.entity_id.in_(entity_ids)).order_by(DE.id)
        if evidence_ids is not None:
            ev_stmt = ev_stmt.where(DE.id.in_(evidence_ids))
        evidence = (await db.execute(ev_stmt)).all()
        reviews = (await db.execute(
            select(reg.ReviewRecord.review_id, reg.ReviewRecord.classification_bucket,
                   reg.ReviewRecord.classification_rule, reg.ReviewRecord.classification_rationale,
                   reg.ReviewRecord.verification_results)
            .where(reg.ReviewRecord.review_id.in_([o["review_id"] for o in by_seed.values()]))
            .order_by(reg.ReviewRecord.review_id))).all()
    return (_sha([list(r) for r in originals]), _sha([list(r) for r in evidence]),
            _sha([list(r) for r in reviews]), [r[0] for r in evidence])


class _User:
    def __init__(self, role):
        self.id, self.role = uuid.uuid4(), role
        self.email = f"corpus-{role}-{uuid.uuid4().hex[:6]}@synthetic-test.docuaction.invalid"


def test_every_component_seed_points_at_a_test_that_exists():
    """The manifest cannot rot: each `proved_by` names a real test function."""
    root = pathlib.Path(__file__).parent.parent
    for seed in MANIFEST["component_seeds"]:
        path, _, func = seed["proved_by"].partition("::")
        text = (root / path).read_text(encoding="utf-8")
        assert re.search(rf"^(async )?def {re.escape(func)}\(", text, re.M), seed["proved_by"]
    assert len(MANIFEST["pipeline_seeds"]) == 12 and len(MANIFEST["component_seeds"]) == 16


@pytest.mark.asyncio
async def test_combined_shadow_proof_over_the_seeded_corpus(db_required, monkeypatch):
    from app.Tefca import source_policy as sp
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import prior_risk
    from app.tefca_registry.rce import recheck_models as rm
    from app.tefca_registry.rce import rechecks
    from app.tefca_registry.rce.shadow_reassessment import classifier_input, risk_signals

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    tag = uuid.uuid4().hex[:8]
    raw, npi_to_seed, names = _delivery(tag)
    sources = _Sources(npi_to_seed, names)
    sources.install(monkeypatch)
    intake_id = await _promote(raw, tag)
    refs = await _refs(intake_id)
    assert len(refs) == 12, f"expected 12 promoted synthetic entities, got {len(refs)}"

    # ── phase 1: OFFICIAL view ───────────────────────────────────────────────
    assert settings.ENFORCE_COMPLETE_EXCLUSION_SCREENING is False
    official = await _cycle(refs, intake_id)
    assert set(official) == set(SEEDS)
    official_status = await _statuses(official)
    records = await _records(official)
    fp_originals, fp_evidence, fp_reviews, phase1_evidence_ids = await _fingerprints(
        intake_id, official)

    official_verified = {sid for sid, st in official_status.items() if st == "verified"}
    assert official_verified == {s for s, d in SEEDS.items() if d["official_verified"]}, (
        official_status, {s: o["bucket"] for s, o in official.items()})

    # G1 -- zero lost seeded risk signals (official view).
    lost = []
    for sid, seed in SEEDS.items():
        if not seed["risk_signal_expected"]:
            continue
        # A seeded risk signal is LOST if the entity is marked verified.
        if official_status[sid] == "verified":
            lost.append((sid, official_status[sid], official[sid]["bucket"]))
    assert lost == [], f"seeded risk signals lost: {lost}"
    # ...and each exclusion signal is visible ON the record, by name.
    for sid in ("B02", "B03", "B08", "B09"):
        signals = risk_signals(classifier_input(records[sid].verification_results))
        assert any(x.startswith("EXCLUSION:") for x in signals), (sid, signals)
    assert official["B02"]["bucket"] == "B4" and official["B08"]["bucket"] == "B4"
    assert official["B09"]["bucket"] == "B4"                 # name candidate, no NPI
    assert official["B03"]["bucket"] != "B1"                 # ambiguous identity
    # The deactivated NPI (B11) is an open BLOCKING ledger finding and the
    # entity is held, whatever bucket the rules gave it.
    from app.tefca_registry.rce import post_promotion_verification as ppv
    async with async_session_maker() as db:
        assert await ppv.has_unresolved_blocking_finding(
            db, uuid.UUID(official["B11"]["entity_id"])) is True
    assert official_status["B11"] == "in_review"
    # The ledger holds exactly the NPPES findings the corpus seeded: one
    # deactivation (B11) and one unavailable lookup (B12). The two NPI-less
    # records (B09, B10) are NOT findings -- having no NPI is a fact about the
    # entity. (Before 2026-10-04 this ledger was empty for every real run:
    # verification_findings matched the wrong dimension name.)
    from app.tefca_registry.rce import models as m
    async with async_session_maker() as db:
        ledger = [t for (t,) in (await db.execute(select(m.RceIssue.issue_type).where(
            m.RceIssue.source_intake_id == intake_id,
            m.RceIssue.issue_type.in_(("NPI_DEACTIVATED", "NPI_NOT_FOUND",
                                       "NPI_VERIFICATION_UNAVAILABLE"))))).all()]
    assert sorted(ledger) == ["NPI_DEACTIVATED", "NPI_VERIFICATION_UNAVAILABLE"], ledger
    # A name-only clean screen (B10) is never a verification pass.
    assert official_status["B10"] != "verified" and official["B10"]["bucket"] != "B1"

    # G2, OFFICIAL view -- the documented finding: every SAM fault seed is
    # classified B1 and marked verified by the active rules.
    fault_seeds = {s for s, d in SEEDS.items() if d["source_fault"]}
    official_false_passes = sorted(fault_seeds & official_verified)
    assert official_false_passes == ["B04", "B05", "B06"]
    for sid in official_false_passes:
        claim = records[sid].verification_results["verification_claim"]
        assert {"control": "sam_gov", "gap": "UNAVAILABLE"} in claim["exclusion_screening_incomplete"]
        assert claim["official"]["entity_marked_verified"] is True
    # Faults on a source the rules DO require never pass, in either view.
    assert official_status["B07"] != "verified" and official_status["B12"] != "verified"

    # G2a -- SOURCE level: a faulted check is never a successful source
    # verification. G2b -- LABEL level: no fault seed carries an unqualified
    # overall "verified". Both hold in the OFFICIAL view; the bucket, the
    # entity status and the reportability gate are untouched (policy P1).
    from app.tefca_registry.rce import verification_completeness as vcomp
    sam_counted_successful, unqualified_verified = [], []
    for sid in sorted(fault_seeds):
        comp = vcomp.completeness(records[sid].verification_results)
        block = vcomp.describe(official_status[sid], comp)
        faulted = {"B07": "oig_leie", "B12": "nppes"}.get(sid, "sam_gov")
        assert comp["source_outcomes"][faulted] == vcomp.UNAVAILABLE, (sid, comp)
        if faulted in comp["successful_sources"]:
            sam_counted_successful.append(sid)
        assert comp["state"] == vcomp.INCOMPLETE, (sid, comp)
        if block["overall_status"] == "verified":
            unqualified_verified.append(sid)
        # the pipeline's own result carries the same qualification
        assert official[sid]["verification_completeness"]["overall_status"] == \
            block["overall_status"], sid
    assert sam_counted_successful == [], sam_counted_successful
    assert unqualified_verified == [], unqualified_verified
    for sid in official_false_passes:
        block = vcomp.describe(official_status[sid],
                               vcomp.completeness(records[sid].verification_results))
        assert block["overall_status"] == vcomp.VERIFIED_CHECKS_INCOMPLETE
        assert block["overall_label"] == "Verified - checks incomplete"
        assert [i["source"] for i in block["incomplete"]] == ["pecos", "sam_gov"] or \
            "sam_gov" in [i["source"] for i in block["incomplete"]]
        # UNAVAILABLE is not turned into an exclusion or a not-found, and the
        # classification is exactly what the active rules gave.
        assert official[sid]["bucket"] == "B1" and records[sid].classification_bucket == "B1"
        assert not any(x.startswith("EXCLUSION:") for x in
                       risk_signals(classifier_input(records[sid].verification_results)))
    # B01 (every source answered) is the only seed that reads plain "Verified".
    # In this corpus the CMS enrolment API is faked unavailable for every
    # record, so even B01 is honestly INCOMPLETE on that source.
    b01 = vcomp.describe(official_status["B01"],
                         vcomp.completeness(records["B01"].verification_results))
    assert b01["source_outcomes"]["sam_gov"] in vcomp.SUCCESSFUL
    assert b01["source_outcomes"]["oig_leie"] in vcomp.SUCCESSFUL
    # COUNTS: the report/registry split of the bare "verified" count.
    corpus_entity_ids = [uuid.UUID(o["entity_id"]) for o in official.values()]
    async with async_session_maker() as db:
        split = await vcomp.split_verified_counts(
            db, {"verified": len(official_verified),
                 "in_review": 12 - len(official_verified)}, corpus_entity_ids)
    assert split["verified_total"] == len(official_verified) == 4
    assert sum(split["split"].values()) == 4                       # reconciles
    assert split["split"][vcomp.VERIFIED_CHECKS_INCOMPLETE] >= 3  # B04-B06
    assert split["incomplete_by_source"].get("sam_gov") == 3
    assert split["counts"]["in_review"] == 8
    # The delivery-scoped view the Verification tab reads: same split, by join.
    async with async_session_maker() as db:
        per_delivery = await vcomp.delivery_completeness(db, intake_id)
    assert per_delivery["entities"] == 12 and per_delivery["verified_total"] == 4
    assert per_delivery["overall_counts"].get("verified", 0) == 0
    assert per_delivery["overall_counts"][vcomp.VERIFIED_CHECKS_INCOMPLETE] == 4
    assert sum(per_delivery["overall_counts"].values()) == 12
    sam_row = [r for r in per_delivery["incomplete_by_source"] if r["source"] == "sam_gov"]
    assert sam_row and sam_row[0]["entities"] == 3
    assert sam_row[0]["outcomes"] == {"Source unavailable": 3}
    official_gate = {
        "G2a_fault_counted_as_successful_source_check": len(sam_counted_successful),
        "G2b_unqualified_overall_verified_on_a_fault_seed": len(unqualified_verified),
        "G2c_entity_status_verified_while_screening_incomplete": len(official_false_passes),
    }
    print("OFFICIAL-VIEW GATES", json.dumps(official_gate), "split", json.dumps(split["split"]))

    # Source policy is on every record: official unapproved, freshness unknown.
    for sid, rec in records.items():
        block = rec.verification_results["source_policy"]
        assert all(e["official"]["approval_status"] == sp.POLICY_UNAPPROVED
                   and e["official"]["freshness"] == sp.FRESHNESS_UNKNOWN
                   and e["proposed_inactive"]["approval_status"] == sp.PROPOSED_INACTIVE
                   for e in block["sources"].values()), sid

    # ── phase 2: PROPOSED view, in shadow (read-only) ────────────────────────
    proposed_shadow = {}
    for sid, rec in records.items():
        vr = rec.verification_results
        gaps = prior_risk.exclusion_screening_gaps(vr["classifier_input"])
        proposed_shadow[sid] = (rec.classification_bucket == "B1" and not gaps
                                and not vr.get("prior_risk_not_cleared"))
    proposed_verified = {s for s, v in proposed_shadow.items() if v}
    assert proposed_verified == {s for s, d in SEEDS.items() if d["proposed_verified"]}
    # G2, PROPOSED view -- zero false passes from seeded source faults.
    assert fault_seeds & proposed_verified == set()

    # G3 -- every outcome that differs between the views is explained.
    changed = sorted(official_verified ^ proposed_verified)
    assert changed == ["B04", "B05", "B06"]
    explanations = {sid: records[sid].verification_results["verification_claim"]
                    ["exclusion_screening_incomplete"] for sid in changed}
    assert all(explanations[sid] for sid in changed)
    # Shadow reading wrote nothing.
    assert (await _fingerprints(intake_id, official, phase1_evidence_ids))[:3] == (
        fp_originals, fp_evidence, fp_reviews)

    # ── phase 3: PROPOSED view, enforced, same population and responses ──────
    monkeypatch.setattr(settings, "ENFORCE_COMPLETE_EXCLUSION_SCREENING", True)
    enforced = await _cycle(refs, intake_id)
    enforced_status = await _statuses(enforced)
    monkeypatch.setattr(settings, "ENFORCE_COMPLETE_EXCLUSION_SCREENING", False)
    assert {s for s, st in enforced_status.items() if st == "verified"} == proposed_verified
    # Enforcement changes who is marked verified -- never the classifier's bucket.
    assert {s: o["bucket"] for s, o in enforced.items()} == {
        s: o["bucket"] for s, o in official.items()}

    # ── technical grouping: affected records vs. cause groups (measured) ─────
    from app.Tefca.models import TEFCADimensionEvidence as DE
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(DE.entity_id, DE.source, DE.disposition)
            .where(DE.id.in_(phase1_evidence_ids),
                   DE.disposition.in_(("UNAVAILABLE", "INSUFFICIENT_EVIDENCE"))))).all()
    technical_records = {(e, s, d) for e, s, d in rows}
    cause_groups = {(s, d) for _, s, d in technical_records}
    sam_group = {e for e, s, d in technical_records if (s, d) == ("SAM_GOV", "UNAVAILABLE")}
    assert sam_group == {official[s]["entity_id"] for s in ("B04", "B05", "B06")}
    assert len(cause_groups) < len(technical_records)
    # Risk signals are NOT in any technical group: each is its own review record.
    risk_review_ids = {official[s]["review_id"] for s, d in SEEDS.items()
                       if d["risk_signal_expected"]}
    assert len(risk_review_ids) == sum(d["risk_signal_expected"] for d in SEEDS.values())

    # ── phase 4: source recovery -> maker/checker recheck ────────────────────
    analyst, qa = _User("reviewer"), _User("qalead")
    async with async_session_maker() as db:
        job = await rechecks.request_recheck(
            db, intake_id, trigger_kind=rm.TRIGGER_SOURCE_RECOVERY, source_id=sp.SAM_GOV,
            trigger_ref=f"SYNTHETIC-INC-{tag}", rationale="synthetic SAM recovery", user=analyst)
    assert job["target_count"] == 3
    jid = uuid.UUID(job["job_id"])
    async with async_session_maker() as db:
        with pytest.raises(rechecks.RecheckRefused, match="segregation of duties"):
            await rechecks.approve_recheck(db, jid, user=analyst)
    async with async_session_maker() as db:
        await rechecks.approve_recheck(db, jid, user=qa)
    sources.recovered = True
    calls_before = sources.sam_calls
    async with async_session_maker() as db:
        await rechecks.claim(db, jid)
    async with async_session_maker() as db:
        done = await rechecks.run_batch(db, jid)
    assert done["state"] == rm.STATE_SUCCEEDED
    assert done["summary"]["by_outcome"] == {rm.OUTCOME_ANSWERED_NO_SIGNAL: 2,
                                             rm.OUTCOME_RISK_SIGNAL: 1}
    assert sources.sam_calls - calls_before == 3
    async with async_session_maker() as db:
        again = await rechecks.request_recheck(
            db, intake_id, trigger_kind=rm.TRIGGER_SOURCE_RECOVERY, source_id=sp.SAM_GOV,
            trigger_ref=f"SYNTHETIC-INC-{tag}", rationale="repeat", user=analyst)
        items = (await rechecks.list_items(db, jid))["items"]
    assert again["already_exists"] is True and sources.sam_calls - calls_before == 3
    # The exclusion hidden behind the outage (B05) surfaced and is in review;
    # the recheck verified nobody.
    hidden = [i for i in items if i["outcome"] == rm.OUTCOME_RISK_SIGNAL]
    assert [i["entity_id"] for i in hidden] == [official["B05"]["entity_id"]]
    final_status = await _statuses(official)
    assert final_status["B05"] == "in_review"
    for sid in ("B04", "B05", "B06"):
        assert final_status[sid] == "in_review"       # re-evaluated, not approved

    # ── G4 / G5: originals, historical evidence and genuine findings intact ──
    end_originals, end_evidence, end_reviews, _ = await _fingerprints(
        intake_id, official, phase1_evidence_ids)
    assert end_originals == fp_originals, "original delivered data changed"
    assert end_evidence == fp_evidence, "historical evidence rows changed"
    assert end_reviews == fp_reviews, "phase-1 review records (genuine findings) changed"

    print("CORPUS-B RESULT " + json.dumps({
        "seeds": len(SEEDS),
        "official_verified": sorted(official_verified),
        "proposed_verified": sorted(proposed_verified),
        "official_false_passes_from_source_faults": official_false_passes,
        "proposed_false_passes_from_source_faults": sorted(fault_seeds & proposed_verified),
        "lost_risk_signals": lost,
        "changed_outcomes_explained": {k: v for k, v in explanations.items()},
        "buckets_official": {s: o["bucket"] for s, o in sorted(official.items())},
        "technical_records": len(technical_records), "technical_cause_groups": len(cause_groups),
        "recheck": done["summary"]["by_outcome"],
        "originals_unchanged": True, "historical_evidence_unchanged": True,
        "genuine_findings_unchanged": True}, sort_keys=True))
