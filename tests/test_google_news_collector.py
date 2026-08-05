"""Google News collector + bulletin QA layer tests.

The FCC verifies our bulletin against Google News, so the QA comparison has to be
right in both directions: it must find stories we genuinely missed, and it must
not cry wolf on stories we already have under a different headline.
"""
import pytest

from app.bulletin_intelligence import google_news_collector as gnc
from app.bulletin_intelligence.google_news_collector import GoogleNewsCollector

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>FCC - Google News</title>
    <item>
      <title>FCC votes on spectrum auction rules - Reuters</title>
      <link>https://example.com/spectrum-auction</link>
      <pubDate>Mon, 04 Aug 2026 12:00:00 GMT</pubDate>
      <description>The commission voted today.</description>
      <source url="https://reuters.com">Reuters</source>
    </item>
    <item>
      <title>Brendan Carr announces broadband initiative - Ars Technica</title>
      <link>https://example.com/broadband</link>
      <pubDate>Mon, 04 Aug 2026 09:30:00 GMT</pubDate>
      <description>New funding.</description>
      <source url="https://arstechnica.com">Ars Technica</source>
    </item>
  </channel>
</rss>"""


def _article(title, url="https://x.test/a"):
    """Minimal stand-in for an engine Article (attribute access, not dict)."""
    return type("A", (), {"title": title, "url": url, "outlet": "", "published_at": ""})()


def test_rss_parse():
    items = gnc.parse_rss(SAMPLE_RSS)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "FCC votes on spectrum auction rules - Reuters"
    assert first["url"] == "https://example.com/spectrum-auction"
    assert first["source"] == "Reuters"
    assert first["published"] == "Mon, 04 Aug 2026 12:00:00 GMT"

    # Malformed XML must degrade to [], never raise — one bad feed response
    # cannot be allowed to take down a collection cycle.
    assert gnc.parse_rss("<rss><channel><item>") == []
    assert gnc.parse_rss("") == []


def test_deduplicate_by_url():
    items = [
        {"title": "FCC votes on spectrum - Reuters", "url": "https://a.test/1"},
        {"title": "Different story", "url": "https://a.test/1"},          # dup URL
        {"title": "FCC votes on spectrum - AP", "url": "https://a.test/2"},  # dup story
        {"title": "Genuinely other news", "url": "https://a.test/3"},
    ]
    out = gnc.deduplicate_by_url(items)
    urls = [i["url"] for i in out]
    assert "https://a.test/1" in urls
    assert "https://a.test/3" in urls
    assert len(out) == 2, "same URL and same normalized headline both collapse"


@pytest.mark.asyncio
async def test_compare_finds_missing():
    collector = GoogleNewsCollector()
    google = [
        {"title": "FCC votes on spectrum auction rules", "url": "https://g/1"},
        {"title": "Robocall enforcement action announced", "url": "https://g/2"},
    ]
    bulletin = [_article("FCC votes on spectrum auction rules")]

    result = await collector.compare_with_bulletin(google, bulletin)
    assert len(result.matched) == 1
    assert len(result.missing_from_bulletin) == 1
    assert result.missing_from_bulletin[0]["url"] == "https://g/2"
    assert result.google_news_count == 2
    assert result.bulletin_count == 1


@pytest.mark.asyncio
async def test_compare_all_matched():
    """Different outlets headline the same story differently; fuzzy matching must
    not report those as missing."""
    collector = GoogleNewsCollector()
    google = [
        {"title": "FCC votes on spectrum auction rules - Reuters", "url": "https://g/1"},
        {"title": "Brendan Carr announces broadband initiative - Ars Technica",
         "url": "https://g/2"},
    ]
    bulletin = [
        _article("FCC votes on spectrum auction rules"),
        _article("Brendan Carr announces broadband initiative"),
    ]
    result = await collector.compare_with_bulletin(google, bulletin)
    assert len(result.missing_from_bulletin) == 0
    assert len(result.matched) == 2

    report = await collector.generate_qa_report(result)
    assert report["coverage_rate"] == 1.0
    assert report["qa_passed"] is True


@pytest.mark.asyncio
async def test_feed_failure_graceful(monkeypatch):
    """One feed raising must not lose the others, and must not raise outward."""
    collector = GoogleNewsCollector(feeds=[
        {"name": "good", "url": "https://feed.test/ok"},
        {"name": "bad", "url": "https://feed.test/boom"},
    ])

    async def fake_fetch(self, client, feed):
        if feed["name"] == "bad":
            raise RuntimeError("connection reset")
        return gnc.parse_rss(SAMPLE_RSS)

    monkeypatch.setattr(GoogleNewsCollector, "_fetch_feed", fake_fetch)
    out = await collector.collect_raw()
    assert len(out) == 2, "the healthy feed's articles must survive"


@pytest.mark.asyncio
async def test_qa_report_structure():
    collector = GoogleNewsCollector()
    google = [{"title": f"Story {i}", "url": f"https://g/{i}", "source": "Reuters",
               "published": "Mon, 04 Aug 2026 12:00:00 GMT"} for i in range(6)]
    result = await collector.compare_with_bulletin(google, [])
    report = await collector.generate_qa_report(result)

    for key in ("google_news_count", "bulletin_count", "matched",
                "missing_from_bulletin", "missing_articles", "coverage_rate",
                "qa_passed", "match_threshold", "generated_at"):
        assert key in report, f"missing key: {key}"

    assert report["google_news_count"] == 6
    assert report["missing_from_bulletin"] == 6
    assert report["coverage_rate"] == 0.0
    # 6 missing is over the threshold of 5 — QA must fail rather than pass quietly.
    assert report["qa_passed"] is False
    entry = report["missing_articles"][0]
    assert set(entry) == {"title", "source", "url", "date"}


def test_jaro_winkler_bounds_and_threshold():
    assert gnc.jaro_winkler("identical", "identical") == 1.0
    assert gnc.jaro_winkler("", "") == 1.0
    assert gnc.jaro_winkler("abc", "") == 0.0
    assert 0.0 <= gnc.jaro_winkler("FCC spectrum", "robocall fines") <= 1.0
    # Unrelated headlines must fall below the match threshold.
    assert not gnc.titles_match("FCC votes on spectrum auction",
                                "Robocall enforcement action announced")


def test_normalize_title_strips_outlet_suffix():
    assert gnc.normalize_title("FCC votes today - Reuters") == "fcc votes today"
    # A hyphenated headline ending in punctuation is not an outlet suffix.
    assert "why" in gnc.normalize_title("FCC acts - but why?")
