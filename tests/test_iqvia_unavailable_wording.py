"""The reasons the IQVIA match route gives for unsupported sources must describe the CURRENT state:
HCP_AFFIL data is delivered and can be staged, but no matching step consumes it. Capability and
codes are unchanged; only the explanation is corrected. No database needed."""
from __future__ import annotations

import pytest

from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import snapshot_models as sm

pytestmark = pytest.mark.regression

STALE = ("never been approved", "as of this pass", "dedicated AFFIL extract absent", "not yet meaningful",
         "has not been delivered")


@pytest.mark.parametrize("source", [sm.SOURCE_IQVIA_HCP, sm.SOURCE_IQVIA_AFFILIATION])
def test_unsupported_sources_keep_code_and_drop_stale_claims(source):
    cap = routes._matching_capability(source)
    assert cap["supported"] is False and cap["code"] == "AFFILIATION_DATA_UNAVAILABLE"
    for phrase in STALE:
        assert phrase not in cap["reason"]
    assert "not implemented" in cap["reason"]


def test_affiliation_reason_says_staging_is_not_coverage_and_nothing_is_verified():
    reason = routes._matching_capability(sm.SOURCE_IQVIA_AFFILIATION)["reason"]
    assert "Staging is not verified source coverage" in reason
    assert "no entity is verified" in reason


def test_hco_capability_and_other_sources_are_unchanged():
    assert routes._matching_capability(sm.SOURCE_IQVIA_HCO) == {"supported": True, "reason": None, "code": None}
    other = routes._matching_capability("ONC_RCE")
    assert other["code"] == "NOT_MATCHABLE" and other["supported"] is False
