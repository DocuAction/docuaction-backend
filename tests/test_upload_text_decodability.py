"""DEF-017 / IMP-005 — a text upload must actually be decodable text.

The tester reported that a renamed executable was accepted. The BACKEND was
never the accepting party: FileScanner rejects a renamed PE or ELF on two
independent layers, and the tester's own DEF-018 evidence corroborates it —
`renamed_executable.csv` and `malicious_script.csv` were rejected (their
absence from Import History is the separate defect reported there).

Reproducing the control with fixtures did surface a real gap, though.
`_looks_binary()` deliberately counts every byte >= 0x80 as text so that UTF-8
documents are not flagged. A blob with no NUL bytes and plenty of high-bit
noise therefore satisfied it, carried no recognised signature, and was
ACCEPTED. The acceptance property has to be "this is valid permitted text",
not "no signature we know about was found".

The two positive cases matter as much as the negative ones: the fix decodes
only the sniff window, so a legitimate unicode file - especially one long
enough to be cut mid-character at the boundary - must still be accepted.
"""
from __future__ import annotations

import pytest

from app.services.file_scanner import FileScanner

PE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 600
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 300
HEADER_41 = ",".join(f"col{i}" for i in range(41))
VALID_CSV = (HEADER_41 + "\n" + ",".join(["v"] * 41) + "\n").encode()
UNICODE_CSV = "name,city\nÜnïcödé Ángel,München\n— em dash, “curly”, ½ ¾ §\n".encode("utf-8")
# Long enough to be truncated at the 8192-byte sniff window, so the cut lands
# inside a multi-byte character. This is the case a naive decode would reject.
LONG_UNICODE_CSV = ("a,b\n" + "Ünïcödé Ángel München,x\n" * 900).encode("utf-8")


@pytest.mark.parametrize("label,payload,filename", [
    ("renamed PE executable", PE, "evil.csv"),
    ("renamed ELF binary", ELF, "evil.csv"),
    ("double extension", PE, "evil.exe.csv"),
    ("null bytes in content", b"a,b\n1,\x00\n", "nul.csv"),
    ("undecodable high-bit binary", b"\xff\xfe\xfa\xfb" * 400, "bad.csv"),
    ("empty file", b"", "empty.csv"),
], ids=["pe", "elf", "double-ext", "nul-bytes", "undecodable", "empty"])
def test_non_text_uploads_are_rejected(label, payload, filename):
    result = FileScanner().scan(payload, filename, "csv")
    assert not result.ok, f"{label} was ACCEPTED as CSV"


def test_undecodable_content_is_rejected_for_that_reason():
    """Named explicitly: this is the gap the QA gate found, and a future change
    that reintroduces it should fail here with a diagnosis, not a bare False."""
    result = FileScanner().scan(b"\xff\xfe\xfa\xfb" * 400, "bad.csv", "csv")
    assert "undecodable_text:csv" in result.findings


def test_oversize_is_rejected():
    assert not FileScanner().scan(b"a,b\n" * 10, "big.csv", "csv", max_size=8).ok


@pytest.mark.parametrize("label,payload", [
    ("valid 41-column ONC-shaped CSV", VALID_CSV),
    ("legitimate unicode CSV", UNICODE_CSV),
    ("unicode CSV longer than the sniff window", LONG_UNICODE_CSV),
], ids=["valid-41-col", "unicode", "unicode-past-sniff-window"])
def test_valid_text_uploads_are_still_accepted(label, payload):
    """The control must not become a blunt instrument: rejecting real unicode
    would push users to strip accents out of Government entity names."""
    result = FileScanner().scan(payload, "ok.csv", "csv")
    assert result.ok, f"{label} was REJECTED: {result.findings}"
