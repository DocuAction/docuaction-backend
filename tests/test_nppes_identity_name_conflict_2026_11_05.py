"""A found-and-active NPI is registry EXISTENCE, not organization IDENTITY.

2026-11-05: the ONC RCE vetting file exposed ~290 October delivery lines whose
format-valid NPIs belong to OTHER organizations (NPPES-confirmed on every
sampled line, e.g. submitted 'NP ON CALL' carrying the NPI NPPES registers to
'ENVIVE OF HARTFORD'). `_dimension_identity` recorded the legal-name conflict
in `field_conflicts` but the disposition was PASS whenever the NPI was found
and not deactivated, so the identity dimension could never surface a
wrong-organization NPI. These tests pin the correction: no-correspondence
(token similarity < 0.5, the same bound the enhanced check_nppes escalates at)
is REVIEW — analyst review, never an auto-fail and never an auto-correction.

Pure tests - no database.
"""
from __future__ import annotations

from app.Tefca.applicability import build_profile
from app.Tefca.connectors import SourceResult
from app.Tefca.evidence_assembly import _dimension_identity
from app.tefca_registry.rce import verification_findings as vf

NPI = "1922206952"  # 10-digit, Luhn-valid (the live line-5631 example's shape)


def _identity_result(submitted_name, legal_name, *, status="ACTIVE", **extra):
    """Run _dimension_identity for one submitted name vs NPPES legal name."""
    entity = {"name": submitted_name,
              "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi",
                              "value": NPI}]}
    nppes_data = {"found": True, "npi": NPI, "legal_name": legal_name,
                  "status": status, **extra}
    profile = build_profile(entity, nppes_data=nppes_data)
    nppes = SourceResult.ok("NPPES", nppes_data, {}, "2.1")
    return _dimension_identity(entity, profile, {"nppes": nppes})


def test_wrong_organization_npi_is_review_not_pass():
    """The October defect in miniature: active NPI, unrelated registered org."""
    result = _identity_result("NP ON CALL", "ENVIVE OF HARTFORD")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "REVIEW"
    assert item.rule_applied == "NPPES_IDENTITY_NAME_CONFLICT"
    assert "different organization" in (item.note or "")
    assert item.field_conflicts and item.field_conflicts[0]["result"] == "DIFFERS"
    assert item.field_conflicts[0]["similarity"] < 0.5
    assert result.disposition == "REVIEW"
    assert result.requires_analyst is True
    assert "may belong to a different organization" in result.rationale


def test_benign_name_variant_stays_pass():
    """Suffix/punctuation variants must not flood REVIEW: high token overlap
    keeps the existence PASS, while the inexact match is still recorded."""
    result = _identity_result("VAN METER PEDIATRIC ENDOCRINOLOGY, P.C.",
                              "VAN METER PEDIATRIC ENDOCRINOLOGY")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "PASS"
    assert item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"
    # exact-equality bookkeeping unchanged: still recorded as DIFFERS
    assert item.field_conflicts and item.field_conflicts[0]["result"] == "DIFFERS"
    assert result.disposition == "PASS"


def test_exact_name_match_is_pass_with_match_recorded():
    result = _identity_result("NP ON CALL", "NP On Call")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "PASS"
    assert item.field_matches and item.field_matches[0]["result"] == "MATCH"
    assert not item.field_conflicts


def test_deactivation_takes_precedence_over_the_name_conflict():
    """A deactivated NPI stays NPPES_NPI_DEACTIVATED (its own finding family),
    whatever the registered name says."""
    result = _identity_result("NP ON CALL", "ENVIVE OF HARTFORD",
                              status="DEACTIVATED", deactivation_date="2024-01-31")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "REVIEW"
    assert item.rule_applied == "NPPES_NPI_DEACTIVATED"
    assert "deactivated" in result.rationale.lower()


def test_missing_either_name_cannot_conflict():
    """No submitted name, or no registered legal name, is no basis for an
    identity conflict - existence alone then carries the disposition."""
    for submitted, legal in (("", "ENVIVE OF HARTFORD"), ("NP ON CALL", "")):
        result = _identity_result(submitted, legal)
        item = next(i for i in result.items if i.source == "NPPES")
        assert item.disposition == "PASS"
        assert item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"


def test_name_conflict_is_never_derived_as_verified():
    """npi_outcome_from_evidence previously read any non-deactivated REVIEW as
    VERIFIED. A name-conflict REVIEW has no contract-reserved ledger rule yet,
    so it derives None ("nothing to record") - never a clean VERIFIED."""
    result = _identity_result("NP ON CALL", "ENVIVE OF HARTFORD")
    bundle = {"dimensions": [result.to_dict()]}
    assert vf.npi_outcome_from_evidence(bundle) is None
    # the neighbouring outcomes still derive exactly as before
    ok = _identity_result("NP ON CALL", "NP ON CALL")
    assert vf.npi_outcome_from_evidence(
        {"dimensions": [ok.to_dict()]})["outcome"] == vf.VERIFIED
    gone = _identity_result("NP ON CALL", "ENVIVE OF HARTFORD",
                            status="DEACTIVATED", deactivation_date="2024-01-31")
    assert vf.npi_outcome_from_evidence(
        {"dimensions": [gone.to_dict()]})["outcome"] == vf.NPI_DEACTIVATED
