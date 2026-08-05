"""USPS Publication 28 address normalization tests (10).

The value of this module is that it stops reviewers chasing formatting-only
differences — so the tests are mostly "these two renderings are the same address"
paired with "these two are genuinely different, don't merge them."
"""
from app.tefca_registry.address_normalizer import USPSNormalizer


def N():
    return USPSNormalizer()


def test_street_suffix_abbreviation():
    n = N()
    assert n.normalize("123 Main Street") == "123 MAIN ST"
    assert n.normalize("45 Oak Avenue") == "45 OAK AVE"
    assert n.normalize("9 Elm Boulevard") == "9 ELM BLVD"
    assert n.normalize("7 Pine Parkway") == "7 PINE PKWY"


def test_directional_abbreviation():
    n = N()
    assert n.normalize("123 North Main Street") == "123 N MAIN ST"
    assert n.normalize("50 Southwest Third Ave") == "50 SW THIRD AVE"


def test_secondary_unit_designators():
    n = N()
    assert n.normalize("123 Main St Suite 400") == "123 MAIN ST STE 400"
    assert n.normalize("123 Main St Apartment 2B") == "123 MAIN ST APT 2B"
    assert n.normalize("1 Tower Rd Floor 12") == "1 TOWER RD FL 12"


def test_state_name_to_abbreviation():
    n = N()
    # Multi-word states must collapse before tokenizing, or "new york" becomes
    # two tokens and never matches.
    assert "NY" in n.normalize("1 Wall St, New York, New York 10005")
    assert "CA" in n.normalize("1 Market St, San Francisco, California")
    assert "DC" in n.normalize("1600 Pennsylvania Ave, District of Columbia")


def test_punctuation_and_case_removed():
    n = N()
    assert n.normalize("123 Main St., Suite #400") == "123 MAIN ST STE 400"
    assert n.normalize("  123   MAIN   street  ") == "123 MAIN ST"


def test_zip_extraction_and_plus_four():
    n = N()
    assert n.extract_zip("123 Main St, Springfield IL 62704") == "62704"
    assert n.extract_zip("123 Main St, Springfield IL 62704-1234") == "62704"
    assert n.extract_zip("no zip here") == ""
    assert "62704-1234" in n.normalize("123 Main St IL 62704-1234")


def test_state_extraction():
    n = N()
    assert n.extract_state("1 Wall St, New York, NY 10005") == "NY"
    assert n.extract_state("1 Market St, San Francisco, California 94105") == "CA"
    assert n.extract_state("somewhere unknown") == ""


def test_compare_formatting_only_difference_is_a_match():
    """The case the module exists for."""
    n = N()
    r = n.compare("123 North Main Street, Suite 400, Springfield, IL 62704",
                  "123 N MAIN ST STE 400, SPRINGFIELD, IL 62704")
    assert r.is_match is True
    assert r.confidence == 1.0
    assert r.normalized_a == r.normalized_b


def test_compare_differing_zip_never_matches():
    """Two suites in one building share nearly every token — ZIP and state must
    be disqualifying regardless of overlap, or distinct sites get merged."""
    n = N()
    r = n.compare("123 Main St Suite 400, Springfield, IL 62704",
                  "123 Main St Suite 400, Springfield, IL 62999")
    assert r.is_match is False
    assert r.confidence == 0.0
    assert any("ZIP" in d for d in r.differences)

    r2 = n.compare("123 Main St, Springfield, IL", "123 Main St, Springfield, CA")
    assert r2.is_match is False
    assert any("state" in d for d in r2.differences)


def test_compare_handles_empty_and_none():
    n = N()
    assert n.normalize(None) == ""
    assert n.normalize("") == ""
    for a, b in (("123 Main St", ""), ("", "123 Main St"), (None, None)):
        r = n.compare(a, b)
        assert r.is_match is False
        assert r.confidence == 0.0
