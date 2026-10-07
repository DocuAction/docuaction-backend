"""Cross-delivery issue history for one entity OID: the thin database layer.

All classification lives in `issue_history_core` (pure). This module only
(1) resolves the caller's allowed feeds, (2) reads the rows inside them with a
fixed number of indexed queries, (3) builds the plain-dict facts the core
classifies, and (4) serialises through an ALLOWLIST per audience.

ACCESS IS ENFORCED HERE, NOT IN THE FRONTEND (fail closed)
    The first thing a read does is resolve the allowed intake set from
    `rce_source_intakes.source_metadata->>'feed'`. Every later query joins only
    inside that set. An intake with no feed tag is in no set. No allowed feed,
    an OID found only in a feed the caller may not read, and an OID nobody
    delivered all raise the SAME `HistoryNotFound`; the response never carries
    a count or placeholder for hidden deliveries.

QUERY SHAPE (criterion 7: at most 4 queries per delivery, all index-served)
    intakes in allowed feeds            1 query
    records with source_rce_id = OID    1 query   (ix source_rce_id)
    runs of the visible intakes         1 query   (ix source_intake_id)
    rule history of the selected runs   1 query   (ix run_id)
    result maps by (run, record)        1 query   (primary key)
    issues of the OID's records         1 query   (ix source_record_id)
    delivery jobs of the deliveries     1 query   (ix source_intake_id)
    -> 7 queries per request in total, independent of the number of deliveries.

IDENTITY IS EXACT. `source_rce_id = :oid` is string equality: no trimming and no
case folding, matching `delivery_delta`. A trailing space is a different OID.
NULL never equals anything, so a record without an id never joins.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select, tuple_

from app.tefca_registry.rce import issue_history_core as core
from app.tefca_registry.rce import models as m

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 12
HARD_CAP = 60
AUDIT_ACTION = "issue_history_read"


class HistoryNotFound(Exception):
    """Unknown OID, hidden OID and 'no allowed feed' are indistinguishable.

    `visible_deliveries` is for the audit row only and never reaches a response.
    """

    def __init__(self, visible_deliveries: int = 0):
        super().__init__("NOT_FOUND")
        self.visible_deliveries = visible_deliveries


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ── database reads ───────────────────────────────────────────────────────────

def intakes_query(feeds: Sequence[str]):
    feed = m.RceSourceIntake.source_metadata["feed"].astext
    return (select(m.RceSourceIntake.id, m.RceSourceIntake.sha256,
                   m.RceSourceIntake.received_at, m.RceSourceIntake.received_by,
                   m.RceSourceIntake.status, m.RceSourceIntake.delivery_label,
                   m.RceSourceIntake.headers, feed.label("feed"))
            .where(feed.in_(list(feeds))))


def records_query(oid: str, intake_ids: Sequence[Any]):
    return (select(m.RceSourceRecord.id, m.RceSourceRecord.source_intake_id,
                   m.RceSourceRecord.parsed)
            .where(m.RceSourceRecord.source_rce_id == oid,
                   m.RceSourceRecord.source_intake_id.in_(list(intake_ids))))


def results_query(pairs: Sequence[Any]):
    return (select(m.RceRecordCheckResult.run_id,
                   m.RceRecordCheckResult.source_record_id,
                   m.RceRecordCheckResult.map_version,
                   m.RceRecordCheckResult.rule_count,
                   m.RceRecordCheckResult.outcomes)
            .where(tuple_(m.RceRecordCheckResult.run_id,
                          m.RceRecordCheckResult.source_record_id).in_(list(pairs))))


def issues_query(record_ids: Sequence[Any], with_values: bool):
    cols = [m.RceIssue.id, m.RceIssue.source_record_id, m.RceIssue.run_id,
            m.RceIssue.rule_id, m.RceIssue.rule_version, m.RceIssue.issue_type,
            m.RceIssue.severity, m.RceIssue.field_name,
            m.RceIssue.correction_authority, m.RceIssue.resolution,
            m.RceIssue.resolved_by, m.RceIssue.resolved_at,
            m.RceIssue.qa_approved_by, m.RceIssue.qa_approved_at,
            m.RceIssue.created_at]
    if with_values:
        cols += [m.RceIssue.original_value, m.RceIssue.suggested_value,
                 m.RceIssue.resolution_notes]
    return (select(*cols).where(m.RceIssue.source_record_id.in_(list(record_ids)))
            .order_by(m.RceIssue.created_at, m.RceIssue.id))


async def _rows(db, statement) -> List[Dict[str, Any]]:
    return [dict(r._mapping) for r in (await db.execute(statement)).all()]


# ── the read ─────────────────────────────────────────────────────────────────

async def get_issue_history(db, oid: str, *, reviewer_or_above: bool, settings,
                            limit: int = DEFAULT_LIMIT,
                            before: Optional[Any] = None) -> Dict[str, Any]:
    """The history response for one OID, or `HistoryNotFound`."""
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
    from app.tefca_registry.rce.quality_rules import NON_QUALITY_ISSUE_TYPES

    limit = max(1, min(int(limit), HARD_CAP))
    feeds = core.allowed_feeds(
        reviewer_or_above=reviewer_or_above,
        viewer_setting=getattr(settings, "ISSUE_HISTORY_FEEDS_VIEWER", ""),
        reviewer_setting=getattr(settings, "ISSUE_HISTORY_FEEDS_REVIEWER", ""))
    if not feeds:
        raise HistoryNotFound(0)

    intakes = await _rows(db, intakes_query(sorted(feeds)))
    if not intakes:
        raise HistoryNotFound(0)
    all_ids = [i["id"] for i in intakes]

    records = await _rows(db, records_query(oid, all_ids))
    if not records:
        raise HistoryNotFound(0)

    deliveries = core.collapse_canonical(intakes)
    canonical_ids = [d["canonical"]["id"] for d in deliveries]
    records_by_intake: Dict[Any, List[Dict[str, Any]]] = {}
    for rec in records:
        records_by_intake.setdefault(rec["source_intake_id"], []).append(rec)

    runs = await _rows(db, select(
        m.RceIngestionRun.id, m.RceIngestionRun.source_intake_id,
        m.RceIngestionRun.run_status.label("status"),
        m.RceIngestionRun.started_at, m.RceIngestionRun.completed_at,
        m.RceIngestionRun.rule_set_version, m.RceIngestionRun.error)
        .where(m.RceIngestionRun.source_intake_id.in_(all_ids)))
    runs_by_intake: Dict[Any, List[Dict[str, Any]]] = {}
    for run in runs:
        runs_by_intake.setdefault(run["source_intake_id"], []).append(run)

    # Only a canonical intake's results are ever read.
    selection = {d["canonical"]["id"]: core.select_runs(
        runs_by_intake.get(d["canonical"]["id"], [])) for d in deliveries}
    selected_run_ids = [s["selected"]["id"] for s in selection.values() if s["selected"]]

    history_by_run: Dict[Any, List[Dict[str, Any]]] = {}
    if selected_run_ids:
        for row in await _rows(db, select(
                m.RceRuleExecutionHistory.run_id, m.RceRuleExecutionHistory.rule_id,
                m.RceRuleExecutionHistory.rule_version,
                m.RceRuleExecutionHistory.execution_status,
                m.RceRuleExecutionHistory.requires_hash,
                m.RceRuleExecutionHistory.scope,
                m.RceRuleExecutionHistory.coverage)
                .where(m.RceRuleExecutionHistory.run_id.in_(selected_run_ids))):
            history_by_run.setdefault(row["run_id"], []).append(row)

    # (run, record) pairs and the records whose issues are needed.
    pairs, record_ids = [], []
    for d in deliveries:
        iid = d["canonical"]["id"]
        recs = records_by_intake.get(iid, [])
        record_ids.extend(r["id"] for r in recs)
        run = selection[iid]["selected"]
        if run and len(recs) == 1:
            pairs.append((run["id"], recs[0]["id"]))
    results: Dict[Any, Dict[str, Any]] = {}
    if pairs:
        for row in await _rows(db, results_query(pairs)):
            results[(row["run_id"], row["source_record_id"])] = row
    issues_by_record: Dict[Any, List[Dict[str, Any]]] = {}
    if record_ids:
        for row in await _rows(db, issues_query(record_ids, reviewer_or_above)):
            issues_by_record.setdefault(row["source_record_id"], []).append(row)
    jobs_by_intake: Dict[Any, List[Dict[str, Any]]] = {}
    for row in await _rows(db, select(RceDeliveryJob.id, RceDeliveryJob.source_intake_id,
                                      RceDeliveryJob.delivery_label)
                           .where(RceDeliveryJob.source_intake_id.in_(canonical_ids))):
        jobs_by_intake.setdefault(row["source_intake_id"], []).append(row)

    # ── facts per canonical delivery, grouped per feed ──────────────────────
    per_feed: Dict[str, List[Dict[str, Any]]] = {}
    for d in deliveries:
        c = d["canonical"]
        iid = c["id"]
        recs = records_by_intake.get(iid, [])
        sel = selection[iid]
        run = sel["selected"]
        state = "ABSENT" if not recs else ("DUPLICATE" if len(recs) > 1 else "PRESENT")
        rec = recs[0] if state == "PRESENT" else None
        run_issues, later = [], []
        if state == "PRESENT":
            for issue in issues_by_record.get(rec["id"], []):
                is_later = (issue["issue_type"] in NON_QUALITY_ISSUE_TYPES
                            or issue["rule_id"] in core.LATER_STAGE_RULE_IDS)
                if is_later:
                    later.append(issue)
                elif run is not None and issue["run_id"] == run["id"]:
                    run_issues.append(issue)
        facts = {
            "delivery_id": str(iid), "feed": c["feed"], "intake": c,
            "intake_status": c["status"], "headers": c["headers"] or [],
            "record_state": state,
            "record_parsed": (rec["parsed"] if rec else None),
            "record_id": (rec["id"] if rec else None),
            "any_record": bool(recs),
            "selected_run": run, "selection": sel,
            "history_rows": history_by_run.get(run["id"], []) if run else [],
            "result": (results.get((run["id"], rec["id"]))
                       if run and rec else None),
            "issues": run_issues, "later_issues": later,
            "duplicates": d["duplicates"],
            "jobs": jobs_by_intake.get(iid, []),
        }
        per_feed.setdefault(c["feed"], []).append(facts)

    # Only deliveries from the first one that carries the OID onward.
    cut: Dict[str, List[Dict[str, Any]]] = {}
    for feed, items in per_feed.items():
        first = next((i for i, f in enumerate(items) if f["any_record"]), None)
        if first is not None:
            cut[feed] = items[first:]
    if not cut:
        raise HistoryNotFound(0)

    entries: List[Dict[str, Any]] = []
    all_cells: List[Dict[str, Any]] = []
    for feed, items in cut.items():
        lane_cells = {rule: core.build_lane_cells(items, rule, field)
                      for rule, field in core.SLICE_LANES}
        for idx, facts in enumerate(items):
            cells = [lane_cells[rule][idx] for rule, _ in core.SLICE_LANES]
            cells += core.later_stage_cells(facts)
            all_cells.extend(cells)
            entries.append(_delivery_entry(facts, cells, reviewer_or_above))

    entries.sort(key=lambda e: (e["_sort"], e["delivery_id"]))
    for e in entries:
        e.pop("_sort")

    # ── paging: newest `limit` deliveries before `before` ───────────────────
    ids = [e["delivery_id"] for e in entries]
    end = len(entries)
    if before is not None:
        if str(before) not in ids:
            raise HistoryNotFound(0)
        end = ids.index(str(before))
    start = max(0, end - limit)
    page = entries[start:end]
    page_ids = {e["delivery_id"] for e in page}
    gaps = [g for g in core.gaps_from_lanes(all_cells)
            if g["to_delivery_id"] in page_ids]

    feed_names = sorted(cut)
    response = {
        "oid": oid,
        "scope_note": "History for the " + (
            f"{feed_names[0]} feed" if len(feed_names) == 1
            else "feeds " + ", ".join(feed_names)),
        "deliveries": page,
        "gaps": gaps,
        "paging": {"limit": limit, "earlier_available": start > 0,
                   "next_before": (page[0]["delivery_id"] if page and start > 0
                                   else None)},
    }
    if not reviewer_or_above:
        leaked = core.forbidden_keys_present(response)
        if leaked:  # defence in depth: fail closed rather than serve a value
            logger.error("issue history viewer response contained %s", leaked)
            raise RuntimeError("viewer response failed the redaction guard")
    return response


# ── serialisation (allowlist per audience) ───────────────────────────────────

def _run_view(run: Dict[str, Any]) -> Dict[str, Any]:
    return {"run_id": str(run["id"]), "status": run["status"],
            "rule_set_version": run["rule_set_version"],
            "started_at": _iso(run["started_at"]),
            "completed_at": _iso(run["completed_at"])}


def _delivery_entry(facts: Dict[str, Any], cells: List[Dict[str, Any]],
                    reviewer: bool) -> Dict[str, Any]:
    c = facts["intake"]
    sel = facts["selection"]
    newer = []
    for run in sel["newer_runs"]:
        view = _run_view(run)
        view["error"] = core.sanitize_error(run.get("error"))
        newer.append(view)
    flags = ["NEWER_RUN_NOT_COMPLETE"] if sel["newer_runs"] else []
    status, reason = "OK", None
    if c["status"] == "FAILED" or sel["selected"] is None:
        status, reason = core.NOT_COMPARABLE, core.R_NO_RUN
    elif facts["record_state"] == "ABSENT":
        status, reason = core.NOT_COMPARABLE, core.R_ABSENT
    elif facts["record_state"] == "DUPLICATE":
        status, reason = core.NOT_COMPARABLE, core.R_DUP_OID
    dups = []
    for dup in facts["duplicates"]:
        item = {"intake_id": str(dup["id"]), "received_at": _iso(dup["received_at"]),
                "note": core.DUPLICATE_NOTE, "results_used": False}
        if reviewer:
            item["received_by"] = dup.get("received_by")
        dups.append(item)
    return {
        "_sort": c["received_at"],
        "delivery_id": facts["delivery_id"],
        "feed": facts["feed"],
        "received_at": _iso(c["received_at"]),
        "as_of": None, "as_of_note": "not recorded",
        "label": c.get("delivery_label"),
        "job_ids": [str(j["id"]) for j in facts["jobs"]],
        "duplicate_uploads": dups,
        "record_present": (None if facts["intake_status"] == "FAILED"
                           else facts["record_state"] != "ABSENT"),
        "status": status, "status_reason": reason, "flags": flags,
        "notice": (core.NEWER_RUN_TEXT if flags else None),
        "runs": {"selected": (_run_view(sel["selected"]) if sel["selected"] else None),
                 "newer_runs": newer,
                 "earlier_completed": {
                     "count": len(sel["earlier_completed"]),
                     "run_ids": [str(r["id"]) for r in sel["earlier_completed"]]}},
        "lanes": [_cell_view(cell, reviewer) for cell in cells],
    }


def _cell_view(cell: Dict[str, Any], reviewer: bool) -> Dict[str, Any]:
    issue = cell["issue"]
    version = cell["rule_version"]
    view: Dict[str, Any] = {
        "rule_id": cell["rule_id"], "field": cell["field"],
        "rule_version": version,
        "label": (f"as recorded in delivery {cell['delivery_id']} under rule "
                  f"{cell['rule_id']} v{version}" if version else
                  f"as recorded in delivery {cell['delivery_id']} under rule "
                  f"{cell['rule_id']}"),
        "check": dict(cell["check"]),
        "recurrence": (dict(cell["recurrence"]) if cell["recurrence"] else None),
        "note": cell["note"],
        "finding": None, "qa": None,
    }
    if issue is not None:
        view["finding"] = {"issue_id": str(issue["id"]),
                           "finding_type": issue["issue_type"],
                           "severity": issue["severity"],
                           "issue_count": cell["issue_count"]}
        qa = cell["qa"]
        view["qa"] = {"status": qa["status"], "decision": qa["decision"],
                      "role": qa["role"], "timestamp": _iso(qa["timestamp"]),
                      "qa_required": qa["qa_required"]}
        if reviewer:
            view["finding"]["original_value"] = issue.get("original_value")
            view["finding"]["suggested_value"] = issue.get("suggested_value")
            view["qa"]["rationale"] = issue.get("resolution_notes")
            view["qa"]["actors"] = {"resolved_by": issue.get("resolved_by"),
                                    "qa_approved_by": issue.get("qa_approved_by")}
    return view
