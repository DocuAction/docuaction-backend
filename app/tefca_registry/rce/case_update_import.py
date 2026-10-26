"""Analyst / case-update spreadsheet import — stage, preview, and an ownership-only apply (2026-10-25).

THREE IMPORT PURPOSES, KEPT SEPARATE (never one importer)
    source_delivery   → the official ONC/RCE intake (`/api/tefca/rce/deliveries`, delimited text).
                        Not this module. This module refuses it and says where to go.
    reference_data    → the IQVIA importer (`/api/tefca/arc/iqvia/...`, licensed data, chunked).
                        Not this module. Refused likewise.
    case_updates      → THIS module: an analyst's spreadsheet of notes, proposed corrections,
                        ownership requests and requested workflow states for EXISTING cases.

WHAT THIS RELEASE DOES AND DOES NOT DO
    * Parses .csv / .psv / .txt / .tsv (delimited text) and .xlsx (openpyxl) with spreadsheet
      safety checks; identifiers are read as TEXT and leading-zero / precision loss is detected.
    * Resolves each row to exactly one existing case: by `review_id`, or by
      (`intake_id` + `source_rce_id`) through the curated record. Zero or several matches are
      NEVER guessed: zero → rejected, several → needs_review.
    * Classifies every row into exactly one disposition — accepted / unchanged / rejected /
      needs_review — and the four always sum to the rows read.
    * Records requested workflow values ("Released", "Approved", a B1–B4 bucket) as REQUESTED
      and routes the row to needs_review. Nothing in a spreadsheet can approve, release or
      reclassify: those remain the analyst-determination and QA routes, with their
      segregation-of-duties checks.
    * Computes an idempotency key over (file sha256, purpose, mapping version) so a retry of the
      same file is recognisable and cannot double-apply.
    * PREVIEW WRITES NOTHING. `build_preview` / `preview_rows` only stage and classify; the preview
      step itself commits no case data (the route does write its own audit row that a preview ran).
    * APPLY IS OWNERSHIP-ONLY. `apply_case_updates` (below) applies exactly ONE update type —
      `assign_to_email`, through the existing, already-audited `case_assignment.assign` service —
      serialised per idempotency key with a transaction-scoped advisory lock, so a retry or a
      concurrent duplicate submission cannot apply twice (see `TestApplyIdempotency`). Notes,
      proposed corrections and requested workflow values are STAGED, never applied: applying them
      needs the append-only follow-up event model, which is still an owner decision (doc 13 §2).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

PURPOSE_CASE_UPDATES = "case_updates"
PURPOSE_SOURCE_DELIVERY = "source_delivery"
PURPOSE_REFERENCE_DATA = "reference_data"
PURPOSES = (PURPOSE_CASE_UPDATES, PURPOSE_SOURCE_DELIVERY, PURPOSE_REFERENCE_DATA)

MAPPING_VERSION = "case-updates/1.0.0"

#: Where the other two purposes belong. Returned, never acted on, when a caller
#: picks the wrong purpose for this importer.
PURPOSE_REDIRECT = {
    PURPOSE_SOURCE_DELIVERY: {"surface": "ONC/RCE Deliveries", "route": "/tefca-arc/deliveries",
                              "api": "POST /api/tefca/rce/deliveries (official intake, delimited text only; .xlsx is not accepted there)"},
    PURPOSE_REFERENCE_DATA: {"surface": "IQVIA Reference Sources", "route": "/tefca-arc/iqvia",
                             "api": "IQVIA importer (licensed data; never routed through this importer)"},
}

#: Identity columns. A row needs `review_id` OR both delivery columns.
IDENTITY_REVIEW = "review_id"
IDENTITY_INTAKE = "intake_id"
IDENTITY_SOURCE = "source_rce_id"
#: Content columns this release understands. Anything else is preserved as "unmapped" and ignored.
CONTENT_COLUMNS = ("analyst_note", "proposed_correction_field", "proposed_correction_value",
                   "assign_to_email", "requested_state")
KNOWN_COLUMNS = (IDENTITY_REVIEW, IDENTITY_INTAKE, IDENTITY_SOURCE) + CONTENT_COLUMNS

#: Values a spreadsheet may REQUEST but can never apply.
WORKFLOW_VALUES = {"released", "release", "approved", "approve", "qa_approved", "b1", "b2", "b3", "b4",
                   "reportable", "confirm", "reclassify"}

DISP_ACCEPTED = "accepted"
DISP_UNCHANGED = "unchanged"
DISP_REJECTED = "rejected"
DISP_NEEDS_REVIEW = "needs_review"
DISPOSITIONS = (DISP_ACCEPTED, DISP_UNCHANGED, DISP_REJECTED, DISP_NEEDS_REVIEW)

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 50_000
MAX_WORKSHEETS = 50
_DELIMITED_SUFFIXES = (".csv", ".psv", ".txt", ".tsv")
_XLSX_SUFFIXES = (".xlsx",)
_REFUSED_SUFFIXES = {".xlsm": "macro-enabled workbook", ".xlsb": "binary workbook", ".xls": "legacy binary workbook",
                     ".xltm": "macro-enabled template", ".ods": "unsupported format"}
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_NPI_RE = re.compile(r"^\d{10}$")
_REVIEW_RE = re.compile(r"^REV-\d{4}-\d{6}$")


class SpreadsheetRefused(ValueError):
    """The file is not something this importer will read. Never degrade to partial rows."""


@dataclass
class LoadedTable:
    headers: List[str]
    rows: List[Dict[str, str]]
    worksheet: Optional[str]
    worksheets: List[str]
    kind: str
    sha256: str
    size_bytes: int
    warnings: List[Dict[str, Any]] = field(default_factory=list)


def _sniff_delimiter(sample: str) -> str:
    first = sample.splitlines()[0] if sample else ""
    counts = {d: first.count(d) for d in (",", "|", "\t", ";")}
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        raise SpreadsheetRefused("header row has a single field; no delimiter (comma, pipe, tab or semicolon) found")
    return best


def _normalise_header(h: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(h or "").strip().lower()).strip("_")


def load_tabular(filename: str, data: bytes, *, worksheet: Optional[str] = None) -> LoadedTable:
    """Read a delimited-text or .xlsx file into header + text rows, refusing unsafe inputs.

    Identifiers are kept as TEXT. For .xlsx, a numeric cell in an identifier column is flagged
    (`numeric_identifier_cell`) because Excel has already dropped leading zeros / may have lost
    precision; the value is still carried as the digits Excel kept, never re-padded by guessing.
    Formula cells are flagged, never evaluated (`data_only=False`).
    """
    name = (filename or "").strip()
    lower = name.lower()
    size = len(data or b"")
    if size == 0:
        raise SpreadsheetRefused("empty file")
    if size > MAX_BYTES:
        raise SpreadsheetRefused(f"file is {size:,} bytes; the limit is {MAX_BYTES:,}")
    for suffix, why in _REFUSED_SUFFIXES.items():
        if lower.endswith(suffix):
            raise SpreadsheetRefused(f"{suffix} refused: {why}")
    if data[:8] == _OLE_SIGNATURE:
        raise SpreadsheetRefused("OLE compound file (legacy .xls or encrypted workbook) refused")
    sha = hashlib.sha256(data).hexdigest()

    if lower.endswith(_XLSX_SUFFIXES) or data[:2] == b"PK" and not lower.endswith(_DELIMITED_SUFFIXES):
        return _load_xlsx(name, data, sha, worksheet)
    if lower.endswith(_DELIMITED_SUFFIXES):
        return _load_delimited(name, data, sha)
    raise SpreadsheetRefused("unsupported file type; accepted: .csv .psv .tsv .txt (delimited text) or .xlsx")


def _load_delimited(name: str, data: bytes, sha: str) -> LoadedTable:
    text = data.decode("utf-8-sig", errors="strict") if _is_utf8(data) else data.decode("cp1252")
    delimiter = _sniff_delimiter(text[:4096])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        raw_header = next(reader)
    except StopIteration:
        raise SpreadsheetRefused("no header row")
    headers = [_normalise_header(h) for h in raw_header]
    rows: List[Dict[str, str]] = []
    warnings: List[Dict[str, Any]] = []
    for line_no, rec in enumerate(reader, start=2):
        if not any((c or "").strip() for c in rec):
            continue
        if len(rec) != len(headers):
            warnings.append({"code": "FIELD_COUNT", "line": line_no, "fields": len(rec), "expected": len(headers)})
            rec = (rec + [""] * len(headers))[: len(headers)]
        rows.append({h: (v or "").strip() for h, v in zip(headers, rec)})
        if len(rows) > MAX_ROWS:
            raise SpreadsheetRefused(f"more than {MAX_ROWS:,} data rows")
    return LoadedTable(headers, rows, None, [], "delimited", sha, len(data), warnings)


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _load_xlsx(name: str, data: bytes, sha: str, worksheet: Optional[str]) -> LoadedTable:
    import zipfile
    try:
        import openpyxl  # noqa: WPS433 — optional heavy import kept local
    except ImportError as exc:  # pragma: no cover
        raise SpreadsheetRefused(f"openpyxl is not installed: {exc}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            total_uncompressed = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile:
        raise SpreadsheetRefused("not a valid .xlsx (zip) container")
    if total_uncompressed > 25 * MAX_BYTES:
        raise SpreadsheetRefused("workbook expands beyond the safety limit (possible zip bomb)")
    if any(n.startswith("xl/externalLinks/") for n in names):
        raise SpreadsheetRefused("workbook contains external links; refused")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise SpreadsheetRefused("workbook contains a VBA project (macros); refused")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception as exc:  # noqa: BLE001 — openpyxl raises many types for a corrupt/encrypted file
        raise SpreadsheetRefused(f"workbook could not be opened: {type(exc).__name__}")
    sheets = list(wb.sheetnames)
    if len(sheets) > MAX_WORKSHEETS:
        raise SpreadsheetRefused(f"workbook has {len(sheets)} worksheets; the limit is {MAX_WORKSHEETS}")
    if worksheet is None:
        worksheet = sheets[0]
    if worksheet not in sheets:
        raise SpreadsheetRefused(f"worksheet {worksheet!r} not found; available: {', '.join(sheets)}")
    ws = wb[worksheet]
    it = ws.iter_rows(values_only=False)
    try:
        header_cells = next(it)
    except StopIteration:
        raise SpreadsheetRefused(f"worksheet {worksheet!r} is empty")
    headers = [_normalise_header(c.value) for c in header_cells]
    id_like = {i for i, h in enumerate(headers) if h in (IDENTITY_REVIEW, IDENTITY_SOURCE, "npi", "hcid", "tefcaid", IDENTITY_INTAKE)}
    rows: List[Dict[str, str]] = []
    warnings: List[Dict[str, Any]] = []
    for r_idx, cells in enumerate(it, start=2):
        values: List[str] = []
        empty = True
        for c_idx, cell in enumerate(cells[: len(headers)]):
            v = cell.value
            if v is None:
                values.append("")
                continue
            empty = False
            if isinstance(v, str) and v.startswith("="):
                warnings.append({"code": "FORMULA_CELL", "row": r_idx, "column": headers[c_idx] if c_idx < len(headers) else c_idx,
                                 "note": "formula detected; not evaluated"})
                values.append("")
                continue
            if isinstance(v, (int, float)) and c_idx in id_like:
                warnings.append({"code": "NUMERIC_IDENTIFIER_CELL", "row": r_idx, "column": headers[c_idx],
                                 "note": "identifier stored as a number; leading zeros/precision may already be lost in the workbook"})
                v = str(int(v)) if float(v).is_integer() else str(v)
            values.append(str(v).strip())
        if empty:
            continue
        values = (values + [""] * len(headers))[: len(headers)]
        rows.append(dict(zip(headers, values)))
        if len(rows) > MAX_ROWS:
            raise SpreadsheetRefused(f"more than {MAX_ROWS:,} data rows")
    wb.close()
    return LoadedTable(headers, rows, worksheet, sheets, "xlsx", sha, len(data), warnings)


# ── preview ──────────────────────────────────────────────────────────────────

#: Resolver contract: given the identity columns of ONE row, return the list of
#: matching review ids (0, 1 or several). The route supplies a database-backed
#: implementation; tests supply a dict.
Resolver = Callable[[Dict[str, str]], Awaitable[List[str]]]


def idempotency_key(file_sha256: str, purpose: str, mapping_version: str = MAPPING_VERSION) -> str:
    return hashlib.sha256(f"{file_sha256}|{purpose}|{mapping_version}".encode("utf-8")).hexdigest()


def validate_header(headers: Sequence[str]) -> Dict[str, Any]:
    hs = set(headers)
    has_review = IDENTITY_REVIEW in hs
    has_delivery = IDENTITY_INTAKE in hs and IDENTITY_SOURCE in hs
    missing: List[str] = []
    if not (has_review or has_delivery):
        missing.append(f"{IDENTITY_REVIEW} (or both {IDENTITY_INTAKE} and {IDENTITY_SOURCE})")
    content = [c for c in CONTENT_COLUMNS if c in hs]
    if not content:
        missing.append("at least one of: " + ", ".join(CONTENT_COLUMNS))
    unmapped = [h for h in headers if h and h not in KNOWN_COLUMNS]
    return {"ok": not missing, "missing": missing, "mapped": [h for h in headers if h in KNOWN_COLUMNS],
            "unmapped_preserved_ignored": unmapped, "mapping_version": MAPPING_VERSION}


def _row_identity(row: Dict[str, str]) -> Tuple[str, Dict[str, str], Optional[str]]:
    """(mode, identity, problem). mode ∈ review_id | delivery | none."""
    rid = (row.get(IDENTITY_REVIEW) or "").strip()
    if rid:
        if not _REVIEW_RE.match(rid):
            return "review_id", {IDENTITY_REVIEW: rid}, f"review_id {rid!r} is not of the form REV-YYYY-NNNNNN"
        return "review_id", {IDENTITY_REVIEW: rid}, None
    intake = (row.get(IDENTITY_INTAKE) or "").strip()
    src = (row.get(IDENTITY_SOURCE) or "").strip()
    if intake and src:
        return "delivery", {IDENTITY_INTAKE: intake, IDENTITY_SOURCE: src}, None
    return "none", {}, "no identity: supply review_id, or both intake_id and source_rce_id"


async def preview_rows(rows: Sequence[Dict[str, str]], resolve: Resolver) -> Dict[str, Any]:
    """One disposition per row; the four dispositions always sum to the rows read."""
    out: List[Dict[str, Any]] = []
    counts = {d: 0 for d in DISPOSITIONS}
    seen_targets: Dict[str, int] = {}
    for n, row in enumerate(rows, start=1):
        mode, identity, problem = _row_identity(row)
        item: Dict[str, Any] = {"row": n, "identity_mode": mode, "identity": identity, "matched_review_ids": [],
                                "requested_workflow_values": {}, "changes": {}, "disposition": None, "reasons": []}
        if problem:
            item["disposition"] = DISP_REJECTED
            item["reasons"].append(problem)
            counts[DISP_REJECTED] += 1
            out.append(item)
            continue
        matches = list(await resolve(identity))
        item["matched_review_ids"] = matches
        changes = {c: row[c].strip() for c in CONTENT_COLUMNS if (row.get(c) or "").strip()}
        requested = {}
        for c in ("requested_state", "proposed_correction_value"):
            v = (changes.get(c) or "").strip()
            if v and v.lower().replace(" ", "_") in WORKFLOW_VALUES:
                requested[c] = v
        if "assign_to_email" in changes and "@" not in changes["assign_to_email"]:
            item["reasons"].append("assign_to_email is not an email address")
        item["changes"] = changes
        item["requested_workflow_values"] = requested
        if len(matches) == 0:
            item["disposition"] = DISP_REJECTED
            item["reasons"].append("no existing case matches this identity")
        elif len(matches) > 1:
            item["disposition"] = DISP_NEEDS_REVIEW
            item["reasons"].append(f"{len(matches)} cases match this identity; linkage must be corrected, nothing is applied")
        elif not changes:
            item["disposition"] = DISP_UNCHANGED
            item["reasons"].append("no content columns populated")
        elif requested:
            item["disposition"] = DISP_NEEDS_REVIEW
            item["reasons"].append("workflow value requested (release/approval/bucket) — recorded as REQUESTED; "
                                   "approval and reclassification happen only through the determination and QA routes")
        elif item["reasons"]:
            item["disposition"] = DISP_REJECTED
        else:
            target = matches[0]
            if target in seen_targets:
                item["disposition"] = DISP_NEEDS_REVIEW
                item["reasons"].append(f"duplicate update for {target} (first at row {seen_targets[target]}); a retry must not apply twice")
            else:
                seen_targets[target] = n
                item["disposition"] = DISP_ACCEPTED
        counts[item["disposition"]] += 1
        out.append(item)
    total = len(rows)
    assert sum(counts.values()) == total, "dispositions must reconcile to rows read"
    return {"rows_read": total, "counts": counts, "reconciles": sum(counts.values()) == total, "rows": out}


async def build_preview(filename: str, data: bytes, *, purpose: str, worksheet: Optional[str],
                        resolve: Resolver, uploaded_by: str) -> Dict[str, Any]:
    if purpose not in PURPOSES:
        raise SpreadsheetRefused(f"purpose must be one of {', '.join(PURPOSES)}")
    if purpose != PURPOSE_CASE_UPDATES:
        return {"purpose": purpose, "handled_here": False, "redirect": PURPOSE_REDIRECT[purpose],
                "note": "This importer handles analyst/case updates only. Source deliveries and reference data keep their own importers."}
    table = load_tabular(filename, data, worksheet=worksheet)
    header = validate_header(table.headers)
    result: Dict[str, Any] = {
        "purpose": purpose, "handled_here": True, "mapping_version": MAPPING_VERSION,
        "file": {"name": filename, "sha256": table.sha256, "size_bytes": table.size_bytes, "kind": table.kind,
                 "worksheet": table.worksheet, "worksheets": table.worksheets, "uploaded_by": uploaded_by},
        "idempotency_key": idempotency_key(table.sha256, purpose),
        "header": header, "warnings": table.warnings,
        "apply": {"available": False,
                  "reason": "Apply is not part of this release: imported notes/corrections/ownership require the "
                            "append-only follow-up event model (owner decision, doc 13 §2). Nothing was written."},
    }
    if not header["ok"]:
        result["preview"] = {"rows_read": len(table.rows), "counts": {d: 0 for d in DISPOSITIONS}, "rows": [], "reconciles": True,
                             "blocked": "header validation failed; no row was staged"}
        return result
    result["preview"] = await preview_rows(table.rows, resolve)
    result["apply"] = describe_apply(result["preview"])
    return result


# ── apply (2026-10-25): the ONE update type with an existing, supported, audited path ──
#
# WHAT IS APPLIED            `assign_to_email` on an ACCEPTED row -> `case_assignment.assign`
#                            (supervisor role, compare-and-set, audit row `review_case_assigned`
#                            / `review_case_reassigned`, APPROVED cases refused, held cases need an
#                            override reason). Nothing new was invented: the import is just another
#                            caller of the same service the QHIN Assignment screen uses.
# WHAT IS STAGED, NOT APPLIED  `analyst_note`, `proposed_correction_field/value`. They are preserved in
#                            the apply audit row (traceable, never lost) but written to NO case table,
#                            because the application has no append-only store for a free-text analyst
#                            note that is not a determination. THE EXACT UNRESOLVED BUSINESS CHOICE:
#                            (a) a new append-only `case_followup_events` table (migration) that
#                            holds notes/proposed corrections/next action/follow-up date, or
#                            (b) treating an imported note as the rationale of an analyst
#                            DETERMINATION in `review_decision_events` (which would make an import
#                            a decision — rejected here as a bypass of the analyst's own act), or
#                            (c) `rce_issues.resolution_notes` (mutable, and it closes a finding).
#                            Until an owner picks (a) or another model, these rows stay staged.
# WHAT IS NEVER APPLIED      `requested_state` / bucket values: recorded as REQUESTED only. Approval,
#                            release and reclassification stay on the determination and QA routes.
# DURABLE IDEMPOTENCY        the apply is serialised per idempotency key with a transaction-scoped
#                            advisory lock and refused if an `case_update_import_applied` audit row
#                            already carries that key — so a retry or a concurrent duplicate submission
#                            cannot apply twice. Matching preview hashes alone decide nothing.

ACTION_ASSIGN = "assign_owner"
STAGED_REASON_NOTES = ("staged, not applied: analyst notes / proposed corrections have no append-only "
                       "case store yet — owner decision between a new case_followup_events table and the "
                       "existing decision-event model (doc 13 §2 / doc 20)")
AUDIT_APPLIED_ACTION = "case_update_import_applied"


def plan_apply(preview: Dict[str, Any]) -> Dict[str, Any]:
    """Partition the preview's rows into what apply will do. Pure; reconciles to rows read."""
    assign: List[Dict[str, Any]] = []
    staged: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for row in preview.get("rows", []):
        if row.get("disposition") != DISP_ACCEPTED:
            skipped.append({"row": row["row"], "disposition": row.get("disposition"), "reason": "; ".join(row.get("reasons") or []) or "not accepted"})
            continue
        changes = row.get("changes") or {}
        target = (row.get("matched_review_ids") or [None])[0]
        if changes.get("assign_to_email"):
            assign.append({"row": row["row"], "review_id": target, "assign_to_email": changes["assign_to_email"]})
        other = {k: v for k, v in changes.items() if k != "assign_to_email"}
        if other:
            staged.append({"row": row["row"], "review_id": target, "content": other, "reason": STAGED_REASON_NOTES})
    total = preview.get("rows_read", 0)
    rows_with_assign = {a["row"] for a in assign}
    rows_with_staged = {s["row"] for s in staged}
    accounted = len(rows_with_assign | rows_with_staged) + len(skipped)
    return {"assignments": assign, "staged_not_applied": staged, "not_applicable": skipped,
            "rows_read": total, "reconciles": accounted == total}


def describe_apply(preview: Dict[str, Any]) -> Dict[str, Any]:
    plan = plan_apply(preview)
    n_assign = len(plan["assignments"])
    return {
        "available": n_assign > 0,
        "applies": "assign_to_email on accepted rows, through case_assignment.assign (senior_analyst or above; "
                   "audit-logged; APPROVED cases refused; held cases refused without an override reason)",
        "assignments_ready": n_assign,
        "staged_not_applied": len(plan["staged_not_applied"]),
        "never_applied": "requested_state / bucket values (recorded as requested only)",
        "reason": (None if n_assign else
                   ("no assignable rows; notes/corrections are staged, not applied — see staged_not_applied"
                    if plan["staged_not_applied"] else
                    "no accepted row carries assign_to_email; nothing is applicable to apply")),
        "unresolved_business_choice": STAGED_REASON_NOTES,
        "endpoint": "POST /api/tefca/arc/cases/import/apply?purpose=case_updates[&worksheet=]&confirm=true (same file)",
    }


async def apply_case_updates(db, *, preview: Dict[str, Any], actor_user, idempotency_key: str,
                             ip_address: Optional[str] = None, resolve_user_id) -> Dict[str, Any]:
    """Apply the plan for ONE staged file, exactly once per idempotency key.

    `resolve_user_id(email) -> uuid | None` is supplied by the route (users table lookup).
    Returns a reconciliation: applied + refused + staged_not_applied + not_applicable = rows read,
    or `already_applied` with the original outcome when the key was applied before.
    """
    from sqlalchemy import select, text
    from app.tefca_registry import audit as reg_audit
    from app.tefca_registry import case_assignment as ca
    from app.tefca_registry import models as reg

    # Serialise all appliers of this key; the lock is held until the surrounding transaction ends,
    # so a concurrent duplicate waits and then sees the audit row the first one committed.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": idempotency_key})
    prior = (await db.execute(
        select(reg.TefcaRegAuditLog.created_at, reg.TefcaRegAuditLog.metadata_)
        .where(reg.TefcaRegAuditLog.action == AUDIT_APPLIED_ACTION,
               reg.TefcaRegAuditLog.metadata_["idempotency_key"].astext == idempotency_key)
        .order_by(reg.TefcaRegAuditLog.created_at.asc()).limit(1))).first()
    if prior is not None:
        return {"already_applied": True, "idempotency_key": idempotency_key,
                "first_applied_at": prior.created_at.isoformat() if prior.created_at else None,
                "original": (prior.metadata_ or {}).get("counts"),
                "note": "this file (same bytes, purpose and mapping version) was already applied; nothing was changed"}

    plan = plan_apply(preview)
    applied: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    actor_id, actor_email = reg_audit.actor_of(actor_user)
    for a in plan["assignments"]:
        to_user = await resolve_user_id(a["assign_to_email"])
        if to_user is None:
            refused.append({**a, "reason": "assign_to_email does not match an active account"})
            continue
        try:
            out = await ca.assign(db, a["review_id"], user=actor_user, to_user_id=to_user,
                                 reason=f"case-update import {idempotency_key[:12]} row {a['row']}",
                                 ip_address=ip_address)
            applied.append({**a, "previous_owner": out.get("previous_owner"), "state": out.get("state")})
        except ca.AssignmentRefused as exc:
            refused.append({**a, "reason": str(exc)})
    counts = {"rows_read": plan["rows_read"], "applied": len(applied), "refused": len(refused),
              "staged_not_applied": len(plan["staged_not_applied"]), "not_applicable": len(plan["not_applicable"])}
    rows_touched = {r["row"] for r in applied} | {r["row"] for r in refused} | {s["row"] for s in plan["staged_not_applied"]} | {s["row"] for s in plan["not_applicable"]}
    counts["reconciles"] = len(rows_touched) == plan["rows_read"]
    reg_audit.record(db, AUDIT_APPLIED_ACTION, None, actor_id=actor_id, actor_email=actor_email,
                     ip_address=ip_address,
                     metadata={"idempotency_key": idempotency_key, "counts": counts, "applied": applied,
                               "refused": refused, "staged_not_applied": plan["staged_not_applied"],
                               "mapping_version": MAPPING_VERSION})
    return {"already_applied": False, "idempotency_key": idempotency_key, "counts": counts,
            "applied": applied, "refused": refused, "staged_not_applied": plan["staged_not_applied"],
            "not_applicable": plan["not_applicable"],
            "unresolved_business_choice": STAGED_REASON_NOTES}
