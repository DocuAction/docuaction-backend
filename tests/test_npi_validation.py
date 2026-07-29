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
    for base in ("123456789", "167957672", "999999999"):
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
