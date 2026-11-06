"""DB-free tests for the PROPOSAL sampling methods. Synthetic frames only."""
import copy
import random

import pytest

from app.tefca_registry import sampling_methods as sm
from app.tefca_registry.sampling_methods import (
    FrameIdentity, SamplingMethodError, SamplingParameters, Unit,
    draw_sample, verify_evidence,
)

# Skew shaped like the aggregate Sept distribution (counts only, synthetic ids).
SIZES = [10794, 7003, 4890, 678, 491, 401, 107, 88, 84, 49, 4]


def frame(sizes=SIZES, shuffle_seed=None):
    units = [Unit(f"U{q:02d}-{i:06d}", f"Q{q:02d}") for q, n in enumerate(sizes, 1) for i in range(n)]
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(units)
    return units


def ident(n, excluded=None):
    excluded = excluded or {}
    return FrameIdentity("synthetic.csv", "2026-09-02", n + sum(excluded.values()), "0" * 64,
                         "synthetic-rules", "v0", excluded, "one synthetic record")


def P(**kw):
    base = dict(confidence=0.95, margin=0.05, proportion=0.5,
                rounding_order=sm.ROUND_CEIL_AT_END, seed=12345)
    base.update(kw)
    return SamplingParameters(**base)


FR = frame()
ID = ident(len(FR))


def test_no_default_method_refused():
    with pytest.raises(SamplingMethodError, match="no default"):
        draw_sample(FR, ID, P())
    with pytest.raises(SamplingMethodError, match="unknown sampling method"):
        draw_sample(FR, ID, P(), "cochran")


def test_floor_requires_explicit_minimum_and_others_reject_it():
    with pytest.raises(SamplingMethodError, match="min_per_stratum"):
        draw_sample(FR, ID, P(), sm.METHOD_FLOOR)
    with pytest.raises(SamplingMethodError, match="only to floor"):
        draw_sample(FR, ID, P(min_per_stratum=30), sm.METHOD_OVERALL)


def test_fpc_and_rounding_order_383_vs_384():
    z = 1.96
    a = sm.cochran_size(94231, z, 0.5, 0.05, sm.ROUND_CEIL_AT_END)
    b = sm.cochran_size(94231, z, 0.5, 0.05, sm.ROUND_CEIL_N0_FIRST)
    assert a["n"] == 383 and abs(a["n_unrounded"] - 382.604263) < 1e-5
    assert b["n"] == 384 and b["n0_used"] == 385.0
    assert sm.cochran_size(24589, z, 0.5, 0.05, sm.ROUND_CEIL_AT_END)["n"] == 379
    # FPC bites: tiny population is capped at N (census)
    assert sm.cochran_size(10, z, 0.5, 0.05, sm.ROUND_CEIL_AT_END)["n"] == 10


def test_overall_proportional_largest_remainder():
    ev = draw_sample(FR, ID, P(), sm.METHOD_OVERALL)
    alloc = [ev["allocation"][f"Q{q:02d}"] for q in range(1, 12)]
    assert sum(alloc) == 379 == len(ev["manifest"])
    assert alloc == [166, 108, 75, 11, 8, 6, 2, 1, 1, 1, 0]
    assert any("n_h=0" in f or "n_h=1" in f for f in ev["precision"]["flags"])


def test_per_qhin_census_and_total():
    ev = draw_sample(FR, ID, P(), sm.METHOD_PER_QHIN)
    assert ev["allocation"]["Q11"] == 4 and ev["precision"]["strata"]["Q11"]["census"]
    assert ev["calculation"]["overall_n"] == sum(ev["allocation"].values()) > 1900
    for q, n in enumerate(SIZES, 1):
        assert ev["allocation"][f"Q{q:02d}"] <= n


def test_floor_matches_agt_illustration_563():
    ev = draw_sample(FR, ID, P(min_per_stratum=30), sm.METHOD_FLOOR)
    assert ev["calculation"]["overall_n"] == 563
    assert ev["allocation"]["Q04"] == 30 and ev["allocation"]["Q11"] == 4
    assert round(ev["precision"]["overall_worst_case_margin"], 4) == 0.0487
    assert abs(ev["precision"]["overall_variance"] - 6.175e-04) < 5e-7


@pytest.mark.parametrize("method,kw", [(sm.METHOD_OVERALL, {}), (sm.METHOD_PER_QHIN, {}),
                                       (sm.METHOD_FLOOR, {"min_per_stratum": 30})])
def test_determinism_and_order_independence(method, kw):
    a = draw_sample(FR, ID, P(**kw), method)
    b = draw_sample(frame(shuffle_seed=7), ID, P(**kw), method)
    assert a["manifest_hash"] == b["manifest_hash"] and a["evidence_hash"] == b["evidence_hash"]
    assert verify_evidence(a, frame(shuffle_seed=9)) == []


def test_seed_change_changes_manifest_and_stratum_seeds_differ():
    a = draw_sample(FR, ID, P(seed=1), sm.METHOD_OVERALL)
    b = draw_sample(FR, ID, P(seed=2), sm.METHOD_OVERALL)
    assert a["manifest_hash"] != b["manifest_hash"]
    assert len(set(a["seed"]["stratum_seeds"].values())) == 11
    # same seed, different method -> different derivation
    c = draw_sample(FR, ID, P(seed=1), sm.METHOD_PER_QHIN)
    assert c["seed"]["stratum_seeds"] != a["seed"]["stratum_seeds"]


def test_manifest_shape_and_unique_units():
    ev = draw_sample(FR, ID, P(), sm.METHOD_OVERALL)
    ids = [e["unit_id"] for e in ev["manifest"]]
    assert len(set(ids)) == len(ids)
    assert {"stratum", "order", "unit_id"} <= set(ev["manifest"][0])
    assert ev["manifest"][0]["order"] == 1


def test_frame_tamper_detected():
    ev = draw_sample(FR, ID, P(), sm.METHOD_OVERALL)
    assert any("frame_hash" in x for x in verify_evidence(ev, FR[:-1]))
    swapped = FR[:-1] + [Unit("X-NEW", FR[-1].stratum)]
    assert any("frame_hash" in x for x in verify_evidence(ev, swapped))


def test_evidence_tamper_detected():
    ev = draw_sample(FR, ID, P(), sm.METHOD_OVERALL)
    t = copy.deepcopy(ev)
    t["manifest"][0]["unit_id"] = "FORGED"
    out = verify_evidence(t, FR)
    assert any("manifest" in x for x in out)
    t2 = copy.deepcopy(ev)
    t2["allocation"]["Q01"] += 1
    assert verify_evidence(t2, FR)


def test_frame_identity_must_reconcile_and_rules_recorded():
    ex = {"inactive": 12, "no_qhin_edge": 3}
    ev = draw_sample(FR, ident(len(FR), ex), P(), sm.METHOD_OVERALL)
    assert ev["frame"]["excluded_by_rule"] == ex and ev["frame"]["row_count"] == len(FR) + 15
    with pytest.raises(SamplingMethodError, match="inconsistent"):
        draw_sample(FR, FrameIdentity("f", "d", len(FR) + 1, "0" * 64, "r", "v", {}, "u"), P(), sm.METHOD_OVERALL)


def test_duplicate_unit_refused():
    with pytest.raises(SamplingMethodError, match="duplicate"):
        draw_sample(FR + [FR[0]], ident(len(FR) + 1), P(), sm.METHOD_OVERALL)


def test_rounding_order_changes_recorded_evidence():
    a = draw_sample(FR, ID, P(), sm.METHOD_OVERALL)
    b = draw_sample(FR, ID, P(rounding_order=sm.ROUND_CEIL_N0_FIRST), sm.METHOD_OVERALL)
    assert a["calculation"]["overall_n"] == 379 and b["calculation"]["overall_n"] == 380
    assert a["calculation"]["overall_cochran"]["n0_used"] == pytest.approx(384.16)
