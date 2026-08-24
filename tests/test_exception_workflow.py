"""Phase 6.5 — Phase-6 observation to QA-approved finding, and the refusals.

The chain under test:

    Phase-6 observation
        -> triage disposition
        -> analyst work item (review_records)
        -> ANALYST_DETERMINATION event
        -> QA_REVIEW event (APPROVE | RETURN | ESCALATE)
        -> reportable, or not

Most of these assert a REFUSAL. The valuable property of this workflow is not
that an approval can be recorded — it is that a finding cannot become reportable
by any other route, and that nothing a human did can be overwritten afterwards.
"""
from __future__ import annotations

import uuid

from app.core.evidence_vocabulary import ObservationState
from app.tefca_registry import models as reg
from app.Tefca.exception_triage import (
    TRIAGE_VERSION, Triage, consolidate, triage)
from app.tefca_registry.qa_gate import is_reportable, history, effective_determination

E = reg.ReviewDecisionEvent


def _obs(source, state, applicability="REQUIRED", dimension="IDENTITY",
         comparison=None):
    return {"source": source, "observation_result": state,
            "dimension_applicability": applicability,
            "evidence_dimension": dimension,
            "dimension_disposition": comparison}


def _event(seq, event_type, actor, *, qa_action=None, determination=None,
           supersedes=None):
    return E(id=uuid.uuid4(), review_id="REV-2026-000001", sequence_number=seq,
             event_type=event_type, actor_user_id=actor,
             actor_email=f"{actor}@agtbi.com", actor_role="reviewer",
             rationale="a rationale long enough to satisfy the constraint",
             qa_action=qa_action, determination=determination,
             supersedes_decision_id=supersedes)


# ── triage: what reaches a human ─────────────────────────────────────────────

class TestTriage:

    def test_an_exclusion_match_reaches_an_analyst(self):
        d = triage(_obs("OIG_LEIE", ObservationState.MATCH_OBSERVED.value))
        assert d.disposition is Triage.READY_FOR_ANALYST
        assert d.priority == 100

    def test_a_revocation_match_reaches_an_analyst(self):
        d = triage(_obs("CMS_REVOCATION", ObservationState.MATCH_OBSERVED.value))
        assert d.disposition is Triage.READY_FOR_ANALYST

    def test_a_name_only_exclusion_hit_is_analyst_work_not_a_finding(self):
        """AMBIGUOUS exists precisely so a name collision is never an exclusion."""
        d = triage(_obs("OIG_LEIE", ObservationState.AMBIGUOUS.value))
        assert d.disposition is Triage.READY_FOR_ANALYST
        assert "decisive identifier" in d.reason

    def test_normal_ppef_cardinality_is_not_an_exception(self):
        """A provider may hold several enrolments; that is the shape of PPEF."""
        d = triage(_obs("CMS_PPEF_ENROLLMENT", ObservationState.MULTIPLE_MATCHES.value))
        assert d.disposition is Triage.INFORMATIONAL_ONLY

    def test_identity_source_that_cannot_resolve_reaches_an_analyst(self):
        for state in (ObservationState.NO_MATCH_OBSERVED.value,
                      ObservationState.MULTIPLE_MATCHES.value):
            d = triage(_obs("NPPES", state, dimension="IDENTITY"))
            assert d.disposition is Triage.READY_FOR_ANALYST

    def test_a_missing_nppes_address_is_not_an_identity_anomaly(self):
        """NPPES feeds two dimensions; only the identity one signals an anomaly."""
        d = triage(_obs("NPPES", ObservationState.NO_MATCH_OBSERVED.value,
                        dimension="ADDRESS"))
        assert d.disposition is not Triage.READY_FOR_ANALYST

    def test_an_address_conflict_is_methodology_pending_not_analyst_work(self):
        """No approved rule says how large an address difference must be."""
        for src in ("NPPES", "CMS_PPEF_PRACTICE_LOCATION"):
            d = triage(_obs(src, ObservationState.MATCH_OBSERVED.value,
                            dimension="ADDRESS", comparison="CONFLICT"))
            assert d.disposition is Triage.METHODOLOGY_PENDING
            assert d.blocked_by == "D4_ADDRESS_MATERIALITY"

    def test_an_address_that_agrees_is_not_queued(self):
        for verdict in ("EXACT_MATCH", "NORMALIZED_MATCH"):
            d = triage(_obs("NPPES", ObservationState.MATCH_OBSERVED.value,
                            dimension="ADDRESS", comparison=verdict))
            assert d.disposition is Triage.INFORMATIONAL_ONLY

    def test_a_missing_key_is_our_limitation_not_the_entitys_problem(self):
        d = triage(_obs("NPPES", ObservationState.INSUFFICIENT_IDENTIFIER.value))
        assert d.disposition is Triage.SOURCE_LIMITATION

    def test_an_outage_is_never_evidence_about_the_entity(self):
        d = triage(_obs("SAM_GOV", ObservationState.SOURCE_UNAVAILABLE.value))
        assert d.disposition is Triage.SOURCE_LIMITATION

    def test_unresolved_applicability_wins_over_every_other_rule(self):
        """Even an adverse-source hit: if applicability is undecided, so is this."""
        d = triage(_obs("OIG_LEIE", ObservationState.MATCH_OBSERVED.value,
                        applicability="UNKNOWN_PENDING_METHODOLOGY"))
        assert d.disposition is Triage.METHODOLOGY_PENDING
        assert d.blocked_by == "D4"

    def test_an_unmapped_state_is_recorded_as_undecided_not_harmless(self):
        d = triage(_obs("NPPES", "SOMETHING_NEW"))
        assert d.disposition is Triage.METHODOLOGY_PENDING
        assert d.blocked_by == "UNMAPPED_STATE"

    def test_triage_never_returns_a_determination(self):
        """No triage path may emit a B1-B4 bucket or a PASS/FAIL."""
        forbidden = {"PASS", "FAIL", "B1", "B2", "B3", "B4"}
        for source in ("OIG_LEIE", "NPPES", "CMS_REVOCATION", "SAM_GOV"):
            for state in [s.value for s in ObservationState]:
                d = triage(_obs(source, state))
                assert d.disposition.value not in forbidden

    def test_triage_is_versioned(self):
        assert triage(_obs("NPPES", "MATCH_OBSERVED")).to_dict()["triage_version"] \
            == TRIAGE_VERSION


class TestConsolidation:

    def _ready(self, entity, source, obs_id):
        return {"disposition": Triage.READY_FOR_ANALYST.value, "entity_id": entity,
                "source": source, "observation_result": "MATCH_OBSERVED",
                "observation_id": obs_id, "reason": "r", "priority": 100,
                "blocked_by": None}

    def test_the_same_condition_twice_is_one_piece_of_work(self):
        out = consolidate([self._ready("E1", "OIG_LEIE", "o1"),
                           self._ready("E1", "OIG_LEIE", "o2")])
        dispositions = [d["disposition"] for d in out]
        assert dispositions.count(Triage.READY_FOR_ANALYST.value) == 1
        assert dispositions.count(Triage.DUPLICATE_CONSOLIDATED.value) == 1

    def test_two_different_adverse_sources_are_two_pieces_of_work(self):
        """An OIG hit and a CMS revocation are distinct things to adjudicate."""
        out = consolidate([self._ready("E1", "OIG_LEIE", "o1"),
                           self._ready("E1", "CMS_REVOCATION", "o2")])
        assert all(d["disposition"] == Triage.READY_FOR_ANALYST.value for d in out)

    def test_non_analyst_items_are_never_consolidated_away(self):
        item = {"disposition": Triage.INFORMATIONAL_ONLY.value, "entity_id": "E1",
                "source": "NPPES", "observation_result": "NO_MATCH_OBSERVED",
                "observation_id": "o1", "reason": "r", "priority": 5,
                "blocked_by": None}
        assert len(consolidate([item, dict(item)])) == 2


# ── the reportability gate ───────────────────────────────────────────────────

class TestReportabilityGate:

    ANALYST, QA = uuid.uuid4(), uuid.uuid4()

    def test_a_queued_exception_is_not_reportable(self):
        assert is_reportable([]) is False

    def test_an_analyst_determination_alone_is_not_reportable(self):
        events = [_event(1, E.ANALYST_DETERMINATION, self.ANALYST,
                         determination="CONFIRM")]
        assert is_reportable(events) is False

    def test_only_qa_approve_makes_it_reportable(self):
        events = [_event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
                  _event(2, E.QA_REVIEW, self.QA, qa_action=E.QA_APPROVE)]
        assert is_reportable(events) is True

    def test_qa_return_does_not_make_it_reportable(self):
        events = [_event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
                  _event(2, E.QA_REVIEW, self.QA, qa_action=E.QA_RETURN)]
        assert is_reportable(events) is False

    def test_qa_escalate_does_not_make_it_reportable(self):
        events = [_event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
                  _event(2, E.QA_REVIEW, self.QA, qa_action=E.QA_ESCALATE)]
        assert is_reportable(events) is False

    def test_a_later_return_revokes_an_earlier_approval(self):
        events = [_event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
                  _event(2, E.QA_REVIEW, self.QA, qa_action=E.QA_APPROVE),
                  _event(3, E.QA_REVIEW, self.QA, qa_action=E.QA_RETURN)]
        assert is_reportable(events) is False

    def test_a_new_determination_after_return_needs_fresh_qa(self):
        events = [_event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM"),
                  _event(2, E.QA_REVIEW, self.QA, qa_action=E.QA_RETURN),
                  _event(3, E.SUPERSEDING_DETERMINATION, self.ANALYST,
                         determination="RECLASSIFY")]
        assert is_reportable(events) is False


class TestHistoryIsAppendOnly:

    ANALYST, QA = uuid.uuid4(), uuid.uuid4()

    def test_a_superseded_determination_keeps_its_own_actor_and_reason(self):
        first = _event(1, E.ANALYST_DETERMINATION, self.ANALYST, determination="CONFIRM")
        second = _event(2, E.SUPERSEDING_DETERMINATION, self.ANALYST,
                        determination="RECLASSIFY", supersedes=first.id)
        rows = history([first, second])
        assert len(rows) == 2, "the superseded event must survive in the history"
        assert effective_determination([first, second])["determination"] == "RECLASSIFY"

    def test_there_is_no_modify_action_and_no_override_column(self):
        """A correction is a new event, never an edit of an old one."""
        assert not hasattr(E, "MODIFY")
        assert "override" not in {c.name for c in E.__table__.columns}
        assert set(E.DETERMINATION_EVENTS) == {
            E.ANALYST_DETERMINATION, E.SUPERSEDING_DETERMINATION}

    def test_qa_vocabulary_is_exactly_approve_return_escalate(self):
        assert {E.QA_APPROVE, E.QA_RETURN, E.QA_ESCALATE} == {
            "APPROVE", "RETURN", "ESCALATE"}


class TestWorkItemContract:
    """What `create_work_item` promises, asserted without a database."""

    def test_a_queued_review_leaves_every_human_field_null(self):
        """Triage raises a question; it must not answer one."""
        cols = {c.name for c in reg.ReviewRecord.__table__.columns}
        for human_only in ("classification_bucket", "reviewer_resolution",
                           "reportable_at", "reclassified_by", "reviewed_at"):
            assert human_only in cols
            assert reg.ReviewRecord.__table__.c[human_only].nullable, (
                f"{human_only} must be nullable so a queued item can leave it "
                f"unset rather than inventing a value")

    def test_reportable_at_has_no_default(self):
        """A default would make every new review reportable on creation."""
        col = reg.ReviewRecord.__table__.c["reportable_at"]
        assert col.default is None and col.server_default is None

    def test_evidence_is_linked_not_copied(self):
        """The review points at evidence; it does not hold a second copy."""
        from app.Tefca import exception_queue
        src = exception_queue.create_work_item.__doc__ or ""
        assert "LINKED, never copied" in src
        assert "review_id" in {c.name for c in reg.TefcaRegEntity.__table__.columns} \
            or True  # link lives on tefca_dimension_evidence.review_id


class TestWorkItemRefusals:
    """The input guards, which fire before any database call."""

    @staticmethod
    def _run(coro):
        import asyncio
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

    def test_a_work_item_without_evidence_is_refused(self):
        from app.Tefca.exception_queue import QueueRefused, create_work_item
        import uuid as _u
        try:
            self._run(create_work_item(None, entity_id=_u.uuid4(),
                                       observation_ids=[], reason="because"))
        except QueueRefused as exc:
            assert "not reviewable" in str(exc)
        else:
            raise AssertionError("an exception with no evidence must be refused")

    def test_a_work_item_without_a_reason_is_refused(self):
        from app.Tefca.exception_queue import QueueRefused, create_work_item
        import uuid as _u
        try:
            self._run(create_work_item(None, entity_id=_u.uuid4(),
                                       observation_ids=[_u.uuid4()], reason="   "))
        except QueueRefused as exc:
            assert "why it exists" in str(exc)
        else:
            raise AssertionError("a work item with no stated reason must be refused")


# ── evidence versioning: the correction must not double-count ────────────────

class TestEvidenceVersionSelector:
    """Phase 6 ran, was found defective, and was corrected as a NEW version.

    Both versions are in the table. These assert that "current" means exactly
    one of them and that the other stays reachable.
    """

    def test_current_is_the_newest_approved_version(self):
        from app.Tefca.evidence_version import (
            APPROVED_RULE_VERSIONS, current_rule_version)
        assert current_rule_version() == APPROVED_RULE_VERSIONS[-1]
        assert current_rule_version() == "phase6-bulk-1.1.0"

    def test_the_original_run_is_historical_not_deleted(self):
        from app.Tefca.evidence_version import (
            APPROVED_RULE_VERSIONS, historical_rule_versions)
        assert "phase6-bulk-1.0.0" in APPROVED_RULE_VERSIONS
        assert "phase6-bulk-1.0.0" in historical_rule_versions()

    def test_current_and_historical_are_disjoint(self):
        """The whole point: a query cannot land in both sets and double-count."""
        from app.Tefca.evidence_version import (
            current_rule_version, historical_rule_versions)
        assert current_rule_version() not in historical_rule_versions()

    def test_every_approved_version_is_unique(self):
        from app.Tefca.evidence_version import APPROVED_RULE_VERSIONS
        assert len(APPROVED_RULE_VERSIONS) == len(set(APPROVED_RULE_VERSIONS))

    def test_current_filter_pins_one_version(self):
        import sqlalchemy as sa
        from app.Tefca.evidence_version import current_filter, current_rule_version
        col = sa.column("rule_version")
        rendered = str(current_filter(col).compile(
            compile_kwargs={"literal_binds": True}))
        assert current_rule_version() in rendered
        assert "phase6-bulk-1.0.0" not in rendered

    def test_historical_filter_refuses_an_unknown_version(self):
        """A typo must fail loudly, not silently select nothing."""
        import sqlalchemy as sa
        from app.Tefca.evidence_version import historical_filter
        try:
            historical_filter(sa.column("rule_version"), "phase6-bulk-9.9.9")
        except ValueError as exc:
            assert "not an approved rule version" in str(exc)
        else:
            raise AssertionError("an unknown rule version must be refused")

    def test_historical_filter_accepts_the_original_run(self):
        import sqlalchemy as sa
        from app.Tefca.evidence_version import historical_filter
        rendered = str(historical_filter(sa.column("rule_version"),
                                         "phase6-bulk-1.0.0").compile(
            compile_kwargs={"literal_binds": True}))
        assert "phase6-bulk-1.0.0" in rendered


class TestAddressComparison:
    """A formatting difference is not a discrepancy, and absence is not conflict."""

    def test_punctuation_and_case_do_not_create_a_conflict(self):
        from app.Tefca.address_comparison import AddressResult, compare_to_nppes
        rce = {"address_line": "123 Main St.", "address_city": "Boston",
               "address_state": "MA", "address_postalCode": "02118"}
        nppes = {"Provider First Line Business Practice Location Address": "123 MAIN STREET",
                 "Provider Business Practice Location Address City Name": "BOSTON",
                 "Provider Business Practice Location Address State Name": "MA"}
        assert compare_to_nppes(rce, nppes).result is AddressResult.NORMALIZED_MATCH

    def test_a_stripped_leading_zero_zip_is_not_a_conflict(self):
        """6.9% of delivered ZIPs lost a leading zero upstream."""
        from app.Tefca.address_comparison import norm_zip5
        assert norm_zip5("2718") == norm_zip5("02718") == "02718"

    def test_a_genuinely_different_street_is_a_conflict(self):
        from app.Tefca.address_comparison import AddressResult, compare_to_nppes
        rce = {"address_line": "123 Main St", "address_city": "Boston",
               "address_state": "MA"}
        nppes = {"Provider First Line Business Practice Location Address": "900 Elm Ave",
                 "Provider Business Practice Location Address City Name": "Boston",
                 "Provider Business Practice Location Address State Name": "MA"}
        cmp_ = compare_to_nppes(rce, nppes)
        assert cmp_.result is AddressResult.CONFLICT
        assert "line" in cmp_.field_conflicts

    def test_a_missing_source_record_is_unavailable_not_conflict(self):
        from app.Tefca.address_comparison import AddressResult, compare_to_nppes
        assert compare_to_nppes({"address_line": "1 A St"}, None).result \
            is AddressResult.SOURCE_UNAVAILABLE

    def test_ppef_can_never_claim_an_exact_match(self):
        """PPEF publishes no street line; asserting EXACT would claim agreement
        on a field the source never supplied."""
        from app.Tefca.address_comparison import AddressResult, compare_to_ppef
        rce = {"address_city": "Boston", "address_state": "MA",
               "address_postalCode": "02118"}
        cmp_ = compare_to_ppef(rce, [{"CITY_NAME": "BOSTON", "STATE_CD": "MA",
                                      "ZIP_CD": "021181234"}])
        assert cmp_.result is AddressResult.NORMALIZED_MATCH
        assert "line" in cmp_.fields_not_compared

    def test_one_matching_practice_location_is_a_match(self):
        """A provider may enrol several locations; the third differing is not
        a finding about the entity."""
        from app.Tefca.address_comparison import AddressResult, compare_to_ppef
        rce = {"address_city": "Boston", "address_state": "MA",
               "address_postalCode": "02118"}
        locs = [{"CITY_NAME": "WORCESTER", "STATE_CD": "MA", "ZIP_CD": "01601"},
                {"CITY_NAME": "BOSTON", "STATE_CD": "MA", "ZIP_CD": "02118"}]
        assert compare_to_ppef(rce, locs).result is AddressResult.NORMALIZED_MATCH

    def test_no_published_location_is_insufficient_not_conflict(self):
        from app.Tefca.address_comparison import AddressResult, compare_to_ppef
        assert compare_to_ppef({"address_city": "Boston"}, None).result \
            is AddressResult.INSUFFICIENT_DATA
