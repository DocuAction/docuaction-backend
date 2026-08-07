"""List endpoints must not carry briefing payload blobs.

A Briefing holds the rendered HTML and a base64 Word document. Both are served
by their own briefing_id-keyed endpoints. Left in a list response they dominate
it: /history/fcc on prod returned 11.8 MB across 184 briefings, of which 11.7 MB
was docx_b64, and took 35-40 seconds — which the browser surfaces to the user as
"Failed to fetch" rather than as a slow request.

html_content was already stripped. docx_b64 was not, and the docstring said
"without HTML payload", which read as though the job was done.
"""
import pytest

from app.bulletin_intelligence import engine


@pytest.fixture
def briefings():
    made = []
    for i in range(3):
        b = engine.Briefing(
            briefing_id=f"fcc_2026080{i}_120000",
            agency_id="fcc",
            briefing_date=f"August {i + 1}, 2026",
            status="delivered",
            html_content="<html>" + ("x" * 50_000) + "</html>",
            article_count=10,
            generated_at=f"2026-08-0{i}T12:00:00",
            docx_b64="QUJD" * 20_000,
        )
        engine._briefings[b.briefing_id] = b
        made.append(b)
    yield made
    for b in made:
        engine._briefings.pop(b.briefing_id, None)


def test_history_carries_neither_blob(briefings):
    history = engine.get_briefing_history("fcc")
    assert len(history) == 3
    for row in history:
        assert "html_content" not in row
        assert "docx_b64" not in row


def test_history_stays_small(briefings):
    """The regression is a size regression, so the test is a size test. Three
    briefings of blobs are ~400 KB; the metadata alone is well under 10 KB."""
    import json

    payload = json.dumps(engine.get_briefing_history("fcc"))
    assert len(payload) < 10_000, f"history payload ballooned to {len(payload)} bytes"


def test_history_keeps_the_fields_the_list_view_renders(briefings):
    row = engine.get_briefing_history("fcc")[0]
    for field_name in ("briefing_id", "briefing_date", "status", "article_count",
                       "generated_at", "delivered_at"):
        assert field_name in row


@pytest.mark.parametrize("getter", ["get_latest_briefing", "get_today_briefing"])
def test_single_briefing_getters_strip_both_blobs(briefings, getter):
    result = getattr(engine, getter)("fcc")
    if result is None:
        pytest.skip("no briefing matches this getter's window")
    assert "html_content" not in result
    assert "docx_b64" not in result


def test_a_new_blob_field_is_stripped_everywhere_at_once():
    """The point of the shared field list: the next large field added to Briefing
    should not have to be remembered at each call site."""
    assert set(engine._BRIEFING_BLOB_FIELDS) == {"html_content", "docx_b64"}
    summarized = engine._summarize_briefing(
        {"briefing_id": "x", "html_content": "h", "docx_b64": "d", "keep": 1})
    assert summarized == {"briefing_id": "x", "keep": 1}


def test_the_download_path_still_has_the_docx(briefings):
    """Stripping the blob from list views must not remove it from the store the
    download endpoint reads."""
    stored = engine._briefings[briefings[0].briefing_id]
    assert stored.docx_b64, "the docx must remain on the Briefing itself"
