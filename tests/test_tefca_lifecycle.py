"""Entity lifecycle wiring: transitions, confidence scoring, NPI, audit.

The rules these cover were all implemented and unit-tested before this sprint —
and reachable from nothing. These assert the wiring: that the state machine
actually gates a status change, that a refusal is recorded rather than merely
returned, and that a confidence score distinguishes "no source agreed" from "no
source answered".
"""
import pytest

from app.services.npi_validator import make_valid_npi, validate_for_import
from app.tefca_registry import lifecycle
from app.tefca_registry import state_machine as sm

GATED = (401, 403)


# ── transitions ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("current,target,allowed", [
    (sm.DRAFT, sm.PENDING_VERIFICATION, True),
    (sm.PENDING_VERIFICATION, sm.ACTIVE, True),
    (sm.PENDING_VERIFICATION, sm.DRAFT, True),
    (sm.ACTIVE, sm.SUSPENDED, True),
    (sm.SUSPENDED, sm.ACTIVE, True),
    (sm.ACTIVE, sm.INACTIVE, True),
    (sm.DRAFT, sm.ACTIVE, False),
    (sm.INACTIVE, sm.ACTIVE, False),
])
def test_check_transition_matches_the_state_machine(current, target, allowed):
    ok, _msg = lifecycle.check_transition(current, target)
    assert ok is allowed


def test_refusal_names_the_missing_step_not_just_invalid():
    """'Invalid transition' tells an operator nothing. The message has to say
    what is missing and what IS reachable, or the 400 is a dead end."""
    ok, msg = lifecycle.check_transition(sm.DRAFT, sm.ACTIVE)
    assert not ok
    assert "draft" in msg and "active" in msg
    assert "verification" in msg.lower()
    assert sm.PENDING_VERIFICATION in msg


def test_terminal_state_says_so():
    ok, msg = lifecycle.check_transition(sm.INACTIVE, sm.ACTIVE)
    assert not ok
    assert "terminal" in msg.lower() or "re-registered" in msg.lower()


def test_every_valid_state_is_reachable_in_the_message_helper():
    for s in sm.VALID_STATES:
        assert isinstance(lifecycle.explain_refusal(s, sm.ACTIVE, ""), str)


# ── confidence scoring ───────────────────────────────────────────────────────

def test_weights_sum_to_one():
    assert round(sum(lifecycle.SOURCE_WEIGHTS.values()), 6) == 1.0


def test_all_sources_matching_scores_one():
    r = lifecycle.compute_confidence({k: True for k in lifecycle.SOURCE_WEIGHTS})
    assert r["confidence_score"] == 1.0
    assert r["coverage"] == 1.0


def test_no_source_answering_is_null_not_zero():
    """The distinction that matters: an outage everywhere must not read as
    'every source disagreed'. Null says we do not know."""
    r = lifecycle.compute_confidence({k: None for k in lifecycle.SOURCE_WEIGHTS})
    assert r["confidence_score"] is None
    assert r["coverage"] == 0.0
    assert "null rather than 0.0" in r["note"]


def test_all_sources_answering_and_disagreeing_scores_zero():
    r = lifecycle.compute_confidence({k: False for k in lifecycle.SOURCE_WEIGHTS})
    assert r["confidence_score"] == 0.0
    assert r["coverage"] == 1.0


def test_unavailable_source_shrinks_the_divisor_rather_than_penalising():
    """NPPES down + PECOS matched should not be scored as 0.20/1.00. Only the
    sources that answered are in the denominator."""
    results = {k: None for k in lifecycle.SOURCE_WEIGHTS}
    results["pecos"] = True
    r = lifecycle.compute_confidence(results)
    assert r["confidence_score"] == 1.0          # the one source that answered agreed
    assert r["coverage"] == 0.20                 # but coverage says how thin that is
    assert r["sources"]["nppes"]["status"] == "unavailable"
    assert r["sources"]["nppes"]["counted"] is False


def test_partial_coverage_is_reported_so_a_high_score_can_be_discounted():
    r = lifecycle.compute_confidence({"nppes": True, "pecos": False})
    assert r["weight_considered"] == 0.60
    assert r["weight_earned"] == 0.40
    assert r["confidence_score"] == round(0.40 / 0.60, 4)
    assert 0 < r["coverage"] < 1


def test_dict_form_available_false_is_treated_as_unavailable():
    r = lifecycle.compute_confidence({"nppes": {"available": False, "matched": True}})
    assert r["confidence_score"] is None


# ── NPI wiring ───────────────────────────────────────────────────────────────

def test_validator_flags_without_rejecting():
    r = validate_for_import("1234567890")
    assert r["npi_valid"] is False
    assert r["requires_review"] is True
    assert r["npi_validation_error"]


def test_valid_npi_needs_no_review():
    r = validate_for_import(make_valid_npi("200000001"))
    assert r["npi_valid"] is True
    assert r["requires_review"] is False


def test_registry_verification_uses_the_shared_validator():
    """verification.py carried its own copy of the Luhn check. Two
    implementations of one rule is one chance for them to disagree."""
    from app.tefca_registry.verification import _npi_valid
    good = make_valid_npi("300000001")
    assert _npi_valid(good) is True
    assert _npi_valid("1234567890") is False
    assert _npi_valid("") is False
    assert _npi_valid("abcdefghij") is False


def test_import_path_imports_the_validator():
    """Guards the wiring itself: the call site existed once without the import,
    which would only have failed at runtime on a bad NPI."""
    import app.tefca_registry.fhir_import as fi
    assert hasattr(fi, "validate_for_import")


# ── seed data ────────────────────────────────────────────────────────────────

def test_seed_csv_parses_and_carries_deliberate_bad_npis():
    from app.tefca_registry import dev_seed
    csv_text = dev_seed.build_csv()
    rows = [r for r in csv_text.strip().splitlines()[1:] if r.strip()]
    assert len(rows) >= 10, "seed should provide enough entities to demo"
    bad = [r for r in dev_seed._rows() if not validate_for_import(r["npi"])["npi_valid"]]
    assert len(bad) == 2, "two invalid NPIs are intentional fixtures"
    states = {r["state"] for r in dev_seed._rows()}
    assert {"VA", "MD", "NY", "CA", "TX"} <= states


def test_seed_covers_multiple_levels_and_statuses():
    from app.tefca_registry import dev_seed
    rows = dev_seed._rows()
    assert {"qhin", "participant", "sub_participant"} <= {r["level"] for r in rows}
    assert len({r["status"] for r in rows}) >= 4


# ── endpoint gating ──────────────────────────────────────────────────────────

def test_status_endpoint_refuses_anonymous(client):
    r = client.patch(
        "/api/tefca/registry/entities/00000000-0000-0000-0000-000000000000/status",
        json={"status": "active"})
    assert r.status_code in GATED


def test_seed_endpoint_refuses_anonymous(client):
    r = client.post("/api/tefca/registry/dev/seed")
    assert r.status_code in GATED


def test_verify_endpoint_refuses_anonymous(client):
    r = client.post(
        "/api/tefca/registry/entities/00000000-0000-0000-0000-000000000000/verify")
    assert r.status_code in GATED
