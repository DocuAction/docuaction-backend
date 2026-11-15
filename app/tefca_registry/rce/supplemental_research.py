"""Supplemental NPI research: entry, read-back and audit history (migration 20261115_rce_npi_research).

WHAT THIS IS
    The persisted home for manual NPPES research on ONE delivered source record (e.g. a Treatment record
    delivered with a blank NPI). Each entry keeps SEPARATELY: the original submitted NPI exactly as delivered,
    the researched NPI, the NPPES reference, the actual research/check date, the identity-match rationale and
    the authenticated researcher. Amendments and withdrawals are NEW rows (append-only, linear chain).

WHAT IT IS NOT
    Not a correction, a resolution, an approval, a bucket change or a hold release. `non_decision` is pinned
    TRUE by a CHECK; nothing here is read by the classifier, the QA gate, the issue ledger or the hold logic.
    A researched NPI never replaces or fills the original submitted NPI and never removes a Treatment finding;
    an analyst determination and a different QA reviewer's action remain the only decision path.

LINKAGE IS EXPLICIT. `source_record_id` (the immutable rce_source_records.id, = AGT_Record_ID) and its intake
are required, and the entry must carry the record's source_line, hcid and tefca_id, which must equal the
record's own. There is deliberately NO entity-only fallback: an entity can span deliveries.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import aliased

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.supplemental_research_models import RceSupplementalNpiResearch as R

ACTION_RECORD, ACTION_AMEND, ACTION_WITHDRAW = "RECORD", "AMEND", "WITHDRAW"
ACTIONS = (ACTION_RECORD, ACTION_AMEND, ACTION_WITHDRAW)
MIN_RATIONALE, MIN_CHANGE_REASON = 20, 10
REQUIRED_CROSSCHECKS = ("source_line", "hcid", "tefca_id")


class ResearchStoreNotApplied(RuntimeError):
    code = "SUPPLEMENTAL_RESEARCH_NOT_APPLIED"


class ResearchRefused(ValueError):
    """The entry was refused, with the reason. Nothing was written."""


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _uuid(v: Any) -> Optional[uuid.UUID]:
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


async def _assert_store(db) -> None:
    if (await db.execute(text("SELECT to_regclass('public.rce_supplemental_npi_research')"))).scalar() is None:
        raise ResearchStoreNotApplied(
            "SUPPLEMENTAL_RESEARCH_NOT_APPLIED: rce_supplemental_npi_research does not exist; the "
            "20261115_rce_npi_research migration has not been applied through the governed DEV release "
            "path. Nothing was written.")


def delivered_npi(record: m.RceSourceRecord) -> str:
    """The submitted NPI exactly as delivered (blank stays blank): the parsed value when the delivered row
    has the column, else the record's npi field - the same rule the reporting loader uses."""
    parsed = record.parsed or {}
    value = parsed.get("NPI") if "NPI" in parsed else record.npi
    return "" if value is None else str(value)


def idempotency_key(source_record_id: Any, action: str, researched_npi: str, reference: str,
                    check_date: str, supersedes_id: Any) -> str:
    return hashlib.sha256("|".join([str(source_record_id), action, researched_npi, reference, check_date,
                                    str(supersedes_id or "")]).encode("utf-8")).hexdigest()


def _validate(entry: Dict[str, Any], *, today: date) -> Dict[str, Any]:
    from app.services.npi_validator import validate_npi

    action = _s(entry.get("action") or ACTION_RECORD).upper()
    if action not in ACTIONS:
        raise ResearchRefused(f"action must be one of {list(ACTIONS)}")
    out: Dict[str, Any] = {"action": action}
    supersedes = entry.get("supersedes_id")
    if (action == ACTION_RECORD) == bool(supersedes):
        raise ResearchRefused("a first entry (RECORD) names no earlier entry; an AMEND or WITHDRAW must name the "
                              "entry it supersedes (supersedes_id)")
    out["supersedes_id"] = _uuid(supersedes) if supersedes else None
    if supersedes and out["supersedes_id"] is None:
        raise ResearchRefused("supersedes_id is not a UUID")
    if action != ACTION_RECORD:
        reason = _s(entry.get("change_reason"))
        if len(reason) < MIN_CHANGE_REASON:
            raise ResearchRefused(f"change_reason is required (at least {MIN_CHANGE_REASON} characters) to amend or withdraw")
        out["change_reason"] = reason
    if action == ACTION_WITHDRAW:
        return out
    npi = _s(entry.get("researched_npi"))
    if len(npi) != 10 or not npi.isdigit():
        raise ResearchRefused("researched_npi must be exactly 10 digits (kept as text)")
    ok, why = validate_npi(npi)
    if not ok:
        raise ResearchRefused(f"researched_npi failed the NPI check digit: {why}")
    reference = _s(entry.get("nppes_reference"))
    if not reference or len(reference) > 500:
        raise ResearchRefused("nppes_reference is required (what was looked up and where), up to 500 characters")
    raw_date = entry.get("nppes_check_date")
    try:
        check = raw_date if isinstance(raw_date, date) and not isinstance(raw_date, datetime) else date.fromisoformat(_s(raw_date))
    except ValueError:
        raise ResearchRefused("nppes_check_date must be the actual research date, YYYY-MM-DD")
    if check > today:
        raise ResearchRefused("nppes_check_date is in the future; enter the date the research was actually done")
    rationale = _s(entry.get("match_rationale"))
    if len(rationale) < MIN_RATIONALE or len(rationale) > 4000:
        raise ResearchRefused(f"match_rationale is required: why this NPPES record is this organization "
                              f"(at least {MIN_RATIONALE}, at most 4000 characters)")
    out.update(researched_npi=npi, nppes_reference=reference, nppes_check_date=check, match_rationale=rationale)
    return out


async def record_entry(db, *, intake_id: Any, source_record_id: Any, expected: Dict[str, Any],
                       entry: Dict[str, Any], actor_email: str, actor_user_id: Any = None,
                       today: Optional[date] = None, commit: bool = True) -> Dict[str, Any]:
    """Write ONE research entry. Raises ResearchRefused / ResearchStoreNotApplied; writes nothing then."""
    await _assert_store(db)
    who = _s(actor_email)
    if not who:
        raise ResearchRefused("the authenticated researcher could not be determined; nothing was written")
    intake_uuid, record_uuid = _uuid(intake_id), _uuid(source_record_id)
    if intake_uuid is None or record_uuid is None:
        raise ResearchRefused("a delivery (intake) id and an explicit source_record_id (AGT_Record_ID) are both "
                              "required; research is never attached by entity or by NPI alone")
    record = await db.get(m.RceSourceRecord, record_uuid)
    if record is None or record.source_intake_id != intake_uuid:
        raise ResearchRefused("that source record does not exist in that delivery")
    for key in REQUIRED_CROSSCHECKS:
        if key not in (expected or {}):
            raise ResearchRefused(f"expected.{key} is required: it is checked against the record so the wrong "
                                  f"line cannot receive the research")
    if (str(record.line_number) != _s(expected["source_line"]) or _s(record.hcid) != _s(expected["hcid"])
            or _s(record.tefcaid) != _s(expected["tefca_id"])):
        raise ResearchRefused("source_line / hcid / tefca_id do not match the record: nothing was written")
    clean = _validate(entry, today=today or datetime.now(timezone.utc).date())
    key = idempotency_key(record.id, clean["action"], clean.get("researched_npi", ""),
                          clean.get("nppes_reference", ""), str(clean.get("nppes_check_date", "")),
                          clean["supersedes_id"])
    # An exact replay of a committed entry is answered BEFORE the chain-state refusals, so a retried RECORD/AMEND
    # (lost response, double click) is "already_present" rather than "research already exists".
    # Only while that entry is still the latest in its chain: replaying an entry that has since been amended or
    # withdrawn must not tell the client the research "stands", so it falls through to the chain-state refusals.
    duplicate = (await db.execute(select(R).where(R.idempotency_key == key))).scalars().first()
    if duplicate is not None and duplicate.source_record_id == record.id:
        # The key does not cover the free text, so an "exact replay" must also match it: a request that changed only the
        # rationale / change reason is a DIFFERENT payload and is refused, never answered with the old row.
        if (duplicate.match_rationale != clean.get("match_rationale")
                or duplicate.change_reason != clean.get("change_reason")):
            raise ResearchRefused("an entry with the same NPI, reference, date and chain already exists with a "
                                  "different rationale or change reason; nothing was written - amend the entry "
                                  "instead of repeating the request with different text")
        if (await db.execute(select(R.id).where(R.supersedes_id == duplicate.id))).first() is None:
            return {"status": "already_present", "entry": _row(duplicate)}
    if clean["supersedes_id"] is not None:
        prev = await db.get(R, clean["supersedes_id"])
        if prev is None or prev.source_record_id != record.id:
            raise ResearchRefused("supersedes_id is not an entry on this source record")
        successor = (await db.execute(select(R.id).where(R.supersedes_id == prev.id))).first()
        if successor is not None:
            raise ResearchRefused("that entry has already been superseded; amend the latest entry")
        if prev.entry_action == ACTION_WITHDRAW:
            raise ResearchRefused("a withdrawn entry cannot be amended; a governed correction is needed to "
                                  "enter research again for this record")
    else:
        existing_root = (await db.execute(select(R.id).where(R.source_record_id == record.id,
                                                             R.supersedes_id.is_(None)))).first()
        if existing_root is not None:
            raise ResearchRefused("research already exists for this record; amend or withdraw the latest entry")
    row = R(id=uuid.uuid4(), source_intake_id=intake_uuid, source_record_id=record.id,
            source_line=record.line_number, hcid=record.hcid, tefca_id=record.tefcaid,
            original_submitted_npi=delivered_npi(record), entry_action=clean["action"],
            researched_npi=clean.get("researched_npi"), nppes_reference=clean.get("nppes_reference"),
            nppes_check_date=clean.get("nppes_check_date"), match_rationale=clean.get("match_rationale"),
            change_reason=clean.get("change_reason"), researcher=who,
            researcher_user_id=_uuid(actor_user_id), supersedes_id=clean["supersedes_id"], idempotency_key=key)
    db.add(row)
    await db.flush()
    if commit:
        await db.commit()
    return {"status": "inserted", "entry": _row(row)}


def _row(r: R) -> Dict[str, Any]:
    return {
        "id": str(r.id), "source_intake_id": str(r.source_intake_id), "source_record_id": str(r.source_record_id),
        "agt_record_id": str(r.source_record_id), "source_line": r.source_line, "hcid": r.hcid, "tefca_id": r.tefca_id,
        "original_submitted_npi": r.original_submitted_npi, "entry_action": r.entry_action,
        "researched_npi": r.researched_npi, "nppes_reference": r.nppes_reference,
        "nppes_check_date": r.nppes_check_date.isoformat() if r.nppes_check_date else None,
        "match_rationale": r.match_rationale, "change_reason": r.change_reason, "researcher": r.researcher,
        "entered_at": r.entered_at.isoformat() if r.entered_at else None,
        "supersedes_id": str(r.supersedes_id) if r.supersedes_id else None, "non_decision": bool(r.non_decision)}


async def list_entries(db, *, intake_id: Any, source_record_id: Any = None, current_only: bool = True,
                       limit: int = 200) -> List[Dict[str, Any]]:
    """Read back, newest chain state first. `current_only` hides superseded rows and withdrawn chains; the full
    history (audit trail) is current_only=False."""
    await _assert_store(db)
    intake_uuid = _uuid(intake_id)
    if intake_uuid is None:
        raise ResearchRefused("intake_id is not a UUID")
    q = select(R).where(R.source_intake_id == intake_uuid)
    if source_record_id:
        rid = _uuid(source_record_id)
        if rid is None:
            raise ResearchRefused("source_record_id is not a UUID")
        q = q.where(R.source_record_id == rid)
    if current_only:
        # Filter in SQL BEFORE the limit: a truncated window must never show a superseded or withdrawn row as current.
        later = aliased(R)
        q = q.where(R.entry_action != ACTION_WITHDRAW,
                    ~exists().where(later.supersedes_id == R.id))
    rows = (await db.execute(q.order_by(R.source_record_id, R.entered_at, R.id).limit(max(1, min(limit, 500))))).scalars().all()
    return [_row(r) for r in rows]


async def current_by_record(db, intake_id: Any, record_ids: List[Any],
                            cutoff: Optional[datetime] = None) -> Dict[Any, Dict[str, Any]]:
    """The standing research per source record for the report: the latest non-superseded, non-withdrawn entry.
    Returns {} when the store is not applied (the report then shows no research and says nothing is stored)."""
    if not record_ids:
        return {}
    if (await db.execute(text("SELECT to_regclass('public.rce_supplemental_npi_research')"))).scalar() is None:
        return {}
    q = select(R).where(R.source_record_id.in_(record_ids))
    if cutoff is not None:
        # Nothing newer than the evidence cutoff: an amendment or withdrawal recorded after it does not exist yet
        # for a historical report, so it neither hides nor replaces the earlier research.
        q = q.where(R.entered_at <= (cutoff.replace(tzinfo=timezone.utc) if cutoff.tzinfo is None else cutoff))
    rows = (await db.execute(q)).scalars().all()
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    return {r.source_record_id: _row(r) for r in rows if r.id not in superseded and r.entry_action != ACTION_WITHDRAW}
