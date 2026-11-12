"""Import and readback service for record comments (rce_record_comments).

SCOPE: non-decision commentary only. This module writes ONLY to
`rce_record_comments`. It never touches review records, issues, curated status,
holds, buckets, supplemental NPIs or QA gates, and it is not reachable from any
determination / resolution / approval path. A comment is information; it closes
nothing.

IDENTITY (strict, no name matching)
    An entry names the immutable `rce_source_records.id` (AGT_Record_ID) AND the
    delivery (intake) it must belong to. It must also carry the three
    cross-check facts - source_line, hcid, tefca_id - and every one must equal the
    stored source record's own. A missing cross-check, a record of another
    delivery, or any mismatch REJECTS the entry with the reason. Organisation
    names are never used to find or confirm a record, and no replacement id is
    ever generated.

STATUSES (each entry gets exactly one; they reconcile to the entries sent)
    inserted         a new comment row was written
    amended          a changed comment was written as a NEW version (explicit `amend`)
    already_present  the same verbatim comment (record + kind + text) already exists
    needs_review     a DIFFERENT comment of this kind already exists for the record and
                     `amend` was not asked for - nothing is written
    rejected         invalid entry; the reason says why

BOUNDED AND RESUMABLE
    At most MAX_BATCH entries per call. Re-sending a batch (or its tail) is safe:
    duplicates are `already_present`; the unique key also makes a concurrent
    double-send safe. `dry_run=True` classifies without writing anything.

Until the 20261112_rce_record_comments migration is applied through the governed
release path, every call refuses with COMMENT_STORE_NOT_APPLIED.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, exists, select, text
from sqlalchemy.exc import IntegrityError

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.record_comment_models import (COMMENT_KINDS, KIND_QA_IZAAN,
                                                          RceRecordComment)

SERVICE_VERSION = "1.0.0"
MAX_BATCH = 500
MAX_PAGE = 500

INSERTED, AMENDED, ALREADY_PRESENT = "inserted", "amended", "already_present"
NEEDS_REVIEW, REJECTED = "needs_review", "rejected"
STATUSES = (INSERTED, AMENDED, ALREADY_PRESENT, NEEDS_REVIEW, REJECTED)

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
#: The facts that must accompany every entry and equal the source record's own.
REQUIRED_CROSSCHECKS = ("source_line", "hcid", "tefca_id")


class CommentStoreNotApplied(RuntimeError):
    """The migration that creates rce_record_comments has not been applied."""
    code = "COMMENT_STORE_NOT_APPLIED"


class CommentRefused(ValueError):
    """The whole call was refused (bad batch/intake), with the reason."""


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def idempotency_key(source_record_id: Any, kind: str, text_hash: str,
                    supersedes_id: Any = None) -> str:
    material = "|".join([str(source_record_id), kind, text_hash,
                         str(supersedes_id) if supersedes_id else ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def _assert_store(db) -> None:
    exists_ = (await db.execute(text("SELECT to_regclass('public.rce_record_comments')"))).scalar()
    if exists_ is None:
        raise CommentStoreNotApplied(
            "COMMENT_STORE_NOT_APPLIED: rce_record_comments does not exist; the "
            "20261112_rce_record_comments migration has not been applied through the "
            "governed DEV release path. Nothing was written.")


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _norm_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _authored_at(value: Any):
    """None (unknown - stays unknown) or a timezone-aware datetime. A naive or
    unparseable value is a validation error, never a guess."""
    if value is None or value == "":
        return None, None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None, f"authored_at {value!r} is not an ISO datetime"
    if dt.tzinfo is None:
        return None, "authored_at has no timezone; leave it unknown rather than guess one"
    return dt, None


def _validate_entry(entry: Dict[str, Any], rec: Optional[Any], intake_uuid: uuid.UUID) -> Dict[str, Any]:
    """(normalized, problem). Pure over an already-loaded source record."""
    out: Dict[str, Any] = {}
    kind = entry.get("comment_kind")
    if kind not in COMMENT_KINDS:
        return {"problem": f"comment_kind must be one of {list(COMMENT_KINDS)}"}
    out["kind"] = kind
    body = entry.get("comment_text")
    if not isinstance(body, str) or body.strip() == "":
        return {"problem": "comment_text is empty (blank comments are not stored)"}
    if "\x00" in body:
        return {"problem": "comment_text contains a NUL character; refusing to alter it"}
    out["text"] = body                       # VERBATIM
    agt = _uuid(entry.get("source_record_id"))
    if agt is None:
        return {"problem": "source_record_id (AGT_Record_ID) is not a UUID"}
    out["record_id"] = agt
    entry_intake = entry.get("intake_id")
    if entry_intake is not None and _uuid(entry_intake) != intake_uuid:
        return {"problem": f"entry names intake {entry_intake} but the call is for {intake_uuid}"}
    if rec is None:
        return {"problem": f"{agt} is not a source record"}
    if rec.source_intake_id != intake_uuid:
        return {"problem": (f"source record {agt} belongs to delivery "
                            f"{rec.source_intake_id}, not {intake_uuid}; refusing to attach "
                            f"a comment to another delivery's record")}
    expected = entry.get("expected") or {}
    missing = [k for k in REQUIRED_CROSSCHECKS if k not in expected]
    if missing:
        return {"problem": ("identity cross-check incomplete: supply " +
                            ", ".join(REQUIRED_CROSSCHECKS) + f" (missing {missing})")}
    parsed = rec.parsed or {}
    actual = {"source_line": rec.line_number,
              "hcid": _norm_str(rec.hcid or parsed.get("HCID")),
              "tefca_id": _norm_str(rec.tefcaid or parsed.get("TEFCAID"))}
    try:
        exp_line = int(expected["source_line"])
    except (ValueError, TypeError):
        return {"problem": f"source_line {expected['source_line']!r} is not an integer"}
    checks = {"source_line": (exp_line, actual["source_line"]),
              "hcid": (_norm_str(expected["hcid"]), actual["hcid"]),
              "tefca_id": (_norm_str(expected["tefca_id"]), actual["tefca_id"])}
    bad = {k: {"entry": a, "stored": b} for k, (a, b) in checks.items() if a != b}
    if bad:
        return {"problem": f"identity cross-check failed for the stored record: {bad}"}
    out["identity_check"] = {"validated": True, **actual}
    src = entry.get("source") or {}
    name = _norm_str(src.get("file_name"))
    sha = _norm_str(src.get("file_sha256"))
    if not name or not _HEX64.match(sha):
        return {"problem": "source.file_name and a 64-hex source.file_sha256 are required"}
    out["src"] = {"file_name": name, "file_sha256": sha.lower(),
                  "sheet": src.get("sheet"), "column": src.get("column"),
                  "row": src.get("row")}
    if out["src"]["row"] is not None:
        try:
            out["src"]["row"] = int(out["src"]["row"])
        except (ValueError, TypeError):
            return {"problem": f"source.row {src.get('row')!r} is not an integer"}
    if kind == KIND_QA_IZAAN and not (entry.get("author_attribution")
                                      or entry.get("attribution_basis")):
        return {"problem": "QA_IZAAN comments require an author attribution or its basis"}
    out["author_attribution"] = entry.get("author_attribution")
    out["attribution_basis"] = entry.get("attribution_basis")
    authored, problem = _authored_at(entry.get("authored_at"))
    if problem:
        return {"problem": problem}
    out["authored_at"] = authored
    out["amend"] = bool(entry.get("amend"))
    return out


async def import_comments(db, *, intake_id: Any, entries: List[Dict[str, Any]], actor: str,
                          batch_id: Any = None, dry_run: bool = False) -> Dict[str, Any]:
    """Validate and (unless dry_run) append a bounded batch of comments.

    Writes nothing outside rce_record_comments. The caller commits."""
    await _assert_store(db)
    intake_uuid = _uuid(intake_id)
    if intake_uuid is None:
        raise CommentRefused(f"intake_id {intake_id!r} is not a UUID")
    if await db.get(m.RceSourceIntake, intake_uuid) is None:
        raise CommentRefused(f"No delivery (intake) {intake_uuid}")
    if not isinstance(entries, list) or not entries:
        raise CommentRefused("entries must be a non-empty list")
    if len(entries) > MAX_BATCH:
        raise CommentRefused(
            f"{len(entries)} entries exceeds the bounded batch size of {MAX_BATCH}; send "
            f"the file in batches (re-sending a batch is safe)")
    if not _norm_str(actor):
        raise CommentRefused("the importing actor is required (it is stored verbatim)")
    batch_uuid = _uuid(batch_id) if batch_id else uuid.uuid4()
    if batch_uuid is None:
        raise CommentRefused(f"batch_id {batch_id!r} is not a UUID")

    ids = {u for u in (_uuid(e.get("source_record_id")) for e in entries if isinstance(e, dict)) if u}
    recs = {r.id: r for r in (await db.execute(
        select(m.RceSourceRecord).where(m.RceSourceRecord.id.in_(sorted(ids))))).scalars().all()} \
        if ids else {}
    existing = (await db.execute(
        select(RceRecordComment.id, RceRecordComment.source_record_id,
               RceRecordComment.comment_kind, RceRecordComment.text_sha256,
               RceRecordComment.supersedes_comment_id)
        .where(RceRecordComment.source_record_id.in_(sorted(ids))))).all() if ids else []
    # current = not named as the predecessor of any other row (the chain is linear)
    superseded = {sup for *_rest, sup in existing if sup is not None}
    by_text: Dict[tuple, Any] = {}
    current: Dict[tuple, Any] = {}
    for cid, rid, kind, thash, _sup in existing:
        by_text[(rid, kind, thash)] = cid
        if cid not in superseded:
            current[(rid, kind)] = (cid, thash)

    results: List[Dict[str, Any]] = []
    counts = {s: 0 for s in STATUSES}

    def done(i, status, reason=None, comment_id=None):
        counts[status] += 1
        results.append({"index": i, "status": status, "reason": reason,
                        "comment_id": str(comment_id) if comment_id else None})

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            done(i, REJECTED, "entry is not an object")
            continue
        rec = recs.get(_uuid(entry.get("source_record_id")))
        v = _validate_entry(entry, rec, intake_uuid)
        if "problem" in v:
            done(i, REJECTED, v["problem"])
            continue
        rid, kind, thash = v["record_id"], v["kind"], text_sha256(v["text"])
        prior = by_text.get((rid, kind, thash))
        cur = current.get((rid, kind))
        # The same verbatim text is "already present" when it IS the current version,
        # when nothing is current, or when no amendment was asked for. Reverting to an
        # EARLIER text (A -> B -> A) is a legitimate amendment and falls through.
        if prior is not None and (cur is None or prior == cur[0] or not v["amend"]):
            done(i, ALREADY_PRESENT, "the same verbatim comment already exists", prior)
            continue
        supersedes = None
        if cur is not None:
            if not v["amend"]:
                done(i, NEEDS_REVIEW,
                     f"a different {kind} comment already exists for this record "
                     f"({cur[0]}); resend with amend=true to append this as a new "
                     f"version - nothing was changed", cur[0])
                continue
            supersedes = cur[0]
        new_id = uuid.uuid4()
        if dry_run:
            done(i, AMENDED if supersedes else INSERTED, "dry run: not written", None)
            by_text[(rid, kind, thash)] = new_id
            current[(rid, kind)] = (new_id, thash)
            continue
        row = RceRecordComment(
            id=new_id, source_intake_id=intake_uuid, source_record_id=rid,
            comment_kind=kind, comment_text=v["text"], text_sha256=thash,
            source_file_name=v["src"]["file_name"], source_file_sha256=v["src"]["file_sha256"],
            source_sheet=v["src"]["sheet"], source_column=v["src"]["column"],
            source_row=v["src"]["row"], author_attribution=v["author_attribution"],
            attribution_basis=v["attribution_basis"], authored_at=v["authored_at"],
            identity_check=v["identity_check"], imported_by=_norm_str(actor),
            import_batch_id=batch_uuid,
            idempotency_key=idempotency_key(rid, kind, thash, supersedes),
            supersedes_comment_id=supersedes)
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            done(i, ALREADY_PRESENT, "a concurrent import wrote the same comment", None)
            continue
        by_text[(rid, kind, thash)] = new_id
        current[(rid, kind)] = (new_id, thash)
        done(i, AMENDED if supersedes else INSERTED, None, new_id)

    return {"service_version": SERVICE_VERSION, "intake_id": str(intake_uuid),
            "batch_id": str(batch_uuid), "dry_run": dry_run, "entries": len(entries),
            "counts": counts, "reconciles": sum(counts.values()) == len(entries),
            "results": results,
            "note": ("Comments are non-decision commentary: nothing was closed, accepted, "
                     "reclassified, released or approved.")}


async def list_comments(db, *, intake_id: Any, source_record_id: Any = None,
                        kind: Optional[str] = None, current_only: bool = True,
                        limit: int = 200, after: Optional[str] = None) -> Dict[str, Any]:
    """Read comments of ONE delivery back, text verbatim, with full provenance.
    `after` is the `next` token of the previous page (keyset on imported_at, id)."""
    await _assert_store(db)
    intake_uuid = _uuid(intake_id)
    if intake_uuid is None:
        raise CommentRefused(f"intake_id {intake_id!r} is not a UUID")
    if kind is not None and kind not in COMMENT_KINDS:
        raise CommentRefused(f"kind must be one of {list(COMMENT_KINDS)}")
    limit = max(1, min(int(limit), MAX_PAGE))
    C = RceRecordComment
    stmt = select(C).where(C.source_intake_id == intake_uuid)
    if source_record_id is not None:
        rid = _uuid(source_record_id)
        if rid is None:
            raise CommentRefused(f"source_record_id {source_record_id!r} is not a UUID")
        stmt = stmt.where(C.source_record_id == rid)
    if kind:
        stmt = stmt.where(C.comment_kind == kind)
    if current_only:
        successor = C.__table__.alias("successor")
        stmt = stmt.where(~exists().where(successor.c.supersedes_comment_id == C.id))
    if after:
        try:
            ts, cid = after.rsplit("|", 1)
            marker = (datetime.fromisoformat(ts), uuid.UUID(cid))
        except (ValueError, TypeError):
            raise CommentRefused("after is not a valid page token")
        stmt = stmt.where((C.imported_at > marker[0]) |
                          and_(C.imported_at == marker[0], C.id > marker[1]))
    rows = (await db.execute(stmt.order_by(C.imported_at, C.id).limit(limit + 1))).scalars().all()
    page = rows[:limit]
    nxt = (f"{page[-1].imported_at.isoformat()}|{page[-1].id}"
           if len(rows) > limit and page else None)
    return {"intake_id": str(intake_uuid), "current_only": current_only, "count": len(page),
            "next": nxt,
            "comments": [{
                "id": str(c.id), "source_record_id": str(c.source_record_id),
                "comment_kind": c.comment_kind, "comment_text": c.comment_text,
                "text_sha256": c.text_sha256, "source_file_name": c.source_file_name,
                "source_file_sha256": c.source_file_sha256, "source_sheet": c.source_sheet,
                "source_column": c.source_column, "source_row": c.source_row,
                "author_attribution": c.author_attribution,
                "attribution_basis": c.attribution_basis,
                "authored_at": c.authored_at.isoformat() if c.authored_at else None,
                "identity_check": c.identity_check, "imported_by": c.imported_by,
                "imported_at": c.imported_at.isoformat() if c.imported_at else None,
                "import_batch_id": str(c.import_batch_id),
                "supersedes_comment_id": (str(c.supersedes_comment_id)
                                          if c.supersedes_comment_id else None),
                "non_decision": c.non_decision} for c in page]}
