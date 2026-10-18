"""Shadow reassessment — classifier-only re-run over PERSISTED evidence,
pinned, hashed, approval-bound, with successor publication refused outside
local test mode.

TWO KINDS OF PROOF, KEPT APART
  1. ENGINE proofs use a what-if candidate rule set (v2 plus one synthetic
     B4 rule keyed on a synthetic field signal) and hand-built official
     records, so NEW / REMOVED / CHANGED / UNCHANGED / NOT_REPRODUCIBLE,
     direction, hashing, approvals, staleness and publication are proven
     independently of any live rules question.
  2. The v2-vs-v3 REAL comparison runs over records the real pipeline
     produced (ingest -> quality -> curate -> promote -> verify_and_classify,
     connector methods patched, no network) for one entity with a CONFIRMED
     exclusion (persisted REVIEW) and one with a CLEAN name screen
     (persisted NOT_FOUND). It asserts only that the comparison DETECTS and
     REPORTS each movement with its direction and the per-source states that
     triggered it -- including which kind of `not_found` each was. Whether
     v3 is RIGHT to disqualify on a clean screen is a rules/translator
     question owned elsewhere; this pilot exists to surface exactly that
     before official application, not to decide it.

Official rows (review_records / rce_issues / tefca_dimension_evidence) are
fingerprinted before and after every step and must not change.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

import test_sam_e2e_delivery_path as sam


class _User:
    def __init__(self, role: str, tag: str):
        self.id = uuid.uuid4()
        self.email = f"shadow-{role}-{tag}@synthetic-test.docuaction.invalid"
        self.role = role


# ── inputs and rule sets ─────────────────────────────────────────────────────

CLEAN_SOURCES = {"nppes": {"status": "verified"}, "oig_leie": {"status": "verified"},
                 "pecos": {"status": "verified"}}
CLEAN_B1 = {"sources": CLEAN_SOURCES, "fields": {}, "confidence_score": None}
SIGNAL_B1 = {"sources": CLEAN_SOURCES,
             "fields": {"shadow_test_signal": {"status": True}}, "confidence_score": None}
FEIN_SIGNAL = {"sources": {**CLEAN_SOURCES, "irs_fein": {"status": "unavailable"}},
               "fields": {"fein_verification": {"status": "flagged"},
                          "shadow_test_signal": {"status": True}}, "confidence_score": None}
OIG_EXCLUDED_B4 = {"sources": {**CLEAN_SOURCES, "oig_leie": {"status": "excluded"}},
                   "fields": {}, "confidence_score": None}

WHAT_IF_RULE = {
    "rule_code": "RULE-900", "name": "B4 what-if signal", "bucket": "B4", "priority": 1,
    "conditions": {"any_of": [{"field": "shadow_test_signal", "status": True}]},
    "description": "synthetic what-if disqualifier used only by this test", "version": 0,
}


async def _v2_plus_what_if(db):
    from app.tefca_registry.rce import shadow_reassessment as shadow
    return (await shadow.rules_for_version(db, 2)) + [dict(WHAT_IF_RULE)]


async def _ensure_rule_versions(db) -> None:
    from app.tefca_registry.bucket_classifier import (ensure_rules_v2, ensure_rules_v3,
                                                       ensure_seed_rules)
    await ensure_seed_rules(db)
    await ensure_rules_v2(db)
    await ensure_rules_v3(db)


async def _active_rule_version(db) -> int:
    from app.tefca_registry import models as reg
    v = (await db.execute(select(func.max(reg.ReviewRule.version))
                          .where(reg.ReviewRule.is_active.is_(True)))).scalar()
    return int(v or 0)


async def _official_fingerprint(entity_ids):
    """Byte-level fingerprint of every official row for these entities."""
    from app.Tefca.models import TEFCADimensionEvidence
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        reviews = (await db.execute(
            select(reg.ReviewRecord).where(reg.ReviewRecord.entity_id.in_(entity_ids))
            .order_by(reg.ReviewRecord.review_id))).scalars().all()
        ev = (await db.execute(
            select(TEFCADimensionEvidence.id, TEFCADimensionEvidence.disposition,
                   TEFCADimensionEvidence.original_values)
            .where(TEFCADimensionEvidence.entity_id.in_([str(e) for e in entity_ids]))
            .order_by(TEFCADimensionEvidence.id))).all()
        issues = int((await db.execute(select(func.count()).select_from(m.RceIssue))).scalar() or 0)
    payload = [[r.review_id, r.classification_bucket, r.classification_rule,
                r.classification_rule_version, r.classification_rationale,
                r.reviewer_resolution, r.reclassified_to, str(r.reportable_at),
                json.dumps(r.verification_results, sort_keys=True, default=str)]
               for r in reviews] + [[str(a), b, json.dumps(c, sort_keys=True, default=str)]
                                    for a, b, c in ev] + [issues]
    return hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest(), len(reviews)


def _sam_factory_confirmed_and_clean():
    """Entity ...0000: a confirmed exclusion (UEI match, excluded) -> persisted
    REVIEW. Every other entity: a clean NAME screen, nothing listed ->
    persisted NOT_FOUND (a name search is weaker than a UEI match, so it is
    NOT_FOUND rather than PASS by the assembly's own rule)."""
    from app.Tefca.connectors import SourceResult

    def factory(*, uei, legal_name):
        if str(legal_name).strip().endswith("0000"):
            return SourceResult.ok("SAM_GOV", {
                "found": True, "matched_by": "uei", "excluded": True, "excluded_known": True,
                "identity_ambiguous": False, "registration_current": True}, {"uei": uei})
        return SourceResult.ok("SAM_GOV", {
            "found": False, "matched_by": "name", "excluded": False, "excluded_known": True,
            "identity_ambiguous": False, "registration_current": None}, {"legal_name": legal_name})
    return factory


async def _seed(monkeypatch, n: int, *, sam_factory=None):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    sam._clean_nppes_leie(monkeypatch)
    sam._patch_sam_verify(monkeypatch, sam_factory or _sam_factory_confirmed_and_clean())

    async with async_session_maker() as db:
        await _ensure_rule_versions(db)
    intake_id = await sam._seed_promoted_delivery(n=n)
    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, n)
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id, actor="pytest-shadow")
    assert result["verified"] == n, result
    entity_ids = [uuid.UUID(o["entity_id"]) for o in result["outcomes"]]
    return intake_id, entity_ids, result


async def _plant_latest_record(entity_id, *, rule_version: int, classifier_input: dict,
                               bucket: str, rule: str):
    """A hand-built, LATER official review record for an entity, so the
    comparison's input is deterministic (the pipeline's own record stays too,
    untouched). created_at is left to the server default, exactly as the
    pipeline leaves it, so "latest" ordering is like-for-like."""
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce.arc_pipeline import (_allocate_review_id,
                                                      _lock_review_id_allocation)
    async with async_session_maker() as db:
        await _lock_review_id_allocation(db)
        rid = await _allocate_review_id(db)
        db.add(reg.ReviewRecord(
            id=uuid.uuid4(), review_id=rid, entity_id=entity_id,
            verification_results={"classifier_input": classifier_input,
                                  "generation_timestamp": datetime.now(timezone.utc).isoformat(),
                                  "resolution_source": "pytest-shadow-planted"},
            classification_bucket=bucket, classification_rule=rule,
            classification_rule_version=rule_version,
            classification_rationale="pytest-shadow planted official record"))
        await db.commit()
    return rid


async def _raise_on_any_connector_call(monkeypatch):
    from app.Tefca import connectors as c

    async def boom(self, *a, **k):
        raise AssertionError("a connector was called during a shadow comparison")
    for cls in (c.NPPESConnector, c.OIGLEIEConnector, c.SAMGovConnector, c.PECOSConnector):
        for name in ("lookup_by_npi", "lookup_by_uei", "lookup_by_name", "verify", "check_exclusions"):
            if hasattr(cls, name):
                monkeypatch.setattr(cls, name, boom)


# ── 1. engine proofs (what-if candidate; rules-question independent) ─────────

@pytest.mark.asyncio
async def test_engine_reports_new_stricter_with_no_predecessor_finding_and_touches_nothing(
        db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    intake_id, entity_ids, _ = await _seed(monkeypatch, n=2)
    planted = await _plant_latest_record(entity_ids[0], rule_version=2, classifier_input=SIGNAL_B1,
                                         bucket="B1", rule="RULE-001")
    planted_clean = await _plant_latest_record(entity_ids[1], rule_version=2, classifier_input=CLEAN_B1,
                                               bucket="B1", rule="RULE-001")
    before, n_before = await _official_fingerprint(entity_ids)
    await _raise_on_any_connector_call(monkeypatch)

    async with async_session_maker() as db:
        candidate = await _v2_plus_what_if(db)
        dto = await shadow.build_comparison(db, intake_id, built_by="pytest-shadow",
                                            baseline_rule_version=2, candidate_rules=candidate)
        deltas = await shadow.list_deltas(db, uuid.UUID(dto["comparison_id"]), limit=100)

    assert dto["summary"]["connector_calls_made"] == 0
    assert dto["summary"]["official_rows_written"] == 0
    assert dto["stale"] is False and dto["official_findings_modified"] is False
    assert dto["candidate_is_what_if"] is True and dto["candidate_rule_version"] is None
    assert len(dto["package_hash"]) == 64 and len(dto["candidate_rules_hash"]) == 64
    pins = dto["evidence_pinned"]["review_records"]
    assert {p["review_id"] for p in pins} >= {planted, planted_clean}
    assert dto["evidence_pinned"]["evidence_generation_stamps"]
    assert dto["evaluation_date"]

    d0 = next(d for d in deltas["items"] if d["entity_id"] == str(entity_ids[0]))
    assert d0["delta_kind"] == pm.DELTA_NEW and d0["direction"] == pm.DIR_STRICTER
    assert d0["baseline"] == {"bucket": "B1", "rule": "RULE-001", "rule_version": 2}
    assert d0["candidate"] == {"bucket": "B4", "rule": "RULE-900", "rule_version": 0}
    assert d0["detail"]["baseline_reproduced"] is True
    assert d0["detail"]["predecessor_finding_id"] is None      # a NEW finding needs no predecessor finding
    assert d0["predecessor_review_id"] == planted               # the review it was measured against
    assert d0["detail"]["field_signals"] == ["shadow_test_signal"]
    assert d0["detail"]["source_states"]["nppes"]["status"] == "verified"
    assert d0["manual_review_required"] is False

    d1 = next(d for d in deltas["items"] if d["entity_id"] == str(entity_ids[1]))
    assert d1["delta_kind"] == pm.DELTA_UNCHANGED and d1["direction"] == pm.DIR_NEUTRAL
    assert dto["summary"]["by_kind"][pm.DELTA_NEW] == 1
    assert dto["summary"]["by_direction"][pm.DIR_STRICTER] == 1

    after, n_after = await _official_fingerprint(entity_ids)
    assert after == before and n_after == n_before, "official rows changed during a shadow build"


@pytest.mark.asyncio
async def test_engine_reports_more_permissive_change_and_not_reproducible_baseline(
        db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    intake_id, entity_ids, _ = await _seed(monkeypatch, n=2)
    # Official B4 under v2 via the v1-era `oig_leie == excluded` literal (frozen history).
    await _plant_latest_record(entity_ids[0], rule_version=2, classifier_input=OIG_EXCLUDED_B4,
                               bucket="B4", rule="RULE-005")
    # Official says B3/RULE-004 but v2 re-classifies the persisted input as B1: not like-for-like.
    await _plant_latest_record(entity_ids[1], rule_version=2, classifier_input=CLEAN_B1,
                               bucket="B3", rule="RULE-004")
    async with async_session_maker() as db:
        v2 = await shadow.rules_for_version(db, 2)
        without_disqualifier = [r for r in v2 if r["rule_code"] != "RULE-005"]
        dto = await shadow.build_comparison(db, intake_id, built_by="pytest",
                                            baseline_rule_version=2, candidate_rules=without_disqualifier)
        deltas = await shadow.list_deltas(db, uuid.UUID(dto["comparison_id"]))
    d0 = next(d for d in deltas["items"] if d["entity_id"] == str(entity_ids[0]))
    # Without RULE-005 the v2 set still refuses B1/B2 for an excluded LEIE
    # state (RULE-001 requires `clear`; RULE-003/004 guard on `excluded`), so
    # the entity falls to the unmatched B3 default: CHANGED, MORE_PERMISSIVE.
    assert d0["delta_kind"] == pm.DELTA_CHANGED and d0["direction"] == pm.DIR_MORE_PERMISSIVE
    assert d0["baseline"]["bucket"] == "B4" and d0["candidate"]["bucket"] == "B3"
    assert d0["candidate"]["rule"] is None   # DEFAULT-UNMATCHED
    assert d0["detail"]["source_states"]["oig_leie"]["status"] == "excluded"

    d1 = next(d for d in deltas["items"] if d["entity_id"] == str(entity_ids[1]))
    assert d1["delta_kind"] == pm.DELTA_NOT_REPRODUCIBLE and d1["manual_review_required"] is True
    assert d1["detail"]["baseline_reproduced"] is False
    assert dto["summary"]["by_direction"][pm.DIR_MORE_PERMISSIVE] == 1


@pytest.mark.asyncio
async def test_approvals_bind_to_hash_and_refuse_the_same_person(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    intake_id, entity_ids, _ = await _seed(monkeypatch, n=1)
    await _plant_latest_record(entity_ids[0], rule_version=2, classifier_input=SIGNAL_B1,
                               bucket="B1", rule="RULE-001")
    tag = uuid.uuid4().hex[:6]
    analyst, qa, qa2 = _User("reviewer", tag), _User("qalead", tag), _User("qalead", tag + "b")

    async with async_session_maker() as db:
        dto = await shadow.build_comparison(db, intake_id, built_by=analyst.email,
                                            baseline_rule_version=2,
                                            candidate_rules=await _v2_plus_what_if(db))
    cid = uuid.UUID(dto["comparison_id"])
    h = dto["package_hash"]

    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="package hash mismatch"):
            await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_ANALYST, user=analyst,
                                         package_hash="0" * 64, rationale="x")
    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="rationale is required"):
            await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_ANALYST, user=analyst,
                                         package_hash=h, rationale="  ")
    async with async_session_maker() as db:
        dto = await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_ANALYST, user=analyst,
                                           package_hash=h, rationale="reviewed every delta")
    assert [a["approval_role"] for a in dto["approvals"]] == ["ANALYST"]
    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="segregation of duties"):
            await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_INDEPENDENT_QA,
                                         user=analyst, package_hash=h, rationale="self-QA")
    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="already stands"):
            await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_ANALYST, user=qa2,
                                         package_hash=h, rationale="duplicate")
    async with async_session_maker() as db:
        dto = await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_INDEPENDENT_QA,
                                           user=qa, package_hash=h, rationale="independent QA")
    assert sorted(a["approval_role"] for a in dto["approvals"]) == ["ANALYST", "INDEPENDENT_QA"]
    assert {a["actor_email"] for a in dto["approvals"]} == {analyst.email, qa.email}


@pytest.mark.asyncio
async def test_publication_refused_outside_local_mode_then_idempotent_in_it(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    intake_id, entity_ids, _ = await _seed(monkeypatch, n=2)
    planted = await _plant_latest_record(entity_ids[0], rule_version=2, classifier_input=SIGNAL_B1,
                                         bucket="B1", rule="RULE-001")
    # An EIN/FEIN-touching input on the second entity: flagged, never auto-superseded.
    await _plant_latest_record(entity_ids[1], rule_version=2, classifier_input=FEIN_SIGNAL,
                               bucket="B1", rule="RULE-001")
    tag = uuid.uuid4().hex[:6]
    analyst, qa = _User("reviewer", tag), _User("qalead", tag)

    async with async_session_maker() as db:
        dto = await shadow.build_comparison(db, intake_id, built_by=analyst.email,
                                            baseline_rule_version=2,
                                            candidate_rules=await _v2_plus_what_if(db))
        cid = uuid.UUID(dto["comparison_id"])
        deltas = await shadow.list_deltas(db, cid)
    fein_delta = next(d for d in deltas["items"] if d["entity_id"] == str(entity_ids[1]))
    assert fein_delta["delta_kind"] == pm.DELTA_NEW and fein_delta["manual_review_required"] is True
    assert "manual review required" in fein_delta["reason"]
    assert dto["summary"]["manual_review_required"] == 1

    before, n_before = await _official_fingerprint(entity_ids)

    # 1. Mode unset: refused, REFUSED event, nothing written.
    monkeypatch.delenv(shadow.PUBLICATION_MODE_ENV, raising=False)
    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="outside this code's authorization"):
            await shadow.publish_successors(db, cid, user=qa)
    async with async_session_maker() as db:
        dto = await shadow.comparison_dto(db, await db.get(pm.RceShadowComparison, cid))
    assert dto["publication_events"][-1]["event_type"] == pm.PUB_REFUSED
    assert dto["publication_events"][-1]["mode"] == "unset"
    assert (await _official_fingerprint(entity_ids))[1] == n_before

    # 2. Local mode but no approvals: refused with the missing roles named.
    monkeypatch.setenv(shadow.PUBLICATION_MODE_ENV, shadow.LOCAL_TEST_MODE)
    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="missing: ANALYST, INDEPENDENT_QA"):
            await shadow.publish_successors(db, cid, user=qa)

    # 3. Both approvals, local mode: successors written, predecessors untouched.
    async with async_session_maker() as db:
        await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_ANALYST, user=analyst,
                                     package_hash=dto["package_hash"], rationale="ok")
    async with async_session_maker() as db:
        await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_INDEPENDENT_QA, user=qa,
                                     package_hash=dto["package_hash"], rationale="ok")
    async with async_session_maker() as db:
        pub = await shadow.publish_successors(db, cid, user=qa)
    assert pub["already_published"] is False and pub["mode"] == shadow.LOCAL_TEST_MODE
    assert len(pub["successor_review_ids"]) == 1
    assert pub["predecessor_review_ids"] == [planted]
    assert pub["withheld"] and pub["withheld"][0]["entity_id"] == str(entity_ids[1])
    assert "never auto-superseded" in pub["withheld"][0]["reason"]

    # The predecessor rows are byte-identical; exactly one NEW row exists.
    after, n_after = await _official_fingerprint(entity_ids)
    assert n_after == n_before + 1
    async with async_session_maker() as db:
        succ = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == pub["successor_review_ids"][0]))).scalars().one()
        pred = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == planted))).scalars().one()
    assert succ.classification_bucket == "B4" and succ.classification_rule == "RULE-900"
    assert succ.classification_rule_version == 0
    assert succ.verification_results["shadow_successor"]["predecessor_review_id"] == planted
    assert succ.verification_results["shadow_successor"]["package_hash"] == dto["package_hash"]
    assert succ.classification_rationale.startswith(f"[SHADOW-SUCCESSOR of {planted}")
    assert pred.classification_bucket == "B1" and pred.classification_rule_version == 2
    assert pred.classification_rationale == "pytest-shadow planted official record"
    async with async_session_maker() as db:
        fein_succ = (await db.execute(select(func.count()).select_from(reg.ReviewRecord).where(
            reg.ReviewRecord.entity_id == entity_ids[1],
            reg.ReviewRecord.classification_rationale.like("[SHADOW-SUCCESSOR%")))).scalar()
    assert fein_succ == 0   # the FEIN-flagged predecessor was withheld, so it has no successor

    # 4. Retry: idempotent -- same successors, no new rows, ALREADY_PUBLISHED event --
    #    even though the successor has made the package stale by construction.
    async with async_session_maker() as db:
        again = await shadow.publish_successors(db, cid, user=qa)
    assert again["already_published"] is True
    assert again["successor_review_ids"] == pub["successor_review_ids"]
    assert (await _official_fingerprint(entity_ids))[1] == n_before + 1
    async with async_session_maker() as db:
        dto = await shadow.comparison_dto(db, await db.get(pm.RceShadowComparison, cid))
    kinds = [e["event_type"] for e in dto["publication_events"]]
    assert kinds[-2:] == [pm.PUB_PUBLISHED, pm.PUB_ALREADY_PUBLISHED]
    assert dto["stale"] is True, "a successor is itself a newer official record, so the package is now stale"


@pytest.mark.asyncio
async def test_stale_baseline_refuses_approval_and_publication(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    intake_id, entity_ids, _ = await _seed(monkeypatch, n=1)
    planted = await _plant_latest_record(entity_ids[0], rule_version=2, classifier_input=SIGNAL_B1,
                                         bucket="B1", rule="RULE-001")
    tag = uuid.uuid4().hex[:6]
    analyst, qa = _User("reviewer", tag), _User("qalead", tag)

    async with async_session_maker() as db:
        dto = await shadow.build_comparison(db, intake_id, built_by=analyst.email,
                                            baseline_rule_version=2,
                                            candidate_rules=await _v2_plus_what_if(db))
        cid = uuid.UUID(dto["comparison_id"])
    async with async_session_maker() as db:
        await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_ANALYST, user=analyst,
                                     package_hash=dto["package_hash"], rationale="ok")

    # An official change lands after the analyst approval: a human confirms the record.
    # A real confirmation leaves a real event -- write it so this fixture does
    # not manufacture a resolved row with no backing decision
    # (see tests/test_qa_gate.py::test_no_fabricated_history_for_existing_determinations).
    async with async_session_maker() as db:
        row = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == planted))).scalars().one()
        row.reviewer_resolution = "confirmed"
        db.add(reg.ReviewDecisionEvent(
            id=uuid.uuid4(), review_id=row.review_id, sequence_number=1,
            event_type="ANALYST_DETERMINATION", actor_user_id=analyst.id,
            actor_email=analyst.email, actor_role=analyst.role,
            determination="CONFIRM",
            rationale="SYNTHETIC: confirmed after analyst approval (test fixture)"))
        await db.commit()

    monkeypatch.setenv(shadow.PUBLICATION_MODE_ENV, shadow.LOCAL_TEST_MODE)
    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="stale baseline"):
            await shadow.record_approval(db, cid, approval_role=pm.APPROVAL_INDEPENDENT_QA, user=qa,
                                         package_hash=dto["package_hash"], rationale="late")
    async with async_session_maker() as db:
        with pytest.raises(shadow.ShadowRefused, match="stale baseline|missing"):
            await shadow.publish_successors(db, cid, user=qa)
    async with async_session_maker() as db:
        dto = await shadow.comparison_dto(db, await db.get(pm.RceShadowComparison, cid))
    assert dto["stale"] is True
    assert dto["publication_events"][-1]["event_type"] == pm.PUB_REFUSED


# ── 2. the REAL v2-vs-v3 comparison: detect and report, do not adjudicate ────

@pytest.mark.asyncio
async def test_real_v2_v3_comparison_distinguishes_confirmed_exclusion_from_clean_screen(
        db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce import shadow_reassessment as shadow

    intake_id, entity_ids, result = await _seed(monkeypatch, n=2)   # ...0000 confirmed, ...0001 clean
    confirmed_id, clean_id = entity_ids[0], entity_ids[1]
    async with async_session_maker() as db:
        official_version = await _active_rule_version(db)
    other_version = 2 if official_version == 3 else 3
    before, n_before = await _official_fingerprint(entity_ids)
    await _raise_on_any_connector_call(monkeypatch)

    async with async_session_maker() as db:
        dto = await shadow.build_comparison(db, intake_id, built_by="pytest-shadow",
                                            baseline_rule_version=official_version,
                                            candidate_rule_version=other_version)
        deltas = await shadow.list_deltas(db, uuid.UUID(dto["comparison_id"]))
    assert dto["summary"]["connector_calls_made"] == 0
    assert (await _official_fingerprint(entity_ids)) == (before, n_before)

    d_conf = next(d for d in deltas["items"] if d["entity_id"] == str(confirmed_id))
    d_clean = next(d for d in deltas["items"] if d["entity_id"] == str(clean_id))

    # Both official records reproduce under their own version (like-for-like).
    assert d_conf["detail"]["baseline_reproduced"] is True
    assert d_clean["detail"]["baseline_reproduced"] is True

    # The triggering source states are reported per entity, with the ORIGIN
    # of each `not_found`: a pending REVIEW hit versus a clean NOT_FOUND screen.
    s_conf = d_conf["detail"]["source_states"]["sam_gov"]
    s_clean = d_clean["detail"]["source_states"]["sam_gov"]
    assert s_conf["disposition"] == "REVIEW"
    assert s_conf["status"] == "not_found" and s_conf["not_found_origin"] == "pending_hit_REVIEW"
    assert s_clean["disposition"] == "NOT_FOUND"
    if s_clean["status"] == "not_found":
        assert s_clean["not_found_origin"] == "clean_screen_NOT_FOUND"
    else:
        assert s_clean["not_found_origin"] is None   # a translator that keeps a clean screen apart
    assert s_conf != s_clean, "the pilot must distinguish the two kinds of not_found"

    # The movement between v2 and v3 is DETECTED and reported with a direction;
    # v3 is the stricter set, so any change toward v3 reads STRICTER and any
    # change toward v2 reads MORE_PERMISSIVE. Which buckets are RIGHT is not
    # asserted here.
    toward_other = pm.DIR_STRICTER if other_version == 3 else pm.DIR_MORE_PERMISSIVE
    for d in (d_conf, d_clean):
        assert d["delta_kind"] in (pm.DELTA_NEW, pm.DELTA_REMOVED, pm.DELTA_CHANGED, pm.DELTA_UNCHANGED)
        assert d["direction"] in (toward_other, pm.DIR_NEUTRAL)
        assert d["baseline"]["rule_version"] == official_version
        assert d["reason"]
    print(f"[shadow v{official_version}->v{other_version}] confirmed: {d_conf['delta_kind']}/"
          f"{d_conf['direction']} {d_conf['baseline']['bucket']}->{d_conf['candidate']['bucket']}; "
          f"clean screen: {d_clean['delta_kind']}/{d_clean['direction']} "
          f"{d_clean['baseline']['bucket']}->{d_clean['candidate']['bucket']}")


def test_shadow_routes_floors(db_required, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from support_delivery_api import headers_for, run

    async def seed():
        from app.core.database import async_session_maker
        intake_id, entity_ids, _ = await _seed(monkeypatch, n=1)
        await _plant_latest_record(entity_ids[0], rule_version=2, classifier_input=SIGNAL_B1,
                                   bucket="B1", rule="RULE-001")
        async with async_session_maker() as db:
            candidate = await _v2_plus_what_if(db)
        return intake_id, candidate
    intake_id, candidate = run(seed())
    client = TestClient(app)
    body = {"intake_id": str(intake_id), "baseline_rule_version": 2, "candidate_rules": candidate}

    assert client.post("/api/tefca/rce/shadow/comparisons", json=body,
                       headers=headers_for("viewer")).status_code == 403
    r = client.post("/api/tefca/rce/shadow/comparisons", json=body, headers=headers_for("reviewer"))
    assert r.status_code == 201, r.text
    cid, h = r.json()["comparison_id"], r.json()["package_hash"]

    # Reads are viewer: buckets, rules and hashes only.
    assert client.get(f"/api/tefca/rce/shadow/comparisons/{cid}",
                      headers=headers_for("viewer")).status_code == 200
    r = client.get(f"/api/tefca/rce/shadow/comparisons/{cid}/deltas?kind=NEW",
                   headers=headers_for("viewer"))
    assert r.status_code == 200 and r.json()["total"] == 1
    assert client.get(f"/api/tefca/rce/shadow/comparisons/{cid}/deltas?kind=BOGUS",
                      headers=headers_for("viewer")).status_code == 422

    # Both candidate forms at once: 409 with the reason.
    bad = dict(body, candidate_rule_version=3)
    assert client.post("/api/tefca/rce/shadow/comparisons", json=bad,
                       headers=headers_for("reviewer")).status_code == 409

    # INDEPENDENT_QA needs qalead even though the route floor is reviewer.
    approval = {"approval_role": "INDEPENDENT_QA", "package_hash": h, "rationale": "x"}
    assert client.post(f"/api/tefca/rce/shadow/comparisons/{cid}/approvals", json=approval,
                       headers=headers_for("reviewer")).status_code == 403
    approval["approval_role"] = "ANALYST"
    r = client.post(f"/api/tefca/rce/shadow/comparisons/{cid}/approvals", json=approval,
                    headers=headers_for("reviewer"))
    assert r.status_code == 201, r.text
    assert client.post(f"/api/tefca/rce/shadow/comparisons/{cid}/approvals", json=approval,
                       headers=headers_for("qalead")).status_code == 409   # duplicate ANALYST
    approval["approval_role"] = "INDEPENDENT_QA"
    assert client.post(f"/api/tefca/rce/shadow/comparisons/{cid}/approvals", json=approval,
                       headers=headers_for("qalead")).status_code == 201

    # Publication: qalead floor; refused outside local mode with a 409 and reason.
    monkeypatch.delenv("SHADOW_PUBLICATION_MODE", raising=False)
    assert client.post(f"/api/tefca/rce/shadow/comparisons/{cid}/publish",
                       headers=headers_for("reviewer")).status_code == 403
    r = client.post(f"/api/tefca/rce/shadow/comparisons/{cid}/publish", headers=headers_for("qalead"))
    assert r.status_code == 409 and "outside this code's authorization" in r.text
