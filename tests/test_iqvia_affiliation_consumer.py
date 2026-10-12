"""IQVIA HCP_AFFIL consumer (default off, advisory). Entirely synthetic data:
the NPIs are generated here with the public Luhn rule and name nothing real.
No database is needed; the DB layer is exercised through a minimal async stub.
"""
from __future__ import annotations

import asyncio
import types
import uuid

import pytest

from app.tefca_registry.rce import iqvia_affiliation_consumer as ac
from app.tefca_registry.rce import snapshot_models as sm

pytestmark = pytest.mark.regression


def make_npi(prefix9: str) -> str:
    def total(digits: str) -> int:
        t = 0
        for i, ch in enumerate(reversed(digits)):
            n = int(ch)
            if i % 2 == 1:
                n = n * 2 - 9 if n * 2 > 9 else n * 2
            t += n
        return t
    for check in range(10):
        if total("80840" + prefix9 + str(check)) % 10 == 0:
            return prefix9 + str(check)
    raise AssertionError


NPI_A, NPI_B = make_npi("123456789"), make_npi("987654321")
E1, E2, E3 = "ent-1", "ent-2", "ent-3"


# -- normalization ------------------------------------------------------------
def test_npi_normalization_accepts_spreadsheet_suffix_and_whitespace():
    assert ac.normalize_npi(f"  {NPI_A}.0 ")["value"] == NPI_A


@pytest.mark.parametrize("bad", ["1234567890", "12345", "abcdefghij", "123456789O"])
def test_npi_failures_are_codes_not_values(bad):
    r = ac.normalize_npi(bad)
    assert r["valid"] is False and r["reason"] == "NPI_FAILED_VALIDATION" and r["value"] is None


def test_npi_missing_is_not_invalid():
    r = ac.normalize_npi("  ")
    assert r["present"] is False and r["valid"] is False and r["reason"] is None


def test_ccn_normalization_pads_exactly_one_lost_zero_and_never_more():
    assert ac.normalize_ccn("10001")["value"] == "010001" and ac.normalize_ccn("10001")["padded"] is True
    assert ac.normalize_ccn(" 01-0001 ")["value"] == "010001"
    assert ac.normalize_ccn("01t001")["value"] == "01T001"
    assert ac.normalize_ccn("1234")["valid"] is False          # not padded: would invent zeros
    assert ac.normalize_ccn("0100011")["reason"] == "CCN_BAD_SHAPE"


def test_ccn_lookup_variants_cover_the_dropped_zero():
    assert ac.ccn_lookup_variants("010001") == ["010001", "10001"]
    assert ac.ccn_lookup_variants("01T001") == ["01T001"]


# -- organisation resolution: positive, absent, ambiguous, conflicting --------
def test_positive_single_npi_hit_is_a_candidate_never_confirmed():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn=None, npi_entities=[E1])
    assert (r.outcome, r.entity_ids, r.basis) == (ac.ORG_CANDIDATE, [E1], "npi")
    assert r.determination == "AUTOMATED_CANDIDATE_NOT_CONFIRMED"
    assert r.requires_analyst_review is True and r.affects_verification is False
    assert "NPPES_ENTITY_TYPE_NOT_EVIDENCED" in r.notes


def test_npi_and_ccn_agreeing_on_one_entity():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn="010001", npi_entities=[E1], ccn_entities=[E1])
    assert (r.outcome, r.basis, r.entity_ids) == (ac.ORG_CANDIDATE, "npi+ccn", [E1])


def test_one_identifier_hits_other_absent_is_candidate_with_note_not_conflict():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn="010001", npi_entities=[E1], ccn_entities=[])
    assert r.outcome == ac.ORG_CANDIDATE and "OTHER_IDENTIFIER_NOT_IN_REGISTRY" in r.notes


def test_absent_valid_identifier_is_not_in_registry_not_invalid_not_clear():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn=None)
    assert r.outcome == ac.ORG_NOT_IN_REGISTRY and r.entity_ids == []
    assert r.outcome not in (ac.ORG_INVALID_IDENTIFIER, "VERIFIED_CLEAR")


def test_no_identifier_and_invalid_identifier_are_distinct():
    assert ac.resolve_organisation_from_sets(org_npi="", org_ccn=None).outcome == ac.ORG_NO_IDENTIFIER
    bad = ac.resolve_organisation_from_sets(org_npi="1234567890", org_ccn="12")
    assert bad.outcome == ac.ORG_INVALID_IDENTIFIER and "NPI_FAILED_VALIDATION" in bad.notes


def test_ambiguous_two_entities_share_the_npi():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn=None, npi_entities=[E1, E2])
    assert r.outcome == ac.ORG_AMBIGUOUS and r.entity_ids == [E1, E2]


def test_ambiguous_overlapping_but_unequal_sets():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn="010001", npi_entities=[E1], ccn_entities=[E1, E2])
    assert r.outcome == ac.ORG_AMBIGUOUS


def test_conflict_npi_and_ccn_point_to_different_entities():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn="010001", npi_entities=[E1], ccn_entities=[E2])
    assert r.outcome == ac.ORG_CONFLICT and r.entity_ids == [E1, E2]
    assert "NPI_AND_CCN_POINT_TO_DIFFERENT_ENTITIES" in r.notes
    assert r.requires_analyst_review is True


def test_valid_ccn_with_invalid_npi_uses_ccn_and_flags_the_bad_npi():
    r = ac.resolve_organisation_from_sets(org_npi="1234567890", org_ccn="010001", ccn_entities=[E3])
    assert r.outcome == ac.ORG_CANDIDATE and r.basis == "ccn" and "NPI_FAILED_VALIDATION" in r.notes


def test_no_outcome_is_ever_auto_approved_or_unreviewed():
    cases = [dict(npi_entities=[E1]), dict(ccn_entities=[E1]), dict(npi_entities=[E1, E2]),
             dict(npi_entities=[E1], ccn_entities=[E2]), dict()]
    for kw in cases:
        r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn="010001", **kw)
        assert r.requires_analyst_review is True and r.affects_verification is False
        assert r.determination == "AUTOMATED_CANDIDATE_NOT_CONFIRMED"


def test_output_never_contains_the_raw_identifiers():
    r = ac.resolve_organisation_from_sets(org_npi=NPI_A, org_ccn="010001", npi_entities=[E1])
    blob = repr(r.as_dict())
    assert NPI_A not in blob and "010001" not in blob


# -- relationship versus identity ---------------------------------------------
def row(hcp, hco, typ, npi=None):
    return {"hcp_record_key": hcp, "hco_record_key": hco, "affiliation_type": typ,
            "payload": {"NPI": npi} if npi else {}}


def test_relationship_kinds_are_kept_apart():
    assert ac.classify_relationship("A1") == {"kind": "PROVIDER", "type_id": "A1"}
    assert ac.classify_relationship("CONTACT:T9") == {"kind": "CONTACT", "type_id": "T9"}
    assert ac.classify_relationship("UNTYPED")["kind"] == "UNTYPED"
    assert ac.classify_relationship(None)["kind"] == "UNTYPED"


def test_multiple_rows_one_pair_are_relationships_not_identities():
    s = ac.summarise_relationships([row("H1", "O1", "A1", NPI_A), row("H1", "O1", "CONTACT:T9", NPI_A),
                                    row("H1", "O2", "A1", NPI_A)])
    assert s["relationship_rows"] == 3 and s["distinct_hcp_keys"] == 1 and s["distinct_hco_keys"] == 2
    assert s["pairs_with_multiple_types"] == 1 and s["hcp_keys_with_conflicting_npi"] == []
    assert s["by_kind"] == {"PROVIDER": 2, "CONTACT": 1, "UNTYPED": 0}
    assert s["identity_inference"].startswith("none")


def test_one_hcp_key_with_two_valid_npis_is_flagged_not_merged():
    s = ac.summarise_relationships([row("H1", "O1", "A1", NPI_A), row("H1", "O2", "A1", NPI_B)])
    assert s["hcp_keys_with_conflicting_npi"] == ["H1***(len2)"] and s["distinct_hcp_keys"] == 1


# -- honest coverage reporting -------------------------------------------------
def snap(status, system=sm.SOURCE_IQVIA_AFFILIATION, n=7551269):
    return {"id": uuid.uuid4(), "source_system": system, "status": status, "record_count": n}


def test_coverage_states_never_claim_verification():
    assert ac.coverage_statement(flag_enabled=False, snapshots=[])["state"] == "not_used"
    staged = ac.coverage_statement(flag_enabled=True, snapshots=[snap("PENDING")])
    assert staged["state"] == "staged_not_used" and "not used to verify" in staged["statement"]
    appr = ac.coverage_statement(flag_enabled=True, snapshots=[snap("APPROVED")])
    assert appr["state"] == "approved_not_consumed"
    used = ac.coverage_statement(flag_enabled=True, snapshots=[snap("APPROVED")], consumed=True)
    assert used["state"] == "consumed_candidates_only"
    for s in (staged, appr, used):
        assert s["verification_source"] is False
        text = s["statement"]
        for negated in ("not used to verify", "not a verification source", "No entity was verified",
                        "do not verify any entity"):
            text = text.replace(negated, "")
        assert "verif" not in text   # every mention of verification is a negation


def test_hco_snapshots_do_not_count_as_affiliation_coverage():
    s = ac.coverage_statement(flag_enabled=True, snapshots=[snap("APPROVED", system=sm.SOURCE_IQVIA_HCO, n=0)])
    assert s["state"] == "not_used" and s["snapshots"] == []


# -- eligibility of the snapshot (DB layer through a stub) --------------------
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeDb:
    def __init__(self, snapshots, observations=()):
        self.snapshots, self.observations = {s.id: s for s in snapshots}, list(observations)

    async def get(self, _model, key):
        return self.snapshots.get(key)

    async def execute(self, _stmt):
        return _Result(self.observations)


def fake_snapshot(status, source=sm.SOURCE_IQVIA_AFFILIATION, supersedes=None):
    return types.SimpleNamespace(id=uuid.uuid4(), source_system=source, status=status, record_count=5,
                                 sha256="ab" * 32, supersedes_snapshot_id=supersedes)


def fake_obs(hcp, hco, typ, npi=None):
    return types.SimpleNamespace(hcp_record_key=hcp, hco_record_key=hco, affiliation_type=typ,
                                 payload={"NPI": npi} if npi else {})


def run(coro):
    return asyncio.run(coro)


def test_pending_snapshot_is_refused():
    s = fake_snapshot("PENDING")
    with pytest.raises(ac.SnapshotNotEligible):
        run(ac.relationships_for_hcp(FakeDb([s]), snapshot_id=s.id, hcp_record_key="H1"))


def test_wrong_source_snapshot_is_refused_even_if_approved():
    s = fake_snapshot("APPROVED", source=sm.SOURCE_IQVIA_HCO)
    with pytest.raises(ac.SnapshotNotEligible):
        run(ac.relationships_for_hcp(FakeDb([s]), snapshot_id=s.id, hcp_record_key="H1"))


def test_approved_snapshot_reads_and_reports_provenance_and_nothing_confirmed():
    s = fake_snapshot("APPROVED")
    out = run(ac.relationships_for_hcp(FakeDb([s], [fake_obs("H1", "O1", "A1", NPI_A)]),
                                       snapshot_id=s.id, hcp_record_key="H1"))
    assert out["found_in_extract"] is True and out["relationship_rows"] == 1
    assert out["provenance"]["snapshot_id"] == str(s.id) and out["provenance"]["status"] == "APPROVED"
    assert out["requires_analyst_review"] is True and out["affects_verification"] is False
    assert "diagnostic_only" not in out


def test_absent_key_is_not_found_in_this_extract():
    s = fake_snapshot("APPROVED")
    out = run(ac.relationships_for_hcp(FakeDb([s], []), snapshot_id=s.id, hcp_record_key="NOPE"))
    assert out["found_in_extract"] is False and out["relationship_rows"] == 0


def test_diagnostic_switch_allows_pending_but_labels_it():
    s = fake_snapshot("PENDING")
    out = run(ac.relationships_for_hcp(FakeDb([s], [fake_obs("H1", "O1", "A1")]), snapshot_id=s.id,
                                       hcp_record_key="H1", diagnostic_allow_pending=True))
    assert out["diagnostic_only"].startswith("NOT_ELIGIBLE_FOR_VERIFICATION")


def test_superseded_chain_reads_rows_under_the_root_snapshot():
    root = fake_snapshot("SUPERSEDED")
    tip = fake_snapshot("APPROVED", supersedes=root.id)
    out = run(ac.relationships_for_hcp(FakeDb([root, tip], [fake_obs("H1", "O1", "A1")]),
                                       snapshot_id=tip.id, hcp_record_key="H1"))
    assert out["provenance"]["snapshot_id"] == str(tip.id) and out["relationship_rows"] == 1


# -- default off ----------------------------------------------------------------
def test_flag_defaults_off_and_router_has_no_routes():
    from app.core.config import settings
    from app.tefca_registry.rce import iqvia_affiliation_routes as routes
    assert getattr(settings, ac.FLAG) is False
    assert list(routes.router.routes) == []
