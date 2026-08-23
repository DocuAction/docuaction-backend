"""Content types and the registry that holds them.

THE ONE UNUSUAL DESIGN CHOICE
    `Lesson.vocabulary` and `Module.vocabulary` name the code-level terms a piece
    of guidance teaches. Nothing renders those lists — they exist so a test can
    assert that every term still exists in the enums it came from.

    Operational guidance rots silently. An analyst reading "a source outage is
    recorded as SOURCE_UNAVAILABLE" has no way to know the constant was renamed
    three sprints ago; they simply stop finding it and start guessing. Declaring
    the vocabulary turns that from a slow misunderstanding into a failing test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

#: Bump when content changes materially, so a screenshot can be dated.
KNOWLEDGE_VERSION = "1.0.0"


class Role(str, Enum):
    """Who a piece of guidance is for. Mirrors the RBAC ladder, not a new one."""

    ANY = "any"
    ANALYST = "reviewer"
    QA = "qalead"
    PROGRAM_MANAGER = "program_manager"
    ADMIN = "admin"


@dataclass(frozen=True)
class ProhibitedConclusion:
    """A statement an operator must NOT make, and why.

    Carried alongside the guidance rather than buried in prose, because the
    conclusions people reach wrongly are the ones nobody wrote down as forbidden.
    """

    claim: str
    why_prohibited: str
    #: The decision or condition that would have to change for it to be allowed.
    unblocked_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"claim": self.claim, "why_prohibited": self.why_prohibited,
                "unblocked_by": self.unblocked_by}


@dataclass(frozen=True)
class KnowledgeCheck:
    """One question with one correct answer and an explanation.

    The explanation is mandatory. A check that says only "wrong" teaches the
    reader that they guessed badly, not what the right model is.
    """

    question: str
    options: List[str]
    correct_index: int
    explanation: str

    def __post_init__(self) -> None:
        if not 0 <= self.correct_index < len(self.options):
            raise ValueError(
                f"correct_index {self.correct_index} is outside "
                f"{len(self.options)} options for {self.question!r}")
        if len(self.options) < 2:
            raise ValueError("a knowledge check needs at least two options")

    def to_dict(self, *, include_answer: bool = False) -> Dict[str, Any]:
        out: Dict[str, Any] = {"question": self.question, "options": list(self.options)}
        if include_answer:
            out["correct_index"] = self.correct_index
            out["explanation"] = self.explanation
        return out


@dataclass(frozen=True)
class Lesson:
    slug: str
    title: str
    objective: str
    body: str
    example: Optional[str] = None
    common_mistakes: List[str] = field(default_factory=list)
    prohibited: List[ProhibitedConclusion] = field(default_factory=list)
    #: Code-level terms this lesson teaches. Verified by test, not rendered.
    vocabulary: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"slug": self.slug, "title": self.title, "objective": self.objective,
                "body": self.body, "example": self.example,
                "common_mistakes": list(self.common_mistakes),
                "prohibited": [p.to_dict() for p in self.prohibited],
                "vocabulary": list(self.vocabulary)}


@dataclass(frozen=True)
class Module:
    slug: str
    title: str
    audience: List[Role]
    objective: str
    lessons: List[Lesson] = field(default_factory=list)
    checks: List[KnowledgeCheck] = field(default_factory=list)

    @property
    def vocabulary(self) -> List[str]:
        seen: List[str] = []
        for lesson in self.lessons:
            for term in lesson.vocabulary:
                if term not in seen:
                    seen.append(term)
        return seen

    def to_dict(self, *, include_answers: bool = False) -> Dict[str, Any]:
        return {"slug": self.slug, "title": self.title,
                "audience": [r.value for r in self.audience],
                "objective": self.objective,
                "lessons": [l.to_dict() for l in self.lessons],
                "checks": [c.to_dict(include_answer=include_answers)
                           for c in self.checks],
                "knowledge_version": KNOWLEDGE_VERSION}


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    definition: str
    #: Set where a term is often mistaken for something it is not.
    not_to_be_confused_with: Optional[str] = None
    #: Set where a term is an internal construct rather than an external standard.
    authority: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"term": self.term, "definition": self.definition,
                "not_to_be_confused_with": self.not_to_be_confused_with,
                "authority": self.authority}


class Glossary:
    """Case-insensitive term lookup. Refuses duplicate definitions."""

    def __init__(self, terms: List[GlossaryTerm]):
        self._terms: Dict[str, GlossaryTerm] = {}
        for t in terms:
            key = t.term.strip().lower()
            if key in self._terms:
                raise ValueError(
                    f"{t.term!r} is defined twice. Two definitions of one term "
                    f"is how a glossary starts contradicting itself.")
            self._terms[key] = t

    def __len__(self) -> int:
        return len(self._terms)

    def get(self, term: str) -> Optional[GlossaryTerm]:
        return self._terms.get((term or "").strip().lower())

    def all(self) -> List[GlossaryTerm]:
        return sorted(self._terms.values(), key=lambda t: t.term.lower())

    def to_dict(self) -> Dict[str, Any]:
        return {"knowledge_version": KNOWLEDGE_VERSION,
                "terms": [t.to_dict() for t in self.all()]}


@dataclass(frozen=True)
class ContextualHelp:
    """Help for one screen or field, answering five fixed questions.

    The five are fixed on purpose. "What conclusion is prohibited?" is the one
    most easily left out and the one that prevents the most damage.
    """

    key: str
    what_is_this: str
    why_am_i_seeing_it: str
    allowed_actions: List[str]
    prohibited_conclusions: List[ProhibitedConclusion]
    evidence_location: str
    audience: List[Role] = field(default_factory=lambda: [Role.ANY])

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "what_is_this": self.what_is_this,
                "why_am_i_seeing_it": self.why_am_i_seeing_it,
                "allowed_actions": list(self.allowed_actions),
                "prohibited_conclusions": [p.to_dict() for p in self.prohibited_conclusions],
                "evidence_location": self.evidence_location,
                "audience": [r.value for r in self.audience],
                "knowledge_version": KNOWLEDGE_VERSION}


class LearningRegistry:
    """Everything a programme's operators can be shown, in one place."""

    def __init__(self, *, modules: List[Module], glossary: Glossary,
                 help_topics: List[ContextualHelp], navigation: List[str]):
        slugs = [m.slug for m in modules]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate module slug")
        keys = [h.key for h in help_topics]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate contextual-help key")
        self.modules = modules
        self.glossary = glossary
        self._help = {h.key: h for h in help_topics}
        self.navigation = navigation

    def module(self, slug: str) -> Optional[Module]:
        return next((m for m in self.modules if m.slug == slug), None)

    def modules_for(self, role: Role) -> List[Module]:
        return [m for m in self.modules
                if Role.ANY in m.audience or role in m.audience]

    def help_for(self, key: str) -> Optional[ContextualHelp]:
        return self._help.get(key)

    def help_keys(self) -> List[str]:
        return sorted(self._help)

    def all_prohibited(self) -> List[ProhibitedConclusion]:
        out: List[ProhibitedConclusion] = []
        for m in self.modules:
            for l in m.lessons:
                out.extend(l.prohibited)
        for h in self._help.values():
            out.extend(h.prohibited_conclusions)
        return out

    def vocabulary(self) -> List[str]:
        seen: List[str] = []
        for m in self.modules:
            for term in m.vocabulary:
                if term not in seen:
                    seen.append(term)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        return {"knowledge_version": KNOWLEDGE_VERSION,
                "navigation": list(self.navigation),
                "modules": [m.to_dict() for m in self.modules],
                "glossary": self.glossary.to_dict(),
                "contextual_help": [h.to_dict() for h in self._help.values()]}
