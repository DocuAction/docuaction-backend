"""USPS connector tests (4).

The connector is optional by design: the pipeline must behave identically whether
or not a key is configured, and a quota problem must degrade rather than fail.
"""
from contextlib import contextmanager

import pytest

from app.tefca_registry import usps_connector as uc
from app.tefca_registry.usps_connector import USPSConnector

VALID_XML = """<?xml version="1.0"?>
<AddressValidateResponse><Address ID="0">
  <Address2>123 MAIN ST STE 400</Address2>
  <City>SPRINGFIELD</City><State>IL</State>
  <Zip5>62704</Zip5><Zip4>1234</Zip4>
</Address></AddressValidateResponse>"""

ERROR_XML = """<?xml version="1.0"?>
<AddressValidateResponse><Address ID="0">
  <Error><Number>-2147219401</Number><Description>Address Not Found.</Description></Error>
</Address></AddressValidateResponse>"""


def _opener(body, calls=None):
    @contextmanager
    def open_url(url, timeout=None):
        if calls is not None:
            calls.append(url)

        class R:
            def read(self):
                return body.encode()
        yield R()
    return open_url


@pytest.fixture(autouse=True)
def _clean():
    uc._reset_for_tests()
    yield
    uc._reset_for_tests()


def test_falls_back_to_code_normalization_without_a_key():
    """No key must be a normal operating mode, not an error."""
    c = USPSConnector(user_id="")
    assert c.enabled is False

    r = c.verify("123 North Main Street, Suite 400")
    assert r.verified is False
    assert r.source == "fallback"
    assert "not configured" in r.reason
    # The fallback still does real work — it returns the USPS-standardized form.
    assert r.standardized == "123 N MAIN ST STE 400"


def test_successful_verification_parses_usps_response():
    calls = []
    c = USPSConnector(user_id="TEST123")
    r = c.verify("123 Main St Suite 400", city="Springfield", state="IL",
                 _opener=_opener(VALID_XML, calls))

    assert r.verified is True
    assert r.source == "usps"
    assert r.zip5 == "62704"
    assert r.zip4 == "1234"
    assert "123 MAIN ST STE 400" in r.standardized
    assert len(calls) == 1


def test_usps_error_and_bad_xml_degrade_to_fallback():
    """Every failure path must return a result, never raise."""
    c = USPSConnector(user_id="TEST123")

    r = c.verify("999 Nowhere Rd", _opener=_opener(ERROR_XML))
    assert r.verified is False
    assert r.source == "fallback"
    assert "Address Not Found" in r.reason

    uc._reset_for_tests()
    r2 = c.verify("123 Main St", _opener=_opener("<not-xml"))
    assert r2.verified is False
    assert r2.source == "fallback"

    uc._reset_for_tests()
    def boom(url, timeout=None):
        raise OSError("connection reset")
    r3 = c.verify("123 Main St", _opener=boom)
    assert r3.verified is False
    assert "request failed" in r3.reason


def test_cache_and_daily_budget_bound_api_usage(monkeypatch):
    """Perigon burned a free tier by 06:00 with no cache and no budget. Not again."""
    calls = []
    c = USPSConnector(user_id="TEST123")

    c.verify("123 Main St", _opener=_opener(VALID_XML, calls))
    second = c.verify("123 Main St", _opener=_opener(VALID_XML, calls))
    assert len(calls) == 1, "a repeated lookup must be served from cache"
    assert second.source == "cache"

    uc._reset_for_tests()
    monkeypatch.setattr(uc, "DAILY_BUDGET", 1)
    calls.clear()
    c.verify("1 First St", _opener=_opener(VALID_XML, calls))
    over = c.verify("2 Second St", _opener=_opener(VALID_XML, calls))
    assert len(calls) == 1, "budget must hard-stop billable calls"
    assert over.source == "fallback"
    assert "budget" in over.reason
    assert uc.budget_status()["remaining"] == 0
