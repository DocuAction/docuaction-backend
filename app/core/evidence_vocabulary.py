"""
The canonical evidence vocabulary — five semantic layers, one registry.

WHY THIS MODULE EXISTS
──────────────────────
The evidence layer produced a field-level observation under one name while the
classifier looked for another, and nothing detected it for a full production run
(`legal_name` vs `name`). That was one instance of a general condition: this
system carries four overlapping vocabularies and had no rule about which layer
owns which term.

This module is that rule, made executable.

WHY IT LIVES IN app/core/
─────────────────────────
`app/Tefca/__init__.py` eagerly imports routes, connectors, validation_engine and
mock_data. Any module placed under `app/Tefca/` drags the entire legacy stack into
whatever imports it — and `bucket_classifier` currently has ZERO `app.*`
module-level imports, while `arc_pipeline` defers every `app.Tefca` import into a
function body on purpose. `app/core/__init__.py` is empty, so this module is
importable from `app/Tefca/`, `app/tefca_registry/` and `app/tefca_registry/rce/`
with no side effect and no cycle.

It therefore imports nothing from either domain package, and must not start.

WHAT THIS MODULE DOES NOT DO — AND THE REASON IS THE WHOLE POINT
────────────────────────────────────────────────────────────────
There is NO mapping here from a Layer 1 observation to a Layer 3 disposition or
to a Layer 4 bucket. Not a dict, not a function, not a default.

    MATCH_OBSERVED      does not mean B1
    NO_MATCH_OBSERVED   does not mean B3 or B4
    SOURCE_UNAVAILABLE  does not mean B3 or B4
    CONFLICT            does not mean B2
    INVALID             does not mean B4
    FLAGGED             does not mean any particular bucket

Those are METHODOLOGY decisions (D1-D9), owned by the COR and expressed in
`review_rules`. A vocabulary that encoded them would be a classifier wearing a
dictionary's clothes. `test_no_layer1_to_layer4_mapping_exists` asserts the
absence rather than trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple

# ── version ──────────────────────────────────────────────────────────────────

#: Bump MAJOR on any rename, any semantic change, or any new Layer 1 state.
#: Bump MINOR on a new Layer 2 signal (interpretation only).
#: Historical rows are NEVER rewritten — see `vocabulary_of`.
EVIDENCE_VOCABULARY_VERSION = "1.0"

#: Derived at read time for rows written before versioning existed. This value
#: is NEVER written to the database; a NULL column means exactly this and
#: backfilling it would destroy the distinction it records.
LEGACY_VOCABULARY = "LEGACY"


class Layer(str, Enum):
    """The five semantic layers. Each owns its own vocabulary."""

    L1_SOURCE_OBSERVATION = "LAYER_1"      # what the external source returned
    L2_EVIDENCE_INTERPRETATION = "LAYER_2"  # what it means for this entity
    L3_DIMENSION_DISPOSITION = "LAYER_3"   # what D1-D6 concluded
    L4_VERIFICATION_RESULT = "LAYER_4"     # the B1-B4 classification
    L5_HUMAN_DETERMINATION = "LAYER_5"     # the analyst / QA decision


# ── LAYER 1 — source observation ─────────────────────────────────────────────

class ObservationState(str, Enum):
    """What a source actually said. Eight states, none of which is a verdict.

    EVERY NAME IS DELIBERATELY QUALIFIED.
    `NO_MATCH_OBSERVED` rather than `NOT_FOUND`; `SOURCE_UNAVAILABLE` rather than
    `UNAVAILABLE`; `LOOKUP_NOT_APPLICABLE` rather than `NOT_APPLICABLE`. Those
    three bare names already exist at Layer 3 and in the Layer 2 comparison
    vocabulary, carrying related but distinct meanings. Qualifying the Layer 1
    names means this module introduces ZERO new cross-layer collisions — which
    `test_layer1_terms_introduce_no_new_collision` checks rather than assumes.
    """

    #: Source answered; exactly one record matched at a stated match level.
    MATCH_OBSERVED = "MATCH_OBSERVED"
    #: Source answered; no record matched. A real, informative NEGATIVE — and a
    #: positive fact that must be recorded, not an absent row.
    NO_MATCH_OBSERVED = "NO_MATCH_OBSERVED"
    #: Source answered; more than one record matched. Cardinality, not fraud.
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
    #: Matched only on supporting evidence; no decisive identifier, no human.
    AMBIGUOUS = "AMBIGUOUS"
    #: The source did not answer. A fact about the world, NOT about the entity.
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    #: The lookup does not apply to this entity.
    LOOKUP_NOT_APPLICABLE = "LOOKUP_NOT_APPLICABLE"
    #: We lacked the key the lookup requires.
    INSUFFICIENT_IDENTIFIER = "INSUFFICIENT_IDENTIFIER"
    #: OUR code failed, not the source.
    #:
    #: Distinct from SOURCE_UNAVAILABLE, and the distinction has already cost
    #: this system once: an organisation-level exclusion lookup was wired with a
    #: missing argument and failed silently for every NPI-less entity,
    #: indistinguishable from an outage. An outage is news about the world; a
    #: TypeError is a bug, and recording them as the same thing means the bug is
    #: filed forever as somebody else's downtime.
    ERROR = "ERROR"


LAYER_1_STATES: Tuple[str, ...] = tuple(s.value for s in ObservationState)


def validate_observation_result(value: object) -> ObservationState:
    """Coerce to a canonical Layer 1 state, or refuse.

    Refuses Layer 3 dispositions and Layer 4 buckets explicitly, because those
    are the values most likely to be passed here by mistake.
    """
    if isinstance(value, ObservationState):
        return value
    try:
        return ObservationState(value)
    except (ValueError, KeyError):
        raise ValueError(
            f"{value!r} is not a canonical Layer 1 observation state. "
            f"Valid states: {', '.join(LAYER_1_STATES)}. "
            f"Note that PASS/FAIL/REVIEW are Layer 3 dispositions and B1-B4 are "
            f"Layer 4 results; neither belongs in an observation field."
        ) from None


# ── term registry ────────────────────────────────────────────────────────────

class TermStatus(str, Enum):
    """How settled a vocabulary term is."""

    CURRENT_CANONICAL = "CURRENT_CANONICAL"
    LEGACY = "LEGACY"
    PROPOSED = "PROPOSED"
    #: Meaning depends on an unresolved COR decision. NEVER promote one of these
    #: to CURRENT_CANONICAL without recording the decision that settled it.
    METHODOLOGY_DEPENDENT = "METHODOLOGY_DEPENDENT"


@dataclass(frozen=True)
class TermEntry:
    status: TermStatus
    meaning: str
    #: "D2", "D4", … when status is METHODOLOGY_DEPENDENT.
    blocking_decision: Optional[str] = None
    #: Set for terms that already existed in more than one layer before v1.0.
    since_version: Optional[str] = None
    #: Where the term is defined in code.
    defined_at: Optional[str] = None


def _t(status, meaning, **kw):
    return TermEntry(status=status, meaning=meaning, **kw)


CC = TermStatus.CURRENT_CANONICAL
LG = TermStatus.LEGACY
PR = TermStatus.PROPOSED
MD = TermStatus.METHODOLOGY_DEPENDENT

L1 = Layer.L1_SOURCE_OBSERVATION
L2 = Layer.L2_EVIDENCE_INTERPRETATION
L3 = Layer.L3_DIMENSION_DISPOSITION
L4 = Layer.L4_VERIFICATION_RESULT
L5 = Layer.L5_HUMAN_DETERMINATION


VOCABULARY_REGISTRY: Dict[Tuple[Layer, str], TermEntry] = {
    # ── Layer 1 ──────────────────────────────────────────────────────────────
    (L1, "MATCH_OBSERVED"): _t(CC, "Source answered; exactly one record matched."),
    (L1, "NO_MATCH_OBSERVED"): _t(CC, "Source answered; no record matched."),
    (L1, "MULTIPLE_MATCHES"): _t(CC, "Source answered; more than one match."),
    (L1, "AMBIGUOUS"): _t(CC, "Matched only on supporting evidence."),
    (L1, "SOURCE_UNAVAILABLE"): _t(CC, "The source did not answer."),
    (L1, "LOOKUP_NOT_APPLICABLE"): _t(CC, "The lookup does not apply here."),
    (L1, "INSUFFICIENT_IDENTIFIER"): _t(CC, "No usable key for the lookup."),
    (L1, "ERROR"): _t(CC, "Our code failed, not the source."),

    # ── Layer 2 — field-level signals consumed by the B1-B4 rules ────────────
    (L2, "name_mismatch"): _t(
        MD, "Organisation name differs between the RCE submission and NPPES.",
        blocking_decision="D5", defined_at="arc_pipeline.py:232"),
    (L2, "address_mismatch"): _t(
        CC, "Submitted address differs from an authoritative source.",
        defined_at="arc_pipeline.py:193"),
    (L2, "npi_validation"): _t(
        MD, "Identifier quality state for the entity's NPI.",
        blocking_decision="D6", defined_at="arc_pipeline.py:236 / review_service.py:248"),
    (L2, "taxonomy_mismatch"): _t(
        PR, "Provider taxonomy differs. No producer — the RCE delivery carries "
            "no NUCC taxonomy, so there is no second operand."),
    (L2, "confidence_below"): _t(
        MD, "Composite confidence below a threshold.", blocking_decision="D2"),
    (L2, "nppes_pecos_conflict"): _t(
        MD, "NPPES and the enrolment source disagree.", blocking_decision="D1"),
    (L2, "multiple_source_conflict"): _t(
        MD, "Two sources that both answered contradict each other.",
        blocking_decision="D1"),
    (L2, "required_verification_failed"): _t(
        PR, "A required verification failed. No producer anywhere."),

    # ── Layer 2 — the address COMPARISON vocabulary ──────────────────────────
    #
    # Registered, not renamed. These terms are correct and clear inside
    # address_evidence.py; three of the five collide by name with Layer 3 or the
    # Layer 4 source states, and 252 rows already carry MATCH or PARTIAL_MATCH.
    # Renaming them would invalidate persisted evidence to tidy a namespace.
    (L2, "MATCH"): _t(LG, "Addresses agree after normalisation.",
                      since_version="pre-1.0", defined_at="address_evidence.py:55"),
    (L2, "PARTIAL_MATCH"): _t(LG, "Same locality, differing street line.",
                              since_version="pre-1.0", defined_at="address_evidence.py:56"),
    (L2, "CONFLICT"): _t(LG, "Materially different address.",
                         since_version="pre-1.0", defined_at="address_evidence.py:57"),
    (L2, "NOT_FOUND"): _t(LG, "No address held by this source.",
                          since_version="pre-1.0", defined_at="address_evidence.py:58"),
    (L2, "UNAVAILABLE"): _t(LG, "Address source did not answer.",
                            since_version="pre-1.0", defined_at="address_evidence.py:59"),

    # ── Layer 3 — dimension disposition ──────────────────────────────────────
    (L3, "PASS"): _t(CC, "The dimension's requirement is satisfied.",
                     defined_at="evidence_dimensions.py:69"),
    (L3, "REVIEW"): _t(CC, "A human must decide.", defined_at="evidence_dimensions.py:71"),
    (L3, "NOT_APPLICABLE"): _t(CC, "The dimension does not apply to this entity.",
                               defined_at="evidence_dimensions.py:73"),
    (L3, "CORROBORATED"): _t(CC, "Supplemental evidence agrees.",
                             defined_at="evidence_dimensions.py:77"),
    (L3, "CONFLICT"): _t(CC, "Supplemental evidence disagrees.",
                         since_version="pre-1.0", defined_at="evidence_dimensions.py:78"),
    (L3, "UNAVAILABLE"): _t(
        MD, "The dimension could not be evaluated because a source did not answer.",
        blocking_decision="D4", since_version="pre-1.0",
        defined_at="evidence_dimensions.py:74"),
    (L3, "NOT_FOUND"): _t(
        LG, "No record was found. LEGACY: duplicates a Layer 1 concept under a "
            "Layer 3 name; the canonical Layer 1 expression is NO_MATCH_OBSERVED. "
            "Not renamed — 504 item rows and 256 dimension rows carry it.",
        blocking_decision="D2", since_version="pre-1.0",
        defined_at="evidence_dimensions.py:79"),
    (L3, "FAIL"): _t(
        MD, "The dimension's requirement is not satisfied. Defined but NEVER "
            "produced; NEVER_AUTOMATIC forbids reaching it from a lookup alone.",
        blocking_decision="D1", defined_at="evidence_dimensions.py:70"),
    (L3, "INSUFFICIENT_EVIDENCE"): _t(
        PR, "Not enough evidence to decide. Defined, never produced.",
        defined_at="evidence_dimensions.py:80"),

    # ── Layer 3 — the APPLICABILITY vocabulary (its own axis) ────────────────
    (L3, "REQUIRED"): _t(CC, "Methodology requires this dimension for this entity.",
                         defined_at="evidence_dimensions.py:99"),
    (L3, "CORROBORATIVE"): _t(CC, "Useful if present; cannot fail the entity.",
                              defined_at="evidence_dimensions.py:100"),

    # ── Layer 4 — verification result ────────────────────────────────────────
    (L4, "B1"): _t(CC, "No discrepancy found against the evidence gathered."),
    (L4, "B2"): _t(CC, "Minor or administrative discrepancy."),
    (L4, "B3"): _t(CC, "Inexplicable; requires manual examination."),
    (L4, "B4"): _t(CC, "Non-compliant."),
    (L4, "UNDETERMINED"): _t(
        MD, "Reserved. Exists only if D2 Option C is approved. NOT in the "
            "classifier's output domain.", blocking_decision="D2"),

    # ── Layer 4 — the SOURCE STATE vocabulary (lowercase, distinct) ──────────
    (L4, "verified"): _t(CC, "Source confirmed the entity.",
                         defined_at="bucket_classifier.py:37"),
    (L4, "not_found"): _t(CC, "Source answered; no record.",
                          since_version="pre-1.0", defined_at="bucket_classifier.py:38"),
    (L4, "not_checked"): _t(CC, "Source was not consulted.",
                            defined_at="bucket_classifier.py:39"),
    (L4, "unavailable"): _t(CC, "Source did not answer.",
                            since_version="pre-1.0", defined_at="bucket_classifier.py:40"),
    (L4, "failed"): _t(CC, "Source lookup failed.", defined_at="bucket_classifier.py:41"),

    # ── Layer 5 — RESERVED. Owned by B2 (QA gate); NOT implemented in E1 ─────
    #
    # Registered so cross-layer collision detection covers them. E1 defines no
    # behaviour, no producer and no consumer at Layer 5.
    (L5, "CONFIRM"): _t(PR, "Determination state: the system classification stands."),
    (L5, "RECLASSIFY"): _t(PR, "Determination state: a different bucket is determined."),
    (L5, "APPROVE"): _t(PR, "Review action: QA approves."),
    (L5, "RETURN"): _t(PR, "Review action: QA returns for correction."),
    (L5, "ESCALATE"): _t(PR, "Review action: QA escalates."),
    (L5, "ANALYST_DETERMINATION"): _t(PR, "Decision event type."),
    (L5, "QA_REVIEW"): _t(PR, "Decision event type."),
    (L5, "SUPERSEDING_DETERMINATION"): _t(
        PR, "Decision event type. A superseding determination is a NEW event, "
            "never a MODIFY of the original — which is why MODIFY is not "
            "registered."),
}


#: Terms that already existed in more than one layer BEFORE v1.0.
#:
#: Grandfathered, with a stated meaning per layer. E1 renames none of them: 504
#: NOT_FOUND, 268 UNAVAILABLE and 252 MATCH/PARTIAL_MATCH item rows are
#: persisted. CHECK 3 fails on any NEW collision, not on these.
ALLOWED_CROSS_LAYER_TERMS: Dict[str, Tuple[Layer, ...]] = {
    "NOT_FOUND": (L2, L3),
    "UNAVAILABLE": (L2, L3),
    "CONFLICT": (L2, L3),
    "NOT_APPLICABLE": (L3,),
}

#: Layer 5 names are reserved by B2 and must not be reused by another layer.
RESERVED_LAYER_5_TERMS: FrozenSet[str] = frozenset(
    term for (layer, term) in VOCABULARY_REGISTRY if layer is L5)


# ── the three-axis signal registry ───────────────────────────────────────────

class ProductionState(str, Enum):
    """CAN the signal be emitted? An engineering question."""

    #: A producer exists, is implemented, and runs.
    PRODUCIBLE = "PRODUCIBLE"
    #: The signal is understood but its operands are not available.
    DECLARED_UNAVAILABLE = "DECLARED_UNAVAILABLE"
    #: Producing it at all would require inventing a semantic.
    METHODOLOGY_BLOCKED = "METHODOLOGY_BLOCKED"


class ValueDomain(str, Enum):
    """WHAT values may it take, and is that settled?"""

    SETTLED = "SETTLED"
    #: Producers disagree, or the value set is not approved.
    UNRECONCILED = "UNRECONCILED"
    UNDEFINED = "UNDEFINED"


class ConsequenceState(str, Enum):
    """WHAT does it mean for B1-B4? A methodology question."""

    DEFINED_IN_RULES = "DEFINED_IN_RULES"
    METHODOLOGY_PENDING = "METHODOLOGY_PENDING"


#: The two evaluation paths that reach the classifier. A signal may be produced
#: on one and not the other, which is why a producer records which path it is on.
PATH_RCE = "rce"            # arc_pipeline.dimensions_to_verification_results
PATH_REGISTRY = "registry"  # review_service._derived_fields


@dataclass(frozen=True)
class Producer:
    """One producer of one signal, on one evaluation path.

    PRODUCERS ARE PER-PATH, AND THE VALUES THEY EMIT ARE PER-PRODUCER.
    A flat union of observed values across paths hides the case this registry
    exists to surface: `npi_validation` is emitted as "flagged" on the RCE path
    and as "valid"/"invalid" on the registry path, so a rule condition requiring
    "invalid" is reachable on one path and dead on the other. Unioning the values
    would report that condition READY, which is precisely the silent-never-fires
    failure the contract is meant to catch.
    """

    path: str
    location: str
    emits: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SignalEntry:
    """Three ORTHOGONAL facts about one classifier signal.

    Kept separate because collapsing them is precisely the error the first
    version of this registry made: `npi_validation` was marked wholly
    METHODOLOGY_BLOCKED, which asserts it cannot be produced. Two producers
    exist and work. What is undecided is the value domain and the B1-B4
    consequence — neither of which is a statement about producibility.
    """

    production_state: ProductionState
    value_domain: ValueDomain
    consequence_state: ConsequenceState
    producers: Tuple[Producer, ...] = ()
    blocking_decision: Optional[str] = None
    note: str = ""
    #: Set only when production_state is PRODUCIBLE and no rule consumes it.
    unused_reason: Optional[str] = None

    @property
    def observed_values(self) -> Tuple[str, ...]:
        """Union across producers. For DISPLAY only — never for reachability."""
        seen: list = []
        for p in self.producers:
            for v in p.emits:
                if v not in seen:
                    seen.append(v)
        return tuple(seen)

    @property
    def producing_paths(self) -> Tuple[str, ...]:
        return tuple(sorted({p.path for p in self.producers}))

    def paths_emitting(self, value: Optional[str]) -> Tuple[str, ...]:
        """Which paths can emit this value. Empty means the value is dead."""
        if value is None:
            return self.producing_paths
        return tuple(sorted({p.path for p in self.producers if value in p.emits}))


PRODUCIBLE = ProductionState.PRODUCIBLE
DECLARED_UNAVAILABLE = ProductionState.DECLARED_UNAVAILABLE
METHODOLOGY_BLOCKED = ProductionState.METHODOLOGY_BLOCKED

SETTLED = ValueDomain.SETTLED
UNRECONCILED = ValueDomain.UNRECONCILED
UNDEFINED = ValueDomain.UNDEFINED

DEFINED_IN_RULES = ConsequenceState.DEFINED_IN_RULES
METHODOLOGY_PENDING = ConsequenceState.METHODOLOGY_PENDING


CLASSIFIER_SIGNAL_REGISTRY: Dict[str, SignalEntry] = {
    "address_mismatch": SignalEntry(
        production_state=PRODUCIBLE,
        value_domain=SETTLED,
        consequence_state=DEFINED_IN_RULES,
        producers=(
            Producer(PATH_RCE, "app/tefca_registry/rce/arc_pipeline.py:193",
                     ("minor", "major")),
        ),
        note="Derived from the D4 dimension disposition plus a PARTIAL_MATCH scan.",
    ),
    "name_mismatch": SignalEntry(
        production_state=PRODUCIBLE,
        value_domain=UNRECONCILED,
        consequence_state=DEFINED_IN_RULES,
        producers=(
            Producer(PATH_RCE, "app/tefca_registry/rce/arc_pipeline.py:232",
                     ("minor",)),
        ),
        blocking_decision="D5",
        note="Producible and consumed by RULE-003. Only 'minor' is ever emitted: "
             "the severity is a hardcoded constant because the grading scale "
             "(NAME_EXACT .. NAME_AMBIGUOUS) is unapproved. D5.",
    ),
    "npi_validation": SignalEntry(
        # PRODUCIBLE — and this is the correction that matters. Two producers
        # exist and run today. What is unsettled is the VALUE DOMAIN and the
        # CONSEQUENCE, which are different questions from producibility.
        production_state=PRODUCIBLE,
        value_domain=UNRECONCILED,
        consequence_state=METHODOLOGY_PENDING,
        producers=(
            # The two paths emit DIFFERENT value sets. Recorded per producer so a
            # rule condition can be reported reachable on one path and dead on
            # the other, instead of a union making both look fine.
            Producer(PATH_RCE, "app/tefca_registry/rce/arc_pipeline.py:236",
                     ("flagged",)),
            Producer(PATH_REGISTRY, "app/tefca_registry/review_service.py:248",
                     ("valid", "invalid")),
        ),
        blocking_decision="D6",
        note="Identifier QUALITY is observable: the RCE path emits 'flagged' from "
             "NPI_MALFORMED / NPI_CHECK_DIGIT_FAILED, and the registry path emits "
             "'valid'/'invalid' from validate_npi(). The two vocabularies have "
             "never met, and what either means for B1-B4 is D6. No INVALID -> B4 "
             "or FLAGGED -> bucket mapping is defined here.",
    ),
    "taxonomy_mismatch": SignalEntry(
        production_state=DECLARED_UNAVAILABLE,
        value_domain=UNDEFINED,
        consequence_state=METHODOLOGY_PENDING,
        note="No second operand. NPPES supplies a NUCC taxonomy; the RCE delivery "
             "supplies none, so there is nothing to compare it against.",
    ),
    "confidence_below": SignalEntry(
        production_state=METHODOLOGY_BLOCKED,
        value_domain=UNDEFINED,
        consequence_state=METHODOLOGY_PENDING,
        blocking_decision="D2",
        note="The dimension layer computes no confidence score, by design — "
             "sufficiency_summary() contains no arithmetic. Porting the legacy "
             "engine's deduction model would transplant one classifier's scoring "
             "into the other.",
    ),
    "nppes_pecos_conflict": SignalEntry(
        production_state=METHODOLOGY_BLOCKED,
        value_domain=UNDEFINED,
        consequence_state=METHODOLOGY_PENDING,
        producers=(
            Producer(PATH_REGISTRY, "app/tefca_registry/review_service.py:251",
                     ("true", "false")),
        ),
        blocking_decision="D1",
        note="A derivation exists on the registry path, but 'pecos' denotes the "
             "NPPES-proxy connector there and genuine CMS PPEF Enrollment on the "
             "RCE path. Reusing it would make a PECOS non-match feed B3, "
             "contradicting the explicit rule that a PECOS non-match is never a "
             "TEFCA failure.",
    ),
    "multiple_source_conflict": SignalEntry(
        production_state=METHODOLOGY_BLOCKED,
        value_domain=UNDEFINED,
        consequence_state=METHODOLOGY_PENDING,
        producers=(
            Producer(PATH_REGISTRY, "app/tefca_registry/review_service.py:254",
                     ("true", "false")),
        ),
        blocking_decision="D1",
        note="Same 'pecos' ambiguity in its first clause.",
    ),
    "required_verification_failed": SignalEntry(
        production_state=DECLARED_UNAVAILABLE,
        value_domain=UNDEFINED,
        consequence_state=METHODOLOGY_PENDING,
        note="No producer anywhere in the codebase, and no definition of which "
             "verifications are 'required' for a given entity. Establishing that "
             "set is a methodology question, not a wiring one — which is why the "
             "signal is declared unavailable rather than synthesised.",
    ),
}


# ── read helpers ─────────────────────────────────────────────────────────────

def vocabulary_of(row_version: Optional[str]) -> str:
    """The vocabulary a stored row belongs to. NULL means LEGACY.

    DERIVED, NEVER WRITTEN. A NULL `vocabulary_version` records that the row was
    written before the vocabulary was versioned, and that is a fact worth
    keeping. Backfilling it — even with the string "LEGACY" — would erase the
    distinction between "we know this predates versioning" and "we decided
    retrospectively what it was".
    """
    return row_version or LEGACY_VOCABULARY


def is_legacy_row(row_version: Optional[str]) -> bool:
    return row_version is None


def term_status(layer: Layer, term: str) -> Optional[TermStatus]:
    entry = VOCABULARY_REGISTRY.get((layer, term))
    return entry.status if entry else None


def term_entry(layer: Layer, term: str) -> Optional[TermEntry]:
    return VOCABULARY_REGISTRY.get((layer, term))


def signal_entry(signal: str) -> Optional[SignalEntry]:
    return CLASSIFIER_SIGNAL_REGISTRY.get(signal)


def is_signal_registered(signal: str) -> bool:
    return signal in CLASSIFIER_SIGNAL_REGISTRY


def terms_for_layer(layer: Layer) -> Tuple[str, ...]:
    return tuple(sorted(t for (lyr, t) in VOCABULARY_REGISTRY if lyr is layer))


def methodology_dependent_terms() -> Dict[Tuple[str, str], str]:
    """Every term whose meaning awaits a COR decision, and which decision."""
    return {
        (layer.value, term): (entry.blocking_decision or "unspecified")
        for (layer, term), entry in VOCABULARY_REGISTRY.items()
        if entry.status is TermStatus.METHODOLOGY_DEPENDENT
    }


def registry_snapshot() -> dict:
    """Serialisable view of the whole vocabulary. Metadata only — no rendering.

    Provided so a future consumer (B4 reporting, B3 provenance) can state which
    vocabulary it used. E1 makes no presentation decision.
    """
    return {
        "vocabulary_version": EVIDENCE_VOCABULARY_VERSION,
        "layer_1_states": list(LAYER_1_STATES),
        "terms": {
            f"{layer.value}:{term}": {
                "status": entry.status.value,
                "meaning": entry.meaning,
                "blocking_decision": entry.blocking_decision,
                "since_version": entry.since_version,
            }
            for (layer, term), entry in sorted(
                VOCABULARY_REGISTRY.items(), key=lambda kv: (kv[0][0].value, kv[0][1]))
        },
        "signals": {
            name: {
                "production_state": s.production_state.value,
                "value_domain": s.value_domain.value,
                "consequence_state": s.consequence_state.value,
                "producers": [
                    {"path": p.path, "location": p.location, "emits": list(p.emits)}
                    for p in s.producers
                ],
                "producing_paths": list(s.producing_paths),
                "observed_values": list(s.observed_values),
                "blocking_decision": s.blocking_decision,
            }
            for name, s in sorted(CLASSIFIER_SIGNAL_REGISTRY.items())
        },
        "grandfathered_cross_layer_terms": {
            term: [layer.value for layer in layers]
            for term, layers in sorted(ALLOWED_CROSS_LAYER_TERMS.items())
        },
    }
