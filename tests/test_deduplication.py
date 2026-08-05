"""URL / AMP deduplication tests (6 required + edge cases).

The asymmetry that shapes these tests: leaving a duplicate in is visible and
annoying, dropping a real story is invisible and costly. So the "different
articles are kept" case matters at least as much as the matching ones.
"""
from app.bulletin_intelligence import url_dedup as ud
from app.bulletin_intelligence.url_dedup import (
    normalize_url, extract_article_id, is_duplicate, is_amp_url, find_duplicates)


def A(title, url, outlet="Reuters"):
    return {"title": title, "url": url, "outlet": outlet}


def test_amp_url_detected():
    """AMP and canonical variants must collapse to one normalized key."""
    pairs = [
        ("https://example.com/news/story", "https://example.com/news/story/amp"),
        ("https://example.com/news/story", "https://example.com/news/amp/story"),
        ("https://example.com/news/story", "https://amp.example.com/news/story"),
        ("https://example.com/news/story", "https://example.com/news/story?amp=1"),
    ]
    for canonical, amp in pairs:
        assert normalize_url(canonical) == normalize_url(amp), f"{amp} != {canonical}"
        assert is_amp_url(amp), f"{amp} not detected as AMP"
        assert not is_amp_url(canonical)
        dup, why = is_duplicate(A("Same headline", canonical), A("Same headline", amp))
        assert dup and "URL" in why

    # Tracking parameters must not defeat the match either.
    assert normalize_url("https://example.com/a?utm_source=x&utm_medium=y") == \
           normalize_url("https://example.com/a")
    assert normalize_url("https://example.com/a?fbclid=123") == normalize_url("https://example.com/a")
    # ...but a meaningful parameter must be preserved: ?story=1 and ?story=2 are
    # different articles on some CMSs.
    assert normalize_url("https://example.com/a?story=1") != \
           normalize_url("https://example.com/a?story=2")


def test_article_id_match():
    assert extract_article_id("https://www.law360.com/articles/2509705/fcc-acts") == "2509705"
    assert extract_article_id("https://www.law360.com/article/2509705") == "2509705"
    assert extract_article_id("https://example.com/no-id-here") is None

    dup, why = is_duplicate(
        A("FCC acts on spectrum", "https://law360.com/articles/2509705/fcc-acts"),
        A("Completely different wording entirely", "https://law360.com/articles/2509705?utm_source=x"))
    assert dup and "article id" in why

    # A date path must NOT be read as an article id — that would merge every
    # story published on the same day.
    assert extract_article_id("https://example.com/2026/08/05/fcc-story") is None


def test_headline_similarity():
    """>0.85 from the SAME source is a duplicate."""
    dup, why = is_duplicate(
        A("FCC votes to approve spectrum auction rules", "https://reuters.com/a", "Reuters"),
        A("FCC votes to approve spectrum auction rule", "https://reuters.com/b", "Reuters"))
    assert dup and "same source" in why

    # The same near-identical pair from DIFFERENT sources must not trip the
    # same-source rule at 0.85 — it needs the higher cross-source bar.
    a = A("FCC opens inquiry into rural carrier billing", "https://x.com/1", "Reuters")
    b = A("FCC opens inquiry into rural carrier billings", "https://y.com/2", "AP")
    dup2, why2 = is_duplicate(a, b)
    if dup2:
        assert "syndicated" in why2, "cross-source match must use the syndication rule"


def test_cross_source_similarity():
    """>0.92 from any source catches AP/Reuters syndication."""
    dup, why = is_duplicate(
        A("FCC fines Milwaukee radio station $8,000", "https://ap.org/x", "AP"),
        A("FCC fines Milwaukee radio station $8,000", "https://star.com/y", "Milwaukee Star"))
    assert dup and "syndicated" in why


def test_different_articles_kept():
    """The expensive failure is dropping a real story. These must all survive."""
    cases = [
        (A("FCC votes on spectrum auction rules", "https://a.com/1"),
         A("FCC fines radio station for pirate broadcasting", "https://b.com/2")),
        (A("Carr announces broadband initiative", "https://a.com/3"),
         A("Gomez dissents on media ownership order", "https://a.com/4")),
        # Same outlet, same day, genuinely different stories.
        (A("FCC opens 911 outage inquiry", "https://law360.com/articles/111"),
         A("FCC closes robocall docket", "https://law360.com/articles/222")),
    ]
    for a, b in cases:
        dup, why = is_duplicate(a, b)
        assert not dup, f"wrongly merged: {a['title']} / {b['title']} ({why})"


def test_non_amp_preferred():
    """When an AMP copy is seen first, the canonical one must replace it."""
    amp = A("FCC votes on spectrum", "https://example.com/story/amp")
    canonical = A("FCC votes on spectrum", "https://example.com/story")

    keepers, groups = find_duplicates([amp, canonical])
    assert len(keepers) == 1
    assert keepers[0]["url"] == canonical["url"], "non-AMP version must be kept"
    assert groups[0].duplicates == [amp]
    assert "AMP demoted" in groups[0].reasons[0]

    # And nothing is lost: every input appears exactly once across the output.
    total = len(keepers) + sum(len(g.duplicates) for g in groups)
    assert total == 2


def test_nothing_is_deleted():
    """find_duplicates marks; it never drops."""
    arts = [A(f"Story {i}", f"https://x.com/{i}") for i in range(5)]
    arts.append(A("Story 0", "https://x.com/0/amp"))
    keepers, groups = find_duplicates(arts)
    total = len(keepers) + sum(len(g.duplicates) for g in groups)
    assert total == len(arts), "every input must be accounted for"


def test_duplicate_flag_values():
    amp = A("Story", "https://x.com/s/amp")
    canonical = A("Story", "https://x.com/s")
    keepers, groups = find_duplicates([canonical, amp])
    assert ud.duplicate_flag(canonical, groups) == "No"
    assert ud.duplicate_flag(amp, groups) == "AMP"


def test_empty_and_malformed_urls_do_not_crash():
    assert normalize_url("") == ""
    assert normalize_url(None) == ""
    assert extract_article_id("") is None
    assert not is_amp_url("")
    dup, _ = is_duplicate(A("", ""), A("", ""))
    assert dup is False, "two empty articles must not be called duplicates"
