"""
Unit tests for DocuAction Bulletin Intelligence enhancement services.
Run: python3 -m pytest test_bulletin_enhancements.py -v
(or: python3 test_bulletin_enhancements.py for a plain run)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boolean_filter as bf
import scoring
import clustering


# ── Lightweight Article stand-in ──────────────────────────────────────────────
class A:
    def __init__(self, title="", summary="", url="https://x.com/a.html",
                 outlet="News", topic="fcc_news", relevance_score=0.7,
                 published_at="2026-06-13T12:00:00+00:00", full_text=None,
                 article_id=None, source_type="news",
                 broadcast_clip_url="", is_paywalled=False, author="",
                 article_type="news", sentiment="neutral"):
        self.title = title
        self.summary = summary
        self.url = url
        self.outlet = outlet
        self.topic = topic
        self.relevance_score = relevance_score
        self.published_at = published_at
        self.full_text = full_text if full_text is not None else summary
        self.article_id = article_id or title[:10]
        self.source_type = source_type
        self.broadcast_clip_url = broadcast_clip_url
        self.is_paywalled = is_paywalled
        self.author = author
        self.article_type = article_type
        self.sentiment = sentiment


# ── Problem #2: scoring ───────────────────────────────────────────────────────
def test_authority_known_and_unknown():
    assert scoring.authority_weight("Reuters") == 100
    assert scoring.authority_weight("reuters") == 100
    assert scoring.authority_weight("Radio World") == 80
    assert scoring.authority_weight("Federal Register — FCC") == 90  # substring
    assert scoring.authority_weight("Totally Unknown Blog") == 60

def test_final_score_orders_by_authority():
    reuters = scoring.final_score(0.7, "Reuters", "2026-06-13T12:00:00+00:00")
    blog = scoring.final_score(0.7, "Unknown Blog", "2026-06-13T12:00:00+00:00")
    assert reuters > blog  # same relevance+recency, higher authority wins

def test_recency_weight_decay():
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    fresh = scoring.recency_weight((now - timedelta(hours=2)).isoformat(), now)
    old = scoring.recency_weight((now - timedelta(hours=70)).isoformat(), now)
    assert fresh == 100
    assert old < fresh


# ── Problem #5: quality filter ────────────────────────────────────────────────
def test_quality_rejects_thin_and_spam():
    good = A(title="FCC Proposes New Spectrum Auction Rules for 2026",
             summary="The FCC announced a detailed proposal " * 10,
             url="https://reuters.com/article.html")
    thin = A(title="FCC", summary="short", url="https://x.com/a.html")
    spam = A(title="10 Best VPN Deals Sponsored Coupon Code",
             summary="buy now discount code " * 10,
             url="https://spam.com/a.html")
    assert clustering.quality_score(good) >= 0.70
    assert clustering.quality_score(thin) < 0.70
    assert clustering.quality_score(spam) < 0.70

def test_filter_quality_threshold():
    arts = [
        A(title="FCC Proposes Detailed New Spectrum Auction Rules",
          summary="x " * 100, url="https://reuters.com/a.html"),
        A(title="x", summary="y", url="bad-url"),
    ]
    kept = clustering.filter_quality(arts, 0.70)
    assert len(kept) == 1


# ── Problem #3: clustering ────────────────────────────────────────────────────
def test_clustering_groups_same_event():
    arts = [
        A(title="FCC plans tighter rules on undersea internet cables",
          outlet="Reuters", topic="international",
          summary="submarine cable " * 30, url="https://reuters.com/1.html"),
        A(title="FCC tightens rules on undersea cables targeting China",
          outlet="Benzinga", topic="international",
          summary="submarine cable " * 30, url="https://benzinga.com/2.html"),
        A(title="Spectrum auction brings in 54 million on day one",
          outlet="Broadband Breakfast", topic="wireless_spectrum",
          summary="spectrum auction " * 30, url="https://bb.com/3.html"),
    ]
    clusters = clustering.cluster_stories(arts, threshold=0.85)
    # The two cable stories should cluster; auction stands alone → 2 clusters
    assert len(clusters) == 2
    cable = [c for c in clusters if "cable" in c.primary.title.lower()][0]
    assert cable.primary.outlet == "Reuters"   # highest authority is primary
    assert len(cable.similar) == 1

def test_cluster_primary_is_highest_authority():
    arts = [
        A(title="Same big FCC story headline about spectrum policy",
          outlet="The Hill", topic="wireless_spectrum",
          summary="spectrum " * 30, url="https://hill.com/1.html"),
        A(title="Same big FCC story headline about spectrum policy",
          outlet="Reuters", topic="wireless_spectrum",
          summary="spectrum " * 30, url="https://reuters.com/2.html"),
    ]
    clusters = clustering.cluster_stories(arts, threshold=0.85)
    assert len(clusters) == 1
    assert clusters[0].primary.outlet == "Reuters"


# ── Problem #7: diversity ─────────────────────────────────────────────────────
def test_diversity_caps_dominant_source():
    arts = [A(title=f"Radio story {i}", outlet="Radio World",
              url=f"https://rw.com/{i}.html") for i in range(10)]
    arts += [A(title=f"Other story {i}", outlet=f"Outlet{i}",
               url=f"https://o{i}.com/a.html") for i in range(10)]
    kept, overflow = clustering.enforce_diversity(arts, max_share=0.20)
    rw = [a for a in kept if a.outlet == "Radio World"]
    assert len(rw) <= max(1, int(len(arts) * 0.20))  # ≤ 20% (≤4 of 20)


# ── Boolean taxonomy regression (must stay authoritative) ─────────────────────
def test_boolean_sections_intact():
    assert len(bf.FCC_SECTIONS) == 9
    sec, _ = bf.assign_section(
        "White House Releases Executive Order on Advanced AI",
        "artificial intelligence executive order")
    assert sec == "ai_ml"
    sec2, _ = bf.assign_section(
        "FCC Spectrum Auction Brings in Millions",
        "FCC spectrum auction wireless 5G")
    assert sec2 == "wireless_spectrum"


# ── Story Repository (persistence + retention) ────────────────────────────────
def test_repository_persist_and_retention():
    import os, story_repository as repo
    from datetime import datetime, timezone, timedelta
    repo._db.path = "/tmp/test_repo_suite.db"
    if os.path.exists(repo._db.path):
        os.remove(repo._db.path)
    repo._db._conn = None
    repo._db.degraded = False
    now = datetime.now(timezone.utc)
    fresh = now.isoformat()
    old = (now - timedelta(days=400)).isoformat()
    repo.upsert_article({"article_id": "r1", "agency_id": "fcc", "topic": "fcc_news",
                         "outlet": "Reuters", "title": "T", "url": "u",
                         "published_at": fresh, "ingested_at": fresh})
    repo.upsert_article({"article_id": "r1", "agency_id": "fcc", "topic": "fcc_news",
                         "outlet": "Reuters", "title": "T2", "url": "u",
                         "published_at": fresh, "ingested_at": fresh})  # idempotent
    repo.upsert_article({"article_id": "r2", "agency_id": "fcc", "topic": "ai_ml",
                         "outlet": "AP", "title": "Old", "url": "u",
                         "published_at": old, "ingested_at": old})
    assert repo.stats("fcc")["total_articles"] == 2  # idempotent upsert
    removed = repo.prune_old(12)
    assert removed == 1
    assert repo.stats("fcc")["total_articles"] == 1


def test_repository_degraded_fallback():
    import story_repository as repo
    # Force degraded mode → must not raise, uses in-memory
    repo._db.degraded = True
    repo._db._conn = None
    repo.upsert_article({"article_id": "d1", "agency_id": "x", "topic": "fcc_news",
                         "ingested_at": "2026-06-13T12:00:00+00:00"})
    s = repo.stats("x")
    assert s["backend"] in ("memory", "sqlite")
    repo._db.degraded = False  # reset for other tests


# ── Daily Quality Validation ──────────────────────────────────────────────────
def test_quality_validation_pass():
    import health_monitor as health
    arts = []
    outlets = ["Reuters", "AP", "Bloomberg", "Politico", "Law360"]
    sections = bf.FCC_SECTIONS
    # 70 articles spread across all sections, no outlet > 20%
    for i in range(70):
        arts.append(A(title=f"FCC story number {i}", outlet=outlets[i % len(outlets)],
                      topic=sections[i % len(sections)], url=f"https://x.com/{i}.html"))
    tc = {}
    for a in arts:
        tc[a.topic] = tc.get(a.topic, 0) + 1
    rep = health.validate_briefing(arts, tc, sections)
    assert rep["passed"] is True
    assert rep["article_count"] == 70

def test_quality_validation_fails_low_volume():
    import health_monitor as health
    arts = [A(title=f"FCC story {i}", outlet="Reuters", topic="fcc_news",
              url=f"https://x.com/{i}.html") for i in range(10)]
    tc = {"fcc_news": 10}
    rep = health.validate_briefing(arts, tc, bf.FCC_SECTIONS)
    assert rep["passed"] is False
    assert "minimum_volume" in rep["summary"] or rep["article_count"] == 10

def test_quality_validation_flags_source_dominance():
    import health_monitor as health
    # 60 articles all from one outlet → diversity check must fail
    arts = [A(title=f"FCC story {i}", outlet="Radio World", topic=bf.FCC_SECTIONS[i % 9],
              url=f"https://x.com/{i}.html") for i in range(60)]
    tc = {}
    for a in arts:
        tc[a.topic] = tc.get(a.topic, 0) + 1
    rep = health.validate_briefing(arts, tc, bf.FCC_SECTIONS)
    div = [c for c in rep["checks"] if c["check"] == "source_diversity"][0]
    assert div["passed"] is False


# ── Editorial rules (subscription / FCC.gov cap / freshness) ──────────────────
def test_subscription_flagging():
    import editorial_rules as ed
    arts = [
        A(title="Story", outlet="Law360", url="https://law360.com/a.html"),
        A(title="Story2", outlet="Communications Daily", url="https://commdaily.com/b.html"),
        A(title="Story3", outlet="Reuters", url="https://reuters.com/c.html"),
    ]
    n = ed.flag_subscriptions(arts)
    assert n == 2
    assert arts[0].is_paywalled is True
    assert arts[1].is_paywalled is True
    assert arts[2].is_paywalled is False

def test_fccgov_cap():
    import editorial_rules as ed
    arts = [A(title=f"FCC item {i}", outlet="FCC",
              url=f"https://fcc.gov/news/{i}", published_at="2026-06-13T12:00:00+00:00")
            for i in range(6)]
    arts.append(A(title="Reuters story", outlet="Reuters",
                  url="https://reuters.com/x", published_at="2026-06-13T12:00:00+00:00"))
    kept = ed.enforce_fccgov_cap(arts)
    fccgov = [a for a in kept if "fcc.gov" in a.url.lower()]
    assert len(fccgov) <= 3  # capped
    assert any(a.outlet == "Reuters" for a in kept)  # external passes

def test_freshness_drops_old():
    import editorial_rules as ed
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    fresh = A(title="fresh", outlet="Reuters",
              published_at=(now - timedelta(hours=5)).isoformat())
    old = A(title="old", outlet="Reuters",
            published_at=(now - timedelta(hours=48)).isoformat())
    kept = ed.enforce_freshness([fresh, old], 24, now)
    assert fresh in kept and old not in kept


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
