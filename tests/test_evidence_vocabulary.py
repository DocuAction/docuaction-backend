"""
The canonical evidence vocabulary and its four contract checks (B5 / E1).

WHAT THESE PIN
──────────────
That the five semantic layers stay distinct, that no methodology decision is
encoded in the vocabulary, and that a rule condition which can never fire is
REPORTED rather than silently absent.

WHAT THEY DELIBERATELY DO NOT PIN
─────────────────────────────────
Any mapping from an observation to a bucket. `test_no_layer1_to_layer4_mapping_exists`
asserts such a mapping does NOT exist — a test that pinned one would freeze a
COR decision that has not been made.
"""

from __future__ import annotations

import os

import pytest

from app.core.evidence_vocabulary import (
    ALLOWED_CROSS_LAYER_TERMS,
    CLASSIFIER_SIGNAL_REGISTRY,
    EVIDENCE_VOCABULARY_VERSION,
    LAYER_1_STATES,
    LEGACY_VOCABULARY,
    PATH_RCE,
    PATH_REGISTRY,
    VOCABULARY_REGISTRY,
    ConsequenceState,
    Layer,
    ObservationState,
    ProductionState,
    Producer,
    TermStatus,
    ValueDomain,
    is_legacy_row,
    methodology_dependent_terms,
    registry_snapshot,
    signal_entry,
    term_entry,
    term_status,
    terms_for_layer,
    validate_observation_result,
    vocabulary_of,
)
from app.core.vocabulary_contract import (
    METHODOLOGY_BLOCKED_STATUS,
    PRODUCER_UNAVAILABLE,
    READY,
    STARTUP_MODE_ENV,
    STARTUP_MODE_FATAL,
    STARTUP_MODE_REPORT,
    UNREGISTERED,
    VALUE_PARTIALLY_REACHABLE,
    UnknownSignalReference,
    assert_db_rules_reference_known_signals,
    assert_vocabulary_contract_at_startup,
    check_1_classifier_signals_registered,
    check_2_produced_signals_consumed,
    check_3_no_cross_layer_collision,
    check_4_db_rules_reference_known_signals,
    condition_readiness,
    startup_mode,
)
from app.tefca_registry.bucket_classifier import SEED_RULES_V2

#: The eight canonical Layer 1 states, spelled out rather than derived, so a
#: silent addition or rename fails here.
EXPECTED_LAYER_1 = {
    "MATCH_OBSERVED", "NO_MATCH_OBSERVED", "MULTIPLE_MATCHES", "AMBIGUOUS",
    "SOURCE_UNAVAILABLE", "LOOKUP_NOT_APPLICABLE", "INSUFFICIENT_IDENTIFIER",
    "ERROR",
}


# ── vocabulary constants ─────────────────────────────────────────────────────

def test_vocabulary_constants_defined():
    assert EVIDENCE_VOCABULARY_VERSION == "1.0"
    assert LEGACY_VOCABULARY == "LEGACY"
    assert len(VOCABULARY_REGISTRY) > 0
    assert len(CLASSIFIER_SIGNAL_REGISTRY) > 0
    assert len(list(Layer)) == 5


def test_layer_1_states_complete():
    """Exactly eight canonical states, and NO_MATCH_OBSERVED is among them."""
    assert set(LAYER_1_STATES) == EXPECTED_LAYER_1
    assert "NO_MATCH_OBSERVED" in LAYER_1_STATES
    assert ObservationState.NO_MATCH_OBSERVED.value == "NO_MATCH_OBSERVED"
    # NOT_FOUND must NOT be introduced as a Layer 1 term.
    assert "NOT_FOUND" not in LAYER_1_STATES
    assert term_entry(Layer.L1_SOURCE_OBSERVATION, "NOT_FOUND") is None


def test_layer_2_signals_complete():
    """Every signal an active rule references is registered."""
    referenced = {
        cond["field"]
        for rule in SEED_RULES_V2
        for clause, conds in (rule.get("conditions") or {}).items()
        if clause != "any_unavailable"
        for cond in conds if isinstance(cond, dict) and "field" in cond
    }
    missing = referenced - set(CLASSIFIER_SIGNAL_REGISTRY)
    assert not missing, f"unregistered rule signals: {sorted(missing)}"


def test_layer_3_dispositions_classified():
    """Every Layer 3 term carries a status, and the dependent ones cite a decision."""
    l3 = terms_for_layer(Layer.L3_DIMENSION_DISPOSITION)
    for expected in ("PASS", "FAIL", "REVIEW", "NOT_APPLICABLE", "UNAVAILABLE",
                     "CORROBORATED", "CONFLICT", "INSUFFICIENT_EVIDENCE", "NOT_FOUND"):
        assert expected in l3, f"{expected} is not registered at Layer 3"
        entry = term_entry(Layer.L3_DIMENSION_DISPOSITION, expected)
        assert entry.status in set(TermStatus)
        if entry.status is TermStatus.METHODOLOGY_DEPENDENT:
            assert entry.blocking_decision, f"{expected} must name its decision"


def test_no_cross_layer_name_collision():
    assert check_3_no_cross_layer_collision() == []


def test_layer1_terms_introduce_no_new_collision():
    """No Layer 1 term appears in any other layer.

    This is why the Layer 1 names are qualified — NO_MATCH_OBSERVED rather than
    NOT_FOUND, SOURCE_UNAVAILABLE rather than UNAVAILABLE.
    """
    other_layers = [l for l in Layer if l is not Layer.L1_SOURCE_OBSERVATION]
    for state in LAYER_1_STATES:
        for layer in other_layers:
            assert term_entry(layer, state) is None, (
                f"Layer 1 term {state!r} also exists at {layer.value}; qualify it")


# ── the methodology boundary ─────────────────────────────────────────────────

def test_no_layer1_to_layer4_mapping_exists():
    """The vocabulary must contain NO observation -> bucket mapping.

    This is the executable form of "E1 encodes no methodology". If a future
    change adds a dict mapping an observation state to B1-B4, this fails.
    """
    import app.core.evidence_vocabulary as vocab

    buckets = {"B1", "B2", "B3", "B4"}
    for name in dir(vocab):
        if name.startswith("_"):
            continue
        obj = getattr(vocab, name)
        if not isinstance(obj, dict):
            continue
        for key, value in obj.items():
            key_s = key if isinstance(key, str) else str(key)
            if not any(state in key_s for state in EXPECTED_LAYER_1):
                continue
            rendered = str(value)
            assert not (buckets & set(rendered.split())), (
                f"{name}[{key!r}] maps a Layer 1 state to a bucket — that is a "
                f"methodology decision (D1-D9) and must not live in the vocabulary")


def test_methodology_dependent_values_not_promoted():
    """Every METHODOLOGY_DEPENDENT term names the decision that would settle it."""
    dependent = methodology_dependent_terms()
    assert dependent, "expected at least one methodology-dependent term"
    for (layer, term), decision in dependent.items():
        assert decision and decision != "unspecified", (
            f"{layer}:{term} is METHODOLOGY_DEPENDENT but names no decision")
        assert decision.startswith("D"), f"{layer}:{term} decision {decision!r}"


def test_observation_result_uses_canonical_vocabulary():
    for state in LAYER_1_STATES:
        assert validate_observation_result(state).value == state
    assert validate_observation_result(ObservationState.ERROR) is ObservationState.ERROR
    # Layer 3 dispositions and Layer 4 buckets must be refused.
    for wrong in ("PASS", "FAIL", "REVIEW", "B1", "B4", "not_found", "", None):
        with pytest.raises(ValueError):
            validate_observation_result(wrong)


# ── the three-axis signal registry ───────────────────────────────────────────

def test_npi_validation_is_producible_not_blocked():
    """npi_validation IS producible. Two producers exist and run.

    Guards against re-introducing the error the first registry draft made:
    marking the whole signal METHODOLOGY_BLOCKED, which asserts it cannot be
    produced. What is unsettled is the value domain and the consequence.
    """
    entry = signal_entry("npi_validation")
    assert entry is not None
    assert entry.production_state is ProductionState.PRODUCIBLE
    assert len(entry.producers) == 2
    assert {p.path for p in entry.producers} == {PATH_RCE, PATH_REGISTRY}
    assert entry.value_domain is ValueDomain.UNRECONCILED
    assert entry.consequence_state is ConsequenceState.METHODOLOGY_PENDING
    assert entry.blocking_decision == "D6"


def test_signal_production_and_consequence_are_independent():
    """A signal may be PRODUCIBLE while its consequence is METHODOLOGY_PENDING."""
    both = [
        name for name, e in CLASSIFIER_SIGNAL_REGISTRY.items()
        if e.production_state is ProductionState.PRODUCIBLE
        and e.consequence_state is ConsequenceState.METHODOLOGY_PENDING
    ]
    assert "npi_validation" in both, (
        "production and consequence have been collapsed back into one axis")
    # And the converse must remain expressible.
    for entry in CLASSIFIER_SIGNAL_REGISTRY.values():
        assert isinstance(entry.production_state, ProductionState)
        assert isinstance(entry.value_domain, ValueDomain)
        assert isinstance(entry.consequence_state, ConsequenceState)


def test_producers_record_their_path_and_values():
    """Values are per producer, never a flat union used for reachability."""
    entry = signal_entry("npi_validation")
    by_path = {p.path: set(p.emits) for p in entry.producers}
    assert by_path[PATH_RCE] == {"flagged"}
    assert by_path[PATH_REGISTRY] == {"valid", "invalid"}
    assert entry.paths_emitting("invalid") == (PATH_REGISTRY,)
    assert entry.paths_emitting("flagged") == (PATH_RCE,)


# ── condition-level readiness ────────────────────────────────────────────────

def test_rule_readiness_is_condition_level():
    """The same signal yields different verdicts in different conditions."""
    readiness = {(r.rule_code, r.signal, r.expected_value): r
                 for r in condition_readiness(SEED_RULES_V2)}

    flagged = readiness[("RULE-001", "npi_validation", "flagged")]
    assert PATH_RCE in flagged.reachable_on_paths
    assert PATH_REGISTRY in flagged.unreachable_on_paths

    invalid = readiness[("RULE-005", "npi_validation", "invalid")]
    assert PATH_REGISTRY in invalid.reachable_on_paths
    assert PATH_RCE in invalid.unreachable_on_paths, (
        "RULE-005 requires 'invalid', which the RCE path never emits. That must "
        "be surfaced, not hidden behind a union of values across paths.")
    assert invalid.status == VALUE_PARTIALLY_REACHABLE


def test_expected_value_reachability_is_reported_not_hidden():
    """No condition may be silently treated as if its signal did not exist."""
    for r in condition_readiness(SEED_RULES_V2):
        assert r.status in {
            READY, VALUE_PARTIALLY_REACHABLE, PRODUCER_UNAVAILABLE,
            METHODOLOGY_BLOCKED_STATUS, UNREGISTERED,
        }
        assert r.reason, f"{r.rule_code}/{r.signal} has no reason"
        if r.status != READY:
            assert r.reason.strip(), "a not-ready condition must explain itself"


def test_unproduced_signals_are_declared_with_a_reason():
    for name in ("taxonomy_mismatch", "required_verification_failed"):
        entry = signal_entry(name)
        assert entry.production_state is ProductionState.DECLARED_UNAVAILABLE
        assert len(entry.note) > 40, f"{name} needs a real reason"
    for name in ("confidence_below", "nppes_pecos_conflict", "multiple_source_conflict"):
        entry = signal_entry(name)
        assert entry.production_state is ProductionState.METHODOLOGY_BLOCKED
        assert entry.blocking_decision, f"{name} must name its blocking decision"


# ── the four checks ──────────────────────────────────────────────────────────

def test_classifier_signals_all_registered():
    assert check_1_classifier_signals_registered(SEED_RULES_V2) == []


def test_no_unknown_signal_references():
    assert check_4_db_rules_reference_known_signals(SEED_RULES_V2) == []


def test_evidence_signals_all_consumed_or_documented():
    assert check_2_produced_signals_consumed(SEED_RULES_V2) == []


def test_unknown_consumer_vocabulary_detected():
    """A consumer expecting a value outside a SETTLED domain is reported."""
    rogue = [{
        "rule_code": "RULE-TEST", "version": 1, "bucket": "B2", "priority": 99,
        "conditions": {"any_of": [{"field": "address_mismatch", "severity": "catastrophic"}]},
    }]
    violations = check_2_produced_signals_consumed(rogue + list(SEED_RULES_V2))
    assert any("catastrophic" in str(v) for v in violations)


def test_unknown_rule_signal_raises_at_startup():
    """CHECK 4 raises on an unregistered signal — the one startup-fatal check."""
    rogue = [{
        "rule_code": "RULE-BOGUS", "version": 1, "bucket": "B3", "priority": 99,
        "conditions": {"any_of": [{"field": "totally_made_up_signal", "status": True}]},
    }]
    assert check_4_db_rules_reference_known_signals(rogue)
    with pytest.raises(UnknownSignalReference) as exc:
        assert_db_rules_reference_known_signals(rogue)
    assert "totally_made_up_signal" in str(exc.value)


def test_new_cross_layer_collision_is_detected():
    """Introducing a term into a second layer without grandfathering fails."""
    import app.core.evidence_vocabulary as vocab

    key = (Layer.L1_SOURCE_OBSERVATION, "PASS")   # PASS already exists at Layer 3
    vocab.VOCABULARY_REGISTRY[key] = vocab.TermEntry(
        status=TermStatus.CURRENT_CANONICAL, meaning="deliberate collision")
    try:
        violations = check_3_no_cross_layer_collision()
        assert any(v.subject == "PASS" for v in violations)
    finally:
        del vocab.VOCABULARY_REGISTRY[key]
    assert check_3_no_cross_layer_collision() == []


# ── CHECK 4 two-stage rollout ────────────────────────────────────────────────

def test_check4_startup_mode_defaults_to_report():
    previous = os.environ.pop(STARTUP_MODE_ENV, None)
    try:
        assert startup_mode() == STARTUP_MODE_REPORT
    finally:
        if previous is not None:
            os.environ[STARTUP_MODE_ENV] = previous


def test_stage_b_requires_explicit_enablement():
    """Stage A reports and returns; Stage B raises. Default is Stage A."""
    rogue = [{
        "rule_code": "RULE-BOGUS", "version": 1, "bucket": "B3", "priority": 99,
        "conditions": {"any_of": [{"field": "unknown_signal_xyz", "status": True}]},
    }]
    previous = os.environ.pop(STARTUP_MODE_ENV, None)
    try:
        summary = assert_vocabulary_contract_at_startup(rogue)   # Stage A
        assert summary["mode"] == STARTUP_MODE_REPORT
        assert summary["violations"]["CHECK_4"]

        os.environ[STARTUP_MODE_ENV] = STARTUP_MODE_FATAL        # Stage B
        with pytest.raises(UnknownSignalReference):
            assert_vocabulary_contract_at_startup(rogue)
    finally:
        os.environ.pop(STARTUP_MODE_ENV, None)
        if previous is not None:
            os.environ[STARTUP_MODE_ENV] = previous


# ── versioning and legacy compatibility ──────────────────────────────────────

def test_historical_null_version_reads_as_legacy():
    assert vocabulary_of(None) == LEGACY_VOCABULARY
    assert is_legacy_row(None) is True
    assert vocabulary_of("1.0") == "1.0"
    assert is_legacy_row("1.0") is False


def test_new_rows_use_versioned_vocabulary():
    """The write-side constant is "1.0"; "LEGACY" is never a written value."""
    assert EVIDENCE_VOCABULARY_VERSION == "1.0"
    assert EVIDENCE_VOCABULARY_VERSION != LEGACY_VOCABULARY
    assert vocabulary_of(EVIDENCE_VOCABULARY_VERSION) == "1.0"


def test_vocabulary_version_recorded_on_the_evidence_model():
    """The model declares the column; reports are out of E1 scope."""
    from app.Tefca.models import TEFCADimensionEvidence

    col = TEFCADimensionEvidence.__table__.columns.get("vocabulary_version")
    assert col is not None, "vocabulary_version must be declared on the model"
    assert col.nullable is True, "must be nullable — NULL means LEGACY"
    assert col.server_default is None, (
        "no server_default: a default would rewrite the 1,984 historical rows "
        "and erase the LEGACY distinction")


def test_registry_snapshot_is_serialisable_metadata_only():
    """E1 exposes metadata for B3/B4 to consume. It renders nothing."""
    import json

    snap = registry_snapshot()
    json.dumps(snap)                       # must not raise
    assert snap["vocabulary_version"] == "1.0"
    assert set(snap["layer_1_states"]) == EXPECTED_LAYER_1
    assert "npi_validation" in snap["signals"]
    assert snap["signals"]["npi_validation"]["production_state"] == "PRODUCIBLE"
