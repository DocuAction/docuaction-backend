"""Month-to-month record investigation: search, delivery history, and
full field-by-field comparison between two selected deliveries.

Extends the EXISTING TEFCA Entity Detail / review-case / issue-history
surface -- does not introduce a separate case-management system. Read-only
throughout: no row written anywhere.

ACCESS CONTROL IS REUSED, NOT REINVENTED. `issue_history.py` already
resolves "which deliveries may this caller read" via
`issue_history_core.allowed_feeds` + `narrow_feeds` + the
`rce_source_intakes.source_metadata->>'feed'` tag. This module calls those
same functions for the exact same reason that module's own docstring gives:
fail-closed, and consistent across every history-shaped feature rather than
each one inventing its own narrower-or-wider rule.

CONTINUITY KEY: HCID, NOT NPI, NOT ORGANISATION NAME.
Per the project's meeting-confirmed decision, HCID is the cross-month
linkage key -- but it is NOT assumed unique. Checked against the real
September/July files (2026-10-20): HCID repeats on >1 row within the SAME
delivery in both files (4 in July, 6 in September) -- a real, not
hypothetical, case. `hcid_delivery_history` therefore flags
`ambiguous_linkage=True` on any intake where the HCID maps to more than one
record, rather than silently picking one. TEFCAID uniqueness is
deliberately NOT enforced here (its extract-specific meaning is still an
open question, per the master instruction).

FIELD COMPARISON classifies every field present in EITHER delivery's
`parsed` JSONB payload (the delivered columns, not a fixed hardcoded list,
so a header that differs between months is still accounted for) into
exactly one of:
  NEWLY_OBSERVED        -- the field, or the whole record, has no earlier
                          counterpart. "First observed" is explicitly not
                          claimed as "created."
  ABSENT_FROM_LATER     -- present earlier, the later delivery carries no
                          record at all for this HCID. Explicitly not
                          claimed as "deleted."
  CHANGED               -- present in both, and the NORMALIZED values
                          differ (not just the raw bytes).
  UNCHANGED             -- present in both, raw values identical.
  UNCHANGED_FORMATTING_ONLY -- present in both, raw values differ but the
                          normalized (trimmed, case-folded, whitespace-
                          collapsed) values are identical. Kept separate
                          from CHANGED, per the explicit instruction to
                          never conflate the two.
A record-level `inactive_reactivated` flag is derived from the `active`
field's own CHANGED classification when that field is one of the compared
fields -- not a separate code path, so it can never disagree with the
field-level diff.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import issue_history_core as core
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.issue_history import intakes_query

OUTCOME_NEWLY_OBSERVED = "NEWLY_OBSERVED"
OUTCOME_ABSENT_FROM_LATER = "ABSENT_FROM_LATER"
OUTCOME_CHANGED = "CHANGED"
OUTCOME_UNCHANGED = "UNCHANGED"
OUTCOME_UNCHANGED_FORMATTING_ONLY = "UNCHANGED_FORMATTING_ONLY"


class RecordHistoryNotFound(Exception):
    """No allowed feed, or nothing visible under this HCID/id in any allowed
    feed. Deliberately indistinguishable from each other in the response,
    matching `issue_history.HistoryNotFound`'s own fail-closed convention."""


async def _rows(db, stmt) -> List[Dict[str, Any]]:
    result = await db.execute(stmt)
    return [dict(r._mapping) for r in result]


async def _allowed_intake_ids(db, *, reviewer_or_above: bool, settings,
                               allowed_modules) -> List[Any]:
    feeds = core.allowed_feeds(
        reviewer_or_above=reviewer_or_above,
        viewer_setting=getattr(settings, "ISSUE_HISTORY_FEEDS_VIEWER", ""),
        reviewer_setting=getattr(settings, "ISSUE_HISTORY_FEEDS_REVIEWER", ""))
    feeds = core.narrow_feeds(feeds, allowed_modules)
    if not feeds:
        return []
    legacy = core.parse_intake_feeds(getattr(settings, "ISSUE_HISTORY_INTAKE_FEEDS", ""))
    intakes = await _rows(db, intakes_query(sorted(feeds), legacy))
    usable = [i for i in intakes if i.get("status") != "FAILED"]
    usable.sort(key=lambda i: (i["received_at"], str(i["id"])))
    return usable


# ── 1. search ─────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    source_record_id: Any
    intake_id: Any
    delivery_label: Optional[str]
    received_at: Any
    line_number: int
    source_rce_id: Optional[str]
    npi: Optional[str]
    hcid: Optional[str]
    tefcaid: Optional[str]
    canonical_entity_id: Optional[Any]
    name: Optional[str]
    matched_on: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_record_id": str(self.source_record_id), "intake_id": str(self.intake_id),
            "delivery_label": self.delivery_label,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "line_number": self.line_number, "source_rce_id": self.source_rce_id,
            "npi": self.npi, "hcid": self.hcid, "tefcaid": self.tefcaid,
            "canonical_entity_id": str(self.canonical_entity_id) if self.canonical_entity_id else None,
            "name": self.name, "matched_on": self.matched_on,
        }


async def search_records(
    db, *, npi: Optional[str] = None, hcid: Optional[str] = None,
    tefcaid: Optional[str] = None, name: Optional[str] = None,
    qhin_entity_id: Optional[Any] = None, limit: int = 25,
    reviewer_or_above: bool, settings, allowed_modules=None,
) -> Dict[str, Any]:
    """Read-only search across every allowed delivery's own records.

    Returns one row PER MATCHING DELIVERED LINE, not collapsed to one row
    per canonical entity -- "show multiple results when an identifier maps
    to multiple directory entries" is the explicit requirement, and
    collapsing here would silently violate it. `qhin_entity_id` filters by
    the promoted entity's active `belongs_to` relationship to that QHIN
    entity (same join `queries.list_participants` already uses), since QHIN
    membership is a registry-level fact tracked in
    `TefcaEntityRelationship`, not a per-record field; a record with no
    promoted entity simply cannot be filtered by QHIN and is excluded when
    this filter is given, not silently matched.
    """
    if not any([npi, hcid, tefcaid, name]):
        raise ValueError("search_records requires at least one of npi/hcid/tefcaid/name")

    intakes = await _allowed_intake_ids(
        db, reviewer_or_above=reviewer_or_above, settings=settings,
        allowed_modules=allowed_modules)
    if not intakes:
        # Unlike `hcid_delivery_history` (about one specific, known subject,
        # where "nothing visible" must stay indistinguishable from "hidden"
        # per the fail-closed convention `issue_history.py` established),
        # a general search with no allowed feed at all is simply a search
        # that found nothing -- the normal, expected shape of "no results",
        # not an error condition to raise on.
        return {"results": [], "count": 0}
    intake_ids = [i["id"] for i in intakes]
    intake_by_id = {i["id"]: i for i in intakes}

    conds = [m.RceSourceRecord.source_intake_id.in_(intake_ids)]
    matched_on = []
    if npi:
        conds.append(m.RceSourceRecord.npi == npi.strip())
        matched_on.append("npi")
    if hcid:
        conds.append(m.RceSourceRecord.hcid == hcid.strip())
        matched_on.append("hcid")
    if tefcaid:
        conds.append(m.RceSourceRecord.tefcaid == tefcaid.strip())
        matched_on.append("tefcaid")
    if name:
        conds.append(m.RceSourceRecord.parsed["name"].astext.ilike(f"%{name.strip()}%"))
        matched_on.append("name")

    rows = (await db.execute(
        select(m.RceSourceRecord.id, m.RceSourceRecord.source_intake_id,
               m.RceSourceRecord.line_number, m.RceSourceRecord.source_rce_id,
               m.RceSourceRecord.npi, m.RceSourceRecord.hcid, m.RceSourceRecord.tefcaid,
               m.RceSourceRecord.canonical_entity_id,
               m.RceSourceRecord.parsed["name"].astext.label("name"))
        .where(*conds)
        .order_by(m.RceSourceRecord.source_intake_id.desc(), m.RceSourceRecord.line_number)
        .limit(limit))).all()

    if qhin_entity_id:
        entity_ids = [r.canonical_entity_id for r in rows if r.canonical_entity_id]
        belongs: set = set()
        if entity_ids:
            belongs = set((await db.execute(
                select(reg.TefcaEntityRelationship.child_entity_id)
                .where(reg.TefcaEntityRelationship.parent_entity_id == qhin_entity_id,
                       reg.TefcaEntityRelationship.relationship_type == "belongs_to",
                       reg.TefcaEntityRelationship.status == "active",
                       reg.TefcaEntityRelationship.child_entity_id.in_(entity_ids)))
            ).scalars().all())
        rows = [r for r in rows if r.canonical_entity_id in belongs]

    results = []
    for r in rows:
        intake = intake_by_id.get(r.source_intake_id)
        results.append(SearchResult(
            source_record_id=r.id, intake_id=r.source_intake_id,
            delivery_label=intake.get("delivery_label") if intake else None,
            received_at=intake.get("received_at") if intake else None,
            line_number=r.line_number, source_rce_id=r.source_rce_id,
            npi=r.npi, hcid=r.hcid, tefcaid=r.tefcaid,
            canonical_entity_id=r.canonical_entity_id, name=r.name,
            matched_on="+".join(matched_on)).as_dict())
    return {"results": results, "count": len(results)}


# ── 2. delivery history by HCID ──────────────────────────────────────────

async def hcid_delivery_history(
    db, *, hcid: str, reviewer_or_above: bool, settings, allowed_modules=None,
) -> Dict[str, Any]:
    """Every allowed delivery's record(s) for this HCID, ordered by receipt
    date. A delivery with no record for this HCID is simply absent from the
    list -- the caller renders "no delivery available" for any month it
    expected and did not get, never an interpolated value."""
    hcid = hcid.strip()
    intakes = await _allowed_intake_ids(
        db, reviewer_or_above=reviewer_or_above, settings=settings,
        allowed_modules=allowed_modules)
    if not intakes:
        raise RecordHistoryNotFound()
    intake_ids = [i["id"] for i in intakes]
    intake_by_id = {i["id"]: i for i in intakes}

    rows = (await db.execute(
        select(m.RceSourceRecord.id, m.RceSourceRecord.source_intake_id,
               m.RceSourceRecord.line_number, m.RceSourceRecord.source_rce_id,
               m.RceSourceRecord.npi, m.RceSourceRecord.tefcaid,
               m.RceSourceRecord.canonical_entity_id, m.RceSourceRecord.promotion_status)
        .where(m.RceSourceRecord.source_intake_id.in_(intake_ids),
               m.RceSourceRecord.hcid == hcid)
        .order_by(m.RceSourceRecord.source_intake_id))).all()
    if not rows:
        raise RecordHistoryNotFound()

    by_intake: Dict[Any, List[Any]] = {}
    for r in rows:
        by_intake.setdefault(r.source_intake_id, []).append(r)

    deliveries = []
    for intake_id in sorted(by_intake, key=lambda k: intake_by_id[k]["received_at"]):
        intake = intake_by_id[intake_id]
        recs = by_intake[intake_id]
        deliveries.append({
            "intake_id": str(intake_id),
            "delivery_label": intake.get("delivery_label"),
            "received_at": intake["received_at"].isoformat() if intake.get("received_at") else None,
            "ambiguous_linkage": len(recs) > 1,
            "records": [{
                "source_record_id": str(r.id), "line_number": r.line_number,
                "source_rce_id": r.source_rce_id, "npi": r.npi, "tefcaid": r.tefcaid,
                "canonical_entity_id": str(r.canonical_entity_id) if r.canonical_entity_id else None,
                "promotion_status": r.promotion_status,
            } for r in recs],
        })
    return {"hcid": hcid, "deliveries": deliveries}


# ── 3. field comparison between two selected deliveries ──────────────────

_WS_RE = re.compile(r"\s+")


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    s = str(value).strip().casefold()
    return _WS_RE.sub(" ", s)


@dataclass
class FieldDiff:
    field: str
    earlier_value: Any
    later_value: Any
    change_type: str

    def as_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "earlier_value": self.earlier_value,
                "later_value": self.later_value, "change_type": self.change_type}


async def compare_two_records(db, *, earlier_record_id, later_record_id) -> Dict[str, Any]:
    """Field-by-field diff between two SPECIFIC `rce_source_records` rows the
    caller already selected (typically from `hcid_delivery_history`'s own
    list) -- this function does not re-derive linkage; it trusts the two ids
    given to it. Raises ValueError if either id does not exist, rather than
    returning a partial/misleading diff."""
    earlier = await db.get(m.RceSourceRecord, earlier_record_id)
    later = await db.get(m.RceSourceRecord, later_record_id)
    if earlier is None or later is None:
        raise ValueError("both earlier_record_id and later_record_id must exist")

    earlier_intake = await db.get(m.RceSourceIntake, earlier.source_intake_id)
    later_intake = await db.get(m.RceSourceIntake, later.source_intake_id)

    e_fields: Dict[str, Any] = dict(earlier.parsed or {})
    l_fields: Dict[str, Any] = dict(later.parsed or {})
    all_keys = sorted(set(e_fields) | set(l_fields))

    diffs: List[Dict[str, Any]] = []
    for key in all_keys:
        in_e, in_l = key in e_fields, key in l_fields
        ev, lv = e_fields.get(key), l_fields.get(key)
        if not in_e and in_l:
            change = OUTCOME_NEWLY_OBSERVED
        elif in_e and not in_l:
            change = OUTCOME_ABSENT_FROM_LATER
        elif ev == lv:
            change = OUTCOME_UNCHANGED
        elif _normalize(ev) == _normalize(lv):
            change = OUTCOME_UNCHANGED_FORMATTING_ONLY
        else:
            change = OUTCOME_CHANGED
        diffs.append(FieldDiff(field=key, earlier_value=ev, later_value=lv,
                               change_type=change).as_dict())

    active_diff = next((d for d in diffs if d["field"] == "active"
                        and d["change_type"] == OUTCOME_CHANGED), None)

    return {
        "earlier": {
            "source_record_id": str(earlier.id), "intake_id": str(earlier.source_intake_id),
            "received_at": earlier_intake.received_at.isoformat() if earlier_intake else None,
            "delivery_label": earlier_intake.delivery_label if earlier_intake else None,
        },
        "later": {
            "source_record_id": str(later.id), "intake_id": str(later.source_intake_id),
            "received_at": later_intake.received_at.isoformat() if later_intake else None,
            "delivery_label": later_intake.delivery_label if later_intake else None,
        },
        "fields": diffs,
        "inactive_reactivated": {
            "earlier_value": active_diff["earlier_value"], "later_value": active_diff["later_value"],
        } if active_diff else None,
    }


# ── 4. full-population change report (July/August/September change CSV shape) ──

async def full_population_change_report(
    db, *, reviewer_or_above: bool, settings, allowed_modules=None,
) -> List[Dict[str, Any]]:
    """Every actual field change across EVERY HCID with two or more allowed
    deliveries, as a flat list of rows shaped for direct CSV export: record
    reference, delivery pair, affected field, old/new values, change type.

    Compares each CONSECUTIVE pair of deliveries per HCID (not just
    first-vs-last), so a three-delivery history yields two delivery-pair
    comparisons, not one that hides the middle transition. Rows where
    `change_type == UNCHANGED` are omitted -- this is a CHANGE report, not a
    full field dump; `UNCHANGED_FORMATTING_ONLY` IS included (it is still a
    disclosed difference, just not a substantive one).

    An HCID whose EITHER side of a pair is `ambiguous_linkage` contributes
    one row stating that explicitly (`field` = "(ambiguous linkage)") instead
    of silently comparing one arbitrarily-chosen record.

    O(N) delivery-list queries plus one `compare_two_records` call per
    consecutive pair -- bounded by the number of HCIDs with real history, not
    the full population size, since an HCID appearing in only one delivery
    has nothing to compare and is skipped before any comparison runs.
    """
    intakes = await _allowed_intake_ids(
        db, reviewer_or_above=reviewer_or_above, settings=settings,
        allowed_modules=allowed_modules)
    if not intakes:
        return []
    intake_ids = [i["id"] for i in intakes]

    hcid_rows = (await db.execute(
        select(m.RceSourceRecord.hcid, m.RceSourceRecord.source_intake_id,
               m.RceSourceRecord.id)
        .where(m.RceSourceRecord.source_intake_id.in_(intake_ids),
               m.RceSourceRecord.hcid.isnot(None)))).all()
    by_hcid: Dict[str, Dict[Any, List[Any]]] = {}
    for hcid, intake_id, record_id in hcid_rows:
        by_hcid.setdefault(hcid, {}).setdefault(intake_id, []).append(record_id)

    intake_by_id = {i["id"]: i for i in intakes}
    report: List[Dict[str, Any]] = []

    for hcid, per_intake in by_hcid.items():
        ordered_intake_ids = sorted(
            per_intake, key=lambda iid: intake_by_id[iid]["received_at"])
        if len(ordered_intake_ids) < 2:
            continue
        for earlier_iid, later_iid in zip(ordered_intake_ids, ordered_intake_ids[1:]):
            earlier_ambiguous = len(per_intake[earlier_iid]) > 1
            later_ambiguous = len(per_intake[later_iid]) > 1
            pair_label = (f"{intake_by_id[earlier_iid]['delivery_label']} -> "
                         f"{intake_by_id[later_iid]['delivery_label']}")
            if earlier_ambiguous or later_ambiguous:
                report.append({
                    "hcid": hcid, "delivery_pair": pair_label,
                    "field": "(ambiguous linkage)", "earlier_value": None, "later_value": None,
                    "change_type": "AMBIGUOUS_LINKAGE_NOT_COMPARED",
                })
                continue
            comparison = await compare_two_records(
                db, earlier_record_id=per_intake[earlier_iid][0],
                later_record_id=per_intake[later_iid][0])
            for f in comparison["fields"]:
                if f["change_type"] == OUTCOME_UNCHANGED:
                    continue
                report.append({
                    "hcid": hcid, "delivery_pair": pair_label,
                    "field": f["field"], "earlier_value": f["earlier_value"],
                    "later_value": f["later_value"], "change_type": f["change_type"],
                })
    return report
