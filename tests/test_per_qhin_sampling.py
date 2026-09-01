"""Per-QHIN sampling: 95% confidence FOR EACH QHIN, not for the country.

THE CONTRACT REQUIREMENT
────────────────────────
    "representative sample FROM EACH QHIN, at or above 95% confidence"

`docs/TEFCA_COR_Methodology_and_Operational_Readiness.md` §4 records what the
contract fixes (per-QHIN, >=95%) and what AGT proposed for COR confirmation
(margin +/-5%, Cochran with FPC **per QHIN**, simple random seeded selection,
without replacement, and — where a QHIN's population is at or below the computed
size — **review the whole stratum and disclose it**).

WHAT WAS WRONG
──────────────
`CochranSampler.draw_sample` with strata computes ONE sample size from the WHOLE
population and allocates it proportionally. That is correct for a statement
about the population as a whole and WRONG for a statement about each stratum.

Measured against the delivered population — 23,562 promoted records across 11
QHINs at 95%/+/-5%:

    national n, allocated proportionally : 379   (smallest QHIN gets 0)
    per-QHIN independent sizing          : 1,967 (smallest QHIN is a census)

A 3-record QHIN received ZERO selected records while the total still read as a
95% sample. `draw_per_stratum` sizes each stratum against its own N.

THE FORMULA IS UNCHANGED. `draw_per_stratum` calls the same
`calculate_sample_size`, with the same Cochran arithmetic and the same finite
population correction. Only WHAT IT IS APPLIED TO changed, and that is the whole
of the defect.

Fixtures are synthetic throughout; the Government population is read only for
the arithmetic comparison above and is never sampled.
"""

from __future__ import annotations

import math

import pytest

from app.tefca_registry.sampling_engine import CochranSampler, z_for

SYN = "SYNTHETIC-QHIN"


def _population(spec):
    """{stratum: N} -> a flat synthetic population."""
    pop = []
    for key, count in spec.items():
        pop.extend({"id": f"9.99.333.{key}.{i}", "qhin": key}
                   for i in range(count))
    return pop


def _cochran(N, confidence=0.95, margin=0.05, p=0.5, fpc=True):
    """An INDEPENDENT implementation of the formula, for cross-checking."""
    z = z_for(confidence)
    n0 = (z ** 2) * p * (1 - p) / (margin ** 2)
    n = n0 / (1 + (n0 - 1) / N) if fpc else n0
    return max(1, min(N, int(math.ceil(n))))


# ── STEP 3/4 — the methodology parameters are preserved ──────────────────────

def test_the_confidence_default_is_the_contract_floor():
    """95% is contract-fixed. This gate does not reopen it."""
    import inspect

    sig = inspect.signature(CochranSampler().calculate_sample_size)
    assert sig.parameters["confidence"].default == 0.95
    assert sig.parameters["margin"].default == 0.05
    assert sig.parameters["proportion"].default == 0.5
    assert sig.parameters["use_fpc"].default is True
    assert z_for(0.95) == 1.9600


@pytest.mark.parametrize("N", [1, 2, 5, 10, 25, 50, 100, 500, 1000, 10481])
def test_sample_size_matches_an_independent_calculation(N):
    assert CochranSampler().calculate_sample_size(N) == _cochran(N)


@pytest.mark.parametrize("N", [1, 2, 5, 10, 25, 50, 100, 500, 1000])
def test_small_populations_are_sane(N):
    """Never zero when review is required, never negative, never above N."""
    n = CochranSampler().calculate_sample_size(N)
    assert 1 <= n <= N


def test_the_finite_population_correction_is_material_and_on():
    s = CochranSampler()
    assert s.calculate_sample_size(200, use_fpc=True) < \
        s.calculate_sample_size(200, use_fpc=False)
    # Without FPC every large population demands the same ~384.
    assert s.calculate_sample_size(96000, use_fpc=False) == _cochran(
        96000, fpc=False)


def test_the_sample_size_rounds_up():
    """Rounding down would quietly widen the interval past the stated margin."""
    N = 300
    z = z_for(0.95)
    n0 = (z ** 2) * 0.25 / (0.05 ** 2)
    exact = n0 / (1 + (n0 - 1) / N)
    assert CochranSampler().calculate_sample_size(N) == math.ceil(exact)


# ── STEP 7 — per-QHIN independence, the heart of this gate ───────────────────

QHIN_SPEC = {"QHIN-A": 60, "QHIN-B": 400, "QHIN-C": 3000}


def test_each_qhin_is_sized_against_its_own_population():
    pop = _population(QHIN_SPEC)
    result = CochranSampler().draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"], seed=4242)

    for key, N in QHIN_SPEC.items():
        expected = _cochran(N)
        assert result.stratum_sizing[key]["population_size"] == N
        assert result.stratum_sizing[key]["sample_size"] == expected, (
            f"{key} (N={N}) must be sized against its OWN population")
        assert result.strata_distribution[key] == expected
    assert result.sample_size == sum(_cochran(N) for N in QHIN_SPEC.values())


def test_a_national_sample_would_understate_every_small_qhin():
    """The defect this gate exists to close, stated as an assertion."""
    pop = _population(QHIN_SPEC)
    sampler = CochranSampler()

    national = sampler.draw_sample(pop, strata=lambda x: x["qhin"], seed=1)
    per_qhin = sampler.draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"], seed=1)

    assert per_qhin.sample_size > national.sample_size
    smallest = "QHIN-A"
    assert per_qhin.strata_distribution[smallest] == _cochran(QHIN_SPEC[smallest])
    assert national.strata_distribution[smallest] < \
        per_qhin.strata_distribution[smallest], (
        "proportional allocation under-samples the smallest stratum, which is "
        "precisely what a per-QHIN confidence requirement forbids")


def test_a_qhin_too_small_to_sample_is_a_census_and_says_so():
    """Approved methodology: review the whole stratum and disclose it."""
    pop = _population({"TINY": 3, "BIG": 5000})
    result = CochranSampler().draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"], seed=7)

    assert result.stratum_sizing["TINY"]["sample_size"] == 3
    assert result.stratum_sizing["TINY"]["census"] is True
    assert result.stratum_sizing["BIG"]["census"] is False
    assert result.strata_distribution["TINY"] == 3


# ── STEP 24 — no cross-QHIN leakage ──────────────────────────────────────────

def test_no_record_is_selected_for_the_wrong_qhin():
    pop = _population(QHIN_SPEC)
    result = CochranSampler().draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"], seed=99)

    by_stratum = {}
    for item in result.selected:
        by_stratum.setdefault(item["qhin"], []).append(item)
    for key, members in by_stratum.items():
        assert all(m["qhin"] == key for m in members)
        assert len(members) == result.strata_distribution[key]
    # One QHIN cannot satisfy another's count.
    assert set(by_stratum) == set(QHIN_SPEC)


def test_adding_a_qhin_does_not_change_another_qhins_sample():
    """Per-stratum RNG: a stratum's selection must not depend on its neighbours."""
    sampler = CochranSampler()
    before = sampler.draw_per_stratum(
        _population({"QHIN-A": 60, "QHIN-B": 400}),
        stratum_of=lambda x: x["qhin"], seed=555)
    after = sampler.draw_per_stratum(
        _population({"QHIN-A": 60, "QHIN-B": 400, "QHIN-Z": 900}),
        stratum_of=lambda x: x["qhin"], seed=555)

    a_before = sorted(i["id"] for i in before.selected if i["qhin"] == "QHIN-A")
    a_after = sorted(i["id"] for i in after.selected if i["qhin"] == "QHIN-A")
    assert a_before == a_after, (
        "adding a QHIN silently re-drew another QHIN's sample")


# ── STEP 25 — uniqueness ─────────────────────────────────────────────────────

def test_no_record_is_selected_twice():
    pop = _population(QHIN_SPEC)
    result = CochranSampler().draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"], seed=31337)
    ids = [i["id"] for i in result.selected]
    assert len(ids) == len(set(ids))
    # Without replacement: every selection came from the frame.
    frame = {i["id"] for i in pop}
    assert set(ids) <= frame


# ── STEPS 17/26 — reproducibility and rerun ──────────────────────────────────

def test_the_same_seed_reproduces_the_same_sample():
    pop = _population(QHIN_SPEC)
    sampler = CochranSampler()
    a = sampler.draw_per_stratum(pop, stratum_of=lambda x: x["qhin"], seed=2026)
    b = sampler.draw_per_stratum(pop, stratum_of=lambda x: x["qhin"], seed=2026)
    assert [i["id"] for i in a.selected] == [i["id"] for i in b.selected]
    assert a.strata_distribution == b.strata_distribution


def test_a_different_seed_gives_a_different_sample_of_the_same_size():
    pop = _population(QHIN_SPEC)
    sampler = CochranSampler()
    a = sampler.draw_per_stratum(pop, stratum_of=lambda x: x["qhin"], seed=1)
    b = sampler.draw_per_stratum(pop, stratum_of=lambda x: x["qhin"], seed=2)
    assert a.sample_size == b.sample_size
    assert [i["id"] for i in a.selected] != [i["id"] for i in b.selected]


# ── STEP 16/18 — the seed is generated and returned, never absent ────────────

def test_an_unseeded_draw_still_returns_the_seed_it_used():
    """A sample nobody can re-draw cannot be checked by a reviewer."""
    pop = _population({"QHIN-A": 100})
    result = CochranSampler().draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"])
    assert isinstance(result.random_seed, int) and result.random_seed > 0

    replay = CochranSampler().draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"], seed=result.random_seed)
    assert [i["id"] for i in replay.selected] == [i["id"] for i in result.selected]


def test_the_seed_is_generated_from_a_system_source_not_the_clock():
    """A clock-derived seed is guessable, so a sample could be steered."""
    import inspect

    src = inspect.getsource(CochranSampler.draw_per_stratum)
    assert "random.SystemRandom()" in src
    for forbidden in ("time.time", "datetime.now", "utcnow"):
        assert forbidden not in src


# ── STEP 42 — the sample carries its own audit record ────────────────────────

def test_every_parameter_needed_to_reconstruct_the_sample_is_returned():
    pop = _population(QHIN_SPEC)
    result = CochranSampler().draw_per_stratum(
        pop, stratum_of=lambda x: x["qhin"], seed=8080,
        strata_config={"stratify_by": "qhin"})
    config = result.config()

    for key in ("population_size", "sample_size", "confidence_level",
                "margin_of_error", "proportion", "use_fpc", "random_seed",
                "strata_config", "strata_distribution", "stratum_sizing"):
        assert key in config, f"{key} missing from the reproducible config"

    assert config["confidence_level"] == 0.95
    assert config["random_seed"] == 8080
    assert config["strata_config"]["allocation"] == "per_stratum_independent"
    assert config["strata_config"]["stratify_by"] == "qhin"
    for key, N in QHIN_SPEC.items():
        assert config["stratum_sizing"][key]["population_size"] == N


def test_the_allocation_method_is_recorded_so_the_two_are_never_confused():
    pop = _population(QHIN_SPEC)
    sampler = CochranSampler()
    per = sampler.draw_per_stratum(pop, stratum_of=lambda x: x["qhin"], seed=5)
    nat = sampler.draw_sample(pop, strata=lambda x: x["qhin"], seed=5,
                              strata_config={"stratify_by": "qhin"})
    assert per.strata_config["allocation"] == "per_stratum_independent"
    assert (nat.strata_config or {}).get("allocation") is None, (
        "the national/proportional draw must not claim per-stratum allocation")


# ── the existing national draw is untouched ──────────────────────────────────

def test_the_existing_proportional_draw_still_behaves_as_before():
    """`draw_sample` serves a different question and was not modified."""
    pop = _population(QHIN_SPEC)
    sampler = CochranSampler()
    result = sampler.draw_sample(pop, strata=lambda x: x["qhin"], seed=17)
    total = sum(QHIN_SPEC.values())
    assert result.sample_size == sampler.calculate_sample_size(total)
    assert sum(result.strata_distribution.values()) == result.sample_size


def test_fixtures_are_synthetic_only():
    for item in _population(QHIN_SPEC):
        assert item["id"].startswith("9.99.333.")
        assert item["qhin"].startswith("QHIN-")
