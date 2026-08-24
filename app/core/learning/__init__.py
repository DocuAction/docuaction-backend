"""Reusable operator-guidance framework. No programme vocabulary lives here.

WHAT THIS IS FOR
    A federal review programme needs its operators to understand what the system
    is telling them, and — more importantly — what it is *not* telling them. That
    second half is why guidance belongs in the codebase rather than in a wiki: a
    lesson that says "a source outage is not an adverse finding" is worthless if
    the code changes and the lesson does not.

    So content declares the vocabulary it teaches, and a test asserts that every
    term it names still exists. Guidance that has drifted from the code fails the
    build instead of quietly misleading an analyst.

WHAT THIS IS NOT
    Not a learning management system. There is no enrolment, no progress
    tracking, no scoring history and no certification record. Those were
    explicitly excluded, and none of them is needed to answer "what does this
    screen mean and what am I allowed to conclude from it".

WHERE THE UI LIVES
    Not here. This repository is the backend; the frontend is a separate
    codebase. This module supplies content and lookup; rendering, navigation and
    tooltip placement belong to whoever builds the screen.
"""
from .framework import (  # noqa: F401
    KNOWLEDGE_VERSION,
    PROGRAMS,
    Classification,
    ContextualHelp,
    Glossary,
    GlossaryTerm,
    KnowledgeCheck,
    LearningRegistry,
    Lesson,
    Module,
    ProgramRegistry,
    ProhibitedConclusion,
    Role,
    Statement,
)

__all__ = [
    "KNOWLEDGE_VERSION", "PROGRAMS", "Classification", "ContextualHelp",
    "Glossary", "GlossaryTerm", "KnowledgeCheck", "LearningRegistry", "Lesson",
    "Module", "ProgramRegistry", "ProhibitedConclusion", "Role", "Statement",
]
