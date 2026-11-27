"""Classification uses the PINNED SAM extract edition's actual evidence (owner, 2026-11-26).

The bulk writer screens the extract live, against whatever edition is in memory; the extract reloads every 24 h.
With `RCE_SAM_EXTRACT_PINNED_SHA256` set, a writer call whose gathered evidence is not from that edition is refused
before its first write. Unset, nothing changes. Pure functions only (no database).
"""
from __future__ import annotations

import pytest

from app.tefca_registry.rce import arc_pipeline as ap

PIN = "b" * 64


def _member(ref, *items):
    return {"ref": ref, "entity": {}, "evidence": {"dimensions": [
        {"dimension": "EXCLUSION_REVOCATION", "evidence": list(items)}]}}


def _screen(disposition, sha=PIN):
    return {"source": ap.EXTRACT_EVIDENCE_SOURCE, "disposition": disposition,
            "original_values": {"extract_sha256": sha, "publication_date": "2026-11-26"}}


def _unavailable(note):
    return {"source": ap.EXTRACT_EVIDENCE_SOURCE, "disposition": "UNAVAILABLE", "original_values": {}, "note": note}


def test_no_pin_means_no_check(monkeypatch):
    monkeypatch.delenv(ap.SAM_EXTRACT_PINNED_EDITION_ENV, raising=False)
    assert ap.pinned_extract_edition() is None


@pytest.mark.parametrize("bad", ["b" * 63, "g" * 64, "not-a-hash"])
def test_a_malformed_pin_refuses_rather_than_being_ignored(monkeypatch, bad):
    monkeypatch.setenv(ap.SAM_EXTRACT_PINNED_EDITION_ENV, bad)
    with pytest.raises(ap.SamExtractEditionMismatch, match="64-hex"):
        ap.pinned_extract_edition()


def test_pin_is_normalised(monkeypatch):
    monkeypatch.setenv(ap.SAM_EXTRACT_PINNED_EDITION_ENV, "  " + "B" * 64 + " ")
    assert ap.pinned_extract_edition() == PIN


def test_pinned_edition_pass_and_candidate_are_accepted():
    assert ap.extract_edition_problems([_member("r1", _screen("PASS")), _member("r2", _screen("REVIEW"))], PIN) == []


def test_another_edition_is_refused_even_when_clear():
    problems = ap.extract_edition_problems([_member("r1", _screen("PASS", sha="a" * 64))], PIN)
    assert len(problems) == 1 and "r1" in problems[0] and "aaaaaaaaaaaa" in problems[0]


@pytest.mark.parametrize("note", ["stale_source: 2.1 day(s) old", "extract unavailable or refused",
                                  "ENABLE_SAM_EXTRACT_SCREENING not enabled", None])
def test_a_screen_that_did_not_happen_is_refused(note):
    assert len(ap.extract_edition_problems([_member("r1", _unavailable(note))], PIN)) == 1


def test_entity_specific_not_screenable_is_accepted_in_the_stored_form():
    """The note is `sam_extract.error` copied verbatim, i.e. SourceResult.unavailable's prefixed form (CodeRabbit)."""
    from app.Tefca.connectors import SourceResult
    error = SourceResult.unavailable(
        "SAM_EXTRACT", "no_usable_identifier: neither a valid NPI nor an organisation name is available", {}).error
    assert error.startswith("SAM_EXTRACT unavailable: no_usable_identifier:")
    assert ap.extract_edition_problems([_member("r1", _unavailable(error))], PIN) == []


@pytest.mark.parametrize("reason", ["extract unavailable or refused", "stale_source: 2.1 day(s) old",
                                    "ENABLE_SAM_EXTRACT_SCREENING not enabled"])
def test_source_failures_in_the_stored_form_are_refused(reason):
    from app.Tefca.connectors import SourceResult
    error = SourceResult.unavailable("SAM_EXTRACT", reason, {}).error
    assert len(ap.extract_edition_problems([_member("r1", _unavailable(error))], PIN)) == 1


def test_the_shared_matcher_is_exact():
    from app.Tefca.sam_extract import is_not_screenable_error
    assert is_not_screenable_error("SAM_EXTRACT unavailable: no_usable_identifier: x")
    assert is_not_screenable_error("no_usable_identifier: x")
    assert not is_not_screenable_error("SAM_EXTRACT unavailable: stale_source: no_usable_identifier: x")
    assert not is_not_screenable_error("no_usable_identifiers are fine")
    assert not is_not_screenable_error(None)


def test_missing_extract_evidence_is_refused():
    assert len(ap.extract_edition_problems([_member("r1")], PIN)) == 1


def test_members_without_evidence_are_not_classified_and_are_skipped():
    assert ap.extract_edition_problems([{"ref": "r1", "entity": {}, "evidence": None}], PIN) == []


def test_one_bad_member_is_enough_to_refuse_the_call():
    gathered = [_member("ok", _screen("PASS")), _member("bad", _screen("PASS", sha="c" * 64))]
    problems = ap.extract_edition_problems(gathered, PIN)
    assert [p.split(":")[0] for p in problems] == ["bad"]


def test_the_summary_route_reports_the_loaded_edition_without_loading_it(monkeypatch):
    """The operator needs the FULL sha of the edition in memory to pin it; the route reports it read-only."""
    import inspect

    from app.Tefca import sam_extract
    from app.tefca_registry.rce import delivery_routes
    src = inspect.getsource(delivery_routes.sam_extract_summary_route)
    assert 'result["loaded_edition"] = _extract.loaded_edition()' in src
    assert "_load_sam_extract" not in src and "screening_not_ready_reason" not in src
    monkeypatch.setitem(sam_extract._SAM_CACHE, "sha256", "d" * 64)
    assert sam_extract.loaded_edition()["extract_sha256"] == "d" * 64
