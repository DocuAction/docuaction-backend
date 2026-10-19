"""Read-only batch comparison: a population of registry entities (e.g. the
ones September's `ReviewRecord`s point to) against the currently APPROVED
`IQVIA_HCO` snapshot's observations, already in our database.

Answers "how many of our records already have a corresponding IQVIA
observation" WITHOUT writing anything and WITHOUT requiring
`ENABLE_IQVIA_AFFILIATION_CONSUMPTION` -- that is a different flag, gating
the live advisory *lookup* endpoints (`iqvia_affiliation_routes.py`), not
this static comparison. It still honors `ENABLE_IQVIA_SOURCES` + the
reviewer floor (`source_matching.licensed_access_allowed`), the same gate
every other IQVIA-observation reader in this codebase respects.

DELIBERATELY NOT A SQL JOIN ACROSS THE LICENSED/REGISTRY BOUNDARY.
`snapshot_models.py`'s own module comment says the IQVIA observation tables
are "never joined to registry tables directly ... `iqvia_match.py` reads
these and writes `EntitySourceMatch` rows, which IS the only sanctioned
link to a registry entity." A raw `SELECT ... JOIN iqvia_hco_observation ON
...` would cross that boundary. Instead this loads each side's identifiers
with its own bounded, indexed batch query and compares them in application
memory -- a hash-join, not a SQL join -- which replaces N per-record round
trips with two bounded batch reads while keeping the separation intact.

Four outcomes, not three, and that is a deliberate choice: the original ask
named three buckets (successful match / missing / ambiguous), but
collapsing "the record HAS an identifier but no IQVIA observation carries
it" into either MATCHED or NO_IDENTIFIER would misrepresent the data -- an
identifier present with zero IQVIA hits is an expected, common outcome
(IQVIA's extract does not necessarily cover every one of our entities), not
an error and not the same thing as "nothing to compare." So:

  MATCHED      -- exactly one NPI on our side, held by exactly one approved
                 IQVIA_HCO observation. The only automatic bucket, mirroring
                 `evaluate_npi_match`'s own "exactly one" gate -- but this
                 tool makes no NPPES-type determination (no live lookup is
                 performed here), so MATCHED means "an IQVIA record for this
                 NPI exists," not "this would auto-approve."
  NO_IDENTIFIER -- the record's entity carries neither NPI nor CCN (or the
                 record names no entity at all). The "missing" bucket asked
                 for: no usable identifier on the record.
  NOT_FOUND    -- an identifier is present but zero approved IQVIA
                 observations carry it. Expected and common; not an error.
  AMBIGUOUS    -- more than one candidate on either side: our entity itself
                 carries >1 identifier of the same type, an NPI is not
                 unique in the approved extract, a CCN is not unique in the
                 approved extract, or the only evidence is a CCN at all --
                 CCN is CANDIDATE-only by this codebase's own existing rule
                 (`evaluate_ccn_candidate`), never auto-matched, so even a
                 single clean CCN hit lands here, not in MATCHED.

Writes nothing: no `EntitySourceMatch` row, no `ReviewRecord` change, no
snapshot row, no commit.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce.source_matching import (current_approved_snapshot,
                                                      licensed_access_allowed,
                                                      licensed_source_enabled)

BUCKET_MATCHED = "MATCHED"
BUCKET_NO_IDENTIFIER = "NO_IDENTIFIER"
BUCKET_NOT_FOUND = "NOT_FOUND"
BUCKET_AMBIGUOUS = "AMBIGUOUS"

#: How many entity ids go in one identifier-lookup query. Bounded so a
#: 24,589-record population still costs a handful of round trips, not one
#: per record.
DEFAULT_BATCH_SIZE = 1000


class IqviaSourceNotAvailable(RuntimeError):
    """`ENABLE_IQVIA_SOURCES` is off, the caller lacks the reviewer floor, or
    no APPROVED `IQVIA_HCO` snapshot exists to compare against."""


@dataclass
class BatchComparisonSummary:
    snapshot_id: Any
    considered: int = 0
    matched: int = 0
    no_identifier: int = 0
    not_found: int = 0
    ambiguous: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["snapshot_id"] = str(self.snapshot_id) if self.snapshot_id else None
        return d


async def _observation_root_snapshot_id(db, snapshot: sm.SourceSnapshot):
    """Same walk as `iqvia_match.py`'s private helper of the same purpose:
    observations are always staged under the snapshot id that existed at
    registration time (PENDING); approval creates a new, superseding row
    rather than reusing the id."""
    current = snapshot
    while current.supersedes_snapshot_id is not None:
        current = await db.get(sm.SourceSnapshot, current.supersedes_snapshot_id)
    return current.id


def _require_licensed_access(user) -> None:
    if user is not None:
        decision = licensed_access_allowed(user)
        if not decision["allowed"]:
            raise IqviaSourceNotAvailable(decision["reason"])
        return
    if not licensed_source_enabled():
        raise IqviaSourceNotAvailable(
            "ENABLE_IQVIA_SOURCES is off; this comparison reads the same "
            "licensed observation tables every other IQVIA reader does, and "
            "respects the same gate even with no consumption involved")


async def compare_entities_to_approved_iqvia_hco(
    db, *, entity_ids: Iterable[Any], user=None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> BatchComparisonSummary:
    """Compare `entity_ids` (e.g. the entities September's `ReviewRecord`s
    point to -- deriving that population is the caller's job, typically a
    query against `ReviewRecord.entity_id` scoped to the delivery/date range
    in question) against the currently APPROVED `IQVIA_HCO` snapshot.

    Read-only. Raises `IqviaSourceNotAvailable` rather than silently
    returning an empty/misleading summary when the source isn't readable at
    all (flag off, role too low, or no approved snapshot) -- the caller
    should surface that distinctly from "zero records matched"."""
    _require_licensed_access(user)

    snapshot = await current_approved_snapshot(db, sm.SOURCE_IQVIA_HCO)
    if snapshot is None:
        raise IqviaSourceNotAvailable("no APPROVED IQVIA_HCO snapshot exists")

    entity_ids = [e for e in entity_ids]
    summary = BatchComparisonSummary(snapshot_id=snapshot.id)
    if not entity_ids:
        return summary

    # --- side A: our registry identifiers for this population, bounded batches ---
    entity_npi: Dict[Any, List[str]] = defaultdict(list)
    entity_ccn: Dict[Any, List[str]] = defaultdict(list)
    for start in range(0, len(entity_ids), batch_size):
        chunk = entity_ids[start:start + batch_size]
        rows = (await db.execute(
            select(reg.TefcaEntityIdentifier.entity_id,
                   reg.TefcaEntityIdentifier.identifier_type,
                   reg.TefcaEntityIdentifier.identifier_value)
            .where(reg.TefcaEntityIdentifier.entity_id.in_(chunk),
                   reg.TefcaEntityIdentifier.identifier_type.in_(("npi", "ccn")))
        )).all()
        for entity_id, id_type, id_value in rows:
            value = (id_value or "").strip()
            if not value:
                continue
            if id_type == "npi":
                entity_npi[entity_id].append(value)
            elif id_type == "ccn":
                entity_ccn[entity_id].append(value)

    # --- side B: the approved IQVIA observation set, one bounded query ---
    observation_snapshot_id = await _observation_root_snapshot_id(db, snapshot)
    obs_rows = (await db.execute(
        select(sm.IqviaHcoObservation.npi, sm.IqviaHcoObservation.ccn)
        .where(sm.IqviaHcoObservation.source_snapshot_id == observation_snapshot_id)
    )).all()
    iqvia_npi_counts: Dict[str, int] = defaultdict(int)
    iqvia_ccn_counts: Dict[str, int] = defaultdict(int)
    for npi, ccn in obs_rows:
        if npi:
            iqvia_npi_counts[npi.strip()] += 1
        if ccn:
            iqvia_ccn_counts[ccn.strip()] += 1

    # --- compare, in memory, no further DB round trips ---
    for entity_id in entity_ids:
        summary.considered += 1
        npis = entity_npi.get(entity_id, [])
        ccns = entity_ccn.get(entity_id, [])

        if not npis and not ccns:
            summary.no_identifier += 1
            summary.details.append({"entity_id": str(entity_id), "bucket": BUCKET_NO_IDENTIFIER})
            continue

        if len(npis) > 1 or len(ccns) > 1:
            summary.ambiguous += 1
            summary.details.append({
                "entity_id": str(entity_id), "bucket": BUCKET_AMBIGUOUS,
                "reason": "entity carries more than one identifier of the same type"})
            continue

        if npis:
            hits = iqvia_npi_counts.get(npis[0], 0)
            if hits == 1:
                summary.matched += 1
                summary.details.append({"entity_id": str(entity_id), "bucket": BUCKET_MATCHED,
                                        "method": "npi"})
                continue
            if hits > 1:
                summary.ambiguous += 1
                summary.details.append({
                    "entity_id": str(entity_id), "bucket": BUCKET_AMBIGUOUS,
                    "reason": "npi value is not unique in the approved IQVIA extract"})
                continue
            if not ccns:
                summary.not_found += 1
                summary.details.append({"entity_id": str(entity_id), "bucket": BUCKET_NOT_FOUND,
                                        "method": "npi"})
                continue

        # CCN-only, or NPI present but unmatched and a CCN is also available.
        hits = iqvia_ccn_counts.get(ccns[0], 0)
        if hits >= 1:
            # CANDIDATE-only by this codebase's own rule -- never automatic,
            # same posture whether one or many observations carry it.
            summary.ambiguous += 1
            summary.details.append({
                "entity_id": str(entity_id), "bucket": BUCKET_AMBIGUOUS,
                "reason": "ccn-only evidence; CCN is never auto-matched"
                          if hits == 1 else
                          "ccn value is not unique in the approved IQVIA extract"})
        else:
            summary.not_found += 1
            summary.details.append({"entity_id": str(entity_id), "bucket": BUCKET_NOT_FOUND,
                                    "method": "ccn"})

    return summary
