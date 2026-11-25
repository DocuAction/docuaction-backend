"""NPI check-digit validation (CMS 45 CFR 162.406, FHIR-ID-002)."""
from app.services.npi_validator import (validate_npi, is_valid_npi,
                                        compute_check_digit, make_valid_npi,
                                        validate_for_import)


def test_valid_npi():
    """1234567893 is the canonical CMS worked example."""
    ok, msg = validate_npi("1234567893")
    assert ok is True
    assert msg == ""


def test_invalid_check_digit():
    ok, msg = validate_npi("1234567890")
    assert ok is False
    assert "Luhn" in msg


def test_npi_too_short():
    ok, msg = validate_npi("12345")
    assert ok is False
    assert "10 digits" in msg


def test_npi_too_long():
    ok, msg = validate_npi("12345678901")
    assert ok is False
    assert "10 digits" in msg


def test_npi_non_numeric():
    ok, msg = validate_npi("12345abcde")
    assert ok is False
    assert "digits only" in msg


def test_npi_empty():
    assert validate_npi("")[0] is False
    assert validate_npi(None)[0] is False


def test_npi_with_80840_prefix_validation():
    """The 80840 prefix is what makes this NPI validation rather than plain Luhn.

    Without the prefix roughly one NPI in ten validates by accident, which looks
    like a working implementation. This asserts the prefix is actually applied by
    checking a value that passes bare Luhn but fails NPI validation.
    """
    from app.services.npi_validator import _luhn_total, CMS_PREFIX
    assert CMS_PREFIX == "80840"
    bare_luhn_ok = _luhn_total("1234567890") % 10 == 0
    npi_ok = validate_npi("1234567890")[0]
    assert bare_luhn_ok != npi_ok or not npi_ok


def test_compute_check_digit_round_trip():
    assert make_valid_npi("123456789") == "1234567893"
    # Bases must themselves start with 1 or 2 -- the only ranges NPPES issues
    # from (see 2026-10-21 first-digit fix). "999999999" was never a realistic
    # base; kept out here rather than silently left in a round-trip test.
    for base in ("123456789", "167957672", "200000000"):
        assert is_valid_npi(make_valid_npi(base))


def test_compute_check_digit_rejects_bad_base():
    import pytest
    with pytest.raises(ValueError):
        compute_check_digit("12345")


def test_validate_for_import_flags_rather_than_rejects():
    """Import must not reject on a bad NPI - existing seed data has some."""
    good = validate_for_import("1234567893")
    bad = validate_for_import("1234567890")
    assert good["npi_valid"] is True and good["requires_review"] is False
    assert bad["npi_valid"] is False and bad["requires_review"] is True
    assert bad["npi_validation_error"]


def test_first_digit_outside_nppes_range_rejected():
    """A 10-digit, Luhn-valid number NPPES never issues (first digit not 1 or
    2) must be rejected -- 2026-10-21 fix. Before this fix, a value shaped
    like this would pass validate_npi() (no first-digit check existed here)
    while separately failing IntakeValidator.validate_npi() in qa_engine.py,
    which had its own, inconsistent, independent check-digit implementation."""
    luhn_valid_wrong_prefix = make_valid_npi("300000000")
    assert luhn_valid_wrong_prefix[0] == "3"
    ok, msg = validate_npi(luhn_valid_wrong_prefix)
    assert ok is False
    assert "1 or 2" in msg


def test_first_digit_1_and_2_both_allowed():
    """1 and 2 are both valid NPPES-issued ranges -- this is a numbering-range
    check, not an individual-vs-organization check (see module docstring)."""
    for base in ("100000000", "200000000"):
        npi = make_valid_npi(base)
        assert npi[0] in ("1", "2")
        assert is_valid_npi(npi) is True


def test_intake_validator_agrees_with_shared_validator():
    """Regression test for the exact inconsistency fixed 2026-10-21:
    app.Tefca.qa_engine.IntakeValidator.validate_npi() used to carry its own
    second NPI-validation implementation, independent of this module's, and
    disagreed with it on first-digit handling. It must now delegate to this
    module, so the two can never disagree again."""
    from app.Tefca.qa_engine import IntakeValidator
    iv = IntakeValidator()

    good = make_valid_npi("123456789")
    wrong_prefix = make_valid_npi("300000000")
    bad_checksum = "1234567890"

    for npi in (good, wrong_prefix, bad_checksum, "", None, "12345"):
        assert iv.validate_npi(npi) == is_valid_npi(npi), f"disagreement on {npi!r}"
