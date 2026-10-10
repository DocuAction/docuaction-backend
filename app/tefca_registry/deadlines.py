"""Track A3 - deadline configuration model and pure calculators.

PROPOSAL / INACTIVE. Nothing here runs on a schedule, writes to the database or
sends anything. It answers one question: "given this configuration and these
event times, when is the deadline due?" - and it refuses to answer when the
configuration is incomplete.

WHAT THE TASK 2 DOCUMENT LEAVES UNRESOLVED (so NO DEFAULT IS CHOSEN)
  * B4 "within 24 hours (1 business day)": clock hours or one business day?
    The document gives both. `unit` is therefore None until an owner sets it.
  * Which event starts each clock (classification? analyst finalisation? QA
    approval? source-restored?). `clock_start_event` is None for every rule
    until configured.
  * The business-day calendar (holidays, time zone, cut-off hour).
    Weekends (Sat/Sun) are the only built-in non-business days; holidays are
    configuration.

Amounts that ARE stated in the document are carried as the rule's `amount`,
with the passage cited, but are still inert until unit/clock-start resolve.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

UNIT_CLOCK_HOURS = "clock_hours"
UNIT_BUSINESS_DAYS = "business_days"
UNITS = (UNIT_CLOCK_HOURS, UNIT_BUSINESS_DAYS)

#: Events a clock may start on. A closed vocabulary so a typo cannot silently
#: produce "never due". Mapping to concrete app events is a later decision.
CLOCK_START_EVENTS = (
    "ENTITY_CLASSIFIED",        # system recommendation recorded
    "ANALYST_DETERMINATION",    # analyst CONFIRM / RECLASSIFY
    "QA_APPROVED",              # standing QA APPROVE
    "FINDING_CONFIRMED",        # B4: exclusion/debarment/lapse confirmed
    "RESEARCH_COMPLETED",       # B3: AGT research documented
    "NOTIFICATION_SENT",        # a notification left AGT (human act)
    "SOURCE_UNAVAILABLE_DETECTED",
    "SOURCE_RESTORED",
)

# Rule code -> document passage, stated amount, stated unit (None if ambiguous)
RULE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "B4_NOTIFY_ONC": {
        "passage": "Task 2 classification table, B4: \"ONC is notified by email "
                   "within 24 hours (1 business day)\"; taxonomy: \"Within 24 hrs "
                   "(1 business day)\"",
        "amount": 24, "stated_unit": None,  # hours vs 1 business day: UNRESOLVED
        "note": "If business_days is chosen, set amount to 1 explicitly."},
    "B3_AGT_RESEARCH": {
        "passage": "B3: \"AGT completes its research within one business day\"; "
                   "taxonomy: \"days 1-2 AGT analyst research and source re-query\"",
        "amount": 1, "stated_unit": UNIT_BUSINESS_DAYS,
        "note": "Taxonomy allots days 1-2 to research; classification text says 1."},
    "B3_ONC_DATA_RESPONSE": {
        "passage": "B3: \"respond within 8 business days\"; taxonomy: \"10 business "
                   "days ... days 3-10 HHS/ONC DATA response window\"",
        "amount": 8, "stated_unit": UNIT_BUSINESS_DAYS,
        "note": "8 = days 3-10 only if the clock starts after research; start "
                "event unresolved."},
    "B3_TOTAL_CYCLE": {
        "passage": "Taxonomy, Bucket 3: \"10 business days\"",
        "amount": 10, "stated_unit": UNIT_BUSINESS_DAYS},
    "INDETERMINATE_RE_REVIEW": {
        "passage": "Indeterminate: \"Reviewed once the source is restored, normally "
                   "within 1 business day\"",
        "amount": 1, "stated_unit": UNIT_BUSINESS_DAYS},
    "SOURCE_OUTAGE_ESCALATION": {
        "passage": "Indeterminate: \"If the source remains unavailable for more "
                   "than 3 business days, AGT will escalate to the COR\"",
        "amount": 3, "stated_unit": UNIT_BUSINESS_DAYS,
        "note": "\"more than 3\": escalate after the 3rd business day has elapsed."},
}

STATUS_UNCONFIGURED = "UNCONFIGURED"
STATUS_WAITING = "WAITING_FOR_START_EVENT"
STATUS_COMPUTED = "COMPUTED"
STATUS_INVALID = "INVALID"


# -- business-day math (pure) -------------------------------------------------

def is_business_day(d: date, holidays: FrozenSet[date] = frozenset()) -> bool:
    return d.weekday() < 5 and d not in holidays


def add_business_days(start: datetime, n: int,
                      holidays: FrozenSet[date] = frozenset()) -> datetime:
    """`start` plus n business days, same time of day.

    If `start` falls on a non-business day it is first rolled FORWARD to the
    next business day (same time of day). n=0 returns the rolled start.
    Negative n is refused.
    """
    if n < 0:
        raise ValueError("n must be >= 0")
    cur = start
    while not is_business_day(cur.date(), holidays):
        cur += timedelta(days=1)
    remaining = n
    while remaining:
        cur += timedelta(days=1)
        if is_business_day(cur.date(), holidays):
            remaining -= 1
    return cur


def business_days_between(a: date, b: date,
                          holidays: FrozenSet[date] = frozenset()) -> int:
    """Business days elapsed after `a` up to and including `b` (0 if b<=a)."""
    if b <= a:
        return 0
    count, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if is_business_day(cur, holidays):
            count += 1
    return count


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# -- configuration ------------------------------------------------------------

@dataclass(frozen=True)
class RuleConfig:
    code: str
    unit: Optional[str] = None
    clock_start_event: Optional[str] = None
    amount: Optional[int] = None


@dataclass(frozen=True)
class DeadlineConfig:
    rules: Dict[str, RuleConfig] = field(default_factory=dict)
    holidays: FrozenSet[date] = frozenset()


class DeadlineConfigError(ValueError):
    pass


def parse_config(raw: Optional[str]) -> DeadlineConfig:
    """Parse DEADLINE_CONFIG_JSON. Empty/None -> an empty (all-UNCONFIGURED)
    config. Unknown rule codes, units or events raise; nothing is guessed.

    Shape: {"holidays": ["2026-11-26"],
            "rules": {"B4_NOTIFY_ONC": {"unit": "clock_hours",
                                        "clock_start_event": "FINDING_CONFIRMED",
                                        "amount": 24}}}
    `amount` is optional (the document's stated amount is used).
    """
    if not raw or not raw.strip():
        return DeadlineConfig()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeadlineConfigError(f"DEADLINE_CONFIG_JSON is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise DeadlineConfigError("DEADLINE_CONFIG_JSON must be an object")
    holidays = set()
    for h in data.get("holidays") or []:
        try:
            holidays.add(date.fromisoformat(str(h)))
        except ValueError:
            raise DeadlineConfigError(f"holiday {h!r} is not an ISO date")
    rules: Dict[str, RuleConfig] = {}
    for code, body in (data.get("rules") or {}).items():
        if code not in RULE_DEFAULTS:
            raise DeadlineConfigError(f"unknown rule {code!r}")
        body = body or {}
        unit, ev, amt = body.get("unit"), body.get("clock_start_event"), body.get("amount")
        if unit is not None and unit not in UNITS:
            raise DeadlineConfigError(f"{code}: unit must be one of {UNITS}")
        if ev is not None and ev not in CLOCK_START_EVENTS:
            raise DeadlineConfigError(
                f"{code}: clock_start_event must be one of {CLOCK_START_EVENTS}")
        if amt is not None and (not isinstance(amt, int) or isinstance(amt, bool)
                                or amt < 0):
            raise DeadlineConfigError(f"{code}: amount must be a non-negative integer")
        rules[code] = RuleConfig(code, unit, ev, amt)
    return DeadlineConfig(rules, frozenset(holidays))


def unresolved_questions(config: DeadlineConfig) -> List[str]:
    """What an owner still has to decide, per rule (empty list = fully set)."""
    out = []
    for code, d in RULE_DEFAULTS.items():
        rc = config.rules.get(code)
        unit = (rc.unit if rc and rc.unit else d["stated_unit"])
        if unit is None:
            out.append(f"{code}: unit (clock_hours vs business_days) not decided")
        if not (rc and rc.clock_start_event):
            out.append(f"{code}: clock-start event not decided")
    return out


# -- calculation --------------------------------------------------------------

def compute_due(code: str, config: DeadlineConfig,
                events: Dict[str, datetime]) -> Dict[str, Any]:
    """Due time for one rule, or the reason none can be given. Pure; sends nothing."""
    d = RULE_DEFAULTS.get(code)
    if d is None:
        return {"rule": code, "status": STATUS_INVALID, "due_at": None,
                "reasons": [f"unknown rule {code!r}"]}
    rc = config.rules.get(code)
    unit = (rc.unit if rc and rc.unit else d["stated_unit"])
    amount = rc.amount if rc and rc.amount is not None else d["amount"]
    reasons = []
    if unit is None:
        reasons.append("unit not decided (clock hours vs business day)")
    if not (rc and rc.clock_start_event):
        reasons.append("clock-start event not decided")
    base = {"rule": code, "passage": d["passage"], "unit": unit, "amount": amount,
            "clock_start_event": rc.clock_start_event if rc else None}
    if reasons:
        return {**base, "status": STATUS_UNCONFIGURED, "due_at": None, "reasons": reasons}
    start = events.get(rc.clock_start_event)
    if start is None:
        return {**base, "status": STATUS_WAITING, "due_at": None,
                "reasons": [f"event {rc.clock_start_event} has not occurred"]}
    start = _aware(start)
    if unit == UNIT_CLOCK_HOURS:
        due = start + timedelta(hours=amount)
    else:
        due = add_business_days(start, amount, config.holidays)
    return {**base, "status": STATUS_COMPUTED, "start_at": start.isoformat(),
            "due_at": due.isoformat(), "reasons": []}


def build_register(items: Iterable[Dict[str, Any]], config: DeadlineConfig,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Dry-run register. `items`: {"item_id", "rule", "events": {NAME: datetime}}.
    Reads and writes nothing, sends nothing."""
    now = _aware(now or datetime.now(timezone.utc))
    rows = []
    for it in items:
        r = compute_due(it["rule"], config, it.get("events") or {})
        r["item_id"] = it.get("item_id")
        if r["status"] == STATUS_COMPUTED:
            r["overdue"] = datetime.fromisoformat(r["due_at"]) < now
        rows.append(r)
    summary: Dict[str, int] = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return {"dry_run": True, "notifications_sent": 0, "rows": rows,
            "summary": summary, "unresolved": unresolved_questions(config)}


def conflicts_with_existing_display_code() -> List[Dict[str, Any]]:
    """The existing display-only numbers beside the document's. Reuses the live
    constants rather than copying them, so this cannot drift from the code."""
    from app.tefca_registry.sla import REVIEW_SLA_DAYS
    out = [{"source": "app/tefca_registry/sla.py REVIEW_SLA_DAYS",
            "values": dict(REVIEW_SLA_DAYS),
            "conflict": "calendar days from sample draw (7/90/3); the document has "
                        "no weekly/quarterly/priority windows and uses business days"}]
    try:
        from app.Tefca.qa_engine import SLA_TARGETS
        out.append({"source": "app/Tefca/qa_engine.py SLA_TARGETS",
                    "values": dict(SLA_TARGETS),
                    "conflict": "calendar days by COR severity (2/5/10/21); none "
                                "match 24h/1/3/8/10 business days"})
    except Exception as exc:  # heavy legacy import; absence must not break this
        out.append({"source": "app/Tefca/qa_engine.py SLA_TARGETS",
                    "values": {"critical": 2, "high": 5, "medium": 10, "low": 21},
                    "conflict": f"(constants quoted; import skipped: {type(exc).__name__})"})
    out.append({"source": "app/Tefca/validation_engine.py recommended_deadline",
                "values": {2: 30, 3: 21, 4: 10},
                "conflict": "calendar days by bucket (30/21/10); document: B2 none, "
                            "B3 8-10 business days, B4 24h/1 business day"})
    return out
