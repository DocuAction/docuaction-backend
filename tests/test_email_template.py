"""Outlook-safe email HTML (Task 3.3).

Outlook Classic renders with the MSWord engine. These pin the constraints that
engine imposes, because nothing in CI can actually render Outlook — the only
defence against a regression is asserting the subset directly.
"""
import re

import pytest

from app.bulletin_intelligence.email_template import (
    build_email_html, build_subject)


class Art:
    def __init__(self, title="T", summary="S", url="https://ex.test/a",
                 outlet="Reuters", relevance_score=0.9, section="Broadband",
                 article_type="news", is_paywalled=False):
        self.title = title
        self.summary = summary
        self.url = url
        self.outlet = outlet
        self.relevance_score = relevance_score
        self.section = section
        self.article_type = article_type
        self.is_paywalled = is_paywalled


def _html(arts=None, **kw):
    kw.setdefault("briefing_date", "August 5, 2026")
    return build_email_html(arts if arts is not None else [Art()], **kw)


# ── The Outlook subset ────────────────────────────────────────────────────────

def test_no_constructs_outlook_classic_cannot_render():
    html = _html([Art(), Art(title="B", section="Spectrum")])
    assert "<style" not in html.lower(), "MSWord engine ignores <style> blocks"
    assert "@media" not in html, "no media query support"
    assert "display:flex" not in html.replace(" ", "")
    assert "display:grid" not in html.replace(" ", "")
    assert "max-width" not in html, "Outlook ignores max-width; width must be fixed"


def test_layout_is_tables_with_a_fixed_pixel_width():
    html = _html()
    assert "<table" in html and "<td" in html
    assert 'width="600"' in html
    assert "width:600px" in html


def test_mso_conditional_wraps_the_layout_and_is_balanced():
    """The conditional is what pins the width in Outlook. An unbalanced pair
    leaks raw markup into every non-Outlook client."""
    html = _html()
    assert "<!--[if mso]>" in html
    assert "<![endif]-->" in html
    assert html.count("<!--[if mso]>") == html.count("<![endif]-->")


def test_every_styled_element_uses_inline_styles():
    html = _html()
    # Any style at all must be an inline attribute, never a rule block.
    assert 'style="' in html
    assert not re.search(r"\{[^}]*:[^}]*\}", html), "looks like a CSS rule block"


# ── Content ───────────────────────────────────────────────────────────────────

def test_content_is_escaped_not_injected():
    """Titles come from scraped feeds. Raw markup in one must not become markup
    in a federal deliverable's email body.

    Two defences run in order and either is sufficient: strip_html removes the
    tag, and whatever survives is escaped. A tag therefore leaves no '<script>'
    AND no '&lt;script&gt;' — it is gone entirely — while a bare ampersand,
    which strip_html has no reason to touch, must still come out escaped."""
    html = _html([Art(title='Ruling <script>alert(1)</script> & "quoted"')])
    assert "<script>" not in html
    assert "&lt;script&gt;" not in html, "stripped, so nothing left to escape"
    assert "&amp;" in html
    assert "&quot;quoted&quot;" in html


def test_escaping_still_holds_if_strip_html_is_unavailable():
    """The second defence alone must be enough — _text_of falls back to the raw
    value when the exporter cannot be imported, and that value reaches the
    email body."""
    import app.bulletin_intelligence.email_template as et

    raw = '<script>alert(1)</script> & more'
    assert "<script>" not in et._esc(raw)
    assert "&lt;script&gt;" in et._esc(raw)


def test_html_in_a_summary_is_stripped_to_text():
    html = _html([Art(summary="The FCC <b>voted</b> today")])
    assert "<b>voted</b>" not in html
    assert "voted" in html


def test_articles_group_under_category_headers_with_counts():
    html = _html([Art(section="Broadband"), Art(section="Broadband"),
                  Art(section="Spectrum")])
    assert "Broadband (2 articles)" in html
    assert "Spectrum (1 article)" in html


def test_a_non_http_url_renders_as_text_not_a_dead_link():
    html = _html([Art(url="")])
    assert "href=\"\"" not in html


def test_paywalled_and_opinion_are_tagged():
    html = _html([Art(is_paywalled=True), Art(title="Op", article_type="opinion")])
    assert "[Subscription Required]" in html
    assert "[Opinion]" in html


def test_empty_briefing_says_so_rather_than_rendering_a_bare_header():
    html = _html([])
    assert "No articles" in html
    assert "0 Articles" in html


def test_relevance_bands():
    html = _html([Art(relevance_score=0.9), Art(title="m", relevance_score=0.5),
                  Art(title="l", relevance_score=0.1)])
    for band in ("High Relevance", "Medium Relevance", "Low Relevance"):
        assert band in html


def test_dict_articles_work_as_well_as_objects():
    html = build_email_html(
        [{"title": "Dict story", "summary": "s", "url": "https://ex.test/d",
          "outlet": "AP", "section": "Broadband"}],
        briefing_date="August 5, 2026")
    assert "Dict story" in html


def test_footer_carries_the_contract_number():
    assert "273FCC26F0061" in _html()


def test_subject_line():
    assert build_subject("August 5, 2026") == "FCC Daily News Summary – August 5, 2026"
