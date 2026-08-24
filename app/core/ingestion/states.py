"""Ingestion lifecycle states, and how they relate to the ones already in use.

WHY THIS IS NOT A NEW VOCABULARY
────────────────────────────────
`tefca_ppef_ingest_jobs` already runs a lifecycle — QUEUED, STARTED,
DOWNLOADING, VALIDATING, LOADING, COMPLETE, FAILED — with a heartbeat, an
attempt count and a reaper. That is a working, tested vocabulary and this module
does not replace it.

It does two things that vocabulary cannot:

  * it names two outcomes the PPEF set collapses. `COMPLETE` cannot distinguish
    "loaded, nothing wrong" from "loaded, and 412 records raised issues", and
    `FAILED` cannot distinguish "the source timed out, try again" from "the
    schema changed, a human must look". A retry loop that cannot tell those
    apart either retries forever or gives up too early.

  * it is program-neutral, so a second program's ingestion does not have to
    borrow a name with `PPEF` in its history.

`PPEF_STATE_MAP` states the correspondence explicitly, and
`tests/test_ingestion_framework.py` fails if a PPEF state stops being covered.
Two vocabularies that map are one vocabulary with two spellings; two that drift
are a defect waiting to happen.
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class IngestionState(str, Enum):
    """Where an ingestion run is. Ordered as the run proceeds."""

    QUEUED = "QUEUED"
    ACQUIRING = "ACQUIRING"
    ACQUIRED = "ACQUIRED"
    PARSING = "PARSING"
    VALIDATING = "VALIDATING"

    #: Finished, and every record was accepted with no finding recorded.
    COMPLETED = "COMPLETED"
    #: Finished, and the issue ledger has entries. This is a SUCCESS: the
    #: delivery was accepted and its problems were recorded. Collapsing it into
    #: COMPLETED loses the fact that somebody needs to look, and collapsing it
    #: into a failure would throw away a delivery that is perfectly usable.
    COMPLETED_WITH_ISSUES = "COMPLETED_WITH_ISSUES"

    #: The run stopped for a reason that may not recur — a timeout, a 503, a
    #: connection reset. Retrying is legitimate.
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    #: The run stopped for a reason that will recur until something changes —
    #: a 404, a schema that no parser understands, an artefact that fails its
    #: own checksum. Retrying wastes the window and hides the problem.
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


TERMINAL_STATES: FrozenSet[IngestionState] = frozenset({
    IngestionState.COMPLETED,
    IngestionState.COMPLETED_WITH_ISSUES,
    IngestionState.RETRYABLE_FAILURE,
    IngestionState.PERMANENT_FAILURE,
})

ACTIVE_STATES: FrozenSet[IngestionState] = frozenset({
    IngestionState.QUEUED,
    IngestionState.ACQUIRING,
    IngestionState.ACQUIRED,
    IngestionState.PARSING,
    IngestionState.VALIDATING,
})

SUCCESS_STATES: FrozenSet[IngestionState] = frozenset({
    IngestionState.COMPLETED,
    IngestionState.COMPLETED_WITH_ISSUES,
})

#: Only one terminal state invites another attempt.
RETRYABLE_STATES: FrozenSet[IngestionState] = frozenset({
    IngestionState.RETRYABLE_FAILURE,
})

#: The order a healthy run passes through. Used to reject a backwards
#: transition, which is how a lost heartbeat used to look like progress.
_ORDER = [
    IngestionState.QUEUED,
    IngestionState.ACQUIRING,
    IngestionState.ACQUIRED,
    IngestionState.PARSING,
    IngestionState.VALIDATING,
]


def may_transition(current: IngestionState, nxt: IngestionState) -> bool:
    """Is this a legal move?

    Forward through the active sequence, or from any active state to a terminal
    one. Nothing leaves a terminal state: a completed run that starts reporting
    progress again means two workers hold the same job, and the transition is
    where that should be caught.
    """
    if current in TERMINAL_STATES:
        return False
    if nxt in TERMINAL_STATES:
        return True
    return _ORDER.index(nxt) > _ORDER.index(current)


#: How the existing PPEF job states line up. `STARTED` and `DOWNLOADING` both
#: describe acquisition; `LOADING` is persistence, which happens during
#: validation in this framework's ordering. `COMPLETE` maps to the clean
#: outcome — a PPEF job carrying issues is reported through its own counts.
PPEF_STATE_MAP = {
    "QUEUED": IngestionState.QUEUED,
    "STARTED": IngestionState.ACQUIRING,
    "DOWNLOADING": IngestionState.ACQUIRING,
    "VALIDATING": IngestionState.VALIDATING,
    "LOADING": IngestionState.VALIDATING,
    "COMPLETE": IngestionState.COMPLETED,
    "FAILED": IngestionState.PERMANENT_FAILURE,
}


def from_ppef_state(state: str) -> IngestionState:
    """Translate a stored PPEF job state. Unknown values are not guessed."""
    try:
        return PPEF_STATE_MAP[state]
    except KeyError:
        raise ValueError(
            f"unmapped PPEF job state {state!r}. Add it to PPEF_STATE_MAP "
            f"rather than inventing a state here.") from None
