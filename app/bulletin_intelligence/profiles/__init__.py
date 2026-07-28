"""Bulletin Intelligence — Boolean search profiles (Phase 2).

Database-driven Boolean queries with a hardcoded fallback. Additive: with an empty
table the behaviour is byte-identical to the previous hardcoded constants.
"""

from app.bulletin_intelligence.profiles.boolean_profiles import (  # noqa: F401
    PROFILES,
    seed_defaults,
    refresh_from_db,
    profiles_status,
)
