"""
Evidence provenance (B3 / Phase 2) — the facts that make a determination re-checkable.

WHAT THESE PIN
──────────────
That an API version can never masquerade as a dataset version, that a source
publishing no version says so instead of implying one, that a hash is taken over
the RAW response rather than a projection, and that PPEF lineage is recorded as
an ordered hop list that does not flatten one-to-many relationships.

WHAT THEY DELIBERATELY DO NOT DO
No PPEF data is ingested here. These tests exercise the provenance CAPABILITY
against fixtures; the tables are inert until a real snapshot is loaded.
"""

from __future__ import annotations

import json

import pytest

from app.core.evidence_provenance import (
    LEVELS_THAT_MAY_ESTABLISH_IDENTITY,
    MATCH_LEVEL,
    PROVENANCE_MODEL_VERSION,
    UNKNOWN_DATASET_VERSION,
    IdentifierType,
    LineageHop,
    MatchMethod,
    ObservationProvenance,
    PpefRelationship,
    RetrievalMethod,
    SourceVersionRef,
    build_ppef_lineage,
    canonical_json,
    file_sha256,
    new_correlation_id,
    observation_hash,
    unknown_version,
)


# ── hashing ──────────────────────────────────────────────────────────────────

def test_observation_hash_is_deterministic_and_order_independent():
    a = {"npi": "1982916078", "status": "A", "name": "UTMB"}
    b = {"name": "UTMB", "status": "A", "npi": "1982916078"}
    assert observation_hash(a) == observation_hash(b)
    assert len(observation_hash(a)) == 64


def test_observation_hash_changes_when_the_payload_changes():
    a = observation_hash({"npi": "1982916078", "status": "A"})
    b = observation_hash({"npi": "1982916078", "status": "D"})
    assert a != b


def test_canonical_json_is_stable():
    payload = {"z": 1, "a": [3, 2, 1], "n": None}
    assert canonical_json(payload) == canonical_json(dict(reversed(list(payload.items()))))


def test_file_sha256_matches_the_known_area1_delivery_hash():
    """Pins the hashing primitive against a value already in the database."""
    assert file_sha256(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


# ── API version is not dataset version ───────────────────────────────────────

def test_api_version_is_never_used_as_dataset_version():
    """NPPES publishes an API version and no data version. It must say so.

    This is the specific defect the design named: the live system records "2.1"
    — the API version — in the dataset-version field.
    """
    ref = unknown_version("NPPES", api_version="2.1")
    assert ref.api_version == "2.1"
    assert ref.dataset_version is None
    assert ref.effective_dataset_version == UNKNOWN_DATASET_VERSION
    assert ref.effective_dataset_version != "2.1"
    row = ref.as_row()
    assert row["version_label"] == UNKNOWN_DATASET_VERSION
    assert row["api_version"] == "2.1"


def test_unknown_version_is_not_point_in_time():
    """A live lookup with no preserved artefact is NOT reproducible."""
    ref = unknown_version("OIG_LEIE")
    assert ref.is_point_in_time is False
    assert ref.as_row()["is_point_in_time"] is False
    assert len(ref.note) > 60, "must explain why it is not reproducible"


def test_dataset_version_never_falls_back_to_retrieval_time():
    ref = SourceVersionRef(source="NPPES", retrieval_method=RetrievalMethod.API,
                           retrieved_at="2026-08-22T10:00:00Z", api_version="2.1")
    assert ref.effective_dataset_version == UNKNOWN_DATASET_VERSION
    assert "2026" not in ref.effective_dataset_version


def test_preserved_artefact_is_point_in_time():
    """A hashed file IS reproducible, and is recorded as such."""
    ref = SourceVersionRef(
        source="CMS_PPEF_PRACTICE_LOCATION",
        retrieval_method=RetrievalMethod.DOWNLOAD,
        retrieved_at="2026-08-22T10:00:00Z",
        dataset_version="2026.07.17",
        source_file_hash="a" * 64,
        dataset_identifier="2457ea29-fc82-48b0-86ec-3b0755de7515",
    )
    assert ref.is_point_in_time is True
    assert ref.effective_dataset_version == "2026.07.17"


def test_dataset_identifier_plus_version_is_point_in_time():
    """A live CMS API call pinned to a dataset UUID and version qualifies."""
    ref = SourceVersionRef(
        source="CMS_PPEF_ENROLLMENT", retrieval_method=RetrievalMethod.API,
        retrieved_at="2026-08-22T10:00:00Z",
        dataset_identifier="2457ea29-fc82-48b0-86ec-3b0755de7515",
        dataset_version="Q3 2026")
    assert ref.is_point_in_time is True


def test_no_provenance_is_manufactured():
    """Every optional field defaults to None, never to a plausible-looking value."""
    ref = SourceVersionRef(source="SAM_GOV", retrieval_method=RetrievalMethod.API,
                           retrieved_at="2026-08-22T10:00:00Z")
    row = ref.as_row()
    for field in ("source_as_of", "source_file_hash", "dataset_identifier",
                  "api_version", "http_last_modified", "record_count", "storage_uri"):
        assert row[field] is None, f"{field} was invented"
    assert row["version_label"] == UNKNOWN_DATASET_VERSION
    assert row["is_point_in_time"] is False


# ── PPEF relational lineage ──────────────────────────────────────────────────

def _enrollment_fixture():
    """Two enrolments, one with several children. Fixture only — no ingestion."""
    return [
        {
            "enrollment_id": "I20040309000221", "pac_id": "1234567890",
            "practice_locations": [
                {"ADR_LN_1": "301 UNIVERSITY BLVD", "CITY_NAME": "GALVESTON",
                 "STATE_CD": "TX", "ZIP_CD": "775550565", "row_key": "PL-1"},
                {"ADR_LN_1": "200 MAIN ST", "CITY_NAME": "HOUSTON",
                 "STATE_CD": "TX", "ZIP_CD": "77002", "row_key": "PL-2"},
            ],
            "secondary_specialties": [{"taxonomy": "282N00000X", "row_key": "SS-1"}],
            "additional_npis": [{"npi": "1770559767", "row_key": "AN-1"}],
            "reassignments": [
                {"receiving_enrollment_id": "I20051212000388", "row_key": "RA-1"}],
        },
        {"enrollment_id": "I20090101000999", "practice_locations": [], "row_key": "E-2"},
    ]


def test_ppef_lineage_does_not_flatten_one_to_many():
    """Two enrolments with five children must yield every hop, not a summary."""
    hops = build_ppef_lineage("1982916078", _enrollment_fixture(),
                              source_version_id="11111111-1111-1111-1111-111111111111")
    kinds = [h.relationship_type for h in hops]
    assert kinds.count(PpefRelationship.ENROLLED_AS.value) == 3          # 2 enrol + 1 pac
    assert kinds.count(PpefRelationship.HAS_PRACTICE_LOCATION.value) == 2
    assert kinds.count(PpefRelationship.HAS_SECONDARY_SPECIALTY.value) == 1
    assert kinds.count(PpefRelationship.HAS_ADDITIONAL_NPI.value) == 1
    assert kinds.count(PpefRelationship.REASSIGNS_BENEFITS_TO.value) == 1
    assert len(hops) == 8


def test_ppef_lineage_hop_sequence_is_dense_and_ordered():
    hops = build_ppef_lineage("1982916078", _enrollment_fixture())
    assert [h.hop_sequence for h in hops] == list(range(1, len(hops) + 1))


def test_reassignment_records_both_enrollment_ids():
    """REASGN_BNFT_ENRLMT_ID -> RCV_BNFT_ENRLMT_ID, both named."""
    hops = build_ppef_lineage("1982916078", _enrollment_fixture())
    reassign = [h for h in hops
                if h.relationship_type == PpefRelationship.REASSIGNS_BENEFITS_TO.value]
    assert len(reassign) == 1
    hop = reassign[0]
    assert hop.from_identifier_value == "I20040309000221"      # the practitioner
    assert hop.to_identifier_value == "I20051212000388"        # the receiver
    assert hop.from_identifier_type == IdentifierType.ENROLLMENT_ID.value
    assert hop.to_identifier_type == IdentifierType.ENROLLMENT_ID.value


def test_pac_id_is_a_first_class_hop():
    """PAC ID identifies the enrolling provider and may span several enrolments."""
    hops = build_ppef_lineage("1982916078", _enrollment_fixture())
    pac = [h for h in hops if h.to_identifier_type == IdentifierType.PAC_ID.value]
    assert len(pac) == 1
    assert pac[0].to_identifier_value == "1234567890"


def test_every_hop_carries_its_source_version():
    """Components are separate files with separate hashes."""
    vid = "22222222-2222-2222-2222-222222222222"
    hops = build_ppef_lineage("1982916078", _enrollment_fixture(), source_version_id=vid)
    assert all(h.source_version_id == vid for h in hops)
    assert {h.ppef_component for h in hops} == {
        "ENROLLMENT", "PRACTICE_LOCATION", "SECONDARY_SPECIALTY",
        "ADDITIONAL_NPIS", "REASSIGNMENT"}


def test_lineage_is_empty_without_enrollments():
    assert build_ppef_lineage("1982916078", []) == []
    assert build_ppef_lineage("1982916078", [{"pac_id": "x"}]) == []  # no enrollment_id


# ── match method and level ───────────────────────────────────────────────────

def test_match_levels_are_stated_not_inferred():
    assert MATCH_LEVEL[MatchMethod.EXACT_IDENTIFIER.value] == 1
    assert MATCH_LEVEL[MatchMethod.STRUCTURED.value] == 2
    assert MATCH_LEVEL[MatchMethod.FUZZY.value] == 3
    assert MATCH_LEVEL[MatchMethod.HUMAN.value] == 4


def test_only_identifier_and_human_may_establish_identity():
    """Levels 2 and 3 corroborate; they never establish identity alone."""
    assert LEVELS_THAT_MAY_ESTABLISH_IDENTITY == {1, 4}
    assert MATCH_LEVEL[MatchMethod.STRUCTURED.value] not in LEVELS_THAT_MAY_ESTABLISH_IDENTITY
    assert MATCH_LEVEL[MatchMethod.FUZZY.value] not in LEVELS_THAT_MAY_ESTABLISH_IDENTITY


# ── the provenance record ────────────────────────────────────────────────────

def test_observation_provenance_produces_evidence_columns():
    prov = ObservationProvenance(
        source="NPPES", entity_id="e-1", dimension="IDENTITY",
        identifier_searched="1982916078", identifier_type=IdentifierType.NPI.value,
        observation_result="MATCH_OBSERVED",
        raw_payload={"npi": "1982916078", "status": "A"},
        match_method=MatchMethod.EXACT_IDENTIFIER.value,
        match_version="1.0", rule_version="2",
        correlation_id="33333333-3333-3333-3333-333333333333",
        vocabulary_version="1.0")
    cols = prov.as_evidence_columns()
    assert cols["observation_result"] == "MATCH_OBSERVED"
    assert cols["match_level"] == 1
    assert len(cols["observation_hash"]) == 64
    assert cols["vocabulary_version"] == "1.0"
    assert "source_version_id" not in cols, "resolved by the caller after the write"


def test_observation_hash_is_none_without_a_payload():
    """No payload means no hash — not a hash of nothing."""
    prov = ObservationProvenance(source="SAM_GOV", entity_id="e-1", dimension="EXCLUSION")
    assert prov.as_evidence_columns()["observation_hash"] is None


def test_observation_result_uses_the_canonical_layer_1_vocabulary():
    """Provenance records a Layer 1 state, never a disposition or a bucket."""
    from app.core.evidence_vocabulary import LAYER_1_STATES, validate_observation_result

    for state in LAYER_1_STATES:
        prov = ObservationProvenance(source="NPPES", entity_id="e", dimension="IDENTITY",
                                     observation_result=state)
        assert validate_observation_result(prov.observation_result).value == state
    with pytest.raises(ValueError):
        validate_observation_result("PASS")     # Layer 3
    with pytest.raises(ValueError):
        validate_observation_result("B2")       # Layer 4


def test_correlation_id_is_a_uuid():
    import uuid as _uuid
    assert _uuid.UUID(new_correlation_id())


def test_provenance_model_version_recorded():
    assert PROVENANCE_MODEL_VERSION == "1.0"


# ── schema ───────────────────────────────────────────────────────────────────

def test_provenance_columns_are_declared_nullable_with_no_default():
    """Historical rows must keep NULL. A default would rewrite all 1,984."""
    from app.Tefca.models import TEFCADimensionEvidence

    table = TEFCADimensionEvidence.__table__
    for name in ("source_version_id", "observation_result", "identifier_searched",
                 "identifier_type", "observation_hash", "raw_observation_ref",
                 "match_method", "match_level", "match_version", "rule_version",
                 "correlation_id"):
        col = table.columns.get(name)
        assert col is not None, f"{name} is not declared on the model"
        assert col.nullable is True, f"{name} must be nullable"
        assert col.server_default is None, (
            f"{name} has a server_default, which would rewrite historical rows")


def test_source_version_snapshot_separates_api_and_dataset_version():
    from app.Tefca.models import SourceVersionSnapshot

    cols = SourceVersionSnapshot.__table__.columns
    assert "version_label" in cols and "api_version" in cols, (
        "the two must be separate columns so one can never stand in for the other")
    assert cols["is_point_in_time"].nullable is False


def test_relationship_path_enforces_one_hop_per_sequence():
    from app.Tefca.models import EvidenceRelationshipPath

    uniques = [c for c in EvidenceRelationshipPath.__table__.constraints
               if getattr(c, "name", None) == "uq_evidence_hop"]
    assert uniques, "an evidence item may not have two hops at the same sequence"
