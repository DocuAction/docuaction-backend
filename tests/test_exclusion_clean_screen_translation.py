"""A CLEAN exclusion-list name screen is not a disqualifier (2026-10-03).

Peer finding (qa-evidence/.../PEER-LANE-S.md, repro
lanes/S/repro_v3_name_screen_not_found_disqualifies.py): the evidence layer
emits NOT_FOUND for "screened SAM.gov / OIG LEIE by organisation name,
nothing listed", `arc_pipeline._DISPOSITION_TO_STATE` mapped that to the same
`not_found` state as a REVIEW (potential hit), and SEED_RULES_V3 treats
`sam_gov/oig_leie == not_found` as B4. On the bulk path SAM is always
name-screened (no UEI in the 41-field delivery) and LEIE is name-screened for
every NPI-less record, so with a SAM key configured a clean entity became B4.

Fixed in the translator only (`arc_pipeline.evidence_item_state`): on the
EXCLUSION_REVOCATION dimension NOT_FOUND -> "clear". No rule changed; v3 is
the active rule set. The four required outcomes, pinned on BOTH paths:
  confirmed exclusion (REVIEW)      -> B4
  pending / ambiguous hit (REVIEW)  -> B4 (never auto-B1, per the existing v3 rules)
  clean screen, nothing listed      -> eligible for B1 (NOT disqualifying)
  unavailable / failed              -> LEIE/NPPES: never B1. SAM/PECOS: B1 via
                                       RULE-001/002 by the documented v1/v2
                                       design ("a disqualifier, never a
                                       requirement"; "an outage is not a
                                       discrepancy") -- pinned explicitly below
                                       as the current policy, not changed here.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tefca_registry.bucket_classifier import BucketClassifier, SEED_RULES_V2, SEED_RULES_V3
from app.tefca_registry.rce.arc_pipeline import (EXCLUSION_CLEAN_SCREEN_STATE,
                                                 _DISPOSITION_TO_STATE,
                                                 dimensions_to_verification_results,
                                                 evidence_item_state)

pytestmark = pytest.mark.regression

V3 = BucketClassifier(rules=SEED_RULES_V3)
V2 = BucketClassifier(rules=SEED_RULES_V2)


def _evidence(sam, leie, nppes="PASS", pecos="PASS"):
    """The peer repro's shape: the real D1/D3/D2 item layout."""
    return {"dimensions": [
        {"dimension": "IDENTITY", "disposition": nppes, "applicability": "REQUIRED",
         "evidence": [{"source": "NPPES", "disposition": nppes},
                      {"source": "CMS_PPEF_ENROLLMENT", "disposition": "CORROBORATED"}]},
        {"dimension": "EXCLUSION_REVOCATION", "disposition": "REVIEW", "applicability": "REQUIRED",
         "evidence": [{"source": "OIG_LEIE", "disposition": leie,
                       "rule_applied": "OIG_LEIE_ORG_LEVEL_CHECK_NO_NPI"},
                      {"source": "SAM_GOV", "disposition": sam,
                       "rule_applied": "SAM_ORG_LEVEL_CHECK_NO_UEI"},
                      {"source": "CMS_REVOCATION", "disposition": "PASS"}]},
        {"dimension": "MEDICARE_ENROLLMENT", "disposition": pecos, "applicability": "REQUIRED",
         "evidence": [{"source": "CMS_PPEF_ENROLLMENT", "disposition": pecos}]},
    ], "data_quality_flags": []}


def _bulk(sam, leie, **kw):
    return V3.classify(dimensions_to_verification_results(_evidence(sam, leie, **kw)))


# ── the translator ───────────────────────────────────────────────────────────

def test_clean_exclusion_screen_translates_to_clear_only_on_the_exclusion_dimension():
    assert evidence_item_state("EXCLUSION_REVOCATION", "NOT_FOUND") == EXCLUSION_CLEAN_SCREEN_STATE
    assert evidence_item_state("EXCLUSION_REVOCATION", "REVIEW") == "not_found"
    assert evidence_item_state("EXCLUSION_REVOCATION", "UNAVAILABLE") == "unavailable"
    assert evidence_item_state("EXCLUSION_REVOCATION", "PASS") == "verified"
    # A PECOS enrolment NOT_FOUND is a finding and stays not_found.
    assert evidence_item_state("MEDICARE_ENROLLMENT", "NOT_FOUND") == "not_found"
    assert evidence_item_state("IDENTITY", "NOT_FOUND") == "not_found"
    # The base table is untouched (test_classifier_signal_contract pins it).
    assert _DISPOSITION_TO_STATE["NOT_FOUND"] == "not_found"
    assert "clear" not in _DISPOSITION_TO_STATE.values()


# ── bulk path: the peer's four cases, now with the required outcomes ─────────

def test_case_a_clean_sam_name_screen_is_eligible_for_b1_not_b4():
    """Peer repro case A: before the fix v2 B1/RULE-001 -> v3 B4/RULE-005."""
    inp = dimensions_to_verification_results(_evidence("NOT_FOUND", "PASS"))
    assert inp["sources"]["sam_gov"]["status"] == "clear"
    r = _bulk("NOT_FOUND", "PASS")
    assert r.bucket == "B1" and r.rule_code == "RULE-001"
    assert V2.classify(inp).bucket == "B1"            # v2 and v3 agree again


def test_case_b_clean_leie_org_name_screen_for_an_npi_less_entity_is_not_b4():
    """Peer repro case B. A no-NPI entity has no NPPES identity, so it is
    not B1 either -- but it must not be DISQUALIFIED for a clean screen."""
    r = _bulk("PASS", "NOT_FOUND", nppes="NOT_APPLICABLE")
    assert r.bucket != "B4" and r.rule_code != "RULE-005"
    # With identity confirmed the same clean LEIE screen is B1-eligible.
    r = _bulk("PASS", "NOT_FOUND")
    assert r.bucket == "B1" and r.rule_code == "RULE-001"


def test_case_c_potential_or_confirmed_exclusion_still_disqualifies():
    """REVIEW covers a confirmed hit, a potential hit and an ambiguous
    multi-match (evidence_assembly._sam_disposition) -- all stay B4."""
    r = _bulk("REVIEW", "PASS")
    assert r.bucket == "B4" and r.rule_code == "RULE-005"
    r = _bulk("PASS", "REVIEW")
    assert r.bucket == "B4" and r.rule_code == "RULE-005"


def test_case_d_unavailable_and_failed_outcomes_pinned_per_source():
    """LEIE/NPPES unavailable or failed: never B1. SAM/PECOS unavailable: B1
    by the v1/v2 design (RULE-001 has no positive SAM requirement; RULE-002's
    partial pass tolerates an unreachable PECOS/SAM). Pinned as the CURRENT
    rule-set policy; changing it is a SEED_RULES_V4 decision."""
    assert _bulk("PASS", "UNAVAILABLE").bucket != "B1"
    assert _bulk("PASS", "FAIL").bucket != "B1"
    assert _bulk("PASS", "PASS", nppes="UNAVAILABLE").bucket != "B1"
    assert _bulk("PASS", "PASS", nppes="FAIL").bucket != "B1"
    r = _bulk("UNAVAILABLE", "PASS")
    assert r.bucket == "B1" and r.rule_code == "RULE-001"
    r = _bulk("FAIL", "PASS")
    assert r.bucket == "B1"                               # same design, same outcome
    r = _bulk("PASS", "PASS", pecos="UNAVAILABLE")
    assert r.bucket == "B1" and r.rule_code == "RULE-002"


# ── manual path with persisted evidence: identical four outcomes ─────────────

def _row(source, disposition):
    return SimpleNamespace(id="00000000-0000-0000-0000-000000000000", source=source,
                           disposition=disposition, generation_timestamp="2026-10-02T12:00:00Z",
                           review_id="REV-2026-000001", rule_applied="D3", query_identifier="x")


def _manual(sam_disposition, *, leie_live="clear"):
    """Manual path: live NPPES/PECOS verified, live LEIE as given, SAM from
    persisted evidence only (the stub otherwise)."""
    from app.tefca_registry import review_service as svc

    sources = {"nppes": {"status": "verified"}, "pecos": {"status": "verified"},
               "oig_leie": {"status": leie_live},
               "sam_gov": {"status": svc.NOT_CHECKED, "reason": svc.NO_CONNECTOR["sam_gov"]}}
    rows = {"SAM_GOV": _row("SAM_GOV", sam_disposition)} if sam_disposition else {}
    svc.apply_persisted_exclusion_evidence(sources, rows)
    return sources, V3.classify({"sources": sources, "fields": {}, "confidence_score": None})


def test_manual_path_persisted_outcomes_match_the_bulk_path():
    sources, r = _manual("REVIEW")                      # confirmed / pending / ambiguous
    assert sources["sam_gov"]["status"] == "not_found" and r.bucket == "B4"
    sources, r = _manual("NOT_FOUND")                   # clean name screen
    assert sources["sam_gov"]["status"] == "clear" and r.bucket == "B1"
    sources, r = _manual("PASS")                        # clean UEI match
    assert sources["sam_gov"]["status"] == "verified" and r.bucket == "B1"
    sources, r = _manual("UNAVAILABLE")                 # SAM unavailable: v1/v2 policy
    assert sources["sam_gov"]["status"] == "unavailable" and r.bucket == "B1"
    sources, r = _manual(None, leie_live="failed")      # LEIE failed live: never B1
    assert r.bucket != "B1"
    sources, r = _manual(None)                          # no evidence at all: stub, policy B1
    assert sources["sam_gov"]["status"] == "not_checked" and r.bucket == "B1"
