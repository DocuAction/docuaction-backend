"""IQVIA Release-1 matching: `iqvia_hco_observation` rows -> `EntitySourceMatch`
rows, using the existing Release-1 matching rules in `source_matching.py`
unchanged -- this module only gathers the registry-side evidence those rules
need and calls them; it invents no new matching logic of its own.

HCO ONLY, DELIBERATELY. HCP observations are never matched to an
organisation entity by this module: the confirmed delivery carries no
populated HCP<->HCO affiliation data at all (dedicated AFFIL extract absent;
HCP_ADDR's own inline HOSP_AFFIL_1..5 slots are 0% populated), so there is no
honest path from an individual practitioner row to an organisation without
guessing. `match_hcp_snapshot` exists for the day that link is delivered and
is not called by anything in this pass.

NPPES TYPE EVIDENCE: INJECTED, NEVER FETCHED LIVE HERE
-----------------------------------------------------------
`evaluate_npi_match` needs to know whether NPPES calls an NPI Type 1
(individual) or Type 2 (organisation) before it will ever AUTO_APPROVE. This
module does not call NPPES itself -- no live external lookup is authorized
in this pass. `nppes_type_lookup` is injected (a callable, sync or async,
`npi -> {"enumeration_type": "NPI-1"|"NPI-2"} | None`); the caller supplies a
real one (wired to the existing NPPES connector, reusing cached evidence
where it already exists) when this runs for real. With no lookup supplied,
every match is correctly evidenced to the engine as "NPPES entity type not
evidenced" -- `evaluate_npi_match`'s own existing CANDIDATE branch, not a
new behavior added here -- never a fabricated Type-2.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce.source_matching import (evaluate_ccn_candidate,
                                                      evaluate_npi_match,
                                                      record_match)

NppesTypeLookup = Callable[[str], Union[Optional[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]]


@dataclass
class MatchRunSummary:
    snapshot_id: Any
    observations_considered: int = 0
    already_matched_skipped: int = 0
    auto_approved: int = 0
    candidates_written: int = 0   # entity_source_match rows, CANDIDATE status
    exceptions_written: int = 0   # entity_source_match rows, EXCEPTION status
    unmatchable_no_identifier: int = 0  # no NPI and no CCN -- nothing to evaluate
    zero_candidate_unwritten: int = 0   # EXCEPTION/CANDIDATE with no entity to
                                         # attach to; entity_id is NOT NULL on
                                         # EntitySourceMatch, so these are
                                         # counted here, not written as a row
    details: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["snapshot_id"] = str(self.snapshot_id)
        return d


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _entities_with_identifier(db, identifier_type: str, value: str) -> List[str]:
    rows = (await db.execute(
        select(reg.TefcaEntityIdentifier.entity_id).where(
            reg.TefcaEntityIdentifier.identifier_type == identifier_type,
            reg.TefcaEntityIdentifier.identifier_value == value))).scalars().all()
    return [str(r) for r in rows]


async def _already_matched_keys(db, snapshot_id) -> set:
    rows = (await db.execute(
        select(sm.EntitySourceMatch.source_record_key).where(
            sm.EntitySourceMatch.source_snapshot_id == snapshot_id))).scalars().all()
    return set(rows)


async def _observation_root_snapshot_id(db, snapshot: sm.SourceSnapshot):
    """`iqvia_import.py` always writes observation rows under the snapshot id
    that existed at REGISTRATION time (PENDING) -- `approve_snapshot` then
    creates a NEW row (a different id) that supersedes it, append-only,
    rather than reusing the id. A caller matching against the (now current,
    APPROVED) snapshot id would otherwise query observations under an id no
    row was ever staged against. Walk `supersedes_snapshot_id` back to the
    root (the one row with no predecessor) to find where the rows actually
    live."""
    current = snapshot
    while current.supersedes_snapshot_id is not None:
        current = await db.get(sm.SourceSnapshot, current.supersedes_snapshot_id)
    return current.id


async def match_hco_snapshot(
    db, *, snapshot_id, actor: str,
    nppes_type_lookup: Optional[NppesTypeLookup] = None,
    limit: Optional[int] = None,
) -> MatchRunSummary:
    """Evaluate every not-yet-matched `iqvia_hco_observation` row under one
    APPROVED HCO snapshot. Idempotent to call again: rows already matched
    (by `source_record_key`) are skipped, so a resumed or repeated run never
    double-writes."""
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError(f"no snapshot {snapshot_id}")
    if snapshot.source_system != sm.SOURCE_IQVIA_HCO:
        raise ValueError(f"snapshot {snapshot_id} is {snapshot.source_system}, not {sm.SOURCE_IQVIA_HCO}")
    if snapshot.status not in sm.SNAPSHOT_EFFECTIVE:
        raise ValueError(
            f"snapshot {snapshot_id} is {snapshot.status}; only an APPROVED "
            "(or its superseded-but-once-effective) snapshot may be matched "
            "-- a PENDING snapshot's rows are never read by anything")

    summary = MatchRunSummary(snapshot_id=snapshot_id)
    already = await _already_matched_keys(db, snapshot_id)
    observation_snapshot_id = await _observation_root_snapshot_id(db, snapshot)

    q = select(sm.IqviaHcoObservation).where(
        sm.IqviaHcoObservation.source_snapshot_id == observation_snapshot_id)
    if limit:
        q = q.limit(limit)
    observations = (await db.execute(q)).scalars().all()

    for obs in observations:
        summary.observations_considered += 1
        if obs.source_record_key in already:
            summary.already_matched_skipped += 1
            continue

        if obs.npi:
            registry_entities = await _entities_with_identifier(db, "npi", obs.npi)
            nppes_evidence = None
            if nppes_type_lookup is not None:
                nppes_evidence = await _maybe_await(nppes_type_lookup(obs.npi))
            decision = evaluate_npi_match(
                source_npi=obs.npi, nppes_evidence=nppes_evidence,
                registry_entities_with_npi=registry_entities,
                source_record_key=obs.source_record_key)
        elif obs.ccn:
            registry_entities = await _entities_with_identifier(db, "ccn", obs.ccn)
            decision = evaluate_ccn_candidate(
                source_ccn=obs.ccn, source_record_key=obs.source_record_key,
                registry_entities_with_ccn=registry_entities)
        else:
            summary.unmatchable_no_identifier += 1
            continue

        candidate_ids = (
            [decision["entity_id"]] if decision["status"] == sm.MATCH_AUTO_APPROVED
            else list(decision.get("candidates") or []))

        if not candidate_ids:
            # Nothing to attach the row to -- `entity_source_match.entity_id`
            # is NOT NULL, so there is nowhere to persist "zero candidates"
            # as a row. Counted, not silently dropped; the observation row
            # itself stays in `iqvia_hco_observation`, visible for an
            # analyst's own search/lookup outside this automatic pass.
            summary.zero_candidate_unwritten += 1
            summary.details.append({"source_record_key": obs.source_record_key,
                                    "status": decision["status"], "candidates": 0})
            continue

        for entity_id in candidate_ids:
            await record_match(db, entity_id=entity_id, source_system=sm.SOURCE_IQVIA_HCO,
                               snapshot_id=snapshot_id, decision=decision,
                               proposed_by=actor, commit=False)
        await db.commit()

        if decision["status"] == sm.MATCH_AUTO_APPROVED:
            summary.auto_approved += 1
        elif decision["status"] == sm.MATCH_EXCEPTION:
            summary.exceptions_written += len(candidate_ids)
        else:
            summary.candidates_written += len(candidate_ids)
        summary.details.append({"source_record_key": obs.source_record_key,
                                "status": decision["status"], "candidates": len(candidate_ids)})

    return summary


async def match_hcp_snapshot(db, *, snapshot_id, actor: str) -> MatchRunSummary:
    """Not implemented against real data by this pass -- see module
    docstring. Raises rather than silently no-op'ing, so a future caller
    cannot mistake "ran, matched nothing" for "cannot run yet"."""
    raise NotImplementedError(
        "HCP-to-organisation matching has no honest basis today: the "
        "delivered extract carries no populated HCP<->HCO affiliation data "
        "(dedicated AFFIL file absent; HCP_ADDR's own HOSP_AFFIL_* slots are "
        "0% populated). Implement this once a populated affiliation source "
        "is actually delivered, not before.")
