"""SUN-13 (DEV 2026-10-06): the package's PDF was not the registered PDF, so its manifest hash never matched the file
a recipient downloaded separately; and a DOCX built twice from the same stored report differed in bytes.

Fix: `get_package` uses the REGISTERED artifact bytes (pdf, html, csv) when they exist (QA108-20260927-013), and the DOCX
container is normalised so identical content gives identical bytes.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.reports.engine.docx_engine import _normalise_zip

from support_delivery_api import headers_for, run, seed_delivery  # noqa: F401
from test_report_storage_durable import _Committed, _generate  # noqa: F401


def _zip(entries, *, stamp):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


class TestDocxContainerIsReproducible:
    ENTRIES = [("[Content_Types].xml", b"<a/>"), ("word/document.xml", b"<doc>hello</doc>"), ("docProps/core.xml", b"<c/>")]

    def test_same_content_with_different_entry_times_gives_identical_bytes(self):
        a = _zip(self.ENTRIES, stamp=(2026, 10, 6, 20, 55, 10))
        b = _zip(self.ENTRIES, stamp=(2026, 10, 6, 20, 56, 44))
        assert a != b, "precondition: the raw containers differ only by entry time"
        assert _normalise_zip(a) == _normalise_zip(b)

    def test_content_and_order_are_preserved(self):
        out = zipfile.ZipFile(io.BytesIO(_normalise_zip(_zip(self.ENTRIES, stamp=(2026, 10, 6, 20, 55, 10)))))
        assert [i.filename for i in out.infolist()] == [n for n, _ in self.ENTRIES]
        assert {n: out.read(n) for n, _ in self.ENTRIES} == dict(self.ENTRIES)
        assert out.testzip() is None

    def test_a_real_python_docx_document_round_trips(self):
        docx = pytest.importorskip("docx")
        d = docx.Document()
        d.add_paragraph("synthetic")
        buf = io.BytesIO()
        d.save(buf)
        again = docx.Document(io.BytesIO(_normalise_zip(buf.getvalue())))
        assert [p.text for p in again.paragraphs] == ["synthetic"]


pytestmark_db = pytest.mark.usefixtures("db_required")


@pytest.fixture
def committed(tmp_path, monkeypatch, db_required):
    from app.core.storage import artifact_store

    monkeypatch.setenv("REPORT_ARTIFACT_ROOT", str(tmp_path / "pkg-artifacts"))
    monkeypatch.delenv("REPORT_ARTIFACT_BACKEND", raising=False)
    artifact_store.reset_artifact_store()
    c = _Committed()
    run(c.seed_and_generate())
    try:
        yield c
    finally:
        run(c.cleanup())
        artifact_store.reset_artifact_store()


def _members(resp):
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    return z, {n: z.read(n) for n in z.namelist()}


@pytest.mark.usefixtures("db_required")
def test_package_members_are_the_registered_bytes_and_match_the_separate_downloads(client, committed, monkeypatch):
    import app.reports.routes as routes

    rid = committed.result["report_id"]
    headers = headers_for("reviewer")
    sentinel_pdf = b"%PDF-1.7 registered-sentinel\n%%EOF"
    real = routes._registered_bytes

    async def fake(db, report_id, content_type):
        if content_type == "application/pdf":
            return {"content": sentinel_pdf, "artifact": {"artifact_version": 1,
                                                          "rendered_sha256": hashlib.sha256(sentinel_pdf).hexdigest()}}
        return await real(db, report_id, content_type)

    monkeypatch.setattr(routes, "_registered_bytes", fake)
    pkg = client.get(f"/api/reports/{rid}/package", headers=headers)
    assert pkg.status_code == 200, pkg.text[:200]
    _z, members = _members(pkg)
    manifest = json.loads(members["manifest.json"])
    files = manifest["files"]
    by_ext = {n.rsplit(".", 1)[-1]: n for n in members if n not in ("README.txt", "manifest.json")}

    # the PDF in the package IS the registered PDF, not a re-render
    assert members[by_ext["pdf"]] == sentinel_pdf
    # and every member still matches its own manifest entry
    for name, data in members.items():
        if name in ("README.txt", "manifest.json"):
            continue
        rec = files[name] if isinstance(files, dict) else next(x for x in files if x.get("name") == name)
        assert (rec.get("sha256") if isinstance(rec, dict) else rec) == hashlib.sha256(data).hexdigest(), name

    # the separately downloaded HTML and CSV equal the package members byte for byte
    html = client.get(f"/api/reports/{rid}/html", headers=headers)
    csv = client.get(f"/api/reports/{rid}/csv", headers=headers)
    assert html.status_code == 200 and csv.status_code == 200
    assert members[by_ext["html"]] == html.content
    assert members[by_ext["csv"]] == csv.content
