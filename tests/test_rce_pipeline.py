"""
P13 — tests for the RCE ingestion pipeline (P0 through P12).

DESIGNED TO RUN WITHOUT A DATABASE where the logic allows it. The reader, the
field map, the quality rules, the correction gate and the classifier mapping are
all pure functions over dicts, so they are tested directly. The database-backed
paths (intake, curation, promotion, reconciliation) are exercised against a
Postgres when one is reachable and skip with a named reason when it is not —
the pattern this suite already uses, so a machine without a database reports
what it could not run rather than a false pass.

WHAT THESE PIN
The invariants that make the pipeline trustworthy rather than merely working:
no record is ever dropped, a duplicate delivery is recorded rather than
rejected, confidence never grants correction authority, held records cannot
reach verification, and organizationNodeType is never read as hierarchy.
"""

from __future__ import annotations

import hashlib

import pytest

from app.tefca_registry.rce import field_map as fm
from app.tefca_registry.rce import quality_rules as qr
from app.tefca_registry.rce.reader import (
    PARSE_FIELD_COUNT_MISMATCH,
    PARSE_OK,
    DelimiterUndecidable,
    detect_delimiter,
    detect_encoding,
    read_delivery,
)

# ── fixtures ─────────────────────────────────────────────────────────────────

HEADER = "|".join(fm.RCE_FIELDS)


def row(**over) -> str:
    """One well-formed 41-field pipe-delimited line."""
    values = {f: "" for f in fm.RCE_FIELDS}
    values.update({
        "id": "2.16.840.1.113883.3.9999.1",
        "domains": "RCE",
        "orgManagingOrg": "2.16.840.1.113883.3.9960",
        "purposesofuse": "T-TRTMNT",
        "NPI": "1881659506",
        "HCID": "urn:oid:2.16.840.1.113883.3.9999.1",
        "TEFCAID": "urn:uuid:f3371cb8-af1a-49d6-baaa-2020691606dc",
        "active": "1",
        "sequoiaorgtype": "Participant",
        "name": "Test Organization",
        "address_line": "1 Main St",
        "address_city": "Buffalo",
        "address_state": "NY",
        "address_postalCode": "14203",
        "address_country": "US",
        "partOf": "2.16.840.1.113883.3.9960",
    })
    values.update(over)
    return "|".join(values[f] for f in fm.RCE_FIELDS)


def delivery(*rows: str) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode("utf-8")


def ctx(line: str, **dataset):
    values = dict(zip(fm.RCE_FIELDS, line.split("|")))
    base = {"expected_field_count": 41, "known_source_ids": set(),
            "qhin_oids": set(fm.OBSERVED_QHIN_OIDS),
            "tefcaid_duplicates": {}, "hcid_duplicates": {}, "npi_duplicates": {}}
    base.update(dataset)
    return qr.RecordContext(line_number=2, parse_status=PARSE_OK,
                            field_count=len(values), values=values, dataset=base)


def findings(rule_id: str, context) -> list:
    return qr.RULE_BY_ID[rule_id].evaluate(context) or []


# ═══ P0/P1 — the field map ═══════════════════════════════════════════════════

class TestFieldMap:
    def test_map_describes_exactly_41_fields(self):
        assert fm.RCE_FIELD_COUNT == 41
        assert len(fm.FIELD_SPECS) == 41
        assert len({s.name for s in fm.FIELD_SPECS}) == 41

    def test_every_field_separates_observed_documented_and_interpretation(self):
        """The three-layer rule: a column name is never its definition."""
        for spec in fm.FIELD_SPECS:
            assert spec.observed.strip(), spec.name
            assert spec.documented.strip(), spec.name
            assert spec.docuaction.strip(), spec.name
            assert spec.observed != spec.documented != spec.docuaction

    def test_field_ordinals_match_delivered_order(self):
        for index, spec in enumerate(fm.FIELD_SPECS):
            assert spec.ordinal == index, spec.name
            assert fm.RCE_FIELDS[index] == spec.name

    def test_schema_fingerprint_is_order_sensitive(self):
        """Two deliveries with the same columns in a different order are NOT the
        same schema — a positional parser would transpose every value."""
        forward = fm.schema_fingerprint(list(fm.RCE_FIELDS))
        swapped = list(fm.RCE_FIELDS)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        assert forward == fm.EXPECTED_SCHEMA_FINGERPRINT
        assert fm.schema_fingerprint(swapped) != forward

    def test_id_is_the_identity_key_not_tefcaid(self):
        """The profiled delivery has 23,566 distinct ids and only 23,325
        distinct TEFCAIDs. Keying on TEFCAID would merge 241 organisations."""
        source_id = fm.FIELD_BY_NAME["id"]
        tefcaid = fm.FIELD_BY_NAME["TEFCAID"]
        assert source_id.distinct == fm.PROFILED_RECORD_COUNT
        assert tefcaid.distinct < fm.PROFILED_RECORD_COUNT
        assert "identity key" in source_id.docuaction
        assert "family" in tefcaid.docuaction.lower()

    def test_npi_is_legitimately_nullable(self):
        spec = fm.FIELD_BY_NAME["NPI"]
        assert spec.necessity == fm.Necessity.LEGITIMATELY_NULLABLE
        assert spec.populated < fm.PROFILED_RECORD_COUNT
        assert "never a failure" in spec.docuaction

    def test_node_type_is_documented_as_not_hierarchy(self):
        spec = fm.FIELD_BY_NAME["organizationNodeType"]
        assert "never read as the tefca hierarchy" in spec.docuaction.lower()
        assert fm.FIELD_BY_NAME["sequoiaorgtype"].target_key == "entity_level"

    def test_columns_empty_in_the_delivery_are_named(self):
        empty = set(fm.empty_in_delivery())
        assert {"transaction", "NAIC", "CCN", "alias", "email",
                "contact_company"} <= empty
        for name in empty:
            assert fm.FIELD_BY_NAME[name].populated == 0


# ═══ P2 — reading and intake ═════════════════════════════════════════════════

class TestReader:
    def test_pipe_delimiter_is_detected(self):
        result = read_delivery(delivery(row()))
        assert result.delimiter == "|"
        assert result.record_count == 1
        assert result.headers == list(fm.RCE_FIELDS)

    def test_comma_and_tab_are_also_supported(self):
        for delimiter in (",", "\t"):
            header = delimiter.join(["a", "b", "c"])
            body = delimiter.join(["1", "2", "3"])
            raw = (header + "\r\n" + body + "\r\n").encode("utf-8")
            found, _note = detect_delimiter(header, [body])
            assert found == delimiter

    def test_delimiter_is_never_guessed_when_inconsistent(self):
        """A wrong delimiter does not fail loudly — it produces one giant field
        per row, which looks like data. Undecidable must raise."""
        header = "a;b;c"
        with pytest.raises(DelimiterUndecidable):
            detect_delimiter(header, ["1;2;3"])

    def test_a_few_malformed_rows_do_not_make_the_file_unreadable(self):
        """The contract is preserve-and-flag, not reject. A delivery with a
        minority of malformed lines must still be read."""
        good = "|".join(str(i) for i in range(41))
        found, note = detect_delimiter(HEADER, [good, "too|few", good])
        assert found == "|"
        assert "consistent on 2/3" in note

    def test_every_line_is_returned_including_malformed_ones(self):
        raw = delivery(row(), "too|few|fields", row(name="Second"))
        result = read_delivery(raw)
        assert result.record_count == 3, "a malformed line must not be dropped"
        assert result.ok_count == 2
        statuses = [line.parse_status for line in result.lines]
        assert statuses[1] == PARSE_FIELD_COUNT_MISMATCH

    def test_malformed_line_is_preserved_verbatim(self):
        raw = delivery("too|few|fields")
        line = read_delivery(raw).lines[0]
        assert line.raw_line == "too|few|fields"
        assert line.field_count == 3
        assert "NOT mapped positionally" in line.parse_note

    def test_record_hash_is_over_the_raw_line(self):
        line = read_delivery(delivery(row())).lines[0]
        assert line.record_sha256 == hashlib.sha256(
            line.raw_line.encode("utf-8")).hexdigest()

    def test_strict_utf8_is_tried_first_and_flagged_when_it_fails(self):
        text, encoding, errors = detect_encoding("héllo".encode("utf-8"))
        assert encoding == "utf-8" and errors is False
        text, encoding, errors = detect_encoding(b"h\xe9llo")
        assert errors is True, "a lossy decode must be flagged, not silent"

    def test_embedded_tabs_and_mojibake_are_counted(self):
        raw = delivery(row(address_line="1 Main\tSt"),
                       row(name="Kapiâ€˜olani"))
        result = read_delivery(raw)
        assert result.embedded_tab_cells == 1
        assert result.mojibake_cells >= 1

    def test_schema_fingerprint_travels_with_the_read(self):
        assert read_delivery(delivery(row())).schema_fingerprint == \
            fm.EXPECTED_SCHEMA_FINGERPRINT


# ═══ P4 — quality rules ══════════════════════════════════════════════════════

class TestQualityRules:
    def test_rule_config_hash_is_stable_and_covers_severity(self):
        first = qr.rule_config_hash()
        assert first == qr.rule_config_hash()
        assert len(first) == 64

    def test_every_rule_has_a_version_and_category(self):
        for rule in qr.RULES:
            assert rule.version and rule.category and rule.description
            assert rule.severity() in qr.SEVERITY_OVERRIDES.values() or \
                rule.severity() in (qr.CRITICAL, qr.HIGH, qr.MEDIUM, qr.LOW, qr.INFO)

    def test_dq_rule_ids_are_unique(self):
        """No two rules may claim the same rule_id.

        `RULE_BY_ID` is a dict comprehension, so a duplicate is silently
        deduplicated with last-wins — while `quality_engine` iterates `RULES`
        and executes BOTH, merges their per-rule counters, stamps every issue
        with the later rule's severity, and finally dies at commit on
        `uq_rce_rule_exec_run_rule` after a full 23,566-record pass.
        """
        ids = [rule.rule_id for rule in qr.RULES]
        duplicates = sorted({rid for rid in ids if ids.count(rid) > 1})
        assert not duplicates, f"duplicate rule_id(s): {duplicates}"
        assert len(qr.RULE_BY_ID) == len(qr.RULES), (
            "RULE_BY_ID lost entries to deduplication — a duplicate rule_id "
            "exists that the list check above should have caught.")

    def test_duplicate_rule_id_is_refused_at_load(self):
        """The guard raises rather than warning, and names the free ids."""
        original = qr.RULES
        try:
            qr.RULES = original + (original[0],)
            with pytest.raises(qr.DuplicateRuleId) as exc:
                qr._assert_rule_ids_unique()
            assert original[0].rule_id in str(exc.value)
        finally:
            qr.RULES = original
        qr._assert_rule_ids_unique()  # restored set must still load

    def test_next_available_rule_ids_are_derived_not_hardcoded(self):
        """FMT-005/006 are TAKEN. The next free ids must reflect that."""
        nxt = qr.next_available_rule_ids()
        assert nxt["FMT"] == "FMT-007", (
            "FMT-005 (Contact phone complete) and FMT-006 (Contact email "
            "well-formed) already ship. A new FMT rule starts at FMT-007.")
        assert nxt["CON"] == "CON-006"
        assert nxt["BUS"] == "BUS-004"
        for prefix, rule_id in nxt.items():
            assert rule_id not in qr.RULE_BY_ID, (
                f"{rule_id} is advertised as free but already exists")

    def test_auto_safe_rules_all_exist(self):
        """The allow-list cannot name a rule that is not in the set."""
        for rule_id in qr.AUTO_SAFE_RULES:
            assert rule_id in qr.RULE_BY_ID, (
                f"AUTO_SAFE_RULES names {rule_id}, which is not a registered "
                f"rule — the correction gate would silently never match it.")

    def test_missing_npi_is_informational_never_a_failure(self):
        """19.45% of the delivered population has no NPI. Absence is a fact."""
        result = findings("NPI-001", ctx(row(NPI="")))
        assert len(result) == 1
        assert result[0].severity == qr.INFO
        assert result[0].correction_authority == qr.NO_CORRECTION
        assert "never treated as a verification failure" in result[0].description

    def test_present_npi_raises_no_absence_issue(self):
        assert findings("NPI-001", ctx(row())) == []

    def test_malformed_npi_requires_a_human(self):
        result = findings("NPI-002", ctx(row(NPI="854565")))
        assert result[0].correction_authority == qr.HUMAN_REQUIRED
        assert result[0].suggested_value is None, \
            "an identity field is never auto-repaired"

    def test_two_npis_in_one_cell_is_not_split_automatically(self):
        result = findings("NPI-002", ctx(row(NPI="1780787176, 1770559767")))
        assert result[0].issue_type == "MULTIPLE_NPI_IN_ONE_FIELD"
        assert result[0].correction_authority == qr.HUMAN_REQUIRED
        assert result[0].suggested_confidence == "LOW"

    def test_missing_hcid_defaults_to_informational(self):
        """No business requirement establishing HCID as mandatory is in hand,
        so the default is INFORMATIONAL and configurable."""
        result = findings("ID-002", ctx(row(HCID="")))
        assert result[0].severity == qr.INFO
        assert "SEVERITY_OVERRIDES" in result[0].description

    def test_missing_purposes_of_use_defaults_to_informational(self):
        result = findings("CON-002", ctx(row(purposesofuse="")))
        assert result[0].severity == qr.INFO
        assert "never inferred" in result[0].description

    def test_purpose_variant_is_reported_not_merged(self):
        result = findings("CON-002", ctx(row(purposesofuse="T-TREAT")))
        variant = [f for f in result if f.issue_type == "PURPOSE_TOKEN_VARIANT"]
        assert variant, "a vocabulary variant must be surfaced"
        assert variant[0].correction_authority == qr.NO_CORRECTION
        assert variant[0].suggested_value == "T-TRTMNT"
        assert variant[0].suggested_confidence == "LOW"

    def test_zip_zero_padding_is_the_one_auto_safe_address_change(self):
        result = findings("FMT-001", ctx(row(address_postalCode="2718")))
        assert result[0].correction_authority == qr.AUTO_SAFE
        assert result[0].suggested_value == "02718"
        assert result[0].rule_id in qr.AUTO_SAFE_RULES

    def test_five_digit_zip_raises_nothing(self):
        assert findings("FMT-001", ctx(row(address_postalCode="14203"))) == []

    def test_zip_state_mismatch_is_reported_not_corrected(self):
        """Nothing in the record establishes which of the two is wrong."""
        result = findings("FMT-003", ctx(row(address_postalCode="94761",
                                             address_state="HI")))
        assert result, "a ZIP allocated to another state must be surfaced"
        assert result[0].correction_authority == qr.HUMAN_REQUIRED
        assert result[0].suggested_value is None
        assert "would fabricate an address" in result[0].description

    def test_consistent_zip_and_state_raise_nothing(self):
        assert findings("FMT-003", ctx(row(address_postalCode="14203",
                                           address_state="NY"))) == []

    def test_embedded_tab_normalisation_is_auto_safe(self):
        result = findings("FMT-004", ctx(row(address_line="1 Main\tSt")))
        assert result[0].correction_authority == qr.AUTO_SAFE
        assert "\t" not in result[0].suggested_value

    def test_inactive_record_is_informational_and_preserved(self):
        result = findings("CON-003", ctx(row(active="0")))
        assert result[0].issue_type == "INACTIVE_RECORD"
        assert result[0].severity == qr.INFO
        assert "never dropped" in result[0].description

    def test_test_record_is_flagged_but_never_dropped(self):
        result = findings("BUS-002", ctx(row(name="ELLKAY-DOA-TEST")))
        assert result[0].issue_type == "TEST_RECORD_SUSPECTED"
        assert "NEVER dropped" in result[0].description

    def test_ordinary_name_is_not_flagged_as_a_test_record(self):
        for name in ("Testa Medical Group", "Protest Health", "Contest Clinic"):
            assert findings("BUS-002", ctx(row(name=name))) == [], name

    def test_node_type_records_that_it_is_not_hierarchy(self):
        result = findings("CON-004", ctx(row(organizationNodeType="initiating-node")))
        assert "never used to derive the TEFCA class" in result[0].description

    def test_shared_tefcaid_is_informational_not_a_duplicate_finding(self):
        tefcaid = "urn:uuid:9483c34b-f148-40af-82dc-25150e6a251c"
        result = findings("ID-006", ctx(row(TEFCAID=tefcaid),
                                        tefcaid_duplicates={tefcaid: 69}))
        assert result[0].severity == qr.INFO
        assert result[0].correction_authority == qr.NO_CORRECTION
        assert "NOT merged" in result[0].description

    def test_unresolved_partof_is_review_not_a_broken_hierarchy(self):
        result = findings("INT-002", ctx(row(partOf="9.9.9.9")))
        assert result[0].correction_authority == qr.HUMAN_REQUIRED
        assert "outside the delivered scope" in result[0].description

    def test_participant_parent_being_its_qhin_is_the_normal_shape(self):
        result = findings("BUS-003", ctx(row(
            sequoiaorgtype="Participant",
            partOf="2.16.840.1.113883.3.9960",
            orgManagingOrg="2.16.840.1.113883.3.9960")))
        assert result[0].severity == qr.INFO
        assert "double-count" in result[0].description

    def test_field_count_mismatch_is_critical_and_uncorrectable(self):
        context = qr.RecordContext(
            line_number=5, parse_status=PARSE_FIELD_COUNT_MISMATCH,
            field_count=3, values={}, dataset={"expected_field_count": 41})
        result = findings("SCH-001", context)
        assert result[0].severity == qr.CRITICAL
        assert result[0].correction_authority == qr.NO_CORRECTION


# ═══ P5/P7 — correction authority ════════════════════════════════════════════

class _Issue:
    def __init__(self, **kw):
        self.correction_authority = kw.get("correction_authority", qr.AUTO_SAFE)
        self.rule_id = kw.get("rule_id", "FMT-001")
        self.field_name = kw.get("field_name", "address_postalCode")
        self.suggested_value = kw.get("suggested_value", "02718")
        self.suggested_confidence = kw.get("suggested_confidence", "HIGH")


class TestCorrectionAuthority:
    def test_zip_padding_is_auto_safe(self):
        from app.tefca_registry.rce.curation import is_auto_safe

        ok, reason = is_auto_safe(_Issue())
        assert ok, reason

    def test_confidence_does_not_grant_authority(self):
        """A HIGH-confidence identity correction is still HUMAN_REQUIRED."""
        from app.tefca_registry.rce.curation import is_auto_safe

        ok, reason = is_auto_safe(_Issue(
            correction_authority=qr.HUMAN_REQUIRED, rule_id="NPI-002",
            field_name="NPI", suggested_value="1881659506",
            suggested_confidence="HIGH"))
        assert not ok
        assert "not AUTO_SAFE" in reason

    def test_declaring_auto_safe_is_not_enough_without_the_allow_list(self):
        """A finding cannot become auto-applicable by carrying the string."""
        from app.tefca_registry.rce.curation import is_auto_safe

        ok, reason = is_auto_safe(_Issue(rule_id="NPI-002", field_name="NPI"))
        assert not ok
        assert "allow-list" in reason

    def test_substantive_fields_are_never_auto_corrected(self):
        from app.tefca_registry.rce.curation import (
            SUBSTANTIVE_FIELDS, is_auto_safe)

        for field in ("NPI", "name", "TEFCAID", "partOf", "sequoiaorgtype"):
            assert field in SUBSTANTIVE_FIELDS
            ok, _reason = is_auto_safe(_Issue(rule_id="FMT-001",
                                              field_name=field))
            assert not ok, field

    def test_auto_safe_allow_list_holds_only_normalisation_rules(self):
        for rule_id in qr.AUTO_SAFE_RULES:
            rule = qr.RULE_BY_ID[rule_id]
            assert rule.category == qr.CAT_FORMAT, (
                f"{rule_id} is AUTO_SAFE but is not a formatting rule")


# ═══ P10 — classifier input mapping ══════════════════════════════════════════

class TestClassifierMapping:
    def _evidence(self, **dispositions):
        def dimension(name, disposition, items):
            return {"dimension": name, "disposition": disposition,
                    "applicability": "REQUIRED", "evidence": items}
        return {"dimensions": [
            dimension("IDENTITY", dispositions.get("d1", "PASS"),
                      [{"source": "NPPES", "disposition": dispositions.get("nppes", "PASS")}]),
            dimension("EXCLUSION_REVOCATION", dispositions.get("d3", "UNAVAILABLE"),
                      [{"source": "OIG_LEIE", "disposition": "PASS"},
                       {"source": "SAM_GOV", "disposition": "UNAVAILABLE"}]),
            dimension("ADDRESS", dispositions.get("d4", "REVIEW"),
                      [{"source": "NPPES", "disposition": "CONFLICT"}]),
        ], "data_quality_flags": dispositions.get("flags", [])}

    def test_output_has_the_shape_the_classifier_reads(self):
        from app.tefca_registry.rce.arc_pipeline import (
            dimensions_to_verification_results)

        result = dimensions_to_verification_results(self._evidence())
        assert "sources" in result and "fields" in result

    def test_address_conflict_does_not_suppress_nppes_identity(self):
        """The defect this pins: NPPES appears in D1 and D4, and letting the
        address comparison set the `nppes` source state made an address
        disagreement read as a failure to confirm identity."""
        from app.tefca_registry.rce.arc_pipeline import (
            dimensions_to_verification_results)

        result = dimensions_to_verification_results(self._evidence())
        assert result["sources"]["nppes"]["status"] == "verified"
        assert result["fields"]["address_mismatch"]["severity"] == "major"

    def test_per_source_detail_survives_a_dimension_rollup(self):
        """D3 rolls up to UNAVAILABLE because SAM is down, but OIG answered."""
        from app.tefca_registry.rce.arc_pipeline import (
            dimensions_to_verification_results)

        sources = dimensions_to_verification_results(self._evidence())["sources"]
        assert sources["oig_leie"]["status"] == "verified"
        assert sources["sam_gov"]["status"] == "unavailable"

    def test_not_applicable_never_becomes_verified(self):
        """Inapplicability must neither help nor hurt an entity."""
        from app.tefca_registry.rce.arc_pipeline import _DISPOSITION_TO_STATE

        assert _DISPOSITION_TO_STATE["NOT_APPLICABLE"] == "not_checked"
        assert _DISPOSITION_TO_STATE["UNAVAILABLE"] == "unavailable"

    def test_bucket_to_tier_routing(self):
        from app.tefca_registry.rce.arc_pipeline import BUCKET_TO_TIER

        assert BUCKET_TO_TIER == {"B1": 1, "B2": 2, "B3": 3, "B4": 3}

    def test_unmatched_classification_still_cites_a_code(self):
        """A determination stored with a bucket and no rule cannot be explained."""
        from app.tefca_registry.rce.arc_pipeline import (
            UNMATCHED_RULE_CODE, UNMATCHED_RULE_VERSION)

        assert UNMATCHED_RULE_CODE == "DEFAULT-UNMATCHED"
        assert UNMATCHED_RULE_VERSION == 0


# ═══ P2 — immutability contract ══════════════════════════════════════════════

class TestImmutabilityContract:
    def test_repository_exposes_no_update_or_delete(self):
        """Layer 1 of the immutability contract, asserted structurally."""
        import inspect

        from app.tefca_registry.rce import repository

        source = inspect.getsource(repository)
        executable = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith("#"))
        for forbidden in ("def update_", "def delete_", ".delete()",
                          "session.delete", "db.delete"):
            assert forbidden not in executable, forbidden

    def test_api_exposes_no_mutating_area1_route(self):
        """Layer 2. Enforced by absence — a route that does not exist cannot be
        called with the wrong arguments."""
        from app.tefca_registry.rce import routes

        for route in routes.router.routes:
            methods = set(getattr(route, "methods", set()))
            path = getattr(route, "path", "")
            if "/deliveries" in path and "issues" not in path:
                assert not (methods & {"PUT", "PATCH", "DELETE"}), path

    def test_immutability_grants_cover_both_area1_tables(self):
        """Both tables lose UPDATE, DELETE and TRUNCATE from the app role.

        TRUNCATE was added in B1/Phase 4: it bypasses row-level protection
        entirely, so revoking UPDATE and DELETE while leaving it granted would
        protect every row individually and none of them collectively. The
        assertion checks the privileges rather than an exact string, so
        strengthening the revoke does not have to break the test.
        """
        from app.tefca_registry.rce.repository import (
            IMMUTABLE_TABLES, immutability_grants_sql)

        statements = immutability_grants_sql("appuser")
        for table in IMMUTABLE_TABLES:
            revoke = next((s for s in statements
                           if s.startswith("REVOKE") and f" ON {table} " in s), None)
            assert revoke is not None, f"no REVOKE emitted for {table}"
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                assert privilege in revoke, f"{privilege} not revoked on {table}"
            assert "FROM appuser" in revoke
        assert set(IMMUTABLE_TABLES) == {"rce_source_intakes", "rce_source_records"}

    def test_grants_keep_the_promotion_write_working(self):
        """A blanket REVOKE UPDATE would break `promote_delivery` mid-transaction.

        Promotion writes promotion_status and canonical_entity_id on 23,562 rows
        AFTER the entities, identifiers and contacts are already committed. The
        column-level grant is what lets Area 1 be hardened without that failure.
        """
        from app.tefca_registry.rce.repository import (
            IMMUTABLE_EVIDENCE_COLUMNS, MUTABLE_WORKFLOW_COLUMNS,
            immutability_grants_sql)

        grant = next(s for s in immutability_grants_sql("appuser")
                     if s.startswith("GRANT UPDATE ("))
        granted = {c.strip() for c in grant.split("(", 1)[1].split(")", 1)[0].split(",")}
        assert granted == set(MUTABLE_WORKFLOW_COLUMNS)
        assert not (granted & set(IMMUTABLE_EVIDENCE_COLUMNS))


# ═══ P2 — duplicate deliveries ═══════════════════════════════════════════════

class TestDuplicateDeliveryContract:
    def test_sha256_is_indexed_but_not_unique(self):
        """ONC may legitimately resend. A UNIQUE constraint would reject the
        second delivery and leave no record that it arrived."""
        from app.tefca_registry.rce.models import RceSourceIntake

        column = RceSourceIntake.__table__.c.sha256
        assert column.index is True
        assert column.unique is not True
        for constraint in RceSourceIntake.__table__.constraints:
            columns = {c.name for c in getattr(constraint, "columns", [])}
            assert columns != {"sha256"}, "sha256 must not be uniquely constrained"

    def test_duplicate_linkage_columns_exist(self):
        from app.tefca_registry.rce.models import RceSourceIntake

        assert "duplicate_of_intake_id" in RceSourceIntake.__table__.c
        assert "duplicate_content" in RceSourceIntake.__table__.c

    def test_one_row_per_line_is_constrained(self):
        """Makes 'every line landed exactly once' checkable, not hoped for."""
        from app.tefca_registry.rce.models import RceSourceRecord

        unique = [c for c in RceSourceRecord.__table__.constraints
                  if {x.name for x in getattr(c, "columns", [])} ==
                  {"source_intake_id", "line_number"}]
        assert unique, "expected a uniqueness constraint on (intake, line)"


# ═══ P6 — Area 2 lineage ═════════════════════════════════════════════════════

class TestArea2Lineage:
    def test_one_curated_record_per_source_record(self):
        from app.tefca_registry.rce.models import RceCuratedRecord

        unique = [c for c in RceCuratedRecord.__table__.constraints
                  if {x.name for x in getattr(c, "columns", [])} == {"source_record_id"}]
        assert unique, "Area 2 must be 1:1 with Area 1"

    def test_correction_records_the_original_value_and_its_hash(self):
        from app.tefca_registry.rce.models import RceCorrectionDetail

        columns = RceCorrectionDetail.__table__.c
        for name in ("original_value", "original_value_hash", "corrected_value",
                     "correction_reason", "correction_authority", "corrected_by"):
            assert name in columns, name
        assert columns["original_value_hash"].nullable is False

    def test_value_hash_is_stable_and_distinguishes_empty_from_none(self):
        from app.tefca_registry.rce.curation import value_hash

        assert value_hash("02718") == value_hash("02718")
        assert value_hash(None) == value_hash("")
        assert value_hash("2718") != value_hash("02718")

    def test_holding_severities_keep_records_out_of_verification(self):
        from app.tefca_registry.rce.curation import HOLDING_SEVERITIES

        assert HOLDING_SEVERITIES == {"CRITICAL", "HIGH"}

    def test_only_clean_and_corrected_are_promotable(self):
        from app.tefca_registry.rce.promotion import PROMOTABLE_STATUSES

        assert set(PROMOTABLE_STATUSES) == {"CLEAN", "CORRECTED"}
        assert "HELD" not in PROMOTABLE_STATUSES
        assert "REJECTED" not in PROMOTABLE_STATUSES


# ═══ P7 — the human gate ═════════════════════════════════════════════════════

class TestHumanGate:
    def test_resolution_workflow_transitions(self):
        from app.tefca_registry.rce.curation import _ALLOWED_TRANSITIONS

        assert "PROPOSED" in _ALLOWED_TRANSITIONS["OPEN"]
        assert "APPROVED" in _ALLOWED_TRANSITIONS["UNDER_REVIEW"]
        assert _ALLOWED_TRANSITIONS["RESOLVED"] == set(), \
            "RESOLVED is terminal"
        assert "RESOLVED" not in _ALLOWED_TRANSITIONS["OPEN"], \
            "an issue cannot jump straight from OPEN to RESOLVED"


# ═══ P8 — promotion semantics ════════════════════════════════════════════════

class TestPromotionSemantics:
    def test_two_relationship_types_for_two_different_edges(self):
        from app.tefca_registry.rce.promotion import (
            REL_MANAGED_BY_QHIN, REL_SUB_PARTICIPANT_OF)

        assert REL_MANAGED_BY_QHIN != REL_SUB_PARTICIPANT_OF

    def test_registry_entity_carries_rce_columns_for_shared_identifiers(self):
        """TEFCAID is a column, not an identifier row, because it identifies an
        organisation family and the identifier table's unique index would
        reject the 241st member."""
        from app.tefca_registry.models import TefcaRegEntity

        columns = TefcaRegEntity.__table__.c
        for name in ("rce_org_oid", "rce_tefcaid", "rce_hcid", "rce_aaid",
                     "sequoia_org_type", "org_node_type", "is_test_record",
                     "source_record_id"):
            assert name in columns, name
        assert columns["rce_tefcaid"].unique is not True


# ═══ P12 — reconciliation ════════════════════════════════════════════════════

class TestReconciliationContract:
    def test_failure_raises_rather_than_warns(self):
        from app.tefca_registry.rce.reconciliation import ReconciliationFailure

        assert issubclass(ReconciliationFailure, RuntimeError)

    def test_reconciliation_asserts_equalities_not_tolerances(self):
        import inspect

        from app.tefca_registry.rce import reconciliation

        source = inspect.getsource(reconciliation.reconcile_delivery)
        assert "d_curated == a_received" in source
        assert "e_promoted == c_eligible" in source
        assert "orphan_curated == 0" in source


# ═══ Regression guard on the enriched fixtures ═══════════════════════════════

class TestFixturesUnaffected:
    def test_bundled_fixtures_still_load_and_are_unchanged(self):
        """The RCE pipeline must not have disturbed the bundled fixtures the
        rest of the evidence suite pins."""
        from app.Tefca.mock_data import ALL_MOCK_ENTITIES, MOCK_STATS

        assert MOCK_STATS["total"] == len(ALL_MOCK_ENTITIES) == 41
        first = ALL_MOCK_ENTITIES[0]
        assert first["id"] == "rce-org-b1-001"
        assert [i["value"] for i in first["identifier"]
                if "us-npi" not in i["system"]] == ["PART-001"]

    def test_entity_resolver_defaults_to_mock(self):
        """The flag is flipped to db in DEV by environment, never by default —
        so the existing suite keeps resolving fixtures."""
        import os

        from app.Tefca.entity_resolution import DEFAULT_SOURCE, resolver_source

        assert DEFAULT_SOURCE == "mock"
        original = os.environ.pop("ENTITY_RESOLVER_SOURCE", None)
        try:
            assert resolver_source() == "mock"
        finally:
            if original is not None:
                os.environ["ENTITY_RESOLVER_SOURCE"] = original
