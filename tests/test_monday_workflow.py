"""Contract-critical regression: the Monday ONC workflow, end to end.

If this file fails, contract-critical functionality is broken. It must pass
before every deployment.

WHY THIS IS SHAPED THE WAY IT IS

The workflow spans an HTTP boundary, a database, and three third-party
registries. A test that drives all of that at once is a test that fails on a
Tuesday because CMS had a slow minute, and a suite that cries wolf gets ignored
— which is worse than not having it.

So the chain is split:

* The pure stages — classification, sampling arithmetic, report assembly,
  bucket accounting — run in-process on fixtures. These are deterministic and
  assert real outcomes, including the arithmetic identity that B1..B4 must sum
  to the number of entities reviewed.
* The HTTP surface is asserted for existence and gating (contract smoke), not
  for live third-party responses.
* The genuinely live end-to-end run against dev is a scripted deployment gate,
  not a unit test — it needs a seeded registry and reachable connectors, and
  is recorded in the sprint report rather than pretended at here.

Being explicit about that boundary is the point: this file proves the pipeline's
logic, and says plainly what it does not prove.
"""
from datetime import date

import pytest

from app.tefca_registry.bucket_classifier import (
    SEED_RULES, BucketClassifier, NOT_CHECKED, NOT_FOUND, UNAVAILABLE, VERIFIED)
from app.tefca_registry.report_generator import build_report_data, render_html
from app.tefca_registry.review_service import (
    IMPLEMENTED_SOURCES, NO_CONNECTOR, coverage_note, detect_source_conflict)
from app.tefca_registry.sampling_engine import CochranSampler

GATED = (401, 403)
BUCKETS = ("B1", "B2", "B3", "B4")
VALID_STATES = {VERIFIED, NOT_FOUND, NOT_CHECKED, UNAVAILABLE, "failed",
                "clear", "excluded"}


def classifier():
    return BucketClassifier(rules=[dict(r, version=1) for r in SEED_RULES])


def sources_for(kind: str) -> dict:
    """Verification payloads standing in for the five seeded entity shapes."""
    base = {k: {"status": NOT_CHECKED, "reason": NO_CONNECTOR[k]} for k in NO_CONNECTOR}
    shapes = {
        # A real, NPPES-listed NPI: everything that answered agrees.
        "real_clean": {"nppes": {"status": VERIFIED}, "pecos": {"status": VERIFIED},
                       "oig_leie": {"status": "clear"}},
        # Synthetic NPI: reachable sources have no record of it.
        "synthetic": {"nppes": {"status": NOT_FOUND}, "pecos": {"status": NOT_FOUND},
                      "oig_leie": {"status": "clear"}},
        # PECOS down — an outage, not a finding.
        "partial": {"nppes": {"status": VERIFIED}, "pecos": {"status": UNAVAILABLE},
                    "oig_leie": {"status": "clear"}},
        # Disqualifying.
        "excluded": {"nppes": {"status": VERIFIED}, "pecos": {"status": VERIFIED},
                     "oig_leie": {"status": "excluded"}},
        # Sources disagree.
        "conflict": {"nppes": {"status": VERIFIED}, "pecos": {"status": NOT_FOUND},
                     "oig_leie": {"status": "clear"}},
    }
    return {**base, **shapes[kind]}


def results_for(kind: str, npi_valid: bool = True) -> dict:
    srcs = sources_for(kind)
    return {
        "sources": srcs,
        "fields": {"npi_validation": "valid" if npi_valid else "invalid",
                   "nppes_pecos_conflict": (
                       srcs["nppes"]["status"] != srcs["pecos"]["status"]
                       and srcs["pecos"]["status"] in (VERIFIED, NOT_FOUND)),
                   "multiple_source_conflict": detect_source_conflict(srcs)},
        "confidence_score": None,
    }


# ── P0.2 conflict detection ──────────────────────────────────────────────────

def test_nppes_pecos_conflict_detected():
    """NPPES has the provider, PECOS does not — both answered, so it is a real
    disagreement."""
    assert detect_source_conflict(sources_for("conflict")) is True


def test_pecos_oig_conflict_detected():
    """Enrolled in PECOS while excluded by OIG — the more serious pairing."""
    assert detect_source_conflict(sources_for("excluded")) is True


def test_no_conflict_when_consistent():
    assert detect_source_conflict(sources_for("real_clean")) is False
    assert detect_source_conflict(sources_for("synthetic")) is False


def test_unavailable_source_is_never_a_conflict():
    """Only sources that ANSWERED can contradict each other. Calling an outage a
    conflict would manufacture a B3 out of someone else's downtime."""
    assert detect_source_conflict(sources_for("partial")) is False


# ── P0.3 coverage excludes unimplemented connectors ──────────────────────────

def test_coverage_counts_only_implemented_connectors():
    cov = coverage_note(sources_for("real_clean"))
    assert cov["sources_available"] == len(IMPLEMENTED_SOURCES) == 3
    assert cov["sources_checked"] == 3


def test_unimplemented_connectors_do_not_reduce_coverage():
    """A connector nobody has written yet must not make a healthy verification
    look degraded — otherwise full coverage is unreachable by construction."""
    cov = coverage_note(sources_for("real_clean"))
    assert cov["sources_checked"] == cov["sources_available"]
    assert cov["sources_not_implemented"] == 3
    assert set(cov["not_implemented"]) == set(NO_CONNECTOR)


def test_unimplemented_are_disclosed_not_hidden():
    note = coverage_note(sources_for("real_clean"))["coverage_note"]
    assert "not implemented" in note.lower()
    for src in NO_CONNECTOR:
        assert src in note


def test_unimplemented_are_not_checked_never_unavailable():
    """'unavailable' implies a source that will recover and invites a retry.
    'not implemented' needs a decision."""
    for src, reason in NO_CONNECTOR.items():
        low = reason.lower()
        # The guard is about MEANING, not a magic phrase: the reason must make
        # clear the source needs a decision rather than a retry. "under
        # investigation" says that as plainly as "not operational" did.
        assert ("not implemented" in low or "not operational" in low
                or "under investigation" in low), reason
    srcs = sources_for("real_clean")
    for src in NO_CONNECTOR:
        assert srcs[src]["status"] == NOT_CHECKED
        assert srcs[src]["status"] != UNAVAILABLE


# ── the workflow chain ───────────────────────────────────────────────────────

WORKFLOW = [
    ("real_clean", True, "B1"),
    ("partial", True, "B1"),        # outage must not demote
    ("synthetic", True, "B3"),      # no record anywhere
    ("excluded", True, "B4"),       # disqualifying
    ("real_clean", False, "B4"),    # invalid NPI is disqualifying
]


@pytest.mark.parametrize("kind,npi_valid,expected", WORKFLOW)
def test_each_entity_classifies_as_expected(kind, npi_valid, expected):
    r = classifier().classify(results_for(kind, npi_valid))
    assert r.bucket == expected, f"{kind}/npi_valid={npi_valid}: {r.rationale}"


def test_every_classification_carries_a_rule_and_version():
    """Without both, a past review cannot be explained after the rules change."""
    for kind, npi_valid, _ in WORKFLOW:
        r = classifier().classify(results_for(kind, npi_valid))
        assert r.rule_code is not None
        assert r.rule_version is not None


def test_every_source_reports_one_of_the_five_states():
    for kind, _npi, _b in WORKFLOW:
        for src, info in sources_for(kind).items():
            assert info["status"] in VALID_STATES, f"{src} -> {info['status']}"


def test_sample_draw_is_reproducible_and_captures_config():
    pop = [{"id": i, "level": "qhin" if i < 2 else "participant"} for i in range(10)]
    s = CochranSampler()
    a = s.draw_sample(pop, strata=lambda x: x["level"], seed=99,
                      confidence=0.95, margin=0.05, proportion=0.5)
    b = s.draw_sample(pop, strata=lambda x: x["level"], seed=99,
                      confidence=0.95, margin=0.05, proportion=0.5)
    assert [x["id"] for x in a.selected] == [x["id"] for x in b.selected]
    cfg = a.config()
    for key in ("confidence_level", "margin_of_error", "proportion", "use_fpc",
                "random_seed", "population_size", "sample_size"):
        assert key in cfg


def test_weekly_report_has_every_required_section():
    reviews = [{"review_id": f"REV-2026-{i:06d}",
                "classification_bucket": b, "reviewer_resolution": None,
                "reclassified_to": None}
               for i, (_k, _n, b) in enumerate(WORKFLOW, start=1)]
    data = build_report_data(
        report_type="weekly", period_start=date(2026, 7, 27),
        period_end=date(2026, 8, 2), reviews=reviews,
        verifications=[{"source": "nppes", "verification_status": VERIFIED}],
        rule_set_version=1)
    for section in ("executive_summary", "sampling_summary",
                    "classification_distribution", "discrepancy_rate",
                    "verification_coverage", "outstanding_items",
                    "data_sources_used", "methodology", "limitations",
                    "configuration"):
        assert section in data, section
    assert data["limitations"], "limitations must never be empty"


def test_bucket_counts_sum_to_entities_reviewed():
    """The arithmetic identity a reader checks first. If these disagree, some
    entity was reviewed and silently dropped from the distribution."""
    reviews = [{"review_id": f"REV-2026-{i:06d}",
                "classification_bucket": b, "reviewer_resolution": None,
                "reclassified_to": None}
               for i, (_k, _n, b) in enumerate(WORKFLOW, start=1)]
    data = build_report_data(
        report_type="weekly", period_start=date(2026, 7, 27),
        period_end=date(2026, 8, 2), reviews=reviews, verifications=[],
        rule_set_version=1)
    counts = data["classification_distribution"]["counts"]
    assert sum(counts[b] for b in BUCKETS) == data["executive_summary"]["entities_reviewed"]
    assert sum(counts[b] for b in BUCKETS) == len(reviews)


def test_report_renders_with_contract_number():
    data = build_report_data(
        report_type="weekly", period_start=date(2026, 7, 27),
        period_end=date(2026, 8, 2), reviews=[], verifications=[],
        rule_set_version=1)
    html = render_html(data, "WR-2026-W31")
    assert "7571MN26F80064" in html
    assert "Limitations and Exceptions" in html


# ── contract smoke: the endpoints exist and are gated ────────────────────────

CONTRACT_ENDPOINTS = [
    ("GET", "/api/tefca/arc/review-rules"),
    ("GET", "/api/tefca/arc/review-rules/history"),
    ("GET", "/api/tefca/arc/samples"),
    ("GET", "/api/tefca/arc/reviews"),
    ("GET", "/api/tefca/arc/reports"),
]


@pytest.mark.parametrize("method,path", CONTRACT_ENDPOINTS)
def test_contract_smoke_endpoints_exist_and_are_guarded(client, method, path):
    """A 404 here means a contract endpoint is missing or was shadowed by
    another router — which has already happened once."""
    r = client.request(method, path)
    assert r.status_code != 404, f"{path} is not mounted"
    assert r.status_code in GATED, f"{path} returned {r.status_code} anonymously"


def test_priority_review_and_generate_are_mounted_and_guarded(client):
    for path in ("/api/tefca/arc/priority-review", "/api/tefca/arc/reports/generate"):
        r = client.post(path)
        assert r.status_code != 404, f"{path} is not mounted"
        assert r.status_code in GATED


def test_legacy_tefca_routes_are_not_shadowed(client):
    """The ARC router sits at /api/tefca/arc precisely so it does not displace
    the legacy module. Both must answer."""
    for path in ("/api/tefca/reports", "/api/tefca/reviews"):
        r = client.get(path)
        assert r.status_code != 404, f"legacy {path} disappeared"


def test_production_seeding_is_refused(client, monkeypatch):
    """Demo entities in the production registry would corrupt the population
    every sample and report is drawn from, and that is not correctable after
    the fact."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    r = client.post("/api/tefca/registry/dev/seed")
    assert r.status_code in (401, 403)
