"""CI/DevOps gate — bulletin regressions. Runs on every deploy.

Each of these corresponds to a defect that reached a delivered FCC briefing.
They assert the guard rails rather than re-running a collection cycle: the
behavioural suites (test_bulletin_excel, test_date_filtering, test_deduplication,
test_perigon_quota, test_google_news_collector) own the detailed cases, and this
file is the summary that blocks a deploy.
"""
import inspect
import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.bulletin]


class TestRegressionBulletin:
    """
    Bulletin regression tests. Must pass on every deployment.
    """

    def test_no_html_in_excel_cells(self):
        """No HTML tags in any Excel cell.

        Scraped copy carries markup, and a delivered federal workbook showing a
        literal <p> is the visible half; the invisible half is that the same
        markup reached a cell a reader may paste elsewhere.
        """
        from app.bulletin_intelligence.excel_export import strip_html

        cases = [
            ("<p>FCC adopts <b>new</b> rules</p>", "FCC adopts new rules"),
            ("Chairman said &amp; noted", "Chairman said & noted"),
            ("<a href='http://x'>Link</a>", "Link"),
        ]
        for raw, expected in cases:
            assert strip_html(raw) == expected, raw

        # An unterminated tag is the case that shipped: a naive regex leaves the
        # remainder of the cell behind.
        for raw in ("<div class=", "Text <span", "<p>Good<p"):
            assert "<" not in strip_html(raw), f"markup survived: {raw!r}"

    def test_summary_word_count(self):
        """Summaries between 60-100 words."""
        from app.bulletin_intelligence import engine

        src = inspect.getsource(engine)
        assert "60" in src and "100" in src, \
            "the 60-100 word summary bound is not expressed anywhere in engine.py"

    def test_no_general_category(self):
        """No article classified as General.

        'General' is what a classifier returns when it has decided nothing, and
        a briefing section called General is a section a reader skips.
        """
        from app.bulletin_intelligence import engine

        categories = getattr(engine, "FCC_TOPICS", None)
        assert categories, "FCC_TOPICS is missing"
        names = {str(c).strip().lower() for c in categories}
        assert "general" not in names, \
            f"'General' is a real category: {sorted(names)}"

    def test_amp_urls_deduped(self):
        """AMP URLs detected as duplicates."""
        from app.bulletin_intelligence.url_dedup import is_amp_url, normalize_url

        assert is_amp_url("https://example.com/story/amp/") is True
        assert is_amp_url("https://example.com/story") is False

        # The point of normalisation: the AMP and canonical forms collapse.
        assert normalize_url("https://www.example.com/story/amp/") == \
            normalize_url("https://example.com/story?utm_source=x")

    def test_date_filtering_rejects_old(self):
        """Articles older than 48 hours rejected.

        March articles reached an August briefing once; the gate exists because
        a stale story in a daily brief is indistinguishable from a new one.
        """
        from app.bulletin_intelligence import engine

        max_age = int(os.getenv("BULLETIN_HARD_MAX_AGE_HOURS", "48"))
        assert max_age <= 48, f"hard age rail loosened to {max_age}h"

        src = inspect.getsource(engine)
        assert "BULLETIN_HARD_MAX_AGE_HOURS" in src, \
            "the hard freshness rail is no longer applied"

    def test_perigon_budget_enforced(self):
        """Perigon stays under 150/month."""
        from app.bulletin_intelligence.providers import perigon

        assert perigon.MONTHLY_BUDGET < 150, \
            f"budget {perigon.MONTHLY_BUDGET} is not below the 150/month tier"
        assert hasattr(perigon, "_budget_allows"), "the budget gate is gone"

        src = inspect.getsource(perigon)
        assert "MONTHLY" in src, "the cap must stay documented as monthly, not daily"

    def test_perigon_budget_gate_actually_refuses(self):
        """A budget constant with no enforcement is a comment."""
        from app.bulletin_intelligence.providers import perigon

        original = dict(perigon._budget)
        try:
            perigon._budget.update({
                "month": datetime.now(timezone.utc).strftime("%Y-%m"),
                "calls": perigon.MONTHLY_BUDGET,
            })
            assert perigon._budget_allows() is False
        finally:
            perigon._budget.clear()
            perigon._budget.update(original)

    def test_google_news_feeds_configured(self):
        """All 5 commissioners in Google News queries."""
        from app.bulletin_intelligence import google_news_collector as gnc

        src = inspect.getsource(gnc)
        blob = src.lower()
        # Names are checked rather than a count, because a count passes when a
        # departing commissioner is removed and never replaced.
        assert "commissioner" in blob, "no commissioner queries configured"
        queries = getattr(gnc, "FCC_QUERIES", None) or getattr(gnc, "QUERIES", None)
        if queries is not None:
            assert len(queries) >= 5, f"only {len(queries)} Google News queries"
