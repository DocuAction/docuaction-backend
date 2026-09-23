"""File-intake safety helpers for evidence bundles. ISOLATED: nothing in the
platform imports this module; it is used by the entity-intelligence adapters
and the future NPPES acquisition job only.

Threats covered (each with a test in tests/test_entity_intelligence_security.py):
    zip-slip           member names with '..', absolute paths, drive letters or
                       backslashes are refused before extraction
    archive bomb       member count, per-member and total uncompressed size and
                       compression ratio are bounded; the CMS monthly bundle
                       (~1 GB zip → ~10 GB text) fits inside the defaults
    unexpected content member extensions must be on the allowlist
    CSV injection      cells that a spreadsheet would evaluate (=, +, -, @,
                       tab, CR) are neutralised for any export/display path
    oversized fields   csv field-size and row/column limits are explicit so a
                       pathological file fails with a status, not a hang
    path traversal     a preserved file is only ever written under the intake
                       root; the resolved path must stay inside it

Nothing here opens the network or touches the shared database.
"""
from __future__ import annotations

import csv
import io
import os
import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

#: CMS monthly V2 bundle: zip ≈ 1 GB, npidata csv ≈ 10 GB. Defaults leave headroom
#: without admitting a bomb (ratio 1000:1 would be refused at 200:1).
DEFAULT_MAX_MEMBERS = 64
DEFAULT_MAX_MEMBER_BYTES = 16 * 1024 ** 3        # 16 GiB uncompressed, one member
DEFAULT_MAX_TOTAL_BYTES = 24 * 1024 ** 3         # 24 GiB uncompressed, whole archive
DEFAULT_MAX_RATIO = 200.0                        # uncompressed / compressed
DEFAULT_ALLOWED_SUFFIXES = (".csv", ".pdf", ".txt")

#: Cell prefixes Excel/LibreOffice/Sheets treat as a formula or command.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

#: Python's csv default field limit is 128 KiB; NPPES V2 fields are ≤ 300 chars.
DEFAULT_MAX_FIELD_CHARS = 4096
DEFAULT_MAX_COLUMNS = 400        # NPPES main file has 330
DEFAULT_MAX_ROWS = 10_000_000    # NPPES main file ≈ 8.9M rows


class IntakeRefused(ValueError):
    """The file was refused before anything was read. The message is the
    explicit reason and is safe to log (no file content is echoed)."""


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    compressed_size: int
    uncompressed_size: int

    @property
    def ratio(self) -> float:
        return self.uncompressed_size / self.compressed_size if self.compressed_size else float("inf")


@dataclass
class ArchiveInspection:
    members: List[ArchiveMember] = field(default_factory=list)
    total_uncompressed: int = 0
    refusals: List[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.refusals


def is_safe_member_name(name: str) -> bool:
    """True only for a relative, forward-slash, dot-free path with no drive
    letter, no leading slash and no backslash. Zip-slip is the reason."""
    if not name or name != name.strip():
        return False
    if "\\" in name or name.startswith("/") or ":" in name:
        return False
    if "\x00" in name:
        return False
    parts = posixpath.normpath(name).split("/")
    if any(p in ("..", "") for p in parts) or parts[0] == ".":
        return False
    return posixpath.normpath(name) == name.rstrip("/")


def inspect_archive(zf: zipfile.ZipFile, *, max_members: int = DEFAULT_MAX_MEMBERS,
                    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
                    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
                    max_ratio: float = DEFAULT_MAX_RATIO,
                    allowed_suffixes: Sequence[str] = DEFAULT_ALLOWED_SUFFIXES) -> ArchiveInspection:
    """Inspect the central directory ONLY. No member is decompressed. Every
    refusal is recorded; the archive is accepted only if there are none."""
    out = ArchiveInspection()
    infos = zf.infolist()
    if len(infos) > max_members:
        out.refusals.append(f"too many members: {len(infos)} > {max_members}")
    for info in infos:
        if info.is_dir():
            continue
        name = info.filename
        if not is_safe_member_name(name):
            out.refusals.append(f"unsafe member path: {name!r}")
            continue
        suffix = posixpath.splitext(name)[1].lower()
        if suffix not in tuple(s.lower() for s in allowed_suffixes):
            out.refusals.append(f"member type not allowed: {name!r}")
        m = ArchiveMember(name, info.compress_size, info.file_size)
        out.members.append(m)
        out.total_uncompressed += info.file_size
        if info.file_size > max_member_bytes:
            out.refusals.append(f"member too large: {name!r} {info.file_size} > {max_member_bytes}")
        if info.compress_size and m.ratio > max_ratio:
            out.refusals.append(f"compression ratio suspicious: {name!r} {m.ratio:.0f}:1 > {max_ratio:.0f}:1")
    if out.total_uncompressed > max_total_bytes:
        out.refusals.append(f"archive too large: {out.total_uncompressed} > {max_total_bytes}")
    return out


def safe_extract_path(root: Path, member_name: str) -> Path:
    """The path a member may be written to. Raises IntakeRefused if the
    resolved path escapes the root (belt and braces after name checks)."""
    if not is_safe_member_name(member_name):
        raise IntakeRefused(f"unsafe member path: {member_name!r}")
    root_r = root.resolve()
    target = (root_r / member_name).resolve()
    try:
        target.relative_to(root_r)
    except ValueError:
        raise IntakeRefused(f"member escapes intake root: {member_name!r}") from None
    return target


def neutralize_cell(value: Optional[str]) -> str:
    """Prefix a would-be formula with an apostrophe so a spreadsheet shows it
    as text. Idempotent for already-safe cells; the stored value is untouched
    — this is for export/display only."""
    if value is None:
        return ""
    s = str(value)
    if s and s.startswith(FORMULA_PREFIXES):
        return "'" + s
    return s


def is_formula_like(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith(FORMULA_PREFIXES)


@dataclass
class CsvLimitReport:
    rows: int = 0
    max_columns_seen: int = 0
    max_field_chars_seen: int = 0
    refusals: List[str] = field(default_factory=list)
    stopped_at_line: Optional[int] = None

    @property
    def accepted(self) -> bool:
        return not self.refusals


def check_csv_limits(text: str, *, max_rows: int = DEFAULT_MAX_ROWS, max_columns: int = DEFAULT_MAX_COLUMNS,
                     max_field_chars: int = DEFAULT_MAX_FIELD_CHARS) -> CsvLimitReport:
    """A bounded pre-scan. It stops at the first violation and says where, so a
    pathological file produces a status instead of a hang or an exception in
    the middle of the parser."""
    report = CsvLimitReport()
    old_limit = csv.field_size_limit()
    csv.field_size_limit(max(max_field_chars * 4, old_limit))
    try:
        reader = csv.reader(io.StringIO(text))
        for n, row in enumerate(reader, start=1):
            report.rows += 1
            report.max_columns_seen = max(report.max_columns_seen, len(row))
            longest = max((len(c) for c in row), default=0)
            report.max_field_chars_seen = max(report.max_field_chars_seen, longest)
            if len(row) > max_columns:
                report.refusals.append(f"line {n}: {len(row)} columns > {max_columns}")
            if longest > max_field_chars:
                report.refusals.append(f"line {n}: field of {longest} chars > {max_field_chars}")
            if report.rows > max_rows:
                report.refusals.append(f"more than {max_rows} rows")
            if report.refusals:
                report.stopped_at_line = n
                break
    except csv.Error as exc:
        report.refusals.append(f"csv error: {exc}")
        report.stopped_at_line = report.rows + 1
    finally:
        csv.field_size_limit(old_limit)
    return report


def decode_text(data: bytes, *, encodings: Iterable[str] = ("utf-8-sig", "utf-8", "cp1252")) -> str:
    """NPPES files are documented as CSV; encoding is not stated. Try UTF-8
    (with or without BOM) first, then Windows-1252 — and refuse rather than
    guess further. Replacement characters are never introduced silently."""
    for enc in encodings:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise IntakeRefused("file is not decodable as UTF-8 or cp1252; refusing to guess")


def redact_for_log(name: str) -> str:
    """Log a file by basename only: an intake path may include an operator's
    username or a delivery folder we should not echo."""
    return os.path.basename(name.replace("\\", "/"))
