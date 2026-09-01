"""Which quality run is CURRENT for a delivery, decided in exactly one place.

THE PROBLEM THIS PREVENTS
─────────────────────────
    A delivery may be quality-run more than once — `RceIngestionRun` exists
    precisely because "a delivery may be processed repeatedly as rules change".
    Every run writes a FULL set of issues for the delivery, so after two runs
    `rce_issues` holds two complete assessments of the same 23,566 records.

    Every operational reader filtered on `source_intake_id` alone. Two runs
    therefore presented a DOUBLED population: issue counts, records-affected,
    severity charts, holds, and the HUMAN_REQUIRED workload an analyst would be
    handed. Measured on synthetic data: run 1 wrote 17 issues, run 2 wrote 17,
    and the unfiltered operational query returned 34.

    So the rule lives here and nowhere else:

        CURRENT  = the most recently COMPLETED run for that intake
        HISTORY  = every earlier run, still queryable, never deleted

THIS MIRRORS `app.Tefca.evidence_version`, AND DIFFERS IN ONE WAY
─────────────────────────────────────────────────────────────────
    That module solves the identical problem for `tefca_dimension_evidence`,
    and its reasoning applies unchanged: "if each report decided for itself
    which rows to read, some would read both and double-count the entire
    population, and the ones that got it right would be right by accident."

    It selects currentness from a CODE-DECLARED list of approved rule versions,
    because evidence generations are a property of the code that produced them.
    A quality run is not: it is a database row created at run time, one per
    execution, with no code-declared version. So currentness here is a QUERY
    over `rce_ingestion_runs` rather than a constant, and it is scoped per
    intake — two deliveries have independent current runs.

WHY "MOST RECENTLY COMPLETED", AND WHAT IT IS NOT
─────────────────────────────────────────────────
    `run_status` is the lifecycle that already exists (RUNNING / COMPLETE /
    FAILED) and `completed_at` is written in the same statement that sets
    COMPLETE, so it is exactly the instant the assessment became final.

    Selection is NOT `MAX(created_at)`. It is gated on the run having actually
    COMPLETED:

      * a RUNNING run never becomes current — it has not finished, and its
        issues are not committed;
      * a FAILED run never becomes current;
      * an aborted run cannot become current at all, because the run row and
        its issues commit in ONE transaction: a run that dies leaves no row and
        no issues, so the previous completed run simply remains current.

    Ordering is `completed_at DESC, started_at DESC, id DESC`. The trailing
    `id` is not decoration: `completed_at` comes from the application clock, so
    two runs finishing in the same microsecond would otherwise be an ambiguous
    ordering. The id makes the order total, so "current" is always exactly one
    row.

    THAT IS DETERMINISM, NOT SAFETY. Two concurrent runs over ONE intake both
    complete, and which of them finishes last is a race — the selector will
    answer consistently, but the answer depends on timing. Concurrent runs over
    one delivery should be serialised operationally; this module does not, and
    cannot, decide that.

EVERY ISSUE CARRIES ITS RUN
    `rce_issues.run_id` is nullable in the schema but is written on every issue
    by `quality_engine._issue_row`, and is populated on all 36,916 delivered
    rows. A NULL would be invisible to these filters, which is why
    `orphaned_issue_filter` exists — so the condition is detectable rather than
    silent.
"""

from __future__ import annotations

from typing import Any, List, Optional

from sqlalchemy import select

from app.tefca_registry.rce import models as m

#: The only run_status a current run may hold. From `models.RUN_STATUS`.
COMPLETED_STATUS = "COMPLETE"


def _ordered_runs(intake_id: Any):
    """Completed runs for one intake, newest first, in a total order."""
    return (select(m.RceIngestionRun.id)
            .where(m.RceIngestionRun.source_intake_id == intake_id,
                   m.RceIngestionRun.run_status == COMPLETED_STATUS,
                   m.RceIngestionRun.completed_at.isnot(None))
            .order_by(m.RceIngestionRun.completed_at.desc(),
                      m.RceIngestionRun.started_at.desc(),
                      m.RceIngestionRun.id.desc()))


def current_run_id_subquery(intake_id: Any):
    """Scalar subquery yielding the current run's id, or NULL if none.

    A subquery rather than a fetched value so a caller needs no extra round
    trip and the predicate composes into GROUP BY and aggregate queries.
    """
    return _ordered_runs(intake_id).limit(1).scalar_subquery()


def current_issues_filter(intake_id: Any) -> Any:
    """Predicate selecting only the CURRENT run's issues for one delivery.

    Use this on every operational population query. Writing
    `RceIssue.source_intake_id == intake_id` on its own at the call site is how
    two runs get mixed, which is the whole reason this module exists.
    """
    return ((m.RceIssue.source_intake_id == intake_id)
            & (m.RceIssue.run_id == current_run_id_subquery(intake_id)))


def issues_of_run_filter(run_id: Any) -> Any:
    """Predicate selecting one SPECIFIC run's issues — history, or a re-read."""
    return m.RceIssue.run_id == run_id


def issues_filter(intake_id: Any, *, run_id: Any = None,
                  all_runs: bool = False) -> Any:
    """The one place a reader chooses between current, a named run, and history.

    `all_runs=True` is deliberately explicit. Reading every run is a legitimate
    audit question — "what has this delivery ever been assessed as?" — and it
    must stay reachable; it just must never be what a caller gets by accident.
    """
    if all_runs:
        return m.RceIssue.source_intake_id == intake_id
    if run_id is not None:
        return ((m.RceIssue.source_intake_id == intake_id)
                & issues_of_run_filter(run_id))
    return current_issues_filter(intake_id)


def orphaned_issue_filter(intake_id: Any) -> Any:
    """Issues that no run claims. Should always be empty; never silently ignored.

    An issue with a NULL `run_id` is invisible to `current_issues_filter`, so
    without a way to ask for it, a write-path regression would look like issues
    quietly disappearing from every operational view.
    """
    return ((m.RceIssue.source_intake_id == intake_id)
            & (m.RceIssue.run_id.is_(None)))


async def current_run(db, intake_id: Any) -> Optional[m.RceIngestionRun]:
    """The current run itself, for callers that must name it in a payload."""
    run_id = (await db.execute(_ordered_runs(intake_id).limit(1))).scalar()
    if run_id is None:
        return None
    return await db.get(m.RceIngestionRun, run_id)


async def run_history(db, intake_id: Any) -> List[Any]:
    """Every completed run for the delivery, newest first. Nothing is deleted."""
    return list((await db.execute(_ordered_runs(intake_id))).scalars().all())
