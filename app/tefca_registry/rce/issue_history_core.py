"""Cross-delivery issue history: the PURE classification core.

No database, no clock, no I/O. Everything here takes plain dicts (built by
`issue_history.py` from rows already scoped to the caller's allowed feeds) and
returns plain dicts, so each rule below is unit-testable on its own and cannot
reach data the caller was not meant to see.

THE FOUR RESOLVED DESIGN POINTS (SCOPE-ISSUE-HISTORY-MIN-SLICE-NPI-PARTOF)
  2.1 feed scoping, fail closed      -> `allowed_feeds`, `parse_feed_list`
  2.2 historical findings as recorded -> `build_lane_cells`, `classify_recurrence`
  2.3 distinct deliveries, provenance -> `build_deliveries`
  2.4 versioned per-record result map -> `record_check_results.read_map`

THREE INDEPENDENT FACTS PER ISSUE CELL
  check       what the persisted check evidence says (FAIL, PASS, ...) and
              whether it is comparable to the baseline
  qa          the human decision on THIS delivery's issue (rce_issues columns)
  recurrence  derived ONLY from the sequence of `check` objects
None is computed from another. In particular `classify_recurrence` never reads
`qa`, and `classify_qa` never reads a check.

NEVER INFERRED. The absence of a finding is not PASS: only a persisted,
comparable `P` result is. A missing delivery, a missing record, a run that did
not complete, a rule that errored, was skipped, did not apply or was undeclared,
and a result map the reader cannot decode all yield NOT_COMPARABLE with a
reason. "resolved" and "corrected" are never produced by this module; the QA
object reports the recorded decision verbatim.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.tefca_registry.rce import record_check_results as rcr

# ── vocabulary ───────────────────────────────────────────────────────────────

#: The eight slice rules and the delivered field each reports on.
SLICE_LANES: Tuple[Tuple[str, str], ...] = (
    ("NPI-001", "NPI"),
    ("NPI-002", "NPI"),
    ("NPI-003", "NPI"),
    ("NPI-004", "NPI"),
    ("INT-001", "orgManagingOrg"),
    ("INT-002", "partOf"),
    ("INT-003", "partOf"),
    ("BUS-003", "partOf"),
)
SLICE_RULE_IDS = frozenset(r for r, _ in SLICE_LANES)
FIELD_OF_RULE = dict(SLICE_LANES)

#: Later-stage NPI rules: shown as observed issues, recurrence not evaluated.
LATER_STAGE_RULE_IDS = frozenset({"NPI-005", "NPI-006", "NPI-008", "NPI-009"})

GAP_TEXT = "No snapshot or correction evidence available."
NEWER_RUN_TEXT = ("A newer run did not complete; these results are from the "
                  "earlier completed run.")
DUPLICATE_NOTE = "identical re-upload, not a separate delivery"
APPROVED_NOT_FOLLOWED_NOTE = ("approved resolution not followed by a comparable "
                              "passing check")

#: Check outcomes. NOT_AVAILABLE: there is no check evidence to read at all.
#: NOT_RECORDED: the delivery was processed but no persisted result exists.
OUT_FAIL, OUT_PASS, OUT_NA = "FAIL", "PASS", "NOT_APPLICABLE"
OUT_SKIPPED, OUT_ERROR, OUT_UNQ = "SKIPPED", "ERROR", "UNQUALIFIED"
OUT_NOT_RECORDED, OUT_NOT_AVAILABLE, OUT_OBSERVED = (
    "NOT_RECORDED", "NOT_AVAILABLE", "OBSERVED")

COMPARABLE, NOT_COMPARABLE = "COMPARABLE", "NOT_COMPARABLE"

# non-comparability reasons
R_NO_RUN = "NO_COMPLETED_RUN"
R_ABSENT = "RECORD_ABSENT"
R_DUP_OID = "DUPLICATE_OID_IN_DELIVERY"
R_NOT_PERSISTED = "CHECK_RESULT_NOT_PERSISTED"
R_UNSUPPORTED = rcr.REASON_UNSUPPORTED
R_MISMATCH = "RESULT_ISSUE_MISMATCH"
R_NOT_IN_RUN = "RULE_NOT_IN_RUN"
R_FIELD_ABSENT = "REQUIRED_FIELD_ABSENT"
R_NOT_APPLICABLE = "APPLICABILITY_NOT_MET"
R_RULE_ERROR = "RULE_ERROR"
R_SKIPPED = "RULE_SKIPPED"
R_UNDECLARED = "APPLICABILITY_UNDECLARED"
R_VERSION = "RULE_VERSION_CHANGED"
R_REQUIRES = "REQUIRES_CHANGED"
R_SCHEMA = "SCHEMA_CHANGED"
R_COVERAGE = "COVERAGE_NOT_RECORDED"
R_REGISTRY = "REGISTRY_STATE_DIFFERS"
R_SCOPE = "RULE_SCOPE_CHANGED"
R_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
REGISTRY_NOTE = ("The registry state differed between these runs, so a pass is not "
                 "comparable to this finding.")
R_EXTERNAL = "EXTERNAL_COVERAGE_NOT_TRACKED_PER_RULE"

# recurrence states
REC_FIRST = "FIRST_OBSERVED"
REC_PERSISTENT = "PERSISTENT_OR_UNVERIFIED"
REC_RECURRING = "RECURRING"
REC_NOT_EVALUATED = "NOT_EVALUATED"

# qa states
QA_NONE, QA_AWAITING, QA_APPROVED, QA_RETURNED = (
    "NONE", "AWAITING_QA", "QA_APPROVED", "QA_RETURNED")

#: Keys that must never appear anywhere in a VIEWER response.
VIEWER_FORBIDDEN_KEYS = frozenset({
    "original_value", "suggested_value", "field_values", "notes", "rationale",
    "actor", "email", "raw_line", "parsed"})


# ── 2.1 feed scoping ─────────────────────────────────────────────────────────

def parse_feed_list(value: Optional[str]) -> Tuple[str, ...]:
    """Comma list of feed tags. Exact tags, no case folding; blanks dropped."""
    if not value:
        return ()
    seen: List[str] = []
    for part in str(value).split(","):
        tag = part.strip()
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen)


def allowed_feeds(*, reviewer_or_above: bool, viewer_setting: Optional[str],
                  reviewer_setting: Optional[str]) -> frozenset:
    """Feeds the caller may read. EMPTY means nothing is visible (fail closed).

    Reviewer and above see the viewer feeds plus their own list; a viewer sees
    only the viewer list. An untagged intake belongs to no feed and is therefore
    never in any set.
    """
    feeds = set(parse_feed_list(viewer_setting))
    if reviewer_or_above:
        feeds |= set(parse_feed_list(reviewer_setting))
    return frozenset(feeds)


FEED_MODULE_PREFIX = "feed:"


def user_feed_tags(allowed_modules) -> Optional[Tuple[str, ...]]:
    """Per-ACCOUNT feed entries from `users.allowed_modules` (`feed:<TAG>`).

    None  = the account carries no `feed:` entry: the role's feeds apply
            unchanged (no account-level narrowing configured).
    tuple = the account IS narrowed to these tags (possibly an empty tuple
            when every `feed:` entry is blank: that narrows to nothing).
    Anything that is not a list of strings is treated as "no entries" only when
    it is empty/absent; a malformed non-empty value narrows to nothing (fail
    closed) rather than being ignored.
    """
    if allowed_modules is None:
        return None
    if not isinstance(allowed_modules, (list, tuple)):
        return ()
    tags: List[str] = []
    seen_entry = False
    for item in allowed_modules:
        if isinstance(item, str) and item.startswith(FEED_MODULE_PREFIX):
            seen_entry = True
            tag = item[len(FEED_MODULE_PREFIX):].strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tuple(tags) if seen_entry else None


def narrow_feeds(feeds: frozenset, allowed_modules) -> frozenset:
    """Role feeds INTERSECT account feeds. Narrows only; never widens."""
    tags = user_feed_tags(allowed_modules)
    if tags is None:
        return feeds
    return frozenset(feeds & set(tags))


# ── 2.3 (REVISED) distinct deliveries, identical content kept distinct ───────

IDENTICAL_NOTE = "content identical to delivery {other}; the same file content can be a separate delivery"


def _order_key(intake: Dict[str, Any]):
    return (intake["received_at"], str(intake["id"]))


def build_deliveries(intakes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every non-FAILED intake of the allowed feeds is its OWN delivery.

    Identical file content is NOT collapsed: ONC can legitimately re-send the
    same bytes on another date and that is a separate delivery with its own
    receipt date, label, runs and results. Provenance only is added:

      identical_content_of    nearest EARLIER intake in the same feed with the
                              same sha256 (None for the first of them)
      identical_content_also  every other same-feed intake with that sha256
      duplicate_upload        the intake itself is flagged duplicate_content /
                              has duplicate_of_intake_id (a same-day accidental
                              re-upload as the intake path recorded it)

    A duplicate never becomes a gap. Order is received_at then id. FAILED
    intakes are not deliveries.
    """
    usable = [i for i in intakes if i.get("status") != "FAILED"]
    usable.sort(key=_order_key)
    by_hash: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for intake in usable:
        by_hash.setdefault((intake["feed"], intake["sha256"]), []).append(intake)
    out = []
    for intake in usable:
        same = by_hash[(intake["feed"], intake["sha256"])]
        earlier = [o for o in same if _order_key(o) < _order_key(intake)]
        also = [o["id"] for o in same if o is not intake]
        out.append({
            "intake": intake,
            "identical_content_of": (earlier[-1]["id"] if earlier else None),
            "identical_content_also": also,
            "duplicate_upload": bool(intake.get("duplicate_content")
                                     or intake.get("duplicate_of_intake_id")),
        })
    return out


# ── run selection (mirrors run_selection.py) ─────────────────────────────────

def select_runs(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Current run = latest COMPLETE run, ordered exactly as `run_selection`.

    Ordering is completed_at DESC, started_at DESC, id DESC. RUNNING and FAILED
    runs are never selectable; those that started after the selected run (or all
    of them when nothing completed) are `newer_runs`, listed beside it and never
    mixed into its states.
    """
    complete = [r for r in runs if r["status"] == "COMPLETE"
                and r.get("completed_at") is not None]
    complete.sort(key=lambda r: (r["completed_at"], r["started_at"], str(r["id"])),
                  reverse=True)
    selected = complete[0] if complete else None
    unfinished = [r for r in runs if r["status"] in ("RUNNING", "FAILED")]
    if selected is None:
        newer = sorted(unfinished, key=lambda r: (r["started_at"], str(r["id"])))
    else:
        newer = sorted((r for r in unfinished
                        if r["started_at"] > selected["started_at"]),
                       key=lambda r: (r["started_at"], str(r["id"])))
    return {"selected": selected, "newer_runs": newer,
            "earlier_completed": complete[1:]}


def sanitize_error(error: Optional[str]) -> Optional[str]:
    """A run error reduced to a short class-like token; never the message."""
    if not error:
        return None
    token = str(error).split(":", 1)[0].strip().split()[0:1]
    token = token[0] if token else ""
    cleaned = "".join(ch for ch in token if ch.isalnum() or ch in "._")[:64]
    return cleaned or "ERROR"


# ── 2.4 raw per-cell check ───────────────────────────────────────────────────

def _required_fields_empty(rule_id: str, parsed: Optional[Dict[str, Any]]) -> bool:
    from app.tefca_registry.rce.quality_rules import RULE_BY_ID

    rule = RULE_BY_ID.get(rule_id)
    if rule is None or not rule.requires_fields:
        return False
    values = parsed or {}
    return any(not str(values.get(f) or "").strip() for f in rule.requires_fields)


def _coverage_recorded(coverage) -> bool:
    """Coverage counts AND the registry-state watermark were recorded."""
    return isinstance(coverage, dict) and coverage.get("registry_watermark") is not None


def _source_unavailable(rule_id: str, coverage) -> bool:
    """Every dataset a reference-dependent rule resolves against was EMPTY.

    INT-002 resolves partOf against the delivery's own ids, the registry OID
    namespace and the QHIN OIDs. When the run recorded ALL THREE as holding zero
    identifiers, a PASS proves nothing: nothing could have been found wanting.
    It is shown as unavailable, never as clear. Only explicit recorded zeros
    count; a missing count is handled by the coverage-not-recorded rule.
    """
    if rule_id != "INT-002" or not isinstance(coverage, dict):
        return False
    counts = [coverage.get(k) for k in ("delivery_ids", "registry_oids", "qhin_oids")]
    return all(isinstance(c, int) and c == 0 for c in counts)


def raw_check(facts: Dict[str, Any], rule_id: str, field: str) -> Dict[str, Any]:
    """The persisted check evidence for one rule in one delivery. No inference.

    `facts` is built by the service layer for ONE canonical delivery:
        intake_status, headers, record_state (PRESENT|ABSENT|DUPLICATE),
        record_parsed, selected_run (or None), history_rows (list of dicts),
        result (dict or None), issues (this record's issues in the selected run)
    Returns: outcome, reason, usable (a persisted F or P that the reader could
    decode and that agrees with the ledger), plus the identity used to decide
    comparability.
    """
    base = {"outcome": OUT_NOT_AVAILABLE, "reason": None, "usable": False,
            "rule_version": None, "requires_hash": None, "scope": None,
            "source_unavailable": False,
            "coverage_recorded": False, "registry_watermark": None,
            "header_has_field": False,
            "issues": []}
    if facts["intake_status"] == "FAILED" or facts["selected_run"] is None:
        return {**base, "reason": R_NO_RUN}
    if facts["record_state"] == "ABSENT":
        return {**base, "reason": R_ABSENT}
    if facts["record_state"] == "DUPLICATE":
        return {**base, "reason": R_DUP_OID}

    lane_issues = [i for i in facts["issues"] if i["rule_id"] == rule_id]
    history_rows = facts["history_rows"]
    by_rule = {h["rule_id"]: h for h in history_rows}
    hist = by_rule.get(rule_id)
    out = {**base, "issues": lane_issues,
           "header_has_field": field in (facts.get("headers") or [])}
    if lane_issues:
        out["rule_version"] = lane_issues[0].get("rule_version")
    if hist is not None:
        out.update(rule_version=hist["rule_version"],
                   requires_hash=hist.get("requires_hash"),
                   scope=hist.get("scope"),
                   source_unavailable=_source_unavailable(rule_id, hist.get("coverage")),
                   coverage_recorded=_coverage_recorded(hist.get("coverage")),
                   registry_watermark=(hist.get("coverage") or {}).get("registry_watermark"))

    fail_or_none = OUT_FAIL if lane_issues else OUT_NOT_RECORDED
    result = facts.get("result")
    if result is None:
        return {**out, "outcome": fail_or_none, "reason": R_NOT_PERSISTED}
    codes, why = rcr.read_map(result["map_version"], result["rule_count"],
                              result["outcomes"], history_rows)
    if why is not None:
        return {**out, "outcome": fail_or_none, "reason": why}
    code = codes.get(rule_id)
    if code is None or hist is None:
        return {**out, "outcome": fail_or_none, "reason": R_NOT_IN_RUN}
    if (code == rcr.CODE_FINDING) != bool(lane_issues):
        # The map and the ledger disagree about whether a finding exists.
        # Neither is trusted to settle it.
        return {**out, "outcome": fail_or_none, "reason": R_MISMATCH}
    if code == rcr.CODE_FINDING:
        return {**out, "outcome": OUT_FAIL, "usable": True}
    if code == rcr.CODE_PASS:
        if out["source_unavailable"]:
            return {**out, "outcome": OUT_PASS, "reason": R_SOURCE_UNAVAILABLE}
        return {**out, "outcome": OUT_PASS, "usable": True}
    if code == rcr.CODE_NOT_APPLICABLE:
        reason = (R_FIELD_ABSENT
                  if _required_fields_empty(rule_id, facts.get("record_parsed"))
                  else R_NOT_APPLICABLE)
        return {**out, "outcome": OUT_NA, "reason": reason}
    if code == rcr.CODE_SKIPPED:
        return {**out, "outcome": OUT_SKIPPED, "reason": R_SKIPPED}
    if code == rcr.CODE_ERROR:
        return {**out, "outcome": OUT_ERROR, "reason": R_RULE_ERROR}
    return {**out, "outcome": OUT_UNQ, "reason": R_UNDECLARED}


def compare_identity(a: Dict[str, Any], b: Dict[str, Any], rule_id: str
                     ) -> Optional[str]:
    """Why two usable checks of the SAME rule are not comparable, or None.

    Strict by default: identical rule_version (an equivalence list may relax
    this later), identical declaration hash, the lane's delivered field present
    in both headers, and, for INT-002, dataset coverage recorded on both runs.
    """
    if a["rule_version"] != b["rule_version"]:
        return R_VERSION
    if a["requires_hash"] != b["requires_hash"]:
        return R_REQUIRES
    if a.get("scope") != b.get("scope"):
        return R_SCOPE
    if rule_id == "INT-002":
        if not (a["coverage_recorded"] and b["coverage_recorded"]):
            return R_COVERAGE
        if a["registry_watermark"] != b["registry_watermark"]:
            return R_REGISTRY
    if not (a["header_has_field"] and b["header_has_field"]):
        return R_SCHEMA
    return None


# ── recurrence: derived ONLY from the sequence of checks ─────────────────────

def classify_recurrence(raws: Sequence[Dict[str, Any]], index: int, rule_id: str
                        ) -> Optional[Dict[str, Any]]:
    """Recurrence for the cell at `index` of one lane (oldest first).

    Reads ONLY the raw checks (outcome, usable, identity). It does not see QA.
    Returns None when the cell holds no finding.
        FIRST_OBSERVED            no earlier occurrence in the visible history
        PERSISTENT_OR_UNVERIFIED  earlier occurrence, no comparable PASS between
        RECURRING                 earlier occurrence, a comparable PASS, then
                                  this occurrence (links to both)
        NOT_EVALUATED             the cell's evidence cannot support it
    """
    cur = raws[index]
    if cur["outcome"] != OUT_FAIL:
        return None
    if cur["reason"] in (R_UNSUPPORTED, R_MISMATCH):
        return {"state": REC_NOT_EVALUATED, "reason": cur["reason"],
                "earlier_occurrence": None, "comparable_pass": None}
    earlier = next((j for j in range(index - 1, -1, -1)
                    if raws[j]["outcome"] == OUT_FAIL), None)
    if earlier is None:
        return {"state": REC_FIRST, "reason": None,
                "earlier_occurrence": None, "comparable_pass": None}
    passing = None
    if cur["usable"]:
        for p in range(index - 1, earlier, -1):
            cand = raws[p]
            if (cand["usable"] and cand["outcome"] == OUT_PASS
                    and compare_identity(cand, cur, rule_id) is None):
                passing = p
                break
    if passing is not None:
        return {"state": REC_RECURRING, "reason": None,
                "earlier_occurrence": earlier, "comparable_pass": passing}
    reason = None
    if cur["usable"] and rule_id == "INT-002":
        for p in range(index - 1, earlier, -1):
            cand = raws[p]
            if (cand["usable"] and cand["outcome"] == OUT_PASS
                    and compare_identity(cand, cur, rule_id) == R_REGISTRY):
                reason = R_REGISTRY
                break
    return {"state": REC_PERSISTENT, "reason": reason,
            "earlier_occurrence": earlier, "comparable_pass": None}


# ── qa: from the issue's own resolution columns only ─────────────────────────

def classify_qa(issue: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The human decision on one issue. Reads NO check evidence.

    QA_APPROVED requires an approver recorded on the issue AND independence
    from the person who resolved it. An approval by the same person is not an
    approval: it stays AWAITING_QA. The decision is reported in the issue's own
    words, except that the terminal state is shown as CLOSED so that no
    response asserts "resolved" on a QA approval's behalf.
    """
    if issue is None:
        return {"status": QA_NONE, "decision": None, "role": None,
                "timestamp": None, "qa_required": False}
    resolution = issue.get("resolution") or "OPEN"
    required = issue.get("correction_authority") == "QA_REQUIRED"
    approved_by = issue.get("qa_approved_by")
    resolved_by = issue.get("resolved_by")
    decision = "CLOSED" if resolution == "RESOLVED" else resolution
    if approved_by and issue.get("qa_approved_at") is not None \
            and approved_by != resolved_by:
        return {"status": QA_APPROVED, "decision": decision,
                "role": "qa_approver", "timestamp": issue["qa_approved_at"],
                "qa_required": required}
    if resolution == "REJECTED":
        return {"status": QA_RETURNED, "decision": decision, "role": "reviewer",
                "timestamp": issue.get("resolved_at"), "qa_required": required}
    if resolution in ("APPROVED", "RESOLVED", "WAIVED") and required:
        return {"status": QA_AWAITING, "decision": decision, "role": "reviewer",
                "timestamp": issue.get("resolved_at"), "qa_required": required}
    return {"status": QA_NONE, "decision": decision,
            "role": "reviewer" if issue.get("resolved_by") else None,
            "timestamp": issue.get("resolved_at"), "qa_required": required}


# ── lane assembly ────────────────────────────────────────────────────────────

def _reference(raws: Sequence[Dict[str, Any]]) -> Optional[int]:
    """The baseline for per-cell comparability: the newest usable check."""
    for i in range(len(raws) - 1, -1, -1):
        if raws[i]["usable"]:
            return i
    return None


def _check_object(raws, i, ref_index, rule_id) -> Dict[str, Any]:
    raw = raws[i]
    if raw["usable"]:
        if ref_index is None or i == ref_index:
            comparability, reason = COMPARABLE, None
        else:
            why = compare_identity(raw, raws[ref_index], rule_id)
            comparability, reason = ((COMPARABLE, None) if why is None
                                     else (NOT_COMPARABLE, why))
    else:
        comparability, reason = NOT_COMPARABLE, raw["reason"]
    return {"outcome": raw["outcome"], "comparability": comparability,
            "reason": reason}


def build_lane_cells(facts_by_delivery: Sequence[Dict[str, Any]], rule_id: str,
                     field: str) -> List[Dict[str, Any]]:
    """Cells for one (rule, field) lane across deliveries, oldest first."""
    raws = [raw_check(f, rule_id, field) for f in facts_by_delivery]
    ref = _reference(raws)
    cells: List[Dict[str, Any]] = []
    for i, facts in enumerate(facts_by_delivery):
        raw = raws[i]
        issue = raw["issues"][0] if raw["issues"] else None
        rec = classify_recurrence(raws, i, rule_id)
        cell = {
            "delivery_id": facts["delivery_id"],
            "rule_id": rule_id, "field": field,
            "rule_version": raw["rule_version"],
            "check": _check_object(raws, i, ref, rule_id),
            "issue": issue, "issue_count": len(raw["issues"]),
            "qa": classify_qa(issue) if issue else None,
            "recurrence": rec,
            "note": None,
        }
        if rec and rec["state"] == REC_PERSISTENT and rec["earlier_occurrence"] is not None:
            earlier_issue = raws[rec["earlier_occurrence"]]["issues"]
            if earlier_issue and classify_qa(earlier_issue[0])["status"] == QA_APPROVED:
                # A note beside the badges; it changes neither of them.
                cell["note"] = APPROVED_NOT_FOLLOWED_NOTE
        if rec and rec.get("reason") == R_REGISTRY:
            cell["note"] = REGISTRY_NOTE if not cell["note"] else cell["note"] + "; " + REGISTRY_NOTE
        cells.append(cell)
    # replace recurrence indexes by delivery ids
    for cell in cells:
        rec = cell["recurrence"]
        if rec:
            for key in ("earlier_occurrence", "comparable_pass"):
                if rec[key] is not None:
                    rec[key] = facts_by_delivery[rec[key]]["delivery_id"]
    return cells


def later_stage_findings(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Later-stage issues (NPI-005/006/008/009 and the post-promotion types),
    kept OUTSIDE the lanes and outside every recurrence calculation.

    Each is labelled with the run it actually belongs to. It is NEVER presented
    as a selected-run result: `from_selected_run` is true only when its run_id
    equals the delivery's selected run. Recurrence is fixed at NOT_EVALUATED.
    """
    selected = facts.get("selected_run")
    selected_id = selected["id"] if selected else None
    runs = facts.get("runs_by_id") or {}
    out = []
    for issue in facts.get("later_issues", []):
        run_id = issue.get("run_id")
        run = runs.get(run_id) if run_id is not None else None
        from_selected = run_id is not None and run_id == selected_id
        if run_id is None:
            note = "run not recorded"
        elif from_selected:
            note = "from the selected run"
        else:
            note = f"from run {str(run_id)[:8]} (not the selected run)"
        out.append({
            "delivery_id": facts["delivery_id"],
            "rule_id": issue["rule_id"],
            "field": issue.get("field_name") or "NPI",
            "rule_version": issue.get("rule_version"),
            "issue": issue,
            "qa": classify_qa(issue),
            "run_id": run_id,
            "run_status": (run["status"] if run else None),
            "run_completed_at": (run.get("completed_at") if run else None),
            "from_selected_run": from_selected,
            "run_note": note,
            "check": {"outcome": OUT_OBSERVED, "comparability": NOT_COMPARABLE,
                      "reason": R_EXTERNAL},
            "recurrence": {"state": REC_NOT_EVALUATED, "reason": R_EXTERNAL,
                           "earlier_occurrence": None, "comparable_pass": None},
        })
    return out


def gaps_from_lanes(lane_cells: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A gap is a finding that persisted with no comparable passing check
    between two VISIBLE deliveries. Never about hidden ones."""
    gaps = []
    for cell in lane_cells:
        rec = cell.get("recurrence")
        if rec and rec["state"] == REC_PERSISTENT:
            gaps.append({"rule_id": cell["rule_id"], "field": cell["field"],
                         "from_delivery_id": rec["earlier_occurrence"],
                         "to_delivery_id": cell["delivery_id"],
                         "text": GAP_TEXT})
    return gaps


# ── explicit sequence gaps ───────────────────────────────────────────────────

K_FAILED_INTAKE = "DELIVERY_NOT_PROCESSED"
K_NO_RUN = "NO_COMPLETED_RUN"
K_ABSENT = "ENTITY_ABSENT_OR_REKEYED"
K_DUP = "DUPLICATE_OID_IN_DELIVERY"
K_SOURCE = "SOURCE_UNAVAILABLE"
K_RULE_ERROR = "RULE_ERROR"
K_RULE_SKIPPED = "RULE_SKIPPED"

GAP_KIND_TEXT = {
    K_FAILED_INTAKE: ("A delivery in this feed failed intake and was not processed; "
                      "nothing is known about this entity in it. It is not a pass."),
    K_NO_RUN: ("No completed check run exists for this delivery; nothing is known "
               "about this entity in it. It is not a pass."),
    K_ABSENT: ("This entity key is not in this delivery. The history does not assume "
               "the entity was removed, re-keyed, or clear; it is a gap."),
    K_DUP: ("This entity key appears more than once in this delivery, so no single "
            "result is shown. It is not a pass."),
    K_SOURCE: ("The reference source this check resolves against was empty when the "
               "delivery was checked, so a pass here is not a clear result."),
    K_RULE_ERROR: "The rule failed to run for this record. It is not a pass.",
    K_RULE_SKIPPED: "The rule was skipped for this record. It is not a pass.",
}
_LANE_REASON_KIND = {R_SOURCE_UNAVAILABLE: K_SOURCE, R_RULE_ERROR: K_RULE_ERROR,
                     R_SKIPPED: K_RULE_SKIPPED}


def sequence_gaps(feed: str, items: Sequence[Dict[str, Any]],
                  cells_by_delivery: Dict[str, Sequence[Dict[str, Any]]],
                  failed_intakes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every place the visible sequence has NO usable evidence, stated outright.

    Never inferred from the absence of a finding. `items` are one feed's
    deliveries (oldest first) from the first one that carries the entity.
    FAILED intakes of the same feed received after that point are listed as
    their own gaps (they are not deliveries). Entries are metadata only.
    """
    out: List[Dict[str, Any]] = []
    if not items:
        return out
    first_at = items[0]["intake"]["received_at"]
    for facts in items:
        at = facts["intake"]["received_at"]
        did = facts["delivery_id"]
        kind = None
        if facts["selected_run"] is None:
            kind = K_NO_RUN
        elif facts["record_state"] == "ABSENT":
            kind = K_ABSENT
        elif facts["record_state"] == "DUPLICATE":
            kind = K_DUP
        if kind:
            out.append({"kind": kind, "feed": feed, "delivery_id": did,
                        "received_at": at, "rule_id": None, "field": None,
                        "text": GAP_KIND_TEXT[kind]})
        for cell in cells_by_delivery.get(did, ()):
            kind = _LANE_REASON_KIND.get(cell["check"].get("reason"))
            if kind:
                out.append({"kind": kind, "feed": feed, "delivery_id": did,
                            "received_at": at, "rule_id": cell["rule_id"],
                            "field": cell["field"], "text": GAP_KIND_TEXT[kind]})
    for intake in failed_intakes:
        if intake.get("feed") == feed and intake["received_at"] >= first_at:
            out.append({"kind": K_FAILED_INTAKE, "feed": feed, "delivery_id": None,
                        "received_at": intake["received_at"], "rule_id": None,
                        "field": None, "text": GAP_KIND_TEXT[K_FAILED_INTAKE]})
    out.sort(key=lambda g: (g["received_at"], g["kind"], g["rule_id"] or "",
                            g["delivery_id"] or ""))
    return out


# ── guard ────────────────────────────────────────────────────────────────────

def forbidden_keys_present(obj: Any, forbidden: Iterable[str] = VIEWER_FORBIDDEN_KEYS
                           ) -> List[str]:
    """Every forbidden key found anywhere in a nested structure (recursive)."""
    bad = set(forbidden)
    found: List[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in bad:
                    found.append(key)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(obj)
    return found
