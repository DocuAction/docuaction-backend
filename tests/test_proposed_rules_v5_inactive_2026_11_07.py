"""The 2026-11-07 PROPOSED configuration (rule set v5 + flag-gated producers)
is implemented, previewable — and INACTIVE.

v5 STACKS ON v4: v4 (this PR's base branch, #200) adds RULE-006/007; v5
derives from it and adds only the D-02/D-03/D-04 guards, so the two proposals
share one lineage and can never define two different "version 4" rows.

Decision pack 2026-11-07: D-02 (flagged vs confirmed-invalid NPI), D-03
(FAILED is an answer, not an outage), D-04 (material conflict demotes B1/B2),
D-05 (name severity grading), D-12 (pinned assessment-as-of), D-15 (verify
skips HELD). Methodology package equivalents: D6, D4, D5.

Every test here protects one of two claims:
  1. DEFAULT-OFF: with no flag set and no v4 activation, behavior is
     byte-identical to today.
  2. PROPOSED BEHAVIOR: under the inactive configuration, each decision's
     recommended wording does exactly what it says — so the owner can approve
     the measured behavior, not a description of it.
"""

from __future__ import annotations

import pathlib
from datetime import date, timedelta

import pytest

from app.tefca_registry.bucket_classifier import (
    SEED_RULES_V3,
    SEED_RULES_V4,
    SEED_RULES_V5,
    BucketClassifier,
    ensure_rules_v5,
)
from app.tefca_registry.rce.arc_pipeline import (
    PROPOSED_NAME_MAJOR_BELOW_ENV,
    PROPOSED_NPI_INVALID_SPLIT_ENV,
    dimensions_to_verification_results,
)
from app.tefca_registry.rce.quality_engine import (
    PROPOSED_ASSESSMENT_AS_OF_ENV,
    _proposed_assessment_as_of,
)
from app.tefca_registry.review_cycle import (
    PROPOSED_VERIFY_SKIP_HELD_ENV,
    _proposed_verify_skip_held,
)
from rce_traceability_support import rolled_back_db  # noqa: F401 — DB fixture

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


def _by_code(rules):
    return {r["rule_code"]: r for r in rules}


def _classify(rules, results):
    return BucketClassifier(rules=[{**r, "version": 99} for r in rules]).classify(results)


# ── 1. the v4 delta is exactly the documented delta ──────────────────────────

def test_v5_delta_is_only_the_documented_guards():
    v4, v5 = _by_code(SEED_RULES_V4), _by_code(SEED_RULES_V5)
    assert set(v4) == set(v5)

    # RULE-004, RULE-005 and v4's own RULE-006/007 are untouched.
    for code in ("RULE-004", "RULE-005", "RULE-006", "RULE-007"):
        assert v4[code]["conditions"] == v5[code]["conditions"]

    def added_none_of(code):
        old = v4[code]["conditions"].get("none_of", [])
        new = v5[code]["conditions"].get("none_of", [])
        assert new[: len(old)] == old, f"{code}: v5 must only APPEND guards"
        return [c for c in new if c not in old]

    material = [
        {"field": "name_mismatch", "severity": "major"},
        {"field": "address_mismatch", "severity": "major"},
        {"field": "npi_validation", "status": "invalid"},
    ]
    assert added_none_of("RULE-001") == material
    assert added_none_of("RULE-002") == material + [
        {"source": "pecos", "status": "failed"},
        {"source": "sam_gov", "status": "failed"},
    ]
    assert added_none_of("RULE-003") == material

    # Positive clauses are untouched everywhere: v5 can only EXCLUDE, never
    # newly match something v4 would not have matched.
    for code in v4:
        for clause in ("all_of", "any_of", "any_unavailable"):
            assert (v4[code]["conditions"].get(clause)
                    == v5[code]["conditions"].get(clause)), (code, clause)


# ── 2. nothing activates v5 ──────────────────────────────────────────────────

def test_nothing_in_app_calls_ensure_rules_v5():
    """`ensure_rules_v5` must be invoked by NO application code path — running
    it is the governed activation step itself (Gate B/C). (v4 deliberately HAS
    one caller: the audited, confirm-literal admin activation route — that is
    #200's reviewed design and is out of scope here.)"""
    offenders = []
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if "ensure_rules_v5(" in text and py.name != "bucket_classifier.py":
            offenders.append(str(py))
    assert not offenders, f"ensure_rules_v5 must stay uncalled: {offenders}"


@pytest.mark.asyncio
async def test_ensure_rules_v5_refuses_without_activation_record():
    with pytest.raises(PermissionError):
        await ensure_rules_v5(None)
    with pytest.raises(PermissionError):
        await ensure_rules_v5(None, activation_record="   ")


# ── 3. proposed rule behavior, measured ──────────────────────────────────────

def _base_clean():
    return {"sources": {"nppes": "verified", "oig_leie": "clear",
                        "pecos": "verified"}, "fields": {}}


def test_d03_failed_mandatory_source_blocks_partial_pass():
    """v3: a FAILED PECOS still partial-passes B1 (FAILED counts as 'did not
    answer'). v4: the same input routes to B3 review."""
    results = {"sources": {"nppes": "verified", "oig_leie": "clear",
                           "pecos": "failed"}, "fields": {}}
    assert _classify(SEED_RULES_V3, results).bucket == "B1"
    out = _classify(SEED_RULES_V5, results)
    assert out.bucket == "B3"
    assert out.rule_code is None  # honest default, not a silent pass


def test_d04_major_name_conflict_demotes_b1():
    results = _base_clean()
    results["fields"]["name_mismatch"] = {"severity": "major"}
    assert _classify(SEED_RULES_V3, results).bucket == "B1"
    assert _classify(SEED_RULES_V5, results).bucket == "B3"


def test_d04_major_address_conflict_cannot_pass_as_b1_or_b2():
    """A minor name variance plus a MAJOR address conflict passes as B1 under
    v3 — RULE-001's first-match priority never consults the conflict signals
    at all (the precise D-04 defect). v4 routes it to review."""
    results = {"sources": {"nppes": "verified", "oig_leie": "clear",
                           "pecos": "verified"},
               "fields": {"name_mismatch": {"severity": "minor"},
                          "address_mismatch": {"severity": "major"}}}
    assert _classify(SEED_RULES_V3, results).bucket == "B1"
    assert _classify(SEED_RULES_V5, results).bucket == "B3"


def test_d02_invalid_npi_is_b4_under_both_rule_sets():
    """RULE-005 has carried the confirmed-invalid clause since v1; v5 adds only
    defense-in-depth guards. The change that makes it REACHABLE is the
    producer split (tested below), not a rule change."""
    results = _base_clean()
    results["fields"]["npi_validation"] = {"status": "invalid"}
    assert _classify(SEED_RULES_V3, results).bucket == "B4"
    assert _classify(SEED_RULES_V5, results).bucket == "B4"


EQUIVALENCE_BATTERY = [
    # (description, results) — no proposed signal present, so v3 == v4.
    ("all clean", _base_clean()),
    ("partial pass: pecos not_checked",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "not_checked"}, "fields": {}}),
    ("partial pass: pecos unavailable",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "unavailable"}, "fields": {}}),
    ("minor name variance",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "verified"},
      "fields": {"name_mismatch": {"severity": "minor"}}}),
    ("minor address variance",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "verified"},
      "fields": {"address_mismatch": {"severity": "minor"}}}),
    ("LEIE excluded",
     {"sources": {"nppes": "verified", "oig_leie": "excluded",
                  "pecos": "verified"}, "fields": {}}),
    ("pending exclusion review (not_found)",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "sam_gov": "not_found"}, "fields": {}}),
    ("NPPES not found",
     {"sources": {"nppes": "not_found", "oig_leie": "clear"}, "fields": {}}),
    ("flagged NPI",
     {"sources": {"nppes": "verified", "oig_leie": "clear",
                  "pecos": "verified"},
      "fields": {"npi_validation": {"status": "flagged"}}}),
    ("nothing checked", {"sources": {}, "fields": {}}),
]


@pytest.mark.parametrize("desc,results", EQUIVALENCE_BATTERY,
                         ids=[d for d, _ in EQUIVALENCE_BATTERY])
def test_v5_equals_v3_when_no_proposed_signal_is_present(desc, results):
    """v5 includes v4's RULE-006/007, but those are gated on treatment signals
    absent from every battery input — so the deployed v3 baseline must be
    reproduced exactly. This is the flags-off equivalence check."""
    v3 = _classify(SEED_RULES_V3, results)
    v5 = _classify(SEED_RULES_V5, results)
    assert (v3.bucket, v3.rule_code) == (v5.bucket, v5.rule_code), desc


def test_v5_name_major_guard_is_disclosed_not_hidden():
    """Until D-05 supplies a grading band, `name_mismatch == major` is
    value-unreachable on the active producer — and `condition_readiness` must
    SAY so for the v5 rows rather than reporting them ready."""
    from app.core.vocabulary_contract import READY, condition_readiness
    rows = [r for r in condition_readiness(SEED_RULES_V5)
            if r.signal == "name_mismatch" and r.expected_value == "major"]
    assert rows, "the v5 guard must be visible to readiness reporting"
    assert all(r.status != READY for r in rows)


# ── 4. proposed producers, flag-gated, default OFF ───────────────────────────

def _identity_evidence(similarity):
    return {"dimensions": [{
        "dimension": "IDENTITY", "disposition": "REVIEW", "applicability": "APPLICABLE",
        "evidence": [{
            "source": "NPPES", "disposition": "PASS",
            "field_conflicts": [{"field": "legal_name", "submitted": "A",
                                 "nppes": "B", "result": "DIFFERS",
                                 "similarity": similarity}],
        }],
    }]}


def test_d05_default_severity_stays_hardcoded_minor(monkeypatch):
    monkeypatch.delenv(PROPOSED_NAME_MAJOR_BELOW_ENV, raising=False)
    out = dimensions_to_verification_results(_identity_evidence(0.12))
    assert out["fields"]["name_mismatch"] == {"severity": "minor"}


def test_d05_band_from_env_grades_major_below_and_minor_at_or_above(monkeypatch):
    monkeypatch.setenv(PROPOSED_NAME_MAJOR_BELOW_ENV, "0.5")
    assert dimensions_to_verification_results(
        _identity_evidence(0.32))["fields"]["name_mismatch"] == {"severity": "major"}
    assert dimensions_to_verification_results(
        _identity_evidence(0.5))["fields"]["name_mismatch"] == {"severity": "minor"}
    # A conflict with no recorded similarity is never upgraded on a missing number.
    assert dimensions_to_verification_results(
        _identity_evidence(None))["fields"]["name_mismatch"] == {"severity": "minor"}


@pytest.mark.parametrize("bad", ["abc", "-0.1", "1.5", ""])
def test_d05_invalid_band_means_grading_stays_off(monkeypatch, bad):
    monkeypatch.setenv(PROPOSED_NAME_MAJOR_BELOW_ENV, bad)
    out = dimensions_to_verification_results(_identity_evidence(0.01))
    assert out["fields"]["name_mismatch"] == {"severity": "minor"}


def _structural_npi_evidence():
    return {"dimensions": [], "data_quality_flags": ["NPI_CHECK_DIGIT_FAILED"]}


def test_d02_default_structural_npi_stays_flagged(monkeypatch):
    monkeypatch.delenv(PROPOSED_NPI_INVALID_SPLIT_ENV, raising=False)
    out = dimensions_to_verification_results(_structural_npi_evidence())
    assert out["fields"]["npi_validation"] == {"status": "flagged"}


def test_d02_split_emits_invalid_for_structurally_impossible(monkeypatch):
    monkeypatch.setenv(PROPOSED_NPI_INVALID_SPLIT_ENV, "1")
    out = dimensions_to_verification_results(_structural_npi_evidence())
    assert out["fields"]["npi_validation"] == {"status": "invalid"}
    # ...which is exactly what makes RULE-005's v1 clause reachable:
    results = _base_clean()
    results["fields"].update(out["fields"])
    assert _classify(SEED_RULES_V5, results).bucket == "B4"


# ── 5. D-12 pinned assessment date — reader semantics ────────────────────────

def test_d12_env_reader_accepts_only_a_past_or_today_iso_date(monkeypatch):
    monkeypatch.delenv(PROPOSED_ASSESSMENT_AS_OF_ENV, raising=False)
    assert _proposed_assessment_as_of() is None

    monkeypatch.setenv(PROPOSED_ASSESSMENT_AS_OF_ENV, "2026-10-14")
    assert _proposed_assessment_as_of() == date(2026, 10, 14)

    monkeypatch.setenv(PROPOSED_ASSESSMENT_AS_OF_ENV, "not-a-date")
    assert _proposed_assessment_as_of() is None

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    monkeypatch.setenv(PROPOSED_ASSESSMENT_AS_OF_ENV, tomorrow)
    assert _proposed_assessment_as_of() is None


# ── 6. D-15 verify-skip-HELD — flag semantics ────────────────────────────────

def test_d15_flag_default_off(monkeypatch):
    monkeypatch.delenv(PROPOSED_VERIFY_SKIP_HELD_ENV, raising=False)
    assert _proposed_verify_skip_held() is False
    monkeypatch.setenv(PROPOSED_VERIFY_SKIP_HELD_ENV, "0")
    assert _proposed_verify_skip_held() is False
    monkeypatch.setenv(PROPOSED_VERIFY_SKIP_HELD_ENV, "1")
    assert _proposed_verify_skip_held() is True


# ── 7. D-15 HELD / re-HELD skip set — database semantics ─────────────────────

@pytest.mark.asyncio
async def test_d15_held_entity_ids_reads_live_record_status(rolled_back_db):
    """The skip set reads the CURRENT record_status: a promoted record is not
    in it; the same record re-HELD afterwards is; releasing is not simulated
    (holds are never released here)."""
    from test_post_promotion_verification import _promoted
    from app.tefca_registry.review_cycle import _held_entity_ids

    db = rolled_back_db
    rows, intake_id, job, curated, promo = await _promoted(db, "D15")
    entity_id = curated.canonical_entity_id
    assert curated.record_status in ("CLEAN", "CORRECTED")

    held = await _held_entity_ids(db, intake_id, [entity_id])
    assert held == set(), "a promoted, non-held record must not be skipped"

    # Re-HELD after promotion (what post-promotion verification can do).
    curated.record_status = "HELD"
    await db.flush()
    held = await _held_entity_ids(db, intake_id, [entity_id])
    assert held == {entity_id}, "a re-HELD record must be in the skip set"

    # Scoped to the delivery and to the asked-about ids.
    assert await _held_entity_ids(db, intake_id, []) == set()
    import uuid as _uuid
    assert await _held_entity_ids(db, _uuid.uuid4(), [entity_id]) == set()


# ── 8. D-02 bulk-path structural producer (pure; flag-gated at call site) ────

def test_structural_npi_signal_separates_np1_from_np3():
    from app.tefca_registry.rce.arc_pipeline import proposed_structural_npi_signal as f

    assert f(None) is None and f("") is None and f("   ") is None
    # NP-3: a multi-value CELL is flagged, never invalid — each contained NPI
    # may be individually valid and splitting would be an identity decision.
    multi = f("1780787176, 1770559767")
    assert multi == {"status": "flagged", "basis": "MULTIPLE_NPI_IN_ONE_FIELD",
                     "raw_npi": "1780787176, 1770559767"}
    # NP-1: structurally impossible single values are invalid.
    assert f("123")["status"] == "invalid"
    assert f("12345678ab")["status"] == "invalid"
    bad_luhn = f("1234567890")
    assert bad_luhn == {"status": "invalid", "basis": "NPI_CHECKSUM_INVALID",
                        "raw_npi": "1234567890"}
    # A valid NPI emits nothing (1234567893 is the canonical valid test NPI).
    assert f("1234567893") is None


def test_structural_npi_signal_agrees_with_the_quality_engine_validator():
    """Same canonical validator as NPI-003 — the two layers cannot disagree."""
    from app.services.npi_validator import validate_npi
    from app.tefca_registry.rce.arc_pipeline import proposed_structural_npi_signal as f

    for value in ("1234567893", "1234567890", "1780787176"):
        ok, _ = validate_npi(value)
        sig = f(value)
        assert (sig is None) == ok, value


# ── 9. Treatment reason codes are emitted by the producer, not a report ──────

def test_treatment_reason_codes_are_mutually_exclusive_and_producer_emitted():
    from datetime import date
    from app.tefca_registry.rce.npi_applicability import (
        active_treatment_missing_required_npi as sig)
    from app.tefca_registry.rce.quality_rules import BR_NPI_TRTMNT_EFFECTIVE

    def run(node_type):
        return sig("T-TRTMNT", None, "1",
                   assessment_date=date(2026, 11, 7),
                   requirement_effective=BR_NPI_TRTMNT_EFFECTIVE,
                   org_node_type=node_type)

    assert run("initiating-node")["reason_code"] == "TREATMENT_NPI_MISSING_B4_CANDIDATE"
    assert run(None)["reason_code"] == "TREATMENT_APPLICABILITY_PRESUMED_NPI_MISSING"
    assert run("")["reason_code"] == "TREATMENT_APPLICABILITY_PRESUMED_NPI_MISSING"
    assert run("no-node")["reason_code"] == "TREATMENT_NODE_TYPE_REVIEW"
    assert run("passthrough-node")["reason_code"] == "TREATMENT_NODE_TYPE_REVIEW"
    # Out of scope entirely: no signal, no code.
    assert sig("T-TREAT", None, "1", assessment_date=date(2026, 11, 7),
               requirement_effective=BR_NPI_TRTMNT_EFFECTIVE,
               org_node_type="initiating-node") is None
