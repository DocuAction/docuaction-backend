"""
File security scanner — multi-layer upload validation (SSP §4.2 Stage 2).

Runs entirely in-process — no ClamAV, no external antivirus API, no added
latency/cost — and provides four defensive layers before an upload is ever
written to disk or handed to a processor:

  1. Magic-byte signature validation  — the real file bytes must match the
     claimed extension, so a renamed executable (evil.exe -> data.csv) is
     rejected instead of trusted on its extension alone.
  2. Dangerous-content scan           — embedded scripts, Office/VBA macro
     markers, PE/ELF executable headers, shell shebangs, encoded PowerShell,
     and embedded PHP are all rejected.
  3. Size & structure validation      — max size, filename hygiene, and a
     "is it actually parseable and sane" check for CSV and JSON.
  4. SHA-256 checksum                  — a content hash the caller stores on the
     file record and writes to the audit trail (integrity + forensics).

USAGE (call BEFORE persisting/processing the upload):

    result = FileScanner().scan(file_bytes, filename, claimed_type)
    if not result.ok:
        # log result.findings to the AUDIT TRAIL only, then reject with a
        # GENERIC message — never leak the specific finding to the user.
        raise HTTPException(422, "File rejected: security validation failed")
    document.checksum_sha256 = result.sha256

`result` also unpacks as the documented ``(is_safe, findings)`` tuple:

    is_safe, findings = FileScanner().scan(...)

IMPORTANT: `findings` is diagnostic detail for the audit log and server logs
ONLY. The API layer must return a generic rejection so an attacker cannot use
the response to learn which check tripped.
"""
from __future__ import annotations

import csv
import io
import json
import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger("docuaction.security.filescan")

# ── Limits ───────────────────────────────────────────────────────────────────
MAX_FILE_SIZE = 50 * 1024 * 1024   # 50 MB (matches the document-upload endpoints)
MAX_FILENAME_LEN = 255
MAX_CSV_COLUMNS = 100
MAX_JSON_DEPTH = 20
_SIGNATURE_SNIFF = 8192            # bytes inspected to decide text-vs-binary
_CSV_SNIFF = 256 * 1024           # bytes decoded to sample CSV structure
_BINARY_RATIO = 0.30              # >30% non-text bytes in the head => binary

# ── Magic-byte signatures, keyed by normalized (dot-stripped, lower) ext ──────
# The file must START WITH one of the listed byte prefixes to be accepted as
# that type. Container formats (docx/xlsx/pptx) are ZIP archives -> "PK..".
_MAGIC = {
    "pdf":  [b"%PDF"],
    "docx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "xlsx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "pptx": [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "zip":  [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    "doc":  [b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"],   # OLE2 (legacy Office)
    "xls":  [b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"],
    "png":  [b"\x89PNG\r\n\x1a\n"],
    "jpg":  [b"\xFF\xD8\xFF"],
    "jpeg": [b"\xFF\xD8\xFF"],
    "gif":  [b"GIF87a", b"GIF89a"],
    "bmp":  [b"BM"],
    "tiff": [b"II*\x00", b"MM\x00*"],
}
# Extensions validated as text (no binary content) rather than by magic bytes.
_TEXT_EXTS = {"csv", "tsv", "txt", "md", "log", "json"}

# ── Dangerous byte signatures at OFFSET 0 (binary headers / shebangs) ─────────
# Binary headers are only trusted when the head region is genuinely binary
# (contains a NUL), so a text file whose first cell happens to read "MZ.." is
# not misclassified as an executable.
_BINARY_HEADERS = [
    (b"MZ", "pe_executable"),      # DOS/PE .exe / .dll
    (b"\x7fELF", "elf_binary"),    # ELF binary
]
_SHEBANGS = [
    (b"#!/bin/bash", "shell_script"),
    (b"#!/bin/sh", "shell_script"),
    (b"#!/usr/bin/env", "shell_script"),
]
# ── Dangerous content substrings (searched case-insensitively, anywhere) ──────
_CONTENT_PATTERNS = [
    (b"<script", "embedded_script"),
    (b"javascript:", "javascript_uri"),
    (b"vbscript:", "vbscript_uri"),
    (b"<?php", "embedded_php"),
    (b"vbaproject", "office_vba_macro"),     # vbaProject.bin entry in a macro doc
    (b"xl/macrosheets", "excel_macro"),
    (b"-encodedcommand", "powershell_encoded"),
    (b"powershell", "powershell"),
]


@dataclass
class ScanResult:
    """Outcome of a scan. `ok` is the pass/fail verdict, `findings` is an
    audit-only list of the specific reasons (empty when ok), `sha256` is the
    content checksum. Unpacks as ``(is_safe, findings)`` for the documented
    contract while still exposing ``.sha256``."""
    ok: bool
    findings: list
    sha256: str

    def __iter__(self):
        yield self.ok
        yield self.findings


class FileScanner:
    """Stateless multi-layer upload validator. Safe to instantiate per call."""

    def scan(self, file_bytes: bytes, filename: str | None,
             claimed_type: str | None, max_size: int = MAX_FILE_SIZE) -> ScanResult:
        """Validate an uploaded file. Returns a ScanResult ``(is_safe, findings)``
        with a SHA-256 checksum. `claimed_type` is the file extension (with or
        without a leading dot, e.g. ``".csv"`` or ``"csv"``)."""
        findings: list[str] = []
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        ext = (claimed_type or "").lstrip(".").lower()

        findings += self._check_filename(filename)
        findings += self._check_size(file_bytes, max_size)
        findings += self._check_signature(file_bytes, ext)
        findings += self._scan_dangerous(file_bytes)
        findings += self._check_structure(file_bytes, ext)

        ok = not findings
        if not ok:
            logger.warning(
                "File scan REJECTED filename=%r type=%s sha256=%s findings=%s",
                (filename or "")[:120], ext or "(none)", sha256, findings,
            )
        return ScanResult(ok=ok, findings=findings, sha256=sha256)

    # ── Layer helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _check_filename(filename: str | None) -> list:
        findings = []
        name = filename or ""
        if len(name) > MAX_FILENAME_LEN:
            findings.append(f"filename_too_long:{len(name)}")
        if "\x00" in name:
            findings.append("null_byte_in_filename")
        return findings

    @staticmethod
    def _check_size(file_bytes: bytes, max_size: int) -> list:
        findings = []
        if len(file_bytes) == 0:
            findings.append("empty_file")
        if len(file_bytes) > max_size:
            findings.append(f"oversize:{len(file_bytes)}>{max_size}")
        return findings

    def _check_signature(self, content: bytes, ext: str) -> list:
        """Reject when the real bytes do not match the claimed extension."""
        if ext in _MAGIC:
            if not any(content.startswith(sig) for sig in _MAGIC[ext]):
                return [f"signature_mismatch:{ext}"]
            return []
        if ext in _TEXT_EXTS:
            if self._looks_binary(content[:_SIGNATURE_SNIFF]):
                return [f"binary_content_in_text:{ext}"]
            return []
        # Unknown/unhandled extension: no signature rule to enforce.
        return []

    @staticmethod
    def _looks_binary(head: bytes) -> bool:
        """True when `head` does not look like plausible text. A NUL byte is a
        hard tell (renamed executables are full of them); otherwise a high ratio
        of control/non-text bytes indicates binary. UTF-8 multibyte sequences
        (>= 0x80) count as text, so unicode documents are not flagged."""
        if not head:
            return False
        if b"\x00" in head:
            return True
        text_bytes = set(range(0x20, 0x100)) | {0x09, 0x0A, 0x0D, 0x0C}
        nontext = sum(1 for b in head if b not in text_bytes)
        return (nontext / len(head)) > _BINARY_RATIO

    @staticmethod
    def _scan_dangerous(content: bytes) -> list:
        findings = []
        head = content[:_SIGNATURE_SNIFF]
        # Executable headers only when the header region is genuinely binary,
        # so an ASCII file starting "MZ,.." is not treated as a PE executable.
        if b"\x00" in head:
            for sig, label in _BINARY_HEADERS:
                if content.startswith(sig):
                    findings.append(f"dangerous_header:{label}")
        for sig, label in _SHEBANGS:
            if content.startswith(sig):
                findings.append(f"dangerous_header:{label}")
        low = content.lower()
        for pat, label in _CONTENT_PATTERNS:
            if pat in low:
                findings.append(f"dangerous_content:{label}")
        return findings

    def _check_structure(self, content: bytes, ext: str) -> list:
        if ext in ("csv", "tsv"):
            return self._check_csv(content, delimiter="\t" if ext == "tsv" else ",")
        if ext == "json":
            return self._check_json(content)
        return []

    @staticmethod
    def _check_csv(content: bytes, delimiter: str = ",") -> list:
        try:
            text = content[:_CSV_SNIFF].decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            for i, row in enumerate(reader):
                if len(row) > MAX_CSV_COLUMNS:
                    return [f"csv_too_many_columns:{len(row)}"]
                if i >= 50:  # sampling the head is enough to catch abuse
                    break
        except Exception as e:  # unparseable => reject
            return [f"csv_unparseable:{type(e).__name__}"]
        return []

    @staticmethod
    def _check_json(content: bytes) -> list:
        try:
            obj = json.loads(content.decode("utf-8", errors="strict"))
        except Exception as e:
            return [f"json_unparseable:{type(e).__name__}"]
        # Iterative depth check (no recursion-limit risk on hostile input).
        stack = [(obj, 1)]
        while stack:
            cur, depth = stack.pop()
            if depth > MAX_JSON_DEPTH:
                return [f"json_too_deep:>{MAX_JSON_DEPTH}"]
            if isinstance(cur, dict):
                stack.extend((v, depth + 1) for v in cur.values())
            elif isinstance(cur, list):
                stack.extend((v, depth + 1) for v in cur)
        return []
