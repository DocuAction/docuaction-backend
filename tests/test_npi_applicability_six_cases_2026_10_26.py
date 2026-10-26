"""NPI applicability — six decision cases (NPI-001 1.4.0 / rule set 1.6.0), 2026-10-26.

Methodology v1.4 (rev. 2026-10-25) "NPI applicability decision cases"; Treatment XP SOP v2.0 §7/§8.
Pure tests, plus one reconciliation of the whole September 2, 2026 population (24,589 records) via the
descriptive distribution fixture against the independently verified case counts.
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime

import pytest

from app.tefca_registry.bucket_classifier import SEED_RULES, SEED_RULES_V2, SEED_RULES_V3
from app.tefca_registry.rce import curation
from app.tefca_registry.rce import npi_applicability as npa
from app.tefca_registry.rce import quality_rules as qr
from app.tefca_registry.rce.field_map import RCE_FIELDS

VALID = "1982916078"
BAD_CHECKSUM = "1982916079"
TODAY = date(2026, 10, 26)
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "september_2026_purpose_npi_distribution.csv")


def ctx(dataset=None, **over) -> qr.RecordContext:
    values = {f: "" for f in RCE_FIELDS}
    values.update({"id": "9.99.777.1.1", "sequoiaorgtype": "Subparticipant", "name": "SYNTHETIC ORG",
                   "NPI": "", "hl7orgrole": "", "purposesofuse": "T-TRTMNT", "active": "1"})
    values.update(over)
    ds = {"assessment_date": TODAY, "snapshot_date": date(2026, 9, 2)}
    ds.update(dataset or {})
    return qr.RecordContext(line_number=2, parse_status="ok", field_count=41, values=values, dataset=ds)


def npi_001(c):
    return qr.RULE_BY_ID["NPI-001"].evaluate(c)


def all_npi(c):
    return [f for r in qr.RULES if r.category == qr.CAT_NPI and r.stage == "QUALITY" for f in r.evaluate(c)]


# ── field mapping ────────────────────────────────────────────────────────────

def test_source_header_is_purposesofuse_and_the_mapping_is_documented():
    assert npa.SOURCE_HEADER == "purposesofuse"
    assert "purposesofuse" in RCE_FIELDS
    assert npa.FIELD_MAPPING["record_context_key"] == "purposesofuse"
    assert npa.FIELD_MAPPING["curated_column"].endswith("exchange_purposes")
    assert npa.FIELD_MAPPING["entity_column"].endswith("exchange_purposes")
    # the rule reads exactly that key
    a = qr.npi_case(ctx(purposesofuse="T-PH-ECR", NPI=""))
    assert a.raw_purposes == "T-PH-ECR"


# ── the six cases ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("purposes, npi, case", [
    ("T-TRTMNT", "", 1),
    ("T-TRTMNT", VALID, 2),
    ("T-TREAT", "", 3),
    ("T-PH-ECR", "", 4),
    ("", "", 5),
    ("T-PH-ECR", VALID, 6),
])
def test_each_case_is_assigned_from_purpose_and_npi_only(purposes, npi, case):
    a = npa.assign_case(purposes, npi)
    assert a.case == case and a.label == npa.CASE_LABELS[case]
    assert a.raw_purposes == purposes and a.raw_npi == npi, "raw values preserved verbatim"


def test_cases_are_mutually_exclusive_and_exhaustive_over_the_inputs():
    seen = set()
    for purposes in ("T-TRTMNT", "T-TREAT", "T-PH-ECR", "", "ZZZ", "T-TREAT,T-TRTMNT", "T-PH-ECR,ZZZ"):
        for npi in ("", VALID, "12345", BAD_CHECKSUM):
            a = npa.assign_case(purposes, npi)
            assert a.case in (1, 2, 3, 4, 5, 6)
            seen.add(a.case)
    assert seen == {1, 2, 3, 4, 5, 6}


# ── token matching ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "T-TRTMNT", "  T-TRTMNT  ", "T-PH-ECR,T-TRTMNT", "T-TRTMNT,T-PH-ECR", "T-PH-ECR, T-TRTMNT ,T-IAS",
    "T-PH-ECR;T-TRTMNT", "T-PH-ECR|T-TRTMNT", "T-PH-ECR T-TRTMNT", "T-TREAT,T-TRTMNT", "ZZZ,T-TRTMNT",
    "T-GOVDTRM,T-HCO,T-IAS,T-PH,T-PH-ECR,T-PH-ELR,T-PYMNT,T-TREAT,T-TRTMNT",
])
def test_t_trtmnt_anywhere_in_the_list_takes_precedence(value):
    assert npa.assign_case(value, "").case == 1
    assert npa.assign_case(value, VALID).case == 2


@pytest.mark.parametrize("value", ["T-TRTMNTX", "XT-TRTMNT", "T-TRTMNT-2", "TRTMNT", "T_TRTMNT", "t-trtmnt", "T-TRTMN"])
def test_similar_strings_never_match(value):
    a = npa.assign_case(value, "")
    assert not a.has_trtmnt and a.case == 5, "an unrecognized look-alike is unrecognized, not Treatment"
    assert a.unrecognized_tokens == (value,)


def test_unknown_codes_alone_and_alongside_recognized_codes():
    alone = npa.assign_case("ZZZ", "")
    assert alone.case == 5 and alone.unrecognized_tokens == ("ZZZ",) and alone.recognized_tokens == ()
    mixed = npa.assign_case("T-PH-ECR,ZZZ", "")
    assert mixed.case == 5, "an unrecognized code alongside recognized ones does not become case 4"
    assert mixed.recognized_tokens == ("T-PH-ECR",) and mixed.unrecognized_tokens == ("ZZZ",)
    with_trtmnt = npa.assign_case("ZZZ,T-TRTMNT", "")
    assert with_trtmnt.case == 1, "T-TRTMNT precedence survives an unknown neighbour"
    assert npa.assign_case("ZZZ", VALID).case == 6
    assert "ZZZ" not in npa.RECOGNIZED_PURPOSE_TOKENS


def test_recognized_vocabulary_is_the_pur_001_vocabulary_not_a_second_list():
    from app.tefca_registry.rce.field_map import PURPOSE_VOCABULARY
    assert npa.RECOGNIZED_PURPOSE_TOKENS == frozenset(PURPOSE_VOCABULARY)


# ── blank role, active/inactive ──────────────────────────────────────────────

@pytest.mark.parametrize("role", ["", "provider", "payer", "agency", "HIE/HIO", "diagnostics"])
def test_blank_or_any_role_never_suppresses_case_1(role):
    f = npi_001(ctx(purposesofuse="T-TRTMNT", hl7orgrole=role))
    assert [x.issue_type for x in f] == ["NPI_REQUIRED"]
    assert "case 1" in f[0].description


@pytest.mark.parametrize("active", ["1", "0", "", "UNSUPPORTED:x"])
def test_active_status_is_not_a_case_input_and_inactive_is_neither_exempt_nor_noncompliant(active):
    for purposes, case in (("T-TRTMNT", 1), ("T-TREAT", 3), ("T-PH-ECR", 4), ("", 5)):
        a = qr.npi_case(ctx(purposesofuse=purposes, active=active))
        assert a.case == case
    f = npi_001(ctx(purposesofuse="T-TRTMNT", active=active))
    assert [x.issue_type for x in f] == ["NPI_REQUIRED"]
    assert "non-compliant" not in f[0].description.lower().replace("non-compliance", "")
    assert "not consulted" in f[0].description


# ── supplied NPIs: valid, malformed, checksum-invalid (cases 2 and 6) ────────

@pytest.mark.parametrize("purposes, case", [("T-TRTMNT", 2), ("T-PH-ECR", 6), ("", 6), ("ZZZ", 6)])
def test_supplied_npi_raises_no_presence_finding_but_validity_still_runs(purposes, case):
    assert qr.npi_case(ctx(purposesofuse=purposes, NPI=VALID)).case == case
    assert npi_001(ctx(purposesofuse=purposes, NPI=VALID)) == []
    assert all_npi(ctx(purposesofuse=purposes, NPI=VALID)) == [], "valid: nothing from the quality NPI rules"
    malformed = all_npi(ctx(purposesofuse=purposes, NPI="12345"))
    assert [f.issue_type for f in malformed] == ["NPI_LENGTH_INVALID"]
    checksum = all_npi(ctx(purposesofuse=purposes, NPI=BAD_CHECKSUM))
    assert [f.issue_type for f in checksum] == ["NPI_CHECKSUM_INVALID"]


def test_supplied_is_not_verified_in_the_handling_text():
    assert "not mean verified" in npa.CASE_HANDLING[2] and "not mean verified" in npa.CASE_HANDLING[6]


# ── findings per case ────────────────────────────────────────────────────────

def test_case_1_observation_is_npi_required_with_separate_applicability_facts():
    f = npi_001(ctx(purposesofuse="T-PH-ECR,T-TRTMNT"))
    assert [x.issue_type for x in f] == ["NPI_REQUIRED"]
    assert f[0].severity == qr.HIGH and f[0].correction_authority == qr.HUMAN_REQUIRED
    d = f[0].description
    assert "no compliance determination" in d and "case 1" in d
    assert "§7 requirement=unresolved" in d and "direction=unknown" in d
    assert "snapshot=2026-09-02" in d and "assessed=2026-10-26" in d
    assert "governing date=2026-09-03" in d and "deadline=pre_deadline" in d


def test_case_3_and_case_5_are_review_required_not_optional():
    for purposes in ("T-TREAT", "", "ZZZ", "T-PH-ECR,ZZZ"):
        f = npi_001(ctx(purposesofuse=purposes))
        assert [x.issue_type for x in f] == ["NPI_APPLICABILITY_UNRESOLVED"], purposes
        assert f[0].severity == qr.MEDIUM and f[0].correction_authority == qr.HUMAN_REQUIRED
        assert "not assumed optional" in f[0].description
        assert "Not a hold, not a bucket" in f[0].description


def test_case_4_is_informational_and_other_checks_are_preserved():
    f = npi_001(ctx(purposesofuse="T-GOVDTRM-ACP,T-PH-ECR"))
    assert [x.issue_type for x in f] == ["NPI_NOT_SUPPLIED"] and f[0].severity == qr.INFO
    # the rule set still evaluates everything else for the record (PUR-001 on an unknown token, e.g.)
    unknown = [x for r in qr.RULES if r.rule_id == "PUR-001" for x in r.evaluate(ctx(purposesofuse="T-PH-ECR,ZZZ"))]
    assert [x.issue_type for x in unknown] == ["UNKNOWN_PURPOSE_TOKEN"]


def test_profile_role_basis_is_unchanged_and_wins_the_label():
    f = npi_001(ctx(purposesofuse="", hl7orgrole="provider", sequoiaorgtype="Participant"))
    assert [x.issue_type for x in f] == ["NPI_REQUIRED"] and "profile requires one" in f[0].description


def test_assessments_before_the_software_effective_date_keep_1_3_0_behaviour():
    old = {"assessment_date": date(2026, 10, 25)}
    assert [x.issue_type for x in npi_001(ctx(purposesofuse="T-TREAT", dataset=old))] == ["NPI_NOT_SUPPLIED"]
    assert [x.issue_type for x in npi_001(ctx(purposesofuse="", dataset=old))] == ["NPI_NOT_SUPPLIED"]
    assert [x.issue_type for x in npi_001(ctx(purposesofuse="T-TRTMNT", dataset=old))] == ["NPI_REQUIRED"]
    assert "case 1" not in npi_001(ctx(purposesofuse="T-TRTMNT", dataset=old))[0].description
    assert qr.NPI_CASES_EFFECTIVE == date(2026, 10, 26)


# ── T-TREAT exception, exchange direction ────────────────────────────────────

def test_t_treat_exception_established_versus_unknown():
    a = npa.assign_case("T-TREAT", "")
    unknown = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=TODAY)
    assert unknown.initiating_requirement == npa.UNRESOLVED and unknown.treat_exception == npa.EXCEPTION_UNKNOWN
    established = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=TODAY,
                                           exchange_direction=npa.INITIATING, treat_exception=npa.EXCEPTION_ESTABLISHED)
    assert established.initiating_requirement == npa.DOES_NOT_APPLY and "§7(a)(v)" in " ".join(established.notes)
    not_established = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=TODAY,
                                               exchange_direction=npa.INITIATING, treat_exception=npa.EXCEPTION_NOT_ESTABLISHED)
    assert not_established.initiating_requirement == npa.APPLIES
    # never an automatic pass or fail
    assert unknown.compliance_determination is None and established.compliance_determination is None


@pytest.mark.parametrize("direction, expected", [
    (npa.INITIATING, npa.APPLIES), (npa.RESPONDING, npa.DOES_NOT_APPLY), (npa.DIRECTION_UNKNOWN, npa.UNRESOLVED),
])
def test_initiating_responding_unknown_applicability_for_case_1(direction, expected):
    a = npa.assign_case("T-TRTMNT", "")
    r = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=TODAY, exchange_direction=direction)
    assert r.initiating_requirement == expected


@pytest.mark.parametrize("purposes, direction, expected", [
    ("T-TREAT", npa.INITIATING, npa.APPLIES),              # supplied NPI satisfies presence; §7 still applies
    ("T-TREAT", npa.RESPONDING, npa.DOES_NOT_APPLY),
    ("T-TREAT", npa.DIRECTION_UNKNOWN, npa.UNRESOLVED),
    ("", npa.DIRECTION_UNKNOWN, npa.UNRESOLVED),           # purpose missing: unresolved even with an NPI
    ("ZZZ", npa.DIRECTION_UNKNOWN, npa.UNRESOLVED),        # unrecognized code: unresolved even with an NPI
    ("T-PH-ECR,ZZZ", npa.INITIATING, npa.UNRESOLVED),
    ("T-PYMNT", npa.DIRECTION_UNKNOWN, npa.DOES_NOT_APPLY),  # other recognized purposes only
    ("T-PH-ECR,T-GOVDTRM-ACP", npa.INITIATING, npa.DOES_NOT_APPLY),
])
def test_case_6_presence_satisfied_does_not_mean_requirement_inapplicable(purposes, direction, expected):
    a = npa.assign_case(purposes, VALID)
    assert a.case == 6
    r = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=TODAY, exchange_direction=direction)
    assert r.npi_presence == npa.NPI_SUPPLIED
    assert r.initiating_requirement == expected
    assert r.compliance_determination is None


def test_case_2_records_presence_supplied_and_decides_by_direction():
    a = npa.assign_case("T-TRTMNT", VALID)
    for direction, expected in ((npa.INITIATING, npa.APPLIES), (npa.RESPONDING, npa.DOES_NOT_APPLY),
                                (npa.DIRECTION_UNKNOWN, npa.UNRESOLVED)):
        r = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=TODAY, exchange_direction=direction)
        assert (r.npi_presence, r.initiating_requirement) == (npa.NPI_SUPPLIED, expected)
    missing = npa.assess_applicability(npa.assign_case("T-TRTMNT", ""), snapshot_date=None, assessment_date=TODAY)
    assert missing.npi_presence == npa.NPI_MISSING
    assert "NPI=missing" in npa.describe(npa.assign_case("T-TRTMNT", ""), missing)


def test_direction_is_never_inferred_from_a_blank_role_or_label():
    assert npa.exchange_direction_from_record("") == npa.DIRECTION_UNKNOWN
    assert npa.exchange_direction_from_record(None) == npa.DIRECTION_UNKNOWN
    assert npa.exchange_direction_from_record("initiating-node") == npa.INITIATING
    # the rule-level helper reads only organizationNodeType
    assert qr.npi_applicability(ctx(hl7orgrole="", sequoiaorgtype="Participant")).exchange_direction == npa.DIRECTION_UNKNOWN
    assert qr.npi_applicability(ctx(organizationNodeType="initiating-node")).exchange_direction == npa.INITIATING


# ── dates ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("snap, expected", [
    (date(2026, 9, 2), npa.PRE_DEADLINE), (date(2026, 9, 3), npa.ON_OR_AFTER_DEADLINE),
    (date(2026, 10, 1), npa.ON_OR_AFTER_DEADLINE),
])
def test_snapshot_before_on_and_after_september_3(snap, expected):
    a = npa.assign_case("T-TRTMNT", "")
    r = npa.assess_applicability(a, snapshot_date=snap, assessment_date=TODAY)
    assert r.deadline_applicability == expected
    assert r.governing_requirement_date == date(2026, 9, 3) == npa.SECTION_7_COMPLIANCE_DATE
    assert r.software_effective_date == date(2026, 10, 26) and r.assessment_date == TODAY
    assert r.snapshot_date == snap
    if expected == npa.PRE_DEADLINE:
        assert "cannot establish a §7 breach" in " ".join(r.notes)
        assert "assessed separately" in " ".join(r.notes)


def test_missing_snapshot_date_is_unresolved_and_never_replaced_by_the_assessment_date():
    a = npa.assign_case("T-TRTMNT", "")
    r = npa.assess_applicability(a, snapshot_date=None, assessment_date=TODAY)
    assert r.deadline_applicability == npa.UNRESOLVED and r.snapshot_date is None
    assert "NOT a substitute" in " ".join(r.notes)
    f = npi_001(ctx(purposesofuse="T-TRTMNT", dataset={"snapshot_date": None}))
    assert "snapshot=missing" in f[0].description and "deadline=unresolved" in f[0].description


def test_october_assessment_date_does_not_manufacture_september_noncompliance():
    a = npa.assign_case("T-TRTMNT", "")
    r = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=date(2026, 10, 26))
    assert r.deadline_applicability == npa.PRE_DEADLINE
    assert r.assessment_date == date(2026, 10, 26) and r.snapshot_date == date(2026, 9, 2)


def test_the_four_dates_are_distinct_fields_with_the_sop_values():
    assert npa.SOP_EFFECTIVE_DATE == date(2026, 8, 3)
    assert npa.SECTION_7_COMPLIANCE_DATE == date(2026, 9, 3)
    assert npa.CASES_SOFTWARE_EFFECTIVE_DATE == date(2026, 10, 26) != qr.BR_NPI_TRTMNT_EFFECTIVE == date(2026, 10, 25)
    fields = set(npa.ApplicabilityAssessment.__dataclass_fields__)
    assert {"snapshot_date", "assessment_date", "governing_requirement_date", "software_effective_date"} <= fields


def test_engine_reads_snapshot_date_only_from_authoritative_metadata_never_receipt_date():
    import inspect
    from types import SimpleNamespace
    from datetime import datetime
    from app.tefca_registry.rce import quality_engine
    src = inspect.getsource(quality_engine)
    assert 'dataset["snapshot_date"] = snapshot_date_from_metadata(intake)' in src
    assert "received_at.date()" not in src, "the stated receipt date is not a population date"
    assert 'dataset["assessment_date"] = run.started_at.date()' in src
    # received later than the snapshot, no metadata → unresolved (None); with metadata → the recorded date
    later = SimpleNamespace(received_at=datetime(2026, 9, 24), created_at=datetime(2026, 9, 24), source_metadata={})
    assert quality_engine.snapshot_date_from_metadata(later) is None
    recorded = SimpleNamespace(received_at=datetime(2026, 9, 24), source_metadata={"snapshot_date": "2026-09-02"})
    assert quality_engine.snapshot_date_from_metadata(recorded) == date(2026, 9, 2)
    assert quality_engine.snapshot_date_from_metadata(SimpleNamespace(source_metadata={"snapshot_date": "not-a-date"})) is None


def test_snapshot_date_recording_is_explicit_bounded_and_audited():
    from types import SimpleNamespace
    from datetime import datetime
    from app.tefca_registry.rce import snapshot_date as sd
    from app.tefca_registry import audit as reg_audit

    recorded = []
    original = reg_audit.record
    reg_audit.record = lambda db, action, entity_id, **kw: recorded.append((action, kw))  # pure stand-in
    try:
        intake = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", received_at=datetime(2026, 9, 24),
                                 source_metadata={"declared_source": "ONC/RCE"})
        user = SimpleNamespace(id="u1", email="pm@example.invalid", role="program_manager")
        out = sd.record_snapshot_date(None, intake, snapshot_date=date(2026, 9, 2),
                                      basis="ONC transmittal: directory extract as of 2026-09-02 (file onc-snapshot-20260902.csv)", user=user)
        assert intake.source_metadata["snapshot_date"] == "2026-09-02" and intake.received_at == datetime(2026, 9, 24)
        assert intake.source_metadata["declared_source"] == "ONC/RCE", "other metadata preserved"
        assert out["received_at"] == "2026-09-24T00:00:00" and out["previous_snapshot_date"] is None
        assert recorded and recorded[0][0] == "source_snapshot_date_recorded"
        assert recorded[0][1]["metadata"]["snapshot_date"] == "2026-09-02"
        with pytest.raises(sd.SnapshotDateRefused):
            sd.record_snapshot_date(None, intake, snapshot_date=date(2026, 9, 2), basis="short", user=user)
        with pytest.raises(sd.SnapshotDateRefused):   # after the stated receipt date
            sd.record_snapshot_date(None, intake, snapshot_date=date(2026, 9, 25), basis="x" * 20, user=user)
        with pytest.raises(sd.SnapshotDateRefused):   # future
            sd.record_snapshot_date(None, intake, snapshot_date=date(2099, 1, 1), basis="x" * 20, user=user)
    finally:
        reg_audit.record = original


def test_snapshot_date_route_is_registered_and_gated_to_program_manager(client):
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app
    from app.tefca_registry.rce import delivery_routes as dr
    from test_rbac_roles import _Session, _User

    assert any(r.path == "/api/tefca/rce/deliveries/{intake_id}/snapshot-date" and "POST" in r.methods
               for r in dr.router.routes)
    url = "/api/tefca/rce/deliveries/00000000-0000-0000-0000-000000000001/snapshot-date"
    form = {"snapshot_date": "2026-09-02", "basis": "synthetic basis text for the role gate"}
    assert client.post(url, data=form).status_code == 401
    try:
        for role in ("viewer", "reviewer", "senior_analyst", "qalead"):
            user = _User(role)

            async def _override(user=user):
                yield _Session(user)
            app.dependency_overrides[get_db] = _override
            token = create_access_token({"sub": user.id, "role": role})
            assert client.post(url, data=form, headers={"Authorization": f"Bearer {token}"}).status_code == 403, role
        user = _User("program_manager")

        async def _pm():
            yield _Session(user)
        app.dependency_overrides[get_db] = _pm
        token = create_access_token({"sub": user.id, "role": "program_manager"})
        r = client.post(url, data={"snapshot_date": "not-a-date", "basis": form["basis"]},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 422, "an unparseable date is refused before any lookup"
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── no automatic B4 / HELD / correction / QA ─────────────────────────────────

def test_no_bucket_hold_correction_or_approval_fields_exist():
    fields = set(npa.ApplicabilityAssessment.__dataclass_fields__) | set(npa.CaseAssignment.__dataclass_fields__)
    for forbidden in ("bucket", "held", "hold", "classification", "correction", "suggested_value", "qa", "approved", "disposition"):
        assert not any(forbidden in f.lower() for f in fields), forbidden


def test_missing_npi_findings_never_hold_a_record_by_themselves():
    assert "NPI-001" in curation.NON_HOLDING_RULES
    assert curation.blocks_promotion("HIGH", "OPEN", "NPI-001") is False
    assert curation.blocks_promotion("MEDIUM", "OPEN", "NPI-001") is False
    assert curation.blocks_promotion("HIGH", "OPEN", "NPI-003") is True, "identifier validity still holds"
    assert curation.blocks_promotion("HIGH", "OPEN") is True, "callers that pass no rule keep the old behaviour"


def test_no_seed_rule_keys_on_the_presence_or_applicability_findings():
    for spec in SEED_RULES + SEED_RULES_V2 + SEED_RULES_V3:
        text = str(spec["conditions"])
        assert "NPI_REQUIRED" not in text and "NPI_APPLICABILITY_UNRESOLVED" not in text and "npi_required" not in text


def test_findings_carry_no_suggested_value_or_source_correction():
    for purposes in ("T-TRTMNT", "T-TREAT", "", "T-PH-ECR"):
        for f in npi_001(ctx(purposesofuse=purposes)):
            assert f.suggested_value is None and f.suggested_source is None
            assert f.correction_authority in (qr.HUMAN_REQUIRED, qr.NO_CORRECTION)


def test_nppes_candidate_never_overwrites_the_submitted_npi():
    """The quality NPI rules read the submitted value only; the entity-resolution fallback carries the
    curated (delivered) NPI, never an NPPES candidate. Analyst confirmation and independent QA stay in
    the review routes."""
    import inspect
    from app.tefca_registry.rce import quality_rules as module
    src = inspect.getsource(module._npi_001)
    assert "nppes" not in src.lower() and "suggested_value=" not in src


# ── September 2026 reconciliation (descriptive counts, not compliance) ───────

EXPECTED_CASES = {1: 3464, 2: 20002, 3: 1, 4: 425, 5: 683, 6: 14}


def _distribution():
    with open(FIXTURE, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_fixture_describes_the_whole_september_population():
    rows = _distribution()
    assert sum(int(r["records"]) for r in rows) == 24589


def test_all_24589_september_records_reconcile_to_the_verified_case_counts():
    totals = {k: 0 for k in EXPECTED_CASES}
    for r in _distribution():
        npi = VALID if r["npi_supplied"] == "1" else ""
        a = npa.assign_case(r["purpose_set"], npi)
        totals[a.case] += int(r["records"])
    assert totals == EXPECTED_CASES
    assert sum(totals.values()) == 24589


def test_case_1_reconciles_by_active_and_organization_type():
    active = {"1": 0, "0": 0}
    otype = {"Participant": 0, "Subparticipant": 0}
    for r in _distribution():
        if r["npi_supplied"] == "1":
            continue
        if npa.assign_case(r["purpose_set"], "").case == 1:
            active[r["active"]] += int(r["records"])
            otype[r["sequoiaorgtype"]] += int(r["records"])
    assert active == {"1": 2961, "0": 503}
    assert otype == {"Participant": 633, "Subparticipant": 2831}


def test_the_reconciliation_is_descriptive_not_a_compliance_result():
    """Case 1 is an observation count; none of the 3,464 is a compliance finding until applicability,
    the §7 date and an analyst determination with independent QA say so."""
    a = npa.assign_case("T-TRTMNT", "")
    r = npa.assess_applicability(a, snapshot_date=date(2026, 9, 2), assessment_date=TODAY)
    assert r.initiating_requirement == npa.UNRESOLVED and r.deadline_applicability == npa.PRE_DEADLINE
    assert r.compliance_determination is None
