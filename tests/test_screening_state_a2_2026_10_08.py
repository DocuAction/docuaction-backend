"""Track A2: four screening states, registration vs exclusion, unavailable-source handling. DB-free, synthetic."""
from __future__ import annotations

import pytest

from app.tefca_registry.rce import screening_state as ss
from app.tefca_registry.rce.arc_pipeline import dimensions_to_verification_results
from app.tefca_registry.bucket_classifier import BucketClassifier


def _inp(**src):
    return {"sources": src}


def _s(status, disposition=None, **kw):
    return {"status": status, "disposition": disposition, **kw}


ALL_CLEAR = dict(oig_leie=_s("clear", "NOT_FOUND"), sam_gov=_s("clear", "NOT_FOUND"),
                 cms_revocation=_s("verified", "PASS"))


def test_four_distinct_state_names():
    assert len(set(ss.SCREENING_STATES)) == 4


def test_no_hit_only_when_every_control_answered_nothing():
    r = ss.derive_screening_state(_inp(**ALL_CLEAR))
    assert r["overall"] == ss.NO_HIT and r["no_hit_is_clearance"] is False
    assert r["incomplete_controls"] == [] and r["unavailable_source_handling"] is None


@pytest.mark.parametrize("status,disp,reason", [
    ("unavailable", "UNAVAILABLE", ss.REASON_UNAVAILABLE),
    ("failed", None, ss.REASON_FAILED),
    ("not_checked", "INSUFFICIENT_EVIDENCE", ss.REASON_INSUFFICIENT),
    ("not_checked", None, ss.REASON_NOT_EVALUATED),
])
def test_unavailable_failed_unchecked_are_incomplete_never_no_hit(status, disp, reason):
    src = dict(ALL_CLEAR, sam_gov=_s(status, disp))
    r = ss.derive_screening_state(_inp(**src))
    assert r["overall"] == ss.INCOMPLETE_SCREENING
    assert r["controls"]["sam_gov"] == {"state": ss.INCOMPLETE_SCREENING, "reason": reason}
    assert r["incomplete_controls"] == ["sam_gov"]
    h = r["unavailable_source_handling"]
    assert h["implemented_on_rce_path"] is False and h["re_review_within_business_days"] == 1
    assert h["escalate_to_cor_after_business_days"] == 3


def test_missing_control_is_incomplete_not_clear():
    r = ss.derive_screening_state(_inp(oig_leie=_s("clear", "NOT_FOUND")))
    assert r["overall"] == ss.INCOMPLETE_SCREENING
    assert set(r["incomplete_controls"]) == {"sam_gov", "cms_revocation"}


def test_empty_input_is_incomplete():
    assert ss.derive_screening_state(None)["overall"] == ss.INCOMPLETE_SCREENING


def test_not_applicable_is_not_a_gap():
    src = dict(ALL_CLEAR, cms_revocation=_s("not_checked", "NOT_APPLICABLE"))
    r = ss.derive_screening_state(_inp(**src))
    assert r["overall"] == ss.NO_HIT and "cms_revocation" not in r["controls"]


def test_potential_match_beats_incomplete_and_no_hit():
    src = dict(ALL_CLEAR, sam_gov=_s("not_found", "REVIEW"), cms_revocation=_s("unavailable", "UNAVAILABLE"))
    r = ss.derive_screening_state(_inp(**src))
    assert r["overall"] == ss.POTENTIAL_MATCH
    assert r["controls"]["cms_revocation"]["state"] == ss.INCOMPLETE_SCREENING   # still reported, not hidden


def test_potential_match_is_not_adjudicated_without_two_people():
    src = dict(ALL_CLEAR, oig_leie=_s("not_found", "REVIEW"))
    for adj in (None, {}, {"analyst_confirmed": True},
                {"analyst_confirmed": True, "qa_approved": True, "analyst_id": "a", "qa_id": "a"},
                {"analyst_confirmed": True, "qa_approved": False, "analyst_id": "a", "qa_id": "b"}):
        assert ss.derive_screening_state(_inp(**src), adjudication=adj)["overall"] == ss.POTENTIAL_MATCH


def test_adjudicated_confirmation_needs_analyst_and_different_qa():
    src = dict(ALL_CLEAR, oig_leie=_s("not_found", "REVIEW"))
    adj = {"analyst_confirmed": True, "qa_approved": True, "analyst_id": "a", "qa_id": "b"}
    assert ss.derive_screening_state(_inp(**src), adjudication=adj)["overall"] == ss.ADJUDICATED_CONFIRMATION


def test_registration_only_default_is_unchanged_but_flagged_is_incomplete():
    src = dict(ALL_CLEAR, sam_gov=_s("clear", "NOT_FOUND", screening_leg="registration_only"))
    assert ss.derive_screening_state(_inp(**src))["overall"] == ss.NO_HIT     # default OFF: legacy reading
    r = ss.derive_screening_state(_inp(**src), registration_only_is_incomplete=True)
    assert r["overall"] == ss.INCOMPLETE_SCREENING
    assert r["controls"]["sam_gov"]["reason"] == ss.REASON_REGISTRATION_ONLY
    assert ss.registration_only_gaps(_inp(**src)) == [{"control": "sam_gov", "gap": ss.REASON_REGISTRATION_ONLY}]


def test_exclusion_leg_is_not_downgraded_by_flag():
    src = dict(ALL_CLEAR, sam_gov=_s("clear", "NOT_FOUND", screening_leg="exclusion"))
    assert ss.derive_screening_state(_inp(**src), registration_only_is_incomplete=True)["overall"] == ss.NO_HIT


def test_translator_carries_leg_only_when_present_and_bucket_unchanged():
    def ev(nv):
        return {"dimensions": [{"dimension": "EXCLUSION_REVOCATION", "disposition": "PASS", "applicability": "APPLICABLE",
                                "evidence": [{"source": "SAM_GOV", "disposition": "NOT_FOUND", "normalized_values": nv}]}]}
    with_leg = dimensions_to_verification_results(ev({"screening_leg": "registration_only"}))
    without = dimensions_to_verification_results(ev({}))
    assert with_leg["sources"]["sam_gov"]["screening_leg"] == "registration_only"
    assert "screening_leg" not in without["sources"]["sam_gov"]
    assert with_leg["sources"]["sam_gov"]["status"] == without["sources"]["sam_gov"]["status"] == "clear"


def test_flags_default_off():
    from app.core.config import Settings
    f = Settings.model_fields if hasattr(Settings, "model_fields") else Settings.__fields__
    for name in ("ENABLE_SCREENING_STATE_RECORDING", "SAM_REGISTRATION_ONLY_IS_INCOMPLETE_SCREENING"):
        assert f[name].default is False


def _npi_less_registration_only_input():
    """Evidence -> translator output for the NPI-less path: SAM registration miss (NOT_FOUND on D3) labelled
    registration_only by #127, other controls answered."""
    ev = {"dimensions": [
        {"dimension": "IDENTITY", "disposition": "PASS", "applicability": "REQUIRED",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]},
        {"dimension": "EXCLUSION_REVOCATION", "disposition": "PASS", "applicability": "APPLICABLE", "evidence": [
            {"source": "OIG_LEIE", "disposition": "NOT_FOUND"},
            {"source": "SAM_GOV", "disposition": "NOT_FOUND", "normalized_values": {"screening_leg": "registration_only"}},
            {"source": "CMS_REVOCATION", "disposition": "PASS"}]}], "data_quality_flags": []}
    return dimensions_to_verification_results(ev)


def test_npi_less_registration_only_miss_old_behaviour_flags_off_vs_corrected_flags_on(monkeypatch):
    """Mirrors the arc_pipeline decision (DB-free; the inline pipeline block itself needs a DB):
    OFF = the pipeline records no screening_state and the claim has no extra gap (B1, verified, unchanged);
    ON  = INCOMPLETE_SCREENING is recorded; `verified` is withheld only if ENFORCE_COMPLETE_EXCLUSION_SCREENING is also on."""
    from app.core.config import settings
    from app.tefca_registry.rce import prior_risk as pr
    inp = _npi_less_registration_only_input()

    def claim(enforce):
        monkeypatch.setattr(settings, "ENFORCE_COMPLETE_EXCLUSION_SCREENING", enforce)
        gaps = pr.exclusion_screening_gaps(inp) + ss.registration_only_gaps(inp)
        return pr.verification_claim("B1", gaps, None)

    # -- flags OFF (defaults): the pipeline's recording branch is skipped, so nothing is derived/recorded; the only
    # gaps are the legacy ones (none here) -> no claim, entity stays `verified`. Derivation would read as NO_HIT.
    monkeypatch.setattr(settings, "ENABLE_SCREENING_STATE_RECORDING", False)
    monkeypatch.setattr(settings, "SAM_REGISTRATION_ONLY_IS_INCOMPLETE_SCREENING", False)
    monkeypatch.setattr(settings, "ENFORCE_COMPLETE_EXCLUSION_SCREENING", False)
    assert pr.exclusion_screening_gaps(inp) == []                           # legacy gap logic sees no gap
    assert ss.derive_screening_state(inp)["overall"] == ss.NO_HIT           # old reading: looks clean

    # -- recording + proposal ON, enforcement OFF: state recorded as INCOMPLETE; verified NOT withheld
    state = ss.derive_screening_state(inp, registration_only_is_incomplete=True)
    assert state["overall"] == ss.INCOMPLETE_SCREENING
    c = claim(enforce=False)
    assert c["exclusion_screening_incomplete"] and c["enforced"] is False
    assert c["official"]["entity_marked_verified"] is True                  # still verified
    assert c["proposed_inactive"]["entity_would_be_marked_verified"] is False

    # -- all three ON: verified withheld
    c = claim(enforce=True)
    assert c["enforced"] is True and c["official"]["entity_marked_verified"] is False
