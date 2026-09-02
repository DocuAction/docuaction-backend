"""DEF-018 / IMP-013 — a rejected upload must still appear in Import History.

The tester found that Import History showed the SHA-256 correctly, but that
`empty.csv`, `malicious_script.csv` and `renamed_executable.csv` were missing
from it entirely even after a refresh — so the newest visible row was not the
newest attempt. An operator reading that table would conclude their upload
never happened, and a reviewer would be looking at a highlight reel of only
the files that parsed.

The cause was ordering: the security scan raises 422 BEFORE any history row is
written, while the parse-failure branch further down already writes one. These
tests pin the rejected-upload path so the two branches cannot drift apart
again.
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi import HTTPException

from app.Tefca import routes as tefca_routes


class _FakeDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class _FakeUpload:
    def __init__(self, payload: bytes, filename: str):
        self._payload = payload
        self.filename = filename

    async def read(self):
        return self._payload


class _User:
    email = "tester@docuaction.io"


REJECTED = b"MZ\x90\x00" + b"\x00" * 400          # renamed executable
FILENAME = "renamed_executable.csv"


def _call_upload(monkeypatch, payload=REJECTED, filename=FILENAME):
    """Drive the route with the scanner forced to reject, as it does for a
    renamed executable, and return the fake session it wrote to."""
    db = _FakeDB()

    async def _reject(*_args, **_kwargs):
        # Mirrors the real scanner: generic 422, naming no specific check.
        raise HTTPException(422, "File rejected: potentially malicious content")

    import app.api.routes as api_routes
    monkeypatch.setattr(api_routes, "_scan_upload_or_reject", _reject)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(tefca_routes.upload_entities(
            request=None, file=_FakeUpload(payload, filename),
            db=db, user=_User()))
    assert exc.value.status_code == 422
    return db


def test_a_scanner_rejected_upload_is_recorded_in_import_history(monkeypatch):
    db = _call_upload(monkeypatch)
    assert db.added, (
        "a rejected upload wrote no Import History row, so the attempt is "
        "invisible to the operator who made it")
    assert db.commits >= 1, "the history row was never committed"


def test_the_rejected_history_row_carries_the_filename_and_checksum(monkeypatch):
    db = _call_upload(monkeypatch)
    row = db.added[-1]
    assert row.filename == FILENAME
    assert row.file_hash == hashlib.sha256(REJECTED).hexdigest(), (
        "the checksum recorded for a rejected file must be the checksum of the "
        "bytes that were actually uploaded")
    assert row.status == "failed"
    assert row.imported_count == 0 and row.record_count == 0


def test_the_rejection_reason_does_not_disclose_which_check_tripped(monkeypatch):
    """Import History must not become the oracle that tells an attacker which
    layer caught them; the scanner's own message is deliberately generic."""
    db = _call_upload(monkeypatch)
    reason = (db.added[-1].errors or [{}])[0].get("reason", "")
    assert "malicious" in reason.lower()
    for leak in ("signature", "magic", "pe_executable", "elf", "ratio", "nul"):
        assert leak not in reason.lower(), f"rejection reason leaks {leak!r}"


def test_the_upload_still_fails_closed(monkeypatch):
    """Recording the attempt must not turn a rejection into a success: the 422
    is re-raised, not swallowed."""
    db = _call_upload(monkeypatch)          # pytest.raises inside asserts the 422
    assert all(getattr(r, "status", None) != "success" for r in db.added)
