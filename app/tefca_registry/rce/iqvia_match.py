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

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.tefca_registry import models as reg
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce.source_matching import (evaluate_ccn_candidate,
                                                      evaluate_npi_match,
                                                      record_match)

NppesTypeLookup = Callable[[str], Union[Optional[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]]

#: Observations evaluated per transaction. Bounds memory (the 2026-11-06 full-snapshot
#: run loaded all 154,683 rows at once and took the container past its memory limit)
#: and bounds the work lost to a mid-run crash to one page.
DEFAULT_PAGE_SIZE = 500
#: `MatchRunSummary.details` entries kept. The counters stay exact for the whole
#: run; only the per-observation detail list is capped, because it is returned in
#: the HTTP response and previously grew by one dict per observation.
MAX_DETAILS = 200


class MatchInProgressError(RuntimeError):
    """Another matcher currently holds this snapshot's advisory lock."""


@dataclass
class MatchRunSummary:
    snapshot_id: Any
    observations_considered: int = 0
    already_matched_skipped: int = 0
    auto_approved: int = 0
    candidates_written: int = 0   # entity_source_match rows, CANDIDATE status
    exceptions_written: int = 0   # entity_source_match rows, EXCEPTION status
    unmatchable_no_identifier: int = 0  # no NPI and no CCN -- nothing to evaluate
    newly_evaluated: int = 0            # observations evaluated this run (skips excluded);
                                        # `limit` counts THESE, so a limited rerun resumes
    pages: int = 0                      # transactions committed (one per page)
    details_truncated: bool = False     # True when details stopped at MAX_DETAILS
    zero_candidate_unwritten: int = 0   # EXCEPTION/CANDIDATE with no entity to
                                         # attach to; entity_id is NOT NULL on
                                         # EntitySourceMatch, so these are
                                         # counted here, not written as a row
    next_after_key: Optional[str] = None  # last source_record_key this run
                                          # fully processed; pass it back as
                                          # `after_key` to resume WITHOUT
                                          # re-reading earlier rows (rows that
                                          # write no match row would otherwise
                                          # consume every later limited run's
                                          # budget -- round-2 review finding)
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


def _detail(summary: MatchRunSummary, entry: Dict[str, Any]) -> None:
    if len(summary.details) < MAX_DETAILS:
        summary.details.append(entry)
    else:
        summary.details_truncated = True


async def match_hco_snapshot(
    db, *, snapshot_id, actor: str,
    nppes_type_lookup: Optional[NppesTypeLookup] = None,
    limit: Optional[int] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    after_key: Optional[str] = None,
) -> MatchRunSummary:
    """Evaluate every not-yet-matched `iqvia_hco_observation` row under one
    APPROVED HCO snapshot. Idempotent to call again: rows already matched
    (by `source_record_key`) are skipped, so a resumed or repeated run never
    double-writes.

    Paged, not loaded whole: observations are read in `page_size` keyset pages
    (ordered by `source_record_key`, unique per snapshot) and each page is one
    transaction, so memory stays bounded regardless of snapshot size and a
    crash loses at most one page of writes. The whole run holds a
    SESSION-level advisory lock on this snapshot's key (a dedicated
    connection, released in `finally`); a concurrent run raises
    `MatchInProgressError` up front, never mid-run, so a run that has
    committed pages always returns its summary and resume cursor
    (round-3 review finding). The decisions written per observation are
    byte-identical to the unpaged implementation this replaces.

    `limit` counts NEWLY EVALUATED observations, never already-matched skips --
    otherwise a repeated limited run would spend its whole budget re-walking
    the rows it already matched and never advance (CodeRabbit round-1 finding
    on this change, verified and fixed with the resume test).

    `after_key` is the resume cursor (round-2 finding, verified): rows that
    write no match row (no identifier, or zero candidates) are legitimately
    re-evaluated by a fresh full run, but they count against `limit`, so
    enough of them early in key order would starve every later limited run
    and each repeated run re-reads all earlier pages regardless. A caller
    doing bounded installments passes the previous summary's
    `next_after_key` back as `after_key` and continues where it stopped;
    omitting it keeps the fresh-full-run contract unchanged."""
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
    observation_snapshot_id = await _observation_root_snapshot_id(db, snapshot)
    lock_key = f"iqvia-hco-match:{snapshot_id}"
    last_key: Optional[str] = after_key

    # Run-level guard (round-3 review finding, verified): the advisory lock
    # is SESSION-level, on a DEDICATED connection, and held for the whole
    # run -- a concurrent request is refused up front and can never
    # interleave between page commits. Interleaving both re-evaluated
    # no-match-row observations and could raise MatchInProgressError in a
    # run that had already committed pages, so the route answered 409
    # without the summary and the resume cursor was lost. Per-page
    # transactions (and their bounded memory) are unchanged. The lock is
    # released in `finally` BEFORE the connection returns to the pool -- a
    # pooled connection would otherwise carry the session lock out with
    # it; if the process dies instead, the server releases the lock when
    # the connection does.
    bind = db.bind
    run_guard_engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    guard = await run_guard_engine.connect()
    # AUTOCOMMIT (round-4 review finding): SQLAlchemy autobegins a transaction
    # on the first execute (the lock call), and the guard connection would
    # then sit idle-in-transaction for the whole multi-minute run -- an
    # idle_in_transaction_session_timeout would kill it (releasing the lock
    # mid-run), and it would pin the vacuum horizon. Advisory session locks
    # need no transaction at all.
    guard = await guard.execution_options(isolation_level="AUTOCOMMIT")
    got = False
    try:
        got = (await guard.execute(
            text("select pg_try_advisory_lock(hashtextextended(:k, 0))"),
            {"k": lock_key})).scalar()
        if not got:
            raise MatchInProgressError(
                f"another matching run currently holds the lock for snapshot {snapshot_id}")

        while True:
            if limit is not None and summary.newly_evaluated >= limit:
                break

            q = (select(sm.IqviaHcoObservation)
                 .where(sm.IqviaHcoObservation.source_snapshot_id == observation_snapshot_id)
                 .order_by(sm.IqviaHcoObservation.source_record_key)
                 .limit(page_size))
            if last_key is not None:
                q = q.where(sm.IqviaHcoObservation.source_record_key > last_key)
            observations = (await db.execute(q)).scalars().all()
            if not observations:
                # Nothing was written in this transaction; end it cleanly. Commit,
                # not rollback: rollback would expire every object loaded on this
                # session, breaking callers that still hold ORM instances.
                await db.commit()
                break

            page_keys = [o.source_record_key for o in observations]
            already = set((await db.execute(
                select(sm.EntitySourceMatch.source_record_key).where(
                    sm.EntitySourceMatch.source_snapshot_id == snapshot_id,
                    sm.EntitySourceMatch.source_record_key.in_(page_keys)))).scalars().all())

            for obs in observations:
                if limit is not None and summary.newly_evaluated >= limit:
                    break
                summary.observations_considered += 1
                # The resume cursor advances over every row this run fully
                # processes -- matched skips, no-identifier and zero-candidate
                # rows included -- NOT over rows a mid-page limit break left
                # unprocessed. Without it, rows that write no match row are
                # re-evaluated by every later run and can starve a limited run.
                summary.next_after_key = obs.source_record_key
                if obs.source_record_key in already:
                    summary.already_matched_skipped += 1
                    continue
                summary.newly_evaluated += 1

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
                    _detail(summary, {"source_record_key": obs.source_record_key,
                                      "status": decision["status"], "candidates": 0})
                    continue

                for entity_id in candidate_ids:
                    await record_match(db, entity_id=entity_id, source_system=sm.SOURCE_IQVIA_HCO,
                                       snapshot_id=snapshot_id, decision=decision,
                                       proposed_by=actor, commit=False)

                if decision["status"] == sm.MATCH_AUTO_APPROVED:
                    summary.auto_approved += 1
                elif decision["status"] == sm.MATCH_EXCEPTION:
                    summary.exceptions_written += len(candidate_ids)
                else:
                    summary.candidates_written += len(candidate_ids)
                _detail(summary, {"source_record_key": obs.source_record_key,
                                  "status": decision["status"], "candidates": len(candidate_ids)})

            last_key = page_keys[-1]
            summary.pages += 1
            await db.commit()
    finally:
        try:
            if got:
                # A session advisory lock SURVIVES the pool's reset-on-return
                # rollback (round-4 review finding): if the unlock raises or
                # reports false, returning this connection to the pool would
                # leave the lock held on a live pooled connection and block
                # every later run. invalidate() closes the real DBAPI
                # connection, so the server itself releases the lock.
                released = False
                try:
                    released = bool((await guard.execute(
                        text("select pg_advisory_unlock(hashtextextended(:k, 0))"),
                        {"k": lock_key})).scalar())
                finally:
                    if not released:
                        await guard.invalidate()
        finally:
            await guard.close()

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
