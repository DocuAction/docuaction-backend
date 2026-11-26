"""Mandatory SAM handling, on controlled synthetic cases (2026-11-26). CHARACTERIZATION: these tests PIN what the
code does; they do not assert what it ought to do.

Owner: "Test mandatory SAM handling with controlled synthetic cases: API unavailable/extract accepted clear, API
unavailable/extract unavailable, and a pending exclusion candidate. Report the applicable policy, resulting bucket
and reason for each; do not assume every case must produce B3."

THE CONFIGURATION is the one active on DEV, built with the repository's own code (see `configuration`):
  * rules: the v1 -> v2 -> v3 lineage is seeded by `ensure_seed_rules` / `ensure_rules_v2` / `ensure_rules_v3`, then
    `rule_configuration.activate_rule_configuration` activates the four components
    exclusion_candidate_b3 + mandatory_source_guards + structural_npi_handling + treatment_b4_onc_scope as
    version 4, effective 2026-11-10, against a synthetic `methodology_approval_recorded` governance row;
  * ONE active `rce_source_policies` row: sam_gov, mandatory, version 1, applicability {} (no repository code
    writes that row - `source_policy.PROPOSED_POLICY_M1` is "seeded NOWHERE" - so the test inserts it from that
    constant);
  * flags RCE_PROPOSED_EXCLUSION_STATES=1, RCE_PROPOSED_NPI_INVALID_SPLIT=1, RCE_PROPOSED_VERIFY_SKIP_HELD=1,
    ENABLE_SAM_EXTRACT_SCREENING=true.

THE RECORD is otherwise clean: active, non-Treatment (T-PYMNT), a valid NPI, NPPES verified and matching, CMS
enrollment found, no CMS revocation, LEIE not excluded. SAM is the only variable.

THE SOURCES are the real connectors, the real evidence assembly and the real writer. Only the data underneath the
connectors is synthetic: the HTTP helper (NPPES, SAM API), the CMS data-API client, and the loaded LEIE / SAM
extract lists. Nothing reaches a network; the file runs with outbound connections refused.
"""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import delete, select

from app.Tefca import cms_ppef, connectors, sam_extract
from app.tefca_registry import models as reg
from app.tefca_registry import rule_configuration as rcfg
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import source_policy as sp
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401 - `rolled_back_db` is a fixture
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

pytestmark = pytest.mark.asyncio

ARC = "9.99.781.1"
NPIS = ["1234567893", "1245319599"]                 # Luhn-valid synthetic NPIs
COMPONENTS = ("exclusion_candidate_b3", "mandatory_source_guards", "structural_npi_handling",
              "treatment_b4_onc_scope")
EFFECTIVE = date(2026, 11, 10)


class _World:
    """The synthetic answers underneath the real connectors. Every attribute is one fact a test may change."""

    def __init__(self):
        self.sam_api = "not_routing"        # not_routing | clear
        self.extract = "clear"              # clear | unavailable | candidate
        self.enrolled = True                # CMS PPEF enrollment found for the NPI
        self.nppes_name = {}                # npi -> the name NPPES holds (default: the delivered name)
        self.names = {}                     # npi -> delivered name
        self.calls = []                     # (source, what) - every question asked

    def install(self, monkeypatch):
        world = self

        async def _get(url, params=None, headers=None, timeout=None, source="UNSPECIFIED"):
            world.calls.append((source, dict(params or {})))
            request = httpx.Request("GET", url)
            target = urlsplit(url)                     # the exact host and path - never a substring of the URL
            if target.hostname == "npiregistry.cms.hhs.gov":
                npi = str(params["number"])
                name = world.nppes_name.get(npi, world.names.get(npi, ""))
                return httpx.Response(200, request=request, json={"result_count": 1, "results": [{
                    "number": npi, "enumeration_type": "NPI-2",
                    "basic": {"organization_name": name, "status": "A", "enumeration_date": "2010-01-01"},
                    "addresses": [{"address_purpose": "LOCATION", "address_1": "1 Synthetic Way",
                                   "city": "Testville", "state": "MA", "postal_code": "02101",
                                   "country_code": "US"}],
                    "taxonomies": [{"code": "261QM1300X", "desc": "Clinic/Center", "primary": True}]}]})
            if target.hostname == "api.sam.gov":
                if world.sam_api == "not_routing":
                    return httpx.Response(404, request=request, content=b"")      # what api.sam.gov serves
                if target.path == "/entity-information/v4/exclusions":
                    return httpx.Response(200, request=request, json={"totalRecords": 0, "excludedEntity": []})
                name = params.get("legalBusinessName", "")
                return httpx.Response(200, request=request, json={"totalRecords": 1, "entityData": [{
                    "entityRegistration": {"ueiSAM": "SYNTHETICUEI1", "legalBusinessName": name,
                                           "registrationStatus": "Active",
                                           "registrationExpirationDate": "2027-06-30",
                                           "exclusionStatusFlag": "N"}}]})
            raise AssertionError(f"unexpected outbound request to {url}")

        async def _cms(self_, dataset_id, component, filters, max_records=None):
            world.calls.append(("CMS", (dataset_id, dict(filters))))
            query = cms_ppef.CMSQuery(dataset_id=dataset_id, component=component, filters=dict(filters))
            npi = str(filters.get(cms_ppef.F_NPI, ""))
            rows = []
            if dataset_id == cms_ppef.PPEF_ENROLLMENT_DATASET_ID and world.enrolled:
                rows = [{cms_ppef.F_NPI: npi, cms_ppef.F_ENRLMT_ID: f"O2026{npi}",
                         cms_ppef.F_PAC_ID: f"PAC{npi}", cms_ppef.F_MULTIPLE_NPI_FLAG: "N",
                         cms_ppef.F_PROVIDER_TYPE_CD: "12-70", cms_ppef.F_PROVIDER_TYPE_DESC: "PART B SUPPLIER",
                         cms_ppef.F_STATE_CD: "MA", cms_ppef.F_ORG_NAME: world.names.get(npi, "")}]
            query.row_count = len(rows)
            return rows, query

        async def _leie_loaded():
            return True

        async def _extract_loaded():
            return world.extract != "unavailable"

        monkeypatch.setenv("SAM_GOV_API_KEY", "synthetic-key-not-a-credential")
        monkeypatch.setattr(connectors, "_get_with_retry", _get)
        monkeypatch.setattr(cms_ppef.CMSDataAPIClient, "fetch_all", _cms)
        monkeypatch.setattr(connectors, "_ensure_leie_loaded", _leie_loaded)
        monkeypatch.setattr(sam_extract, "_load_sam_extract", _extract_loaded)
        now = time.time()
        for key, value in (("loaded_at", now), ("row_count", 84_001), ("by_npi", {}), ("by_name", {})):
            monkeypatch.setitem(connectors._LEIE_CACHE, key, value)
        for key, value in (("loaded_at", now), ("row_count", 160_000), ("by_npi", {}), ("by_name", {}),
                           ("filename", "SAM_Exclusions_Public_Extract_V2_SYNTHETIC.ZIP"),
                           ("publication_date", datetime.now(timezone.utc).date()),
                           ("date_modified", None), ("http_last_modified", None), ("sha256", "0" * 64),
                           ("source_url", "synthetic://sam-extract")):
            monkeypatch.setitem(sam_extract._SAM_CACHE, key, value)
        return self

    def set_extract_candidate(self, npi):
        """One exclusion row in the loaded extract carrying this NPI: a pending, unadjudicated candidate."""
        sam_extract._SAM_CACHE["by_npi"] = {npi: [{
            "Classification": "Firm", "Name": f"{SYN} EXCLUDED FIRM", "NPI": npi, "Exclusion Type": "Ineligible",
            "Excluding Agency": "HHS", "Active Date": "01/02/2020", "Termination Date": "Indefinite",
            "Record Status": "Active", "SAM Number": "S4MSYNTH1"}]}

    def set_leie_hit(self, npi):
        """One active exclusion row in the loaded LEIE list carrying this NPI."""
        connectors._LEIE_CACHE["by_npi"] = {npi: [{
            "lastname": "", "firstname": "", "busname": f"{SYN} EXCLUDED", "exclusion_type": "1128a1",
            "exclusion_date": "20200102", "reinstatement_date": "00000000", "state": "MA", "npi": npi}]}


@pytest.fixture
async def configuration(rolled_back_db, monkeypatch):
    """The DEV-active configuration, built by the repository's own code inside the rolled-back transaction."""
    from app.tefca_registry.bucket_classifier import ensure_rules_v2, ensure_rules_v3, ensure_seed_rules
    db = rolled_back_db
    for flag, value in (("RCE_PROPOSED_EXCLUSION_STATES", "1"), ("RCE_PROPOSED_NPI_INVALID_SPLIT", "1"),
                        ("RCE_PROPOSED_VERIFY_SKIP_HELD", "1"), ("ENABLE_SAM_EXTRACT_SCREENING", "true"),
                        ("ENTITY_RESOLVER_SOURCE", "db")):
        monkeypatch.setenv(flag, value)
    # Whatever rule and policy rows this database already holds are set aside (rolled back with the rest), so the
    # lineage below is exactly v1 -> v2 -> v3 -> the composed v4.
    await db.execute(delete(reg.ReviewRule))
    await db.execute(delete(m.RceSourcePolicy))
    await ensure_seed_rules(db)
    await ensure_rules_v2(db, effective=date(2026, 10, 1))
    await ensure_rules_v3(db, effective=date(2026, 10, 2))
    approval = reg.TefcaRegAuditLog(
        id=uuid.uuid4(), action=rcfg.APPROVAL_EVENT_TYPE, actor_email=f"owner@{SYN.lower()}.invalid",
        metadata_={"components": list(COMPONENTS), "synthetic_test_record": True})
    db.add(approval)
    await db.flush()
    activated = await rcfg.activate_rule_configuration(
        db, {name: str(approval.id) for name in COMPONENTS}, effective=EFFECTIVE, actor=SYN)
    proposed = sp.PROPOSED_POLICY_M1[0]
    policy = m.RceSourcePolicy(
        id=uuid.uuid4(), version=1, source=proposed["source"], role=proposed["role"],
        applicability=proposed["applicability"], policy_reference=proposed["policy_reference"],
        approval_reference=approval.id, is_active=True)
    db.add(policy)
    await db.flush()
    from app.tefca_registry import review_service
    # The single-record path keeps a process-wide rule cache: emptied here, and put back as it was afterwards.
    monkeypatch.setattr(review_service._classifier, "_rules", None)
    monkeypatch.setattr(review_service._classifier, "_loaded_at", review_service._classifier._loaded_at)
    return SimpleNamespace(db=db, activated=activated, policy_id=str(policy.id), approval_id=str(approval.id))


@pytest.fixture
async def delivery(configuration, monkeypatch):
    """One promoted synthetic delivery of two clean, active, non-Treatment organisations with valid NPIs."""
    db = configuration.db
    rows = make_rows(len(NPIS), arc=ARC)
    for row, npi in zip(rows, NPIS):
        row.update({"NPI": npi, "purposesofuse": "T-PYMNT", "active": "1"})
    intake_id, _ = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)

    from app.Tefca.entity_resolution import resolve_entity

    async def _resolve_all(refs):                    # the real resolver, on the test's own (uncommitted) session
        return {ref: await resolve_entity(db, ref) for ref in dict.fromkeys(refs)}
    monkeypatch.setattr(ap, "_resolve_all", _resolve_all)
    world = _World().install(monkeypatch)
    world.names = {npi: row["name"] for row, npi in zip(rows, NPIS)}
    return SimpleNamespace(db=db, intake_id=intake_id, refs=[r["id"] for r in rows], rows=rows, world=world,
                           configuration=configuration)


async def _bulk(d, ref):
    """Run one record through the real bulk writer and return the facts the report is made of."""
    result = await ap.verify_and_classify(d.db, [ref], intake_id=d.intake_id, actor=SYN)
    assert result["verified"] == 1, result
    review = (await d.db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == result["outcomes"][0]["review_id"]))).scalars().one()
    return _facts(review, result["outcomes"][0])


def _facts(review, outcome=None):
    results = review.verification_results or {}
    sources = (results.get("classifier_input") or {}).get("sources") or {}
    return {
        "sam_gov": (sources.get("sam_gov") or {}).get("status"),
        "sam_extract": (sources.get("sam_extract") or {}).get("status"),
        "oig_leie": (sources.get("oig_leie") or {}).get("status"),
        "nppes": (sources.get("nppes") or {}).get("status"),
        "pecos": (sources.get("pecos") or {}).get("status"),
        "bucket": review.classification_bucket, "rule": review.classification_rule,
        "rule_version": review.classification_rule_version, "rationale": review.classification_rationale,
        "tier": (outcome or {}).get("tier"), "results": results, "outcome": outcome,
        "fields": (results.get("classifier_input") or {}).get("fields") or {},
    }


B1_TEXT = "B1 No Discrepancy (RULE-001 v4): nppes is verified; oig_leie is clear; pecos is verified"


def _policy_text(reason, cfg):
    """The rationale `source_policy.enforce_on_classification` writes, for this test's own policy row."""
    proposed = sp.PROPOSED_POLICY_M1[0]
    return (f"Mandatory-source policy: {reason}. An applicable mandatory check without an acceptable answer cannot "
            f"classify B1/B2 (M-4). Policy row {cfg.policy_id} v1 - {proposed['policy_reference']}; approval "
            f"{cfg.approval_id}.")


#: WHAT THE CODE DOES (pinned 2026-11-26 on origin/main 96c4938), one row per controlled case.
#:   arrange: sam_api, extract, and the contrast switches (minor = NPPES holds a slightly different name;
#:            enrolled = CMS enrollment found; leie = a LEIE row carries the NPI; policy = the sam_gov row is active)
#:   states : classifier input sam_gov / sam_extract / oig_leie / pecos
#:   before : (bucket, rule) the rules give BEFORE the mandatory-source policy
#:   policy : the policy's reason, or None when it changes nothing
#:   final  : (bucket, rule code, rule version, tier) stored on the review
CASES = {
    "1_api_unavailable_extract_clear": dict(
        arrange=dict(sam_api="not_routing", extract="clear"),
        states=("unavailable", "clear", "verified", "verified"), before=("B1", "RULE-001"), policy=None,
        final=("B1", "RULE-001", 4, 1), fields={}, marked_verified=True),
    "2_api_unavailable_extract_unavailable": dict(
        arrange=dict(sam_api="not_routing", extract="unavailable"),
        states=("unavailable", "unavailable", "verified", "verified"), before=("B1", "RULE-001"),
        policy="SOURCE_NOT_COMPLETED_SAM_GOV",
        final=("B3", "SOURCE-POLICY-M4", 1, 3), fields={}, marked_verified=False),
    "3_api_unavailable_extract_candidate": dict(
        arrange=dict(sam_api="not_routing", extract="candidate"),
        states=("unavailable", "possible_match", "verified", "verified"), before=("B1", "RULE-001"),
        policy="EXCLUSION_POSSIBLE_MATCH_PENDING",
        final=("B3", "SOURCE-POLICY-M4", 1, 3), fields={}, marked_verified=False),
    "4_api_clear_extract_clear": dict(
        arrange=dict(sam_api="clear", extract="clear"),
        states=("clear", "clear", "verified", "verified"), before=("B1", "RULE-001"), policy=None,
        final=("B1", "RULE-001", 4, 1), fields={}, marked_verified=True),
    "5a_minor_name_mismatch_extract_clear": dict(
        arrange=dict(sam_api="not_routing", extract="clear", minor=True),
        states=("unavailable", "clear", "verified", "verified"), before=("B1", "RULE-001"), policy=None,
        final=("B1", "RULE-001", 4, 1), fields={"name_mismatch": {"severity": "minor"}}, marked_verified=True),
    "5b_minor_mismatch_no_enrollment_extract_clear": dict(
        arrange=dict(sam_api="not_routing", extract="clear", minor=True, enrolled=False),
        states=("unavailable", "clear", "verified", "not_found"), before=("B2", "RULE-003"), policy=None,
        final=("B2", "RULE-003", 4, 2), fields={"name_mismatch": {"severity": "minor"}}, marked_verified=False),
    "5c_minor_mismatch_no_enrollment_extract_unavailable": dict(
        arrange=dict(sam_api="not_routing", extract="unavailable", minor=True, enrolled=False),
        states=("unavailable", "unavailable", "verified", "not_found"), before=("B2", "RULE-003"),
        policy="SOURCE_NOT_COMPLETED_SAM_GOV",
        final=("B3", "SOURCE-POLICY-M4", 1, 3), fields={"name_mismatch": {"severity": "minor"}},
        marked_verified=False),
    "6_leie_possible_match_extract_clear": dict(
        arrange=dict(sam_api="not_routing", extract="clear", leie=True),
        states=("unavailable", "clear", "possible_match", "verified"), before=("B3", "RULE-008"), policy=None,
        final=("B3", "RULE-008", 4, 3), fields={}, marked_verified=False),
    "7_extract_candidate_with_no_active_policy_row": dict(
        arrange=dict(sam_api="not_routing", extract="candidate", policy=False),
        states=("unavailable", "possible_match", "verified", "verified"), before=("B1", "RULE-001"), policy=None,
        final=("B1", "RULE-001", 4, 1), fields={}, marked_verified=True),
}
RATIONALES = {
    "1_api_unavailable_extract_clear": B1_TEXT, "4_api_clear_extract_clear": B1_TEXT,
    "5a_minor_name_mismatch_extract_clear": B1_TEXT, "7_extract_candidate_with_no_active_policy_row": B1_TEXT,
    "5b_minor_mismatch_no_enrollment_extract_clear":
        "B2 Minor/Administrative (RULE-003 v4): name_mismatch severity minor",
    "6_leie_possible_match_extract_clear":
        "B3 Exclusion Possible Match Pending Adjudication (RULE-008 v4): oig_leie is possible_match",
}


async def _arrange(d, arrange):
    w, npi = d.world, NPIS[0]
    w.sam_api, w.extract, w.enrolled = arrange["sam_api"], arrange["extract"], arrange.get("enrolled", True)
    if arrange["extract"] == "candidate":
        w.set_extract_candidate(npi)
    if arrange.get("minor"):
        w.nppes_name[npi] = w.names[npi] + " HOLDINGS"
    if arrange.get("leie"):
        w.set_leie_hit(npi)
    if arrange.get("policy") is False:
        row = await d.db.get(m.RceSourcePolicy, uuid.UUID(d.configuration.policy_id))
        row.is_active = False
        await d.db.flush()


async def test_the_configuration_is_the_one_active_on_dev(delivery):
    d = delivery
    assert d.configuration.activated == {"version": 4, "rules": 7, "components": sorted(COMPONENTS)}
    active = (await d.db.execute(select(reg.ReviewRule).where(reg.ReviewRule.is_active.is_(True)))).scalars().all()
    assert sorted(r.rule_code for r in active) == [
        "RULE-001", "RULE-002", "RULE-003", "RULE-004", "RULE-005", "RULE-006", "RULE-008"]
    assert {r.version for r in active} == {4} and {r.effective_date for r in active} == {EFFECTIVE}
    by_code = {r.rule_code: r for r in active}
    assert by_code["RULE-006"].conditions == {"all_of": [{"field": "treatment_onc_scope_missing_npi", "status": True}]}
    assert by_code["RULE-008"].conditions == {"any_of": [{"source": "sam_gov", "status": "possible_match"},
                                                         {"source": "oig_leie", "status": "possible_match"}]}
    for rule in active:
        assert "[components: " + ", ".join(sorted(COMPONENTS)) + "]" in rule.description
    # RULE-008 names the two LIVE sources only: a candidate found in the dated extract (`sam_extract`) is not a
    # condition of any rule. Only the mandatory-source policy acts on it (cases 3 and 7).
    assert "sam_extract" not in str([r.conditions for r in active])
    policies = await sp.load_active_policies(d.db)
    assert policies == [{"id": d.configuration.policy_id, "version": 1, "source": "sam_gov", "role": "mandatory",
                         "applicability": {}, "policy_reference": sp.PROPOSED_POLICY_M1[0]["policy_reference"],
                         "approval_reference": d.configuration.approval_id}]
    rules = await ap._rule_set(d.db)                       # what the writer classifies with: the same seven, v4
    assert sorted((r["rule_code"], r["version"]) for r in rules) == sorted((r.rule_code, 4) for r in active)


@pytest.mark.parametrize("name", list(CASES))
async def test_bulk_writer(delivery, name):
    from app.tefca_registry.bucket_classifier import BucketClassifier
    d, case = delivery, CASES[name]
    await _arrange(d, case["arrange"])
    rules, policies = await ap._rule_set(d.db), await sp.load_active_policies(d.db)

    facts = await _bulk(d, d.refs[0])

    assert (facts["sam_gov"], facts["sam_extract"], facts["oig_leie"], facts["pecos"]) == case["states"]
    assert facts["nppes"] == "verified" and facts["fields"] == case["fields"]
    # BEFORE the policy: the same classifier call the writer makes, on the classifier input it stored.
    stored_input = facts["results"]["classifier_input"]
    before = BucketClassifier().classify(stored_input, rules=rules)
    assert (before.bucket, before.rule_code) == case["before"]
    # THE POLICY: the pure function the writer's enforcement wraps, on the same input.
    bucket, rule_code, reason, matched = sp.apply_source_policy(
        before.bucket, before.rule_code, stored_input["sources"], {"has_submitted_npi": True}, policies)
    assert reason == case["policy"]
    if reason is None:
        assert (bucket, rule_code, matched) == (before.bucket, before.rule_code, None)
    else:
        assert (bucket, rule_code) == ("B3", None) and matched == policies[0]
        assert sorted(matched) == ["applicability", "approval_reference", "id", "policy_reference", "role",
                                   "source", "version"]
    # WHAT IS STORED
    assert (facts["bucket"], facts["rule"], facts["rule_version"], facts["tier"]) == case["final"], facts["rationale"]
    expected_text = _policy_text(reason, d.configuration) if reason else RATIONALES[name]
    if name == "7_extract_candidate_with_no_active_policy_row":
        # Since 2026-11-26 the candidate is at least SAID on the review (still B1, still marked verified: with no
        # active policy row nothing routes an extract candidate to review).
        from datetime import datetime as _dt, timezone as _tz
        expected_text += (
            f" Pending exclusion concern: the dated SAM public exclusions extract (edition "
            f"{_dt.now(_tz.utc).date().isoformat()}, SAM_Exclusions_Public_Extract_V2_SYNTHETIC.ZIP) returned 1 "
            f"candidate match(es) for this record (matched by npi). A candidate is not a confirmed exclusion; an "
            f"analyst must confirm or dismiss it. The bucket and rule above are unchanged.")
    assert facts["rationale"] == expected_text
    assert facts["outcome"]["entity_marked_verified"] is case["marked_verified"]
    # Every source was asked through the real connectors, and both SAM API legs were tried.
    assert sorted(source for source, _what in d.world.calls) == [
        "CMS", "CMS", "NPPES", "SAM_GOV", "SAM_GOV_EXCLUSIONS"]


# ── the single-record path (`review_service.run_review`) ─────────────────────────────────────────────────────────
#
# It cannot be driven the same way. It does not gather through the evidence assembly: `probe_sources` asks NPPES
# and reads the loaded LEIE list, and asks NEITHER the SAM API NOR the SAM extract (`sam_gov` is "not_checked").
# The only SAM answer it can use is the extract evidence the BULK path stored for the entity earlier, handed to the
# policy alone. So its outcome depends on whether the bulk writer has already run for the record.

async def _single(d):
    from app.tefca_registry import review_service
    row = (await d.db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == d.intake_id,
        m.RceCuratedRecord.rce_org_oid == d.refs[0]))).scalars().one()
    entity = await d.db.get(reg.TefcaRegEntity, row.canonical_entity_id)
    d.world.calls.clear()
    envelope = await review_service.run_review(d.db, entity, user=None, source_record_id=row.source_record_id)
    review = (await d.db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == envelope["review_id"]))).scalars().one()
    sources = (review.verification_results or {}).get("sources") or {}
    return {"bucket": review.classification_bucket, "rule": review.classification_rule,
            "rule_version": review.classification_rule_version, "rationale": review.classification_rationale,
            "sam_gov": (sources.get("sam_gov") or {}).get("status"), "has_sam_extract": "sam_extract" in sources,
            "oig_leie": (sources.get("oig_leie") or {}).get("status"),
            "asked": sorted(source for source, _what in d.world.calls)}


SINGLE_AFTER_BULK = ["1_api_unavailable_extract_clear", "2_api_unavailable_extract_unavailable",
                     "3_api_unavailable_extract_candidate"]


@pytest.mark.parametrize("name", SINGLE_AFTER_BULK)
async def test_single_record_path_after_the_bulk_writer_agrees_with_it(delivery, name):
    """With the bulk writer's stored extract evidence to read, the single-record review reaches the SAME bucket,
    rule, version and rationale for cases 1-3 - without asking SAM anything itself."""
    d, case = delivery, CASES[name]
    await _arrange(d, case["arrange"])
    bulk = await _bulk(d, d.refs[0])
    single = await _single(d)
    assert (single["bucket"], single["rule"], single["rule_version"], single["rationale"]) == (
        bulk["bucket"], bulk["rule"], bulk["rule_version"], bulk["rationale"])
    assert (single["bucket"], single["rule"], single["rule_version"]) == case["final"][:3]
    assert single["sam_gov"] == "not_checked" and single["has_sam_extract"] is False
    assert single["asked"] == ["NPPES", "PECOS"]                       # no SAM API leg, no extract screen


@pytest.mark.parametrize("name", SINGLE_AFTER_BULK)
async def test_single_record_path_on_its_own_is_b3_whatever_the_extract_would_say(delivery, name):
    """With no stored extract evidence (the bulk writer has not run for the record), all three cases end the same
    way: B3, SOURCE_NOT_COMPLETED_SAM_GOV. Case 1 therefore DISAGREES with the bulk path (B1) until the bulk
    writer has run; cases 2 and 3 are B3 on both paths, case 3 for a different reason (not-completed here,
    pending candidate there)."""
    d, case = delivery, CASES[name]
    await _arrange(d, case["arrange"])
    single = await _single(d)
    assert (single["bucket"], single["rule"], single["rule_version"]) == ("B3", "SOURCE-POLICY-M4", 1)
    assert single["rationale"] == _policy_text("SOURCE_NOT_COMPLETED_SAM_GOV", d.configuration)
    assert single["sam_gov"] == "not_checked" and single["has_sam_extract"] is False
    assert single["asked"] == ["NPPES", "PECOS"]


async def test_the_two_paths_disagree_on_a_leie_row_carrying_the_npi(delivery):
    """Case 6, both paths, same record, same LEIE row. The bulk path reports a possible match awaiting an analyst
    (B3, RULE-008); the single-record path reports the entity excluded (B4, RULE-005)."""
    d = delivery
    await _arrange(d, CASES["6_leie_possible_match_extract_clear"]["arrange"])
    bulk = await _bulk(d, d.refs[0])
    single = await _single(d)
    assert (bulk["oig_leie"], bulk["bucket"], bulk["rule"], bulk["rule_version"]) == (
        "possible_match", "B3", "RULE-008", 4)
    assert (single["oig_leie"], single["bucket"], single["rule"], single["rule_version"]) == (
        "excluded", "B4", "RULE-005", 4)
    assert single["rationale"] == "B4 Non-Compliant (RULE-005 v4): oig_leie is excluded"
