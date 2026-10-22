"""Entity Identity & Location Intelligence — CORE, isolated, OFF by default.

WHAT THIS IS
    The evidence-intelligence foundation that lets an analyst answer, for one
    delivered organisation: who is it, what names does it operate under, what
    locations are associated with it, what relationships, what independent
    evidence corroborates or conflicts, what changed since the prior review,
    can the difference be explained, and what still needs a person.

WHERE IT STOPS
    SOURCE DATA → INDEPENDENT EVIDENCE → SYSTEM EVIDENCE ASSESSMENT → (analyst)
    The engine produces observations, comparisons, deltas and a SYSTEM EVIDENCE
    ASSESSMENT. It never produces a determination, a contractual category, a
    pass/fail, or a verdict. `assessment.py` enforces that vocabulary.

BOUNDARY
    Core knows nothing about ONC, RCE, TEFCA, QHINs, NPPES, IQVIA, Google or any
    state registry. Those are adapters (app/evidence_sources/*) or program
    configuration. Relationship kinds, location roles and name kinds are
    controlled vocabularies here; a program supplies its own relationship
    semantics without editing this package.

ISOLATION
    * Every entry point is gated by `flags.require_enabled()`; with the master
      flag False nothing here runs, calls out, persists or registers.
    * Persistence uses its OWN declarative base (`models.EntityIntelligenceBase`)
      so the application's startup `create_all()` and Alembic autogenerate —
      both bound to `app.core.database.Base` — cannot touch the shared schema.
    * No router, no background job, no report reads this package.
"""
from .flags import FeatureDisabled, entity_intelligence_enabled, require_enabled  # noqa: F401

__all__ = ["FeatureDisabled", "entity_intelligence_enabled", "require_enabled"]
