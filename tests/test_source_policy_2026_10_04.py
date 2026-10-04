"""app.Tefca.source_policy -- Round 23, Part B §2.

No live source call, no database, no network -- this module is pure
policy bookkeeping, so these tests run anywhere, always.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.Tefca import source_policy as sp


@pytest.mark.parametrize("source_id", ["NPPES_BULK", "PECOS_PROXY", "USPS", "IQVIA"])
def test_every_official_policy_is_unapproved_today(source_id):
    view = sp.official_view(source_id)
    assert view["approval_status"] == sp.POLICY_UNAPPROVED
    assert view["freshness"] == sp.FRESHNESS_UNKNOWN


def test_a_very_recent_as_of_date_does_not_make_an_unapproved_source_current():
    """The central rule: source cadence alone is never an approved
    freshness deadline. Even an as_of from today must not read CURRENT
    for a POLICY_UNAPPROVED source."""
    today = datetime.now(timezone.utc).isoformat()
    view = sp.official_view("NPPES_BULK", as_of=today)
    assert view["approval_status"] == sp.POLICY_UNAPPROVED
    assert view["freshness"] == sp.FRESHNESS_UNKNOWN


@pytest.mark.parametrize("source_id", ["NPPES_BULK", "PECOS_PROXY", "USPS", "IQVIA"])
def test_every_proposed_policy_is_labeled_inactive(source_id):
    view = sp.proposed_view(source_id)
    assert view["approval_status"] == sp.PROPOSED_INACTIVE
    assert view["policy"].approval_status == sp.PROPOSED_INACTIVE


def test_proposed_and_official_use_identical_pinned_evidence():
    as_of, retrieved, verified = "2026-10-01T00:00:00+00:00", "2026-10-04T00:00:00+00:00", "2026-10-04T01:00:00+00:00"
    both = sp.both_views("NPPES_BULK", as_of=as_of, retrieved_at=retrieved, verified_at=verified)
    assert both["official"]["evidence"] == both["proposed"]["evidence"]
    assert both["official"]["evidence"] == {"as_of": as_of, "retrieved_at": retrieved, "verified_at": verified}


def test_nppes_bulk_proposed_mapping_is_pinned_to_v2():
    policy = sp.PROPOSED_POLICIES["NPPES_BULK"]
    assert policy.mapping_version == "V2"


def test_pecos_proxy_proposed_policy_names_it_as_a_proxy_not_pecos():
    from app.Tefca.connectors import PECOS_BACKING

    policy = sp.PROPOSED_POLICIES["PECOS_PROXY"]
    assert policy.mapping_version == "PROXY_NOT_PECOS"
    assert PECOS_BACKING in policy.identity_method or "nppes_proxy" in policy.identity_method
    assert policy.freshness_window_days is None  # inherits NPPES's state, not its own cadence


def test_usps_proposed_policy_records_permitted_scope_and_disclaims_identity_and_occupancy():
    policy = sp.PROPOSED_POLICIES["USPS"]
    assert policy.extra["proves_identity"] is False
    assert policy.extra["proves_occupancy"] is False
    assert "identity" in policy.identity_method.lower()
    assert "occupancy" in policy.identity_method.lower()


def test_iqvia_proposed_policy_names_the_affiliation_journey_as_the_only_unsupported_part():
    policy = sp.PROPOSED_POLICIES["IQVIA"]
    assert "IQVIA_AFFILIATION_JOURNEY_UNSUPPORTED" in policy.reason_codes
    # The delivered facts themselves are NOT described as unsupported.
    assert "delivered-facts-only" == policy.mapping_version


def test_a_stale_as_of_is_reported_stale_only_in_the_proposed_shadow_view():
    """Freshness computed from the PROPOSED window is for review visibility
    only -- it never appears as the OFFICIAL answer, which stays UNKNOWN
    regardless (see test_a_very_recent_as_of_date... above)."""
    stale_as_of = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    official = sp.official_view("NPPES_BULK", as_of=stale_as_of)
    proposed = sp.proposed_view("NPPES_BULK", as_of=stale_as_of)
    assert official["freshness"] == sp.FRESHNESS_UNKNOWN
    assert proposed["freshness"] == sp.FRESHNESS_STALE  # 90 days > the proposed 35-day window
    assert proposed["approval_status"] == sp.PROPOSED_INACTIVE  # still never installed


def test_approved_policy_requires_approval_provenance():
    with pytest.raises(ValueError, match="approval provenance"):
        sp.SourcePolicy(
            source_id="X", schema_version="1", mapping_version="1", identity_method="x",
            dependencies=[], applicability_authority="x", publication_cadence="x",
            freshness_window_days=30, blocking_scope="x", reason_codes=[],
            effective_date="2026-10-04", approval_status=sp.POLICY_APPROVED)


def test_unknown_source_id_raises_rather_than_silently_returning_an_empty_policy():
    with pytest.raises(KeyError):
        sp.official_view("NOT_A_REAL_SOURCE")
    with pytest.raises(KeyError):
        sp.proposed_view("NOT_A_REAL_SOURCE")
