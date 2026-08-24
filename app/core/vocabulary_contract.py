"""
The four vocabulary contract checks — the seam that had no test.

WHAT THESE PREVENT
──────────────────
A rule condition whose signal is never produced does not fail. It simply never
fires, and the rule set silently claims coverage it does not have. That happened:
`name_mismatch` was looked up under the wrong key for a full production run and
RULE-003 could only ever match on an address difference.

CHECK 1  every active-rule signal is registered, and each CONDITION's readiness
         is explicit
CHECK 2  every producible signal is consumed, or documented as unused
CHECK 3  no NEW cross-layer term collision
CHECK 4  no rule references an unregistered signal

WHY CHECK 4 IS THE ONLY STARTUP-FATAL ONE
Checks 1-3 range over code-defined vocabularies, which cannot change without a
deploy — a CI failure is the right feedback and a production crash over a naming
issue is not. Rules are DATABASE ROWS and can change without a deploy, so a rule
inserted referencing an unknown signal would reach production unexamined.

CHECK 4 SHIPS IN TWO STAGES, AND STARTS IN THE SAFE ONE
    Stage A   CI fatal, startup REPORT-ONLY          <- the default
    Stage B   CI fatal, startup fatal                <- requires the Azure
                                                        review_rules inventory
Shipping straight to Stage B would trade a silent-miss risk for an availability
risk: a production start failure over a rule row nobody has inventoried. Stage A
already detects and reports the condition on every boot.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.core.evidence_vocabulary import (
    ALLOWED_CROSS_LAYER_TERMS,
    CLASSIFIER_SIGNAL_REGISTRY,
    VOCABULARY_REGISTRY,
    ConsequenceState,
    Layer,
    ProductionState,
    SignalEntry,
    ValueDomain,
    is_signal_registered,
    signal_entry,
)

logger = logging.getLogger(__name__)

#: "report" (default) or "fatal". Stage B is opt-in and must stay opt-in until
#: the Azure dev/prod review_rules inventory proves no unregistered signal exists.
STARTUP_MODE_ENV = "VOCABULARY_CONTRACT_STARTUP_MODE"
STARTUP_MODE_REPORT = "report"
STARTUP_MODE_FATAL = "fatal"


def startup_mode() -> str:
    """Read fresh each call so a test can set it without reimporting."""
    mode = (os.getenv(STARTUP_MODE_ENV) or STARTUP_MODE_REPORT).strip().lower()
    return mode if mode in (STARTUP_MODE_REPORT, STARTUP_MODE_FATAL) else STARTUP_MODE_REPORT


class UnknownSignalReference(RuntimeError):
    """A rule references a signal that is not in the registry."""


class VocabularyContractViolation(RuntimeError):
    """A contract check failed."""


@dataclass(frozen=True)
class Violation:
    check: str
    subject: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.subject}: {self.detail}"


# ── condition-level readiness ────────────────────────────────────────────────

READY = "READY"
#: Produced somewhere, but not on every path that produces the signal. The
#: condition is live on some paths and dead on others — reported, never hidden.
VALUE_PARTIALLY_REACHABLE = "VALUE_PARTIALLY_REACHABLE"
#: The signal is produced, but no producer anywhere emits the required value.
VALUE_UNREACHABLE = "VALUE_UNREACHABLE"
PRODUCER_UNAVAILABLE = "PRODUCER_UNAVAILABLE"
METHODOLOGY_BLOCKED_STATUS = "METHODOLOGY_BLOCKED"
UNREGISTERED = "UNREGISTERED"

#: Statuses that mean the condition cannot fire everywhere it is evaluated.
NOT_FULLY_READY = frozenset({
    VALUE_PARTIALLY_REACHABLE, VALUE_UNREACHABLE,
    PRODUCER_UNAVAILABLE, METHODOLOGY_BLOCKED_STATUS, UNREGISTERED,
})


@dataclass(frozen=True)
class ConditionReadiness:
    """Readiness of ONE (rule, condition) pair — not of a signal.

    A signal-level verdict cannot express the real situation. `npi_validation`
    is produced as "flagged" on the RCE path, so RULE-001's
    `none_of {npi_validation: flagged}` is reachable — while RULE-005's
    `any_of {npi_validation: invalid}` is not, because no producer on that path
    emits "invalid". One signal, two conditions, two different answers.
    """

    rule_code: str
    rule_version: Optional[int]
    clause: str                 # all_of | any_of | none_of
    signal: str
    expected_value: Optional[str]
    signal_registered: bool
    producer_exists: bool
    value_reachable: bool
    reachable_on_paths: tuple
    unreachable_on_paths: tuple
    methodology_dependency: Optional[str]
    blocking_decision: Optional[str]
    status: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "clause": self.clause,
            "signal": self.signal,
            "expected_value": self.expected_value,
            "signal_registered": self.signal_registered,
            "producer_exists": self.producer_exists,
            "value_reachable": self.value_reachable,
            "reachable_on_paths": list(self.reachable_on_paths),
            "unreachable_on_paths": list(self.unreachable_on_paths),
            "methodology_dependency": self.methodology_dependency,
            "blocking_decision": self.blocking_decision,
            "status": self.status,
            "reason": self.reason,
        }


def _expected_value(cond: dict) -> Optional[str]:
    """The value a condition requires, whichever key expresses it."""
    for key in ("status", "severity"):
        if key in cond:
            value = cond[key]
            return value if isinstance(value, str) else str(value).lower()
    if "threshold" in cond:
        return f"<{cond['threshold']}"
    return None


def iter_field_conditions(rules: Sequence[dict]) -> Iterable[tuple]:
    """Yield (rule, clause, condition) for every `field` condition in every rule."""
    for rule in rules or ():
        for clause, conditions in (rule.get("conditions") or {}).items():
            if clause == "any_unavailable" or not isinstance(conditions, list):
                continue
            for cond in conditions:
                if isinstance(cond, dict) and "field" in cond:
                    yield rule, clause, cond


def condition_readiness(rules: Sequence[dict]) -> List[ConditionReadiness]:
    """Readiness for every (rule, condition) pair. The auditable answer."""
    out: List[ConditionReadiness] = []
    for rule, clause, cond in iter_field_conditions(rules):
        signal = cond["field"]
        expected = _expected_value(cond)
        entry: Optional[SignalEntry] = signal_entry(signal)

        if entry is None:
            out.append(ConditionReadiness(
                rule_code=rule.get("rule_code", "?"), rule_version=rule.get("version"),
                clause=clause, signal=signal, expected_value=expected,
                signal_registered=False, producer_exists=False, value_reachable=False,
                reachable_on_paths=(), unreachable_on_paths=(),
                methodology_dependency=None, blocking_decision=None,
                status=UNREGISTERED,
                reason=f"{signal!r} is not in the signal registry. A rule condition "
                       f"on an unregistered signal can never fire and nothing says so."))
            continue

        producer_exists = bool(entry.producers) and (
            entry.production_state is ProductionState.PRODUCIBLE)

        # REACHABILITY IS PER PATH, NOT PER SIGNAL.
        #
        # `npi_validation` is emitted as "flagged" on the RCE path and as
        # "valid"/"invalid" on the registry path. A union of values across paths
        # would report RULE-005's `invalid` condition READY, when on the RCE path
        # nothing can ever emit it. That is exactly the silent-never-fires failure
        # this contract exists to catch, so the paths are compared separately.
        producing = entry.producing_paths
        reachable_on = entry.paths_emitting(expected)
        unreachable_on = tuple(p for p in producing if p not in reachable_on)
        value_reachable = bool(reachable_on)

        methodology = None
        if entry.consequence_state is ConsequenceState.METHODOLOGY_PENDING:
            methodology = "consequence"
        if entry.value_domain is ValueDomain.UNRECONCILED:
            methodology = "value_domain" if methodology is None else "value_domain+consequence"

        if entry.production_state is ProductionState.METHODOLOGY_BLOCKED:
            status = METHODOLOGY_BLOCKED_STATUS
            reason = (f"production blocked pending {entry.blocking_decision or 'a decision'}"
                      f" — {entry.note[:110]}")
        elif entry.production_state is ProductionState.DECLARED_UNAVAILABLE:
            status = PRODUCER_UNAVAILABLE
            reason = f"no producer — {entry.note[:110]}"
        elif not value_reachable:
            status = VALUE_UNREACHABLE
            reason = (f"produced, but NO path emits {expected!r}; producers emit "
                      f"{list(entry.observed_values)}"
                      + (f" ({entry.blocking_decision})" if entry.blocking_decision else ""))
        elif unreachable_on:
            status = VALUE_PARTIALLY_REACHABLE
            reason = (f"{expected!r} is emitted on {list(reachable_on)} but NOT on "
                      f"{list(unreachable_on)}; this condition is dead on "
                      f"{list(unreachable_on)}"
                      + (f" ({entry.blocking_decision})" if entry.blocking_decision else ""))
        else:
            status = READY
            reason = (f"emitted on {list(reachable_on)} by "
                      f"{entry.producers[0].location if entry.producers else 'a registered producer'}")

        out.append(ConditionReadiness(
            rule_code=rule.get("rule_code", "?"), rule_version=rule.get("version"),
            clause=clause, signal=signal, expected_value=expected,
            signal_registered=True, producer_exists=producer_exists,
            value_reachable=value_reachable,
            reachable_on_paths=reachable_on, unreachable_on_paths=unreachable_on,
            methodology_dependency=methodology,
            blocking_decision=entry.blocking_decision, status=status, reason=reason))
    return out


# ── CHECK 1 — producer contract ──────────────────────────────────────────────

def check_1_classifier_signals_registered(rules: Sequence[dict]) -> List[Violation]:
    """Every active-rule signal must be REGISTERED. UNKNOWN is the only failure.

    A registered signal that is DECLARED_UNAVAILABLE or METHODOLOGY_BLOCKED is
    NOT a violation — it is a disclosed gap, and its dependent conditions are
    reported by `condition_readiness`. Creating a producer merely to satisfy this
    check would be inventing semantics, which is the thing the check exists to
    make visible.
    """
    return [
        Violation("CHECK_1", f"{r.rule_code}/{r.signal}", r.reason)
        for r in condition_readiness(rules) if r.status == UNREGISTERED
    ]


# ── CHECK 2 — consumer contract ──────────────────────────────────────────────

def check_2_produced_signals_consumed(rules: Sequence[dict]) -> List[Violation]:
    """Every PRODUCIBLE signal is consumed by a rule, or documents why not.

    Also detects the reverse direction of unknown vocabulary: a consumer value
    that no producer of that signal can emit is reported, so an unregistered
    consumer value cannot pass unnoticed.
    """
    violations: List[Violation] = []
    consumed = {cond["field"] for _rule, _clause, cond in iter_field_conditions(rules)}

    for name, entry in CLASSIFIER_SIGNAL_REGISTRY.items():
        if entry.production_state is not ProductionState.PRODUCIBLE:
            continue
        if name in consumed or entry.unused_reason:
            continue
        violations.append(Violation(
            "CHECK_2", name,
            "signal is PRODUCIBLE but no active rule consumes it, and no "
            "unused_reason is recorded. Either consume it or document why not."))

    # Unknown consumer VALUES: the condition wants something the signal's
    # declared domain cannot produce and the domain is settled.
    for _rule, _clause, cond in iter_field_conditions(rules):
        entry = signal_entry(cond["field"])
        if entry is None or entry.value_domain is not ValueDomain.SETTLED:
            continue
        expected = _expected_value(cond)
        if expected is not None and entry.observed_values and expected not in entry.observed_values:
            violations.append(Violation(
                "CHECK_2", f"{cond['field']}={expected}",
                f"consumer expects a value outside the SETTLED domain "
                f"{list(entry.observed_values)}"))
    return violations


# ── CHECK 3 — cross-layer contract ───────────────────────────────────────────

def check_3_no_cross_layer_collision() -> List[Violation]:
    """No NEW term may exist in two layers. Pre-1.0 collisions are grandfathered."""
    by_term: Dict[str, List[Layer]] = {}
    for (layer, term) in VOCABULARY_REGISTRY:
        by_term.setdefault(term, []).append(layer)

    violations: List[Violation] = []
    for term, layers in sorted(by_term.items()):
        if len(layers) < 2:
            continue
        allowed = ALLOWED_CROSS_LAYER_TERMS.get(term)
        if allowed is not None and set(layers) <= set(allowed):
            continue
        # Every colliding entry must be grandfathered by an explicit marker.
        ungrandfathered = [
            lyr for lyr in layers
            if (VOCABULARY_REGISTRY[(lyr, term)].since_version or "") != "pre-1.0"
        ]
        if ungrandfathered:
            violations.append(Violation(
                "CHECK_3", term,
                f"appears in {[l.value for l in sorted(layers, key=lambda x: x.value)]} "
                f"without a pre-1.0 grandfather marker or an "
                f"ALLOWED_CROSS_LAYER_TERMS entry. Qualify the new term instead "
                f"(e.g. Layer 1 uses NO_MATCH_OBSERVED, not NOT_FOUND)."))
    return violations


# ── CHECK 4 — classifier / rule contract ─────────────────────────────────────

def check_4_db_rules_reference_known_signals(rules: Sequence[dict]) -> List[Violation]:
    """No database rule may reference an unregistered signal.

    The one check that becomes startup-fatal, because `review_rules` rows change
    without a deploy.
    """
    return [
        Violation("CHECK_4", f"{r.rule_code} ({r.clause})",
                  f"unregistered signal {r.signal!r}")
        for r in condition_readiness(rules) if r.status == UNREGISTERED
    ]


def assert_db_rules_reference_known_signals(rules: Sequence[dict]) -> None:
    """CHECK 4, raising. Called from rule loading regardless of startup mode."""
    violations = check_4_db_rules_reference_known_signals(rules)
    if violations:
        raise UnknownSignalReference(
            "review_rules reference signals that are not in the vocabulary "
            "registry, so those conditions can never fire and nothing would say "
            "so:\n  " + "\n  ".join(str(v) for v in violations))


# ── aggregate ────────────────────────────────────────────────────────────────

def run_all_checks(rules: Sequence[dict]) -> Dict[str, List[Violation]]:
    return {
        "CHECK_1": check_1_classifier_signals_registered(rules),
        "CHECK_2": check_2_produced_signals_consumed(rules),
        "CHECK_3": check_3_no_cross_layer_collision(),
        "CHECK_4": check_4_db_rules_reference_known_signals(rules),
    }


def assert_vocabulary_contract(rules: Sequence[dict]) -> None:
    """All four checks, raising. For CI."""
    results = run_all_checks(rules)
    failed = [v for vs in results.values() for v in vs]
    if failed:
        raise VocabularyContractViolation(
            "vocabulary contract violated:\n  " + "\n  ".join(str(v) for v in failed))


def assert_vocabulary_contract_at_startup(rules: Sequence[dict]) -> Dict[str, Any]:
    """Startup entry point. Honours the two-stage mode; never raises in Stage A.

    Returns a summary so the caller can log or expose it. Reports readiness for
    every condition so a value-unreachable rule is visible on every boot rather
    than discovered when a determination silently fails to fire.
    """
    mode = startup_mode()
    results = run_all_checks(rules)
    readiness = condition_readiness(rules)
    not_ready = [r for r in readiness if r.status in NOT_FULLY_READY]

    for name, violations in results.items():
        for v in violations:
            logger.error("vocabulary contract %s", v)
    for r in not_ready:
        logger.warning(
            "rule condition not ready: %s %s %s=%s -> %s (%s)",
            r.rule_code, r.clause, r.signal, r.expected_value, r.status, r.reason)

    check_4 = results["CHECK_4"]
    if check_4:
        if mode == STARTUP_MODE_FATAL:
            raise UnknownSignalReference(
                "CHECK 4 (Stage B, startup-fatal): " +
                "; ".join(str(v) for v in check_4))
        logger.error(
            "CHECK 4 found %d unregistered signal reference(s). Startup mode is "
            "%r, so this is REPORTED and not fatal. Set %s=fatal only after the "
            "Azure dev/prod review_rules inventory confirms no unregistered "
            "signals exist.", len(check_4), mode, STARTUP_MODE_ENV)

    return {
        "mode": mode,
        "violations": {k: [str(v) for v in vs] for k, vs in results.items()},
        "conditions_total": len(readiness),
        "conditions_ready": len(readiness) - len(not_ready),
        "conditions_not_ready": [r.as_dict() for r in not_ready],
    }
