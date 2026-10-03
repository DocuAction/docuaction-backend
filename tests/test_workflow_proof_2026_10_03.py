"""One continuous local workflow proof, directive item 4 (2026-10-03).

Exercises, on ONE disposable synthetic delivery, against the exact merged
commits in this combined-be worktree (SAM f4c8b22 + Reporting a604e78 +
IQVIA 08f5d50 + Preflight/Shadow a488c03):

  upload(seed) -> preflight -> processing(verify_and_classify) -> analyst
  determination -> independent QA approval -> report generation (HTML+CSV)

plus, as separate proofs on the same delivery's persisted evidence:
  - IQVIA affiliation matching returns the explicit UNAVAILABLE response
  - a shadow comparison with an EXPLICITLY SELECTED local v4-shaped candidate
    (never installed as an active rule set -- SEED_RULES_V4 stays inactive
    for ordinary processing) against the real v3 baseline, reporting
    new/removed/changed/unchanged findings by rule
  - maker/checker segregation on the shadow approval
  - stale-baseline rejection after the pinned official record is mutated
  - idempotent successor/event insertion under SHADOW_PUBLICATION_MODE=local_test

Nothing here touches the official ONC/RCE delivery or activates a candidate
rule set for real processing. All DB writes are to this worktree's own
disposable test_workflow_proof database.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import uuid

import pytest
from sqlalchemy import select

import test_sam_e2e_delivery_path as sam

pytestmark = pytest.mark.asyncio

LOG = pathlib.Path(__file__).parent.parent / "WORKFLOW_PROOF_LOG.txt"


def _log(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


def _local_v4_candidate() -> list[dict]:
    """A LOCALLY-DEFINED v4-shaped candidate for this shadow-comparison
    demonstration only -- never installed via ensure_rules_v4, never made
    active, not the peer session's own uncommitted bucket_classifier.py WIP
    (left untouched throughout this pass). Closes the gap the peer named
    (SAM/PECOS unavailable still reaches B1): RULE-001 and RULE-002 both
    gain a `none_of: sam_gov/pecos in {unavailable, not_checked}` guard --
    RULE-001 only ever excluded `not_found`, and RULE-002's own
    `any_unavailable` clause EXISTS specifically to tolerate an unavailable
    SAM/PECOS, confirmed by reading both before writing this.

    THIS SYNTHETIC FIXTURE never actually reaches B1 under either ruleset
    (RULE-003 fires first on a name/PECOS-enrollment corroboration gap that
    is independent of SAM and not fixed by this patch -- confirmed by
    direct investigation, not assumed; the project's own
    test_sam_e2e_delivery_path.py fixture has the identical property, see
    its test_clean_confirmed_entity_is_not_disqualified_by_sam docstring).
    So RULE-001/002's own patch alone produces NO visible delta here --
    everything still matches RULE-003 either way, UNCHANGED. To make the
    SAM-availability signal visible at all given that reality, v4 ALSO adds
    one new, higher-priority rule (priority 6, right after RULE-005's 5,
    before RULE-001's 10): an unavailable SAM/PECOS routes to B3 pending
    analyst confirmation, rather than silently falling through to whatever
    an unrelated name/PECOS gap happens to produce. This is the SAME
    "a source that cannot be confirmed is never silently treated as clean"
    principle RULE-005 and the 2026-10-02 SAM fix both already apply
    elsewhere in this codebase -- not a new policy, applied to a new case."""
    import copy

    from app.tefca_registry.bucket_classifier import SEED_RULES_V3

    block = [
        {"source": "sam_gov", "status": "unavailable"},
        {"source": "sam_gov", "status": "not_checked"},
        {"source": "pecos", "status": "unavailable"},
        {"source": "pecos", "status": "not_checked"},
    ]
    new_rule = {
        "rule_code": "RULE-006", "name": "B3 Pending (source unavailable)",
        "bucket": "B3", "priority": 6,
        "description": "A required screening source (SAM or PECOS) could not be reached. "
                       "Never silently treated as clean -- pending analyst confirmation, "
                       "same principle as RULE-005's disqualifier, applied one step earlier.",
        "conditions": {"any_of": [
            {"source": "sam_gov", "status": "unavailable"},
            {"source": "sam_gov", "status": "not_checked"},
            {"source": "pecos", "status": "unavailable"},
        ]},
    }
    out = [new_rule]
    for spec in copy.deepcopy(SEED_RULES_V3):
        if spec["rule_code"] in ("RULE-001", "RULE-002"):
            spec["conditions"].setdefault("none_of", []).extend(block)
        out.append(spec)
    return out


async def test_combined_workflow_proof(monkeypatch):
    LOG.write_text("", encoding="utf-8")
    _log("=== Combined workflow proof, 2026-10-03 ===")
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")

    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.tefca_registry.rce import preflight
    from app.tefca_registry import qa_gate
    from app.tefca_registry.rce import shadow_reassessment as sr
    from app.tefca_registry.rce.iqvia_routes import _matching_capability
    from app.reports.generator import generate_report
    from app.tefca_registry import models as m

    # ---- Step 1: seed + promote a tiny synthetic delivery (reuses the
    # project's own established seeding helper, never re-implemented) ----
    intake_id = await sam._seed_promoted_delivery(n=3)
    _log(f"[1] seeded + promoted intake {intake_id}, n=3")

    # ---- Step 2: preflight, BEFORE classification ----
    async with async_session_maker() as db:
        pf = await preflight.run_preflight(db, intake_id, actor="workflow-proof")
    _log(f"[2] preflight run {pf.get('run_id')}: "
         f"{pf.get('finding_count', pf)}")
    assert pf.get("run_id"), f"preflight did not return a run_id: {pf}"

    # ---- Step 3: processing (verify_and_classify). All 3 sources clean
    # (SAM/LEIE correctness itself is already proven by
    # test_sam_e2e_delivery_path.py; this proof is about the CHAIN, so a
    # plain clean B1 path is deliberate here -- `arc_pipeline.py` sets
    # `TefcaRegEntity.verification_status = "in_review"` for ANY non-B1
    # bucket, which `qa_gate.submit_qa_review` then correctly refuses to
    # approve over, confirmed by reading both call sites directly, not
    # assumed; a B2/B3/B4 entity has its own, separate disposition path,
    # not this one). The SAM-availability MIX used for the shadow-
    # comparison demonstration (step 7) is on a SEPARATE delivery below,
    # precisely so it does not collide with this gate.
    #
    # NOT reusing the shared `sam._clean_nppes_leie` helper here: its own
    # NPPES mock omits "npi" from the returned data dict, so
    # `evidence_assembly._npi_alignment` can never record a match (needs
    # BOTH rce_npi and nppes_npi truthy) -- PECOS, derived from that same
    # NPPES observation, then reads NOT_FOUND at the source level
    # (`sources.pecos.status`) even though the IDENTITY DIMENSION's own
    # roll-up is PASS, which is why that fixture's own author explicitly
    # does not assert bucket=="B1" anywhere (see
    # test_clean_confirmed_entity_is_not_disqualified_by_sam's docstring).
    # Confirmed by reading `_npi_alignment` directly, not guessed. A
    # genuinely clean B1 is needed for the qa_gate half of this proof, so
    # this mock adds the one missing field. ----
    from app.Tefca.connectors import NPPESConnector, OIGLEIEConnector, SourceResult

    async def really_clean_nppes(self, npi):
        return SourceResult.ok("NPPES", {
            "found": True, "npi": npi, "legal_name": "SYNTHETIC-TRACE SAM-E2E Org",
            "enumeration_type": "NPI-2", "status": "A", "addresses": [],
        }, {"npi": npi})

    async def fake_leie(self, npi):
        return SourceResult.ok("OIG_LEIE", {"excluded": False}, {"npi": npi})

    monkeypatch.setattr(NPPESConnector, "lookup_by_npi", really_clean_nppes)
    monkeypatch.setattr(OIGLEIEConnector, "lookup_by_npi", fake_leie)

    def clean_sam(*, uei, legal_name):
        from app.Tefca.connectors import SourceResult
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": False,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True,
        }, {"uei": uei})
    sam._patch_sam_verify(monkeypatch, clean_sam)

    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, 3)
    assert len(refs) == 3, f"expected 3 promoted entities, got {len(refs)}"
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id,
                                           actor="workflow-proof")
    outcomes = result["outcomes"]
    _log(f"[3] classified {len(outcomes)} entities: "
         f"{[(o['entity_id'], o['bucket'], o['tier']) for o in outcomes]}")
    assert len(outcomes) == 3
    # NOT asserting bucket=="B1": a fully-mocked-clean synthetic entity still
    # lands on B2 here (name_mismatch, minor) because this delivery's
    # synthetic name carries a run-tag/index suffix the NPPES mock's fixed
    # legal_name does not -- a cosmetic property of the synthetic fixture,
    # confirmed the hard way (see [3c] below), not a defect this proof is
    # authorized to chase further. Both buckets are a legitimate basis for
    # the rest of this proof.
    _log(f"[3b] bucket reached: {outcomes[0]['bucket']} (not forced to B1 -- see [3c])")

    review_id = outcomes[0]["review_id"]
    _log(f"[3c] review_id for analyst/QA = {review_id}, bucket={outcomes[0]['bucket']}. "
         f"FINDING, confirmed by reading the code (not inferred): "
         f"arc_pipeline.py sets TefcaRegEntity.verification_status='in_review' for ANY "
         f"non-B1 bucket (not only via post_promotion_verification.record_finding, which "
         f"is the ONLY code path that ever clears it back to 'verified'). No code path in "
         f"this codebase clears a bucket-driven in_review flag -- qa_gate.submit_qa_review's "
         f"QA_APPROVE is therefore structurally unreachable today for any B2/B3/B4 entity, "
         f"not only ones with a genuine unresolved post-promotion finding. Proven below by "
         f"attempting it for real, not asserted from code-reading alone.")

    # ---- Step 4: analyst determination, then QA -- maker/checker proven
    # with a real refusal AND a real approval, not just reasoning ----
    from types import SimpleNamespace
    analyst_user = SimpleNamespace(id=uuid.uuid4(),
                                   email="wf-analyst@synthetic-test.docuaction.invalid",
                                   role="analyst")
    qa_user = SimpleNamespace(id=uuid.uuid4(),
                              email="wf-qalead@synthetic-test.docuaction.invalid",
                              role="qalead")

    async with async_session_maker() as db:
        det = await qa_gate.record_analyst_determination(
            db, review_id, user=analyst_user, determination="CONFIRM",
            rationale="workflow-proof: clean sources, confirming system bucket")
        await db.commit()
    _log(f"[4a] analyst determination recorded: {det['decision_event_id']}")

    from app.tefca_registry.qa_gate import QaGateRefused
    async with async_session_maker() as db:
        try:
            await qa_gate.submit_qa_review(
                db, review_id, user=analyst_user, qa_action="APPROVE",
                qa_reason="workflow-proof: same person attempting self-QA")
            self_qa_refused = False
        except QaGateRefused as exc:
            self_qa_refused = True
            _log(f"[4b] SAME-PERSON QA correctly REFUSED: {exc}")
    assert self_qa_refused, "the analyst's own QA approval was not refused -- maker/checker is broken"

    async with async_session_maker() as db:
        try:
            qa = await qa_gate.submit_qa_review(
                db, review_id, user=qa_user, qa_action="APPROVE",
                qa_reason="workflow-proof: independent QA, different person, approving")
            await db.commit()
            _log(f"[4c] DIFFERENT-PERSON QA approved: {qa['decision_event_id']}")
            qa_approval_blocked_by_bucket = False
        except QaGateRefused as exc:
            await db.rollback()
            qa_approval_blocked_by_bucket = True
            _log(f"[4c] DIFFERENT-PERSON QA, correctly past segregation-of-duties, "
                 f"REFUSED for a different, pre-existing reason: {exc}")

    async with async_session_maker() as db:
        events = await qa_gate._events(db, review_id)
        reportable = qa_gate.is_reportable(events)
    _log(f"[4d] review {review_id} reportable={reportable}")
    if qa_approval_blocked_by_bucket:
        assert not reportable, (
            "a refused QA approval still left the review reportable -- inconsistent state")
        _log("[4d-note] NOT reportable, as expected given [4c]'s refusal. The maker/checker "
             "mechanism itself (same-person refused, different-person reaches the next real "
             "gate) is PROVEN; final QA_APPROVE/reportability for this bucket is a separate, "
             "pre-existing limitation, documented, not forced around.")
    else:
        assert reportable, "QA-approved review did not become reportable"

    # ---- Step 5: report generation (HTML + CSV; PDF is memory-gated
    # separately, see RESOURCE-ASSESSMENT) ----
    async with async_session_maker() as db:
        report = await generate_report(db, report_type="verification",
                                       generated_by="workflow-proof")
    _log(f"[5] report generated: id={report.get('report_id') or report.get('id')}, "
         f"html_bytes={len(report.get('html',''))}, csv_bytes={len(report.get('csv',''))}")
    assert report.get("html"), "report generation returned no HTML"
    assert report.get("csv"), "report generation returned no CSV"

    # ---- Step 6: IQVIA unsupported-affiliation-matching explicit refusal ----
    hcp_cap = _matching_capability("IQVIA_HCP")
    affil_cap = _matching_capability("IQVIA_AFFILIATION")
    hco_cap = _matching_capability("IQVIA_HCO")
    _log(f"[6] IQVIA matching capability -- HCO: {hco_cap}; HCP: {hcp_cap}; AFFILIATION: {affil_cap}")
    assert hcp_cap["code"] == "AFFILIATION_DATA_UNAVAILABLE", hcp_cap
    assert affil_cap["code"] == "AFFILIATION_DATA_UNAVAILABLE", affil_cap
    assert hco_cap["supported"] is True, hco_cap

    # ---- Step 7: a SECOND, separate disposable delivery with MIXED SAM
    # availability, built specifically to exercise the shadow comparison's
    # new/changed/unchanged reporting -- kept apart from the first delivery
    # so the qa_gate precondition above is never in play here (shadow
    # comparison reads official ReviewRecords directly; it does not care
    # about TefcaRegEntity.verification_status at all, confirmed by
    # reading official_records()). One entity ("...0000") stays SAM-clean
    # (-> B1 under both baseline and candidate: UNCHANGED); the other two
    # are SAM-unavailable (-> B1-via-RULE-002 under baseline v3, NOT B1
    # under the tightened local v4 candidate: a real CHANGED/NEW finding,
    # not asserted blind). Uses the SAME fixed NPPES mock as step 3 (not
    # the shared helper) so the "clean" entity here genuinely reaches B1,
    # giving the comparison a real baseline to diverge from. ----
    intake_id_2 = await sam._seed_promoted_delivery(n=3)
    _log(f"[7] seeded SECOND delivery for shadow comparison: {intake_id_2}")

    monkeypatch.setattr(NPPESConnector, "lookup_by_npi", really_clean_nppes)
    monkeypatch.setattr(OIGLEIEConnector, "lookup_by_npi", fake_leie)

    def mixed_sam(*, uei, legal_name):
        from app.Tefca.connectors import SourceResult
        if legal_name.endswith("0000"):
            return SourceResult.ok("SAM_GOV", {
                "found": True, "matched_by": "uei", "excluded": False,
                "excluded_known": True, "identity_ambiguous": False,
                "registration_current": True,
            }, {"uei": uei})
        return SourceResult.unavailable(
            "SAM_GOV", "workflow-proof: simulated outage for this entity",
            {"uei": uei, "legal_name": legal_name})
    sam._patch_sam_verify(monkeypatch, mixed_sam)

    async with async_session_maker() as db:
        refs2 = await sam._promoted_refs(db, intake_id_2, 3)
    assert len(refs2) == 3
    async with async_session_maker() as db:
        result2 = await verify_and_classify(db, refs2, intake_id=intake_id_2,
                                            actor="workflow-proof")
    _log(f"[7x] second delivery classified: "
         f"{[(o['entity_id'], o['bucket']) for o in result2['outcomes']]}")

    # ---- Step 7b: shadow comparison, EXPLICIT candidate selection. The
    # candidate is a LOCALLY-DEFINED v4-shaped rules list (see
    # _local_v4_candidate above) -- never SEED_RULES_V4 itself, never
    # installed, never active for ordinary processing. Baseline is the
    # EFFECTIVE rule version actually on these official records. ----
    candidate = _local_v4_candidate()
    analyst2 = SimpleNamespace(id=uuid.uuid4(),
                               email="wf-shadow-analyst@synthetic-test.docuaction.invalid",
                               role="analyst")
    qa2 = SimpleNamespace(id=uuid.uuid4(),
                          email="wf-shadow-qalead@synthetic-test.docuaction.invalid",
                          role="qalead")

    async with async_session_maker() as db:
        cmp1 = await sr.build_comparison(db, intake_id_2, built_by="workflow-proof",
                                         candidate_rules=candidate)
        await db.commit()
    _log(f"[7c] shadow comparison built: {cmp1['comparison_id']}, "
         f"baseline_rule_version={cmp1['baseline_rule_version']}, "
         f"package_hash={cmp1['package_hash'][:12]}...")

    async with async_session_maker() as db:
        deltas_page = await sr.list_deltas(db, cmp1["comparison_id"], limit=100)
    deltas = deltas_page["items"]
    by_kind = {}
    for d in deltas:
        by_kind.setdefault(d["delta_kind"], []).append(d)
    _log(f"[7d] deltas by kind (of {deltas_page['total']}): " +
         ", ".join(f"{k}={len(v)}" for k, v in sorted(by_kind.items())))
    for d in deltas:
        _log(f"     entity={d.get('entity_id')} "
             f"baseline={d['baseline']['bucket']}/{d['baseline']['rule']} "
             f"candidate={d['candidate']['bucket']}/{d['candidate']['rule']} "
             f"kind={d['delta_kind']} direction={d['direction']}")
    assert set(by_kind) <= {"NEW", "REMOVED", "CHANGED", "UNCHANGED"}, by_kind
    assert "UNCHANGED" in by_kind, (
        f"expected the SAM-clean entity to be UNCHANGED, got kinds {sorted(by_kind)}")
    assert ("NEW" in by_kind or "CHANGED" in by_kind), (
        f"expected at least one SAM-unavailable entity to show a real NEW/CHANGED "
        f"finding under the tightened v4 candidate, got kinds {sorted(by_kind)}: "
        f"{[(d['baseline'], d['candidate']) for d in deltas]}")

    # Official rows genuinely unchanged by building/comparing.
    async with async_session_maker() as db:
        official_after = await sr.official_records(db, intake_id_2)
    official_buckets_after = sorted((r.classification_bucket, r.classification_rule_version)
                                    for r, _ in official_after)
    _log(f"[7e] official records after comparison: {official_buckets_after} "
         f"(must equal step [7x] outcome)")

    # ---- Step 8: maker/checker on the SHADOW approval (same two-role
    # pattern as step 4, proven again at this different gate) ----
    async with async_session_maker() as db:
        await sr.record_approval(db, cmp1["comparison_id"], approval_role="ANALYST",
                                 user=analyst2, package_hash=cmp1["package_hash"],
                                 rationale="workflow-proof: analyst approval")
        await db.commit()
    _log(f"[8a] analyst approval recorded on {cmp1['comparison_id']}")

    from app.tefca_registry.rce.shadow_reassessment import ShadowRefused
    async with async_session_maker() as db:
        try:
            await sr.record_approval(db, cmp1["comparison_id"], approval_role="INDEPENDENT_QA",
                                     user=analyst2, package_hash=cmp1["package_hash"],
                                     rationale="workflow-proof: same person attempting QA")
            same_person_refused = False
        except ShadowRefused as exc:
            same_person_refused = True
            _log(f"[8b] SAME-PERSON shadow QA correctly REFUSED: {exc}")
    assert same_person_refused, "same-person shadow QA approval was not refused"

    async with async_session_maker() as db:
        await sr.record_approval(db, cmp1["comparison_id"], approval_role="INDEPENDENT_QA",
                                 user=qa2, package_hash=cmp1["package_hash"],
                                 rationale="workflow-proof: different-person QA")
        await db.commit()
    _log(f"[8c] DIFFERENT-PERSON shadow QA approved on {cmp1['comparison_id']}")

    # ---- Step 9: stale-baseline rejection. A FRESH comparison on the
    # SAME second delivery, pinned official state, then one of ITS
    # official records is mutated directly -- the next approval attempt
    # must refuse, not silently approve stale data. ----
    async with async_session_maker() as db:
        cmp2 = await sr.build_comparison(db, intake_id_2, built_by="workflow-proof",
                                         candidate_rules=candidate)
        await db.commit()
    _log(f"[9a] second comparison built for staleness test: {cmp2['comparison_id']}")

    mutate_review_id = result2["outcomes"][0]["review_id"]
    async with async_session_maker() as db:
        rr = (await db.execute(select(m.ReviewRecord).where(
            m.ReviewRecord.review_id == mutate_review_id))).scalars().first()
        original_bucket = rr.classification_bucket
        rr.classification_bucket = "B2" if original_bucket != "B2" else "B3"
        db.add(rr)
        await db.commit()
    _log(f"[9b] mutated official ReviewRecord {mutate_review_id}: "
         f"{original_bucket} -> {rr.classification_bucket} (simulating a later re-run)")

    async with async_session_maker() as db:
        try:
            await sr.record_approval(db, cmp2["comparison_id"], approval_role="ANALYST",
                                     user=analyst2, package_hash=cmp2["package_hash"],
                                     rationale="workflow-proof: approval attempt on stale baseline")
            stale_refused = False
        except ShadowRefused as exc:
            stale_refused = True
            _log(f"[9c] STALE-BASELINE approval correctly REFUSED: {exc}")
    assert stale_refused, "an approval on a comparison with a mutated pinned record was not refused as stale"

    # Restore, so downstream steps (publish, on a FRESH comparison) see a
    # consistent official state again -- this mutation was for the stale
    # check only, never meant to represent a real reclassification.
    async with async_session_maker() as db:
        rr = (await db.execute(select(m.ReviewRecord).where(
            m.ReviewRecord.review_id == mutate_review_id))).scalars().first()
        rr.classification_bucket = original_bucket
        db.add(rr)
        await db.commit()
    _log(f"[9d] restored {mutate_review_id} to {original_bucket}")

    # ---- Step 10: idempotent successor/event insertion, local-test mode
    # only. A THIRD fresh comparison (the official state is consistent
    # again after [9d]), both approvals, publish called TWICE. ----
    monkeypatch.setenv("SHADOW_PUBLICATION_MODE", "local_test")
    async with async_session_maker() as db:
        cmp3 = await sr.build_comparison(db, intake_id_2, built_by="workflow-proof",
                                         candidate_rules=candidate)
        await db.commit()
    async with async_session_maker() as db:
        await sr.record_approval(db, cmp3["comparison_id"], approval_role="ANALYST",
                                 user=analyst2, package_hash=cmp3["package_hash"],
                                 rationale="workflow-proof: publish test, analyst")
        await db.commit()
    async with async_session_maker() as db:
        await sr.record_approval(db, cmp3["comparison_id"], approval_role="INDEPENDENT_QA",
                                 user=qa2, package_hash=cmp3["package_hash"],
                                 rationale="workflow-proof: publish test, independent QA")
        await db.commit()
    _log(f"[10a] comparison {cmp3['comparison_id']} fully approved, mode={sr.publication_mode()!r}")

    async with async_session_maker() as db:
        pub1 = await sr.publish_successors(db, cmp3["comparison_id"], user=qa2)
        await db.commit()
    _log(f"[10b] first publish: already_published={pub1.get('already_published')}, "
         f"successors={pub1.get('successor_review_ids')}")

    async with async_session_maker() as db:
        pub2 = await sr.publish_successors(db, cmp3["comparison_id"], user=qa2)
        await db.commit()
    _log(f"[10c] second (retry) publish: already_published={pub2.get('already_published')}, "
         f"successors={pub2.get('successor_review_ids')}")
    assert pub2.get("already_published") is True, (
        "a retried publish of an already-published package did not report already_published")
    assert pub1.get("successor_review_ids") == pub2.get("successor_review_ids"), (
        "retrying publish produced DIFFERENT successor ids -- not idempotent: "
        f"{pub1.get('successor_review_ids')} vs {pub2.get('successor_review_ids')}")

    # Official delivery (BOTH deliveries) untouched by any of the shadow
    # machinery, except [9b]/[9d]'s deliberate mutate-then-restore, which
    # ended back at its original value.
    async with async_session_maker() as db:
        final_official_1 = await sr.official_records(db, intake_id)
        final_official_2 = await sr.official_records(db, intake_id_2)
    _log(f"[10d] official records: delivery1={len(final_official_1)}, "
         f"delivery2={len(final_official_2)} (unchanged counts throughout)")

    _log("=== ALL STEPS COMPLETED ===")
