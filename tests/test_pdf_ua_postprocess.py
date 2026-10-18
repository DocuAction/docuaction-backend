"""PDF/UA post-processing of the dev Chromium tagged engine's output
(2026-10-03) -- the three veraPDF-identified gaps and, above all, the
fail-safe rules that stop the artifact marking from ever hiding content.

Network-independent and engine-independent: the PDFs are built here with
pikepdf, shaped like Chromium's output (tagged body at marked-content depth 1
inside the margins; header/footer text in the margin bands and a
page-background fill at depth 0), so the rules are tested directly rather
than inferred from one real render.
"""
from __future__ import annotations

import io

import pikepdf
import pytest
from pikepdf import Dictionary, Name, Operator, unparse_content_stream

from app.reports.engine.pdf_engine import (
    _CHROME_BAND_PT, _css_top_center_marking, _html_document_metadata,
    _mark_page_chrome_as_artifacts, _pdf_ua_postprocess, inspect_pdf_accessibility,
    inspect_pdf_tagging, structure_signature)

HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Delivery Processing Report DA-TEST-0001</title>
<meta name="author" content="Alliance Global Tech Inc.">
<meta name="keywords" content="DocuAction; TEFCA ARC">
<meta name="dcterms.created" content="2026-10-03T01:02:03+00:00">
<style>@page { @top-center { content: "DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY"; } }</style>
</head><body><h1>x</h1></body></html>"""

W, H = 612.0, 792.0
PAGE_BOX = (0.0, 0.0, W, H)
#: Kept alive for the module: pikepdf objects must not outlive their Pdf.
_KEEP = pikepdf.Pdf.new()


def _text_block(y: float, text: str = "DocuAction"):
    """A BT…ET block positioned at page-space y (Chromium flips with `cm`;
    here we position directly with Tm in the un-flipped space)."""
    return [([], Operator("BT")), ([Name.F1, 9], Operator("Tf")),
            ([1, 0, 0, 1, 72, y], Operator("Tm")),
            ([pikepdf.String(text)], Operator("Tj")), ([], Operator("ET"))]


def _page_ops(*, header=True, footer=True, body_untagged_text=False,
              background=True, extra_fill=False, image=False, flipped=False):
    ops = []
    if flipped:
        # Chromium's real shape: `q 1 0 0 -1 0 H cm` then y measured downward.
        ops += [([], Operator("q")), ([1, 0, 0, -1, 0, H], Operator("cm"))]
    if background:
        ops += [([0, 0, W, H], Operator("re")), ([], Operator("f"))]
    ops += [([0, 0, W, H], Operator("re")), ([], Operator("W")), ([], Operator("n"))]
    top = H - 20 if not flipped else 20          # 20 pt from the top edge
    bottom = 20 if not flipped else H - 20       # 20 pt from the bottom edge
    if header:
        ops += _text_block(top, "DEVELOPMENT / TEST DATA · DocuAction TEFCA ARC")
    if footer:
        ops += _text_block(bottom, "Page 1 of 3")
    if body_untagged_text:
        ops += _text_block(H / 2 if not flipped else H / 2, "escaped body text")
    # the tagged body: depth 1
    ops += [([Name.P, Dictionary(MCID=0)], Operator("BDC")), ([], Operator("BT")),
            ([Name.F1, 11], Operator("Tf")), ([1, 0, 0, 1, 72, H / 2], Operator("Tm")),
            ([pikepdf.String("Body text")], Operator("Tj")),
            ([], Operator("ET")), ([], Operator("EMC"))]
    if extra_fill:
        ops += [([10, 10, 50, 50], Operator("re")), ([], Operator("f"))]
    if image:
        ops += [([Name.Im0], Operator("Do"))]
    if flipped:
        ops += [([], Operator("Q"))]
    return ops


def _tagged_pdf(ops, *, tags=("Document", "P", "Strong")) -> bytes:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(W, H))
    page.Contents = pdf.make_stream(unparse_content_stream(ops))
    page.Resources = Dictionary(Font=Dictionary(F1=Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)))
    strong = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + tags[2]), K=[]))
    p = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + tags[1]), Pg=page.obj,
                                     K=[0, strong]))
    h1 = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.H1, Pg=page.obj, K=[]))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + tags[0]), K=[h1, p]))
    strong.P = p
    p.P = doc
    h1.P = doc
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, K=[doc]))
    doc.P = root
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _depth0_artifacts(pdf_bytes: bytes):
    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    page = pdf.pages[0]
    page.contents_coalesce()
    kinds = []
    for instr in pikepdf.parse_content_stream(page):
        if str(instr.operator) == "BDC" and str(instr.operands[0]) == "/Artifact":
            kinds.append(str(instr.operands[1].get("/Type")))
    return kinds


def test_metadata_and_marking_come_from_the_documents_own_declarations():
    assert _html_document_metadata(HTML) == {
        "title": "Delivery Processing Report DA-TEST-0001",
        "author": "Alliance Global Tech Inc.", "keywords": "DocuAction; TEFCA ARC",
        "created": "2026-10-03T01:02:03+00:00", "lang": "en"}
    assert _css_top_center_marking(HTML) == "DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY"
    assert _css_top_center_marking("<html><head></head></html>") == ""


@pytest.mark.parametrize("flipped", [False, True])
def test_margin_band_text_and_background_become_artifacts_body_stays_tagged(flipped):
    out, stats = _pdf_ua_postprocess(_tagged_pdf(_page_ops(flipped=flipped)), HTML)
    assert stats["text_blocks_wrapped"] == 2
    assert stats["background_fills_wrapped"] == 1
    assert stats["unclassified_depth0_items"] == 0
    assert stats["depth0_text_blocks_left_unwrapped"] == 0
    assert _depth0_artifacts(out) == ["/Layout", "/Pagination", "/Pagination"]
    tagging = inspect_pdf_tagging(out)
    assert tagging["is_tagged_pdf"] and not tagging["is_trivial_stub"]
    reopened = pikepdf.open(io.BytesIO(out))  # kept alive: objects die with their Pdf
    ops = pikepdf.parse_content_stream(reopened.pages[0])
    assert any(str(i.operator) == "BDC" and str(i.operands[0]) == "/P" for i in ops)


def test_untagged_text_inside_the_content_area_is_never_hidden():
    """Depth-0 text OUTSIDE the margin bands means document text escaped
    tagging. It must stay visible to a validator, not become an artifact."""
    out, stats = _pdf_ua_postprocess(_tagged_pdf(_page_ops(body_untagged_text=True)), HTML)
    assert stats["text_blocks_wrapped"] == 2, "header and footer are still chrome"
    assert stats["depth0_text_blocks_left_unwrapped"] == 1
    assert stats["decorative_glyph_blocks_wrapped"] == 0
    assert _depth0_artifacts(out).count("/Pagination") == 2


HTML_WITH_GLYPH = HTML.replace("<h1>x</h1>", '<h1>x</h1><span class="glyph" aria-hidden="true">*</span>')


def test_only_text_the_document_declared_aria_hidden_becomes_a_decorative_artifact():
    """Chromium paints `aria-hidden="true"` glyphs untagged. They are wrapped
    only when the decoded text is exactly a string the HTML itself declared
    aria-hidden — here `*` — at that position. The same `*` with no such
    declaration in the HTML stays untouched."""
    ops = _page_ops() + _text_block(H / 2 - 40, "*")
    declared, stats = _pdf_ua_postprocess(_tagged_pdf(ops), HTML_WITH_GLYPH)
    assert stats["decorative_glyph_blocks_wrapped"] == 1
    assert stats["depth0_text_blocks_left_unwrapped"] == 0
    assert _depth0_artifacts(declared) == ["/Layout", "/Pagination", "/Pagination", "/Layout"]

    _, stats_undeclared = _pdf_ua_postprocess(_tagged_pdf(ops), HTML)
    assert stats_undeclared["decorative_glyph_blocks_wrapped"] == 0
    assert stats_undeclared["depth0_text_blocks_left_unwrapped"] == 1


def test_a_declared_glyph_string_does_not_license_other_text():
    """`*` is declared decorative; "escaped body text" is not — it must remain
    visible even in the same document."""
    ops = _page_ops(body_untagged_text=True) + _text_block(H / 2 - 40, "*")
    _, stats = _pdf_ua_postprocess(_tagged_pdf(ops), HTML_WITH_GLYPH)
    assert stats["decorative_glyph_blocks_wrapped"] == 1
    assert stats["depth0_text_blocks_left_unwrapped"] == 1


def test_the_band_is_the_requested_margin_plus_tolerance():
    assert _CHROME_BAND_PT == pytest.approx(0.6 * 72 + 12.0)
    # Text just inside the band is chrome; just outside it is not.
    inside = _tagged_pdf(_page_ops(header=False, footer=False) + _text_block(H - _CHROME_BAND_PT + 1))
    outside = _tagged_pdf(_page_ops(header=False, footer=False) + _text_block(H - _CHROME_BAND_PT - 1))
    assert _pdf_ua_postprocess(inside, HTML)[1]["text_blocks_wrapped"] == 1
    assert _pdf_ua_postprocess(outside, HTML)[1]["depth0_text_blocks_left_unwrapped"] == 1


def test_page_background_and_css_decoration_fills_are_layout_artifacts():
    """The full-page background and a CSS-decoration fill (table shading, a
    rule) are both Layout artifacts, counted separately. Pure path painting
    at depth 0 can only be decoration in Chromium's tagged export: images,
    SVG and canvas arrive as tagged Figures, never as bare path operators."""
    out, stats = _pdf_ua_postprocess(_tagged_pdf(_page_ops(extra_fill=True)), HTML)
    assert stats["background_fills_wrapped"] == 1
    assert stats["decoration_paint_runs_wrapped"] == 1
    assert stats["unclassified_depth0_items"] == 0
    assert _depth0_artifacts(out).count("/Layout") == 2


def test_an_image_xobject_at_depth0_is_reported_not_hidden():
    _, stats = _pdf_ua_postprocess(_tagged_pdf(_page_ops(image=True)), HTML)
    assert stats["unclassified_depth0_items"] == 1


def test_xmp_metadata_lang_title_and_display_doc_title_are_written():
    out, _ = _pdf_ua_postprocess(_tagged_pdf(_page_ops()), HTML)
    pdf = pikepdf.open(io.BytesIO(out))
    xmp = pdf.Root.Metadata.read_bytes()
    assert b"pdfuaid:part" in xmp
    assert b"Delivery Processing Report DA-TEST-0001" in xmp
    assert str(pdf.docinfo["/Title"]) == "Delivery Processing Report DA-TEST-0001"
    assert str(pdf.Root.Lang) == "en"
    assert bool(pdf.Root.ViewerPreferences.DisplayDocTitle) is True
    a11y = inspect_pdf_accessibility(out)
    assert a11y["has_xmp_metadata"] and a11y["xmp_pdfua_part"] == 1
    assert a11y["lang"] == "en" and a11y["display_doc_title"]


def test_strong_is_mapped_to_span_and_unknown_tags_are_reported_not_guessed():
    out, stats = _pdf_ua_postprocess(_tagged_pdf(_page_ops()), HTML)
    assert stats["role_map_added"] == ["Strong->Span"]
    assert inspect_pdf_accessibility(out)["role_map"] == {"Strong": "Span"}

    out2, stats2 = _pdf_ua_postprocess(_tagged_pdf(_page_ops(), tags=("Document", "P", "Widget")), HTML)
    assert stats2["role_map_added"] == []
    assert stats2["non_standard_tags_unmapped"] == ["Widget"]
    assert inspect_pdf_accessibility(out2)["non_standard_tags_unmapped"] == ["Widget"]


def test_structure_tree_signature_is_identical_before_and_after():
    before = _tagged_pdf(_page_ops())
    after, _ = _pdf_ua_postprocess(before, HTML)
    sig_before, sig_after = structure_signature(before), structure_signature(after)
    assert sig_before == sig_after
    assert sig_before["element_count"] == 4
    assert sig_before["tag_histogram"] == {"Document": 1, "H1": 1, "P": 1, "Strong": 1}
    assert sig_before["headings"] == [("H1", 0, -1)]


def test_postprocess_is_idempotent():
    once, _ = _pdf_ua_postprocess(_tagged_pdf(_page_ops()), HTML)
    twice, s2 = _pdf_ua_postprocess(once, HTML)
    # Already-wrapped chrome sits at depth 1 on the second pass: nothing to do.
    assert s2["text_blocks_wrapped"] == 0 and s2["background_fills_wrapped"] == 0
    assert _depth0_artifacts(twice) == _depth0_artifacts(once)
    assert structure_signature(twice) == structure_signature(once)


def test_marker_pass_leaves_clip_paths_alone():
    ops = pikepdf.parse_content_stream(_KEEP.make_stream(unparse_content_stream(
        [([0, 0, W, H], Operator("re")), ([], Operator("W")), ([], Operator("n"))])))
    new_ops, stats = _mark_page_chrome_as_artifacts(ops, PAGE_BOX, _CHROME_BAND_PT)
    assert stats == {"text_blocks_wrapped": 0, "background_fills_wrapped": 0,
                     "decoration_paint_runs_wrapped": 0, "decorative_glyph_blocks_wrapped": 0,
                     "unclassified_depth0_items": 0, "depth0_text_blocks_left_unwrapped": 0}
    assert [str(i.operator) for i in new_ops] == ["re", "W", "n"]
