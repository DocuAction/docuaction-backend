"""IQVIA Release-1 local import: CSV -> PENDING `source_snapshot` +
observation rows. Chunked/streamed throughout -- the real DEMOGRAPHIC extract
is ~155K rows (107MB), HCP_ADDR is ~6.8M rows (3.8GB); nothing here ever
loads a whole file into memory.

WHAT THIS DOES NOT DO
----------------------
Does not approve a snapshot (`source_matching.approve_snapshot` is the only
way a snapshot becomes usable -- a QA-lead-or-above human action, append-only,
never from this module). Does not match observations to registry entities
(`iqvia_match.py`). Does not touch any TEFCA-authoritative field --
`source_matching.assert_not_tefca_fact` already refuses that for any
observation source, and nothing here writes outside `payload`/the lifted
identifier columns anyway.

FORMAT, CONFIRMED AGAINST THE REAL DELIVERED FILES (2026-10-02 local
profiling; no licensed values left that analysis)
---------------------------------------------------------------------
Comma-delimited, CRLF, UTF-8, no BOM, a header row that exactly matches the
corresponding layout file's field list, blank string is the null convention
(no literal "NULL"/"NA" token). Every field is read as `str` --
`csv.DictReader` already yields strings; the only discipline required is to
NEVER let anything downstream parse an identifier column as numeric, since
~10% of ZIPs and 14% of populated CCNs in the real file carry a leading zero.

IDEMPOTENCY AND RESUMABILITY
---------------------------------
Each row's insert targets `(source_snapshot_id, source_record_key)`, the
table's own unique constraint, via `ON CONFLICT DO NOTHING`. Calling the
import function again with the SAME `snapshot_id` (pass it explicitly to
resume) only inserts whatever is still missing -- an interrupted run, or a
deliberate retry, never double-counts or errors on rows already staged.

REJECTED-ROW ACCOUNTING
----------------------------
A row is REJECTED, not silently dropped, when it has no usable
`source_record_key` (the one field every row must carry to be addressable at
all). Rejected rows are counted and the first `_MAX_SAMPLE_REJECTED_LINES`
line numbers are returned for follow-up -- never the row content itself, to
keep this module's own return value and anything it logs free of licensed
field values.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import inspect
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import request_context
from app.core.upload_security import open_no_follow
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce.source_matching import register_snapshot

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 5000
MAX_ROWS_PER_STATEMENT = 100
_MAX_SAMPLE_REJECTED_LINES = 25

# Column names confirmed against the real delivered layouts (2026-10-02
# local profiling). Kept here, not guessed per-call, so a caller cannot
# silently point the importer at the wrong key for a given extract.
HCO_KEY_FIELD = "HCO_HCE_ID"
HCO_NPI_FIELD = "ORG_NPI"
HCO_CCN_FIELD = "ORG_CCN_ID"
HCP_HCE_FIELD = "HCP_HCE_ID"
HCP_ADDR_ID_FIELD = "ADDR_ID"
HCP_NPI_FIELD = "NPI"


@dataclass
class ImportSummary:
    snapshot_id: uuid.UUID
    source_system: str
    rows_read: int = 0
    rows_staged: int = 0          # newly inserted this run
    rows_already_staged: int = 0  # ON CONFLICT DO NOTHING hits -- already there
    rows_rejected: int = 0
    rejected_sample_lines: List[int] = field(default_factory=list)
    file_sha256: str = ""
    completed: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": str(self.snapshot_id), "source_system": self.source_system,
            "rows_read": self.rows_read, "rows_staged": self.rows_staged,
            "rows_already_staged": self.rows_already_staged,
            "rows_rejected": self.rows_rejected,
            "rejected_sample_lines": self.rejected_sample_lines,
            "file_sha256": self.file_sha256, "completed": self.completed,
        }


def _cid() -> str:
    return request_context.correlation_id()[:64]


async def _notify_progress(progress_cb, summary: "ImportSummary") -> None:
    """`progress_cb` may be a plain sync callable (existing tests/callers) or
    an async one (the durable-job runner, which needs to await a DB write
    for a live heartbeat) -- accept either without the caller having to
    know which, and without breaking any existing sync callback."""
    if progress_cb is None:
        return
    result = progress_cb(summary)
    if inspect.isawaitable(result):
        await result


def file_sha256(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    """Streamed hash -- a 3.8GB file is never read into memory at once.
    open_no_follow, not open(): refuses a symlink even if one replaced
    `path` after safe_existing_path's own check (TOCTOU)."""
    h = hashlib.sha256()
    with open_no_follow(path, "rb") as fh:
        while True:
            block = fh.read(chunk_bytes)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


#: The one decoding of a staged reference file, shared with the preflight that
#: gates it (see reference_preflight.REFERENCE_FILE_ENCODING). Before 2026-11-14
#: this module opened the file as "utf-8": a leading UTF-8 byte-order mark,
#: which the preflight tolerates (REF-SCH-010), stayed in the first column's
#: name, no row carried the identity column, and every row was rejected.
from app.tefca_registry.rce.reference_preflight import REFERENCE_FILE_ENCODING  # noqa: E402
from app.tefca_registry.rce import iqvia_contract_registry as contract_registry  # noqa: E402
from app.tefca_registry.rce import iqvia_intake_validation as intake  # noqa: E402

#: Snapshot metadata keys written by the intake-contract work (2026-11-25).
#: None of them is read by `iqvia_reconciliation.compute_staging_token`.
META_SOURCE_HEADER = "source_header"          # ordered header names + digest, as delivered
META_CONTRACT_BINDING = "contract_binding"    # which registered contract the header binds to, by name
META_IMPORT_VALIDATION = "import_validation"  # full-file validation summary, written once at the end


#: Which reader staged a snapshot's rows, recorded in the snapshot's metadata
#: ("reference_reader") when staging starts. A snapshot holds the rows of ONE
#: reader: a resume is allowed only where the recorded reader is this one, or
#: where no reader is recorded and the earlier reader (plain "utf-8") provably
#: read the file the same way - that is, the file has no leading byte-order
#: mark. Change this value whenever a change to the read can change a row's
#: keys, payload or hash.
REFERENCE_READER = f"iqvia_import/{REFERENCE_FILE_ENCODING}"
_UTF8_BOM = b"\xef\xbb\xbf"


class ReaderChangeRefused(ValueError):
    """A resume into a snapshot whose staged rows came from a different reader.

    Nothing is staged, rewritten or deleted. The snapshot stays PENDING, is
    marked in its metadata ("reference_reader_refused") and can never
    reconcile, so it can never be approved. This module has no way to discard
    a PENDING snapshot's rows; what happens to it is an owner decision."""


def _starts_with_utf8_bom(path) -> bool:
    with open_no_follow(path, "rb") as fh:
        return fh.read(3) == _UTF8_BOM


async def _admit_reader(db, snapshot, *, model, path) -> None:
    """Record this reader on the snapshot, or refuse the resume.

    Decided BEFORE the preflight is recorded and before any row is staged."""
    meta = dict(snapshot.metadata_ or {})
    recorded = meta.get("reference_reader")
    if recorded == REFERENCE_READER:
        return
    staged = int((await db.execute(
        select(func.count()).select_from(model)
        .where(model.source_snapshot_id == snapshot.id))).scalar() or 0)
    # No recorded reader means the plain utf-8 reader, which differs from this
    # one in exactly one case: a leading byte-order mark (it stayed in the
    # first column's name, so that column was lost from every staged row).
    differs = _starts_with_utf8_bom(path) if recorded is None else True
    if staged and differs:
        snapshot.metadata_ = {**meta, "reference_reader_refused": {
            "code": "READER_CHANGED_AFTER_STAGING",
            "rows_already_staged": staged,
            "staged_by": recorded or "utf-8 (earlier reader, not recorded)",
            "refused_by": REFERENCE_READER,
            "refused_at": datetime.now(timezone.utc).isoformat()}}
        await db.commit()
        raise ReaderChangeRefused(
            f"snapshot {snapshot.id}: {staged} row(s) were staged by an earlier reader that "
            "kept a byte-order mark or read differently; resume refused, nothing staged or "
            "changed. It cannot reconcile. No automated recovery: owner decision.")
    meta.pop("reference_reader_refused", None)
    snapshot.metadata_ = {**meta, "reference_reader": REFERENCE_READER}
    await db.commit()


def iter_reference_rows(path):
    """(line number, row) for every data row of a staged reference file - the
    ONLY reader of that file's rows in this module.

    Decoded with REFERENCE_FILE_ENCODING, so a byte-order mark at the very
    start of the file is not part of the first column's name. Only those three
    bytes are treated specially: a U+FEFF anywhere else is data and is kept,
    and nothing else about the read differs from a plain utf-8 read (an
    unmarked file yields exactly the rows it always did). The file itself is
    opened read-only and is never rewritten; its sha256 is taken from the raw
    bytes by `file_sha256`, mark included. The header is line 1.

    A row is built by `iqvia_intake_validation.legacy_row`, which reproduces
    `csv.DictReader` exactly (pinned by tests/test_iqvia_contract_registry_2026_11_25.py).
    `_import_csv` reads the same records through `_iter_reference_records` so
    that it can see each record's cells before they are keyed by name."""
    records = _reference_cells(path)
    header = next(records, None)
    if header is None:
        return
    line_no = 1
    for cells in records:
        if not cells:
            continue
        line_no += 1
        yield line_no, intake.legacy_row(header, cells)


def _reference_cells(path):
    """Every row of the file as `csv.reader` returns it, the header row first.
    The ONE text-mode open of a staged reference file in this module."""
    # open_no_follow, not open(): same TOCTOU reason as file_sha256 above --
    # this is the actual multi-GB import read, the highest-value target for
    # a file swapped in between validation and this call.
    with open_no_follow(path, "r", encoding=REFERENCE_FILE_ENCODING, newline="") as fh:
        yield from csv.reader(fh)


#: Which delivered column each lifted identifier column is taken from.
_LIFTED_SOURCE_COLUMNS = {
    sm.SOURCE_IQVIA_HCO: {"npi": "ORG_NPI", "ccn": "ORG_CCN_ID"},
    sm.SOURCE_IQVIA_HCP: {"npi": "NPI"},
    sm.SOURCE_IQVIA_AFFILIATION: {},
}


def _iter_reference_records(path, validation):
    """(record number, cells) for every data record, read with `csv.reader`:
    the same file, decoding and reader settings as `iter_reference_rows`, minus
    the name-keyed mapping. The header row is consumed and not yielded; blank
    lines are skipped (and counted on `validation`), exactly as DictReader skips
    them. Record numbers start at 1."""
    records = _reference_cells(path)
    if next(records, None) is None:
        return
    record_no = 0
    for cells in records:
        if not cells:
            validation.blank_lines += 1
            continue
        record_no += 1
        yield record_no, cells


#: How often the job is told the worker is alive while a whole-file pass runs
#: in a worker thread. Far below iqvia_upload_jobs.STALE_HEARTBEAT_SECONDS.
OFF_LOOP_HEARTBEAT_SECONDS = 30


async def _off_loop(fn, *args, heartbeat_cb=None, phase: str = ""):
    """Run a blocking whole-file pass (the sha256, the reference preflight) in
    a worker thread, so the event loop keeps serving requests, and keep the
    job heartbeat alive meanwhile so the reaper cannot take a long hash for a
    dead worker and requeue it. `heartbeat_cb(phase)` is awaited now and every
    OFF_LOOP_HEARTBEAT_SECONDS until `fn` returns; a failed beat is logged and
    never stops the work. Without a callback the work still leaves the loop."""
    if heartbeat_cb is None:
        return await asyncio.to_thread(fn, *args)
    stop = asyncio.Event()

    async def _beat() -> None:
        while not stop.is_set():
            try:
                await heartbeat_cb(phase)
            except Exception as exc:  # noqa: BLE001 - a missed beat must not kill the import
                logger.warning("IQVIA import heartbeat during %s failed: %s", phase, type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=OFF_LOOP_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass

    beat = asyncio.create_task(_beat())
    try:
        return await asyncio.to_thread(fn, *args)
    finally:
        stop.set()
        await beat


def _preflight_and_header(path, source_system: str):
    """The two reads of the file that precede staging, as one blocking call."""
    from app.tefca_registry.rce import reference_preflight as _rp
    preflight = _rp.preflight_reference_file(path, source_system)
    header, header_error = intake.read_reference_header(path)
    return preflight, header, header_error


def _row_sha256(row: Dict[str, str]) -> str:
    """Of the row's own fields, canonically ordered -- reproducible from the
    stored `payload` alone, independent of the source CSV's column order."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _clean(value: Optional[str]) -> Optional[str]:
    """Blank string is this format's null convention (confirmed against the
    real file) -- normalized to None so a lifted npi/ccn column reads as
    actually absent, not as an empty string that would pass `IS NOT NULL`
    checks unexpectedly."""
    if value is None:
        return None
    v = value.strip()
    return v or None


async def _stage_rows(
    db, *, snapshot_id, model, rows: List[Dict[str, Any]],
) -> int:
    """One bulk upsert per chunk. Returns how many were ACTUALLY new (the
    `RETURNING` clause only returns the rows the INSERT touched; a
    conflict-skipped row returns nothing)."""
    if not rows:
        return 0
    # The conflict target is the table's own named unique constraint, not
    # the primary key (the pk is a fresh uuid every row, never a conflict)
    # -- set explicitly so DO NOTHING actually fires on a re-run of the SAME
    # snapshot, rather than inserting duplicate rows under new uuids.
    constraint_name = {
        sm.IqviaHcoObservation: "uq_iqvia_hco_snapshot_key",
        sm.IqviaHcpObservation: "uq_iqvia_hcp_snapshot_key",
        sm.IqviaAffiliationObservation: "uq_iqvia_affiliation_snapshot_key",
    }[model]
    # asyncpg refuses more than 32,767 bound parameters in one statement
    # (`InterfaceError: the number of query arguments cannot exceed 32767`)
    # -- hit in practice at the default chunk size (5000 rows x 9 HCO
    # columns = 45,000). `values(rows)` binds every cell of every row in
    # the chunk as one parameter, so the real cap is rows-per-column, not
    # rows-per-chunk; sub-batching here means a caller's `chunk_size` is a
    # COMMIT-frequency knob only, never something it has to get exactly
    # right to avoid this error.
    cols_per_row = max(1, len(rows[0]))
    # ...and, separately, a throughput cap: one statement's cost grows faster than its row count. Measured on
    # the real 191-column affiliation shape (PostgreSQL 18, 3,000 JSONB rows): 100 rows/statement ~1,100-1,600
    # rows/s, 400 ~520, 800 ~335, and the previous ~3,600-row statement ~82 rows/s -- 17x slower.
    max_rows_per_statement = max(1, min(32767 // cols_per_row, MAX_ROWS_PER_STATEMENT))
    staged = 0
    for start in range(0, len(rows), max_rows_per_statement):
        sub = rows[start:start + max_rows_per_statement]
        stmt = pg_insert(model).values(sub).on_conflict_do_nothing(
            constraint=constraint_name).returning(model.id)
        result = await db.execute(stmt)
        staged += len(result.fetchall())
    return staged


async def _import_csv(
    db, *, file_path: Path, source_system: str, model, key_field: str,
    build_payload_and_keys: Callable[[Dict[str, str]], Optional[Dict[str, Any]]],
    label: str, created_by: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
    snapshot_id: Optional[uuid.UUID] = None,
    progress_cb: Optional[Callable[[ImportSummary], None]] = None,
    heartbeat_cb: Optional[Callable[[str], Any]] = None,
) -> ImportSummary:
    """Shared engine behind the three public `import_*_csv` functions below.

    `build_payload_and_keys(row_dict) -> {"source_record_key": ..., "npi": ...,
    "ccn": ..., ...}` or `None` to reject the row -- extract-specific, passed
    in by the caller rather than branched on here, so adding a fourth extract
    never means editing this function.
    """
    path = Path(file_path)
    # Off the event loop, with a heartbeat (2026-11-25): hashing a multi-GB file
    # here used to block every request for minutes and write no heartbeat.
    sha = await _off_loop(file_sha256, path, heartbeat_cb=heartbeat_cb, phase="hashing the file")

    if snapshot_id is not None:
        snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
        if snapshot is None:
            raise ValueError(f"no snapshot {snapshot_id} to resume into")
        if snapshot.status != sm.SNAPSHOT_PENDING:
            raise ValueError(
                f"snapshot {snapshot_id} is {snapshot.status}, not PENDING -- "
                "an approved/rejected snapshot is never resumed into")
        if snapshot.sha256 != sha:
            raise ValueError(
                f"file at {path} (sha256 {sha[:12]}...) does not match the "
                f"snapshot being resumed (sha256 {snapshot.sha256[:12]}...); "
                "refusing to mix two different files under one snapshot id")
    else:
        # record_count is corrected to the real total once the file has been
        # read in full, below -- registered with 0 so the row exists for a
        # caller to reference (e.g. to resume) even if staging is interrupted
        # immediately after registration.
        snapshot = await register_snapshot(
            db, source_system=source_system, label=label, sha256=sha,
            record_count=0, received_at=datetime.now(timezone.utc),
            created_by=created_by, metadata={"original_path": str(path)})

    # ONE READER PER SNAPSHOT (2026-11-14): recorded when staging starts, and a
    # resume into rows staged by a different reader is refused here, before the
    # preflight is recorded and before any row is staged (see REFERENCE_READER).
    await _admit_reader(db, snapshot, model=model, path=path)

    # REFERENCE-SNAPSHOT PREFLIGHT (2026-10-04, Part B; reference_preflight.py).
    # Always RUN and always RECORDED on the snapshot -- the shadow record.
    # ENFORCED (the import refused before any row is staged) only when
    # ENABLE_PREFLIGHT_ENFORCEMENT is on; off by default, so an official
    # import behaves exactly as before. Independently of the flag, a
    # snapshot whose recorded gate is BLOCKED can never reconcile -- see
    # `verify_snapshot_staged_completely`.
    from app.core.config import settings as _settings
    from app.tefca_registry.rce import reference_preflight as _rp

    preflight, header, header_error = await _off_loop(
        _preflight_and_header, path, source_system,
        heartbeat_cb=heartbeat_cb, phase="reference preflight (whole-file read)")
    enforce = bool(getattr(_settings, "ENABLE_PREFLIGHT_ENFORCEMENT", False))
    # INTAKE CONTRACT (2026-11-25), RECORD ONLY. The header is read with
    # csv.reader BEFORE any row is keyed by name, bound by NAME to the registered
    # contracts of this source, and stored with the preflight in ONE commit.
    # Nothing here refuses, and no row's stored content depends on it.
    registry = contract_registry.get_registry()
    source_header = contract_registry.describe_header(registry, source_system, header)
    if header_error:
        source_header["unreadable"] = header_error
    binding = contract_registry.bind_header(registry, source_system, header)
    snapshot.metadata_ = {**(snapshot.metadata_ or {}),
                          META_SOURCE_HEADER: source_header, META_CONTRACT_BINDING: binding,
                          "reference_preflight": {
        "gate": preflight["gate"], "schema_version": preflight["schema_version"],
        "preflight_version": preflight["preflight_version"],
        "enforced": enforce, "rows_sampled": preflight["rows_sampled"],
        "bound": preflight["bound"], "held_checks": preflight["held_checks"],
        "findings": [{k: f[k] for k in ("code", "field_name", "applicability",
                                        "execution", "disposition", "description",
                                        "evidence")} for f in preflight["findings"]],
    }}
    await db.commit()
    if enforce and preflight["gate"] == _rp.pm.GATE_BLOCKED:
        codes = sorted({f["code"] for f in preflight["findings"]
                        if f["disposition"] == _rp.pm.DISP_BLOCKED})
        raise _rp.ReferencePreflightBlocked(
            f"reference preflight BLOCKED {source_system} snapshot {snapshot.id}: "
            f"{', '.join(codes)}. No row was staged. One technical issue, not one "
            f"finding per record.")

    summary = ImportSummary(snapshot_id=snapshot.id, source_system=source_system,
                            file_sha256=sha)
    cid = _cid()

    contract, basis = contract_registry.contract_for_validation(registry, source_system, binding)
    validation = intake.FullFileValidation(
        header=header, contract=contract, basis=basis,
        lifted_limits={c: model.__table__.c[c].type.length
                       for c in ("npi", "ccn") if c in model.__table__.c},
        lifted_sources=_LIFTED_SOURCE_COLUMNS.get(source_system, {}))

    buffer: List[Dict[str, Any]] = []
    for record_no, cells in _iter_reference_records(path, validation):
        line_no = record_no + 1               # what enumerate(DictReader, start=2) called the line
        summary.rows_read += 1
        if len(cells) == validation.width:
            validation.measure(record_no, cells)
        else:
            validation.width_differs(record_no, cells)
        row = intake.legacy_row(validation.header, cells)
        built = build_payload_and_keys(row)
        # `built is None` is the one universal rejection signal every
        # extract's builder uses -- the field that actually carries the
        # row's identity differs by extract (`source_record_key` for
        # HCO/HCP, `hcp_record_key`/`hco_record_key` for affiliation),
        # so this check cannot also hardcode a field name without
        # silently rejecting every row of whichever extract doesn't use
        # it (confirmed the hard way: this previously rejected 100% of
        # affiliation rows).
        if built is None:
            summary.rows_rejected += 1
            if len(summary.rejected_sample_lines) < _MAX_SAMPLE_REJECTED_LINES:
                summary.rejected_sample_lines.append(line_no)
            continue
        validation.lifted(record_no, built)
        buffer.append({
            "id": uuid.uuid4(), "source_snapshot_id": snapshot.id,
            "record_sha256": _row_sha256(row), "observed_at": datetime.now(timezone.utc),
            "correlation_id": cid, "payload": row,
            **{k: v for k, v in built.items()},
        })
        if len(buffer) >= chunk_size:
            staged = await _stage_rows(db, snapshot_id=snapshot.id, model=model, rows=buffer)
            summary.rows_staged += staged
            summary.rows_already_staged += len(buffer) - staged
            await db.commit()
            buffer = []
            await _notify_progress(progress_cb, summary)
    if buffer:
        staged = await _stage_rows(db, snapshot_id=snapshot.id, model=model, rows=buffer)
        summary.rows_staged += staged
        summary.rows_already_staged += len(buffer) - staged
        await db.commit()
        await _notify_progress(progress_cb, summary)

    # Correct record_count to the real total now that the file has been read
    # in full, and persist the rejected count alongside it -- the exact
    # number `verify_snapshot_staged_completely` needs to tell "fully staged"
    # apart from "quietly incomplete" (rows_read - rows_rejected is the
    # number of rows that SHOULD be in the observation table; record_count
    # alone, without rejected, would make an interrupted run that stopped
    # partway look indistinguishable from a complete one, since both would
    # read `staged <= record_count` as true).
    snapshot.record_count = summary.rows_read
    # The full-file validation summary is written ONCE, here, when the file has
    # been read to its end - under its own key, bounded in size, and holding
    # column names, counts and record numbers only.
    snapshot.metadata_ = {**(snapshot.metadata_ or {}), "rows_rejected": summary.rows_rejected,
                          META_IMPORT_VALIDATION: validation.as_metadata(rows={
                              "read": summary.rows_read, "inserted": summary.rows_staged,
                              "already_present": summary.rows_already_staged,
                              "rejected": summary.rows_rejected})}
    await db.commit()

    summary.completed = True
    return summary


def _hco_payload_and_keys(row: Dict[str, str]) -> Optional[Dict[str, Any]]:
    key = _clean(row.get(HCO_KEY_FIELD))
    if not key:
        return None
    return {"source_record_key": key, "npi": _clean(row.get(HCO_NPI_FIELD)),
            "ccn": _clean(row.get(HCO_CCN_FIELD))}


def _hcp_payload_and_keys(row: Dict[str, str]) -> Optional[Dict[str, Any]]:
    hcp_id = _clean(row.get(HCP_HCE_FIELD))
    addr_id = _clean(row.get(HCP_ADDR_ID_FIELD))
    if not hcp_id:
        return None
    # The row grain is (practitioner, address) -- HCP_HCE_ID alone repeats
    # across a practitioner's address rows in the real file (up to 39 of
    # them). Falling back to the practitioner id alone when ADDR_ID is
    # missing would silently collapse distinct address rows under one key
    # and lose all but the first to ON CONFLICT DO NOTHING -- reject instead.
    if not addr_id:
        return None
    return {"source_record_key": f"{hcp_id}:{addr_id}", "npi": _clean(row.get(HCP_NPI_FIELD))}


#: The columns of the real HCP_AFFIL layout (191 wide, delivered 2026-09-21) that carry the RELATIONSHIP and
#: the identifiers needed to match it. Every row of that file repeats the full HCP and HCO descriptive blocks
#: (names, birth year, addresses, 84 opening-hours and language columns, ...); storing all 191 columns as JSONB
#: measured ~3.4 KB/row, ~26 GB for the 7.55M-row file, more than the DEV database has free. The full row is
#: still hashed into `record_sha256` and the source file's own sha256 is on the snapshot, so any stored row can
#: be re-verified against the untouched original; only the descriptive duplication is not copied.
#: Layers, so the owner's projection decision is one switch and every column has a stated purpose
#: (qa-evidence/2026-10-06-iqvia-affil-assessment/PROJECTION-MATRIX-PR123-2026-10-07.md):
#:   KEYS_AND_IDS (17)      relationship, identifiers and the match inputs that run today
#:   ORG_DESCRIPTORS (+8)   organisation name/alias, address and phone: the inputs of the declared
#:                          name+address+phone method and the reviewer's reading of a candidate
#:   PERSON_NAMES (+2)      HCP first/last name: display only, personal data, never a match input;
#:                          OFF unless IQVIA_AFFIL_STORE_PERSON_NAMES=1 (data minimisation)
AFFILIATION_KEYS_AND_IDS = (
    "HCP_HCE_ID", "OK_INDV_ID", "NPI", "HCO_HCE_ID", "OK_WKP_ID", "ORG_NPI", "ORG_CCN_ID", "ORG_TAX_ID", "ADDR_ID",
    "AFFL_TYP_ID", "AFFL_TYP_DESC", "AFFL_GRP_CD", "AFFL_GRP_DESC",
    "TITL_TYP_ID", "TITL_TYP_DESC", "TITL_CATG_CD", "TITL_CATG_DESC",
)
AFFILIATION_ORG_DESCRIPTORS = (
    "BUS_NM", "DBA_NM", "ADDR_LN_1_TXT", "ADDR_LN_2_TXT", "CITY_NM", "ST_CD", "ZIP5_CD", "TELEPHN_NBR",
)
AFFILIATION_PERSON_NAMES = ("FRST_NM", "LAST_NM")


def affiliation_payload_columns() -> tuple:
    """The projection in force: 25 columns by default, 27 with IQVIA_AFFIL_STORE_PERSON_NAMES=1. Read per call
    (not at import) so the switch is testable and takes effect on the next import."""
    import os
    cols = AFFILIATION_KEYS_AND_IDS + AFFILIATION_ORG_DESCRIPTORS
    if os.getenv("IQVIA_AFFIL_STORE_PERSON_NAMES", "").strip().lower() in ("1", "true", "yes"):
        cols += AFFILIATION_PERSON_NAMES
    return cols


#: Back-compat name for the layer the importer's keys depend on.
AFFILIATION_PAYLOAD_COLUMNS = AFFILIATION_KEYS_AND_IDS


def _affiliation_kind(row: Dict[str, str]) -> str:
    """The affiliation's kind, never NULL (a NULL would defeat the table's unique key and let a resumed run
    insert the same row twice). The real file holds two kinds of relationship: a PROVIDER affiliation
    (AFFL_TYP_ID, e.g. attending/admitting) and a CONTACT affiliation (TITL_TYP_ID, a person's role at an
    organisation) -- in the delivered file every row is exactly one of them. AFFIL_TYPE_CD is the earlier
    synthetic-fixture spelling, still accepted."""
    provider = _clean(row.get("AFFL_TYP_ID")) or _clean(row.get("AFFIL_TYPE_CD"))
    if provider:
        return provider
    contact = _clean(row.get("TITL_TYP_ID"))
    return f"CONTACT:{contact}" if contact else "UNTYPED"


def _affiliation_payload_and_keys(row: Dict[str, str]) -> Optional[Dict[str, Any]]:
    hcp_key = _clean(row.get(HCP_HCE_FIELD))
    hco_key = _clean(row.get("HCO_HCE_ID"))
    if not hcp_key or not hco_key:
        return None
    slim = {c: row[c] for c in affiliation_payload_columns() if c in row}
    if "AFFIL_TYPE_CD" in row:
        slim["AFFIL_TYPE_CD"] = row["AFFIL_TYPE_CD"]
    return {"hcp_record_key": hcp_key, "hco_record_key": hco_key,
            "affiliation_type": _affiliation_kind(row), "payload": slim}


async def import_hco_csv(db, *, file_path, label: str, created_by: str,
                         chunk_size: int = DEFAULT_CHUNK_SIZE,
                         snapshot_id: Optional[uuid.UUID] = None,
                         progress_cb: Optional[Callable[[ImportSummary], None]] = None,
                         heartbeat_cb: Optional[Callable[[str], Any]] = None,
                         ) -> ImportSummary:
    """DEMOGRAPHIC extract -> `iqvia_hco_observation`. Organisation-level."""
    return await _import_csv(
        db, file_path=file_path, source_system=sm.SOURCE_IQVIA_HCO,
        model=sm.IqviaHcoObservation, key_field=HCO_KEY_FIELD,
        build_payload_and_keys=_hco_payload_and_keys, label=label, created_by=created_by,
        chunk_size=chunk_size, snapshot_id=snapshot_id, progress_cb=progress_cb,
        heartbeat_cb=heartbeat_cb)


async def import_hcp_csv(db, *, file_path, label: str, created_by: str,
                         chunk_size: int = DEFAULT_CHUNK_SIZE,
                         snapshot_id: Optional[uuid.UUID] = None,
                         progress_cb: Optional[Callable[[ImportSummary], None]] = None,
                         heartbeat_cb: Optional[Callable[[str], Any]] = None,
                         ) -> ImportSummary:
    """HCP_ADDR extract -> `iqvia_hcp_observation`. Individual-practitioner,
    one row per address. NOTE (confirmed by local profiling): the delivered
    file carries no populated HOSP_AFFIL_* link to any HCO -- these rows
    cannot feed organisation matching on their own; see `iqvia_match.py`."""
    return await _import_csv(
        db, file_path=file_path, source_system=sm.SOURCE_IQVIA_HCP,
        model=sm.IqviaHcpObservation, key_field=HCP_HCE_FIELD,
        build_payload_and_keys=_hcp_payload_and_keys, label=label, created_by=created_by,
        chunk_size=chunk_size, snapshot_id=snapshot_id, progress_cb=progress_cb,
        heartbeat_cb=heartbeat_cb)


async def import_affiliation_csv(db, *, file_path, label: str, created_by: str,
                                 chunk_size: int = DEFAULT_CHUNK_SIZE,
                                 snapshot_id: Optional[uuid.UUID] = None,
                                 progress_cb: Optional[Callable[[ImportSummary], None]] = None,
                                 heartbeat_cb: Optional[Callable[[str], Any]] = None,
                                 ) -> ImportSummary:
    """HCP_AFFIL extract -> `iqvia_affiliation_observation`. No data has been
    delivered for this extract as of 2026-10-02 (layout only) -- this
    function exists and is tested against a synthetic fixture so the path is
    ready the day a populated file arrives; it is never called against a
    real file by anything in this pass."""
    return await _import_csv(
        db, file_path=file_path, source_system=sm.SOURCE_IQVIA_AFFILIATION,
        model=sm.IqviaAffiliationObservation, key_field=HCP_HCE_FIELD,
        build_payload_and_keys=_affiliation_payload_and_keys, label=label,
        created_by=created_by, chunk_size=chunk_size, snapshot_id=snapshot_id,
        progress_cb=progress_cb, heartbeat_cb=heartbeat_cb)


async def verify_snapshot_staged_completely(db, snapshot_id) -> Dict[str, Any]:
    """Reconciliation gate: does the observation table actually hold
    `record_count` rows for this snapshot? Run this BEFORE approving --
    `approve_snapshot` itself has no way to know which observation table a
    given snapshot's rows live in, so it cannot check this on its own.
    Atomic publication in practice: a snapshot that fails this check is never
    approved, so its rows are never read by anything (nothing reads a
    PENDING snapshot) -- a partial import never becomes active."""
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError(f"no snapshot {snapshot_id}")
    model = {
        sm.SOURCE_IQVIA_HCO: sm.IqviaHcoObservation,
        sm.SOURCE_IQVIA_HCP: sm.IqviaHcpObservation,
        sm.SOURCE_IQVIA_AFFILIATION: sm.IqviaAffiliationObservation,
    }.get(snapshot.source_system)
    if model is None:
        raise ValueError(f"{snapshot.source_system} is not an IQVIA observation source")
    staged = (await db.execute(
        select(func.count()).select_from(model)
        .where(model.source_snapshot_id == snapshot_id))).scalar() or 0
    # `record_count` is every row READ, including rejected ones that never
    # reached the observation table by design -- comparing `staged` directly
    # against it would read an interrupted import (far fewer rows staged
    # than declared) as "fine" the same way a complete one is, since
    # staged <= record_count holds in both cases. `rows_rejected`
    # (persisted into metadata by the importer) narrows this to an exact
    # equality: a snapshot not produced by this module's importer (no
    # `rows_rejected` key at all) is assumed to have rejected none, which
    # keeps this check meaningful rather than permissive by default.
    rejected = int((snapshot.metadata_ or {}).get("rows_rejected", 0))
    expected = snapshot.record_count - rejected
    # 2026-10-04 (Part B): equality alone reconciled an EMPTY snapshot. A file
    # with a missing/renamed identity column has every row rejected, so
    # expected == staged == 0 and the check read True -- an approvable
    # reference snapshot containing nothing. Two refusals, both independent
    # of ENABLE_PREFLIGHT_ENFORCEMENT (they only ever withhold approval):
    reasons = []
    if staged != expected:
        reasons.append("staged row count does not equal read-minus-rejected")
    if (snapshot.record_count or 0) > 0 and staged == 0:
        reasons.append("every row read was rejected; an all-rejected extract is a "
                       "schema/identity fault, not an empty-but-valid snapshot")
    ref_preflight = (snapshot.metadata_ or {}).get("reference_preflight") or {}
    if ref_preflight.get("gate") == "BLOCKED":
        reasons.append("reference preflight gate is BLOCKED for this snapshot")
    # A snapshot whose resume was refused holds rows of an earlier reader and
    # is incomplete by construction; said explicitly so the reason is not read
    # as an ordinary interrupted import that a retry would finish.
    if (snapshot.metadata_ or {}).get("reference_reader_refused"):
        reasons.append("rows were staged by an earlier reader and the resume was refused "
                       "(READER_CHANGED_AFTER_STAGING); this snapshot cannot be completed")
    return {
        "snapshot_id": str(snapshot_id), "source_system": snapshot.source_system,
        "declared_record_count": snapshot.record_count, "rows_rejected": rejected,
        "expected_staged_count": expected, "staged_row_count": staged,
        "reference_preflight_gate": ref_preflight.get("gate"),
        "reconciles": not reasons,
        "refusal_reasons": reasons,
    }
