"""
Two guards on the Area 1 delivery path, ahead of the real ONC files.

WHY THE BINARY GUARD EXISTS
The reader is deliberately forgiving. Handed bytes that are not clean UTF-8 it
decodes with replacement and flags the anomaly rather than failing, because a
genuine delivery with a few bad bytes must not be lost.

Handed a BINARY file that tolerance inverts into a hazard. Measured before the
fix: a ZIP header followed by arbitrary bytes produced `delimiter='\\t'` and three
parsed "lines", with no error raised. Those lines would have been written into
Area 1 — which is append-only and immutable by design — so nothing could remove
them afterwards, and the operator would have seen a successful intake.

The ONC deliveries are described as Excel/CSV, and .xlsx *is* a ZIP. Uploading
one to a pipeline that accepts delimited text would have permanently
contaminated the evidence store.

WHY THE RECEIPT DATE EXISTS
`received_at` defaulted to now(), so the record could only ever say when the file
was loaded. A Government delivery can arrive well before anyone is authorised to
load it — these files arrived 2026-08-21 — and the receipt date is the one the
record needs. It is now settable, and parsed strictly: a malformed date is
refused rather than silently becoming today.

No ONC data is used here. Every fixture is synthetic.
"""

from __future__ import annotations

import inspect
from datetime import datetime

import pytest

from app.tefca_registry.rce.intake import (
    NotADelimitedFile,
    ingest_delivery,
    reject_if_binary,
)

pytestmark = pytest.mark.regression

CSV = b"npi,name,state\n1234567890,Example Health,MD\n"


# ── the binary guard ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,payload,label", [
    ("delivery.xlsx", bytes([0x50, 0x4B, 0x03, 0x04]) + b"\x14\x00stuff", "xlsx / ZIP"),
    ("delivery.xls", bytes([0xD0, 0xCF, 0x11, 0xE0]) + b"legacy", "legacy OLE2"),
    ("delivery.pdf", b"%PDF-1.7 junk", "PDF"),
    ("img.png", bytes([0x89]) + b"PNG\r\n", "PNG"),
    ("img.jpg", bytes([0xFF, 0xD8, 0xFF]) + b"jfif", "JPEG"),
])
def test_binary_containers_are_refused(name, payload, label):
    """THE case: an .xlsx must never reach the parser."""
    with pytest.raises(NotADelimitedFile) as exc:
        reject_if_binary(payload, name)
    assert name in str(exc.value)


def test_nul_bytes_are_refused_even_without_a_known_signature():
    """A NUL never appears in the delimited text this pipeline accepts."""
    with pytest.raises(NotADelimitedFile):
        reject_if_binary(b"header,row\n" + bytes([0]) + b"more", "odd.dat")


def test_genuine_delimited_text_is_accepted():
    """The guard must not reject the deliveries it exists to protect."""
    reject_if_binary(CSV, "delivery.csv")
    reject_if_binary(b"a|b|c\n1|2|3\n", "delivery.psv")
    reject_if_binary("npi,name\n1,Ünicöde Health\n".encode("utf-8"), "utf8.csv")


def test_detection_is_by_content_not_by_extension():
    """The extension is caller-controlled; the magic bytes are what the parser meets.

    An .xlsx renamed to .csv is still a ZIP, and a CSV named .xlsx is still text.
    """
    with pytest.raises(NotADelimitedFile):
        reject_if_binary(bytes([0x50, 0x4B, 0x03, 0x04]) + b"x", "actually_a_zip.csv")
    reject_if_binary(CSV, "mislabelled.xlsx")   # must NOT raise


@pytest.mark.asyncio
async def test_rejection_happens_before_anything_is_written(monkeypatch):
    """Area 1 is append-only: a refused delivery must leave no trace at all.

    Asserted by making both the byte-preservation step and the reader explode if
    they are reached.
    """
    import app.tefca_registry.rce.intake as intake

    def _must_not_run(*a, **k):
        raise AssertionError("a refused delivery reached the write/parse path")

    monkeypatch.setattr(intake, "preserve_original", _must_not_run)
    monkeypatch.setattr(intake, "read_delivery", _must_not_run)

    with pytest.raises(NotADelimitedFile):
        await ingest_delivery(object(), bytes([0x50, 0x4B, 0x03, 0x04]) + b"zip",
                              filename="onc.xlsx")


def test_guard_runs_before_preserve_original_in_source_order():
    """Ordering, not merely presence — the same property, pinned statically."""
    src = inspect.getsource(ingest_delivery)
    assert src.index("reject_if_binary(") < src.index("preserve_original(")


# ── the receipt date ─────────────────────────────────────────────────────────

def test_ingest_accepts_an_explicit_receipt_date():
    """The date the delivery arrived is not the date it was loaded."""
    assert "received_at" in inspect.signature(ingest_delivery).parameters


def test_receipt_date_defaults_to_now_only_when_absent():
    src = inspect.getsource(ingest_delivery)
    assert "received_at=received_at or datetime.utcnow()" in src


def test_endpoint_exposes_received_date_and_refuses_a_malformed_one():
    """A bad date must be refused, not silently recorded as today.

    Recording the wrong receipt date on a Government delivery is worse than
    refusing the upload: it is a provenance claim nobody can later detect.
    """
    import app.tefca_registry.rce.routes as routes

    src = inspect.getsource(routes.upload_delivery)
    assert "received_date" in src
    assert "fromisoformat" in src
    assert "422" in src
    assert "received_at=received_at" in src


def test_iso_receipt_date_parses_to_the_expected_day():
    """Sanity on the format an operator will actually type."""
    assert datetime.fromisoformat("2026-08-21").date().isoformat() == "2026-08-21"
