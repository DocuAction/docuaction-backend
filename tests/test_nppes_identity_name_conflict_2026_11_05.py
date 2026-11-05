"""A found-and-active NPI is registry EXISTENCE, not organization IDENTITY.

2026-11-05: supplemental vetting evidence exposed delivery lines whose
format-valid NPIs belong to OTHER organizations (confirmed against the public
registry per record). `_dimension_identity` recorded the legal-name conflict
in `field_conflicts` but the disposition was PASS whenever the NPI was found
and not deactivated, so the identity dimension could never surface a
wrong-organization NPI. These tests pin the correction: no-correspondence
(token similarity < 0.5, the same bound the enhanced check_nppes escalates at)
is REVIEW — analyst review, never an auto-fail and never an auto-correction.

All organization names and NPIs here are synthetic. Pure tests - no database.
"""
from __future__ import annotations

from app.Tefca.applicability import build_profile
from app.Tefca.connectors import SourceResult
from app.Tefca.evidence_assembly import _dimension_identity
from app.tefca_registry.rce import verification_findings as vf
from rce_traceability_support import NPI_VALID_OTHER  # Luhn-valid synthetic NPI


def _identity_result(submitted_name, legal_name, *, status="ACTIVE", **extra):
    """Run _dimension_identity for one submitted name vs NPPES legal name."""
    entity = {"name": submitted_name,
              "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi",
                              "value": NPI_VALID_OTHER}]}
    nppes_data = {"found": True, "npi": NPI_VALID_OTHER, "legal_name": legal_name,
                  "status": status, **extra}
    profile = build_profile(entity, nppes_data=nppes_data)
    nppes = SourceResult.ok("NPPES", nppes_data, {}, "2.1")
    return _dimension_identity(entity, profile, {"nppes": nppes})


def test_wrong_organization_npi_is_review_not_pass():
    """The defect in miniature: an active NPI registered to an unrelated org."""
    result = _identity_result("SYNTHETIC URGENT CARE ALPHA",
                              "SYNTHETIC NURSING OPERATIONS BETA LLC")
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
    result = _identity_result("SYNTHETIC PEDIATRIC GROUP GAMMA, P.C.",
                              "SYNTHETIC PEDIATRIC GROUP GAMMA")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "PASS"
    assert item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"
    # exact-equality bookkeeping unchanged: still recorded as DIFFERS
    assert item.field_conflicts and item.field_conflicts[0]["result"] == "DIFFERS"
    assert result.disposition == "PASS"


def test_punctuation_only_difference_is_not_a_conflict():
    """'ACME, INC.' vs 'ACME INC' tokenizes identically once punctuation is
    normalized - similarity must be high, never the 0.0 that raw
    whitespace-splitting produced."""
    result = _identity_result("SYNTHETIC ACME CLINIC, INC.", "SYNTHETIC ACME CLINIC INC")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "PASS"
    assert item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"


def test_dotted_legal_suffix_is_not_a_conflict():
    """'… L.L.C.' shatters into single letters under punctuation
    normalization; those fragments carry no identity and must not dilute
    the score below the conflict bound."""
    result = _identity_result("SYNTHETIC ACME CLINIC L.L.C.", "SYNTHETIC ACME CLINIC LLC")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "PASS"
    assert item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"


def test_identical_all_stop_word_name_cannot_conflict_with_itself():
    """A name made entirely of stop words ('MD') scores 0.0 against itself in
    the token measure; exact equality must win and stay PASS/MATCH."""
    result = _identity_result("MD", "MD")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "PASS"
    assert item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"
    assert item.field_matches and item.field_matches[0]["result"] == "MATCH"
    assert not item.field_conflicts


def test_exact_name_match_is_pass_with_match_recorded():
    result = _identity_result("SYNTHETIC URGENT CARE ALPHA", "Synthetic Urgent Care Alpha")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "PASS"
    assert item.field_matches and item.field_matches[0]["result"] == "MATCH"
    assert not item.field_conflicts


def test_deactivation_takes_precedence_over_the_name_conflict():
    """A deactivated NPI stays NPPES_NPI_DEACTIVATED (its own finding family),
    whatever the registered name says."""
    result = _identity_result("SYNTHETIC URGENT CARE ALPHA",
                              "SYNTHETIC NURSING OPERATIONS BETA LLC",
                              status="DEACTIVATED", deactivation_date="2024-01-31")
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "REVIEW"
    assert item.rule_applied == "NPPES_NPI_DEACTIVATED"
    assert "deactivated" in result.rationale.lower()


def test_missing_either_name_cannot_conflict():
    """No submitted name, or no registered legal name, is no basis for an
    identity conflict - existence alone then carries the disposition."""
    for submitted, legal in (("", "SYNTHETIC NURSING OPERATIONS BETA LLC"),
                             ("SYNTHETIC URGENT CARE ALPHA", ""),
                             ("   ", "SYNTHETIC NURSING OPERATIONS BETA LLC"),
                             ("SYNTHETIC URGENT CARE ALPHA", "   ")):
        result = _identity_result(submitted, legal)
        item = next(i for i in result.items if i.source == "NPPES")
        assert item.disposition == "PASS"
        assert item.rule_applied == "NPPES_PRIMARY_IDENTITY_AUTHORITY"


def test_entity_outcome_routes_review_to_review_required():
    """A dimension that explicitly asks for an analyst (REVIEW) must reach
    REVIEW_REQUIRED even when another applicable dimension is NOT_FOUND -
    previously the NOT_FOUND mix swallowed it into VERIFIED_WITH_DIFFERENCES
    and the entity never got verification_status in_review."""
    from app.tefca_registry.rce import automated_verification as av

    def bundle(*dispositions):
        return {"dimensions": [{"applicability": "APPLICABLE", "disposition": d}
                               for d in dispositions]}

    assert av._classify_entity_outcome(bundle("REVIEW", "NOT_FOUND")) == av.OUTCOME_REVIEW_REQUIRED
    assert av._classify_entity_outcome(bundle("REVIEW", "PASS")) == av.OUTCOME_REVIEW_REQUIRED
    # the established ordering is preserved around it
    assert av._classify_entity_outcome(bundle("CONFLICT", "REVIEW")) == av.OUTCOME_VERIFIED_WITH_DIFFERENCES
    assert av._classify_entity_outcome(bundle("NOT_FOUND", "PASS")) == av.OUTCOME_VERIFIED_WITH_DIFFERENCES
    assert av._classify_entity_outcome(bundle("PASS", "CORROBORATED")) == av.OUTCOME_VERIFIED
    assert av._classify_entity_outcome(bundle("NOT_FOUND", "NOT_FOUND")) == av.OUTCOME_NOT_FOUND


def test_name_conflict_is_never_derived_as_verified():
    """npi_outcome_from_evidence previously read any non-deactivated REVIEW as
    VERIFIED. A name-conflict REVIEW has no contract-reserved ledger rule yet,
    so it derives None ("nothing to record") - never a clean VERIFIED."""
    result = _identity_result("SYNTHETIC URGENT CARE ALPHA",
                              "SYNTHETIC NURSING OPERATIONS BETA LLC")
    bundle = {"dimensions": [result.to_dict()]}
    assert vf.npi_outcome_from_evidence(bundle) is None
    # the neighbouring outcomes still derive exactly as before
    ok = _identity_result("SYNTHETIC URGENT CARE ALPHA", "SYNTHETIC URGENT CARE ALPHA")
    assert vf.npi_outcome_from_evidence(
        {"dimensions": [ok.to_dict()]})["outcome"] == vf.VERIFIED
    gone = _identity_result("SYNTHETIC URGENT CARE ALPHA",
                            "SYNTHETIC NURSING OPERATIONS BETA LLC",
                            status="DEACTIVATED", deactivation_date="2024-01-31")
    assert vf.npi_outcome_from_evidence(
        {"dimensions": [gone.to_dict()]})["outcome"] == vf.NPI_DEACTIVATED
