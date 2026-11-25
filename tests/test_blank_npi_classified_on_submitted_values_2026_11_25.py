"""A record that submitted NO NPI is classified on what it submitted (owner decision 2026-11-25).

  "For blank-NPI records, prepare the correction so classification evaluates the original submitted values. An
   earlier registry NPI must neither fill the blank nor prevent evaluation of the missing-NPI rule. Keep
   identity-dependent source checks separate and preserve original data. Demonstrate this on synthetic records before
   release."  ...  "Do not change October results."

THE DEMONSTRATION runs the real path on synthetic data: delivery A is ingested, quality-checked, curated and promoted
and gives each entity an NPI; delivery B delivers the SAME organisations with a blank NPI and is promoted onto the
same entities; delivery N delivers twin organisations that never had an NPI anywhere. The real bulk writer and the
real evidence gatherer then verify B and N. Only the connectors' outbound calls are replaced, by recorders that note
every NPI or name they were asked about; the whole file also runs with outbound network refused.

What is proved:
  * B's records are reviewed, not refused, in four variants (sole-purpose Treatment active, combined-purpose
    Treatment, non-Treatment, inactive Treatment);
  * no connector was asked about A's NPI for B; the classifier input and the review carry no NPI; A's NPI appears
    nowhere in B's review;
  * B is classified IDENTICALLY to N - bucket, rule, version, tier and the whole classifier input - under the shipped
    rules, under the composed ONC-scope rule set, and with an active mandatory sam_gov source policy;
  * the registry identifier row, the delivered record and the entity evidence store are untouched;
  * a record that submitted a DIFFERENT non-blank NPI is refused exactly as before, a record that passes today is
    unchanged, and a delivery assessed before the effective date (October) is refused exactly as before;
  * the review cycle's pre-check and the record report agree with the writer.
  * pinned as REMAINING: coverage, rechecks and the anchored single-record review still refuse such a record.

Synthetic data only.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.Tefca.connectors import SourceResult
from app.Tefca.models import TEFCADimensionEvidence
from app.reports.data import october_review_loader as ld
from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import verification_findings as vf
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

pytestmark = pytest.mark.asyncio

NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
#: Four distinct, Luhn-valid synthetic NPIs: the ones delivery A submits.
A_NPIS = ["1234567893", "1245319599", "1982916078", "1881659506"]
VARIANTS = [
    # name                     purposesofuse          active
    ("sole_treatment_active",  "T-TRTMNT",            "1"),
    ("combined_treatment",     "T-TRTMNT,T-PYMNT",    "1"),
    ("non_treatment",          "T-PYMNT",             "1"),
    ("inactive_treatment",     "T-TRTMNT",            "0"),
]


class _Sources:
    """Recorders standing in for every outbound connector call. Each notes what it was asked about and answers
    "unavailable" (a synthetic outage), so nothing here depends on, or reaches, a real source."""

    def __init__(self):
        self.npi_calls: list = []       # (connector, npi) - every NPI-keyed question
        self.name_calls: list = []      # (connector, name) - every name-keyed question

    def install(self, monkeypatch):
        from app.Tefca import cms_ppef, connectors, sam_extract
        sources = self

        def _npi(label):
            async def _lookup(self_, npi, *a, **k):
                sources.npi_calls.append((label, str(npi or "")))
                return SourceResult.unavailable(label, "synthetic: source not contacted", {"npi": npi})
            return _lookup

        async def _leie_name(self_, last="", first="", org="", **k):
            sources.name_calls.append(("OIG_LEIE", org or last))
            return SourceResult.unavailable("OIG_LEIE", "synthetic: source not contacted", {"org": org})

        async def _sam_name(self_, legal_name, *a, **k):
            sources.name_calls.append(("SAM_GOV", legal_name))
            return SourceResult.unavailable("SAM_GOV", "synthetic: source not contacted", {"legal_name": legal_name})

        async def _sam_verify(self_, uei="", legal_name="", **k):
            sources.name_calls.append(("SAM_GOV.verify", legal_name))
            return SourceResult.unavailable("SAM_GOV", "synthetic: source not contacted",
                                            {"uei": uei, "legal_name": legal_name})

        async def _extract(self_, npi, org_name, *a, **k):
            sources.npi_calls.append(("SAM_EXTRACT", str(npi or "")))
            sources.name_calls.append(("SAM_EXTRACT", org_name))
            return SourceResult.unavailable("SAM_EXTRACT", "synthetic: source not contacted",
                                            {"npi": npi, "org": org_name})

        async def _relational(self_, enrollment_ids, *a, **k):
            return SourceResult.unavailable("CMS_PPEF", "synthetic: source not contacted", {})

        monkeypatch.setattr(connectors.NPPESConnector, "lookup_by_npi", _npi("NPPES"))
        monkeypatch.setattr(connectors.OIGLEIEConnector, "lookup_by_npi", _npi("OIG_LEIE"))
        monkeypatch.setattr(connectors.OIGLEIEConnector, "lookup_by_name", _leie_name)
        monkeypatch.setattr(connectors.SAMGovConnector, "verify", _sam_verify)
        monkeypatch.setattr(connectors.SAMGovConnector, "lookup_by_name", _sam_name)
        monkeypatch.setattr(cms_ppef.PPEFEnrollmentConnector, "lookup_by_npi", _npi("CMS_PPEF_ENROLLMENT"))
        monkeypatch.setattr(cms_ppef.CMSRevocationConnector, "lookup_by_npi", _npi("CMS_REVOCATION"))
        monkeypatch.setattr(sam_extract.SAMExtractConnector, "screen", _extract)
        for name in ("practice_locations", "reassignments", "additional_npis"):
            monkeypatch.setattr(cms_ppef.PPEFRelationalConnector, name, _relational)
        return self

    def npis_asked(self):
        return {npi for _label, npi in self.npi_calls if npi}


def _rows(arc, *, npis, all_active=False):
    rows = make_rows(len(VARIANTS), arc=arc)
    for row, (name, purposes, active), npi in zip(rows, VARIANTS, npis):
        row.update({"purposesofuse": purposes, "active": "1" if all_active else active, "NPI": npi,
                    "name": f"{SYN} BLANKNPI {arc} {name}"})
    return rows


async def _ingest(db, rows):
    """The real path: Area-1 intake -> quality run -> curation -> promotion."""
    intake_id, _ = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    return intake_id


async def _curated(db, intake_id):
    rows = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == intake_id)
        .order_by(m.RceCuratedRecord.rce_org_oid))).scalars().all()
    return {row.rce_org_oid: row for row in rows}


async def _npi_rows(db, entity_ids):
    rows = (await db.execute(select(reg.TefcaEntityIdentifier).where(
        reg.TefcaEntityIdentifier.entity_id.in_(list(entity_ids)),
        reg.TefcaEntityIdentifier.identifier_type == "npi"))).scalars().all()
    return sorted((str(r.entity_id), r.identifier_value, r.identifier_status) for r in rows)


async def _evidence_rows(db, entity_ids):
    return (await db.execute(select(TEFCADimensionEvidence.id).where(
        TEFCADimensionEvidence.entity_id.in_([str(e) for e in entity_ids])))).scalars().all()


@pytest.fixture
async def world(rolled_back_db, monkeypatch):
    """Delivery A (NPIs), delivery B (same organisations, blank NPI), delivery N (twins that never had an NPI).
    The resolver is the REAL registry resolver, run on the test's own session (the writer's scratch sessions cannot
    see an uncommitted transaction); the gatherer is the REAL one; the connectors are recorders."""
    db = rolled_back_db
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    monkeypatch.delenv("ENABLE_SAM_EXTRACT_SCREENING", raising=False)
    arc, twin_arc = "9.99.780.1", "9.99.780.2"
    # A first delivery of an INACTIVE organisation is held (ACT-001), so every organisation is first delivered
    # active - with an NPI in delivery A, without one for the twins - and the variant under test arrives second.
    rows_a = _rows(arc, npis=A_NPIS, all_active=True)
    rows_b = _rows(arc, npis=[""] * len(VARIANTS))                       # the SAME organisations, NPI blank
    rows_n0 = _rows(twin_arc, npis=[""] * len(VARIANTS), all_active=True)
    rows_n = _rows(twin_arc, npis=[""] * len(VARIANTS))                  # twins: no NPI anywhere, ever
    intake_a = await _ingest(db, rows_a)
    intake_b = await _ingest(db, rows_b)
    await _ingest(db, rows_n0)
    intake_n = await _ingest(db, rows_n)

    from app.Tefca.entity_resolution import resolve_entity

    async def _resolve_all(refs):
        return {ref: await resolve_entity(db, ref) for ref in dict.fromkeys(refs)}
    monkeypatch.setattr(ap, "_resolve_all", _resolve_all)
    sources = _Sources().install(monkeypatch)

    cur_a, cur_b, cur_n = await _curated(db, intake_a), await _curated(db, intake_b), await _curated(db, intake_n)
    refs = [r["id"] for r in rows_a]
    twin_refs = [r["id"] for r in rows_n]
    return SimpleNamespace(
        db=db, sources=sources, intake_a=intake_a, intake_b=intake_b, intake_n=intake_n,
        refs=refs, twin_refs=twin_refs, cur_a=cur_a, cur_b=cur_b, cur_n=cur_n,
        entities=[cur_b[ref].canonical_entity_id for ref in refs],
        twins=[cur_n[ref].canonical_entity_id for ref in twin_refs])


async def _review_of(db, review_id):
    return (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == review_id))).scalars().one()


def _without_names(value, names):
    """A classifier input with the organisation-specific strings masked, so two organisations can be compared."""
    text_ = json.dumps(value, sort_keys=True, default=str)
    for index, name in enumerate(names):
        text_ = text_.replace(name, f"<org{index % len(VARIANTS)}>")
    return text_


# ── the fixture is the situation the decision is about ───────────────────────────────────────────────────────────────

async def test_the_synthetic_world_is_the_situation_the_decision_is_about(world):
    w = world
    for ref in w.refs:                                                  # same entity, promoted by A and by B
        assert w.cur_a[ref].canonical_entity_id == w.cur_b[ref].canonical_entity_id is not None
        assert w.cur_b[ref].record_status != "HELD"                     # NPI-001 does not hold: B reaches verification
    assert set(w.entities).isdisjoint(w.twins)
    rows = await _npi_rows(w.db, w.entities)
    assert [(npi, status) for _e, npi, status in rows] and {npi for _e, npi, _s in rows} == set(A_NPIS)
    assert await _npi_rows(w.db, w.twins) == []                         # the twins have no NPI anywhere
    for ref in w.refs:                                                  # delivery B really submitted no NPI
        rec = await w.db.get(m.RceSourceRecord, w.cur_b[ref].source_record_id)
        assert (rec.parsed or {}).get("NPI") == "" and not rec.npi


# ── THE DEMONSTRATION ────────────────────────────────────────────────────────────────────────────────────────────────

async def _demonstrate(w, monkeypatch, *, rules=None, policies=None):
    if rules is not None:
        async def _rule_set(_db):
            return rules
        monkeypatch.setattr(ap, "_rule_set", _rule_set)
    if policies is not None:
        from app.tefca_registry.rce import source_policy as sp

        async def _active(_db):
            return policies
        monkeypatch.setattr(sp, "load_active_policies", _active)
    w.sources.npi_calls.clear()
    w.sources.name_calls.clear()
    res_b = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)
    b_calls = list(w.sources.npi_calls)
    res_n = await ap.verify_and_classify(w.db, list(w.twin_refs), intake_id=w.intake_n)
    return res_b, res_n, b_calls


async def _assert_demonstration(w, res_b, res_n, b_calls):
    db = w.db
    names_b = [f"{SYN} BLANKNPI 9.99.780.1 {v[0]}" for v in VARIANTS] + w.refs
    names_n = [f"{SYN} BLANKNPI 9.99.780.2 {v[0]}" for v in VARIANTS] + w.twin_refs
    table = []
    # reviewed, not refused - all four
    assert res_b["unresolved"] == [] and res_b["resolution_failures"] == [] and res_b["verified"] == 4
    assert res_b["classified_on_submitted_values"] == w.refs
    assert res_b["evidence_snapshot_only_submitted_values"] == w.refs
    assert res_n["classified_on_submitted_values"] == []               # the twins never needed the rule
    # no connector was asked about ANY NPI for delivery B - A's least of all
    assert {npi for _label, npi in b_calls if npi} == set()
    assert {label for label, _npi in b_calls} >= {"NPPES", "OIG_LEIE", "CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"}
    by_ref_b = {o["entity_ref"]: o for o in res_b["outcomes"]}
    by_ref_n = {o["entity_ref"]: o for o in res_n["outcomes"]}
    for (variant, _p, _a), ref, twin_ref, a_npi in zip(VARIANTS, w.refs, w.twin_refs, A_NPIS):
        ob, on = by_ref_b[ref], by_ref_n[twin_ref]
        assert ob["lineage"] == ap.LINEAGE_ANCHORED and ob["classified_on_submitted_values"] is True
        assert ob["source_record_id"] == str(w.cur_b[ref].source_record_id)
        # THE KEY EQUIVALENCE: classified exactly as the same record on an entity with no NPI anywhere
        for key in ("bucket", "rule_code", "rule_version", "rule_matched", "tier", "assigned_role",
                    "dimensions", "applicability", "entity_marked_verified"):
            assert ob[key] == on[key], (variant, key, ob[key], on[key])
        rb, rn = await _review_of(db, ob["review_id"]), await _review_of(db, on["review_id"])
        ci_b, ci_n = rb.verification_results["classifier_input"], rn.verification_results["classifier_input"]
        assert _without_names(ci_b, names_b) == _without_names(ci_n, names_n), variant
        # the review says what was done, and carries no NPI anywhere
        assert rb.verification_results["submitted_values_basis"] == ap.SUBMITTED_VALUES_BASIS
        assert "submitted_values_basis" not in rn.verification_results
        whole = json.dumps(rb.verification_results, default=str) + (rb.classification_rationale or "")
        assert all(npi not in whole for npi in A_NPIS), variant
        assert rb.source_record_id == w.cur_b[ref].source_record_id and rb.entity_id == w.cur_b[ref].canonical_entity_id
        table.append((variant, ob["bucket"], ob["rule_code"], ob["tier"]))
    return table


async def test_demonstration_shipped_rules(world, monkeypatch, capsys):
    w = world
    npi_rows_before = await _npi_rows(w.db, w.entities)
    delivered_before = {ref: copy.deepcopy((await w.db.get(m.RceSourceRecord, w.cur_b[ref].source_record_id)).parsed)
                        for ref in w.refs}
    res_b, res_n, b_calls = await _demonstrate(w, monkeypatch)
    table = await _assert_demonstration(w, res_b, res_n, b_calls)
    # the registry's NPI rows are exactly as they were (not ended, not replaced, not flagged)
    assert await _npi_rows(w.db, w.entities) == npi_rows_before
    # the delivered records are exactly as delivered
    for ref in w.refs:
        rec = await w.db.get(m.RceSourceRecord, w.cur_b[ref].source_record_id)
        assert rec.parsed == delivered_before[ref] and not rec.npi
    # nothing was appended to the entity evidence store for B (its rows carry no record key); the twins' rows are
    assert await _evidence_rows(w.db, w.entities) == []
    assert len(await _evidence_rows(w.db, w.twins)) > 0
    # no NPI ledger finding and no identifier-status change came out of it
    issues = (await w.db.execute(select(m.RceIssue).where(
        m.RceIssue.source_intake_id == w.intake_b,
        m.RceIssue.issue_type.in_((vf.NPI_NOT_FOUND, vf.NPI_DEACTIVATED, vf.NPI_VERIFICATION_UNAVAILABLE))))).scalars().all()
    assert issues == []
    with capsys.disabled():
        print("\nDEMONSTRATION (shipped rules):", table)


async def test_demonstration_onc_scope_rules_and_active_mandatory_sam_policy(world, monkeypatch, capsys):
    """The same four variants under the composed ONC-scope rule set (where the sole-purpose Treatment missing-NPI
    rule exists) and with an ACTIVE mandatory sam_gov source policy. Nothing is activated in the database: the rule
    set and the policy are handed to the writer. Whatever each variant gets, it is what its no-NPI twin gets."""
    from app.tefca_registry.rule_configuration import compose
    from test_mandatory_source_parity_2026_11_11 import _policy
    w = world
    rules = [{**r, "version": 99} for r in compose({"treatment_b4_onc_scope"})]
    res_b, res_n, b_calls = await _demonstrate(w, monkeypatch, rules=rules)
    onc = await _assert_demonstration(w, res_b, res_n, b_calls)
    res_b, res_n, b_calls = await _demonstrate(w, monkeypatch, rules=rules, policies=[_policy()])
    with_policy = await _assert_demonstration(w, res_b, res_n, b_calls)
    # The missing-NPI rule was EVALUATED for the sole-purpose active Treatment record - the thing the registry's
    # NPI used to prevent - and it decides the bucket.
    sole = {name: (bucket, rule) for name, bucket, rule, _tier in onc}["sole_treatment_active"]
    assert sole == ("B4", "RULE-006"), sole
    with capsys.disabled():
        print("\nDEMONSTRATION (ONC-scope rules):", onc)
        print("DEMONSTRATION (ONC-scope rules + active mandatory sam_gov policy):", with_policy)


async def test_the_treatment_signals_come_from_the_delivered_row(world, monkeypatch):
    w = world
    res_b, _res_n, _calls = await _demonstrate(w, monkeypatch)
    by_ref = {o["entity_ref"]: o for o in res_b["outcomes"]}
    fields = {}
    for (variant, _p, _a), ref in zip(VARIANTS, w.refs):
        review = await _review_of(w.db, by_ref[ref]["review_id"])
        fields[variant] = review.verification_results["classifier_input"]["fields"]
    assert fields["sole_treatment_active"]["active_treatment_missing_required_npi"]
    assert "inactive_treatment_npi_missing" in fields["inactive_treatment"]
    assert "active_treatment_missing_required_npi" not in fields["non_treatment"]
    for variant, value in fields.items():                               # NPI is "not supplied" wherever it is stated
        for signal in value.values():
            if isinstance(signal, dict) and "raw_npi" in signal:
                assert signal["raw_npi"] == "", (variant, signal)


# ── what does NOT change ─────────────────────────────────────────────────────────────────────────────────────────────

async def test_a_different_non_blank_npi_is_refused_exactly_as_before(world, monkeypatch):
    w = world
    ref = w.refs[0]
    rec = await w.db.get(m.RceSourceRecord, w.cur_b[ref].source_record_id)
    rec.parsed = {**rec.parsed, "NPI": "1003000126"}                    # a non-blank NPI the registry does not hold
    rec.npi = "1003000126"
    await w.db.flush()
    w.sources.npi_calls.clear()
    res = await ap.verify_and_classify(w.db, [ref], intake_id=w.intake_b)
    assert res["unresolved"] == [ref] and res["verified"] == 0
    assert res["resolution_failures"] == [{"entity_ref": ref, "reason": ap.REFUSAL_SUBMITTED_NPI_DIFFERS}]
    assert res["classified_on_submitted_values"] == [] and res["refused_before_gather"] == [ref]
    assert w.sources.npi_calls == []                                    # refused before any source was asked


async def test_a_record_that_passes_today_is_unchanged(world, monkeypatch):
    """Delivery A's records (submitted NPI == registry NPI) and the twins take the path they always took: the rule
    is not applied, the entity object is the resolver's own, the sources are asked about the submitted NPI, and the
    evidence is stored. Compared field for field with the same call made with the new rule switched off."""
    w = world
    res_new = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_a)
    asked_new = sorted(w.sources.npi_calls)
    assert res_new["classified_on_submitted_values"] == [] and w.sources.npis_asked() == set(A_NPIS)
    assert all(o["evidence_stored"] and not o["classified_on_submitted_values"] for o in res_new["outcomes"])
    monkeypatch.setattr(ap, "BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE", date(2999, 1, 1))     # the rule switched off
    w.sources.npi_calls.clear()
    res_old = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_a)
    assert sorted(w.sources.npi_calls) == asked_new
    for new, old in zip(res_new["outcomes"], res_old["outcomes"]):
        for key in ("entity_ref", "entity_id", "source_record_id", "lineage", "bucket", "rule_code", "rule_version",
                    "rule_matched", "tier", "assigned_role", "dimensions", "applicability", "evidence_stored",
                    "entity_marked_verified"):
            assert new[key] == old[key], key
        a, b = await _review_of(w.db, new["review_id"]), await _review_of(w.db, old["review_id"])
        strip = lambda v: {k: x for k, x in v.items() if k not in ("generation_timestamp", "dimensions")}  # noqa: E731
        assert strip(a.verification_results["classifier_input"]) == strip(b.verification_results["classifier_input"])
        assert "submitted_values_basis" not in a.verification_results
        assert a.classification_rationale == b.classification_rationale
    assert len(await _evidence_rows(w.db, w.entities)) > 0              # stored, as always


async def test_a_delivery_assessed_before_the_effective_date_is_refused_exactly_as_before(world, monkeypatch):
    """"Do not change October results." The rule applies only to a delivery whose OWN assessment date is on or after
    BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE. October's is 2026-11-08: re-running its cycle classifies nothing new."""
    w = world
    assert ap.BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE == date(2026, 11, 9)
    run = (await w.db.execute(select(m.RceIngestionRun).where(
        m.RceIngestionRun.source_intake_id == w.intake_b))).scalars().first()
    run.assessment_metadata = {"assessment_date": "2026-11-08", "source": "pinned_param"}
    await w.db.flush()
    w.sources.npi_calls.clear()
    res = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)
    assert res["verified"] == 0 and res["unresolved"] == w.refs and res["classified_on_submitted_values"] == []
    assert {f["reason"] for f in res["resolution_failures"]} == {ap.REFUSAL_SUBMITTED_NPI_DIFFERS}
    assert w.sources.npi_calls == []
    # ... and one day later it applies
    run.assessment_metadata = {"assessment_date": "2026-11-09", "source": "pinned_param"}
    await w.db.flush()
    res = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)
    assert res["verified"] == 4 and res["classified_on_submitted_values"] == w.refs


# ── fail closed ──────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_evidence_built_with_the_registry_npi_is_refused_not_relabelled(world, monkeypatch):
    """A gatherer that ignores the pre-resolved view and builds evidence for the NPI-bearing entity: the record is
    REFUSED. Swapping the entity afterwards would have classified and snapshotted another NPI's evidence as its own."""
    w = world
    from app.Tefca.entity_resolution import resolve_entity

    async def _ignores_the_view(refs, timing_log=None):
        out = []
        for ref in refs:
            entity = await resolve_entity(w.db, ref)                    # the registry entity, NPI and all
            out.append({"ref": ref, "entity": entity, "evidence": {
                "generated_at": "2026-11-25T12:00:00Z", "applicability": {}, "sufficiency": {},
                "data_quality_flags": [], "dimensions": [
                    {"dimension": "IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE", "evidence": [
                        {"source": "NPPES", "disposition": "PASS", "query_identifier": f"npi={A_NPIS[0]}",
                         "original_values": {"npi": A_NPIS[0]}}]}]}})
        return out
    monkeypatch.setattr(ap, "_gather_all_evidence", _ignores_the_view)
    res = await ap.verify_and_classify(w.db, [w.refs[0]], intake_id=w.intake_b)
    assert res["verified"] == 0 and res["unresolved"] == [w.refs[0]]
    assert res["resolution_failures"] == [{"entity_ref": w.refs[0], "reason": ap.REFUSAL_GATHERED_WITH_REGISTRY_NPI}]
    assert res["classified_on_submitted_values"] == []
    assert (await w.db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id == w.entities[0]))).scalars().all() == []

    async def _view_but_npi_evidence(refs, timing_log=None):
        pre = ap._PRE_RESOLVED.get() or {}
        return [{"ref": ref, "entity": pre[ref], "evidence": (await _ignores_the_view([ref]))[0]["evidence"]}
                for ref in refs]
    monkeypatch.setattr(ap, "_gather_all_evidence", _view_but_npi_evidence)
    res = await ap.verify_and_classify(w.db, [w.refs[0]], intake_id=w.intake_b)
    assert res["resolution_failures"][0]["reason"] == ap.REFUSAL_GATHERED_WITH_REGISTRY_NPI


def test_the_view_is_request_local_and_the_decision_is_one_function():
    record = SimpleNamespace(parsed={"NPI": ""}, npi=None)
    entity = {"id": "e", "_registry_entity_id": "e", "name": "n", "_rce": {"NPI": A_NPIS[0], "name": "n"},
              "identifier": [{"system": NPI_SYSTEM, "value": A_NPIS[0]},
                             {"system": "urn:docuaction:tefca/identifier/rce-org-oid", "value": "9.9"}]}
    before = copy.deepcopy(entity)
    on = date(2026, 11, 9)
    refusal, use, withheld = ap.record_identity(entity, record, assessment_date=on)
    assert refusal is None and withheld is True and use is not entity and entity == before     # the input is untouched
    assert use["identifier"] == [before["identifier"][1]] and use["_rce"] == {"NPI": "", "name": "n"}
    assert not ap._entity_carries_npi(use) and ap._entity_carries_npi(entity)
    # the gate itself is still pure: asked about the NPI-bearing entity it still refuses
    assert ap.anchored_npi_refusal(entity, record) == ap.REFUSAL_SUBMITTED_NPI_DIFFERS
    # before the effective date, and for an undated call: exactly as before
    for when in (date(2026, 11, 8), None):
        assert ap.record_identity(entity, record, assessment_date=when) == (ap.REFUSAL_SUBMITTED_NPI_DIFFERS, entity, False)
    # no anchor / a matching NPI / a different NPI / the curated-fallback-only shape (RCE block, no identifier)
    assert ap.record_identity(entity, None, assessment_date=on) == (None, entity, False)
    same = SimpleNamespace(parsed={"NPI": A_NPIS[0]}, npi=A_NPIS[0])
    assert ap.record_identity(entity, same, assessment_date=on) == (None, entity, False)
    other = SimpleNamespace(parsed={"NPI": A_NPIS[1]}, npi=A_NPIS[1])
    assert ap.record_identity(entity, other, assessment_date=on) == (ap.REFUSAL_SUBMITTED_NPI_DIFFERS, entity, False)
    rce_only = {"id": "e", "_rce": {"NPI": A_NPIS[0]}, "identifier": []}
    assert ap.record_identity(rce_only, record, assessment_date=on)[2] is True
    none_anywhere = {"id": "e", "_rce": {"NPI": ""}, "identifier": []}
    assert ap.record_identity(none_anywhere, record, assessment_date=on) == (None, none_anywhere, False)
    # the evidence check reads the fields the record report reads
    item = lambda **kw: {"dimensions": [{"evidence": [kw]}]}             # noqa: E731
    assert ap._evidence_carries_npi(item(source="NPPES", query_identifier=f"npi={A_NPIS[0]}"))
    assert ap._evidence_carries_npi(item(source="CMS_PPEF_ENROLLMENT", original_values={"npi": A_NPIS[0]}))
    assert ap._evidence_carries_npi(item(source="NPPES", source_record_identifier=A_NPIS[0]))
    assert not ap._evidence_carries_npi(item(source="NPPES", query_identifier="npi="))
    assert not ap._evidence_carries_npi(item(source="OIG_LEIE", query_identifier="org=ACME"))


# ── the cycle and the report agree with the writer ───────────────────────────────────────────────────────────────────

async def test_the_review_cycles_precheck_decides_as_the_writer_does(world):
    w = world
    for ref, entity in zip(w.refs, w.entities):
        with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(
                side_effect=(await _real_resolver(w.db)))):
            assert await rc._identity_precheck(w.db, w.intake_b, entity, ref) is None                # reviewable now
            assert await rc._identity_precheck(w.db, w.intake_b, entity, ref,
                                               assessment_date=date(2026, 11, 8)) == ap.REFUSAL_SUBMITTED_NPI_DIFFERS


async def _real_resolver(db):
    from app.Tefca.entity_resolution import resolve_from_db

    async def _resolve(_db, ref):
        return await resolve_from_db(db, ref)
    return _resolve


async def test_the_review_cycle_reviews_the_blank_npi_members_and_reports_it(world, monkeypatch):
    import app.tefca_registry.processing_controls as pc
    import app.tefca_registry.qhin_sampling as qs
    import app.tefca_registry.rce.reconciliation as rec
    w = world
    sample = reg.ReviewSample(id=uuid.uuid4(), sample_name="synthetic blank-NPI sample", review_type="quarterly",
                              population_size=4, sample_size=4, confidence_level=0.95, margin_of_error=0.05,
                              proportion=0.5)
    sample_id = sample.id
    w.db.add(sample)
    await w.db.flush()
    for entity in w.entities:
        w.db.add(reg.SampleEntity(id=uuid.uuid4(), sample_id=sample_id, entity_id=entity, stratum="s"))
    await w.db.flush()

    async def _enter(_db):
        return None

    async def _reconciled(_db, _intake):
        return {"passed": True, "checks": []}

    async def _plan(_db, intake_id, **_kw):
        return {"sample_id": str(sample_id), "already_finalized": True, "sample_size": 4, "population_size": 4,
                "membership_count": 4}
    monkeypatch.setattr(pc, "enter_processing", _enter)
    monkeypatch.setattr(rec, "reconcile_delivery", _reconciled)
    monkeypatch.setattr(qs, "finalize_plan", _plan)
    monkeypatch.setattr("app.Tefca.entity_resolution.resolve_entity", await _real_resolver(w.db))

    result = await rc._create_review_cycle(
        w.db, w.intake_b, user=SimpleNamespace(id=None, email="cycle@synthetic-test.docuaction.invalid"))

    assert result["linked_this_call"] == 4 and result["remaining"] == 0 and result["complete"] is True
    assert result["not_reviewable_for_identity"] == []
    verification = result["verification"]
    assert verification["classified_on_submitted_values_count"] == 4
    assert sorted(verification["classified_on_submitted_values"]) == sorted(w.refs)
    assert w.sources.npis_asked() == set()                              # still no NPI-keyed question
    audit = (await w.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_members_verified"))).scalars().all()
    meta = next(a.metadata_ for a in audit if (a.metadata_ or {}).get("sample_id") == str(sample_id))
    assert meta["classified_on_submitted_values"] == 4


async def test_the_record_report_shows_the_record_classified_and_its_npi_sources_not_checked(world, monkeypatch):
    w = world
    await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_a)        # A is reviewed with ITS NPI
    res_b, _res_n, _calls = await _demonstrate(w, monkeypatch)
    out = await ld.load_record_facts(w.db, intake_id=w.intake_b, evidence_cutoff=datetime.utcnow() + timedelta(days=1))
    facts = out["facts"]
    assert len(facts) == 4
    by_bucket = {o["source_record_id"]: o["bucket"] for o in res_b["outcomes"]}
    for fact in facts:
        assert fact["bucket"] in by_bucket.values() and fact["bucket"]              # classified
        assert fact["nppes"] == ld.NOT_CHECKED_NO_SUBMITTED_NPI                     # not "verified", not "not found"
        assert fact["pecos"] == ld.NOT_CHECKED_NO_SUBMITTED_NPI
    assert all(npi not in json.dumps(facts, default=str) for npi in A_NPIS)        # A's NPI is in no cell of B's report
    assert out["meta"]["stats"]["npi_sources_not_checked_no_submitted_npi"] == 8
    # delivery A's own report is unaffected by B's review: its NPI-keyed cells still rest on its own rows
    out_a = await ld.load_record_facts(w.db, intake_id=w.intake_a, evidence_cutoff=datetime.utcnow() + timedelta(days=1))
    assert all(f["nppes"] != ld.NOT_CHECKED_NO_SUBMITTED_NPI for f in out_a["facts"])
    assert out_a["meta"]["stats"].get("npi_sources_not_checked_no_submitted_npi", 0) == 0


# ── REMAINING: paths that still refuse such a record (pinned so they are not forgotten) ──────────────────────────────

async def test_remaining_coverage_and_rechecks_still_refuse_a_blank_npi_record_on_an_entity_with_an_npi(world):
    """NOT changed by this commit. `validated_delivery_context` (coverage, rechecks) refuses when the probes would use
    an NPI the record did not submit - and a blank one is not submitted. Their "identity refused" counts will
    therefore include records that DO have a review from the cycle. Making them consistent means deciding whether
    an NPI-less generation may be appended to the entity evidence store, whose rows carry no record key."""
    w = world
    from app.Tefca.entity_resolution import resolve_from_db
    entity = await resolve_from_db(w.db, w.refs[0])
    probe, conflict = vf.resolved_probe_npi(entity)
    assert conflict is None and probe == A_NPIS[0]
    context, refusal = await vf.validated_delivery_context(
        w.db, intake_id=w.intake_b, ref=w.refs[0], queued_entity_id=w.entities[0], entity=entity, probe_npi=probe)
    assert context is None and "did not submit" in refusal


async def test_remaining_the_anchored_single_record_review_still_refuses_it(world, monkeypatch):
    """NOT changed by this commit. `review_service.run_review` compares the entity's NPI identifier rows with the
    selected record's submitted NPI and raises before any probe."""
    from app.tefca_registry import review_service as rs
    w = world
    entity = await w.db.get(reg.TefcaRegEntity, w.entities[0])
    with pytest.raises(ValueError, match="does not match the registered"):
        await rs.run_review(w.db, entity, source_record_id=w.cur_b[w.refs[0]].source_record_id)
