"""Data quality: what is wrong with a record, and nothing about what it means.

THE LINE THIS MODULE EXISTS TO HOLD
───────────────────────────────────
A data-quality finding says something about the DATA. A methodology disposition
says something about the ENTITY. They are different claims, made by different
authorities, and the gap between them is where a system quietly starts deciding
policy.

"NPI field is empty" is a fact about a delivered record. "This organisation
cannot be verified" is a determination about a real organisation under a
methodology the COR owns. The first does not imply the second. An empty NPI
might mean the entity has no NPI, or that the RCE did not collect it, or that a
column shifted — and which of those it is decides the disposition, not the
emptiness.

So `DataQualityFinding` carries no disposition, no bucket and no evidence state,
and there is no function here that converts one into the other. A program that
wants that mapping writes it as a versioned, attributable rule in its own module
where the COR can read it. `assert_not_a_disposition()` fails loudly if a
methodology term is smuggled into a finding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

#: Bumped when the category set changes.
DQ_TAXONOMY_VERSION = "1.0"


class DataQualityCategory(str, Enum):
    """Reusable across programs. Each is a statement about the data only."""

    #: A field the schema requires carried no value.
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    #: A value is present and cannot be an identifier of its declared kind —
    #: wrong length, failed check digit, illegal characters.
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    #: Another record in the same delivery carries the same identity.
    DUPLICATE_RECORD = "DUPLICATE_RECORD"
    #: A value is present and does not match the field's format.
    INVALID_FORMAT = "INVALID_FORMAT"
    #: Two sources, or two fields, assert values that cannot both hold.
    CONFLICTING_SOURCE_VALUES = "CONFLICTING_SOURCE_VALUES"
    #: The parser could not read this record. The raw line is still preserved.
    PARSER_FAILURE = "PARSER_FAILURE"
    #: The source did not answer. A fact about the source, not the entity.
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    #: The source answered with an edition older than the policy allows.
    STALE_SOURCE = "STALE_SOURCE"
    #: The record describes a kind of thing this program does not handle.
    UNSUPPORTED_ENTITY_TYPE = "UNSUPPORTED_ENTITY_TYPE"
    #: The delivery's column set differs from the locked map. Recorded, never a
    #: reason to reject: a schema change is precisely what must not be dropped.
    SCHEMA_DRIFT = "SCHEMA_DRIFT"


class Severity(str, Enum):
    """How much this finding should interrupt.

    Severity is about handling, not about truth. A CRITICAL finding is not more
    factual than a LOW one; it is more urgent.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class CorrectionAuthority(str, Enum):
    """Who may act on a finding.

    Kept identical in meaning to the RCE ledger's existing four values so the
    two never have to be translated. Confidence about a value says nothing about
    the authority to change it: a high-confidence NPI correction is still
    HUMAN_REQUIRED, because an identifier is an identity claim.
    """

    #: Deterministic, non-substantive normalisation only — whitespace, case of a
    #: state code, ZIP zero-padding, date canonical form.
    AUTO_SAFE = "AUTO_SAFE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    QA_REQUIRED = "QA_REQUIRED"
    NO_CORRECTION = "NO_CORRECTION"


#: Terms that belong to the methodology layers, not to data quality. A finding
#: whose description or type contains one of these is asserting something it has
#: no authority to assert. Drawn from the Layer 3/4 vocabulary.
_METHODOLOGY_TERMS: FrozenSet[str] = frozenset({
    "BUCKET_1", "BUCKET_2", "BUCKET_3", "BUCKET_4",
    "VERIFIED", "UNVERIFIED", "NON_COMPLIANT", "COMPLIANT",
    "SUFFICIENT", "INSUFFICIENT", "DISPOSITION", "DETERMINATION",
    "REPORTABLE",
})


def assert_not_a_disposition(text: str, *, where: str) -> None:
    """Refuse a finding that states a methodology conclusion.

    Raises rather than warns. A finding that quietly carries a disposition
    becomes one the moment anything downstream reads it, and by then the
    authority boundary has already been crossed.
    """
    upper = (text or "").upper()
    for term in _METHODOLOGY_TERMS:
        if term in upper:
            raise ValueError(
                f"{where} contains the methodology term {term!r}. A data-quality "
                f"finding describes the data; it does not decide a disposition. "
                f"If a rule genuinely maps this condition to a disposition, that "
                f"rule belongs in the program module, versioned and attributable.")


@dataclass
class DataQualityFinding:
    """One rule firing on one record, or on the delivery as a whole."""

    rule_id: str
    rule_version: str
    category: DataQualityCategory
    severity: Severity
    description: str
    correction_authority: CorrectionAuthority = CorrectionAuthority.HUMAN_REQUIRED

    line_number: Optional[int] = None
    field_name: Optional[str] = None
    original_value: Optional[str] = None
    suggested_value: Optional[str] = None
    suggested_source: Optional[str] = None
    #: How sure the rule is about the suggested value. Independent of authority.
    suggested_confidence: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert_not_a_disposition(self.description,
                                 where=f"finding {self.rule_id} description")
        if (self.correction_authority is CorrectionAuthority.AUTO_SAFE
                and self.category in _IDENTITY_CATEGORIES):
            raise ValueError(
                f"{self.rule_id}: {self.category.value} may not be AUTO_SAFE. "
                f"Anything touching identity requires a human, whatever the "
                f"rule thinks it knows.")


#: Categories that touch identity. Never auto-correctable.
_IDENTITY_CATEGORIES = frozenset({
    DataQualityCategory.MALFORMED_IDENTIFIER,
    DataQualityCategory.DUPLICATE_RECORD,
    DataQualityCategory.CONFLICTING_SOURCE_VALUES,
})


@dataclass
class DataQualityRule:
    """A versioned, deterministic check.

    `evaluate` must be pure: the same record and the same dataset context
    produce the same findings, in the same order. That is what makes a re-run
    diffable against the previous one, and it is why nothing here may read the
    clock or a random source.
    """

    rule_id: str
    version: str
    category: DataQualityCategory
    description: str
    default_severity: Severity = Severity.MEDIUM
    #: Set when the rule cannot run until a COR decision lands. A blocked rule
    #: is registered, reported and NOT evaluated — it never guesses.
    blocked_by: Optional[str] = None

    def is_blocked(self) -> bool:
        return self.blocked_by is not None


class RuleSet:
    """The rules one program runs, at one version."""

    def __init__(self, program: str, version: str) -> None:
        self.program = program
        self.version = version
        self._rules: Dict[str, DataQualityRule] = {}

    def register(self, rule: DataQualityRule) -> DataQualityRule:
        if rule.rule_id in self._rules:
            raise ValueError(
                f"{rule.rule_id} is already registered in {self.program}. Two "
                f"rules with one id make an issue ledger unattributable.")
        self._rules[rule.rule_id] = rule
        return rule

    @property
    def rules(self) -> List[DataQualityRule]:
        return sorted(self._rules.values(), key=lambda r: r.rule_id)

    @property
    def runnable(self) -> List[DataQualityRule]:
        return [r for r in self.rules if not r.is_blocked()]

    @property
    def blocked(self) -> List[DataQualityRule]:
        return [r for r in self.rules if r.is_blocked()]

    def blocked_report(self) -> List[Dict[str, str]]:
        """What is not being evaluated, and what decision would unblock it.

        Surfaced rather than silently skipped: a rule that produces no findings
        because it is blocked looks exactly like a rule that found nothing.
        """
        return [{"rule_id": r.rule_id, "blocked_by": r.blocked_by or "",
                 "description": r.description} for r in self.blocked]
