"""Artifact download security.

An artifact download is the moment Government content leaves the application's
custody. Five things have to hold at that moment, and each was wrong somewhere
before this gate:

  * the response says what it IS and the browser must believe it — attachment,
    correct type, nosniff;
  * the filename cannot escape the header it sits in;
  * the bytes served are the bytes registered, re-hashed at read time;
  * nothing tells the caller where the bytes live;
  * a controlled export is not left in a shared cache after the authorisation
    that produced it has gone.

GOVERNMENT DATA
    Every test uses a synthetic artifact written to a temporary store. Nothing
    reads or writes the delivered population.
"""

from __future__ import annotations

import inspect
import io
import os
import uuid

import pytest

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.reports.routes import (MAX_FILENAME, download_headers, safe_filename)


@pytest.fixture
async def rolled_back_db(db_required):
    engine = create_async_engine(
        _normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    connection = await engine.connect()
    outer = await connection.begin()
    session = AsyncSession(bind=connection,
                           join_transaction_mode="create_savepoint",
                           expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()
        await connection.close()
        await engine.dispose()


# ═══ the filename ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("hostile,forbidden", [
    ('report"; rm -rf /; x="', '"'),
    ("report\r\nSet-Cookie: a=b", "\n"),
    ("report; filename=other.exe", ";"),
    ("../../etc/passwd", "/"),
    ("..\\\\windows\\\\system32", "\\\\"),
])
def test_a_filename_cannot_escape_its_header(hostile, forbidden):
    """A report identifier reaches a header the browser parses.

    A quote ends the filename and whatever follows is read as another
    directive; a newline ends the header entirely. Neither may survive.
    """
    name = safe_filename(hostile, "xlsx")
    assert forbidden.replace("\\\\", "\\") not in name
    for character in '";\r\n/\\':
        assert character not in name, f"{character!r} survived in {name!r}"
    assert name.endswith(".xlsx")


def test_a_filename_cannot_be_hidden_or_unbounded():
    assert not safe_filename(".hidden", "csv").startswith(".")
    assert safe_filename("", "pdf") == "report.pdf"
    assert len(safe_filename("x" * 5000, "xlsx")) <= MAX_FILENAME + len(".xlsx")


# ═══ the headers ════════════════════════════════════════════════════════════

def test_every_download_is_an_attachment():
    """Stored HTML rendered on this origin would execute a report's markup with
    the application's own privileges."""
    assert download_headers("a.html")["Content-Disposition"].startswith(
        "attachment;")


def test_every_download_refuses_content_sniffing():
    """Without nosniff a file whose bytes look like HTML can be rendered
    whatever the Content-Type says, which is the attachment problem again by
    another route."""
    assert download_headers("a.xlsx")["X-Content-Type-Options"] == "nosniff"


def test_a_controlled_export_is_not_cacheable():
    """A copy in a shared or proxy cache outlives the authorisation that
    produced it."""
    headers = download_headers("a.xlsx")
    assert "no-store" in headers["Cache-Control"]
    assert "private" in headers["Cache-Control"]
    assert "public" not in headers["Cache-Control"]


def test_the_cache_decision_is_per_response_not_global():
    """Deliberately a parameter, not a middleware. Turning off caching for the
    whole application to protect one download would be a policy change nobody
    asked for."""
    assert "Cache-Control" not in download_headers("a.txt", sensitive=False)
    source = inspect.getsource(download_headers)
    assert "sensitive" in source


def test_every_download_response_in_the_router_uses_the_helper():
    """One place builds a download response. There were four, and they
    disagreed: one set no disposition, one set no cache policy, and none set
    nosniff."""
    import ast

    import app.reports.routes as routes

    tree = ast.parse(io.open("app/reports/routes.py", encoding="utf-8").read())
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Response"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "headers":
                continue
            # A headers= that is a literal dict is a response building its own
            # security headers by hand.
            if isinstance(keyword.value, ast.Dict):
                offenders.append(node.lineno)
    assert not offenders, (
        f"Response(headers=<literal dict>) at line(s) {offenders} — a download "
        f"is constructing its own headers instead of using download_headers()")


# ═══ integrity, on a real store ═════════════════════════════════════════════

@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.core.storage import artifact_store

    monkeypatch.setenv("ARTIFACT_STORE_ROOT", str(tmp_path))
    monkeypatch.setattr(artifact_store, "_store", None, raising=False)
    return artifact_store.LocalFilesystemArtifactStore(str(tmp_path))


def test_the_store_notices_bytes_that_no_longer_match_their_hash(store):
    """The lower half of the guard: the store's own verify()."""
    from app.core.storage.artifact_store import RetentionPolicy

    stored = store.put("verify-me", b"the registered bytes",
                       content_type="text/plain", retention=RetentionPolicy())
    assert store.verify(stored.locator) is True

    with open(store._resolve(stored.locator), "wb") as handle:
        handle.write(b"different bytes entirely")

    assert store.verify(stored.locator) is False, (
        "the store verified content that no longer matches its hash")


async def test_a_tampered_artifact_is_refused_rather_than_served(rolled_back_db,
                                                                 store):
    """The guard at the layer the download route actually calls.

    A stored hash nobody recomputes is a claim. This registers real bytes,
    alters them underneath the store, and asserts the retrieval REFUSES —
    exercised end to end rather than by reading the source, because an earlier
    version of this test checked the store's own `verify()` and would have
    passed with the registry's check deleted.

    Serving the altered bytes would be worse than a failed download: the
    response carries the registered hash in a header, so it would attest to
    content that is not what was registered.
    """
    from app.reports.data.artifact_registry import (finalize_artifact,
                                                    retrieve_artifact)

    db = rolled_back_db
    report_id = f"INTEGRITY-{uuid.uuid4().hex[:8]}"
    await finalize_artifact(
        db, report_id=report_id, report_type="test", content=b"issued bytes",
        content_type="text/csv", review_cycle_id="cycle",
        generated_by="synthetic", store=store)

    got = await retrieve_artifact(db, report_id, content_type="text/csv",
                                  store=store)
    assert got["content"] == b"issued bytes" and got["verified"] is True

    locator = got["artifact"]["storage_locator"]
    with open(store._resolve(locator), "wb") as handle:
        handle.write(b"tampered bytes")

    with pytest.raises(RuntimeError, match="INTEGRITY FAILURE"):
        await retrieve_artifact(db, report_id, content_type="text/csv",
                                store=store)


def test_the_route_turns_an_integrity_failure_into_a_refusal():
    """And the route must not swallow it into a served response."""
    import app.reports.routes as routes

    handler = inspect.getsource(routes.artifact_download)
    assert "RuntimeError" in handler and "HTTPException(500" in handler


# ═══ the served type ════════════════════════════════════════════════════════

def test_the_served_type_is_the_stored_type():
    """`content_type` is a query parameter. It selects WHICH artifact to fetch;
    echoing it back as the response type would let a caller name the type their
    browser sees, which is a content-type confusion primitive."""
    import ast

    import app.reports.routes as routes

    tree = ast.parse(inspect.getsource(routes.artifact_download))
    assigned = [node for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "served_type"
                        for t in node.targets)]
    assert assigned, "the download no longer resolves a served type at all"
    source = ast.unparse(assigned[0].value)
    assert "artifact" in source, (
        f"the served type comes from {source} rather than the stored artifact")


def test_the_workbook_is_served_as_a_workbook():
    from app.reports.data.artifact_registry import ARTIFACT_SUFFIXES
    from app.reports.engine.xlsx_engine import XLSX_CONTENT_TYPE

    assert ARTIFACT_SUFFIXES[XLSX_CONTENT_TYPE] == "xlsx"
    assert XLSX_CONTENT_TYPE.endswith("spreadsheetml.sheet"), (
        "the workbook MIME type is not the one Excel registers")


# ═══ nothing says where the bytes live ══════════════════════════════════════

def test_no_download_response_names_a_storage_location():
    """Headers included. `X-Artifact-SHA256` identifies the content; a path
    would identify the store."""
    import app.reports.routes as routes

    for handler in (routes.artifact_download, routes._workbook_preview,
                    routes.artifact_history):
        source = inspect.getsource(handler)
        for leak in ("storage_locator", "storage_backend", "local://",
                     "blob.core.windows.net", "os.path", "abspath"):
            assert leak not in source, (
                f"{handler.__name__} references {leak!r}")


def test_the_store_refuses_a_locator_that_leaves_the_root(store):
    """No caller supplies a locator — they come from the registry row — but the
    store refuses an escaping one regardless. A control that depends on nobody
    ever passing the wrong thing is not a control."""
    from app.core.storage.artifact_store import ArtifactNotFound

    for hostile in ("local://../../../../etc/passwd/1/x",
                    "local://a/1/../../../../etc/passwd",
                    "file:///etc/passwd",
                    "/etc/passwd",
                    "local://a/1/.hidden"):
        with pytest.raises(ArtifactNotFound):
            store._resolve(hostile)
