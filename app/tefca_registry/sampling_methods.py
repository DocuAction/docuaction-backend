"""PROPOSAL (AGT, not COR-accepted): reproducible, auditable sampling methods.

Pure module: no database, no I/O, no imports from the deployed sampling path.
It does NOT change app/tefca_registry/sampling_engine.py or qhin_sampling.py.

Task 2 leaves the method unresolved, so this module has NO default method.
A call without an explicit, known method name is refused. Every parameter
that changes the numbers (confidence, margin, proportion, rounding order,
seed, and for the floor method the minimum) is also mandatory.

Methods (all produce the same evidence structure):
  overall_proportional   one overall Cochran+FPC n over the whole frame,
                         largest-remainder proportional allocation to strata
                         (Task 2 text; 383 at N=94,231, 95%, +/-5%).
  per_qhin_cochran       an independent Cochran+FPC n per stratum; strata
                         whose n reaches N_h are censused (the deployed
                         official path's design, re-expressed here, not
                         imported).
  floor_allocation       overall n, proportional, then
                         n_h = min(N_h, max(prop_h, min_per_stratum))
                         (AGT's proposed floor; 563 / +/-4.87% at floor 30
                         on the Sept aggregate counts).

Selection is portable and recomputable in any language: each unit gets the
key sha256("{stratum_seed}:{unit_id}") and the n_h lowest keys are taken.
The stratum seed is the first 16 hex of sha256("{seed}:{method}:{stratum}").
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ENGINE_VERSION = "sampling_methods/0.1.0-proposal"

METHOD_OVERALL = "overall_proportional"
METHOD_PER_QHIN = "per_qhin_cochran"
METHOD_FLOOR = "floor_allocation"
METHODS = (METHOD_OVERALL, METHOD_PER_QHIN, METHOD_FLOOR)

ROUND_CEIL_AT_END = "ceil_at_end"      # n0 unrounded through FPC, ceil once
ROUND_CEIL_N0_FIRST = "ceil_n0_first"  # n0 ceiled before FPC
ROUNDING_ORDERS = (ROUND_CEIL_AT_END, ROUND_CEIL_N0_FIRST)

_Z = {0.90: 1.6449, 0.95: 1.96, 0.99: 2.5758}


class SamplingMethodError(ValueError):
    """Refusal: a missing/unknown method or parameter, or a failed check."""


@dataclass(frozen=True)
class Unit:
    unit_id: str
    stratum: str


@dataclass(frozen=True)
class FrameIdentity:
    file_name: str
    extract_date: str
    row_count: int                     # rows in the file before eligibility
    file_sha256: str
    eligibility_rule_set: str
    eligibility_rule_version: str
    excluded_by_rule: Mapping[str, int]
    unit_definition: str


@dataclass(frozen=True)
class SamplingParameters:
    confidence: float
    margin: float
    proportion: float
    rounding_order: str
    seed: int
    min_per_stratum: Optional[int] = None   # required iff floor_allocation


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sorted_frame(units: Iterable[Unit]) -> List[Unit]:
    """Deterministic frame order: (stratum, unit_id). Duplicates refused."""
    out = sorted(units, key=lambda u: (u.stratum, u.unit_id))
    ids = [u.unit_id for u in out]
    if len(set(ids)) != len(ids):
        raise SamplingMethodError("duplicate unit_id in frame")
    return out


def frame_hash(units: Iterable[Unit]) -> str:
    return _sha("\n".join(f"{u.stratum}\t{u.unit_id}" for u in sorted_frame(units)))


def stratum_seed(seed: int, method: str, stratum: str) -> str:
    return _sha(f"{seed}:{method}:{stratum}")[:16]


def _z(conf: float) -> float:
    try:
        return _Z[round(conf, 4)]
    except KeyError:
        raise SamplingMethodError(f"unsupported confidence {conf}; use one of {sorted(_Z)}")


def cochran_size(N: int, z: float, p: float, e: float, rounding_order: str) -> dict:
    """Cochran n0 with finite-population correction, rounding order explicit."""
    if N <= 0:
        return {"N": N, "n0": 0.0, "n0_used": 0.0, "n_unrounded": 0.0, "n": 0}
    n0 = z * z * p * (1 - p) / (e * e)
    n0_used = float(math.ceil(n0)) if rounding_order == ROUND_CEIL_N0_FIRST else n0
    n_un = N * n0_used / (N + n0_used - 1)
    n = max(1, min(N, math.ceil(n_un - 1e-12)))
    return {"N": N, "n0": n0, "n0_used": n0_used, "n_unrounded": n_un, "n": n}


def largest_remainder(total: int, sizes: Mapping[str, int]) -> Tuple[Dict[str, int], List[dict]]:
    """Floor each total*N_h/N, hand leftovers to the largest fractions, ties by
    stratum id ascending. Never exceeds N_h. Returns (alloc, trace)."""
    N = sum(sizes.values())
    exact = {k: total * v / N for k, v in sizes.items()}
    alloc = {k: min(sizes[k], math.floor(exact[k] + 1e-12)) for k in sizes}
    left = total - sum(alloc.values())
    order = sorted(sizes, key=lambda k: (-(exact[k] - math.floor(exact[k] + 1e-12)), k))
    i = 0
    while left > 0 and i < 10 * len(order) + 10:
        k = order[i % len(order)]
        if alloc[k] < sizes[k]:
            alloc[k] += 1
            left -= 1
        i += 1
    trace = [{"stratum": k, "exact": exact[k], "floor": math.floor(exact[k] + 1e-12),
              "final": alloc[k]} for k in sorted(sizes)]
    return alloc, trace


def _require(params: SamplingParameters, method: Optional[str]) -> None:
    if not method:
        raise SamplingMethodError(
            "no sampling method given: Task 2 leaves the method unresolved; "
            f"pass one of {list(METHODS)} explicitly (there is no default)")
    if method not in METHODS:
        raise SamplingMethodError(f"unknown sampling method {method!r}; expected one of {list(METHODS)}")
    if params is None:
        raise SamplingMethodError("parameters are required")
    if params.rounding_order not in ROUNDING_ORDERS:
        raise SamplingMethodError(f"rounding_order must be one of {list(ROUNDING_ORDERS)}")
    if not (0 < params.margin < 1 and 0 < params.proportion < 1):
        raise SamplingMethodError("margin and proportion must be in (0,1)")
    if not isinstance(params.seed, int) or isinstance(params.seed, bool):
        raise SamplingMethodError("seed must be an explicit integer")
    _z(params.confidence)
    if method == METHOD_FLOOR:
        if params.min_per_stratum is None or params.min_per_stratum < 1:
            raise SamplingMethodError("floor_allocation requires an explicit min_per_stratum >= 1 (COR decision)")
    elif params.min_per_stratum is not None:
        raise SamplingMethodError("min_per_stratum applies only to floor_allocation")


def _allocate(method: str, sizes: Mapping[str, int], z: float, p: SamplingParameters) -> dict:
    N = sum(sizes.values())
    calc: dict = {"method": method, "N": N, "z": z, "rounding_order": p.rounding_order,
                  "formula": "n0=z^2*p*(1-p)/e^2; n=N*n0/(N+n0-1); ceil applied "
                             + ("once at the end" if p.rounding_order == ROUND_CEIL_AT_END
                                else "to n0 first, then at the end")}
    if method == METHOD_PER_QHIN:
        per = {k: cochran_size(v, z, p.proportion, p.margin, p.rounding_order) for k, v in sizes.items()}
        calc["per_stratum_cochran"] = per
        alloc = {k: per[k]["n"] for k in sizes}
        calc["overall_n"] = sum(alloc.values())
        calc["census_rule"] = "n_h = N_h whenever Cochran n_h >= N_h"
        return {"alloc": alloc, "calc": calc}
    overall = cochran_size(N, z, p.proportion, p.margin, p.rounding_order)
    calc["overall_cochran"] = overall
    prop, trace = largest_remainder(overall["n"], sizes)
    calc["proportional_trace"] = trace
    calc["proportional_alloc"] = prop
    if method == METHOD_FLOOR:
        m = p.min_per_stratum
        alloc = {k: min(sizes[k], max(prop[k], m)) for k in sizes}
        calc["min_per_stratum"] = m
        calc["floor_rule"] = "n_h = min(N_h, max(proportional_h, min_per_stratum)); N_h <= floor is a census"
    else:
        alloc = dict(prop)
    calc["overall_n"] = sum(alloc.values())
    return {"alloc": alloc, "calc": calc}


def _precision(sizes: Mapping[str, int], alloc: Mapping[str, int], z: float, pr: float) -> dict:
    N = sum(sizes.values())
    var = 0.0
    rows, flags = {}, []
    for k in sorted(sizes):
        Nh, nh = sizes[k], alloc[k]
        census = nh >= Nh
        if census:
            m, term = 0.0, 0.0
        elif nh >= 2:
            m = z * math.sqrt(pr * (1 - pr) / nh * (1 - nh / Nh))
            term = (Nh / N) ** 2 * (1 - nh / Nh) * pr * (1 - pr) / (nh - 1)
        else:
            m, term = None, 0.0
            flags.append(f"{k}: n_h={nh} < 2, no variance estimate (collapse or report separately)")
        var += term
        rows[k] = {"N_h": Nh, "n_h": nh, "census": census, "worst_case_margin": m,
                   "variance_term": term, "weight_N_over_n": (Nh / nh) if nh else None}
    return {"overall_variance": var, "overall_worst_case_margin": z * math.sqrt(var),
            "strata": rows, "flags": flags,
            "assumptions": "worst case p; SRS within stratum; full response; design effect 1"}


def _select(units: Sequence[Unit], alloc: Mapping[str, int], method: str, seed: int):
    by: Dict[str, List[str]] = {}
    for u in units:
        by.setdefault(u.stratum, []).append(u.unit_id)
    seeds, entries = {}, []
    for k in sorted(by):
        ss = stratum_seed(seed, method, k)
        seeds[k] = ss
        ranked = sorted(by[k], key=lambda uid: (_sha(f"{ss}:{uid}"), uid))
        for order, uid in enumerate(ranked[: alloc[k]], start=1):
            entries.append({"stratum": k, "order": order, "unit_id": uid})
    return seeds, entries


def draw_sample(units: Iterable[Unit], identity: FrameIdentity, params: SamplingParameters,
                method: Optional[str] = None) -> dict:
    """Return the evidence document (JSON-serialisable). No default method."""
    _require(params, method)
    frame = sorted_frame(units)
    if not frame:
        raise SamplingMethodError("empty frame")
    excl = sum(identity.excluded_by_rule.values())
    if identity.row_count - excl != len(frame):
        raise SamplingMethodError(
            f"frame identity inconsistent: row_count {identity.row_count} - excluded {excl} "
            f"!= eligible units {len(frame)}")
    sizes: Dict[str, int] = {}
    for u in frame:
        sizes[u.stratum] = sizes.get(u.stratum, 0) + 1
    z = _z(params.confidence)
    a = _allocate(method, sizes, z, params)
    seeds, entries = _select(frame, a["alloc"], method, params.seed)
    ev = {
        "engine_version": ENGINE_VERSION,
        "status": "PROPOSAL - AGT, not COR-accepted",
        "method": method,
        "frame": {
            "file_name": identity.file_name, "extract_date": identity.extract_date,
            "row_count": identity.row_count, "file_sha256": identity.file_sha256,
            "eligibility_rule_set": identity.eligibility_rule_set,
            "eligibility_rule_version": identity.eligibility_rule_version,
            "excluded_by_rule": dict(identity.excluded_by_rule),
            "eligible_count": len(frame), "unit_definition": identity.unit_definition,
            "frame_order": "(stratum, unit_id) ascending", "frame_hash": frame_hash(frame),
            "stratum_sizes": sizes,
        },
        "parameters": {"confidence": params.confidence, "margin": params.margin,
                       "proportion": params.proportion, "rounding_order": params.rounding_order,
                       "min_per_stratum": params.min_per_stratum},
        "seed": {"seed": params.seed,
                 "derivation": "stratum_seed=sha256('{seed}:{method}:{stratum}')[:16]; "
                               "unit key=sha256('{stratum_seed}:{unit_id}'); lowest n_h keys",
                 "stratum_seeds": seeds},
        "calculation": a["calc"],
        "allocation": a["alloc"],
        "precision": _precision(sizes, a["alloc"], z, params.proportion),
        "manifest": entries,
    }
    ev["manifest_hash"] = _sha(_canon(entries))
    ev["evidence_hash"] = _sha(_canon({k: v for k, v in ev.items() if k != "evidence_hash"}))
    return ev


def verify_evidence(evidence: Mapping, units: Iterable[Unit]) -> List[str]:
    """Recompute everything from the evidence's own inputs and the supplied
    frame. Returns discrepancies; an empty list means it reproduces exactly."""
    bad: List[str] = []
    try:
        body = {k: v for k, v in evidence.items() if k != "evidence_hash"}
        if _sha(_canon(body)) != evidence.get("evidence_hash"):
            bad.append("evidence_hash mismatch (evidence edited)")
        if _sha(_canon(evidence["manifest"])) != evidence.get("manifest_hash"):
            bad.append("manifest_hash mismatch (manifest edited)")
        fr = evidence["frame"]
        units = list(units)
        if frame_hash(units) != fr["frame_hash"]:
            bad.append("frame_hash mismatch (frame differs from the pinned population)")
        pm = evidence["parameters"]
        params = SamplingParameters(pm["confidence"], pm["margin"], pm["proportion"],
                                    pm["rounding_order"], evidence["seed"]["seed"],
                                    pm.get("min_per_stratum"))
        ident = FrameIdentity(fr["file_name"], fr["extract_date"], fr["row_count"],
                              fr["file_sha256"], fr["eligibility_rule_set"],
                              fr["eligibility_rule_version"], fr["excluded_by_rule"],
                              fr["unit_definition"])
        rc = draw_sample(units, ident, params, evidence["method"])
        for key in ("allocation", "calculation", "precision", "seed", "manifest"):
            if rc[key] != evidence[key]:
                bad.append(f"{key} does not recompute")
    except SamplingMethodError as e:
        bad.append(f"recompute refused: {e}")
    except (KeyError, TypeError) as e:
        bad.append(f"evidence malformed: {e!r}")
    return bad


def evidence_to_json(evidence: Mapping) -> str:
    return json.dumps(evidence, indent=2, sort_keys=True)
