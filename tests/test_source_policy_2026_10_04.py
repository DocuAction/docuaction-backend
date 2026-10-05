"""app.Tefca.source_policy -- registry AND its integration (Part B).

Section 1 is pure (no DB, no network). Section 2 proves the block is
actually produced by the code that processes records and is shown to
analysts -- a module with passing unit tests is not, by itself, proof that
anything uses it.
"""
from __future__ import annotations

import copy
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.Tefca import source_policy as sp

VERIFICATION_SOURCES = [s for s in sp.ALL_SOURCE_IDS if s != sp.EVIDENCE_RETENTION]


# ── 1. the registry ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("source_id", sp.ALL_SOURCE_IDS)
def test_every_official_policy_is_unapproved_and_every_proposed_one_inactive(source_id):
    official, proposed = sp.official_view(source_id), sp.proposed_view(source_id)
    assert official["approval_status"] == sp.POLICY_UNAPPROVED
    assert official["freshness"] == sp.FRESHNESS_UNKNOWN
    assert proposed["approval_status"] == sp.PROPOSED_INACTIVE
    assert sp.registry_dto()["any_policy_approved"] is False


@pytest.mark.parametrize("source_id", VERIFICATION_SOURCES)
def test_a_recent_as_of_never_makes_an_unapproved_source_current(source_id):
    now = datetime.now(timezone.utc).isoformat()
    assert sp.official_view(source_id, as_of=now, retrieved_at=now)["freshness"] == sp.FRESHNESS_UNKNOWN


def test_oig_and_sam_are_registered_explicitly_with_candidate_only_name_matching():
    leie, sam = sp.PROPOSED_POLICIES[sp.OIG_LEIE], sp.PROPOSED_POLICIES[sp.SAM_GOV]
    for policy in (leie, sam):
        assert "EIN/TIN/SSN are unavailable and not used" in policy.identity_method
    assert "POTENTIAL_HIT candidate only" in leie.identity_method
    assert "no threshold" in leie.identity_method
    assert "AMBIGUOUS" in sam.identity_method
    assert "one legal effect is NOT assumed" in sam.applicability_authority
    assert "disappearance" in leie.rationale and "clears nothing" in leie.rationale
    assert "LEIE_NO_CANDIDATE_IN_LIST" in leie.reason_codes
    assert leie.schema_version == "oig-leie-updated-csv/required-columns-v1"


def test_nppes_registry_api_and_dissemination_file_are_distinct_policies():
    api = sp.PROPOSED_POLICIES[sp.NPPES_REGISTRY_API]
    bulk = sp.PROPOSED_POLICIES[sp.NPPES_DISSEMINATION_FILE]
    assert api.schema_version == "npiregistry-api/2.1"
    assert bulk.mapping_version == "V2"
    assert api.freshness_window_days != bulk.freshness_window_days
    assert sp.EVIDENCE_SOURCE_TO_POLICY["NPPES"] == sp.NPPES_REGISTRY_API
    assert "no loader" in bulk.identity_method


def test_the_registry_does_not_trip_the_no_bulk_loader_guard():
    """tests/test_ppef_bulk_ingest_gate.py forbids this literal anywhere in app/."""
    text = pathlib.Path(sp.__file__).read_text(encoding="utf-8").lower()
    assert "nppes" + "_bulk" not in text


def test_pecos_proxy_is_distinguished_from_the_cms_enrolment_dataset():
    from app.Tefca.connectors import PECOS_BACKING

    proxy, ppef = sp.PROPOSED_POLICIES[sp.PECOS_PROXY], sp.PROPOSED_POLICIES[sp.CMS_PPEF]
    assert proxy.mapping_version == "PROXY_NOT_PECOS" and PECOS_BACKING in proxy.identity_method
    assert sp.EVIDENCE_SOURCE_TO_POLICY["CMS_PPEF_ENROLLMENT"] == sp.CMS_PPEF
    assert sp.EVIDENCE_SOURCE_TO_POLICY["PECOS"] == sp.PECOS_PROXY
    assert ppef.publication_cadence == "quarterly"


def test_usps_scope_is_recorded_and_proves_neither_identity_nor_occupancy():
    usps = sp.PROPOSED_POLICIES[sp.USPS]
    assert usps.extra == {"proves_identity": False, "proves_occupancy": False}
    assert "Holding credentials does not" in usps.applicability_authority
    assert "signed USPS agreement" in usps.applicability_authority


def test_iqvia_only_the_affiliation_journey_is_unsupported():
    iqvia = sp.PROPOSED_POLICIES[sp.IQVIA]
    assert iqvia.mapping_version == "delivered-facts-only"
    assert "IQVIA_AFFILIATION_JOURNEY_UNSUPPORTED" in iqvia.reason_codes
    assert "none is inferred or fabricated" in iqvia.identity_method


def test_retention_is_unapproved_deletes_nothing_and_is_not_indefinite_by_default():
    official = sp.OFFICIAL_POLICIES[sp.EVIDENCE_RETENTION]
    proposed = sp.PROPOSED_POLICIES[sp.EVIDENCE_RETENTION]
    assert official.approval_status == sp.POLICY_UNAPPROVED
    assert "NOTHING is deleted" in official.rationale
    assert "NOT thereby approved" in official.rationale
    assert proposed.extra == {"automatic_deletion": False,
                              "indefinite_retention_approved": False}
    assert "REQUIRED AND NOT YET IDENTIFIED" in proposed.applicability_authority


def test_stale_is_visible_only_in_the_inactive_proposed_view():
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    both = sp.both_views(sp.OIG_LEIE, as_of=old, retrieved_at=old, verified_at=old)
    assert both["official"]["freshness"] == sp.FRESHNESS_UNKNOWN
    assert both["proposed"]["freshness"] == sp.FRESHNESS_STALE
    assert both["proposed"]["approval_status"] == sp.PROPOSED_INACTIVE
    assert both["official"]["evidence"] == both["proposed"]["evidence"]
    assert set(both["official"]["evidence"]) == {"as_of", "retrieved_at", "verified_at"}


def test_approved_requires_provenance_and_unknown_ids_raise():
    with pytest.raises(ValueError, match="approval provenance"):
        sp.SourcePolicy(source_id="X", schema_version="1", mapping_version="1",
                        identity_method="x", dependencies=[], applicability_authority="x",
                        publication_cadence="x", freshness_window_days=30, blocking_scope="x",
                        reason_codes=[], effective_date="2026-10-04",
                        approval_status=sp.POLICY_APPROVED)
    with pytest.raises(KeyError):
        sp.official_view("NOT_A_SOURCE")


# ── 2. integration: processing and analyst surfaces actually use it ──────────

def _evidence():
    now = datetime.now(timezone.utc).isoformat()
    return {"generated_at": now, "dimensions": [
        {"dimension": "IDENTITY", "disposition": "PASS", "applicability": "REQUIRED",
         "evidence": [{"source": "NPPES", "disposition": "PASS", "query_timestamp": now}]},
        {"dimension": "EXCLUSION_REVOCATION", "disposition": "REVIEW",
         "applicability": "REQUIRED",
         "evidence": [{"source": "OIG_LEIE", "disposition": "PASS", "query_timestamp": now},
                      {"source": "SAM_GOV", "disposition": "REVIEW", "query_timestamp": now},
                      {"source": "ENTRANT_WEBSITE", "disposition": "NOT_FOUND"}]}]}


def test_annotation_adds_one_key_and_changes_no_disposition():
    evidence = _evidence()
    before = copy.deepcopy(evidence)
    out = sp.annotate_evidence(evidence)
    assert out is evidence
    assert set(evidence) - set(before) == {"source_policy"}
    assert evidence["dimensions"] == before["dimensions"]      # nothing overwritten

    block = evidence["source_policy"]
    assert set(block["sources"]) == {sp.NPPES_REGISTRY_API, sp.OIG_LEIE, sp.SAM_GOV}
    assert block["unregistered_sources"] == ["ENTRANT_WEBSITE"]   # listed, not dropped
    for entry in block["sources"].values():
        assert entry["official"]["approval_status"] == sp.POLICY_UNAPPROVED
        assert entry["official"]["freshness"] == sp.FRESHNESS_UNKNOWN
        assert entry["proposed_inactive"]["approval_status"] == sp.PROPOSED_INACTIVE
        assert entry["evidence"]["retrieved_at"] and entry["evidence"]["verified_at"]
    # A just-retrieved answer is CURRENT only in the inactive proposed view.
    assert block["sources"][sp.SAM_GOV]["proposed_inactive"]["freshness"] == sp.FRESHNESS_CURRENT


def test_the_manual_path_block_covers_its_sources_including_persisted_evidence():
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    sources = {"nppes": {"status": "verified", "verified_at": datetime.now(timezone.utc).isoformat()},
               "oig_leie": {"status": "clear", "persisted_evidence": {"generation_timestamp": old}},
               "sam_gov": {"status": "not_checked"}, "irs": {"status": "not_checked"}}
    block = sp.manual_sources_block(sources, verified_at="2026-10-04T00:00:00Z")
    assert set(block["sources"]) == {sp.NPPES_REGISTRY_API, sp.OIG_LEIE, sp.SAM_GOV}
    assert block["unregistered_sources"] == ["irs"]
    leie = block["sources"][sp.OIG_LEIE]
    assert leie["evidence"]["retrieved_at"] == old
    assert leie["official"]["freshness"] == sp.FRESHNESS_UNKNOWN          # official: never guessed
    assert leie["proposed_inactive"]["freshness"] == sp.FRESHNESS_STALE   # shadow: visibly old


def test_the_workspace_attaches_the_policy_view_to_each_evidence_row():
    from app.tefca_registry.rce import analyst_workspace as ws

    now = datetime.now(timezone.utc)
    row = SimpleNamespace(source="OIG_LEIE", retrieved_at=None, query_timestamp=now.isoformat(),
                          generation_timestamp=now.isoformat())
    view = ws._policy_for_item(row)
    assert view["policy_registered"] is True and view["policy_id"] == sp.OIG_LEIE
    assert view["official"]["freshness"] == sp.FRESHNESS_UNKNOWN
    unknown = ws._policy_for_item(SimpleNamespace(source="SOMETHING_ELSE", retrieved_at=None,
                                                  query_timestamp=None, generation_timestamp=None))
    assert unknown == {"policy_registered": False, "approval_status": sp.POLICY_UNAPPROVED,
                       "freshness": sp.FRESHNESS_UNKNOWN}
    assert ws.FRESHNESS_WINDOW_BASIS.startswith("OPERATIONAL_DEFAULT_NOT_APPROVED_POLICY")


@pytest.mark.asyncio
async def test_the_policy_api_is_read_only_and_lists_every_source():
    from app.tefca_registry.rce import preflight_shadow_routes as routes

    dto = await routes.source_policies_route(user=None)
    assert [s["source_id"] for s in dto["sources"]] == list(sp.ALL_SOURCE_IDS)
    assert dto["any_policy_approved"] is False
    paths = {(r.path, tuple(sorted(r.methods))) for r in routes.router.routes
             if "source-policies" in r.path}
    assert paths == {("/api/tefca/rce/source-policies", ("GET",))}     # no write route exists


@pytest.mark.asyncio
async def test_the_real_pipeline_persists_the_block_and_the_classifier_never_sees_it(
        db_required, monkeypatch):
    """Real ingest -> quality -> curate -> promote -> verify_and_classify,
    patched connectors, synthetic record."""
    from sqlalchemy import select

    import test_sam_e2e_delivery_path as sam
    from app.Tefca.connectors import SourceResult
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from app.tefca_registry.rce import arc_pipeline

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    sam._clean_nppes_leie(monkeypatch)
    sam._patch_sam_verify(monkeypatch, lambda uei, legal_name: SourceResult.ok("SAM_GOV", {
        "found": True, "matched_by": "uei", "excluded": True, "excluded_known": True,
        "identity_ambiguous": False, "registration_current": True}, {"uei": uei}))
    intake_id = await sam._seed_promoted_delivery(n=1)
    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, 1)
    async with async_session_maker() as db:
        outcome = (await arc_pipeline.verify_and_classify(
            db, refs, intake_id=intake_id, actor="pytest-source-policy"))["outcomes"][0]
    async with async_session_maker() as db:
        record = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == outcome["review_id"]))).scalars().one()
        rules = await arc_pipeline._rule_set(db)

    vr = record.verification_results
    block = vr["source_policy"]
    assert {sp.SAM_GOV, sp.OIG_LEIE, sp.NPPES_REGISTRY_API} <= set(block["sources"])
    assert all(e["official"]["approval_status"] == sp.POLICY_UNAPPROVED
               for e in block["sources"].values())
    # Not an input to classification, and the official outcome is exactly
    # what the classifier gives for the persisted input: an unapproved policy
    # did not overwrite it.
    assert "source_policy" not in vr["classifier_input"]
    again = BucketClassifier().classify(vr["classifier_input"], rules=rules)
    assert again.bucket == record.classification_bucket == outcome["bucket"] != "B1"
