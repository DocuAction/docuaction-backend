"""Issue history: pure tests (no database).

Covers acceptance criteria 3 (never inferred), 4 (result map writer / reader,
applicability consistency) and 8 (non-GET -> 405, flag off -> 404), plus the
independence of the three facts and the feed / duplicate / run-selection rules.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.tefca_registry.rce import issue_history_core as core
from app.tefca_registry.rce import record_check_results as rcr
from app.tefca_registry.rce.quality_rules import (RULE_BY_ID, RULES, NON_QUALITY_RULES,
                                                  RecordContext, declaration_hash,
                                                  rule_config_hash)

QHIN = "2.16.840.1.113883.4.391.1000"


# ── 2: declarations and the applicability / finding consistency ───────────────

def _corpus():
    npis = ["", "1234567893", "123", "1234567890", "12345abcde",
            "1234567893,1234567893", " 1234567893 ", "123456789a", "0000000000",
            "12345678901"]
    types = ["", "Participant", "Subparticipant", "QHIN"]
    part_of = ["", QHIN, "X", "9.99.777.1"]
    managing = ["", QHIN, "Y"]
    for npi, org, po, mo, ok in itertools.product(
            npis, types, part_of, managing, (True, False)):
        values = {"NPI": npi, "sequoiaorgtype": org, "partOf": po,
                  "orgManagingOrg": mo, "id": "9.99.777.1"} if ok else {}
        yield RecordContext(
            line_number=2, parse_status="ok" if ok else "field_count_mismatch",
            field_count=41 if ok else 40, values=values,
            dataset={"known_source_ids": {"X"}, "qhin_oids": {QHIN},
                     "registry_oids": set(), "expected_field_count": 41})


SLICE = [RULE_BY_ID[r] for r, _ in core.SLICE_LANES]


def test_exactly_the_eight_slice_rules_are_declared():
    declared = {r.rule_id for r in RULES if r.declared}
    assert declared == set(core.SLICE_RULE_IDS)
    assert len(declared) == 8
    # Everything else stays undeclared -> outcome U, never a pass.
    for rule in RULES:
        if rule.rule_id not in declared:
            assert rule.applies is None and not rule.declared
            assert declaration_hash(rule) is None


@pytest.mark.parametrize("rule", SLICE, ids=lambda r: r.rule_id)
def test_not_applicable_implies_no_finding_over_the_corpus(rule):
    checked = fired = 0
    for ctx in _corpus():
        checked += 1
        applies = rule.applies(ctx)
        findings = rule.evaluate(ctx)
        if findings:
            fired += 1
        if not applies:
            assert findings == [], (rule.rule_id, ctx.values, findings)
    assert checked > 900 and fired > 0  # the corpus actually exercises the rule


def test_declaration_hash_is_deterministic_and_distinguishes_declarations():
    first = {r.rule_id: declaration_hash(r) for r in SLICE}
    second = {r.rule_id: declaration_hash(r) for r in SLICE}
    assert first == second and all(len(h) == 64 for h in first.values())
    assert len(set(first.values())) == len(first)  # rule id is part of it
    # changing what a rule requires changes the hash
    r = RULE_BY_ID["NPI-002"]
    changed = type(r)(**{**r.__dict__, "requires_fields": ("NPI", "name")})
    assert declaration_hash(changed) != first["NPI-002"]


def test_the_config_hash_of_the_rule_set_is_unchanged_by_the_declarations():
    # Declarations are metadata: they must not alter the existing run identity
    # (rule_config_hash is recorded on every run and compared across runs).
    import hashlib, json
    payload = json.dumps(
        [{"rule_id": r.rule_id, "version": r.version, "severity": r.severity(),
          "category": r.category} for r in RULES], sort_keys=True,
        separators=(",", ":"))
    assert rule_config_hash() == hashlib.sha256(payload.encode()).hexdigest()


def test_later_stage_rules_are_not_quality_rules():
    ids = {r.rule_id for r in NON_QUALITY_RULES}
    assert core.LATER_STAGE_RULE_IDS <= ids
    assert all(not r.declared for r in NON_QUALITY_RULES)


def test_lane_fields_match_the_findings_the_rules_report():
    for rule_id, field in core.SLICE_LANES:
        names = {f.field_name for ctx in _corpus() for f in RULE_BY_ID[rule_id].evaluate(ctx)}
        assert names <= {field}, (rule_id, names)


# ── 4: result map writer and reader ───────────────────────────────────────────

IDS = ["A-1", "B-1", "C-1"]


def test_writer_accepts_a_well_formed_map():
    rcr.validate_for_write({"A-1": "P", "B-1": "F", "C-1": "U"}, 3, IDS)


@pytest.mark.parametrize("outcomes,count,why", [
    ({"A-1": "X"}, 1, "closed set"),                      # unknown code
    ({"A-1": "p"}, 1, "closed set"),                      # case matters
    ({"A-1": "P", "Z-9": "P"}, 2, "not in this run"),     # foreign rule id
    ({"A-1": "P", "B-1": "P"}, 3, "rule_count"),          # truncation
])
def test_writer_refuses_a_bad_map(outcomes, count, why):
    with pytest.raises(rcr.ResultMapInvalid, match=why):
        rcr.validate_for_write(outcomes, count, IDS)


def test_writer_refuses_an_unknown_map_version():
    with pytest.raises(rcr.ResultMapInvalid, match="map_version"):
        rcr.validate_for_write({"A-1": "P"}, 1, IDS, map_version=2)


# A-1 and B-1 are declared rules of the run (requires_hash set); C-1 is undeclared.
HIST = [{"rule_id": "A-1", "scope": None, "requires_hash": "h"},
        {"rule_id": "B-1", "scope": "RECORD", "requires_hash": "h"},
        {"rule_id": "C-1", "scope": None, "requires_hash": None}]


def test_reader_decodes_a_valid_map_and_supplies_u_for_an_unstored_undeclared_rule():
    codes, why = rcr.read_map(1, 2, {"A-1": "P", "B-1": "F"}, HIST)
    assert why is None and codes == {"A-1": "P", "B-1": "F", "C-1": "U"}
    # an explicit U for the undeclared rule is also valid
    codes, why = rcr.read_map(1, 3, {"A-1": "P", "B-1": "E", "C-1": "U"}, HIST)
    assert why is None and codes == {"A-1": "P", "B-1": "E", "C-1": "U"}


@pytest.mark.parametrize("version,count,outcomes", [
    (2, 2, {"A-1": "P", "B-1": "P"}),                     # unknown map_version
    (None, 2, {"A-1": "P", "B-1": "P"}),
    (1, 2, {"A-1": "P", "B-1": "Q"}),                     # unknown code
    (1, 1, {"A-1": "P"}),                                 # truncated: declared rule missing
    (1, 3, {"A-1": "P", "B-1": "P"}),                     # rule_count disagrees with keys
    (1, 1, {"A-1": "P", "B-1": "P"}),                     # rule_count disagrees with keys
    (1, 3, {"A-1": "P", "B-1": "P", "Z-9": "P"}),         # key not a rule of this run
    (1, 3, {"A-1": "P", "B-1": "P", "C-1": "P"}),         # undeclared rule cannot be a pass
    (1, 2, None),
    (1, 3, ["P", "P", "P"]),
])
def test_reader_never_guesses(version, count, outcomes):
    codes, why = rcr.read_map(version, count, outcomes, HIST)
    assert codes is None and why == "RESULT_SCHEMA_UNSUPPORTED"


def test_run_scope_rules_are_absent_from_the_expected_key_set():
    hist = HIST + [{"rule_id": "RUN-1", "scope": "RUN", "requires_hash": "h"}]
    codes, why = rcr.read_map(1, 2, {"A-1": "P", "B-1": "P"}, hist)
    assert why is None and "RUN-1" not in codes
    # a run-scope rule appearing in a per-record map is refused
    codes, why = rcr.read_map(1, 3, {"A-1": "P", "B-1": "P", "RUN-1": "P"}, hist)
    assert codes is None


def test_outcome_codes():
    npi2 = RULE_BY_ID["NPI-002"]
    ok = RecordContext(2, "ok", 41, {"NPI": "1234567893"}, {})
    bad = RecordContext(2, "ok", 41, {"NPI": "123"}, {})
    none = RecordContext(2, "ok", 41, {"NPI": ""}, {})
    assert rcr.outcome_code(npi2, ok, [], False) == "P"
    assert rcr.outcome_code(npi2, bad, npi2.evaluate(bad), False) == "F"
    assert rcr.outcome_code(npi2, none, [], False) == "N"
    assert rcr.outcome_code(npi2, ok, [], True) == "E"
    assert rcr.outcome_code(RULE_BY_ID["REQ-001"], ok, [], False) == "U"

    class Raises(type(npi2)):
        pass

    boom = type(npi2)(**{**npi2.__dict__,
                         "applies": lambda c: (_ for _ in ()).throw(ValueError("x"))})
    assert rcr.outcome_code(boom, ok, [], False) == "E"


# ── 2.1 feed scoping ──────────────────────────────────────────────────────────

def test_empty_settings_show_nothing_to_any_role():
    assert core.allowed_feeds(reviewer_or_above=False, viewer_setting="",
                              reviewer_setting="") == frozenset()
    assert core.allowed_feeds(reviewer_or_above=True, viewer_setting=None,
                              reviewer_setting="") == frozenset()


def test_viewer_sees_viewer_feeds_reviewer_adds_its_own():
    kw = dict(viewer_setting="ONC_RCE", reviewer_setting=" SYN , ONC_RCE,,")
    assert core.allowed_feeds(reviewer_or_above=False, **kw) == {"ONC_RCE"}
    assert core.allowed_feeds(reviewer_or_above=True, **kw) == {"ONC_RCE", "SYN"}
    # exact tags: no case folding
    assert core.parse_feed_list("onc_rce") == ("onc_rce",)


# ── 2.3 (revised): identical content stays distinct ──────────────────────────

def _intake(sha, day, feed="F", status="PARSED", n=None, dup=False):
    return {"id": uuid.UUID(int=n if n is not None else day * 7 + len(sha)),
            "sha256": sha, "feed": feed, "status": status, "duplicate_content": dup,
            "duplicate_of_intake_id": None,
            "received_at": datetime(2026, 9, day), "received_by": "u"}


def test_identical_hashes_are_distinct_deliveries_with_provenance_only():
    a, b, c = _intake("h1", 5), _intake("h1", 9), _intake("h2", 12)
    out = core.build_deliveries([b, c, a])
    assert [d["intake"]["id"] for d in out] == [a["id"], b["id"], c["id"]]
    first, second, third = out
    assert first["identical_content_of"] is None
    assert first["identical_content_also"] == [b["id"]]
    assert second["identical_content_of"] == a["id"]
    assert second["identical_content_also"] == [a["id"]]
    assert third["identical_content_of"] is None and third["identical_content_also"] == []


def test_same_hash_in_different_feeds_is_not_linked():
    out = core.build_deliveries([_intake("h1", 5, "F"), _intake("h1", 6, "G")])
    assert all(d["identical_content_of"] is None and not d["identical_content_also"]
               for d in out)


def test_order_is_received_at_then_id():
    lo, hi = _intake("h", 5, n=1), _intake("h", 5, n=2)
    out = core.build_deliveries([hi, lo])
    assert [d["intake"] for d in out] == [lo, hi]
    assert out[1]["identical_content_of"] == lo["id"]


def test_failed_intakes_are_not_deliveries_and_duplicate_flag_is_carried():
    failed, ok = _intake("h", 5, status="FAILED"), _intake("h", 6, dup=True)
    out = core.build_deliveries([ok, failed])
    assert [d["intake"] for d in out] == [ok]
    assert out[0]["duplicate_upload"] is True and out[0]["identical_content_of"] is None


# ── run selection ─────────────────────────────────────────────────────────────

def _run(n, status, start, end=None):
    t = datetime(2026, 9, 1)
    return {"id": uuid.UUID(int=n), "status": status,
            "started_at": t + timedelta(hours=start),
            "completed_at": (t + timedelta(hours=end)) if end is not None else None,
            "rule_set_version": "1.3.0", "error": None}


def test_current_run_is_latest_complete_and_newer_unfinished_runs_are_listed():
    runs = [_run(1, "COMPLETE", 0, 1), _run(2, "COMPLETE", 2, 3),
            _run(3, "FAILED", 4), _run(4, "RUNNING", 5), _run(5, "FAILED", -1)]
    sel = core.select_runs(runs)
    assert sel["selected"]["id"] == uuid.UUID(int=2)
    assert [r["id"].int for r in sel["newer_runs"]] == [3, 4]   # not the older failure
    assert [r["id"].int for r in sel["earlier_completed"]] == [1]


def test_only_unfinished_runs_means_no_selected_run():
    sel = core.select_runs([_run(1, "FAILED", 0), _run(2, "RUNNING", 1)])
    assert sel["selected"] is None and len(sel["newer_runs"]) == 2


def test_run_error_is_reduced_to_a_token():
    assert core.sanitize_error("ValueError: secret value 123 at /srv/x") == "ValueError"
    assert core.sanitize_error(None) is None


# ── the three facts, and their independence ───────────────────────────────────

def _facts(i, *, state="PRESENT", issue=False, code="P", version="1.2.0",
           status="PARSED", with_result=True, run=True, rule="NPI-002"):
    ids = [r.rule_id for r in RULES]
    outcomes = {r: "U" for r in ids}
    outcomes[rule] = "F" if issue else code
    iss = [{"rule_id": rule, "rule_version": version, "issue_type": "T",
            "severity": "HIGH", "field_name": "NPI", "id": uuid.UUID(int=i),
            "resolution": "OPEN", "correction_authority": "HUMAN_REQUIRED",
            "resolved_by": None, "resolved_at": None, "qa_approved_by": None,
            "qa_approved_at": None}] if issue else []
    hist = [{"rule_id": r, "rule_version": (version if r == rule else "1.0.0"),
             "requires_hash": ("h" if r == rule else None), "scope": "RECORD",
             "coverage": None, "execution_status": "COMPLETE"} for r in ids]
    return {"delivery_id": f"D{i}", "intake_status": status,
            "headers": ["NPI", "partOf"], "record_state": state,
            "record_parsed": {"NPI": "x"},
            "selected_run": ({"id": 1} if run else None),
            "history_rows": hist if run else [],
            "result": ({"map_version": 1, "rule_count": len(ids), "outcomes": outcomes}
                       if with_result else None),
            "issues": iss, "later_issues": []}


def cells(seq, rule="NPI-002"):
    return core.build_lane_cells(seq, rule, "NPI")


def test_fail_pass_fail_is_recurring_and_links_both():
    c = cells([_facts(1, issue=True), _facts(2), _facts(3, issue=True)])
    assert c[0]["recurrence"]["state"] == "FIRST_OBSERVED"
    assert c[1]["recurrence"] is None and c[1]["check"]["outcome"] == "PASS"
    assert c[1]["check"]["comparability"] == "COMPARABLE"
    r = c[2]["recurrence"]
    assert r["state"] == "RECURRING"
    assert r["earlier_occurrence"] == "D1" and r["comparable_pass"] == "D2"


def test_no_delivery_between_is_persistent_or_unverified_with_the_gap_text():
    c = cells([_facts(1, issue=True), _facts(3, issue=True)])
    assert c[1]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    gaps = core.gaps_from_lanes(c)
    assert gaps == [{"rule_id": "NPI-002", "field": "NPI", "from_delivery_id": "D1",
                     "to_delivery_id": "D3",
                     "text": "No snapshot or correction evidence available."}]


def test_absent_record_between_is_not_a_pass():
    c = cells([_facts(1, issue=True), _facts(2, state="ABSENT"), _facts(3, issue=True)])
    assert c[1]["check"] == {"outcome": "NOT_AVAILABLE", "comparability": "NOT_COMPARABLE",
                             "reason": "RECORD_ABSENT"}
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


def test_rule_version_change_is_not_credited_as_a_pass():
    c = cells([_facts(1, issue=True), _facts(2, version="1.3.0"), _facts(3, issue=True)])
    assert c[1]["check"]["outcome"] == "PASS"
    assert c[1]["check"]["comparability"] == "NOT_COMPARABLE"
    assert c[1]["check"]["reason"] == "RULE_VERSION_CHANGED"
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


@pytest.mark.parametrize("code,reason", [
    ("N", "APPLICABILITY_NOT_MET"), ("E", "RULE_ERROR"), ("S", "RULE_SKIPPED"),
    ("U", "APPLICABILITY_UNDECLARED")])
def test_na_error_skipped_and_undeclared_are_never_a_pass(code, reason):
    c = cells([_facts(1, issue=True), _facts(2, code=code), _facts(3, issue=True)])
    assert c[1]["check"]["outcome"] != "PASS"
    assert c[1]["check"]["comparability"] == "NOT_COMPARABLE"
    assert c[1]["check"]["reason"] == reason
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


def test_no_completed_run_and_failed_intake():
    c = cells([_facts(1, run=False), _facts(2, status="FAILED")])
    for cell in c:
        assert cell["check"]["reason"] == "NO_COMPLETED_RUN"
        assert cell["check"]["outcome"] == "NOT_AVAILABLE"


def test_duplicate_oid_in_delivery_is_not_comparable():
    c = cells([_facts(1, state="DUPLICATE")])
    assert c[0]["check"]["reason"] == "DUPLICATE_OID_IN_DELIVERY"
    assert c[0]["issue"] is None and c[0]["recurrence"] is None


def test_unpersisted_result_is_not_a_pass_and_shows_the_finding_if_there_is_one():
    c = cells([_facts(1, issue=True, with_result=False), _facts(2, with_result=False),
               _facts(3, issue=True)])
    assert c[0]["check"]["outcome"] == "FAIL"
    assert c[0]["check"]["reason"] == "CHECK_RESULT_NOT_PERSISTED"
    assert c[1]["check"]["outcome"] == "NOT_RECORDED"
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


def test_ledger_and_map_disagreement_is_not_trusted():
    f = _facts(1, issue=True)
    f["result"]["outcomes"]["NPI-002"] = "P"          # map says pass, ledger has a finding
    c = cells([f])
    assert c[0]["check"]["reason"] == "RESULT_ISSUE_MISMATCH"
    assert c[0]["recurrence"]["state"] == "NOT_EVALUATED"


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(map_version=2),
    lambda r: r.update(rule_count=r["rule_count"] - 1),
    lambda r: r["outcomes"].pop("NPI-002"),
    lambda r: r["outcomes"].update({"NPI-002": "Q"}),
])
def test_unsupported_map_is_reported_not_guessed(mutate):
    f = _facts(1, issue=True)
    mutate(f["result"])
    c = cells([f])
    assert c[0]["check"]["reason"] == "RESULT_SCHEMA_UNSUPPORTED"
    assert c[0]["check"]["comparability"] == "NOT_COMPARABLE"


def _qa_approved(d, by="qa@x", resolved_by="rev@x"):
    d["issues"][0].update(resolution="APPROVED", resolved_by=resolved_by,
                          resolved_at=datetime(2026, 8, 1), qa_approved_by=by,
                          qa_approved_at=datetime(2026, 8, 1),
                          correction_authority="QA_REQUIRED")
    return d


def test_qa_and_recurrence_are_independent_in_both_directions():
    base = [_facts(1, issue=True), _facts(3, issue=True)]
    plain = cells(base)
    approved = cells([_qa_approved(_facts(1, issue=True)), _facts(3, issue=True)])
    # changing QA never changes recurrence (or any check)
    assert [c["recurrence"] for c in plain] == [c["recurrence"] for c in approved]
    assert [c["check"] for c in plain] == [c["check"] for c in approved]
    assert approved[0]["qa"]["status"] == "QA_APPROVED"
    assert approved[1]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"
    assert approved[1]["note"] == "approved resolution not followed by a comparable passing check"
    # changing the checks never changes qa
    with_pass = cells([_qa_approved(_facts(1, issue=True)), _facts(2), _facts(3, issue=True)])
    assert with_pass[0]["qa"] == approved[0]["qa"]
    assert with_pass[2]["recurrence"]["state"] == "RECURRING"
    assert with_pass[2]["qa"]["status"] == "NONE"


def test_qa_needs_an_independent_approver():
    same = core.classify_qa(_qa_approved(_facts(1, issue=True), by="rev@x")["issues"][0])
    assert same["status"] == "AWAITING_QA"
    ok = core.classify_qa(_qa_approved(_facts(1, issue=True))["issues"][0])
    assert ok["status"] == "QA_APPROVED" and ok["role"] == "qa_approver"
    assert ok["decision"] == "APPROVED" and ok["timestamp"] == datetime(2026, 8, 1)
    returned = _facts(1, issue=True)["issues"][0]
    returned["resolution"] = "REJECTED"
    assert core.classify_qa(returned)["status"] == "QA_RETURNED"
    assert core.classify_qa(None)["status"] == "NONE"
    closed = _facts(1, issue=True)["issues"][0]
    closed.update(resolution="RESOLVED")
    assert core.classify_qa(closed)["decision"] == "CLOSED"   # never says "resolved"


def _later(run_id, selected_id=7, runs=None):
    f = _facts(1)
    f["selected_run"] = {"id": selected_id}
    f["runs_by_id"] = runs if runs is not None else {
        uuid.UUID(int=9): {"status": "COMPLETE", "completed_at": datetime(2026, 9, 1)}}
    f["later_issues"] = [{"rule_id": "NPI-005", "rule_version": "1.2.0",
                          "issue_type": "NPI_NOT_FOUND", "severity": "MEDIUM",
                          "field_name": "NPI", "id": uuid.uuid4(), "run_id": run_id,
                          "resolution": "OPEN", "correction_authority": "HUMAN_REQUIRED"}]
    return core.later_stage_findings(f)


def test_later_stage_findings_are_outside_lanes_and_labelled_with_their_run():
    other = uuid.UUID(int=9)
    (x,) = _later(other)
    assert x["from_selected_run"] is False
    assert x["run_note"] == f"from run {str(other)[:8]} (not the selected run)"
    assert x["run_status"] == "COMPLETE" and x["run_completed_at"] == datetime(2026, 9, 1)
    assert x["check"] == {"outcome": "OBSERVED", "comparability": "NOT_COMPARABLE",
                          "reason": "EXTERNAL_COVERAGE_NOT_TRACKED_PER_RULE"}
    assert x["recurrence"]["state"] == "NOT_EVALUATED"
    assert x["recurrence"]["reason"] == "EXTERNAL_COVERAGE_NOT_TRACKED_PER_RULE"
    (y,) = _later(7)
    assert y["from_selected_run"] is True and y["run_note"] == "from the selected run"
    (z,) = _later(None)
    assert z["from_selected_run"] is False and z["run_note"] == "run not recorded"
    assert z["run_id"] is None and z["run_status"] is None
    # lanes never carry a later-stage rule
    assert not hasattr(core, "later_stage_cells")
    assert {r for r, _ in core.SLICE_LANES}.isdisjoint(core.LATER_STAGE_RULE_IDS)


def _int2(i, cov, issue=False):
    d = _facts(i, issue=issue, rule="INT-002")
    for h in d["history_rows"]:
        if h["rule_id"] == "INT-002":
            h["coverage"] = cov
    d["headers"] = ["partOf"]
    return d


def test_int002_requires_recorded_coverage_with_a_watermark_on_both_runs():
    seq = [_int2(1, {"delivery_ids": 3, "registry_watermark": "w"}), _int2(2, None)]
    c = core.build_lane_cells(seq, "INT-002", "partOf")
    assert c[0]["check"]["reason"] == "COVERAGE_NOT_RECORDED"
    # counts without a watermark are not enough
    seq = [_int2(1, {"delivery_ids": 3}), _int2(2, {"registry_watermark": "w"})]
    assert core.build_lane_cells(seq, "INT-002", "partOf")[0]["check"]["reason"] ==         "COVERAGE_NOT_RECORDED"
    ok = core.build_lane_cells([_int2(1, {"registry_watermark": "w"}),
                                _int2(2, {"registry_watermark": "w"})], "INT-002", "partOf")
    assert ok[0]["check"]["comparability"] == "COMPARABLE"


def test_int002_pass_then_fail_is_recurring_only_when_the_registry_state_matches():
    same = core.build_lane_cells(
        [_int2(1, {"registry_watermark": "w"}, issue=True),
         _int2(2, {"registry_watermark": "w"}),
         _int2(3, {"registry_watermark": "w"}, issue=True)], "INT-002", "partOf")
    assert same[2]["recurrence"]["state"] == "RECURRING"
    diff = core.build_lane_cells(
        [_int2(1, {"registry_watermark": "w"}, issue=True),
         _int2(2, {"registry_watermark": "w"}),
         _int2(3, {"registry_watermark": "w2"}, issue=True)], "INT-002", "partOf")
    rec = diff[2]["recurrence"]
    assert rec["state"] == "PERSISTENT_OR_UNVERIFIED" and rec["reason"] == "REGISTRY_STATE_DIFFERS"
    assert "registry state differed" in diff[2]["note"]
    assert diff[1]["check"]["reason"] == "REGISTRY_STATE_DIFFERS"


# ── redaction guard ───────────────────────────────────────────────────────────

def test_forbidden_key_scan_is_recursive():
    clean = {"a": [{"b": {"c": 1}}]}
    assert core.forbidden_keys_present(clean) == []
    dirty = {"a": [{"b": {"rationale": 1}}], "c": ({"parsed": 2},)}
    assert sorted(core.forbidden_keys_present(dirty)) == ["parsed", "rationale"]


# ── 8: access: non-GET and flag-off through the real router ───────────────────

@pytest.fixture
def app_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.tefca_registry.rce import issue_history_routes as routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_non_get_is_405(app_client, method):
    url = "/api/tefca/rce/entities/by-oid/SYN-OID-0001/issue-history"
    assert getattr(app_client, method)(url).status_code == 405


def test_the_module_defines_only_get_routes():
    from app.tefca_registry.rce import issue_history_routes as routes

    methods = {m for r in routes.router.routes for m in r.methods}
    assert methods == {"GET"}


def test_route_is_registered_in_the_application():
    from app.main import app

    paths = app.openapi()["paths"]
    entry = paths["/api/tefca/rce/entities/by-oid/{oid}/issue-history"]
    assert set(entry) == {"get"}


def test_flag_off_is_the_default_and_answers_404(monkeypatch):
    import asyncio

    from fastapi import HTTPException

    from app.core.config import settings
    from app.tefca_registry.rce import issue_history_routes as routes

    assert settings.ENABLE_ISSUE_HISTORY is False
    assert settings.ENABLE_RECORD_CHECK_RESULTS is False
    assert settings.ISSUE_HISTORY_FEEDS_VIEWER == ""
    assert settings.ISSUE_HISTORY_FEEDS_REVIEWER == ""
    user = SimpleNamespace(id=None, email="v@x", role="viewer")
    request = SimpleNamespace(headers={}, client=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.issue_history_route("SYN-OID-0001", request, 12, None,
                                               db=None, user=user))
    assert exc.value.status_code == 404 and exc.value.detail == "NOT_FOUND"
