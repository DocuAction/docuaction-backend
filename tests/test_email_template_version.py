"""EMAIL_TEMPLATE_VERSION — which body a send carries.

classic is the short summary + "VIEW FULL BRIEFING" button that has been going
out. modern is the full Outlook-safe article list. The flag exists so the switch
can be made on dev, checked in a real client, and promoted by changing an App
Service setting — not by shipping code.

The cases that matter are the ones where the flag says modern and modern cannot
honestly be produced: a briefing whose stories will not rehydrate would render
as an article list missing articles, which reads as complete. Those must fall
back to classic and say why.
"""
import asyncio

import pytest

from app.bulletin_intelligence import engine


# ── Flag parsing ──────────────────────────────────────────────────────────────

def test_the_default_is_classic(monkeypatch):
    monkeypatch.delenv("EMAIL_TEMPLATE_VERSION", raising=False)
    assert engine.email_template_version() == "classic"


@pytest.mark.parametrize("value,expected", [
    ("classic", "classic"),
    ("modern", "modern"),
    ("MODERN", "modern"),
    ("  modern  ", "modern"),
])
def test_recognised_values_are_accepted_case_and_space_insensitively(
        monkeypatch, value, expected):
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", value)
    assert engine.email_template_version() == expected


@pytest.mark.parametrize("value", ["", "moderrn", "new", "v2", "true"])
def test_an_unrecognised_value_is_classic_not_an_error(monkeypatch, value):
    """A typo in an env var must not decide what a federal deliverable looks
    like, and must not take the send down either."""
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", value)
    assert engine.email_template_version() == "classic"


def test_the_flag_is_read_per_call_not_captured_at_import(monkeypatch):
    """Promotion is meant to be an App Service setting change plus a restart. If
    the value were bound at import, a test could not flip it and neither could
    a running process re-read it."""
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "modern")
    assert engine.email_template_version() == "modern"
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "classic")
    assert engine.email_template_version() == "classic"


def test_an_explicit_override_beats_the_environment(monkeypatch):
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "classic")
    assert engine.email_template_version("modern") == "modern"


# ── Body selection in the send path ───────────────────────────────────────────

@pytest.fixture
def briefing(monkeypatch):
    """A live briefing with no SendGrid key, so sends stop at dry_run and the
    returned metadata is what gets asserted."""
    monkeypatch.setattr(engine, "SENDGRID_KEY", "")
    b = engine.Briefing(
        briefing_id="fcc_20260806_test",
        agency_id="fcc",
        briefing_date="August 6, 2026",
        status="delivered",
        html_content="<h1>x</h1>",
        article_count=3,
        topic_counts={"spectrum": 3},
    )
    engine._briefings[b.briefing_id] = b
    yield b
    engine._briefings.pop(b.briefing_id, None)


def _send(briefing_id, **kw):
    return asyncio.run(engine.send_briefing_email(briefing_id, **kw))


def test_classic_is_what_goes_out_by_default(monkeypatch, briefing):
    monkeypatch.delenv("EMAIL_TEMPLATE_VERSION", raising=False)
    result = _send(briefing.briefing_id, recipients=["imran@agtbi.com"])
    assert result["status"] == "dry_run"
    assert result["template_version"] == "classic"
    assert "template_fallback_reason" not in result


@pytest.fixture
def rehydrates(monkeypatch, briefing):
    """Stub the archive lookup so the REAL builder and template run. Patching
    _modern_email_for_briefing instead would skip the code under test."""
    from app.bulletin_intelligence import bulletin_download_routes

    arts = [
        {"title": f"Story {i}", "url": f"https://example.com/{i}",
         "summary": "A summary.", "outlet": "Example", "section": "Spectrum",
         "relevance_score": 0.8}
        for i in range(briefing.article_count)
    ]
    monkeypatch.setattr(
        bulletin_download_routes, "_briefing_articles",
        lambda bid: ({"briefing_date": briefing.briefing_date},
                     engine.get_agency("fcc"), arts))
    return arts


def test_modern_is_used_when_the_articles_rehydrate(monkeypatch, briefing,
                                                    rehydrates):
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "modern")
    result = _send(briefing.briefing_id, recipients=["imran@agtbi.com"])
    assert result["template_version"] == "modern"
    assert result["subject"] == "FCC Daily News Summary – August 6, 2026"


def test_the_modern_body_carries_every_story(monkeypatch, briefing, rehydrates):
    """The point of modern is that the briefing reads in the mail client. A body
    that renders but drops stories would pass a version check and still be wrong."""
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "modern")
    html, _subject, reason = engine._modern_email_for_briefing(
        briefing.briefing_id, briefing, engine.get_agency("fcc"))
    assert reason == ""
    for article in rehydrates:
        assert article["title"] in html
        assert article["url"] in html


def test_a_truncated_rehydration_falls_back_to_classic_with_a_reason(
        monkeypatch, briefing, rehydrates):
    """A modern body built from 1 of 3 stories looks like a complete briefing
    that simply had one story that day. Classic links to the real thing."""
    from app.bulletin_intelligence import bulletin_download_routes

    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "modern")
    monkeypatch.setattr(
        bulletin_download_routes, "_briefing_articles",
        lambda bid: ({"briefing_date": briefing.briefing_date},
                     engine.get_agency("fcc"), rehydrates[:1]))
    result = _send(briefing.briefing_id, recipients=["imran@agtbi.com"])
    assert result["template_version"] == "classic"
    assert result["template_requested"] == "modern"
    assert "rehydrated 1 of 3" in result["template_fallback_reason"]


def test_a_failing_modern_template_still_sends_the_briefing(monkeypatch, briefing,
                                                            rehydrates):
    """Losing the nicer body is not a reason to miss a morning bulletin. The
    render is wrapped inside the builder, so a template that blows up comes back
    as a fallback reason rather than as an exception."""
    def _explode(*_a, **_kw):
        raise RuntimeError("template exploded")

    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "modern")
    monkeypatch.setattr(engine, "render_modern_email", _explode)
    result = _send(briefing.briefing_id, recipients=["imran@agtbi.com"])
    assert result["status"] == "dry_run"
    assert result["template_version"] == "classic"
    assert result["template_requested"] == "modern"


def test_the_builder_reports_rather_than_raises_on_a_missing_briefing():
    """_modern_email_for_briefing is the thing that must never raise — that is
    what makes the fallback in the send path reliable."""
    html, _subject, reason = engine._modern_email_for_briefing(
        "does_not_exist", engine.Briefing(
            briefing_id="does_not_exist", agency_id="fcc", briefing_date="x",
            status="delivered", html_content="", article_count=0),
        engine.get_agency("fcc"))
    assert html is None
    assert reason


def test_a_per_send_override_does_not_need_an_env_change(monkeypatch, briefing):
    """The trial path: POST /send?template_version=modern, without touching the
    setting every other send reads."""
    monkeypatch.setenv("EMAIL_TEMPLATE_VERSION", "classic")
    monkeypatch.setattr(
        engine, "_modern_email_for_briefing",
        lambda bid, b, ag: ("<html>modern</html>", "subject", ""))
    result = _send(briefing.briefing_id, recipients=["imran@agtbi.com"],
                   template_version="modern")
    assert result["template_version"] == "modern"


# ── Preview and send agree ────────────────────────────────────────────────────

def test_the_preview_route_renders_through_the_send_path_builder():
    """GET /email-preview exists to check the body before it ships. If it built
    its own HTML, it could pass while the send sent something else."""
    import inspect
    from app.bulletin_intelligence import routes

    src = inspect.getsource(routes.email_preview)
    assert "render_modern_email" in src
    assert "build_email_html" not in src
