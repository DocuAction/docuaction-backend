"""Phase 5.5 QA verification logic (no network)."""
from app.bulletin_intelligence.fcc_qa_verification import (normalise, titles_match,
                                                           is_relevant, QA_QUERIES)


def test_normalise_strips_google_news_publisher_suffix():
    """Google News appends ' - Publisher'. Without stripping it, the same story
    from RSS and from Google News never matches and gets added twice."""
    assert normalise("FCC Opens Auction - Reuters") == "fcc opens auction"


def test_normalise_strips_punctuation_and_case():
    assert normalise("FCC's C-Band Auction: Round 2!") == "fcc s c band auction round 2"


def test_titles_match_identical():
    assert titles_match("FCC opens spectrum auction", "FCC opens spectrum auction")


def test_titles_match_syndicated_variant():
    assert titles_match("FCC Chairman Carr Announces Plan - Reuters",
                        "FCC Chairman Carr announces plan")


def test_titles_do_not_match_different_stories():
    assert not titles_match("FCC opens spectrum auction",
                            "Court strikes down net neutrality order")


def test_relevance_gate_rejects_false_fcc():
    """A bare 'FCC' keyword search returns football clubs and credit companies."""
    assert not is_relevant({"title": "FC Barcelona defeats rival", "summary": ""})
    assert not is_relevant({"title": "Florida Citrus Commission meets", "summary": ""})


def test_relevance_gate_accepts_real_fcc():
    for t in ("FCC spectrum auction opens", "Robocall enforcement action announced",
              "BEAD broadband funding allocated", "Starlink direct-to-device approval"):
        assert is_relevant({"title": t, "summary": ""}), t


def test_qa_queries_configured():
    # 9 as of 2026-08-04: a query covering Olivia Trusty and Nathan Simington was
    # added because neither commissioner appeared in any prior pattern, so stories
    # naming only one of them were structurally invisible to the QA cross-check.
    assert len(QA_QUERIES) == 9
    joined = " ".join(QA_QUERIES)
    for commissioner in ("Brendan Carr", "Anna Gomez", "Geoffrey Starks",
                         "Olivia Trusty", "Nathan Simington"):
        assert commissioner in joined, f"no QA query covers {commissioner}"


def test_empty_titles_never_match():
    assert not titles_match("", "anything")
    assert not titles_match("anything", "")
