"""Cochran sample-size calculation and reproducible sample drawing.

Every statistical parameter is an argument with a stated default — none are
baked into the arithmetic. A sample drawn today has to be defensible in a year,
which means the report must be able to say which confidence level, margin,
proportion and seed produced it, not "the defaults at the time".

Two choices worth stating:

* The finite population correction is ON by default. Without it a population of
  96,000 and a population of 900 both demand ~384, which is right for the former
  and absurd for the latter. FPC is what makes the number honest for small
  frames.
* Drawing is seeded and the seed is returned. An unseeded sample cannot be
  re-drawn, so a reviewer cannot check the work — and "trust me, it was random"
  is not evidence.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

# z for common two-sided confidence levels. Looked up rather than computed so
# the engine needs no scipy; anything else falls back to an inverse-normal
# approximation.
_Z = {0.80: 1.2816, 0.85: 1.4395, 0.90: 1.6449, 0.95: 1.9600,
      0.98: 2.3263, 0.99: 2.5758}


def z_for(confidence: float) -> float:
    """Two-sided z score. Exact for the common levels, approximated otherwise."""
    key = round(confidence, 4)
    if key in _Z:
        return _Z[key]
    # Acklam's inverse normal CDF approximation — adequate at the precision a
    # sample size is quoted to.
    p = 1 - (1 - confidence) / 2
    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.3577518672690, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572]
    c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996,
         3.754408661907416]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


@dataclass
class SampleResult:
    sample_size: int
    population_size: int
    selected: List[Any]
    confidence_level: float
    margin_of_error: float
    proportion: float
    use_fpc: bool
    random_seed: int
    strata_config: Optional[dict] = None
    strata_distribution: Dict[str, int] = field(default_factory=dict)
    #: Per-stratum N and n. Populated only by `draw_per_stratum`, where each
    #: stratum has its own calculation to show; empty for a single national draw.
    stratum_sizing: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def config(self) -> dict:
        """Everything needed to redraw this exact sample."""
        return {
            "population_size": self.population_size,
            "sample_size": self.sample_size,
            "confidence_level": self.confidence_level,
            "margin_of_error": self.margin_of_error,
            "proportion": self.proportion,
            "use_fpc": self.use_fpc,
            "random_seed": self.random_seed,
            "strata_config": self.strata_config,
            "strata_distribution": self.strata_distribution,
            "stratum_sizing": self.stratum_sizing,
        }


class CochranSampler:
    """Cochran's formula with optional finite population correction."""

    def calculate_sample_size(self, population_size: int, confidence: float = 0.95,
                              margin: float = 0.05, proportion: float = 0.5,
                              use_fpc: bool = True) -> int:
        if population_size <= 0:
            return 0
        if not 0 < confidence < 1:
            raise ValueError("confidence must be between 0 and 1")
        if not 0 < margin < 1:
            raise ValueError("margin must be between 0 and 1")
        if not 0 <= proportion <= 1:
            raise ValueError("proportion must be between 0 and 1")

        z = z_for(confidence)
        n0 = (z ** 2) * proportion * (1 - proportion) / (margin ** 2)
        n = n0 / (1 + (n0 - 1) / population_size) if use_fpc else n0
        # Round UP: rounding a sample size down quietly widens the interval
        # beyond the margin that was asked for.
        return max(1, min(population_size, int(math.ceil(n))))

    def draw_sample(self, population: Sequence[Any], sample_size: Optional[int] = None,
                    strata: Optional[Callable[[Any], str]] = None,
                    seed: Optional[int] = None, confidence: float = 0.95,
                    margin: float = 0.05, proportion: float = 0.5,
                    use_fpc: bool = True,
                    strata_config: Optional[dict] = None) -> SampleResult:
        pop = list(population)
        n_pop = len(pop)
        if sample_size is None:
            sample_size = self.calculate_sample_size(
                n_pop, confidence=confidence, margin=margin,
                proportion=proportion, use_fpc=use_fpc)
        sample_size = max(0, min(sample_size, n_pop))

        # Generate a seed when none is given, and RETURN it. An unseeded draw
        # cannot be reproduced, so a reviewer could never check the selection.
        if seed is None:
            seed = random.SystemRandom().randint(1, 2 ** 31 - 1)
        rng = random.Random(seed)

        if strata is None or sample_size == 0:
            selected = rng.sample(pop, sample_size) if sample_size else []
            dist: Dict[str, int] = {}
        else:
            groups: Dict[str, list] = {}
            for item in pop:
                groups.setdefault(str(strata(item)), []).append(item)

            # Proportional allocation, largest-remainder. Plain rounding either
            # over- or under-fills the total; the remainder pass makes the parts
            # sum to exactly sample_size.
            quotas, remainders = {}, []
            for key, members in sorted(groups.items()):
                exact = sample_size * len(members) / n_pop
                base = int(math.floor(exact))
                quotas[key] = min(base, len(members))
                remainders.append((exact - base, key))
            leftover = sample_size - sum(quotas.values())
            for _frac, key in sorted(remainders, reverse=True):
                if leftover <= 0:
                    break
                if quotas[key] < len(groups[key]):
                    quotas[key] += 1
                    leftover -= 1

            selected, dist = [], {}
            for key in sorted(groups):
                take = quotas.get(key, 0)
                if take:
                    selected.extend(rng.sample(groups[key], take))
                dist[key] = take

        return SampleResult(
            sample_size=len(selected), population_size=n_pop, selected=selected,
            confidence_level=confidence, margin_of_error=margin,
            proportion=proportion, use_fpc=use_fpc, random_seed=seed,
            strata_config=strata_config, strata_distribution=dist,
        )


    def draw_per_stratum(self, population: Sequence[Any],
                         stratum_of: Callable[[Any], str],
                         seed: Optional[int] = None, confidence: float = 0.95,
                         margin: float = 0.05, proportion: float = 0.5,
                         use_fpc: bool = True,
                         strata_config: Optional[dict] = None) -> "SampleResult":
        """A sample sized INDEPENDENTLY for every stratum.

        WHY THIS EXISTS ALONGSIDE `draw_sample`
        ───────────────────────────────────────
        `draw_sample` computes ONE sample size from the WHOLE population and
        then allocates it across strata proportionally. That is correct when the
        confidence statement is about the population as a whole.

        It is NOT correct when confidence is required OF EACH STRATUM. Measured
        against the delivered population — 23,562 records across 11 QHINs at
        95%/±5% — proportional allocation gives the smallest QHIN (3 records)
        ZERO selected records, and three others a single record each, while the
        total (379) still reads as a 95% sample. Per-QHIN sizing gives 1,967 and
        a census of the 3-record stratum.

        So this method calls `calculate_sample_size` once per stratum, against
        that stratum's own N. The formula is untouched — the same Cochran with
        the same finite-population correction. What changes is WHAT IT IS
        APPLIED TO, which is the whole of the difference.

        Where a stratum's computed size reaches its population, the whole
        stratum is taken. That is a census, and it is what the approved
        methodology asks for: "where a QHIN's population is at or below the
        computed size, review the whole stratum and disclose it". A 3-record
        QHIN cannot be sampled.
        """
        pop = list(population)
        n_pop = len(pop)

        # One seed for the whole draw, generated here when absent and RETURNED,
        # so the entire multi-stratum selection is reproducible from one value.
        if seed is None:
            seed = random.SystemRandom().randint(1, 2 ** 31 - 1)

        groups: Dict[str, list] = {}
        for item in pop:
            groups.setdefault(str(stratum_of(item)), []).append(item)

        selected: list = []
        dist: Dict[str, int] = {}
        sizing: Dict[str, Dict[str, Any]] = {}
        for key in sorted(groups):
            members = groups[key]
            stratum_n = self.calculate_sample_size(
                len(members), confidence=confidence, margin=margin,
                proportion=proportion, use_fpc=use_fpc)
            # A separate RNG per stratum, derived from the run seed and the
            # stratum key. One shared generator would make every stratum's
            # selection depend on how many were drawn before it, so adding a
            # QHIN would silently change another QHIN's sample.
            rng = random.Random(f"{seed}:{key}")
            take = min(stratum_n, len(members))
            selected.extend(rng.sample(members, take))
            dist[key] = take
            sizing[key] = {
                "population_size": len(members),
                "sample_size": take,
                "census": take == len(members),
            }

        result = SampleResult(
            sample_size=len(selected), population_size=n_pop, selected=selected,
            confidence_level=confidence, margin_of_error=margin,
            proportion=proportion, use_fpc=use_fpc, random_seed=seed,
            strata_config={**(strata_config or {}),
                           "allocation": "per_stratum_independent"},
            strata_distribution=dist,
            # Per-stratum N and n, so a reviewer can check each stratum's own
            # calculation rather than only the total.
            stratum_sizing=sizing,
        )
        return result


def discrepancy_rate_ci(successes: int, n: int, confidence: float = 0.95) -> dict:
    """Wilson score interval for an observed discrepancy rate.

    Wilson rather than the normal approximation on purpose: at the small samples
    and low rates this work produces, the normal interval routinely runs below
    zero, which is not a defensible thing to print in a federal report.
    """
    if n <= 0:
        return {"rate": None, "lower": None, "upper": None,
                "method": "wilson", "confidence": confidence, "n": 0,
                "note": "no reviewed items in period; no interval computable"}
    z = z_for(confidence)
    p = successes / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return {
        "rate": round(p, 6),
        "lower": round(max(0.0, centre - half), 6),
        "upper": round(min(1.0, centre + half), 6),
        "method": "wilson", "confidence": confidence, "n": n,
        "successes": successes,
    }
