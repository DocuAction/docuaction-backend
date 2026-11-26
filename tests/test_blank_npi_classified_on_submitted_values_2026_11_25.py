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
    assert res_b["evidence_not_stored_unanchored"] == []               # anchored: never reported as unanchored
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
    assert verification["evidence_not_stored_unanchored_count"] == 0   # they are anchored, and audited as such
    assert verification["evidence_not_stored_unanchored"] == []
    assert w.sources.npis_asked() == set()                              # still no NPI-keyed question
    audit = (await w.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_members_verified"))).scalars().all()
    meta = next(a.metadata_ for a in audit if (a.metadata_ or {}).get("sample_id") == str(sample_id))
    assert meta["classified_on_submitted_values"] == 4
    assert meta["evidence_not_stored_unanchored"] == 0 and meta["evidence_not_stored_unanchored_refs"] == []


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


# ═══ the shared entity is left as it is (owner decision on #249) ═════════════════════════════════════════════════════
#
#   "preserve the shared entity's status and keep evidence in the review snapshot without appending it to shared
#    entity evidence. Treat this as correction of classification on original submitted values, not authorization to
#    change RULE-006 scope."

async def _entity_state(db, entity_ids):
    """What a review can write about an ENTITY: its status, and its per-entity verification (marker) rows."""
    entities = {str(e): (await db.get(reg.TefcaRegEntity, e)).verification_status for e in entity_ids}
    markers = sorted((str(v.entity_id), v.source, v.verification_status, v.review_id)
                     for v in (await db.execute(select(reg.TefcaVerification).where(
                         reg.TefcaVerification.entity_id.in_(list(entity_ids))))).scalars().all())
    return entities, markers


@pytest.mark.parametrize("forced_bucket", [None, "B1"])
async def test_the_shared_entitys_status_and_marker_rows_are_untouched(world, monkeypatch, forced_bucket):
    """All four variants - as the rules classify them, and with the rules engine forced to B1 (the bucket that
    would mark an entity verified). Before and after: the entity's verification status and its marker rows are
    identical. The review exists with its bucket, anchored to the delivered record. A record that submitted its
    NPI (delivery A, verified next by the same writer) still gets its entity status and marker written as before."""
    w = world
    if forced_bucket:
        from app.tefca_registry.bucket_classifier import BucketClassifier
        from test_mandatory_source_parity_2026_11_11 import _cls
        monkeypatch.setattr(BucketClassifier, "classify", lambda self, results, rules=None: _cls(forced_bucket))
    # Delivery A holds the same four organisations, each submitting the NPI the registry holds. One writer call
    # serves one delivery, so A is verified right after B, by the same writer, further down.
    status_before, markers_before = await _entity_state(w.db, w.entities)
    assert set(status_before.values()) == {"not_verified"} and markers_before == []

    res_b = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)

    assert res_b["verified"] == 4 and res_b["classified_on_submitted_values"] == w.refs
    assert await _entity_state(w.db, w.entities) == (status_before, markers_before)       # identical
    assert res_b["entities_marked_verified"] == 0
    for outcome, ref in zip(res_b["outcomes"], w.refs):
        assert outcome["entity_status_preserved"] is True and outcome["entity_marked_verified"] is False
        review = await _review_of(w.db, outcome["review_id"])
        assert review.classification_bucket == outcome["bucket"] == (forced_bucket or outcome["bucket"])
        assert review.source_record_id == w.cur_b[ref].source_record_id
        assert review.verification_results["submitted_values_basis"]["entity"].startswith("the shared entity")
        assert review.verification_results["dimensions"]             # its own evidence snapshot is there
    if forced_bucket:
        assert {o["bucket"] for o in res_b["outcomes"]} == {"B1"}

    # A record that submitted its NPI (delivery A, same entities): status and marker written exactly as before.
    res_a = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_a)
    status_after, markers_after = await _entity_state(w.db, w.entities)
    expected = "verified" if forced_bucket == "B1" else "in_review"
    assert all(o["entity_status_preserved"] is False for o in res_a["outcomes"])
    if forced_bucket == "B1":
        expected_status = {str(e): ("verified" if o["entity_marked_verified"] else "in_review")
                           for e, o in zip(w.entities, res_a["outcomes"])}
    else:
        expected_status = {str(e): expected for e in w.entities}
    assert status_after == expected_status
    assert sorted(mk[3] for mk in markers_after) == sorted(o["review_id"] for o in res_a["outcomes"])
    assert {(mk[1], mk[2]) for mk in markers_after} == {("rce_arc_pipeline", "verified")}


async def test_the_review_cycle_still_links_a_member_whose_entity_was_left_as_it_is(world, monkeypatch):
    import app.tefca_registry.processing_controls as pc
    import app.tefca_registry.qhin_sampling as qs
    import app.tefca_registry.rce.reconciliation as rec
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from test_mandatory_source_parity_2026_11_11 import _cls
    w = world
    monkeypatch.setattr(BucketClassifier, "classify", lambda self, results, rules=None: _cls("B1"))
    sample = reg.ReviewSample(id=uuid.uuid4(), sample_name="synthetic preserved-status sample",
                              review_type="quarterly", population_size=4, sample_size=4, confidence_level=0.95,
                              margin_of_error=0.05, proportion=0.5)
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
    before = await _entity_state(w.db, w.entities)

    result = await rc._create_review_cycle(
        w.db, w.intake_b, user=SimpleNamespace(id=None, email="cycle@synthetic-test.docuaction.invalid"))

    assert result["linked_this_call"] == 4 and result["remaining"] == 0 and result["complete"] is True
    members = (await w.db.execute(select(reg.SampleEntity).where(
        reg.SampleEntity.sample_id == sample_id))).scalars().all()
    assert all(mbr.review_id and mbr.review_status == "in_review" for mbr in members)     # the MEMBER is linked
    for mbr in members:
        review = await _review_of(w.db, mbr.review_id)
        assert review.classification_bucket == "B1" and review.entity_id == mbr.entity_id
        assert review.sample_id == sample_id
    assert await _entity_state(w.db, w.entities) == before          # ... and the ENTITY is as it was


async def test_reconciliation_still_sees_these_reviews_as_carrying_evidence(world, monkeypatch):
    """The marker row is one of two things `reconciliation` accepts as "this review carries evidence"; the other is
    the review's own `dimensions` snapshot, which these reviews have. Not writing the marker leaves that count at
    zero for them."""
    from sqlalchemy import text as sql
    w = world
    res_b = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)
    ids = [o["review_id"] for o in res_b["outcomes"]]
    without_evidence = (await w.db.execute(sql(
        "SELECT count(*) FROM review_records r WHERE r.review_id = ANY(:ids) "
        "AND (r.verification_results IS NULL OR r.verification_results->'dimensions' IS NULL "
        "     OR jsonb_array_length(r.verification_results->'dimensions') = 0) "
        "AND NOT EXISTS (SELECT 1 FROM tefca_verifications v WHERE v.review_id = r.review_id "
        "                AND v.verification_status IN ('verified', 'not_found', 'excluded'))"),
        {"ids": ids})).scalar()
    assert without_evidence == 0


async def test_this_change_touches_no_rule_no_rule_component_and_not_the_scope_of_rule_006(world, monkeypatch):
    """"... not authorization to change RULE-006 scope." The stored rule rows are identical before and after a
    classification call; the rule components and what each composes to are what the rule modules define, and those
    modules know nothing of this change; and the writer hands the classifier the rule set it was given."""
    import inspect
    from app.tefca_registry import bucket_classifier, rule_configuration
    w = world

    async def _rule_rows():
        rows = (await w.db.execute(select(reg.ReviewRule).order_by(
            reg.ReviewRule.rule_code, reg.ReviewRule.version))).scalars().all()
        return [{c.name: getattr(row, c.name) for c in reg.ReviewRule.__table__.columns} for row in rows]

    await ap._rule_set(w.db)                                        # seed the shipped rules first, if not there
    rows_before = await _rule_rows()
    composed_before = {name: json.dumps(rule_configuration.compose({name}), sort_keys=True, default=str)
                       for name in ("treatment_b4_onc_scope", "treatment_b4_owner_adopted")}
    assert rows_before
    seen = []
    real_classify = bucket_classifier.BucketClassifier.classify

    def _spy(self, results, rules=None):
        seen.append(json.dumps(rules, sort_keys=True, default=str))
        return real_classify(self, results, rules=rules)
    monkeypatch.setattr(bucket_classifier.BucketClassifier, "classify", _spy)
    effective = json.dumps(await ap._rule_set(w.db), sort_keys=True, default=str)

    res = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)

    assert res["classified_on_submitted_values"] == w.refs
    assert await _rule_rows() == rows_before                         # no rule row written, changed or retired
    assert set(seen) == {effective}                                  # the classifier got the effective rule set
    assert {name: json.dumps(rule_configuration.compose({name}), sort_keys=True, default=str)
            for name in composed_before} == composed_before
    # Under the shipped rules RULE-006 does not exist for these records: the sole-purpose Treatment one is B3.
    assert {o["rule_code"] for o in res["outcomes"]} == {"DEFAULT-UNMATCHED"}
    for module in (bucket_classifier, rule_configuration):
        source = inspect.getsource(module)
        assert "submitted_values" not in source and "BLANK_NPI" not in source, module.__name__


# ═══ sampling and work-queue consequences of leaving the shared entity as it is ══════════════════════════════════════
#
# Owner: "the same record must not be repeatedly selected, and any record needing analyst attention must remain
# visible through its review."

_PM = SimpleNamespace(id=None, email="cycle@synthetic-test.docuaction.invalid", role="program_manager")


async def _give_qhin(db, entities):
    """The canonical `managed_by_qhin` edge the sampling frame needs (one QHIN for all)."""
    qhin = uuid.uuid4()
    db.add(reg.TefcaRegEntity(id=qhin, name=f"{SYN} QHIN queues", display_name=f"{SYN} QHIN",
                              entity_level="qhin", entity_type="health_information_network",
                              operational_status="active", verification_status="not_verified",
                              current_version=1, is_active=True, is_test_record=False, rce_attributes={}))
    await db.flush()
    existing = {row for row in (await db.execute(select(reg.TefcaEntityRelationship.child_entity_id).where(
        reg.TefcaEntityRelationship.child_entity_id.in_(list(entities)),
        reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin"))).scalars().all()}
    for entity in entities:
        if entity not in existing:
            db.add(reg.TefcaEntityRelationship(
                id=uuid.uuid4(), parent_entity_id=qhin, child_entity_id=entity,
                relationship_type="managed_by_qhin", status="active", source="import",
                effective_date=date(2026, 11, 1)))
    await db.flush()


def _real_cycle(monkeypatch, db):
    """A REAL review cycle (real plan, members, walk, writer, gatherer); only reconciliation and the processing
    barrier are replaced, and the resolver runs on the test session."""
    import app.tefca_registry.processing_controls as pc
    import app.tefca_registry.rce.reconciliation as rec

    async def _enter(_db):
        return None

    async def _reconciled(_db, _intake):
        return {"passed": True, "checks": []}

    async def _resolve(_db, ref):
        from app.Tefca.entity_resolution import resolve_from_db
        return await resolve_from_db(db, ref)
    monkeypatch.setattr(pc, "enter_processing", _enter)
    monkeypatch.setattr(rec, "reconcile_delivery", _reconciled)
    monkeypatch.setattr("app.Tefca.entity_resolution.resolve_entity", _resolve)


async def _reviews_for_records(db, curated_by_ref, refs):
    ids = [curated_by_ref[ref].source_record_id for ref in refs]
    return (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.source_record_id.in_(ids)))).scalars().all()


async def test_a1_repeated_cycle_calls_never_review_the_record_again(world, monkeypatch):
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    first = await rc._create_review_cycle(w.db, w.intake_b, user=_PM, full_population=True)
    assert first["linked_this_call"] == 4 and first["complete"] is True
    asked_after_first = len(w.sources.npi_calls) + len(w.sources.name_calls)
    for _ in range(3):
        again = await rc._create_review_cycle(w.db, w.intake_b, user=_PM, full_population=True)
        assert again["linked_this_call"] == 0 and again["remaining"] == 0 and again["complete"] is True
        assert again["verification"]["requested"] == 0
    reviews = await _reviews_for_records(w.db, w.cur_b, w.refs)
    assert len(reviews) == 4 and len({r.source_record_id for r in reviews}) == 4      # ONE review per record
    assert len(w.sources.npi_calls) + len(w.sources.name_calls) == asked_after_first  # and no source asked again


async def test_a2_a_second_plan_for_the_same_delivery_does_not_draw_the_record_again(world, monkeypatch):
    """A second plan for the same delivery is a different plan key (another review type, margin, or a sample after
    a full enumeration), so `finalize_plan` builds a new frame. BEFORE this change an ordinary reviewed record was
    kept out of that frame by the entity status its review wrote (`in_review`); a record classified on its submitted
    values writes no entity status, so nothing kept it out and the second plan reviewed it again. NOW the frame
    excludes a delivered record that already has ITS OWN review classified on submitted values - keyed on the
    record, reported with a reason, and placing no restriction on the entity."""
    from app.tefca_registry import qhin_sampling as qs
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    await rc._create_review_cycle(w.db, w.intake_b, user=_PM, full_population=True)
    statuses = {(await w.db.get(reg.TefcaRegEntity, e)).verification_status for e in w.entities}
    assert statuses == {"not_verified"}                              # the entity status is NOT what protects it

    eligible, unresolved = await qs.resolve_qhin_strata(w.db, w.intake_b)
    assert eligible == []
    assert {u["exclusion"] for u in unresolved} == {qs.EXCLUSION_REVIEWED_ON_SUBMITTED_VALUES}
    assert {u["entity_id"] for u in unresolved} == set(w.entities)
    assert all(u["reason"] == qs.REVIEWED_ON_SUBMITTED_VALUES_REASON for u in unresolved)

    record_ids = [w.cur_b[ref].source_record_id for ref in w.refs]   # plain values: a refusal rolls back
    entity_ids, intake_b = list(w.entities), w.intake_b
    for second_plan in ({"review_type": "weekly", "full_population": True}, {"review_type": "weekly"},
                        {"margin": 0.1}):
        with pytest.raises(rc.CycleRefused, match="no eligible population"):
            await rc._create_review_cycle(w.db, intake_b, user=_PM, **second_plan)
    reviews = (await w.db.execute(select(reg.ReviewRecord.review_id).where(
        reg.ReviewRecord.source_record_id.in_(record_ids)))).scalars().all()
    assert len(reviews) == 4                                         # no duplicate review
    samples = (await w.db.execute(select(reg.SampleEntity.id).where(
        reg.SampleEntity.entity_id.in_(entity_ids)))).scalars().all()
    assert len(samples) == 4                                         # and no second sample membership


async def test_a2_the_exclusion_is_the_delivered_record_and_nothing_wider(world, monkeypatch):
    """Only two of the four records are reviewed (as their own delivery's blank-NPI records). The other two records
    of the same delivery stay eligible; so does the SAME entity's record in delivery A; an ordinary review of the
    same entity (no `submitted_values_basis`) excludes nothing through this key."""
    from app.tefca_registry import qhin_sampling as qs
    w = world
    await _give_qhin(w.db, w.entities)
    await ap.verify_and_classify(w.db, w.refs[:2], intake_id=w.intake_b)
    eligible, unresolved = await qs.resolve_qhin_strata(w.db, w.intake_b)
    assert {u["entity_id"] for u in eligible} == set(w.entities[2:])
    assert {u["entity_id"] for u in unresolved
            if u.get("exclusion") == qs.EXCLUSION_REVIEWED_ON_SUBMITTED_VALUES} == set(w.entities[:2])
    # delivery A (the same entities, records that submitted the NPI): untouched by B's reviews
    eligible_a, unresolved_a = await qs.resolve_qhin_strata(w.db, w.intake_a)
    assert {u["entity_id"] for u in eligible_a} == set(w.entities) and unresolved_a == []
    # an ordinary review of A's records writes the entity status; THAT exclusion is the old one, not this key
    await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_a)
    _eligible_a, unresolved_a = await qs.resolve_qhin_strata(w.db, w.intake_a)
    assert {u.get("exclusion") for u in unresolved_a} == {qs.EXCLUSION_REVIEW_REQUIRED}


async def test_a3_a_later_delivery_draws_the_same_entity_for_its_own_record(world, monkeypatch):
    """Delivery B2 delivers the same organisations again (blank NPI). The entities are selectable for B2's own
    records, B2 gets its OWN reviews anchored to ITS records, and B's reviews are neither reused nor changed."""
    from app.tefca_registry import qhin_sampling as qs
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    await rc._create_review_cycle(w.db, w.intake_b, user=_PM, full_population=True)
    b_reviews = {r.review_id: r.source_record_id for r in await _reviews_for_records(w.db, w.cur_b, w.refs)}

    rows_b2 = [dict(r, name=r["name"]) for r in _rows("9.99.780.1", npis=[""] * len(VARIANTS))]
    for row in rows_b2:
        row["address_line"] = "2 Later Delivery Way"                 # a different file, the same organisations
    intake_b2 = await _ingest(w.db, rows_b2)
    cur_b2 = await _curated(w.db, intake_b2)
    assert [cur_b2[ref].canonical_entity_id for ref in w.refs] == w.entities          # the same entities

    eligible, unresolved = await qs.resolve_qhin_strata(w.db, intake_b2)
    assert {u["entity_id"] for u in eligible} == set(w.entities)     # selectable for the later delivery
    assert [u for u in unresolved if u.get("exclusion") == qs.EXCLUSION_REVIEWED_ON_SUBMITTED_VALUES] == []

    cycle = await rc._create_review_cycle(w.db, intake_b2, user=_PM, full_population=True)
    assert cycle["linked_this_call"] == 4 and cycle["complete"] is True
    b2_reviews = {r.review_id: r.source_record_id for r in await _reviews_for_records(w.db, cur_b2, w.refs)}
    assert len(b2_reviews) == 4 and set(b2_reviews).isdisjoint(b_reviews)            # its own, new reviews
    assert set(b2_reviews.values()) == {cur_b2[ref].source_record_id for ref in w.refs}
    assert {r.review_id: r.source_record_id
            for r in await _reviews_for_records(w.db, w.cur_b, w.refs)} == b_reviews  # B's are as they were


async def test_b_a_record_needing_attention_is_visible_and_assignable_through_its_review(world, monkeypatch):
    """Every analyst / supervisor queue is built from ReviewRecord rows, not from the entity status this change no
    longer writes. Under the composed ONC-scope rules the four records are one B4 (RULE-006) and three default B3:
    each is in the available-cases queue and the operations work queue with its bucket, tier-routing reason and
    `submitted_values_basis`; each opens in the analyst workspace with the basis and the (unchanged) entity status;
    each can be assigned; and the QHIN workload rollup counts it under review."""
    from app.tefca_registry import case_assignment as ca
    from app.tefca_registry import qhin_workload, supervisor_ops, workspace
    from app.tefca_registry.rule_configuration import compose
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    rules = [{**r, "version": 99} for r in compose({"treatment_b4_onc_scope"})]

    async def _rule_set(_db):
        return rules
    monkeypatch.setattr(ap, "_rule_set", _rule_set)
    cycle = await rc._create_review_cycle(w.db, w.intake_b, user=_PM, full_population=True)
    assert cycle["linked_this_call"] == 4
    reviews = {r.source_record_id: r for r in await _reviews_for_records(w.db, w.cur_b, w.refs)}
    by_variant = {v[0]: reviews[w.cur_b[ref].source_record_id] for v, ref in zip(VARIANTS, w.refs)}
    assert (by_variant["sole_treatment_active"].classification_bucket,
            by_variant["sole_treatment_active"].classification_rule) == ("B4", "RULE-006")
    assert {by_variant[v].classification_bucket for v in ("combined_treatment", "non_treatment",
                                                          "inactive_treatment")} == {"B3"}
    review_ids = {r.review_id for r in reviews.values()}
    # the entity status says nothing - visibility cannot be coming from it
    assert {(await w.db.get(reg.TefcaRegEntity, e)).verification_status for e in w.entities} == {"not_verified"}

    # 1. the analysts' "available cases" queue (review-driven)
    available = await ca.available_cases(w.db, queue_source=rc.QUEUE_SOURCE, intake_id=w.intake_b)
    assert {c["review_id"] for c in available} == review_ids
    # 2. the supervisor operations work queue (review-driven), with bucket, reason and the basis
    queue = await supervisor_ops.work_queue(w.db, intake_id=w.intake_b, limit=50)
    items = {i["review_id"]: i for i in queue["items"]}
    assert set(items) == review_ids
    for item in items.values():
        assert item["submitted_values_basis"] == ap.SUBMITTED_VALUES_BASIS
        assert item["source_record_id"] and item["queue_source"] == rc.QUEUE_SOURCE and item["work_reasons"]
    # 3. the analyst workspace for the case: bucket, rule, rationale, the basis, and the untouched entity status
    b4 = by_variant["sole_treatment_active"]
    page = await workspace.workspace(w.db, b4.review_id)
    status = page["verification_status"]
    assert status["classification_bucket"] == "B4" and status["rule"]["rule_code"] == "RULE-006"
    assert status["submitted_values_basis"] == ap.SUBMITTED_VALUES_BASIS
    assert status["entity_verification_status"] == "not_verified"
    assert all(npi not in json.dumps(page, default=str) for npi in A_NPIS)           # and still no NPI of A's
    # 4. assignable: a supervisor assigns it, and it leaves the unowned queue for the analyst's own work list
    from app.models.database import User
    analyst = (await w.db.execute(select(User).limit(1))).scalars().first()
    if analyst is not None:
        supervisor = SimpleNamespace(id=analyst.id, email=analyst.email, role="program_manager")
        assigned = await ca.assign(w.db, b4.review_id, user=supervisor, to_user_id=analyst.id, reason="synthetic")
        assert assigned["assigned_to_user_id"] == str(analyst.id)
        assert b4.review_id not in {c["review_id"] for c in await ca.available_cases(
            w.db, queue_source=rc.QUEUE_SOURCE, intake_id=w.intake_b)}
        mine = await ca.my_work(w.db, user=supervisor, queue_source=rc.QUEUE_SOURCE, intake_id=w.intake_b)
        assert b4.review_id in {c["review_id"] for c in mine}
    # 5. the QHIN workload rollup: placed, under review through the review, labelled - never "held"
    placed, unplaced, summary = await qhin_workload.placement_units(w.db, w.intake_b)
    assert summary["reviewed_on_submitted_values"] == 4 and summary["held"] == 0 and unplaced == []
    assert {u["draw_eligibility"] for u in placed} == {"reviewed_on_submitted_values"}


# ═══ final-code review of #249: answering sources, the DEV-like rule composition, report cells, edge values ══════════

def _answering_sources(monkeypatch, sources, *, extract_candidate):
    """The NAME-keyed sources ANSWER (as on DEV, where the extract screen is ON): LEIE by organisation - no
    exclusion found; SAM by name - a registration answer, not excluded; the public extract - clear or a candidate.
    Result shapes are the connectors' own (`OIGLEIEConnector._build`, `SAMExtractConnector._build`): neither puts a
    top-level `npi` in its data; a candidate ROW may carry the excluded party's own NPI, as here."""
    from app.Tefca import connectors, sam_extract
    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")

    async def _leie_name(self_, last="", first="", org="", **k):
        sources.name_calls.append(("OIG_LEIE", org or last))
        return SourceResult.ok("OIG_LEIE", {
            "excluded": False, "exclusion_found": False, "active_exclusions": [], "historical_exclusions": [],
            "exclusion_count": 0, "exclusion_date": None, "exclusion_type": None, "reinstatement_date": None,
            "list_rows_searched": 80000, "list_loaded_at_epoch": 1764000000.0,
            "match_methods": ["exact_business_name", "normalized_business_name"],
            "candidates_exact": 0, "candidates_normalized_only": 0},
            {"last": last, "first": first, "org": org}, "LEIE-CSV")

    async def _sam_verify(self_, uei="", legal_name="", **k):
        sources.name_calls.append(("SAM_GOV.verify", legal_name))
        return SourceResult.ok("SAM_GOV", {"found": True, "matched_by": "name", "ambiguous": False,
                                           "excluded": False, "exclusions": [], "match_count": 1},
                               {"uei": uei, "legal_name": legal_name}, "SAM-v3/v4")

    async def _sam_name(self_, legal_name, *a, **k):
        sources.name_calls.append(("SAM_GOV", legal_name))
        return SourceResult.ok("SAM_GOV", {"found": True, "matched_by": "name", "ambiguous": False,
                                           "excluded": False, "exclusions": [], "match_count": 1},
                               {"legal_name": legal_name}, "SAM-v3/v4")

    async def _extract(self_, npi, org_name, *a, **k):
        sources.npi_calls.append(("SAM_EXTRACT", str(npi or "")))
        sources.name_calls.append(("SAM_EXTRACT", org_name))
        rows = ([{"name": "SYNTHETIC EXCLUDED PARTY", "npi": "1003000126", "classification": "Firm"}]
                if extract_candidate else [])
        return SourceResult.ok("SAM_EXTRACT", {
            "candidate_match": bool(rows), "candidate_count": len(rows), "candidates": rows,
            "match_methods": ["normalized_business_name"], "source_dataset": "SAM_Exclusions_Public_Extract_V2",
            "publication_date": "2026-11-24", "extract_sha256": "b" * 64, "list_rows_searched": 160000},
            {"npi": npi, "org": org_name}, "GSA-Public-Extract-V2")

    monkeypatch.setattr(connectors.OIGLEIEConnector, "lookup_by_name", _leie_name)
    monkeypatch.setattr(connectors.SAMGovConnector, "verify", _sam_verify)
    monkeypatch.setattr(connectors.SAMGovConnector, "lookup_by_name", _sam_name)
    monkeypatch.setattr(sam_extract.SAMExtractConnector, "screen", _extract)


def _dev_like_rules(monkeypatch):
    """The composition closest to DEV that the code allows: the ONC-scope Treatment rule, exclusion candidates to
    B3, the mandatory-source guards and structural NPI handling - with the EX-3 producer flag the consistency guard
    requires alongside `exclusion_candidate_b3`. Composed and handed to the writer; nothing is activated or stored."""
    from app.tefca_registry.rule_configuration import compose
    monkeypatch.setenv(ap.PROPOSED_EXCLUSION_STATES_ENV, "true")
    return [{**r, "version": 99} for r in compose({
        "treatment_b4_onc_scope", "exclusion_candidate_b3", "mandatory_source_guards", "structural_npi_handling"})]


@pytest.mark.parametrize("extract_candidate", [False, True])
async def test_equivalence_holds_when_the_name_keyed_sources_answer_and_the_extract_screen_is_on(
        world, monkeypatch, capsys, extract_candidate):
    """The demonstration above runs with every source "unavailable". Here the name-keyed sources ANSWER and the
    SAM extract screen is ON, under the DEV-like rule composition with an active mandatory sam_gov policy. The
    blank-NPI record still equals its no-NPI twin (bucket, rule, version, tier, whole classifier input), no
    NPI-keyed question was asked about the withheld NPI, and a name-keyed result that carries an excluded PARTY's
    NPI in a candidate row is not mistaken for evidence keyed on the withheld NPI (the record is not refused)."""
    from test_mandatory_source_parity_2026_11_11 import _policy
    w = world
    _answering_sources(monkeypatch, w.sources, extract_candidate=extract_candidate)
    res_b, res_n, b_calls = await _demonstrate(w, monkeypatch, rules=_dev_like_rules(monkeypatch),
                                               policies=[_policy()])
    table = await _assert_demonstration(w, res_b, res_n, b_calls)
    assert res_b["resolution_failures"] == []                          # nobody refused for the candidate row's NPI
    asked = {label for label, _name in w.sources.name_calls}
    assert {"OIG_LEIE", "SAM_EXTRACT"} <= asked                        # the name screens really ran
    review = await _review_of(w.db, res_b["outcomes"][0]["review_id"])
    sources_seen = {i["source"] for d in review.verification_results["dimensions"] for i in d.get("evidence") or []}
    assert {"OIG_LEIE", "SAM_EXTRACT_SUPPLEMENTARY"} <= sources_seen
    extract = [i for d in review.verification_results["dimensions"] for i in d.get("evidence") or []
               if i["source"] == "SAM_EXTRACT_SUPPLEMENTARY"][0]
    assert extract["disposition"] == ("REVIEW" if extract_candidate else "PASS")
    assert str(extract["query_identifier"]).startswith("org=")         # keyed on the NAME
    assert not ap._evidence_carries_npi({"dimensions": review.verification_results["dimensions"]})
    with capsys.disabled():
        print(f"\nDEMONSTRATION (DEV-like rules + mandatory sam_gov policy, sources answering, extract "
              f"{'CANDIDATE' if extract_candidate else 'clear'}):", table)


async def test_the_screen_cells_of_such_a_record_come_from_its_own_review_never_from_shared_entity_evidence(
        world, monkeypatch):
    """Delivery A is reviewed first, so the shared entity store holds A's NPI-keyed LEIE / SAM rows. B's report
    cells must not read those ("not attributed ... queried by an NPI") and must not print "clear": they quote what
    B's OWN review recorded for the name-keyed screens, labelled as such. One facts list feeds every rendering
    (CSV, Excel, PDF, frozen JSON), and each rendering is checked for the same three properties."""
    w = world
    _answering_sources(monkeypatch, w.sources, extract_candidate=True)
    await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_a)           # A's rows are in the store
    res_b = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)
    review_ids = {o["source_record_id"]: o["review_id"] for o in res_b["outcomes"]}
    out = await ld.load_record_facts(w.db, intake_id=w.intake_b, evidence_cutoff=datetime.utcnow() + timedelta(days=1))
    assert len(out["facts"]) == 4
    for fact in out["facts"]:
        assert fact["nppes"] == fact["pecos"] == ld.NOT_CHECKED_NO_SUBMITTED_NPI
        for key, recorded in (("leie", "NOT_FOUND"), ("sam_extract", "REVIEW")):
            cell = fact[key]
            assert cell.startswith("From this record's own review REV-") and "not taken from shared entity" in cell
            assert f"recorded {recorded} (keyed on the organisation name)" in cell, cell
        # the extract cell carries the ordinary cell's caveats: a candidate is never a confirmed exclusion, and the
        # screen is supplementary to the api.sam.gov check
        assert "NOT a confirmed exclusion" in fact["sam_extract"] and "edition 2026-11-24" in fact["sam_extract"]
        assert "supplementary to, not a substitute for, the api.sam.gov check" in fact["sam_extract"]
        assert fact["sam"].startswith(("From this record's own review REV-", "Not checked for this record: its own"))
        for key in ("nppes", "pecos", "leie", "sam", "sam_extract"):
            cell = fact[key].lower().replace("not exclusion clearance", "")     # the caveat itself
            assert "clear" not in cell and "not attributed" not in cell and "queried by" not in cell, (key, cell)
            assert all(npi not in fact[key] for npi in A_NPIS + ["1003000126"]), key
    assert any(rid in f["leie"] for f in out["facts"] for rid in review_ids.values())
    assert out["meta"]["stats"]["screen_cells_from_review_snapshot"] == 12
    # The CSV, Excel, PDF and frozen-JSON renderings are all produced from ONE shared dataset
    # (`load_dataset` -> `october_review_dataset.build_dataset`); the same properties hold in it.
    dataset = await ld.load_dataset(w.db, intake_id=w.intake_b, report_id="DA-TEST-BLANK-NPI",
                                    evidence_cutoff=datetime.utcnow() + timedelta(days=1))
    body = json.dumps(dataset, default=str)
    assert body.count("From this record's own review") >= 8 and ld.NOT_CHECKED_NO_SUBMITTED_NPI in body
    assert all(npi not in body for npi in A_NPIS + ["1003000126"])
    assert "queried by" not in body.lower()
    # delivery A's own report still reads ITS rows from the entity store
    out_a = await ld.load_record_facts(w.db, intake_id=w.intake_a, evidence_cutoff=datetime.utcnow() + timedelta(days=1))
    assert all("From this record's own review" not in json.dumps(f, default=str) for f in out_a["facts"])
    assert out_a["meta"]["stats"].get("screen_cells_from_review_snapshot", 0) == 0


@pytest.mark.parametrize("submitted,takes_the_path", [
    ("   ", True),            # whitespace only: blank, as delivered and stripped
    ("\t", True),             # a tab
    ("0", False),             # a value: not blank, not the registry's NPI -> refused exactly as before
    ("N/A", False),
    ("1234567893;1245319599", False),     # a multi-value cell
])
async def test_what_counts_as_a_blank_submitted_npi(world, monkeypatch, submitted, takes_the_path):
    w = world
    ref = w.refs[0]
    rec = await w.db.get(m.RceSourceRecord, w.cur_b[ref].source_record_id)
    rec.parsed = {**rec.parsed, "NPI": submitted}
    rec.npi = submitted
    await w.db.flush()
    w.sources.npi_calls.clear()
    res = await ap.verify_and_classify(w.db, [ref], intake_id=w.intake_b)
    if takes_the_path:
        assert res["verified"] == 1 and res["classified_on_submitted_values"] == [ref]
        assert w.sources.npis_asked() == set()
        review = await _review_of(w.db, res["outcomes"][0]["review_id"])
        fields = review.verification_results["classifier_input"]["fields"]
        raw = [s["raw_npi"] for s in fields.values() if isinstance(s, dict) and "raw_npi" in s]
        assert all(not str(value).strip() for value in raw)             # the classifier sees no NPI
    else:
        assert res["verified"] == 0 and res["classified_on_submitted_values"] == []
        assert res["resolution_failures"] == [{"entity_ref": ref, "reason": ap.REFUSAL_SUBMITTED_NPI_DIFFERS}]
        assert w.sources.npi_calls == []
    assert rec.parsed["NPI"] == submitted                               # the delivered value is never rewritten


async def test_records_spread_over_several_gather_chunks_are_all_classified_on_their_submitted_values(
        world, monkeypatch):
    w = world
    monkeypatch.setattr(ap, "_GATHER_CHUNK_SIZE", 1)                    # four records, four chunks
    res = await ap.verify_and_classify(w.db, list(w.refs), intake_id=w.intake_b)
    assert res["verified"] == 4 and res["classified_on_submitted_values"] == w.refs
    assert res["evidence_snapshot_only_submitted_values"] == w.refs and res["evidence_not_stored_unanchored"] == []
    assert [o["entity_ref"] for o in res["outcomes"]] == w.refs and w.sources.npis_asked() == set()
    assert await _evidence_rows(w.db, w.entities) == []


def test_the_supplementary_verify_route_states_that_it_does_not_filter_held_records():
    import inspect
    from app.tefca_registry.rce import routes as rce_routes
    doc = inspect.getdoc(rce_routes.verify)
    assert "NO HELD FILTER" in doc and "Do not use this" in doc
    assert "record_status" not in inspect.getsource(rce_routes.verify).split('"""')[2]     # behaviour unchanged


# ═══ delta review of a6a257c: the exclusion is audited on the plan; the cell says what was keyed; no cost before ════

async def test_the_plan_its_audit_rows_and_the_preview_state_the_reviewed_on_submitted_values_exclusion(
        world, monkeypatch):
    """Two of the four records are already reviewed on their submitted values. The preview says so; the official
    plan drawn next FREEZES the count and the units in its `strata_config`; the plan's audit row counts them and a
    `sampling_eligibility_excluded` audit row names them with the exclusion and its reason."""
    from app.tefca_registry import qhin_sampling as qs
    w = world
    await _give_qhin(w.db, w.entities)
    await ap.verify_and_classify(w.db, w.refs[:2], intake_id=w.intake_b)
    excluded_units = sorted((str(e), ref) for e, ref in zip(w.entities[:2], w.refs[:2]))

    preview = await qs.preview_plan(w.db, w.intake_b)
    assert preview["excluded_reviewed_on_submitted_values"] == 2 and preview["eligible_population"] == 2
    assert preview["excluded_review_required"] == 0

    _real_cycle(monkeypatch, w.db)
    cycle = await rc._create_review_cycle(w.db, w.intake_b, user=_PM, full_population=True)
    assert cycle["members"] == 2 and cycle["linked_this_call"] == 2
    sample = await w.db.get(reg.ReviewSample, uuid.UUID(cycle["plan"]["sample_id"]))
    config = sample.strata_config
    assert config["excluded_reviewed_on_submitted_values"] == 2 and config["excluded_review_required"] == 0
    assert sorted((u["entity_id"], u["rce_org_oid"])
                  for u in config["excluded_reviewed_on_submitted_values_units"]) == excluded_units
    audit = (await w.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action.in_(("sampling_plan_finalized", "sampling_eligibility_excluded"))))).scalars().all()
    mine = [a for a in audit if (a.metadata_ or {}).get("sample_id") == str(sample.id)]
    finalized = next(a.metadata_ for a in mine if a.action == "sampling_plan_finalized")
    assert finalized["excluded_reviewed_on_submitted_values"] == 2
    excluded_rows = [a.metadata_ for a in mine if a.action == "sampling_eligibility_excluded"]
    assert len(excluded_rows) == 1 and excluded_rows[0]["count"] == 2
    assert excluded_rows[0]["exclusion"] == qs.EXCLUSION_REVIEWED_ON_SUBMITTED_VALUES
    assert excluded_rows[0]["reason"] == qs.REVIEWED_ON_SUBMITTED_VALUES_REASON
    assert sorted(excluded_rows[0]["entity_ids"]) == sorted(str(e) for e in w.entities[:2])
    assert sorted(excluded_rows[0]["references"]) == sorted(w.refs[:2])


def test_the_review_snapshot_cell_says_what_each_item_was_keyed_on_and_carries_the_ordinary_caveats():
    """A quoted disposition must never read stronger than the ordinary cell for the same result: each item is
    labelled by what it was ACTUALLY keyed on, a SAM registration-only miss says the exclusion question was not
    screened, and the extract line keeps its "supplementary / not clearance" caveat, edition and check date."""
    rev = SimpleNamespace(review_id="REV-2026-000001", verification_results={"dimensions": [
        {"dimension": ap.EXCLUSION_DIMENSION, "evidence": [
            {"source": "SAM_GOV", "disposition": "NOT_FOUND", "query_identifier": "uei=ABCDEF123456",
             "normalized_values": {"screening_leg": "registration_only"}, "query_timestamp": "2026-11-25T10:00:00"},
            {"source": "SAM_GOV", "disposition": "PASS", "query_identifier": "legal_name=SYNTHETIC ORG"},
            {"source": "SAM_GOV", "disposition": "PASS"},
            {"source": "SAM_GOV", "disposition": "PASS", "query_identifier": "npi=1234567893"},     # never quoted
            {"source": "OIG_LEIE", "disposition": "NOT_FOUND", "query_identifier": "org=SYNTHETIC ORG"},
            {"source": "SAM_EXTRACT_SUPPLEMENTARY", "disposition": "PASS", "query_identifier": "org=SYNTHETIC ORG",
             "original_values": {"publication_date": "2026-11-24"}, "query_timestamp": "2026-11-25T10:00:00"},
        ]}]})
    sam = ld._review_snapshot_screen_cell(rev, "SAM_GOV")
    parts = sam.split(": ", 1)[1].split(" | ")
    assert len(parts) == 3                                                   # the npi= item is not quoted
    assert parts[0].startswith("recorded NOT_FOUND (keyed on an identifier other than NPI (UEI))")
    assert "SAM registration-only search; the exclusion question was NOT screened" in parts[0]
    assert "checked 2026-11-25" in parts[0]
    assert parts[1].startswith("recorded PASS (keyed on the organisation name)")
    assert parts[2].startswith("recorded PASS (key not recorded)") and "check date not recorded" in parts[2]
    assert "ABCDEF123456" not in sam and "SYNTHETIC ORG" not in sam and "1234567893" not in sam   # no identifier
    assert "name-keyed" not in sam and "clear" not in sam.lower().replace("clearance", "")
    leie = ld._review_snapshot_screen_cell(rev, "OIG_LEIE")
    assert "recorded NOT_FOUND (keyed on the organisation name)" in leie
    extract = ld._review_snapshot_screen_cell(rev, "SAM_EXTRACT_SUPPLEMENTARY")
    assert "no candidate match in the extract" in extract and "edition 2026-11-24" in extract
    assert "supplementary to, not a substitute for, the api.sam.gov check" in extract
    assert "a non-match is not exclusion clearance" in extract and "checked 2026-11-25" in extract
    none = ld._review_snapshot_screen_cell(SimpleNamespace(review_id="REV-X", verification_results={}), "SAM_GOV")
    assert none.startswith("Not checked for this record: its own review REV-X holds no SAM_GOV screen result")


async def test_the_frame_pays_nothing_for_a_delivery_assessed_before_the_effective_date(world, monkeypatch):
    """The reviewed-on-submitted-values read is not issued at all for a delivery assessed before the effective
    date (no such review can exist for it), and is issued for a later one."""
    from sqlalchemy import event
    from app.tefca_registry import qhin_sampling as qs
    w = world
    await _give_qhin(w.db, w.entities)
    seen = []
    sync_conn = (await w.db.connection()).sync_connection

    def _capture(_conn, _cursor, statement, *_rest):
        if "review_records" in statement and "verification_results" in statement:
            seen.append(statement)
    event.listen(sync_conn, "before_cursor_execute", _capture)
    try:
        run = (await w.db.execute(select(m.RceIngestionRun).where(
            m.RceIngestionRun.source_intake_id == w.intake_b))).scalars().first()
        run.assessment_metadata = {"assessment_date": "2026-11-08", "source": "pinned_param"}
        await w.db.flush()
        eligible, _unresolved = await qs.resolve_qhin_strata(w.db, w.intake_b)
        assert len(eligible) == 4 and seen == []                        # October-dated: the read is skipped
        assert await rc._refs_for(w.db, w.intake_b, list(w.entities)) and seen == []
        run.assessment_metadata = {"assessment_date": "2026-11-09", "source": "pinned_param"}
        await w.db.flush()
        await qs.resolve_qhin_strata(w.db, w.intake_b)
        assert len(seen) == 1                                           # from the effective date: one read
    finally:
        event.remove(sync_conn, "before_cursor_execute", _capture)


# ═══ a member whose record is already reviewed but was never linked (second delta review of #249) ═══════════════════
#
# The writer commits its reviews before the cycle commits the links. A crash or timeout in between leaves a member
# UNLINKED with its own review classified on submitted values; since the frame and `_refs_for` skip such a record,
# the member used to get no reference, was reported as "no delivered reference" (false) and the cycle could never
# complete. The cycle now links the member to ITS OWN existing review. Auto-linking is the coordinator's
# engineering choice, for the owner to confirm.

async def _crash_between_reviews_and_links(w, monkeypatch):
    """Run the first cycle call so that the reviews are committed and the linking never happens."""
    real_verify = ap.verify_and_classify

    async def _verify_then_die(db_, refs, **kw):
        await real_verify(db_, refs, **kw)                              # the reviews ARE committed
        raise RuntimeError("synthetic crash between the review commit and the link commit")
    monkeypatch.setattr(ap, "verify_and_classify", _verify_then_die)
    intake_b, entities = w.intake_b, list(w.entities)
    record_ids = [w.cur_b[ref].source_record_id for ref in w.refs]       # plain values: the failure rolls back
    with pytest.raises(RuntimeError, match="synthetic crash"):
        await rc._create_review_cycle(w.db, intake_b, user=_PM, full_population=True)
    monkeypatch.setattr(ap, "verify_and_classify", real_verify)
    return intake_b, entities, record_ids


async def _review_ids(db, record_ids):
    return sorted((await db.execute(select(reg.ReviewRecord.review_id).where(
        reg.ReviewRecord.source_record_id.in_(record_ids)))).scalars().all())


async def test_after_a_crash_the_next_call_links_each_member_to_its_own_existing_review(world, monkeypatch):
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    intake_b, entities, record_ids = await _crash_between_reviews_and_links(w, monkeypatch)
    existing = await _review_ids(w.db, record_ids)
    assert len(existing) == 4                                           # reviewed ...
    members = (await w.db.execute(select(reg.SampleEntity.review_id).where(
        reg.SampleEntity.entity_id.in_(entities)))).scalars().all()
    assert members == [None] * 4                                        # ... and not one member linked
    w.sources.npi_calls.clear()
    w.sources.name_calls.clear()

    result = await rc._create_review_cycle(w.db, intake_b, user=_PM, full_population=True)

    assert result["linked_this_call"] == 4 and result["remaining"] == 0 and result["complete"] is True
    assert result["linked_existing_submitted_values_review"] == 4
    assert sorted(result["linked_existing_submitted_values_review_ids"]) == existing
    assert result["not_reviewable_for_identity"] == [] and result["verification"]["requested"] == 0
    assert await _review_ids(w.db, record_ids) == existing              # NO second review
    assert w.sources.npi_calls == [] and w.sources.name_calls == []     # NO source asked
    rows = (await w.db.execute(select(reg.SampleEntity.entity_id, reg.SampleEntity.review_id,
                                      reg.SampleEntity.review_status)
                               .where(reg.SampleEntity.entity_id.in_(entities)))).all()
    assert sorted(r[1] for r in rows) == existing and {r[2] for r in rows} == {"in_review"}
    for entity_id, review_id, _status in rows:                          # each member got ITS OWN review
        review = await _review_of(w.db, review_id)
        assert review.entity_id == entity_id and review.source_record_id in record_ids
        assert review.verification_results["queue_source"] == rc.QUEUE_SOURCE     # and it is in the work queue
        assert review.verification_results["submitted_values_basis"]
    audit = (await w.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_members_verified"))).scalars().all()
    assert any((a.metadata_ or {}).get("linked_existing_submitted_values_review") == 4
               and sorted(a.metadata_["linked_existing_submitted_values_review_ids"]) == existing for a in audit)
    again = await rc._create_review_cycle(w.db, intake_b, user=_PM, full_population=True)
    assert again["linked_this_call"] == 0 and again["complete"] is True


@pytest.mark.parametrize("case", ["another_entity", "another_sample", "another_member"])
async def test_an_existing_review_that_is_not_the_members_own_is_never_linked(world, monkeypatch, case):
    """The existing review is linked only when it is the member's own and nobody else's. A review of a different
    entity, one that belongs to another sample, or one another sample member is already linked to, is NOT linked:
    the member is reported with the review id and a reason of its own (kind `already_reviewed`), never as "no
    delivered reference"; the stop signal stays false (this is not a settled identity fact - a person has to look);
    the sample is not complete; no new review is made and no source is asked."""
    w = world
    await _give_qhin(w.db, w.entities)
    _real_cycle(monkeypatch, w.db)
    intake_b, entities, record_ids = await _crash_between_reviews_and_links(w, monkeypatch)
    existing = await _review_ids(w.db, record_ids)
    target = (await w.db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.source_record_id == record_ids[0]))).scalars().one()
    target_id, target_entity = target.review_id, target.entity_id
    if case == "another_entity":
        target.entity_id = w.twins[0]
        why = rc.WHY_OTHER_ENTITY
    elif case == "another_sample":
        other_sample = reg.ReviewSample(id=uuid.uuid4(), sample_name="synthetic other sample",
                                        review_type="weekly", population_size=1, sample_size=1,
                                        confidence_level=0.95, margin_of_error=0.05, proportion=0.5)
        w.db.add(other_sample)
        await w.db.flush()
        target.sample_id = other_sample.id
        why = rc.WHY_OTHER_SAMPLE
    else:
        other_sample = reg.ReviewSample(id=uuid.uuid4(), sample_name="synthetic other sample",
                                        review_type="weekly", population_size=1, sample_size=1,
                                        confidence_level=0.95, margin_of_error=0.05, proportion=0.5)
        w.db.add(other_sample)
        await w.db.flush()
        w.db.add(reg.SampleEntity(id=uuid.uuid4(), sample_id=other_sample.id, entity_id=w.twins[0],
                                  review_id=target_id, review_status="in_review", stratum="s"))
        why = rc.WHY_OTHER_MEMBER
    await w.db.flush()
    w.sources.npi_calls.clear()
    w.sources.name_calls.clear()

    result = await rc._create_review_cycle(w.db, intake_b, user=_PM, full_population=True)

    assert result["linked_this_call"] == 3 and result["linked_existing_submitted_values_review"] == 3
    assert target_id not in result["linked_existing_submitted_values_review_ids"]
    assert result["remaining"] == 1 and result["complete"] is False
    assert result["no_further_reviews_possible"] is False               # not a settled identity reason
    assert result["not_reviewable_already_reviewed_count"] == 1 and result["not_reviewable_identity_count"] == 0
    (listed,) = result["not_reviewable_for_identity"]
    assert listed["kind"] == rc.KIND_ALREADY_REVIEWED and listed["existing_review"] == target_id
    assert listed["entity_id"] == str(target_entity)
    assert listed["reason"] == rc.NOT_REVIEWABLE_ALREADY_REVIEWED.format(review=target_id, why=why)
    assert "no delivered reference" not in listed["reason"]
    assert await _review_ids(w.db, record_ids) == existing              # no new review for it either
    assert w.sources.npi_calls == [] and w.sources.name_calls == []
    member = (await w.db.execute(select(reg.SampleEntity).where(
        reg.SampleEntity.entity_id == target_entity,
        reg.SampleEntity.sample_id == uuid.UUID(result["plan"]["sample_id"])))).scalars().one()
    assert member.review_id is None and member.review_status == "pending"


@pytest.mark.parametrize("full_population", [True, False])
async def test_both_plan_kinds_freeze_the_reviewed_on_submitted_values_exclusion(world, monkeypatch,
                                                                                 full_population):
    """The enumerated plan and the SAMPLED plan both record the count and the units in `strata_config`."""
    from app.tefca_registry import qhin_sampling as qs
    w = world
    await _give_qhin(w.db, w.entities)
    await ap.verify_and_classify(w.db, w.refs[:2], intake_id=w.intake_b)
    plan = await qs.finalize_plan(w.db, w.intake_b, full_population=full_population)
    assert plan["excluded_reviewed_on_submitted_values"] == 2
    sample = await w.db.get(reg.ReviewSample, uuid.UUID(plan["sample_id"]))
    config = sample.strata_config
    assert config["selection_algorithm"] == ("FULL_POPULATION_ENUMERATION" if full_population
                                             else qs.SELECTION_ALGORITHM)
    assert config["excluded_reviewed_on_submitted_values"] == 2
    assert sorted(u["rce_org_oid"] for u in config["excluded_reviewed_on_submitted_values_units"]) == sorted(w.refs[:2])
    members = (await w.db.execute(select(reg.SampleEntity.entity_id).where(
        reg.SampleEntity.sample_id == sample.id))).scalars().all()
    assert set(members) <= set(w.entities[2:]) and members             # drawn only from the records not yet reviewed
