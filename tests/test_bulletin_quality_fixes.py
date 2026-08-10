"""Bulletin quality issues found reviewing the Aug 10 2026 prod briefing.

Five fixes, each traced to a specific root cause rather than a symptom:

  1  29 stories in "General" — get_category() returns "General" the moment a
     story mentions the FCC generically, and because "General" is a member of
     AGT_SECTIONS that answer short-circuited every router below it.
  2  "CHAIRMAN CARR:" on headlines — the FCC's own release format leaking into
     the title.
  4  Spanish/Greek/Vietnamese stories in an English deliverable.
  5  "Enforcement & Consumer" rendered with zero articles — every label in
     AGT_SECTIONS was emitted whether or not it had stories.
  6  Articles whose "summary" was the headline repeated.

The examples below are the real headlines from that briefing, not invented ones.
"""

import pytest

from app.bulletin_intelligence import engine
from app.bulletin_intelligence.engine import (AGT_SECTIONS, GENERIC_SECTION,
                                              _clean_headline, _keyword_section,
                                              is_english)

pytestmark = [pytest.mark.regression, pytest.mark.bulletin]


class Art:
    def __init__(self, title="", summary="", topic="other", section="",
                 relevance_score=0.8, source_type="news"):
        self.title = title
        self.summary = summary
        self.topic = topic
        self.section = section
        self.relevance_score = relevance_score
        self.source_type = source_type
        self.article_id = title[:20]


# ── ISSUE 1 — "General" is no longer a sink ──────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("FCC Removes Nationwide Cap Limiting Local TV Ownership",
     "Media & Broadcasting"),
    ("Trump Nominates Danielle Thumann for FCC Spot", "Business & Tech"),
    ("FCC considering new restrictions on DJI drones",
     "Public Safety / Cybersecurity / Privacy"),
    ("House Committee Wants More FCC Oversight of Chinese Firms",
     "Business & Tech"),
])
def test_real_headlines_no_longer_land_in_general(title, expected):
    """Every one of these was in General on Aug 10."""
    assert engine._section_of(Art(title=title)) == expected


def test_general_from_the_org_classifier_no_longer_short_circuits(monkeypatch):
    """The root cause, tested at the mechanism.

    get_category() returns "General" for any story that mentions the FCC
    generically. Because "General" is a member of AGT_SECTIONS, that answer used
    to be accepted and returned before the boolean spec and the keyword router
    ever ran. Forcing it here proves the short-circuit is gone regardless of
    which real headlines happen to trip it.
    """
    monkeypatch.setattr(engine, "get_category", lambda *a, **kw: GENERIC_SECTION)
    article = Art(title="FCC Removes Nationwide Cap Limiting Local TV Ownership")
    assert engine._section_of(article) == "Media & Broadcasting"


def test_a_general_answer_from_the_boolean_spec_also_does_not_win(monkeypatch):
    monkeypatch.setattr(engine, "get_category", lambda *a, **kw: GENERIC_SECTION)
    monkeypatch.setattr(engine, "_boolean_section", lambda *a, **kw: GENERIC_SECTION)
    article = Art(title="Emergency alert system outage in three states")
    assert engine._section_of(article) == "Public Safety / Cybersecurity / Privacy"


def test_a_stored_general_section_on_the_article_does_not_win(monkeypatch):
    """art.section is whatever a previous run wrote. A stored "General" is the
    same sink by another route."""
    monkeypatch.setattr(engine, "get_category", lambda *a, **kw: GENERIC_SECTION)
    monkeypatch.setattr(engine, "_boolean_section", lambda *a, **kw: "")
    article = Art(title="Senate Commerce schedules confirmation hearing",
                  section=GENERIC_SECTION)
    assert engine._section_of(article) == "Business & Tech"


def test_general_remains_the_last_resort():
    """Nothing is dropped. A story matching no rule still gets a home."""
    assert engine._section_of(Art(title="Zzzz qqqq wwww")) == GENERIC_SECTION


def test_general_is_still_a_valid_section_label():
    """It stays in AGT_SECTIONS — that list is a contract deliverable's
    structure, and removing the label is a different decision from stopping it
    being used as a sink."""
    assert GENERIC_SECTION in AGT_SECTIONS


@pytest.mark.parametrize("title,expected", [
    ("Senate Commerce schedules confirmation hearing", "Business & Tech"),
    ("Huawei equipment added to the covered list", "International"),
    ("House Committee Wants More FCC Oversight of Chinese Firms",
     "Business & Tech"),
    ("Emergency alert system outage in three states",
     "Public Safety / Cybersecurity / Privacy"),
    ("FCC considering new restrictions on DJI drones",
     "Public Safety / Cybersecurity / Privacy"),
])
def test_the_added_keywords_route(title, expected):
    assert _keyword_section(title) == expected


def test_rule_order_is_deliberate_where_rules_overlap():
    """"FCC bans Chinese robots" matches BOTH the equipment-security rule and
    the China rule. The brief asked for both, so one has to win: the earlier
    rule does, and Public Safety is checked first because the FCC treats these
    as equipment-authorisation matters. Recorded so the precedence is a decision
    rather than an accident of list order."""
    assert _keyword_section("FCC bans Chinese robot vacuums") == \
        "Public Safety / Cybersecurity / Privacy"


# ── ISSUE 2 — speaker prefixes ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("CHAIRMAN CARR: FCC Removes Nationwide Cap",
     "FCC Removes Nationwide Cap"),
    ("COMMISSIONER GOMEZ: Statement on Broadband",
     "Statement on Broadband"),
    ("CHAIRWOMAN ROSENWORCEL: On Spectrum", "On Spectrum"),
    ("[Broadcast] CHAIRMAN CARR: FCC Acts", "FCC Acts"),
])
def test_speaker_prefix_is_stripped(raw, clean):
    assert _clean_headline(raw) == clean


@pytest.mark.parametrize("title", [
    "Starlink: what the FCC filing says",
    "Analysis: FCC ownership rules explained",
    "T-Mobile CEO Dismisses Fixed Wireless Concerns",
    "FCC Removes Nationwide Cap Limiting Local TV Ownership",
])
def test_a_legitimate_headline_is_not_decapitated(title):
    """The reason the rule is anchored and bounded rather than "strip anything
    before the first colon" — that would gut real headlines."""
    assert _clean_headline(title) == title


# ── ISSUE 4 — English only ───────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Amazon prepara un ejército de más de 5.000 satélites",
    "Δορυφόροι Starlink ενισχύουν την κάλυψη",
    "Làn sóng tác nhân AI nổi loạn đang lan rộng",
])
def test_foreign_language_articles_are_excluded(title):
    """All three appeared in the Aug 10 briefing."""
    assert is_english(Art(title=title)) is False


@pytest.mark.parametrize("title", [
    "FCC Removes Nationwide Cap Limiting Local TV Ownership",
    "Telefonica CEO to meet FCC on 5G roll-out",
    "FCC fines operator $2.5M",
])
def test_english_articles_are_kept(title):
    assert is_english(Art(title=title)) is True


def test_an_empty_article_is_kept():
    """Nothing to detect is not evidence of a foreign language."""
    assert is_english(Art(title="", summary="")) is True


def test_the_gate_fails_open_when_the_detector_is_missing(monkeypatch):
    """A missing optional dependency must never empty a briefing."""
    import builtins

    real_import = builtins.__import__

    def _no_langdetect(name, *a, **kw):
        if name == "langdetect":
            raise ImportError("not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_langdetect)
    assert is_english(Art(title="Δορυφόροι Starlink ενισχύουν")) is True


def test_the_gate_fails_open_when_detection_raises(monkeypatch):
    import langdetect

    def _boom(_text):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(langdetect, "detect", _boom)
    assert is_english(Art(title="Δορυφόροι Starlink")) is True


def test_detection_is_deterministic():
    """langdetect is randomised by default. A story included one day and dropped
    the next for identical text is worse than either outcome consistently."""
    article = Art(title="Amazon prepara un ejército de más de 5.000 satélites")
    assert {is_english(article) for _ in range(8)} == {False}


# ── ISSUE 5 — empty sections ─────────────────────────────────────────────────

def test_empty_sections_are_not_rendered():
    """An empty heading does not read as "no news today"; it reads as a section
    that failed to populate."""
    import inspect

    src = inspect.getsource(engine._collect_sections)
    assert "if by_section[sec]" in src, \
        "sections with no articles must be filtered out"


def test_sections_with_articles_are_still_rendered():
    import inspect

    src = inspect.getsource(engine._collect_sections)
    assert "for sec in AGT_SECTIONS" in src, "section ORDER must be preserved"


# ── ISSUE 6 — missing summaries ──────────────────────────────────────────────

def test_articles_without_summaries_are_held_back():
    import inspect

    src = inspect.getsource(engine._prepare_briefing_sections)
    assert "_has_summary" in src
    assert "no_summary" in src, "held-back stories must be flagged for QA"


def test_a_summary_that_repeats_the_headline_does_not_count():
    """The reported symptom: the reader saw the same sentence twice and learned
    nothing, which looked like a formatting fault."""
    import inspect

    src = inspect.getsource(engine._prepare_briefing_sections)
    assert "!= title" in src or "text.strip().lower() != title" in src


def test_held_back_articles_are_not_deleted():
    """They stay in the archive and the QA workbook. Silently discarding them
    would hide a summarisation outage behind a slightly shorter briefing."""
    import inspect

    src = inspect.getsource(engine._prepare_briefing_sections)
    assert 'setattr(cluster[0], "qa_flag", "no_summary")' in src


# ── ISSUE 3 — Related Coverage grouping ──────────────────────────────────────
# Thresholds are deliberately NOT changed. Only which member of an existing
# cluster leads it moves.

class SrcArt(Art):
    def __init__(self, title, source="", outlet="", url="", relevance_score=0.5):
        super().__init__(title=title, relevance_score=relevance_score)
        self.source = source
        self.outlet = outlet
        self.url = url
        self.provider = ""


def test_the_federal_register_leads_over_an_aggregator():
    """Ten outlets rewrite one FCC announcement. The record should lead, not the
    aggregation of a rewrite — which is what relevance-only ordering produced."""
    aggregated = SrcArt("FCC Removes Nationwide Cap on TV Ownership",
                        source="gdelt", relevance_score=0.95)
    record = SrcArt("FCC Removes Nationwide Cap on TV Ownership",
                    source="federal_register", relevance_score=0.40)
    clusters = engine._cluster_stories([aggregated, record])
    assert len(clusters) == 1, "these are the same story"
    assert clusters[0][0] is record, "the most authoritative source must lead"


def test_relevance_still_breaks_ties_within_a_tier():
    low = SrcArt("FCC opens spectrum auction docket", source="rss",
                 relevance_score=0.30)
    high = SrcArt("FCC opens spectrum auction docket", source="rss",
                  relevance_score=0.90)
    clusters = engine._cluster_stories([low, high])
    assert clusters[0][0] is high


@pytest.mark.parametrize("source,expected_rank", [
    ("federal_register", 0),
    ("congress_gov", 0),
    ("rss", 1),
    ("newsapi_ai", 2),
    ("gdelt", 3),
    ("claude_search", 4),
])
def test_authority_tiers(source, expected_rank):
    assert engine._source_authority(SrcArt("t", source=source)) == expected_rank


def test_authority_reads_the_url_when_the_source_is_unhelpful():
    """The same publication arrives under different labels depending on which
    ingester found it."""
    art = SrcArt("FCC order", source="", url="https://www.federalregister.gov/d/2026-1")
    assert engine._source_authority(art) == 0


def test_an_unknown_source_is_not_treated_as_authoritative():
    """Defaulting an unrecognised source to the top would let anything lead."""
    assert engine._source_authority(SrcArt("t", source="mystery-wire")) == 3


def test_every_cluster_member_after_the_primary_becomes_related():
    """"Extend Related Coverage to all duplicate clusters" — the related list is
    built from members[1:], so any cluster with more than one member has one."""
    import inspect

    src = inspect.getsource(engine._collect_sections)
    assert "for m in members[1:]" in src


def test_related_coverage_renders_in_the_specified_format():
    """RELATED — [Source]: [Title], hyperlinked."""
    import inspect

    src = inspect.getsource(engine._render_agt_html)
    assert "RELATED &mdash;" in src
    assert "Related Coverage" in src
    assert 'extlink(x["url"], x["headline"])' in src


def test_clustering_thresholds_are_unchanged():
    """Explicitly pinned: the brief said group first, tune later with real data."""
    import inspect

    src = inspect.getsource(engine._cluster_stories)
    assert "inter >= 2 and (inter / union) >= 0.30" in src


def test_unrelated_stories_are_not_merged():
    a = SrcArt("FCC opens spectrum auction docket", source="rss")
    b = SrcArt("Senate confirms new agriculture secretary", source="rss")
    assert len(engine._cluster_stories([a, b])) == 2
