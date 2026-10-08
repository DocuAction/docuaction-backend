"""Issue history, track A6 (2026-10-08): explicit gaps, comparable evidence,
account-level feed narrowing. Pure tests: no database.

  (a) a missing delivery, an absent / re-keyed entity, a delivery with no
      completed run and an unavailable reference source are STATED as gaps and
      never read as a clear result;
  (b) a pass is credited across deliveries only when rule version, requirement
      declaration, scope, schema and as-of basis (INT-002 registry state) match;
  (c) `feed:<TAG>` entries in users.allowed_modules NARROW the role's feeds and
      never widen them.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.tefca_registry.rce import issue_history_core as core

import test_issue_history_pure_2026_10_07 as base

_facts, cells = base._facts, base.cells


# -- (b) comparability beyond the rule version --------------------------------

def _with_scope(facts, scope):
    for h in facts["history_rows"]:
        h["scope"] = scope
    return facts


def test_scope_change_is_not_credited_as_a_pass_and_names_the_reason():
    c = cells([_facts(1, issue=True), _with_scope(_facts(2), "POPULATION"),
               _facts(3, issue=True)])
    assert c[1]["check"] == {"outcome": "PASS", "comparability": "NOT_COMPARABLE",
                             "reason": "RULE_SCOPE_CHANGED"}
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


def test_requirement_declaration_change_is_not_credited_as_a_pass():
    mid = _facts(2)
    for h in mid["history_rows"]:
        if h["rule_id"] == "NPI-002":
            h["requires_hash"] = "other"
    c = cells([_facts(1, issue=True), mid, _facts(3, issue=True)])
    assert c[1]["check"]["reason"] == "REQUIRES_CHANGED"
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


def test_schema_change_is_not_credited_as_a_pass():
    mid = _facts(2)
    mid["headers"] = ["partOf"]               # the lane's delivered field is gone
    c = cells([_facts(1, issue=True), mid, _facts(3, issue=True)])
    assert c[1]["check"]["reason"] == "SCHEMA_CHANGED"
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


def test_identical_evidence_is_comparable_and_only_then_recurring():
    c = cells([_facts(1, issue=True), _facts(2), _facts(3, issue=True)])
    assert c[1]["check"]["comparability"] == "COMPARABLE"
    assert c[2]["recurrence"]["state"] == "RECURRING"


def _int2(i, cov, issue=False):
    d = _facts(i, issue=issue, rule="INT-002")
    for h in d["history_rows"]:
        if h["rule_id"] == "INT-002":
            h["coverage"] = cov
    d["headers"] = ["partOf"]
    return d


EMPTY = {"delivery_ids": 0, "registry_oids": 0, "qhin_oids": 0, "registry_watermark": "w"}
SOME = {"delivery_ids": 3, "registry_oids": 0, "qhin_oids": 1, "registry_watermark": "w"}


def test_pass_against_an_entirely_empty_reference_source_is_not_clear():
    c = core.build_lane_cells(
        [_int2(1, SOME, issue=True), _int2(2, EMPTY), _int2(3, SOME, issue=True)],
        "INT-002", "partOf")
    assert c[1]["check"] == {"outcome": "PASS", "comparability": "NOT_COMPARABLE",
                             "reason": "SOURCE_UNAVAILABLE"}
    assert c[2]["recurrence"]["state"] == "PERSISTENT_OR_UNVERIFIED"


def test_a_partly_empty_source_is_still_a_usable_pass():
    # only an explicit ALL-zero coverage is "unavailable"; an empty registry
    # alone does not stop the delivery's own ids and QHIN OIDs from resolving
    c = core.build_lane_cells(
        [_int2(1, SOME, issue=True), _int2(2, SOME), _int2(3, SOME, issue=True)],
        "INT-002", "partOf")
    assert c[1]["check"]["comparability"] == "COMPARABLE"
    assert c[2]["recurrence"]["state"] == "RECURRING"


def test_a_finding_is_kept_when_the_source_was_empty():
    c = core.build_lane_cells([_int2(1, EMPTY, issue=True)], "INT-002", "partOf")
    assert c[0]["check"]["outcome"] == "FAIL"        # a finding is never hidden


# -- (a) explicit gaps ---------------------------------------------------------

def _item(i, day, **kw):
    f = _facts(i, **kw)
    f["intake"] = {"received_at": datetime(2026, 7, day)}
    return f


def _gaps(items, failed=(), feed="F"):
    by = {f["delivery_id"]: core.build_lane_cells([f], "NPI-002", "NPI")
          for f in items}
    return core.sequence_gaps(feed, items, by, failed)


def test_a_clean_sequence_has_no_gaps():
    assert _gaps([_item(1, 1), _item(2, 2)]) == []


def test_absent_entity_is_a_gap_that_does_not_claim_removal_or_clear():
    (g,) = _gaps([_item(1, 1), _item(2, 2, state="ABSENT"), _item(3, 3)])
    assert g["kind"] == "ENTITY_ABSENT_OR_REKEYED" and g["delivery_id"] == "D2"
    assert "re-keyed" in g["text"] and "gap" in g["text"]


def test_delivery_without_a_completed_run_is_a_gap():
    (g,) = _gaps([_item(1, 1), _item(2, 2, run=False)])
    assert g["kind"] == "NO_COMPLETED_RUN" and g["delivery_id"] == "D2"


def test_duplicate_oid_is_a_gap():
    (g,) = _gaps([_item(1, 1, state="DUPLICATE")])
    assert g["kind"] == "DUPLICATE_OID_IN_DELIVERY"


def test_failed_intake_after_the_first_delivery_is_a_gap_and_before_it_is_not():
    failed = [{"feed": "F", "received_at": datetime(2026, 7, 2)},
              {"feed": "F", "received_at": datetime(2026, 6, 1)},
              {"feed": "OTHER", "received_at": datetime(2026, 7, 2)}]
    gaps = _gaps([_item(1, 1), _item(3, 3)], failed)
    assert [(g["kind"], g["delivery_id"]) for g in gaps] == [
        ("DELIVERY_NOT_PROCESSED", None)]
    assert gaps[0]["received_at"] == datetime(2026, 7, 2)


def test_rule_error_skip_and_unavailable_source_are_gaps_per_lane():
    items = [_item(1, 1, code="E"), _item(2, 2, code="S")]
    kinds = [(g["kind"], g["rule_id"]) for g in _gaps(items)]
    assert kinds == [("RULE_ERROR", "NPI-002"), ("RULE_SKIPPED", "NPI-002")]
    d = _int2(1, EMPTY)
    d["intake"] = {"received_at": datetime(2026, 7, 1)}
    cells_ = {d["delivery_id"]: core.build_lane_cells([d], "INT-002", "partOf")}
    (g,) = core.sequence_gaps("F", [d], cells_, [])
    assert g["kind"] == "SOURCE_UNAVAILABLE" and g["rule_id"] == "INT-002"


def test_gap_entries_carry_metadata_only():
    (g,) = _gaps([_item(1, 1, state="ABSENT")])
    assert set(g) == {"kind", "feed", "delivery_id", "received_at", "rule_id",
                      "field", "text"}
    assert core.forbidden_keys_present(g) == []


# -- (c) account-level narrowing -----------------------------------------------

ROLE = frozenset({"ONC", "SYN"})


@pytest.mark.parametrize("modules,expected", [
    (None, ROLE), ([], ROLE), (["dashboard", "reports"], ROLE),
    (["feed:ONC"], frozenset({"ONC"})),
    (["feed:ONC", "feed:SYN", "reports"], ROLE),
    (["feed: ONC "], frozenset({"ONC"})),
    (["feed:OTHER"], frozenset()),                  # cannot widen
    (["feed:"], frozenset()),                       # blank entry narrows to nothing
    (["feed:ONC", "feed:OTHER"], frozenset({"ONC"})),
    ("feed:ONC", frozenset()),                      # malformed => fail closed
    ({"feed": "ONC"}, frozenset()),
    (["feed:onc"], frozenset()),                    # exact tags, no case folding
])
def test_account_feed_entries_only_narrow_the_role_feeds(modules, expected):
    assert core.narrow_feeds(ROLE, modules) == expected


def test_narrowing_never_widens_an_empty_role_scope():
    assert core.narrow_feeds(frozenset(), ["feed:ONC"]) == frozenset()
    assert core.narrow_feeds(frozenset(), None) == frozenset()


def test_user_feed_tags_distinguishes_unset_from_empty():
    assert core.user_feed_tags(None) is None
    assert core.user_feed_tags([]) is None
    assert core.user_feed_tags(["feed:"]) == ()
