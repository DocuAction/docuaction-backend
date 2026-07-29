"""TEFCA entity lifecycle enforcement (WF-002)."""
import pytest
from app.tefca_registry.state_machine import (validate_transition, assert_transition,
                                              allowed_targets, InvalidTransition,
                                              VALID_STATES)


def test_valid_transition_draft_to_pending():
    ok, reason = validate_transition("draft", "pending_verification")
    assert ok is True and reason == ""


def test_valid_transition_pending_to_active():
    assert validate_transition("pending_verification", "active")[0] is True


def test_valid_transition_pending_back_to_draft():
    """Verification failure returns the entity for correction."""
    assert validate_transition("pending_verification", "draft")[0] is True


def test_invalid_transition_draft_to_active():
    """Skipping verification is the transition this control exists to stop."""
    ok, reason = validate_transition("draft", "active")
    assert ok is False
    assert "verification" in reason.lower()


def test_invalid_transition_inactive_to_active():
    ok, reason = validate_transition("inactive", "active")
    assert ok is False
    assert "re-registered" in reason.lower()


def test_suspend_active_entity():
    assert validate_transition("active", "suspended")[0] is True


def test_reactivate_suspended_entity():
    assert validate_transition("suspended", "active")[0] is True


def test_active_to_inactive_allowed():
    assert validate_transition("active", "inactive")[0] is True


def test_inactive_is_terminal():
    assert allowed_targets("inactive") == []


def test_same_state_is_refused():
    ok, reason = validate_transition("active", "active")
    assert ok is False and "already" in reason


def test_unknown_target_state_refused():
    ok, reason = validate_transition("active", "banana")
    assert ok is False and "not a valid state" in reason


def test_unknown_current_state_refused():
    ok, reason = validate_transition("zombie", "active")
    assert ok is False and "unrecognised" in reason


def test_missing_state_refused():
    assert validate_transition(None, "active")[0] is False
    assert validate_transition("active", None)[0] is False


def test_case_and_whitespace_tolerated():
    assert validate_transition("  ACTIVE  ", "Suspended")[0] is True


def test_assert_transition_raises_with_both_states():
    with pytest.raises(InvalidTransition) as exc:
        assert_transition("draft", "active")
    assert exc.value.current == "draft" and exc.value.target == "active"


def test_state_set_matches_documented_lifecycle():
    assert VALID_STATES == {"draft", "pending_verification", "active",
                            "suspended", "inactive"}
