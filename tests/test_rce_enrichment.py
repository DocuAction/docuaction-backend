"""
Track 1 — RCE-enriched fixtures, D5/D6, applicability, and the DB resolver.

WHAT THESE TESTS ARE FOR
The evidence layer used to be exercised against 30 FHIR fixtures that carried no
TEFCA identifiers, no exchange purpose and no organisational classification, so
D5 could only ever report "not supplied". These pin the behaviour once the
record actually carries that data — and, just as importantly, pin the cases
where a field is legitimately absent and the answer must NOT be a pass.
"""

from __future__ import annotations

import logging
import os

import pytest

from app.Tefca import rce_fields
from app.Tefca.applicability import (
    build_profile,
    node_type_of,
    tefca_class_of,
)
from app.Tefca.evidence_assembly import assemble_dimensions
from app.Tefca.evidence_dimensions import Dimension, Disposition
from app.Tefca.mock_data import (
    ALL_MOCK_ENTITIES,
    MOCK_ENTITY_INDEX,
    RCE_PROFILE_ENTITIES,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def entity(entity_id: str) -> dict:
    return MOCK_ENTITY_INDEX[entity_id]


def population_resolver():
    from app.Tefca.entity_resolution import make_parent_resolver
    return make_parent_resolver(None, list(ALL_MOCK_ENTITIES))


def dimensions_for(entity_id: str, sources=None):
    ent = entity(entity_id)
    profile = build_profile(ent)
    return {
        d.dimension: d
        for d in assemble_dimensions(ent, profile, sources or {}, None,
                                     parent_resolver=population_resolver())
    }


def d5_facets(entity_id: str) -> dict:
    d5 = dimensions_for(entity_id)[Dimension.D5_TEFCA_ALIGNMENT.value]
    return d5.items[0].normalized_values["facets"]


# ═══ STEP 1 — enrichment ═════════════════════════════════════════════════════

class TestEnrichedFixtures:
    def test_every_entity_carries_all_41_rce_fields(self):
        for ent in ALL_MOCK_ENTITIES:
            block = rce_fields.rce_block(ent)
            assert set(block) == set(rce_fields.RCE_FIELDS), ent["id"]
            assert len(block) == 41, ent["id"]

    def test_enrichment_did_not_disturb_existing_fixtures(self):
        """The bucket fixtures are pinned by ~80 other tests. Enrichment adds
        `_rce` and touches nothing else — this is the guard on that promise."""
        first = ALL_MOCK_ENTITIES[0]
        assert first["id"] == "rce-org-b1-001"
        assert [i["value"] for i in first["identifier"]
                if "us-npi" not in i["system"]] == ["PART-001"]
        assert first["active"] is True
        assert first["_expected_bucket"] == 1

    def test_representative_mix_is_present(self):
        subs_without_npi = [
            e for e in RCE_PROFILE_ENTITIES
            if rce_fields.sequoia_org_type(e) == "Subparticipant"
            and not rce_fields.rce_npi(e)
            and not rce_fields.is_test_record(e)
        ]
        assert len(subs_without_npi) == 5

        assert len([e for e in ALL_MOCK_ENTITIES
                    if not rce_fields.is_active(e)]) == 3
        assert len([e for e in ALL_MOCK_ENTITIES
                    if rce_fields.has_mojibake(e)]) == 2
        assert len([e for e in ALL_MOCK_ENTITIES
                    if rce_fields.is_test_record(e)]) == 1

        states = {rce_fields.rce_value(e, "address_state")
                  for e in RCE_PROFILE_ENTITIES}
        assert {"HI", "TX", "NY"} <= states

    def test_identifiers_are_synthetic_and_cannot_collide_with_real_ones(self):
        """The synthetic TEFCAID is deliberately not valid RFC-4122 hex, so no
        real TEFCAID can ever equal one. That impossibility is the safeguard —
        not a naming convention someone could quietly break."""
        for ent in ALL_MOCK_ENTITIES:
            tefcaid = rce_fields.tefca_id(ent)
            assert tefcaid.startswith("urn:uuid:00000000-test-")
            assert "-mock-" in tefcaid
            hcid = rce_fields.hcid(ent)
            if hcid:
                assert ".9999." in hcid, "synthetic OIDs live on the reserved arc"
            contact = rce_fields.contact(ent)
            if contact["contact_email"]:
                assert contact["contact_email"].endswith("@example.com")

    def test_hierarchy_qhin_participant_subparticipant_is_preserved(self):
        resolve = population_resolver()
        for ent in ALL_MOCK_ENTITIES:
            if rce_fields.sequoia_org_type(ent) != "Subparticipant":
                continue
            parent_ref = rce_fields.part_of(ent)
            assert parent_ref, f"{ent['id']} is a Subparticipant with no partOf"
            parent = resolve(parent_ref)
            assert parent is not None, f"{ent['id']} partOf does not resolve"
            assert rce_fields.sequoia_org_type(parent) == "Participant", (
                f"{ent['id']} partOf points at a "
                f"{rce_fields.sequoia_org_type(parent)}, not a Participant")

    def test_participants_link_to_a_qhin_through_org_managing_org(self):
        for ent in ALL_MOCK_ENTITIES:
            if rce_fields.sequoia_org_type(ent) != "Participant":
                continue
            assert rce_fields.org_managing_org(ent), ent["id"]
            # A Participant's parent is its QHIN, and that edge is NOT partOf.
            assert not rce_fields.part_of(ent), ent["id"]


# ═══ STEP 2 — D5 TEFCA alignment ═════════════════════════════════════════════

class TestD5TefcaAlignment:
    def test_d5_evaluates_enriched_mock_tefcaid(self):
        facets = d5_facets("rce-org-b1-001")
        assert facets["tefca_identifier"]["disposition"] == Disposition.PASS.value
        assert facets["tefca_identifier"]["value"] == rce_fields.tefca_id(
            entity("rce-org-b1-001"))

    def test_d5_evaluates_enriched_mock_hcid(self):
        assert d5_facets("rce-org-b1-001")["hcid"]["disposition"] == \
            Disposition.PASS.value

    def test_d5_missing_hcid_is_not_found_never_a_failure(self):
        """The delivery omits HCID on some records. That is a property of the
        feed, not a question about the entity — so NOT_FOUND, and it must not
        drag the dimension into REVIEW on its own."""
        facets = d5_facets("rce-org-b2-006")
        assert facets["hcid"]["disposition"] == Disposition.NOT_FOUND.value
        d5 = dimensions_for("rce-org-b2-006")[Dimension.D5_TEFCA_ALIGNMENT.value]
        assert d5.disposition != Disposition.FAIL.value
        assert d5.disposition == Disposition.PASS.value

    def test_d5_exchange_purpose_from_mock(self):
        facets = d5_facets("rce-org-b1-001")
        assert facets["exchange_purpose"]["disposition"] == Disposition.PASS.value
        assert rce_fields.PURPOSE_TREATMENT in facets["exchange_purpose"]["value"]

    def test_d5_absent_exchange_purpose_is_not_applicable_not_review(self):
        facets = d5_facets("rce-org-b2-003")
        assert facets["exchange_purpose"]["disposition"] == \
            Disposition.NOT_APPLICABLE.value

    def test_d5_evaluates_sequoiaorgtype(self):
        assert d5_facets("rce-org-b1-001")["sequoia_org_type"]["disposition"] == \
            Disposition.PASS.value
        assert d5_facets("rce-org-b1-001")["sequoia_org_type"]["value"] == "Participant"

    def test_d5_partof_resolved_is_pass(self):
        assert d5_facets("rce-org-b1-003")["parent_relationship"]["disposition"] == \
            Disposition.PASS.value

    def test_d5_unresolvable_partof_is_review_never_fail(self):
        from app.Tefca.evidence_assembly import assemble_dimensions as assemble

        ent = dict(entity("rce-org-b1-003"))
        ent["_rce"] = dict(ent["_rce"], partOf="urn:uuid:00000000-test-9999-mock-x")
        dims = {d.dimension: d for d in assemble(
            ent, build_profile(ent), {}, None, parent_resolver=population_resolver())}
        d5 = dims[Dimension.D5_TEFCA_ALIGNMENT.value]
        facets = d5.items[0].normalized_values["facets"]
        assert facets["parent_relationship"]["disposition"] == Disposition.REVIEW.value
        assert d5.disposition == Disposition.REVIEW.value
        assert d5.disposition != Disposition.FAIL.value

    def test_inactive_entity_flagged(self):
        facets = d5_facets("rce-org-rp-009")
        assert facets["active_status"]["disposition"] == Disposition.REVIEW.value
        assert facets["active_status"]["value"] == "0"
        d5 = dimensions_for("rce-org-rp-009")[Dimension.D5_TEFCA_ALIGNMENT.value]
        assert d5.requires_analyst is True
        assert "INACTIVE_RECORD" in rce_fields.quality_flags(entity("rce-org-rp-009"))

    def test_d5_no_longer_claims_hcid_and_purpose_are_never_supplied(self):
        d5 = dimensions_for("rce-org-b1-001")[Dimension.D5_TEFCA_ALIGNMENT.value]
        not_supplied = d5.items[0].normalized_values["fields_not_supplied_by_rce"]
        assert "hcid" not in not_supplied
        assert "exchange_purpose" not in not_supplied

    def test_d5_never_reaches_fail_automatically(self):
        for ent in ALL_MOCK_ENTITIES:
            dims = dimensions_for(ent["id"])
            assert dims[Dimension.D5_TEFCA_ALIGNMENT.value].disposition != \
                Disposition.FAIL.value, ent["id"]

    def test_encoding_defect_is_reported_and_never_auto_corrected(self):
        ent = entity("rce-org-rp-004")
        assert rce_fields.has_mojibake(ent)
        assert "â€˜" in ent["_rce"]["name"], "the corrupt bytes must be preserved"
        d5 = dimensions_for("rce-org-rp-004")[Dimension.D5_TEFCA_ALIGNMENT.value]
        flags = d5.items[0].normalized_values["data_quality_flags"]
        assert "MOJIBAKE_DETECTED" in flags
        # A defective record is not a non-aligned one.
        assert d5.disposition == Disposition.PASS.value

    def test_test_record_is_flagged_but_preserved(self):
        ent = entity("rce-org-rp-011")
        assert rce_fields.is_test_record(ent)
        assert ent in ALL_MOCK_ENTITIES, "a flagged test record is never dropped"
        assert "TEST_RECORD_SUSPECTED" in rce_fields.quality_flags(ent)


# ═══ STEP 3 — D6 relationship ════════════════════════════════════════════════

class TestD6Relationship:
    def test_d6_partof_from_enriched_mock(self):
        d6 = dimensions_for("rce-org-b1-003")[
            Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value]
        rce_item = next(i for i in d6.items if i.source == "ONC_RCE_DIRECTORY")
        assert rce_item.original_values["rce_part_of"] == \
            rce_fields.part_of(entity("rce-org-b1-003"))
        assert rce_item.normalized_values["parent_resolution"] == "RESOLVED"
        assert rce_item.disposition == Disposition.PASS.value

    def test_d6_keeps_partof_and_orgmanagingorg_as_separate_edges(self):
        """Collapsing them would make 'has a parent' true for every entity and
        would put a QHIN where the hierarchy expects a Participant."""
        d6 = dimensions_for("rce-org-b1-003")[
            Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value]
        values = next(i for i in d6.items
                      if i.source == "ONC_RCE_DIRECTORY").original_values
        assert values["rce_part_of"] != values["org_managing_org"]
        semantics = next(i for i in d6.items
                         if i.source == "ONC_RCE_DIRECTORY"
                         ).normalized_values["edge_semantics"]
        assert "Participant" in semantics["partOf"]
        assert "QHIN" in semantics["orgManagingOrg"]

    def test_participant_has_no_partof_and_that_is_not_a_finding(self):
        d6 = dimensions_for("rce-org-b1-001")[
            Dimension.D6_PROVIDER_ORG_RELATIONSHIP.value]
        assert d6.disposition in (Disposition.NOT_FOUND.value,
                                  Disposition.NOT_APPLICABLE.value)
        assert d6.disposition != Disposition.FAIL.value


# ═══ STEP 4 — applicability ══════════════════════════════════════════════════

class TestApplicability:
    def test_applicability_reads_sequoiaorgtype(self):
        assert tefca_class_of(entity("rce-org-b1-001")) == "PARTICIPANT"
        assert tefca_class_of(entity("rce-org-b1-003")) == "SUBPARTICIPANT"
        profile = build_profile(entity("rce-org-b1-003"))
        assert profile.sequoia_org_type == "Subparticipant"

    def test_sequoiaorgtype_overrides_a_stale_fhir_type_coding(self):
        """sequoiaorgtype is the RCE's own statement of what the entity IS, so it
        must win over the pre-delivery FHIR coding rather than the other way
        round."""
        ent = dict(entity("rce-org-b1-001"))
        ent["_rce"] = dict(ent["_rce"], sequoiaorgtype="Subparticipant")
        assert ent["type"][0]["coding"][0]["code"] == "PARTICIPANT"
        assert tefca_class_of(ent) == "SUBPARTICIPANT"

    def test_organization_node_type_not_hierarchy(self):
        """organizationNodeType is technical exchange behaviour. It must have NO
        influence on the TEFCA class — a Participant may operate no node and a
        Subparticipant may be an initiator."""
        participant_no_node = entity("rce-org-rp-003")
        assert node_type_of(participant_no_node) == "no node"
        assert tefca_class_of(participant_no_node) == "PARTICIPANT"

        sub_initiator = entity("rce-org-rp-004")
        assert node_type_of(sub_initiator) == "initiator"
        assert tefca_class_of(sub_initiator) == "SUBPARTICIPANT"

        # Changing ONLY the node type must not move the class or any dimension.
        before = build_profile(participant_no_node)
        mutated = dict(participant_no_node)
        mutated["_rce"] = dict(mutated["_rce"], organizationNodeType="initiator")
        after = build_profile(mutated)
        assert before.tefca_class == after.tefca_class
        assert before.dimensions == after.dimensions

    def test_node_type_is_carried_for_audit_but_marked_as_not_hierarchy(self):
        profile = build_profile(entity("rce-org-rp-003"))
        payload = profile.to_dict()
        assert payload["organization_node_type"] == "no node"
        assert "NOT the TEFCA hierarchy" in payload["organization_node_type_note"]


# ═══ STEP 5 — entities without an NPI ════════════════════════════════════════

class TestEntityWithoutNPI:
    def test_entity_without_npi_d2_not_applicable(self):
        for entity_id in ("rce-org-rp-006", "rce-org-rp-007", "rce-org-rp-008",
                          "rce-org-rp-009", "rce-org-rp-010"):
            profile = build_profile(entity(entity_id))
            assert profile.npi_available is None, entity_id
            assert profile.dimensions[Dimension.D2_MEDICARE_ENROLLMENT.value] == \
                "NOT_APPLICABLE", entity_id

    def test_d2_not_applicable_states_it_is_about_identifiers_not_the_entity(self):
        profile = build_profile(entity("rce-org-rp-009"))
        why = profile.rationale[Dimension.D2_MEDICARE_ENROLLMENT.value]
        assert "never a finding against the entity" in why

    def test_entity_without_npi_d3_per_source_applicability(self):
        """D3 must NOT go NOT_APPLICABLE just because the NPI is absent.
        Exclusion and debarment attach to the organisation."""
        dims = dimensions_for("rce-org-rp-006")
        d3 = dims[Dimension.D3_EXCLUSION_REVOCATION.value]
        assert d3.disposition != Disposition.NOT_APPLICABLE.value
        assert d3.applicability == "REQUIRED"
        # Each of the three controls is still separately identifiable.
        assert {"OIG_LEIE", "SAM_GOV", "CMS_REVOCATION"} <= {i.source for i in d3.items}

    def test_d3_attempts_org_level_check_without_npi(self):
        from app.Tefca.evidence_assembly import _dimension_exclusion

        class _Result:
            def __init__(self, data):
                self.success, self.data = True, data
                self.query_timestamp, self.api_version, self.error = "t", "v", None

        ent = entity("rce-org-rp-006")
        profile = build_profile(ent)
        d3 = _dimension_exclusion(ent, profile, {
            "leie_org": _Result({"excluded": False, "exclusion_found": False}),
            "sam_name": _Result({"debarred": False, "exclusions": []}),
            "cms_revocation": _Result({"matches": [], "result": "NONE"}),
        })
        leie = next(i for i in d3.items if i.source == "OIG_LEIE")
        assert leie.rule_applied == "OIG_LEIE_ORG_LEVEL_CHECK_NO_NPI"
        assert leie.query_identifier.startswith("org=")
        # NOT_FOUND, not PASS: a name search is weaker than an identifier match
        # and must not read as an equivalent clearance.
        assert leie.disposition == Disposition.NOT_FOUND.value
        sam = next(i for i in d3.items if i.source == "SAM_GOV")
        assert sam.disposition == Disposition.NOT_FOUND.value

    def test_d3_with_no_usable_identifier_is_insufficient_never_clean(self):
        """The bug this pins: `lookup_by_npi("")` used to return a clean
        'no exclusion found' for an entity nothing had been searched for."""
        from app.Tefca.evidence_assembly import _dimension_exclusion

        ent = dict(entity("rce-org-rp-006"))
        ent["name"] = ""
        ent["_rce"] = dict(ent["_rce"], name="", NPI="")
        d3 = _dimension_exclusion(ent, build_profile(ent), {})
        leie = next(i for i in d3.items if i.source == "OIG_LEIE")
        assert leie.disposition == Disposition.INSUFFICIENT_EVIDENCE.value
        assert d3.disposition != Disposition.PASS.value

    def test_entity_with_npi_populates_all_six_dimensions(self):
        dims = dimensions_for("rce-org-b1-001")
        assert len(dims) == 6
        for dimension in Dimension:
            assert dimension.value in dims


# ═══ TRACK 1B — DB-backed entity resolver ════════════════════════════════════

class _FakeIdentifier:
    def __init__(self, entity_id, itype, value, system=None):
        self.entity_id, self.identifier_type = entity_id, itype
        self.identifier_value, self.system_uri = value, system


class _FakeRegistryEntity:
    def __init__(self, **kw):
        self.id = kw.get("id", "11111111-1111-4111-8111-111111111111")
        self.name = kw.get("name", "Registry Test Organization")
        self.entity_level = kw.get("entity_level", "participant")
        self.entity_type = kw.get("entity_type", "provider")
        self.is_active = kw.get("is_active", True)
        self.is_deleted = kw.get("is_deleted", False)
        self.state, self.city = kw.get("state", "MD"), kw.get("city", "Baltimore")
        self.zip, self.address = kw.get("zip", "21201"), kw.get("address", "1 Main St")
        self.exchange_purposes = kw.get("exchange_purposes",
                                        {"purposes": ["T-TRTMNT"]})


class TestEntityResolverShape:
    def test_registry_row_projects_into_the_evidence_entity_shape(self):
        from app.Tefca.entity_resolution import registry_entity_to_evidence_shape

        row = _FakeRegistryEntity()
        shaped = registry_entity_to_evidence_shape(row, [
            _FakeIdentifier(row.id, "tefcaid", "urn:uuid:abc"),
            _FakeIdentifier(row.id, "hcid", "urn:oid:1.2.3"),
            _FakeIdentifier(row.id, "npi", "1003000126"),
        ], parent_tefcaid="urn:uuid:parent")

        assert rce_fields.tefca_id(shaped) == "urn:uuid:abc"
        assert rce_fields.hcid(shaped) == "urn:oid:1.2.3"
        assert rce_fields.rce_npi(shaped) == "1003000126"
        assert rce_fields.part_of(shaped) == "urn:uuid:parent"
        assert rce_fields.sequoia_org_type(shaped) == "Participant"
        assert rce_fields.purposes_of_use(shaped) == ["T-TRTMNT"]
        assert set(rce_fields.rce_block(shaped)) == set(rce_fields.RCE_FIELDS)
        assert shaped["_resolution_source"] == "db"

    def test_shaped_registry_entity_runs_through_all_six_dimensions(self):
        """The point of the projection: a registry entity must need no special
        case anywhere downstream."""
        from app.Tefca.entity_resolution import registry_entity_to_evidence_shape

        row = _FakeRegistryEntity()
        shaped = registry_entity_to_evidence_shape(
            row, [_FakeIdentifier(row.id, "tefcaid", "urn:uuid:abc")])
        dims = assemble_dimensions(shaped, build_profile(shaped), {}, None)
        assert len(dims) == 6
        assert all(d.disposition != Disposition.FAIL.value for d in dims)


class TestFeatureFlag:
    @pytest.fixture(autouse=True)
    def _restore_env(self):
        original = os.environ.get("ENTITY_RESOLVER_SOURCE")
        yield
        if original is None:
            os.environ.pop("ENTITY_RESOLVER_SOURCE", None)
        else:
            os.environ["ENTITY_RESOLVER_SOURCE"] = original

    def test_default_is_mock_so_existing_behaviour_is_unchanged(self):
        from app.Tefca.entity_resolution import DEFAULT_SOURCE, resolver_source

        os.environ.pop("ENTITY_RESOLVER_SOURCE", None)
        assert resolver_source() == "mock"
        assert DEFAULT_SOURCE == "mock"

    def test_feature_flag_controls_resolution_source(self):
        from app.Tefca.entity_resolution import resolver_source

        for value in ("db", "mock", "db_then_mock"):
            os.environ["ENTITY_RESOLVER_SOURCE"] = value
            assert resolver_source() == value

    def test_unrecognised_flag_falls_back_to_default_with_a_warning(self, caplog):
        from app.Tefca.entity_resolution import resolver_source

        os.environ["ENTITY_RESOLVER_SOURCE"] = "postgres-ish"
        with caplog.at_level(logging.WARNING):
            assert resolver_source() == "mock"
        assert any("postgres-ish" in r.getMessage() for r in caplog.records), \
            "a typo in the flag must fall back safely but never silently"

    @pytest.mark.asyncio
    async def test_db_resolver_finds_entity_by_tefcaid(self):
        from app.Tefca.entity_resolution import resolve_entity

        os.environ["ENTITY_RESOLVER_SOURCE"] = "mock"
        target = entity("rce-org-b1-001")
        found = await resolve_entity(None, rce_fields.tefca_id(target))
        assert found is not None and found["id"] == "rce-org-b1-001"

    @pytest.mark.asyncio
    async def test_db_resolver_finds_entity_by_hcid(self):
        from app.Tefca.entity_resolution import resolve_entity

        os.environ["ENTITY_RESOLVER_SOURCE"] = "mock"
        target = entity("rce-org-b1-002")
        found = await resolve_entity(None, rce_fields.hcid(target))
        assert found is not None and found["id"] == "rce-org-b1-002"

    @pytest.mark.asyncio
    async def test_db_resolver_finds_entity_by_npi(self):
        from app.Tefca.entity_resolution import resolve_entity

        os.environ["ENTITY_RESOLVER_SOURCE"] = "mock"
        found = await resolve_entity(None, "1003000126")
        assert found is not None and found["id"] == "rce-org-b1-001"

    @pytest.mark.asyncio
    async def test_db_resolver_returns_none_when_not_found(self):
        from app.Tefca.entity_resolution import resolve_entity

        os.environ["ENTITY_RESOLVER_SOURCE"] = "db"
        assert await resolve_entity(None, "no-such-identifier") is None

    @pytest.mark.asyncio
    async def test_db_source_does_not_silently_fall_back_to_fixtures(self):
        """"db" means the database. If it misses, the answer is None — not a
        fixture wearing the database's clothes."""
        from app.Tefca.entity_resolution import resolve_entity

        os.environ["ENTITY_RESOLVER_SOURCE"] = "db"
        assert await resolve_entity(None, "1003000126") is None

    @pytest.mark.asyncio
    async def test_mock_fallback_logs_warning(self, caplog):
        from app.Tefca.entity_resolution import resolve_entity

        os.environ["ENTITY_RESOLVER_SOURCE"] = "db_then_mock"
        with caplog.at_level(logging.WARNING):
            found = await resolve_entity(None, "1003000126")
        assert found is not None
        assert found["_resolution_source"] == "mock"
        assert any("FELL BACK TO BUNDLED FIXTURE" in r.getMessage()
                   for r in caplog.records), \
            "a silent fallback would let fixtures pass as real data"

    @pytest.mark.asyncio
    async def test_resolution_never_mutates_the_shared_fixture(self):
        from app.Tefca.entity_resolution import resolve_entity

        os.environ["ENTITY_RESOLVER_SOURCE"] = "mock"
        await resolve_entity(None, "1003000126")
        assert "_resolution_source" not in entity("rce-org-b1-001")

    @pytest.mark.asyncio
    async def test_existing_mock_tests_still_pass_under_default_flag(self):
        """The whole point of defaulting to "mock": every pre-existing caller of
        the fixture path resolves exactly what it always did."""
        from app.Tefca.entity_resolution import resolve_entity

        os.environ.pop("ENTITY_RESOLVER_SOURCE", None)
        for entity_id in ("rce-org-b1-001", "rce-org-b4-005", "rce-org-rp-011"):
            found = await resolve_entity(None, entity_id)
            assert found is not None and found["id"] == entity_id


class TestOrgLevelLookupWiring:
    """The org-level D3 fallback must actually invoke the connectors.

    Written after the LEIE call was found to be passing the wrong arguments:
    `_safe_lookup` caught the TypeError, returned None, and D3 reported
    INSUFFICIENT_EVIDENCE for every NPI-less entity. The dimension logic was
    right, the wiring was not, and nothing failed — the check looked connected
    and screened nothing. These tests exercise the real call signatures.
    """

    @pytest.mark.asyncio
    async def test_leie_org_lookup_signature_is_correct(self):
        from app.Tefca.connectors import OIGLEIEConnector
        from app.Tefca.evidence_service import EvidenceService

        result = await EvidenceService._safe_lookup(
            OIGLEIEConnector().lookup_by_name, last="", first="", org="Test Org")
        assert result is not None, \
            "the LEIE organisation lookup did not run — check the call signature"

    @pytest.mark.asyncio
    async def test_sam_org_lookup_signature_is_correct(self):
        from app.Tefca.connectors import SAMGovConnector
        from app.Tefca.evidence_service import EvidenceService

        result = await EvidenceService._safe_lookup(
            SAMGovConnector().lookup_by_name, "Test Org")
        assert result is not None, \
            "the SAM.gov organisation lookup did not run"

    @pytest.mark.asyncio
    async def test_gather_sources_adds_org_keys_for_an_npi_less_entity(self):
        """The keys D3 reads must be present for an entity with no NPI."""
        from app.Tefca.evidence_service import EvidenceService

        service = EvidenceService()
        captured = {}

        async def fake_leie(last="", first="", org=""):
            captured["leie_org"] = org
            return _StubResult({"excluded": False, "exclusion_found": False})

        async def fake_sam(legal_name):
            captured["sam_name"] = legal_name
            return _StubResult({"debarred": False, "exclusions": []})

        async def nothing(*a, **k):
            return _StubResult({})

        service.manager.leie.lookup_by_name = fake_leie
        service.manager.sam.lookup_by_name = fake_sam
        service.manager.query_all_sources = lambda e: _async({})
        service.ppef.lookup_by_npi = nothing
        service.revocation.lookup_by_npi = nothing

        sources = await service.gather_sources(entity("rce-org-rp-006"))
        assert "leie_org" in sources and "sam_name" in sources
        assert captured["leie_org"] == entity("rce-org-rp-006")["name"]
        assert captured["sam_name"] == entity("rce-org-rp-006")["name"]

    @pytest.mark.asyncio
    async def test_org_keys_absent_when_an_npi_exists(self):
        """An entity WITH an NPI is screened by identifier; the weaker name
        search is not run and must not appear as though it had been."""
        from app.Tefca.evidence_service import EvidenceService

        service = EvidenceService()

        async def nothing(*a, **k):
            return _StubResult({})

        service.manager.query_all_sources = lambda e: _async({})
        service.ppef.lookup_by_npi = nothing
        service.revocation.lookup_by_npi = nothing

        sources = await service.gather_sources(entity("rce-org-b1-001"))
        assert "leie_org" not in sources
        assert "sam_name" not in sources


class _StubResult:
    def __init__(self, data):
        self.success, self.data, self.error = True, data, None
        self.query_timestamp, self.api_version = "t", "v"


async def _async(value):
    return value
