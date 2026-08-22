"""B1-B4 discrepancy classification against versioned, DB-driven rules.

WHY RULES LIVE IN THE DATABASE
    ONC guidance changes. A hardcoded classifier means every change is a code
    deploy, and — worse — old classifications silently start meaning something
    new. Rules are rows, versioned, and every classification records the
    rule_code AND rule_version that produced it, so a review from Q3 stays
    explainable after the rule is retired in Q4.

THE FIVE VERIFICATION STATES
    verified | not_found | not_checked | unavailable | failed

    The engine treats these as genuinely different, because the statistics
    depend on it. `unavailable` (source unreachable) must never count against an
    entity; `not_found` (source reached, no record) must. Collapsing them into
    pass/fail converts a third party's outage into a finding against a provider,
    which is a false accusation rather than a low score.

DETERMINISM
    Same inputs, same output, always. Rules evaluate in priority order and the
    FIRST match wins; ties break on rule_code so ordering cannot depend on how
    the database happened to return rows. No match defaults to B3
    (Inexplicable) — the honest bucket for "the rules do not describe this",
    rather than silently passing it as B1.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── verification state vocabulary ────────────────────────────────────────────
VERIFIED = "verified"
NOT_FOUND = "not_found"
NOT_CHECKED = "not_checked"
UNAVAILABLE = "unavailable"
FAILED = "failed"

VERIFICATION_STATES = (VERIFIED, NOT_FOUND, NOT_CHECKED, UNAVAILABLE, FAILED)

#: States meaning "the source told us nothing". Excluded from scoring and from
#: discrepancy counts — an outage is not evidence about the entity.
NO_SIGNAL_STATES = frozenset({NOT_CHECKED, UNAVAILABLE, FAILED})

DEFAULT_BUCKET = "B3"
CACHE_TTL_SECONDS = 3600


@dataclass
class ClassificationResult:
    bucket: str
    rule_code: Optional[str]
    rule_version: Optional[int]
    rule_name: Optional[str]
    rationale: str
    matched_conditions: List[str] = field(default_factory=list)
    evidence_summary: Dict[str, Any] = field(default_factory=dict)
    evaluated_rules: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "rule_name": self.rule_name,
            "rationale": self.rationale,
            "matched_conditions": self.matched_conditions,
            "evidence_summary": self.evidence_summary,
            "evaluated_rules": self.evaluated_rules,
        }


# ── seed rules (version 1) ───────────────────────────────────────────────────
# Seeded into review_rules on first use. Editing these does NOT change a
# deployed rule set: seeding is skipped when rows already exist, and a genuine
# change goes through the versioning API so the old text survives.
SEED_RULES: List[dict] = [
    {
        "rule_code": "RULE-001", "name": "B1 No Discrepancy", "bucket": "B1",
        "priority": 10,
        "description": "Every required authoritative source was reached and confirmed "
                       "the entity, and the NPI passed its check digit.",
        "conditions": {
            "all_of": [
                {"source": "nppes", "status": "verified"},
                {"source": "oig_leie", "status": "clear"},
                {"source": "pecos", "status": "verified"},
            ],
            "none_of": [{"field": "npi_validation", "status": "flagged"}],
        },
    },
    {
        "rule_code": "RULE-002", "name": "B1 Partial Pass", "bucket": "B1",
        "priority": 20,
        "description": "The sources that answered all confirmed the entity; the "
                       "remainder were unreachable. An outage is not a discrepancy.",
        "conditions": {
            "all_of": [
                {"source": "nppes", "status": "verified"},
                {"source": "oig_leie", "status": "clear"},
            ],
            "any_unavailable": ["pecos", "sam_gov"],
            "none_of": [
                {"field": "npi_validation", "status": "flagged"},
                # A source that returned "no record" ANSWERED — that is a
                # finding, not an outage, and must not be swallowed by the
                # partial pass. Without these two guards the rule fires whenever
                # any listed source is merely unqueried, which would let a real
                # PECOS not_found be reported as a clean B1.
                {"source": "pecos", "status": "not_found"},
                {"source": "sam_gov", "status": "not_found"},
            ],
        },
    },
    {
        "rule_code": "RULE-003", "name": "B2 Minor/Administrative", "bucket": "B2",
        "priority": 30,
        "description": "Administrative variance — name, address or taxonomy differs "
                       "in form but not in identity.",
        "conditions": {
            "any_of": [
                {"field": "name_mismatch", "severity": "minor"},
                {"field": "address_mismatch", "severity": "minor"},
                {"field": "taxonomy_mismatch", "severity": "minor"},
            ],
            "none_of": [
                {"source": "oig_leie", "status": "excluded"},
                {"field": "npi_validation", "status": "flagged"},
            ],
        },
    },
    {
        "rule_code": "RULE-004", "name": "B3 Inexplicable", "bucket": "B3",
        "priority": 40,
        "description": "Sources reached and disagreed, or the primary source has no "
                       "record. Requires manual review — not auto-resolvable.",
        "conditions": {
            "any_of": [
                {"field": "nppes_pecos_conflict", "status": True},
                {"field": "multiple_source_conflict", "status": True},
                {"source": "nppes", "status": "not_found"},
                {"field": "confidence_below", "threshold": 0.5},
            ],
            "none_of": [{"source": "oig_leie", "status": "excluded"}],
        },
    },
    {
        # PRIORITY 5, not 50. B4 is disqualifying "regardless of what other
        # sources say" — its own description — so it must be evaluated FIRST.
        # At priority 50 it was evaluated last, and an OIG-excluded or
        # SAM-debarred entity with otherwise clean NPPES/PECOS matched RULE-001
        # and was classified B1. A debarred provider reported as "no
        # discrepancy" is the most consequential error this engine could make,
        # so the disqualifying rule leads.
        "rule_code": "RULE-005", "name": "B4 Non-Compliant", "bucket": "B4",
        "priority": 5,
        "description": "Exclusion, debarment or an invalid identifier. Disqualifying "
                       "regardless of what other sources say.",
        "conditions": {
            "any_of": [
                {"source": "oig_leie", "status": "excluded"},
                {"source": "sam_gov", "status": "debarred"},
                {"field": "npi_validation", "status": "invalid"},
                {"field": "required_verification_failed", "status": True},
            ],
        },
    },
]


def _v2_rules() -> List[dict]:
    """Version 2 — SAM.gov wired into classification.

    Derived from SEED_RULES rather than retyped, so the delta is the only thing
    to read and the two cannot drift.

    DESIGN NOTE, and it is the important one: SAM is added as a DISQUALIFIER,
    never as a requirement. Requiring `sam_gov: verified` for B1 would drop every
    entity out of B1 the moment SAM is unreachable — and SAM is currently
    unreachable for want of an API key, so it would reclassify the entire
    registry on deploy. "Entity is not excluded" is the claim the contract needs,
    and a source that never answered has not contradicted it.

    So:
      RULE-001/002/003 (B1/B2)  gain `none_of sam_gov in {excluded, debarred}`
      RULE-005        (B4)      gains `any_of  sam_gov == excluded`

    Every added condition fires only on a positive SAM finding. With no key,
    SAM reports `not_checked`, none of these match, and classification is
    byte-for-byte identical to version 1. When a key is provisioned the rules
    start biting with no further code change.
    """
    import copy

    SAM_BAD = [{"source": "sam_gov", "status": "excluded"},
               {"source": "sam_gov", "status": "debarred"}]
    out = []
    for spec in copy.deepcopy(SEED_RULES):
        code = spec["rule_code"]
        cond = spec["conditions"]
        if code in ("RULE-001", "RULE-002", "RULE-003"):
            cond.setdefault("none_of", []).extend(SAM_BAD)
        elif code == "RULE-005":
            # "debarred" is already present from v1; add the status our connector
            # actually emits so the rule matches real payloads, not just the
            # vocabulary v1 anticipated.
            existing = {(c.get("source"), c.get("status"))
                        for c in cond.get("any_of", [])}
            for c in SAM_BAD:
                if (c["source"], c["status"]) not in existing:
                    cond.setdefault("any_of", []).append(c)
        out.append(spec)
    return out


SEED_RULES_V2: List[dict] = _v2_rules()


class BucketClassifier:
    """Evaluates verification results against the active rule set.

    Rules are cached for CACHE_TTL_SECONDS. The cache is per-instance rather
    than global so a test can construct a classifier with explicit rules and not
    be affected by whatever another test loaded.
    """

    def __init__(self, rules: Optional[List[dict]] = None,
                 ttl_seconds: int = CACHE_TTL_SECONDS):
        self._rules: Optional[List[dict]] = None
        self._loaded_at: float = 0.0
        self._ttl = ttl_seconds
        if rules is not None:
            self._rules = self._sorted(rules)
            self._loaded_at = time.monotonic()

    # ── rule loading ─────────────────────────────────────────────────────────

    @staticmethod
    def _sorted(rules: List[dict]) -> List[dict]:
        # rule_code is the tiebreaker so evaluation order can never depend on
        # database row order.
        return sorted(rules, key=lambda r: (r.get("priority", 9999),
                                            r.get("rule_code", "")))

    def _cache_valid(self) -> bool:
        return (self._rules is not None
                and (time.monotonic() - self._loaded_at) < self._ttl)

    async def load_rules(self, session) -> List[dict]:
        """Active, non-retired rules from the DB, newest version per code."""
        if self._cache_valid():
            return self._rules

        from sqlalchemy import select
        from app.tefca_registry import models as reg

        rows = (await session.execute(
            select(reg.ReviewRule).where(reg.ReviewRule.is_active.is_(True))
        )).scalars().all()

        today = date.today()
        best: Dict[str, Any] = {}
        for r in rows:
            if r.retired_date and r.retired_date <= today:
                continue
            if r.effective_date and r.effective_date > today:
                continue
            cur = best.get(r.rule_code)
            if cur is None or (r.version or 0) > (cur.version or 0):
                best[r.rule_code] = r

        loaded = self._sorted([{
            "rule_code": r.rule_code, "name": r.name, "bucket": r.bucket,
            "priority": r.priority, "conditions": r.conditions or {},
            "description": r.description, "version": r.version,
        } for r in best.values()])

        # CHECK 4 — the vocabulary contract, enforced BEFORE the rules are cached.
        #
        # Rules are DATABASE ROWS. A rule inserted referencing a signal nothing
        # produces does not fail — it silently never fires, and the rule set
        # claims coverage it does not have. That already happened once with
        # `name_mismatch`. Every other contract check ranges over code-defined
        # vocabulary and belongs in CI; this one has to run against whatever the
        # database actually holds, so it runs here.
        #
        # A registered signal that is DECLARED_UNAVAILABLE or METHODOLOGY_BLOCKED
        # does NOT raise: that is a disclosed gap, reported per condition by
        # `condition_readiness`. Only an UNREGISTERED reference raises.
        from app.core.vocabulary_contract import assert_db_rules_reference_known_signals

        assert_db_rules_reference_known_signals(loaded)

        self._rules = loaded
        self._loaded_at = time.monotonic()
        return self._rules

    # ── condition evaluation ─────────────────────────────────────────────────

    @staticmethod
    def _source_state(results: dict, source: str) -> str:
        """Normalised state for a source. Missing entirely means not_checked —
        the honest reading of 'we have no record of asking'."""
        sources = results.get("sources") or results.get("verification") or {}
        raw = sources.get(source)
        if raw is None:
            return NOT_CHECKED
        if isinstance(raw, str):
            return raw
        return raw.get("status") or NOT_CHECKED

    def _match_source(self, results: dict, cond: dict) -> bool:
        source = cond["source"]
        want = cond["status"]
        actual = self._source_state(results, source)
        # "clear" on an exclusion list means "reached it and the entity is not
        # listed" — the good outcome. Callers may express it either way.
        if want == "clear":
            return actual in ("clear", VERIFIED, "not_excluded")
        if want == "excluded":
            return actual in ("excluded", "hit")
        return actual == want

    @staticmethod
    def _field_value(results: dict, name: str):
        fields = results.get("fields") or {}
        if name in fields:
            return fields[name]
        return results.get(name)

    def _match_field(self, results: dict, cond: dict) -> bool:
        name = cond["field"]
        raw = self._field_value(results, name)

        if "threshold" in cond:                     # e.g. confidence_below
            conf = results.get("confidence_score")
            if conf is None:
                conf = self._field_value(results, "confidence_score")
            # A null confidence is NOT "below threshold" — nothing was measured.
            # Treating unknown as failing would penalise unreachable sources.
            return conf is not None and conf < cond["threshold"]

        if "severity" in cond:
            if isinstance(raw, dict):
                return raw.get("severity") == cond["severity"]
            return raw == cond["severity"]

        want = cond.get("status")
        if isinstance(raw, dict):
            raw = raw.get("status", raw.get("value"))
        if isinstance(want, bool):
            return bool(raw) is want
        return raw == want

    def _match(self, results: dict, cond: dict) -> bool:
        if "source" in cond:
            return self._match_source(results, cond)
        if "field" in cond:
            return self._match_field(results, cond)
        return False

    def _evaluate(self, results: dict, conditions: dict) -> tuple:
        """(matched, [descriptions of what matched])"""
        matched: List[str] = []

        for cond in conditions.get("all_of", []):
            if not self._match(results, cond):
                return False, []
            matched.append(self._describe(cond))

        any_of = conditions.get("any_of", [])
        if any_of:
            hits = [c for c in any_of if self._match(results, c)]
            if not hits:
                return False, []
            matched.extend(self._describe(c) for c in hits)

        unavail = conditions.get("any_unavailable", [])
        if unavail:
            hits = [s for s in unavail
                    if self._source_state(results, s) in NO_SIGNAL_STATES]
            if not hits:
                return False, []
            matched.extend(f"{s} did not answer" for s in hits)

        for cond in conditions.get("none_of", []):
            if self._match(results, cond):
                return False, []

        return True, matched

    @staticmethod
    def _describe(cond: dict) -> str:
        if "source" in cond:
            return f"{cond['source']} is {cond['status']}"
        if "threshold" in cond:
            return f"{cond['field']} < {cond['threshold']}"
        if "severity" in cond:
            return f"{cond['field']} severity {cond['severity']}"
        return f"{cond.get('field')} is {cond.get('status')}"

    # ── public API ───────────────────────────────────────────────────────────

    def classify(self, verification_results: dict,
                 rules: Optional[List[dict]] = None) -> ClassificationResult:
        active = self._sorted(rules) if rules is not None else (self._rules or [])
        evaluated: List[str] = []

        for rule in active:
            evaluated.append(f"{rule['rule_code']}v{rule.get('version', 1)}")
            ok, matched = self._evaluate(verification_results,
                                         rule.get("conditions") or {})
            if ok:
                return ClassificationResult(
                    bucket=rule["bucket"],
                    rule_code=rule["rule_code"],
                    rule_version=rule.get("version", 1),
                    rule_name=rule["name"],
                    rationale=(f"{rule['name']} ({rule['rule_code']} v"
                               f"{rule.get('version', 1)}): "
                               + ("; ".join(matched) if matched
                                  else rule.get("description") or "conditions met")),
                    matched_conditions=matched,
                    evidence_summary=self.evidence_summary(verification_results),
                    evaluated_rules=evaluated,
                )

        # No rule matched. B3 is the honest default: the rule set does not
        # describe this case, which is precisely "inexplicable" and needs a
        # human — not a silent pass.
        return ClassificationResult(
            bucket=DEFAULT_BUCKET, rule_code=None, rule_version=None,
            rule_name="Unmatched — default",
            rationale=("No active rule matched these results, so the review "
                       "defaults to B3 for manual examination rather than being "
                       "passed silently."),
            matched_conditions=[],
            evidence_summary=self.evidence_summary(verification_results),
            evaluated_rules=evaluated,
        )

    async def classify_with_db(self, session, verification_results: dict):
        return self.classify(verification_results,
                             rules=await self.load_rules(session))

    @staticmethod
    def evidence_summary(results: dict) -> dict:
        """Counts by verification state — the numbers a report needs, kept
        distinct so an unreachable source is never reported as a discrepancy."""
        sources = results.get("sources") or results.get("verification") or {}
        counts = {s: 0 for s in VERIFICATION_STATES}
        counts["clear"] = 0
        counts["excluded"] = 0
        for _name, raw in sources.items():
            state = raw if isinstance(raw, str) else (raw or {}).get("status")
            if state in counts:
                counts[state] += 1
        checked = sum(counts[s] for s in (VERIFIED, NOT_FOUND)) + counts["clear"] + counts["excluded"]
        return {
            "sources_total": len(sources),
            "sources_checked": checked,
            "sources_verified": counts[VERIFIED] + counts["clear"],
            "sources_not_found": counts[NOT_FOUND],
            "sources_unavailable": counts[UNAVAILABLE],
            "sources_not_checked": counts[NOT_CHECKED],
            "sources_failed": counts[FAILED],
            "by_state": counts,
        }


async def ensure_seed_rules(session) -> int:
    """Insert the version-1 rule set if review_rules is empty. Idempotent.

    Skips entirely when any row exists, so a deployed rule set edited through
    the API is never overwritten by a redeploy.
    """
    from sqlalchemy import func as sqlfunc, select
    from app.tefca_registry import models as reg

    existing = int((await session.execute(
        select(sqlfunc.count()).select_from(reg.ReviewRule))).scalar() or 0)
    if existing:
        return 0

    for spec in SEED_RULES:
        session.add(reg.ReviewRule(
            rule_code=spec["rule_code"], name=spec["name"], bucket=spec["bucket"],
            priority=spec["priority"], conditions=spec["conditions"],
            description=spec["description"], version=1,
            effective_date=date(2026, 8, 1), is_active=True))
    await session.commit()
    logger.info("Seeded %d review rules (version 1)", len(SEED_RULES))
    return len(SEED_RULES)


async def ensure_rules_v2(session, effective: Optional[date] = None) -> int:
    """Retire version 1 rules and activate version 2 (SAM.gov wired in).

    Idempotent: returns 0 if any version-2 row already exists.

    v1 rows are RETIRED, never deleted. A classification recorded last week
    cites the rule version that produced it, and an auditor asking "what did
    RULE-001 say when this entity was bucketed" needs that row to still exist.
    Deleting it would leave every historical review pointing at nothing.
    """
    from sqlalchemy import func as sqlfunc, select
    from app.tefca_registry import models as reg

    eff = effective or date.today()
    already = int((await session.execute(
        select(sqlfunc.count()).select_from(reg.ReviewRule)
        .where(reg.ReviewRule.version == 2))).scalar() or 0)
    if already:
        return 0

    v1 = (await session.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.version == 1))).scalars().all()
    for row in v1:
        row.is_active = False
        row.retired_date = eff

    for spec in SEED_RULES_V2:
        session.add(reg.ReviewRule(
            rule_code=spec["rule_code"], name=spec["name"], bucket=spec["bucket"],
            priority=spec["priority"], conditions=spec["conditions"],
            description=spec["description"], version=2,
            effective_date=eff, is_active=True))
    await session.commit()
    logger.info("Retired %d v1 rules; activated %d v2 rules",
                len(v1), len(SEED_RULES_V2))
    return len(SEED_RULES_V2)
