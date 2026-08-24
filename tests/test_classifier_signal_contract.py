"""
The contract between the evidence layer and the B1-B4 classifier.

WHY THIS FILE EXISTS
────────────────────
The evidence layer produces field-level observations; `arc_pipeline
.dimensions_to_verification_results` translates them into the SIGNAL vocabulary
that `review_rules` is written against. Those are two different namespaces and
nothing was checking that they line up.

They did not line up. `_dimension_identity` writes
`field_conflicts=[{"field": "legal_name", ...}]` and the translator asked for
`field == "name"`, so `name_mismatch` was emitted zero times across all 43
verified entities and RULE-003 could only ever fire on an address difference.
Nothing failed. No test covered the seam, the rule matched on its other
condition often enough to look alive, and the defect was invisible for a whole
production run.

WHAT THESE TESTS PIN, AND WHAT THEY DELIBERATELY DO NOT
───────────────────────────────────────────────────────
PINNED  the producer's field names and the translator's lookups are the same
        strings; every signal an active rule references is either produced or
        explicitly declared unproduced with a reason.

NOT PINNED  what the classifier DOES with any signal. Bucket assignment, tier
        routing and severity grading are methodology, owned by `review_rules`
        and by the COR, and no test here asserts an outcome. A test that pinned
        "name_mismatch means B2" would freeze a methodology decision that has
        not been made.

THE UNPRODUCED SIGNALS ARE PART OF THE CONTRACT
Four rule conditions have no producer on this path. That is recorded here as a
fact with a reason rather than left to be rediscovered, and
`test_unproduced_signals_are_declared_not_forgotten` fails if someone adds a
rule condition without either wiring it or declaring it.
"""

from __future__ import annotations

import pytest

from app.core.evidence_vocabulary import CLASSIFIER_SIGNAL_REGISTRY, PATH_RCE
from app.tefca_registry.rce.arc_pipeline import (
    EMITTED_FIELD_SIGNALS,
    IDENTITY_NAME_FIELD,
    dimensions_to_verification_results,
)
from app.tefca_registry.bucket_classifier import SEED_RULES_V2

# ── the producer side ────────────────────────────────────────────────────────

#: Field names `evidence_assembly` writes into `field_conflicts[].field`, per
#: dimension. Verified against the 1,984 persisted evidence rows from the
#: 43-entity run: IDENTITY:legal_name x92, ADDRESS:address x88,
#: TEFCA_ALIGNMENT:data_quality x64, TEFCA_ALIGNMENT:active_status x16.
#: The string "name" occurs zero times.
PRODUCER_CONFLICT_FIELDS = {
    "IDENTITY": {"legal_name"},
    "ADDRESS": {"address"},
    "TEFCA_ALIGNMENT": {"data_quality", "active_status"},
}

#: Rule conditions with no producer on the RCE path, and WHY.
#:
#: DERIVED FROM THE SHARED REGISTRY (B5 / E1), not restated here. This dict used
#: to hold its own copy of the reasons while `arc_pipeline` held its own copy of
#: the emitted list — so the producer, the consumer and this test could each be
#: correct about a different thing. `app.core.evidence_vocabulary` now holds one
#: entry per signal and everything reads it.
DECLARED_UNPRODUCED = {
    name: entry.note
    for name, entry in CLASSIFIER_SIGNAL_REGISTRY.items()
    if not any(p.path == PATH_RCE for p in entry.producers)
}


def _rule_field_conditions(rules):
    """Every distinct `field` name referenced by any clause of any rule."""
    names = set()
    for rule in rules:
        for clause, conditions in (rule.get("conditions") or {}).items():
            if clause == "any_unavailable":
                continue
            for condition in conditions:
                if "field" in condition:
                    names.add(condition["field"])
    return names


def _identity_evidence(conflict_field):
    """Minimal assembled-evidence structure carrying one D1 name conflict."""
    return {
        "dimensions": [{
            "dimension": "IDENTITY",
            "disposition": "PASS",
            "applicability": "REQUIRED",
            "evidence": [{
                "source": "NPPES",
                "disposition": "PASS",
                "rule_applied": "NPPES_PRIMARY_IDENTITY_AUTHORITY",
                "field_conflicts": [{
                    "field": conflict_field,
                    "submitted": "UTMB - Health",
                    "nppes": "THE UNIVERSITY OF TEXAS MEDICAL BRANCH",
                    "result": "DIFFERS",
                }],
            }],
        }],
        "data_quality_flags": [],
    }


# ── Step 1: the field-name contract ──────────────────────────────────────────

def test_evidence_field_names_match_classifier_contract():
    """Every field name the producer emits is one the translator looks for, and
    every field name the translator looks for is one the producer emits.

    This is the test that was missing. `legal_name` vs `name` would have failed
    it on the first run.
    """
    # The translator's IDENTITY lookup must name a field the producer emits.
    assert IDENTITY_NAME_FIELD in PRODUCER_CONFLICT_FIELDS["IDENTITY"], (
        f"arc_pipeline looks for field {IDENTITY_NAME_FIELD!r} in D1 conflicts, "
        f"but evidence_assembly emits {PRODUCER_CONFLICT_FIELDS['IDENTITY']}. "
        f"The signal would never fire.")

    # And it must be the specific field D1 actually writes.
    assert IDENTITY_NAME_FIELD == "legal_name", (
        "The D1 name-comparison field is `legal_name` — in fields_evaluated, in "
        "the NPPES and SAM connector payloads, in TEFCAEntity.legal_name_"
        "submitted, and in all 92 persisted conflict rows. Changing this "
        "constant without changing evidence_assembly breaks the signal again.")


def test_name_mismatch_signal_fires_on_the_field_the_producer_emits():
    """A D1 conflict on `legal_name` produces `name_mismatch`."""
    results = dimensions_to_verification_results(_identity_evidence("legal_name"))
    assert "name_mismatch" in results["fields"], (
        "A legal_name conflict must produce the name_mismatch signal — this is "
        "the defect that made RULE-003 unable to fire on a name difference.")


def test_name_mismatch_signal_does_not_fire_on_an_unrelated_conflict():
    """A conflict on some other field must NOT produce `name_mismatch`."""
    results = dimensions_to_verification_results(_identity_evidence("npi_type"))
    assert "name_mismatch" not in results["fields"]


def test_name_mismatch_would_not_fire_on_the_old_wrong_key():
    """Regression pin: the pre-fix lookup string must stay broken.

    If `name` ever starts producing the signal, the producer has been renamed
    and the persisted evidence no longer matches the code that reads it.
    """
    results = dimensions_to_verification_results(_identity_evidence("name"))
    assert "name_mismatch" not in results["fields"]


def test_signal_namespace_is_distinct_from_evidence_namespace():
    """`legal_name` is an EVIDENCE field; `name_mismatch` is a CLASSIFIER signal.

    Merging the two namespaces would either rename a signal the active rules
    reference by name, or rename a field 92 persisted rows already carry.
    """
    assert IDENTITY_NAME_FIELD not in EMITTED_FIELD_SIGNALS
    assert "name_mismatch" in EMITTED_FIELD_SIGNALS
    assert "name_mismatch" not in PRODUCER_CONFLICT_FIELDS["IDENTITY"]


# ── Step 2: signal coverage ──────────────────────────────────────────────────

def test_every_rule_field_condition_is_produced_or_declared_unproduced():
    """No active rule may reference a signal that is neither wired nor declared.

    A rule condition with no producer is not a rule — it is a branch that can
    never be taken, and the rule set silently claims coverage it does not have.
    """
    referenced = _rule_field_conditions(SEED_RULES_V2)
    accounted = set(EMITTED_FIELD_SIGNALS) | set(DECLARED_UNPRODUCED)
    unaccounted = referenced - accounted
    assert not unaccounted, (
        f"Rule conditions reference field signals that are neither emitted by "
        f"arc_pipeline nor declared unproduced: {sorted(unaccounted)}. Either "
        f"wire the signal or add it to DECLARED_UNPRODUCED with a reason.")


def test_unproduced_signals_are_declared_not_forgotten():
    """Each declared-unproduced signal carries a reason, and is really absent."""
    for signal, reason in DECLARED_UNPRODUCED.items():
        assert len(reason) > 40, f"{signal} needs a real reason, not a stub"
        assert signal not in EMITTED_FIELD_SIGNALS, (
            f"{signal} is declared unproduced but appears in "
            f"EMITTED_FIELD_SIGNALS — one of the two is now wrong.")


def test_emitted_signals_each_document_their_input():
    """Every emitted signal names the evidence it is derived from."""
    for signal, derivation in EMITTED_FIELD_SIGNALS.items():
        assert derivation and len(derivation) > 10, (
            f"{signal} must document what it is derived from")


def test_npi_validation_vocabulary_split_is_pinned():
    """RULE-005 wants `invalid`; this path can only emit `flagged`.

    Pinned as a KNOWN GAP, not asserted as correct. RULE-001/002/003 exclude on
    `flagged` and RULE-005 escalates on `invalid`, so the two vocabularies may
    be a deliberate two-level model or a drift — that is Decision D6. This test
    fails if the emitted vocabulary changes, forcing the question to be answered
    rather than silently resolved.
    """
    evidence = {
        "dimensions": [],
        "data_quality_flags": ["NPI_CHECK_DIGIT_FAILED"],
    }
    results = dimensions_to_verification_results(evidence)
    assert results["fields"]["npi_validation"] == {"status": "flagged"}

    rule_005 = next(r for r in SEED_RULES_V2 if r["rule_code"] == "RULE-005")
    wanted = {c.get("status") for c in rule_005["conditions"]["any_of"]
              if c.get("field") == "npi_validation"}
    assert wanted == {"invalid"}, (
        "RULE-005 escalates to B4 on npi_validation=invalid, which this path "
        "never emits. Recorded as Decision D6; do not 'fix' by changing either "
        "side without a methodology ruling.")


def test_b4_rule_conditions_have_no_reachable_producer_on_this_path():
    """RULE-005 (B4) cannot currently fire from the RCE path. Pinned, not fixed.

    All four of its conditions are unreachable here:
      oig_leie=excluded / sam_gov=excluded|debarred — no disposition in
        _DISPOSITION_TO_STATE maps to `excluded` or `debarred`, so a D3
        exclusion hit (which the evidence layer reports as REVIEW, deliberately
        "never an automatic rejection") arrives as `not_found`
      npi_validation=invalid — vocabulary split, see above
      required_verification_failed — no producer anywhere

    Whether a potential exclusion match should become an automatic B4 is
    Decision D7, not an engineering question: `evidence_dimensions
    .NEVER_AUTOMATIC` exists precisely to stop a lookup producing a
    determination on its own.
    """
    from app.tefca_registry.rce.arc_pipeline import _DISPOSITION_TO_STATE
    produced_states = set(_DISPOSITION_TO_STATE.values())
    assert "excluded" not in produced_states
    assert "debarred" not in produced_states
