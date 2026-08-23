"""Which evidence version is CURRENT, decided in exactly one place.

THE PROBLEM THIS PREVENTS
    Phase 6 ran once and produced 164,962 observations. Phase 6.5 found two
    defects in that run, and correcting them means emitting a second set. Both
    sets are now in `tefca_dimension_evidence`. If each report decided for
    itself which rows to read, some would read both and double-count the entire
    population, and the ones that got it right would be right by accident.

    So the rule lives here and nowhere else:

        CURRENT  = the newest APPROVED rule_version
        HISTORY  = every earlier rule_version, still queryable, never deleted

WHY THE ORIGINAL RUN IS KEPT
    It is an auditable execution that actually happened. Rewriting it would
    destroy the answer to "what did the system observe on the day it ran?",
    which is the question an auditor asks first. A correction is a NEW version
    that supersedes the old one by being newer — not an edit that erases it.

ADDING A VERSION
    Append it to `APPROVED_RULE_VERSIONS`, newest last. Nothing else changes:
    `current_rule_version()` and the two filters below follow automatically.
"""
from __future__ import annotations

from typing import Any, List

#: Oldest first, newest last. Every version ever emitted, all still queryable.
APPROVED_RULE_VERSIONS: List[str] = [
    # The original Phase-6 population run. Preserved unchanged. Its PPEF
    # relationship hops carry component names in `relationship_type` and no
    # `ppef_component`, and it persisted no address comparison — the two
    # defects 1.1.0 exists to correct.
    "phase6-bulk-1.0.0",
    # Phase 6.5 correction: PPEF relationships re-expressed in the approved
    # PpefRelationship vocabulary with full component and source-row provenance,
    # the two previously unrepresented PPEF components added, and address
    # comparison persisted as evidence rather than computed in a report.
    "phase6-bulk-1.1.0",
]


def current_rule_version() -> str:
    """The one version reports and triage must read."""
    return APPROVED_RULE_VERSIONS[-1]


def historical_rule_versions() -> List[str]:
    """Everything superseded. Queryable, never current, never deleted."""
    return APPROVED_RULE_VERSIONS[:-1]


def current_filter(column: Any) -> Any:
    """SQLAlchemy predicate selecting only current-version rows.

    Use this on every population query. Passing a bare `rule_version ==` literal
    at the call site is how the two versions get mixed.
    """
    return column == current_rule_version()


def historical_filter(column: Any, version: str) -> Any:
    """SQLAlchemy predicate selecting one specific historical version."""
    if version not in APPROVED_RULE_VERSIONS:
        raise ValueError(
            f"{version!r} is not an approved rule version. Known versions: "
            f"{APPROVED_RULE_VERSIONS}")
    return column == version
