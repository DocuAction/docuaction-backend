"""Regression: proposing a correction released the hold on a HIGH-severity record.

WHAT BROKE
curation.py states the contract twice — "HELD RECORDS DO NOT ENTER VERIFICATION"
and "A record stops being HELD when nothing at holding severity remains OPEN".
The second sentence was implemented literally: the hold keyed on
`resolution == "OPEN"`.

But the issue state machine is

    OPEN -> PROPOSED -> APPROVED | REJECTED | WAIVED -> RESOLVED

and PROPOSED means an analyst has SUGGESTED a disposition that nobody has yet
decided. Keying the hold on OPEN alone therefore released the record the moment
the analyst touched it.

Measured against the real August 21 ONC delivery in dev: all four HIGH-severity
records (three malformed NPIs and one field carrying two NPIs) were HELD, an
analyst moved their issues to PROPOSED, and `recompute_hold_status` then reported
`status_changed: 4, still_held: 0`. Four records with unresolved identity
questions became eligible for promotion, verification and Government reporting
because someone had proposed something about them.

A hold must survive until the question is DECIDED, not until it is discussed.
"""
from __future__ import annotations

import pytest

from app.tefca_registry.rce.curation import (HOLDING_SEVERITIES,
                                             UNDECIDED_RESOLUTIONS,
                                             blocks_promotion)


class TestUndecidedStates:
    def test_proposed_is_not_a_decision(self):
        assert "PROPOSED" in UNDECIDED_RESOLUTIONS

    def test_open_and_under_review_are_not_decisions(self):
        assert "OPEN" in UNDECIDED_RESOLUTIONS
        assert "UNDER_REVIEW" in UNDECIDED_RESOLUTIONS

    @pytest.mark.parametrize("decided", ["APPROVED", "REJECTED", "WAIVED", "RESOLVED"])
    def test_every_terminal_state_is_a_decision(self, decided):
        assert decided not in UNDECIDED_RESOLUTIONS


@pytest.mark.regression
class TestHoldSurvivesUntilDecided:
    @pytest.mark.parametrize("severity", sorted(HOLDING_SEVERITIES))
    @pytest.mark.parametrize("resolution", ["OPEN", "PROPOSED", "UNDER_REVIEW", None])
    def test_undecided_holding_severity_still_blocks(self, severity, resolution):
        """The defect: PROPOSED (and NULL) previously returned False here."""
        assert blocks_promotion(severity, resolution) is True

    @pytest.mark.parametrize("severity", sorted(HOLDING_SEVERITIES))
    @pytest.mark.parametrize("resolution", ["APPROVED", "REJECTED", "WAIVED", "RESOLVED"])
    def test_a_decided_issue_releases_the_hold(self, severity, resolution):
        assert blocks_promotion(severity, resolution) is False

    @pytest.mark.parametrize("severity", ["INFORMATIONAL", "LOW", "MEDIUM"])
    @pytest.mark.parametrize("resolution", ["OPEN", "PROPOSED", "APPROVED"])
    def test_non_holding_severities_never_block(self, severity, resolution):
        """MEDIUM must not start holding — 134 of the 138 human-required issues
        in the August delivery are MEDIUM, and holding them would take 134
        records out of the population without anyone deciding to."""
        assert blocks_promotion(severity, resolution) is False

    def test_a_null_resolution_is_treated_as_undecided_not_as_cleared(self):
        """A NULL resolution must fail closed. Treating absent as decided would
        release every record whose issue predates the resolution column."""
        assert blocks_promotion("HIGH", None) is True


@pytest.mark.regression
class TestBothCallSitesUseTheSharedPredicate:
    """curate_delivery and recompute_hold_status must not drift apart.

    They set the same flag from two different code paths — one at curation time
    over ORM objects, one afterwards over a query result. The original bug was
    only in the second, which is exactly what happens when the rule is written
    out twice.
    """

    def test_neither_call_site_compares_resolution_to_open_directly(self):
        import inspect

        from app.tefca_registry.rce import curation

        for fn in (curation.curate_delivery, curation.recompute_hold_status):
            source = inspect.getsource(fn)
            assert '== "OPEN"' not in source and "== 'OPEN'" not in source, (
                "%s still decides the hold by comparing resolution to OPEN; use "
                "blocks_promotion()/UNDECIDED_RESOLUTIONS so a PROPOSED issue "
                "keeps holding" % fn.__name__)
