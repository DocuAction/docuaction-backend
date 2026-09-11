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
KNOWLEDGE_VERSION = "1.1.0"


class Role(str, Enum):
    """Who a piece of guidance is for. Mirrors the RBAC ladder, not a new one."""

    ANY = "any"
    ANALYST = "reviewer"
    QA = "qalead"
    PROGRAM_MANAGER = "program_manager"
    ADMIN = "admin"


class Classification(str, Enum):
    """Whose statement this is. The single most important label in the system.

    A programme's operators read guidance and act on it. If they cannot tell an
    agency requirement from the contractor's own choice, the contractor's choice
    quietly becomes policy — first internally, then in something sent to the
    agency. Every substantive statement therefore carries one of these, and the
    UI is expected to show it wherever it is material.

    GOVERNMENT_REQUIREMENT
        Stated by the agency in the contract, regulation or written direction.
        Quotable, and traceable to a source.

    AGT_IMPLEMENTATION
        How the contractor has built or operationalised something. Binding on
        staff, not on the agency, and changeable without contract action.

    AGT_RECOMMENDATION
        The contractor's proposed answer to an open question. Not yet agreed by
        anyone.

    PROGRAM_GUIDANCE_REQUESTED
        Genuinely unanswered. Must not be presented as decided, and must not be
        resolved by picking a default in software.

    SOURCE_LIMITATION
        A fact about what a data source can and cannot establish. Never a fact
        about the entity being reviewed.
    """

    GOVERNMENT_REQUIREMENT = "GOVERNMENT_REQUIREMENT"
    AGT_IMPLEMENTATION = "AGT_IMPLEMENTATION"
    AGT_RECOMMENDATION = "AGT_RECOMMENDATION"
    PROGRAM_GUIDANCE_REQUESTED = "PROGRAM_GUIDANCE_REQUESTED"
    SOURCE_LIMITATION = "SOURCE_LIMITATION"

    @property
    def is_authoritative(self) -> bool:
        """True only for an agency requirement.

        Used to stop anything else being rendered as policy.
        """
        return self is Classification.GOVERNMENT_REQUIREMENT


@dataclass(frozen=True)
class Statement:
    """One classified assertion, with its authority.

    `source` is mandatory for a GOVERNMENT_REQUIREMENT: a requirement nobody can
    trace to a document is indistinguishable from an assumption, and that is the
    failure this whole scheme exists to prevent.
    """

    text: str
    classification: Classification
    #: Where it comes from — a solicitation paragraph, a deliverable, a decision
    #: id. Required when the classification claims agency authority.
    source: Optional[str] = None

    def __post_init__(self) -> None:
        if self.classification.is_authoritative and not (self.source or "").strip():
            raise ValueError(
                f"a GOVERNMENT_REQUIREMENT must cite its source: {self.text!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text,
                "classification": self.classification.value,
                "is_authoritative": self.classification.is_authoritative,
                "source": self.source}


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
    #: Classified assertions. Anything a reader might act on or repeat to the
    #: agency belongs here rather than in free prose, so its authority travels
    #: with it.
    statements: List[Statement] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"slug": self.slug, "title": self.title, "objective": self.objective,
                "body": self.body, "example": self.example,
                "common_mistakes": list(self.common_mistakes),
                "prohibited": [p.to_dict() for p in self.prohibited],
                "vocabulary": list(self.vocabulary),
                "statements": [st.to_dict() for st in self.statements]}

    def search_text(self) -> str:
        parts = [self.title, self.objective, self.body, self.example or ""]
        parts.extend(self.common_mistakes)
        parts.extend(st.text for st in self.statements)
        parts.extend(p.claim for p in self.prohibited)
        return " ".join(parts).lower()


@dataclass(frozen=True)
class ModuleGuide:
    """The fixed seven-part content standard every module answers.

    The order is the order an operator needs it: what the thing is, why it
    matters to the contract, what the software already did, what the person
    must do, how, what not to do, and what happens after. A module without a
    guide is a reference, not training.
    """

    what_is_this: str
    why_it_matters: str
    what_automation_does: str
    what_human_does: str
    steps: List[str]
    what_not_to_do: List[str]
    what_happens_next: str

    def to_dict(self) -> Dict[str, Any]:
        return {"what_is_this": self.what_is_this,
                "why_it_matters": self.why_it_matters,
                "what_automation_does": self.what_automation_does,
                "what_human_does": self.what_human_does,
                "steps": list(self.steps),
                "what_not_to_do": list(self.what_not_to_do),
                "what_happens_next": self.what_happens_next}


@dataclass(frozen=True)
class ModuleRevision:
    """One entry in a module's version history. History is preserved, not
    overwritten: a superseded version stays listed with its effective date so
    a reader can tell which version they were trained on."""

    version: str
    effective_date: str
    summary: str
    status: str = "superseded"  # superseded | current | draft

    def to_dict(self) -> Dict[str, Any]:
        return {"version": self.version, "effective_date": self.effective_date,
                "summary": self.summary, "status": self.status}


MODULE_STATUSES = ("current", "draft", "superseded")


@dataclass(frozen=True)
class Module:
    slug: str
    title: str
    audience: List[Role]
    objective: str
    lessons: List[Lesson] = field(default_factory=list)
    checks: List[KnowledgeCheck] = field(default_factory=list)
    #: Content versioning. `version` is this text; `history` lists earlier
    #: versions and is never pruned.
    version: str = "1.0.0"
    effective_date: Optional[str] = None
    status: str = "current"
    history: List[ModuleRevision] = field(default_factory=list)
    #: The seven-part content standard. Optional in the type so a programme
    #: can register reference-only modules; the TEFCA content test requires it.
    guide: Optional[ModuleGuide] = None

    def __post_init__(self) -> None:
        if self.status not in MODULE_STATUSES:
            raise ValueError(f"module {self.slug!r}: status must be one of "
                             f"{MODULE_STATUSES}, not {self.status!r}")

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
                "version": self.version,
                "effective_date": self.effective_date,
                "status": self.status,
                "history": [h.to_dict() for h in self.history],
                "guide": self.guide.to_dict() if self.guide else None,
                "knowledge_version": KNOWLEDGE_VERSION}


@dataclass(frozen=True)
class LearningPath:
    """An ordered route through modules for one role. The modules are shared;
    the order and the emphasis are the role's."""

    slug: str
    title: str
    role: Role
    description: str
    module_slugs: List[str]
    #: Read-only audiences (a COR or viewer) get a path too — one that
    #: explains what they are looking at without teaching them to operate it.
    read_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"slug": self.slug, "title": self.title, "role": self.role.value,
                "description": self.description,
                "module_slugs": list(self.module_slugs),
                "read_only": self.read_only}


class Authority(str, Enum):
    """Where a library item's authority comes from.

    Only CONTRACT_SOW binds the programme contractually. Everything else is
    context — accepted methodology, RCE governing material, guidance, source
    documentation, reporting standards, training, or industry research — and
    the UI is expected to say so, because a research paper shelved next to the
    SOW starts to look like a requirement.
    """

    CONTRACT_SOW = "CONTRACT_SOW"
    ACCEPTED_METHODOLOGY = "ACCEPTED_METHODOLOGY"
    RCE_GOVERNING_MATERIAL = "RCE_GOVERNING_MATERIAL"
    PROGRAM_GUIDANCE = "PROGRAM_GUIDANCE"
    EVIDENCE_SOURCE_GUIDE = "EVIDENCE_SOURCE_GUIDE"
    REPORTING_GUIDE = "REPORTING_GUIDE"
    TRAINING = "TRAINING"
    RESEARCH_INDUSTRY = "RESEARCH_INDUSTRY"

    @property
    def is_contractual(self) -> bool:
        return self is Authority.CONTRACT_SOW

    @property
    def label(self) -> str:
        return {
            Authority.CONTRACT_SOW: "Contract / SOW",
            Authority.ACCEPTED_METHODOLOGY: "Accepted methodology",
            Authority.RCE_GOVERNING_MATERIAL: "RCE governing material",
            Authority.PROGRAM_GUIDANCE: "Program guidance",
            Authority.EVIDENCE_SOURCE_GUIDE: "Evidence source guide",
            Authority.REPORTING_GUIDE: "Reporting guide",
            Authority.TRAINING: "Training",
            Authority.RESEARCH_INDUSTRY: "Research / industry",
        }[self]


@dataclass(frozen=True)
class LibraryItem:
    """One reference in the research/reference library."""

    title: str
    authority: Authority
    summary: str
    #: A URL, a document identifier, or a location on file.
    reference: str
    module_slugs: List[str] = field(default_factory=list)
    #: Anything the reader must know before relying on it — "proposed, not
    #: yet accepted", "external, not contractual".
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "authority": self.authority.value,
                "authority_label": self.authority.label,
                "contractual": self.authority.is_contractual,
                "summary": self.summary, "reference": self.reference,
                "module_slugs": list(self.module_slugs), "note": self.note}


@dataclass(frozen=True)
class FeatureLink:
    """One row of the feature-to-training traceability matrix.

    When a feature changes, this row is what tells the team which module has
    to change in the same pull request — and `last_verified_version` is what
    tells a reader whether anyone did.
    """

    feature: str
    screen: str
    route: str
    roles: List[Role]
    module_slug: str
    last_verified_version: str
    last_updated: str
    owner: str
    lesson_slug: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"feature": self.feature, "screen": self.screen, "route": self.route,
                "roles": [r.value for r in self.roles],
                "module_slug": self.module_slug, "lesson_slug": self.lesson_slug,
                "last_verified_version": self.last_verified_version,
                "last_updated": self.last_updated, "owner": self.owner}


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
    #: Where "read more" goes: a module slug, optionally with a lesson after a
    #: slash. Deep-linking to the topic rather than the Learning Center home is
    #: the difference between help that answers the question and help that
    #: hands the reader a table of contents.
    learn_more: Optional[str] = None
    #: Classified statements shown alongside the help, so an operator sees
    #: whose rule they are following at the moment they follow it.
    statements: List[Statement] = field(default_factory=list)

    @property
    def module_slug(self) -> Optional[str]:
        return self.learn_more.split("/", 1)[0] if self.learn_more else None

    @property
    def lesson_slug(self) -> Optional[str]:
        if self.learn_more and "/" in self.learn_more:
            return self.learn_more.split("/", 1)[1]
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "what_is_this": self.what_is_this,
                "why_am_i_seeing_it": self.why_am_i_seeing_it,
                "allowed_actions": list(self.allowed_actions),
                "prohibited_conclusions": [p.to_dict() for p in self.prohibited_conclusions],
                "evidence_location": self.evidence_location,
                "audience": [r.value for r in self.audience],
                "learn_more": self.learn_more,
                "module_slug": self.module_slug,
                "lesson_slug": self.lesson_slug,
                "statements": [st.to_dict() for st in self.statements],
                "knowledge_version": KNOWLEDGE_VERSION}


class LearningRegistry:
    """Everything a programme's operators can be shown, in one place."""

    def __init__(self, *, modules: List[Module], glossary: Glossary,
                 help_topics: List[ContextualHelp], navigation: List[str],
                 program: str = "DEFAULT", program_title: str = "",
                 last_updated: Optional[str] = None,
                 paths: Optional[List[LearningPath]] = None,
                 features: Optional[List[FeatureLink]] = None,
                 library: Optional[List[LibraryItem]] = None,
                 learning_center_route: str = "/learning"):
        #: Where the client renders this programme's Learning Center. The
        #: programme owns its routes; the core only formats the deep link.
        self.learning_center_route = learning_center_route
        slugs = [m.slug for m in modules]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate module slug")
        known_slugs = set(slugs)

        # A path, a feature row or a library item that points at a module that
        # does not exist is the LMS equivalent of a dead link.
        self.paths = list(paths or [])
        if len({p.slug for p in self.paths}) != len(self.paths):
            raise ValueError("duplicate learning path slug")
        for path in self.paths:
            missing = [s for s in path.module_slugs if s not in known_slugs]
            if missing:
                raise ValueError(f"learning path {path.slug!r} names unknown "
                                 f"modules {missing}")
        self.features = list(features or [])
        for link in self.features:
            if link.module_slug not in known_slugs:
                raise ValueError(f"feature {link.feature!r} maps to unknown "
                                 f"module {link.module_slug!r}")
            if link.lesson_slug:
                module = next(m for m in modules if m.slug == link.module_slug)
                if link.lesson_slug not in {l.slug for l in module.lessons}:
                    raise ValueError(f"feature {link.feature!r} maps to unknown "
                                     f"lesson {link.lesson_slug!r}")
        self.library = list(library or [])
        for item in self.library:
            missing = [s for s in item.module_slugs if s not in known_slugs]
            if missing:
                raise ValueError(f"library item {item.title!r} names unknown "
                                 f"modules {missing}")
        keys = [h.key for h in help_topics]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate contextual-help key")

        # A deep link that goes nowhere is worse than no deep link: the reader
        # follows it, lands on an error, and stops trusting the help.
        known = set(slugs)
        for topic in help_topics:
            if topic.module_slug and topic.module_slug not in known:
                raise ValueError(
                    f"contextual help {topic.key!r} deep-links to unknown "
                    f"module {topic.module_slug!r}")
            if topic.lesson_slug:
                module = next(m for m in modules if m.slug == topic.module_slug)
                if topic.lesson_slug not in {l.slug for l in module.lessons}:
                    raise ValueError(
                        f"contextual help {topic.key!r} deep-links to unknown "
                        f"lesson {topic.lesson_slug!r} in {topic.module_slug!r}")

        #: Which programme this content belongs to. The framework is shared;
        #: the content is not, and two programmes must not collide.
        self.program = program
        self.program_title = program_title or program
        self.last_updated = last_updated
        self.modules = modules
        self.glossary = glossary
        self._help = {h.key: h for h in help_topics}
        self.navigation = navigation

    def module(self, slug: str) -> Optional[Module]:
        return next((m for m in self.modules if m.slug == slug), None)

    def modules_for(self, role: Role) -> List[Module]:
        return [m for m in self.modules
                if Role.ANY in m.audience or role in m.audience]

    def path(self, slug: str) -> Optional[LearningPath]:
        return next((p for p in self.paths if p.slug == slug), None)

    def paths_for(self, role: Role) -> List[LearningPath]:
        """Every path, with the caller's own first. A QA lead benefits from
        seeing the analyst path; nothing in a path is role-secret, because the
        modules it lists are still filtered by audience when opened."""
        mine = [p for p in self.paths if p.role == role]
        others = [p for p in self.paths if p.role != role]
        return mine + others

    def learning_center_path(self, module_slug: Optional[str],
                             lesson_slug: Optional[str] = None) -> Optional[str]:
        """In-app deep link to a module or lesson."""
        if not module_slug:
            return None
        path = f"{self.learning_center_route}?module={module_slug}"
        if lesson_slug:
            path += f"&lesson={lesson_slug}"
        return path

    def features_for_route(self, route: str) -> List[FeatureLink]:
        return [f for f in self.features if f.route == route]

    def stale_features(self) -> List[FeatureLink]:
        """Rows whose last verification predates the current knowledge
        version. Served so the gap is visible instead of silent."""
        return [f for f in self.features
                if f.last_verified_version != KNOWLEDGE_VERSION]

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

    def statements(self) -> List[Statement]:
        """Every classified assertion in the programme content."""
        out: List[Statement] = []
        for module in self.modules:
            for lesson in module.lessons:
                out.extend(lesson.statements)
        for topic in self._help.values():
            out.extend(topic.statements)
        return out

    def statements_by_classification(self) -> Dict[str, int]:
        counts: Dict[str, int] = {c.value: 0 for c in Classification}
        for statement in self.statements():
            counts[statement.classification.value] += 1
        return counts

    def search(self, query: str, *, role: Optional[Role] = None,
               limit: int = 25) -> List[Dict[str, Any]]:
        """Substring search across lessons and glossary terms.

        Deliberately not fuzzy and not cleverly ranked. An operator searching
        this content knows roughly what word they want; a matcher that guesses
        would surface a confidently wrong lesson, and confidently wrong is the
        failure mode this whole module exists to avoid.

        Ordered: title matches, then body matches, then glossary. Role filtering
        uses the same audience rule as navigation, so search cannot reveal
        content the sidebar hides.
        """
        needle = (query or "").strip().lower()
        if not needle:
            return []

        modules = self.modules_for(role) if role else self.modules
        titled: List[Dict[str, Any]] = []
        bodied: List[Dict[str, Any]] = []
        for module in modules:
            for lesson in module.lessons:
                hit = {"type": "lesson", "module_slug": module.slug,
                       "module_title": module.title, "lesson_slug": lesson.slug,
                       "title": lesson.title, "objective": lesson.objective,
                       "deep_link": module.slug + "/" + lesson.slug}
                if needle in lesson.title.lower():
                    titled.append(hit)
                elif needle in lesson.search_text():
                    bodied.append(hit)

        glossary_hits = [
            {"type": "glossary", "term": t.term, "definition": t.definition,
             "authority": t.authority, "deep_link": "glossary/" + t.term.lower()}
            for t in self.glossary.all()
            if needle in t.term.lower() or needle in t.definition.lower()
        ]
        return (titled + bodied + glossary_hits)[:limit]

    def to_dict(self, *, role: Optional[Role] = None) -> Dict[str, Any]:
        modules = self.modules_for(role) if role else self.modules
        return {"program": self.program,
                "program_title": self.program_title,
                "knowledge_version": KNOWLEDGE_VERSION,
                "last_updated": self.last_updated,
                "role": role.value if role else None,
                "navigation": list(self.navigation),
                "modules": [m.to_dict() for m in modules],
                "glossary": self.glossary.to_dict(),
                "contextual_help": [h.to_dict() for h in self._help.values()],
                "paths": [p.to_dict() for p in (self.paths_for(role) if role else self.paths)],
                "features": [f.to_dict() for f in self.features],
                "library": [i.to_dict() for i in self.library],
                "statement_classifications": self.statements_by_classification()}


class ProgramRegistry:
    """Every programme learning pack, keyed by programme.

    This is what makes the framework reusable rather than merely
    reusable-looking: a second programme registers its own content and gets
    navigation, search, role filtering, classification and contextual help
    without importing anything from the first.
    """

    def __init__(self) -> None:
        self._programs: Dict[str, LearningRegistry] = {}

    def register(self, registry: LearningRegistry) -> None:
        key = registry.program.strip().upper()
        if not key or key == "DEFAULT":
            raise ValueError("a registered programme needs a real program key")
        if key in self._programs:
            raise ValueError("programme " + key + " is already registered")
        self._programs[key] = registry

    def get(self, program: str) -> Optional[LearningRegistry]:
        return self._programs.get((program or "").strip().upper())

    def keys(self) -> List[str]:
        return sorted(self._programs)

    def __len__(self) -> int:
        return len(self._programs)


#: Process-wide programme registry.
PROGRAMS = ProgramRegistry()
