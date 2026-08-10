"""Coverage Assurance survives a restart.

`_last_coverage` is written by a collection cycle and lives only in that
process's memory. After any restart — a deploy, a scale event, or simply a
different worker taking the request — it is empty, and the panel reported "no
collection run recorded" while a complete briefing sat in the database. That
reads as a failed collection rather than as a lost cache, which is the bug: the
data was there the whole time.

The rebuild is deliberately partial. Content counts come from the shipped
articles and are exact. The funnel counts (collected, deduped, rejected) were
measurements of what happened DURING the cycle and cannot be recovered from the
final article set — so they are null, not back-filled. Back-filling them with
the post-briefing count would report zero duplicates removed on every rebuilt
report and overstate the collection's precision.
"""

import pytest

from app.bulletin_intelligence import engine

pytestmark = [pytest.mark.regression, pytest.mark.bulletin]

FUNNEL_FIELDS = ("stories_collected", "after_dedup", "duplicates_removed",
                 "classified", "rejected")


class FakeArticle:
    def __init__(self, source="RSS", outlet="Reuters", paywalled=False,
                 source_type="news"):
        self.source = source
        self.outlet = outlet
        self.is_paywalled = paywalled
        self.source_type = source_type
        self.title = "FCC acts"
        self.url = "https://example.com/a"
        self.summary = "Summary."
        # Fields the classifiers read. Mirrors the real Article dataclass so the
        # fake exercises the same paths rather than a friendlier subset.
        self.section = "Spectrum Policy"
        self.topic = "spectrum_policy"
        self.relevance_score = 0.8


# A unique agency id, NOT "fcc".
#
# coverage_from_latest_briefing() calls get_latest_briefing(agency_id), which
# returns the NEWEST briefing for that agency across the whole module-global
# store. Registering this fixture's briefing under "fcc" meant any other test
# that added an fcc briefing could win the lookup, and then the monkeypatched
# rehydration below did not apply to it. That passed in isolation and failed in
# the full suite — an order-dependent test, which is worse than no test.
AGENCY = "fcc_coverage_rebuild_test"


@pytest.fixture
def briefing(monkeypatch):
    b = engine.Briefing(
        briefing_id=f"{AGENCY}_20260810_000000",
        agency_id=AGENCY,
        briefing_date="August 10, 2026",
        status="delivered",
        html_content="<h1>x</h1>",
        article_count=3,
        generated_at="2026-08-10T00:06:38+00:00",
    )
    engine._briefings[b.briefing_id] = b

    from app.bulletin_intelligence import bulletin_download_routes

    arts = [FakeArticle(), FakeArticle(outlet="WSJ", paywalled=True),
            FakeArticle(source="Social", source_type="social")]
    monkeypatch.setattr(bulletin_download_routes, "_briefing_articles",
                        lambda bid: ({"briefing_date": b.briefing_date}, None, arts))
    yield b
    engine._briefings.pop(b.briefing_id, None)


def test_a_briefing_produces_coverage_even_with_a_cold_cache(briefing):
    """The bug: a complete briefing existed and the panel said nothing had run."""
    engine._last_coverage.pop(AGENCY, None)
    report = engine.coverage_from_latest_briefing(AGENCY)
    assert report is not None
    assert report["derived_from"] == "latest_briefing"
    assert report["briefing_id"] == briefing.briefing_id


def test_content_counts_are_exact(briefing):
    report = engine.coverage_from_latest_briefing(AGENCY)
    # Social posts are excluded from the briefing counts, exactly as the
    # renderer excludes them, so the report matches what shipped.
    assert report["in_briefing"] == 2
    assert report["social_collected"] == 1
    assert report["subscription_stories"] == 1
    assert report["source_count"] >= 1


def test_funnel_counts_are_null_not_backfilled(briefing):
    """Back-filling these with the post-briefing count would report zero
    duplicates removed every time and overstate the collection's precision."""
    report = engine.coverage_from_latest_briefing(AGENCY)
    for field in FUNNEL_FIELDS:
        assert report[field] is None, f"{field} was back-filled with a guess"
    assert set(FUNNEL_FIELDS) <= set(report["unavailable_fields"])


def test_the_rebuild_declares_its_provenance(briefing):
    """A consumer must be able to tell a rebuilt report from a measured one."""
    report = engine.coverage_from_latest_briefing(AGENCY)
    assert report["derived_from"] == "latest_briefing"
    assert "Rebuilt from the stored briefing" in report["note"]


def test_no_briefing_means_no_report():
    """The 404 must still be reachable — it now means what it says."""
    for key in [k for k in engine._briefings if
                engine._briefings[k].agency_id == "nonexistent"]:
        engine._briefings.pop(key, None)
    assert engine.coverage_from_latest_briefing("nonexistent") is None


def test_a_rehydration_failure_still_yields_a_report(briefing, monkeypatch):
    """A rebuild must never raise into the route. Losing the article detail is
    survivable; a 500 on a dashboard panel is not."""
    from app.bulletin_intelligence import bulletin_download_routes

    def _boom(_bid):
        raise RuntimeError("archive unavailable")

    monkeypatch.setattr(bulletin_download_routes, "_briefing_articles", _boom)
    report = engine.coverage_from_latest_briefing(AGENCY)
    assert report is not None
    assert report["in_briefing"] == 0


def test_an_article_missing_classifier_fields_degrades_to_other(briefing, monkeypatch):
    """Archived articles do not always carry every field the classifiers read.
    A dashboard panel must degrade, never 500."""
    from app.bulletin_intelligence import bulletin_download_routes

    class Bare:
        source = "RSS"

    monkeypatch.setattr(bulletin_download_routes, "_briefing_articles",
                        lambda bid: ({"briefing_date": "August 10, 2026"}, None, [Bare()]))
    report = engine.coverage_from_latest_briefing(AGENCY)
    assert report is not None
    assert report["in_briefing"] == 1


def test_the_live_report_still_wins_when_present():
    """A rebuild is the fallback, never a replacement — the measured report has
    the funnel counts and must not be shadowed by a partial one."""
    import inspect

    from app.bulletin_intelligence import routes

    src = inspect.getsource(routes.coverage_report)
    assert src.index("_last_coverage.get(agency_id)") < \
        src.index("coverage_from_latest_briefing(agency_id)")


def test_the_404_now_means_no_briefing_not_no_cycle():
    import inspect

    from app.bulletin_intelligence import routes

    src = inspect.getsource(routes.coverage_report)
    assert "No briefing exists yet" in src
    assert "No coverage report yet" not in src
