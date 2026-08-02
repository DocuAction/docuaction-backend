"""B1-B4 rules engine and the sampling engine underneath the review workflow.

These assert the judgements the engine is supposed to encode, not just that it
runs: that an unreachable source never counts against an entity, that an
unmatched case lands in B3 rather than passing silently, and that the same
inputs always produce the same bucket.
"""
import pytest

from app.tefca_registry.bucket_classifier import (
    SEED_RULES, BucketClassifier, ClassificationResult)
from app.tefca_registry.sampling_engine import (
    CochranSampler, discrepancy_rate_ci, z_for)


def clf():
    return BucketClassifier(rules=[dict(r, version=1) for r in SEED_RULES])


def results(**sources):
    """Verification results in the shape the classifier consumes."""
    fields = sources.pop("fields", {})
    conf = sources.pop("confidence_score", None)
    return {"sources": sources, "fields": fields, "confidence_score": conf}


# ── B1 ───────────────────────────────────────────────────────────────────────

def test_b1_all_verified():
    r = clf().classify(results(nppes="verified", oig_leie="clear", pecos="verified"))
    assert r.bucket == "B1"
    assert r.rule_code == "RULE-001"
    assert r.rule_version == 1


def test_b1_partial_unavailable():
    """PECOS down must not demote a clean entity. An outage is not a finding."""
    r = clf().classify(results(nppes="verified", oig_leie="clear",
                               pecos="unavailable", sam_gov="unavailable"))
    assert r.bucket == "B1"
    assert r.rule_code == "RULE-002"
    assert any("did not answer" in m for m in r.matched_conditions)


# ── B2 ───────────────────────────────────────────────────────────────────────

def test_b2_minor_mismatch():
    r = clf().classify(results(nppes="verified", oig_leie="clear", pecos="not_found",
                               fields={"name_mismatch": {"severity": "minor"}}))
    assert r.bucket == "B2"
    assert r.rule_code == "RULE-003"


# ── B3 ───────────────────────────────────────────────────────────────────────

def test_b3_conflicting_sources():
    r = clf().classify(results(nppes="verified", oig_leie="clear", pecos="not_found",
                               fields={"nppes_pecos_conflict": True}))
    assert r.bucket == "B3"
    assert r.rule_code == "RULE-004"


def test_b3_nppes_not_found():
    r = clf().classify(results(nppes="not_found", oig_leie="clear", pecos="verified"))
    assert r.bucket == "B3"


def test_b3_low_confidence():
    r = clf().classify(results(nppes="verified", oig_leie="clear", pecos="not_found",
                               confidence_score=0.3))
    assert r.bucket == "B3"


def test_null_confidence_is_not_below_threshold():
    """Nothing measured is not the same as measured-and-low. A null confidence
    must not trip the <0.5 rule, or every unreachable-source case becomes B3 by
    accident rather than by evidence."""
    c = clf()
    below = c.classify(results(nppes="verified", oig_leie="clear",
                               pecos="not_found", confidence_score=None))
    assert "confidence_below" not in " ".join(below.matched_conditions)


# ── B4 ───────────────────────────────────────────────────────────────────────

def test_b4_oig_excluded():
    """Exclusion outranks everything — B4 even with an otherwise clean record."""
    r = clf().classify(results(nppes="verified", oig_leie="excluded", pecos="verified"))
    assert r.bucket == "B4"
    assert r.rule_code == "RULE-005"


def test_b4_invalid_npi():
    r = clf().classify(results(nppes="verified", oig_leie="clear", pecos="verified",
                               fields={"npi_validation": "invalid"}))
    assert r.bucket == "B4"


def test_b4_sam_debarred():
    r = clf().classify(results(nppes="verified", oig_leie="clear",
                               pecos="verified", sam_gov="debarred"))
    assert r.bucket == "B4"


# ── engine behaviour ─────────────────────────────────────────────────────────

def test_no_match_defaults_b3():
    """An unmatched case must default to manual review, never to a silent pass."""
    r = clf().classify(results(nppes="not_checked", oig_leie="not_checked"))
    assert r.bucket == "B3"
    assert r.rule_code is None
    assert "default" in r.rationale.lower()


def test_rules_priority_order():
    """B4 evaluates FIRST. Disqualifying conditions have to be checked before
    the pass rules, or an excluded/debarred entity that otherwise looks clean
    matches RULE-001 and is reported as B1 — the worst error available here."""
    rules = [dict(r, version=1) for r in SEED_RULES]
    ordered = BucketClassifier(rules=rules)._sorted(rules)
    assert [r["rule_code"] for r in ordered] == [
        "RULE-005", "RULE-001", "RULE-002", "RULE-003", "RULE-004"]


def test_disqualifying_conditions_outrank_a_clean_record():
    """The regression this ordering exists to prevent."""
    c = clf()
    for bad in ({"oig_leie": "excluded"}, {"sam_gov": "debarred"}):
        r = c.classify(results(nppes="verified", pecos="verified",
                               oig_leie=bad.get("oig_leie", "clear"),
                               **{k: v for k, v in bad.items() if k != "oig_leie"}))
        assert r.bucket == "B4", f"{bad} should be B4, got {r.bucket}"


def test_deactivated_rule_skipped():
    active = [dict(r, version=1) for r in SEED_RULES if r["rule_code"] != "RULE-001"]
    r = BucketClassifier(rules=active).classify(
        results(nppes="verified", oig_leie="clear", pecos="verified"))
    assert r.rule_code != "RULE-001"


def test_deterministic():
    payload = results(nppes="verified", oig_leie="clear", pecos="unavailable")
    outs = {(_r.bucket, _r.rule_code, _r.rule_version)
            for _r in (clf().classify(payload) for _ in range(10))}
    assert len(outs) == 1


def test_rule_version_recorded_on_every_classification():
    r = clf().classify(results(nppes="verified", oig_leie="clear", pecos="verified"))
    assert r.rule_version == 1
    assert r.as_dict()["rule_version"] == 1


def test_evidence_summary_separates_the_five_states():
    r = clf().classify(results(nppes="verified", oig_leie="clear",
                               pecos="not_found", sam_gov="unavailable",
                               state_registry="not_checked"))
    ev = r.evidence_summary
    assert ev["sources_unavailable"] == 1
    assert ev["sources_not_checked"] == 1
    assert ev["sources_not_found"] == 1
    # The point of the split: an unreachable source is not counted as checked.
    assert ev["sources_checked"] == 3


# ── sampling ─────────────────────────────────────────────────────────────────

def test_cochran_96000():
    n = CochranSampler().calculate_sample_size(96000)
    assert 380 <= n <= 390, n


def test_cochran_small_population():
    """FPC is what stops a 200-item frame demanding 384."""
    s = CochranSampler()
    with_fpc = s.calculate_sample_size(200, use_fpc=True)
    without = s.calculate_sample_size(200, use_fpc=False)
    assert with_fpc < without
    assert with_fpc <= 200


def test_configurable_confidence():
    s = CochranSampler()
    assert (s.calculate_sample_size(10000, confidence=0.99)
            > s.calculate_sample_size(10000, confidence=0.95)
            > s.calculate_sample_size(10000, confidence=0.90))


def test_configurable_margin_and_proportion():
    s = CochranSampler()
    assert (s.calculate_sample_size(10000, margin=0.03)
            > s.calculate_sample_size(10000, margin=0.05))
    assert (s.calculate_sample_size(10000, proportion=0.5)
            >= s.calculate_sample_size(10000, proportion=0.2))


def test_stratified_proportional():
    pop = [{"id": i, "level": "qhin" if i < 10 else "participant"} for i in range(100)]
    res = CochranSampler().draw_sample(pop, sample_size=20,
                                       strata=lambda x: x["level"], seed=42)
    assert res.sample_size == 20
    assert sum(res.strata_distribution.values()) == 20
    assert res.strata_distribution["qhin"] >= 1


def test_reproducible_seed():
    pop = list(range(500))
    a = CochranSampler().draw_sample(pop, sample_size=25, seed=12345)
    b = CochranSampler().draw_sample(pop, sample_size=25, seed=12345)
    assert a.selected == b.selected


def test_seed_returned_even_when_not_supplied():
    """An unseeded draw would be unreproducible, so a seed is generated and
    reported rather than left implicit."""
    res = CochranSampler().draw_sample(list(range(100)), sample_size=10)
    assert res.random_seed
    again = CochranSampler().draw_sample(list(range(100)), sample_size=10,
                                         seed=res.random_seed)
    assert again.selected == res.selected


def test_config_captured_on_sample():
    res = CochranSampler().draw_sample(list(range(1000)), confidence=0.9,
                                       margin=0.04, proportion=0.3, seed=7)
    cfg = res.config()
    for k in ("confidence_level", "margin_of_error", "proportion", "use_fpc",
              "random_seed", "population_size", "sample_size"):
        assert k in cfg
    assert cfg["confidence_level"] == 0.9 and cfg["random_seed"] == 7


def test_wilson_interval_never_goes_negative():
    """The normal approximation routinely produces a negative lower bound at
    these rates; that is not printable in a federal report."""
    ci = discrepancy_rate_ci(1, 40)
    assert ci["lower"] >= 0.0 and ci["upper"] <= 1.0
    assert ci["method"] == "wilson"


def test_ci_empty_period_is_graceful():
    ci = discrepancy_rate_ci(0, 0)
    assert ci["rate"] is None and "no reviewed items" in ci["note"]


def test_z_values_match_standard_tables():
    assert round(z_for(0.95), 2) == 1.96
    assert round(z_for(0.99), 2) == 2.58


# ── connector semantics regression ───────────────────────────────────────────

class _FakeResult:
    """Mimics SourceResult: success means THE QUERY completed, not the answer."""
    def __init__(self, success=True, data=None, error=None):
        self.success, self.data, self.error = success, data, error


@pytest.mark.asyncio
async def test_leie_success_alone_never_means_excluded(monkeypatch):
    """The regression that matters most.

    OIG LEIE returns SourceResult.ok() whenever the exclusions CSV was
    readable — that is the QUERY succeeding, not a finding. Reading `success`
    as the answer classified every entity whose lookup merely completed as
    EXCLUDED, i.e. B4 and disqualifying. The verdict must come from
    data["excluded"].
    """
    from app.tefca_registry import review_service as svc

    class _Conn:
        async def lookup_by_npi(self, npi):
            # Query fine, entity NOT excluded — the common case.
            return _FakeResult(success=True, data={"excluded": False,
                                                   "exclusion_count": 0})

    class _Mgr:
        nppes = pecos = leie = _Conn()

    monkeypatch.setattr(svc, "SOURCE_LABELS", svc.SOURCE_LABELS)
    import app.Tefca.connectors as conns
    monkeypatch.setattr(conns, "SourceConnectorManager", lambda: _Mgr())

    class _DB:
        async def execute(self, *_a, **_k):
            class R:
                @staticmethod
                def scalar_one_or_none():
                    return "1205839487"
            return R()

    out = await svc.probe_sources(_DB(), "entity")
    assert out["oig_leie"]["status"] == "clear", out["oig_leie"]
    assert out["oig_leie"]["status"] != "excluded"


@pytest.mark.asyncio
async def test_nppes_not_found_is_distinguished_from_verified(monkeypatch):
    """NPPES returns ok() for BOTH found and not-found; only data['found']
    separates them. Without reading it, an NPI absent from the registry would
    be reported as verified."""
    from app.tefca_registry import review_service as svc
    import app.Tefca.connectors as conns

    class _Conn:
        async def lookup_by_npi(self, npi):
            return _FakeResult(success=True, data={"found": False, "npi": npi})

    class _Mgr:
        nppes = pecos = leie = _Conn()

    monkeypatch.setattr(conns, "SourceConnectorManager", lambda: _Mgr())

    class _DB:
        async def execute(self, *_a, **_k):
            class R:
                @staticmethod
                def scalar_one_or_none():
                    return "1234567893"
            return R()

    out = await svc.probe_sources(_DB(), "entity")
    assert out["nppes"]["status"] == "not_found"
    assert out["pecos"]["status"] == "not_found"


def test_sources_without_a_connector_are_disclosed_not_omitted():
    """A source missing from the response reads as an oversight; 'not_checked
    with a reason' is a disclosed gap."""
    from app.tefca_registry.review_service import NO_CONNECTOR
    assert {"sam_gov", "state_registry", "irs"} <= set(NO_CONNECTOR)
    assert all(NO_CONNECTOR[k] for k in NO_CONNECTOR)


# ── version 2: SAM.gov wired in ──────────────────────────────────────────────

from app.tefca_registry.bucket_classifier import SEED_RULES, SEED_RULES_V2  # noqa: E402


@pytest.mark.parametrize("sources", [
    dict(nppes="verified", pecos="verified", oig_leie="clear", sam_gov="not_checked"),
    dict(nppes="verified", pecos="verified", oig_leie="clear", sam_gov="unavailable"),
    dict(nppes="not_found", pecos="verified", oig_leie="clear", sam_gov="not_checked"),
    dict(nppes="verified", pecos="unavailable", oig_leie="clear", sam_gov="unavailable"),
    dict(nppes="verified", pecos="verified", oig_leie="excluded", sam_gov="not_checked"),
])
def test_v2_is_identical_to_v1_when_sam_is_silent(sources):
    """The whole point of the v2 design.

    SAM has no API key in any current environment, so it reports not_checked.
    If v2 changed any bucket under those conditions it would reclassify the
    entire registry on deploy. Every added condition must fire only on a
    positive SAM finding.
    """
    r = results(**sources)
    assert clf().classify(r, rules=SEED_RULES).bucket == \
           clf().classify(r, rules=SEED_RULES_V2).bucket


def test_v2_sends_sam_excluded_to_b4():
    """v1 got this wrong: RULE-005 matched only status 'debarred', but the
    connector emits 'excluded', so a SAM-excluded entity with clean NPPES/PECOS
    fell through to RULE-001 and was reported B1 'No Discrepancy'."""
    r = results(nppes="verified", pecos="verified", oig_leie="clear",
                sam_gov="excluded")
    assert clf().classify(r, rules=SEED_RULES).bucket == "B1"        # the bug
    out = clf().classify(r, rules=SEED_RULES_V2)
    assert out.bucket == "B4"
    assert out.rule_code == "RULE-005"


def test_v2_still_catches_debarred():
    r = results(nppes="verified", pecos="verified", oig_leie="clear",
                sam_gov="debarred")
    assert clf().classify(r, rules=SEED_RULES_V2).bucket == "B4"


def test_v2_sam_excluded_blocks_b2_and_b3_too():
    """A disqualifier that only guards B1 would let the same entity in via the
    administrative-variance rule."""
    r = results(nppes="verified", pecos="verified", oig_leie="clear",
                sam_gov="excluded", fields={"name_mismatch": {"severity": "minor"}})
    assert clf().classify(r, rules=SEED_RULES_V2).bucket == "B4"
