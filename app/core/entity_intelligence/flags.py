"""Feature gates for the isolated capability.

Python has no FeatureGate attribute; gating is explicit at every boundary that
could do work: service entry, adapter/connector entry, job entry. Each reads the
application Settings (environment-backed) at call time, never at import time,
so a test can flip a flag with monkeypatch and production cannot be enabled by
a stale import-time value.

The flags are declared on `app.core.config.Settings` with default False. This
module deliberately has no other dependency.
"""
from __future__ import annotations

from typing import Optional

MASTER = "ENTITY_INTELLIGENCE_ENABLED"
NPPES = "NPPES_IDENTITY_CORROBORATION_ENABLED"
IQVIA = "IQVIA_EVIDENCE_ENABLED"
GOOGLE = "GOOGLE_ADDRESS_INTELLIGENCE_ENABLED"
STATE_REGISTRY = "STATE_REGISTRY_INTELLIGENCE_ENABLED"

ALL_FLAGS = (MASTER, NPPES, IQVIA, GOOGLE, STATE_REGISTRY)


class FeatureDisabled(RuntimeError):
    """Raised at a gated boundary when the capability is off.

    Raised, not silently skipped: a caller that reaches a gated boundary while
    the feature is off is a wiring mistake worth surfacing, and a silent no-op
    would let "ran, produced nothing" masquerade as "the feature is enabled".
    """


def _settings():
    from app.core.config import settings
    return settings


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _as_bool(value) -> bool:
    """Strict: only True or a recognised true-string enables. Anything else —
    including the strings "false", "no", "maybe", "" and None — is False.
    `bool("false")` is True in Python, which is exactly the mistake this
    prevents."""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in TRUE_VALUES
    return False


def flag_enabled(name: str) -> bool:
    if name not in ALL_FLAGS:
        raise ValueError(f"unknown entity-intelligence flag {name!r}")
    return _as_bool(getattr(_settings(), name, False))


def entity_intelligence_enabled() -> bool:
    return flag_enabled(MASTER)


def source_enabled(flag: Optional[str]) -> bool:
    """A source is usable only when the master AND its own flag are on."""
    if not entity_intelligence_enabled():
        return False
    return True if flag is None else flag_enabled(flag)


def require_enabled(flag: Optional[str] = None, *, boundary: str = "") -> None:
    """Gate a service, connector or job entry point."""
    if not entity_intelligence_enabled():
        raise FeatureDisabled(
            f"{boundary or 'entity intelligence'}: {MASTER} is off")
    if flag is not None and not flag_enabled(flag):
        raise FeatureDisabled(f"{boundary or 'entity intelligence'}: {flag} is off")
