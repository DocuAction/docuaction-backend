"""Exception inventory — rule -> source -> delivery -> stage -> cause -> findings
-> unique records -> review cases, produced from a database (and optionally from
a delivered file), as Markdown + JSON. Aggregates only; no row values are printed.

Three populations are kept as three separate sections and never summed:

    PROCESSING FAILURES    rce_delivery_stage_events / rce_delivery_jobs /
                           rce_rule_execution_history with a non-success status.
                           A stage that errored. Not a statement about an entity.
    VERIFICATION OUTCOMES  tefca_dimension_evidence (disposition per source x
                           dimension x entity) and tefca_verifications (status per
                           source). What a source said. UNAVAILABLE is "the source
                           did not answer", never "the entity failed".
    ANALYST / REVIEW CASES review_records (bucket + rule + rule version) and the
                           decision events / QA state on them. Human-facing queue
                           items. A case can carry many findings.

Data-quality findings (rce_issues) are reported by rule, source, delivery,
stage and cause, with BOTH the finding count and the number of unique affected
records, because one record can carry many findings and one rule can fire on
many records; neither number is the other.

Optional: `--real-file <delivery.csv>` adds the NPI six-assessment section
(syntax, type fitness, identity matching, own-NPI presence requirement,
representative-provider evidence, covered-provider eligibility), computed
offline from the delivered file plus the locally held NPPES Data Dissemination
index (`nppes_index.json`, built by scripts/phase6_population_enrichment.py) and
the CMS PPEF enrolment extract. Each assessment is reported separately; none is
allowed to decide another. The delivered file is read with the application's
own reader (`rce.reader.read_delivery`) so the row count matches ingestion.

Usage:
    python scripts/exception_inventory.py --label SYNTHETIC --out <dir> \
        [--database-url postgresql+asyncpg://...] [--intake <uuid>] \
        [--real-file <csv> --nppes-index <json> --ppef-enrollment <csv>] [--skip-db]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── stage attribution for ledger rows ────────────────────────────────────────
# `rce_issues.issue_code` carries a namespace prefix: DQ- rows are written by the
# quality engine over Area 1; VR- rows are written at verification time
# (verification_findings.py). The quality rule registry also records a `stage`
# per rule id; both are consulted and reported.
_ISSUE_PREFIX_STAGE = {"DQ": "QUALITY", "VR": "VERIFICATION"}


def _rule_registry() -> Dict[str, Dict[str, str]]:
    """rule_id -> {category, stage, version, description} from quality_rules."""
    try:
        from app.tefca_registry.rce.quality_rules import ALL_RULES
    except Exception as exc:  # noqa: BLE001 — the inventory must still run
        return {"__error__": {"description": f"quality_rules import failed: {exc!r}"}}
    return {r.rule_id: {"category": r.category, "stage": r.stage,
                        "version": r.version, "description": r.description}
            for r in ALL_RULES}


def _asyncpg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


# ── database sections ────────────────────────────────────────────────────────

async def _rows(conn, sql: str, *args) -> List[Dict[str, Any]]:
    return [dict(r) for r in await conn.fetch(sql, *args)]


async def inventory_from_db(url: str, intake: Optional[str]) -> Dict[str, Any]:
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_url(url))
    try:
        out: Dict[str, Any] = {"database": url.rsplit("/", 1)[-1], "intake_filter": intake}
        # Population scoping: entity ids of the intake's promoted records, or all.
        if intake:
            pop_uuid = ("SELECT DISTINCT canonical_entity_id FROM rce_curated_records "
                        "WHERE source_intake_id = CAST($1 AS uuid) AND canonical_entity_id IS NOT NULL")
            args = (intake,)
            issue_scope = "WHERE source_intake_id = CAST($1 AS uuid)"
        else:
            pop_uuid = "SELECT id FROM tefca_reg_entities"
            args = ()
            issue_scope = ""
        pop_text = f"SELECT CAST(e AS text) FROM ({pop_uuid}) p(e)"

        out["context"] = {
            "intakes": await _rows(conn,
                "SELECT id::text AS intake_id, delivery_label, record_count FROM rce_source_intakes "
                + ("WHERE id = CAST($1 AS uuid)" if intake else "") + " ORDER BY 2", *args),
            "review_rules": await _rows(conn,
                "SELECT rule_code, version, bucket, priority, is_active, retired_date::text, "
                "conditions::text FROM review_rules ORDER BY rule_code, version"),
            "population_entities": (await conn.fetchval(
                f"SELECT count(*) FROM ({pop_uuid}) p", *args)),
        }

        # 1. PROCESSING FAILURES ------------------------------------------------
        out["processing"] = {
            "stage_events_by_stage_status": await _rows(conn,
                "SELECT stage, status, count(*) AS events, max(attempt) AS max_attempt, "
                "count(DISTINCT job_id) AS jobs, count(*) FILTER (WHERE failure_class IS NOT NULL) AS with_failure_class "
                "FROM rce_delivery_stage_events "
                + ("WHERE intake_id = CAST($1 AS uuid) " if intake else "")
                + "GROUP BY 1,2 ORDER BY 1,2", *args),
            "failure_classes": await _rows(conn,
                "SELECT stage, failure_class, count(*) AS events FROM rce_delivery_stage_events "
                "WHERE status NOT IN ('COMPLETED','STARTED','SKIPPED') "
                + ("AND intake_id = CAST($1 AS uuid) " if intake else "")
                + "GROUP BY 1,2 ORDER BY 1,2", *args),
            "jobs_by_state_stage": await _rows(conn,
                "SELECT state, stage, count(*) AS jobs FROM rce_delivery_jobs "
                + ("WHERE source_intake_id = CAST($1 AS uuid) " if intake else "")
                + "GROUP BY 1,2 ORDER BY 1,2", *args),
            "rule_execution_by_status": await _rows(conn,
                "SELECT execution_status, count(*) AS executions, count(DISTINCT rule_id) AS rules, "
                "count(*) FILTER (WHERE error IS NOT NULL) AS with_error "
                "FROM rce_rule_execution_history GROUP BY 1 ORDER BY 1"),
        }

        # 2. DATA-QUALITY FINDINGS (ledger) -------------------------------------
        out["findings"] = {
            "by_rule": await _rows(conn, f"""
                SELECT rule_id, issue_type, severity, correction_authority,
                       split_part(issue_code, '-', 1) AS code_prefix,
                       count(*) AS findings,
                       count(DISTINCT source_record_id) AS unique_records,
                       count(*) FILTER (WHERE source_record_id IS NULL) AS delivery_level,
                       count(DISTINCT source_intake_id) AS deliveries,
                       count(*) FILTER (WHERE resolution = 'OPEN') AS open_findings,
                       count(*) FILTER (WHERE qa_approved_at IS NOT NULL) AS qa_approved
                FROM rce_issues {issue_scope}
                GROUP BY 1,2,3,4,5 ORDER BY 1,2""", *args),
            "by_delivery": await _rows(conn, f"""
                SELECT i.source_intake_id::text AS intake_id,
                       count(*) AS findings, count(DISTINCT i.source_record_id) AS unique_records,
                       count(DISTINCT i.rule_id) AS rules
                FROM rce_issues i {issue_scope.replace('source_intake_id', 'i.source_intake_id')}
                GROUP BY 1 ORDER BY 2 DESC""", *args),
            "by_severity": await _rows(conn, f"""
                SELECT severity, count(*) AS findings, count(DISTINCT source_record_id) AS unique_records
                FROM rce_issues {issue_scope} GROUP BY 1 ORDER BY 1""", *args),
            "unique_records_any_finding": await conn.fetchval(
                f"SELECT count(DISTINCT source_record_id) FROM rce_issues {issue_scope}", *args),
            "records_by_finding_count": await _rows(conn, f"""
                SELECT n AS findings_on_record, count(*) AS records FROM (
                    SELECT source_record_id, count(*) AS n FROM rce_issues
                    {issue_scope + ' AND' if issue_scope else 'WHERE'} source_record_id IS NOT NULL GROUP BY 1) t
                GROUP BY 1 ORDER BY 1""", *args),
        }

        # 3. VERIFICATION OUTCOMES ----------------------------------------------
        out["verification"] = {
            "dimension_evidence": await _rows(conn, f"""
                SELECT evidence_dimension, source, disposition,
                       count(*) AS rows, count(DISTINCT entity_id) AS unique_entities,
                       count(DISTINCT generation_timestamp) AS generations
                FROM tefca_dimension_evidence
                WHERE entity_id IN ({pop_text})
                GROUP BY 1,2,3 ORDER BY 1,2,3""", *args),
            "verifications": await _rows(conn, f"""
                SELECT source, verification_status,
                       count(*) AS rows, count(DISTINCT entity_id) AS unique_entities
                FROM tefca_verifications
                WHERE entity_id IN ({pop_uuid})
                GROUP BY 1,2 ORDER BY 1,2""", *args),
            "per_source_outcome_entities": await _rows(conn, f"""
                SELECT source,
                       count(DISTINCT entity_id) AS attempted,
                       count(DISTINCT entity_id) FILTER (WHERE disposition IN ('PASS','CORROBORATED')) AS verified,
                       count(DISTINCT entity_id) FILTER (WHERE disposition IN ('NOT_FOUND')) AS not_found,
                       count(DISTINCT entity_id) FILTER (WHERE disposition = 'REVIEW') AS review,
                       count(DISTINCT entity_id) FILTER (WHERE disposition = 'UNAVAILABLE') AS unavailable,
                       count(DISTINCT entity_id) FILTER (WHERE disposition IN ('FAIL','CONFLICT')) AS failed,
                       count(DISTINCT entity_id) FILTER (WHERE disposition = 'INSUFFICIENT_EVIDENCE') AS insufficient,
                       count(DISTINCT entity_id) FILTER (WHERE disposition = 'NOT_APPLICABLE') AS not_applicable
                FROM tefca_dimension_evidence
                WHERE evidence_dimension IN ('IDENTITY','MEDICARE_ENROLLMENT','EXCLUSION_REVOCATION',
                                             'TEFCA_ALIGNMENT','PROVIDER_ORG_RELATIONSHIP')
                  AND entity_id IN ({pop_text})
                GROUP BY 1 ORDER BY 1""", *args),
        }

        # 4. ANALYST / REVIEW CASES ---------------------------------------------
        out["cases"] = {
            "by_bucket_rule_version": await _rows(conn, f"""
                SELECT classification_bucket AS bucket, classification_rule AS rule,
                       classification_rule_version AS rule_version,
                       count(*) AS cases, count(DISTINCT entity_id) AS unique_entities,
                       count(*) FILTER (WHERE reviewer_resolution IS NOT NULL) AS human_resolved,
                       count(*) FILTER (WHERE reportable_at IS NOT NULL) AS qa_reportable,
                       count(*) FILTER (WHERE assigned_to_user_id IS NOT NULL) AS assigned
                FROM review_records
                WHERE entity_id IN ({pop_uuid}) OR entity_id IS NULL
                GROUP BY 1,2,3 ORDER BY 1,2,3""", *args),
            "entities_with_multiple_cases": await conn.fetchval(f"""
                SELECT count(*) FROM (SELECT entity_id FROM review_records
                    WHERE entity_id IN ({pop_uuid}) GROUP BY 1 HAVING count(*) > 1) t""", *args),
            "decision_events": await _rows(conn,
                "SELECT event_type, qa_action, determination, count(*) AS events "
                "FROM review_decision_events GROUP BY 1,2,3 ORDER BY 1,2,3"),
            "sample_entities_by_status": await _rows(conn,
                "SELECT review_status, discrepancy_bucket, count(*) AS entities "
                "FROM sample_entities GROUP BY 1,2 ORDER BY 1,2"),
        }
        return out
    finally:
        await conn.close()


# ── NPI six assessments from the delivered file ───────────────────────────────

def npi_assessments(real_file: str, nppes_index: Optional[str],
                    ppef_enrollment: Optional[str], delimiter: Optional[str] = "|") -> Dict[str, Any]:
    from app.services.npi_validator import validate_npi
    from app.tefca_registry.rce.field_map import NON_PROVIDER_HL7_ROLES, OBSERVED_SEQUOIA_ORG_TYPES
    from app.tefca_registry.rce.reader import PARSE_OK, read_delivery

    t0 = time.perf_counter()
    raw = pathlib.Path(real_file).read_bytes()
    # The ONC July delivery is pipe-delimited; ingestion declares it (intake.ingest_delivery
    # declared_delimiter="|"), so the inventory declares it too instead of sniffing.
    read = read_delivery(raw, declared_delimiter=delimiter or None)
    ok_lines = [ln for ln in read.lines if ln.parse_status == PARSE_OK]
    res: Dict[str, Any] = {
        "file": pathlib.Path(real_file).name, "sha256": read.sha256,
        "physical_lines": len(read.lines), "parsed_ok": len(ok_lines),
        "parse_field_count_mismatch": len(read.lines) - len(ok_lines),
        "delimiter": read.delimiter, "encoding": read.encoding,
    }

    # 1. SYNTAX ---------------------------------------------------------------
    present = absent = valid = invalid = 0
    invalid_reasons: collections.Counter = collections.Counter()
    npi_of_record: List[Optional[str]] = []
    per_npi_records: collections.Counter = collections.Counter()
    for ln in ok_lines:
        npi = (ln.parsed.get("NPI") or "").strip()
        if not npi:
            absent += 1
            npi_of_record.append(None)
            continue
        present += 1
        ok, why = validate_npi(npi)
        if ok:
            valid += 1
            npi_of_record.append(npi)
            per_npi_records[npi] += 1
        else:
            invalid += 1
            npi_of_record.append(None)
            invalid_reasons[(why or "invalid")[:60]] += 1
    distinct_valid = set(per_npi_records)
    res["syntax"] = {
        "records": len(ok_lines), "npi_present": present, "npi_absent": absent,
        "valid": valid, "invalid": invalid, "invalid_reasons": dict(invalid_reasons),
        "distinct_valid_npis": len(distinct_valid),
        "npis_shared_by_more_than_one_record": sum(1 for v in per_npi_records.values() if v > 1),
        "records_carrying_a_shared_npi": sum(v for v in per_npi_records.values() if v > 1),
        "basis": "45 CFR 162.406 format + CMS check digit (app.services.npi_validator); "
                 "absence is assessment 4, not a syntax outcome",
    }

    # 4. OWN-NPI PRESENCE REQUIREMENT (policy predicate, replicated from
    #    quality_rules.npi_required: Participant/Subparticipant AND hl7orgrole
    #    present AND not a NON_PROVIDER role) ----------------------------------
    req_total = req_without_npi = 0
    role_counts: collections.Counter = collections.Counter()
    role_without_npi: collections.Counter = collections.Counter()
    type_counts: collections.Counter = collections.Counter()
    type_without_npi: collections.Counter = collections.Counter()
    for ln in ok_lines:
        st = (ln.parsed.get("sequoiaorgtype") or "").strip()
        role = (ln.parsed.get("hl7orgrole") or "").strip()
        has_npi = bool((ln.parsed.get("NPI") or "").strip())
        type_counts[st or "<blank>"] += 1
        role_counts[role or "<blank>"] += 1
        if not has_npi:
            type_without_npi[st or "<blank>"] += 1
            role_without_npi[role or "<blank>"] += 1
        if st in OBSERVED_SEQUOIA_ORG_TYPES and role and role not in NON_PROVIDER_HL7_ROLES:
            req_total += 1
            if not has_npi:
                req_without_npi += 1
    res["own_npi_requirement"] = {
        "policy_predicate": "quality_rules.npi_required(): sequoiaorgtype in {Participant, "
                            "Subparticipant} AND hl7orgrole populated AND not in "
                            f"{sorted(NON_PROVIDER_HL7_ROLES)}",
        "records_where_policy_predicate_is_true": req_total,
        "of_which_without_npi (NPI-001 NPI_REQUIRED, HIGH)": req_without_npi,
        "records_with_blank_hl7orgrole": role_counts.get("<blank>", 0),
        "blank_role_records_without_npi (NPI-001 NPI_NOT_SUPPLIED, INFO — never held)":
            role_without_npi.get("<blank>", 0),
        "by_sequoiaorgtype": {k: {"records": v, "without_npi": type_without_npi.get(k, 0)}
                              for k, v in sorted(type_counts.items())},
        "by_hl7orgrole": {k: {"records": v, "without_npi": role_without_npi.get(k, 0)}
                          for k, v in sorted(role_counts.items())},
        "legal_basis_note": "45 CFR 162.410 requires an NPI of a covered health care "
                            "provider; no delivered field states covered-provider status, "
                            "so the affirmative legal requirement is assessment 6, not this "
                            "predicate. The predicate is AGT policy (internal).",
    }

    # 2/3/5/6 need NPPES evidence --------------------------------------------
    if not nppes_index or not pathlib.Path(nppes_index).exists():
        res["nppes_evidence"] = {"available": False,
                                 "note": "no local NPPES index supplied; assessments 2, 3, 5 "
                                         "and prong 1 of 6 not computed"}
        res["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
        return res

    blob = json.load(open(nppes_index, encoding="utf-8"))
    index: Dict[str, Dict[str, str]] = blob.get("index") or {}
    res["nppes_evidence"] = {
        "available": True, "source": blob.get("source"), "edition_member": blob.get("member"),
        "npis_in_index": len(index),
        "note": "NPPES Data Dissemination monthly file, indexed to the delivery's NPIs by "
                "scripts/phase6_population_enrichment.py; a pinned edition, not a live lookup",
    }

    # 2. TYPE FITNESS + 5. REPRESENTATIVE EVIDENCE ----------------------------
    found = not_found = 0
    type_by_npi: collections.Counter = collections.Counter()
    type_by_record: collections.Counter = collections.Counter()
    deactivated_npis = 0
    deactivated_records = 0
    for npi in distinct_valid:
        row = index.get(npi)
        if not row:
            not_found += 1
            continue
        found += 1
        et = (row.get("Entity Type Code") or "").strip()
        type_by_npi[et or "<blank>"] += 1
        type_by_record[et or "<blank>"] += per_npi_records[npi]
        if (row.get("NPI Deactivation Date") or "").strip() and not (row.get("NPI Reactivation Date") or "").strip():
            deactivated_npis += 1
            deactivated_records += per_npi_records[npi]
    res["type_fitness"] = {
        "distinct_valid_npis_resolved_in_nppes": found,
        "distinct_valid_npis_not_in_nppes": not_found,
        "entity_type_by_distinct_npi": {{"1": "NPI-1 individual", "2": "NPI-2 organization"}.get(k, k): v
                                        for k, v in sorted(type_by_npi.items())},
        "entity_type_by_record": {{"1": "NPI-1 individual", "2": "NPI-2 organization"}.get(k, k): v
                                  for k, v in sorted(type_by_record.items())},
        "basis": "NPPES Entity Type Code is the type authority (45 CFR 162 Type 1 / Type 2); "
                 "every delivered record is an organisation (sequoiaorgtype), so NPI-2 is the "
                 "fitting type",
    }
    res["representative_evidence"] = {
        "records_whose_npi_is_an_individual (NPI-1 on an organisation record -> "
        "ENTITY_TYPE_MISMATCH, validation_engine.py; EXCEPTION never auto-match, source_matching.py)":
            type_by_record.get("1", 0),
        "distinct_individual_npis_involved": type_by_npi.get("1", 0),
        "npis_deactivated_without_reactivation": deactivated_npis,
        "records_carrying_a_deactivated_npi": deactivated_records,
        "basis": "an individual's NPI is representative-provider evidence, kept distinct from "
                 "the organisation's own identity (45 CFR 160.103 provider vs workforce)",
    }

    # 3. IDENTITY MATCHING (name bands + practice-address comparison) ---------
    bands: collections.Counter = collections.Counter()
    addr: collections.Counter = collections.Counter()
    addr_conflict_fields: collections.Counter = collections.Counter()
    compared = 0
    try:
        from app.Tefca.address_comparison import compare_to_nppes
        from app.Tefca.validation_engine import FindingCode, ValidationEngine
        engine = ValidationEngine()
        have_engine = True
    except Exception as exc:  # noqa: BLE001
        have_engine = False
        res["identity_matching"] = {"error": f"validation engine import failed: {exc!r}"}
    if have_engine:
        band_label = {None: "MATCH (>=0.90)", FindingCode.NAME_ABBREVIATION_DIFF: "ABBREVIATION (0.70-0.90)",
                      FindingCode.NAME_DBA_VS_LEGAL: "DBA_VS_LEGAL (0.50-0.70)",
                      FindingCode.NAME_PUNCTUATION_DIFF: "PUNCTUATION (0.50-0.70)",
                      FindingCode.NAME_COMPLETELY_DIFFERENT: "COMPLETELY_DIFFERENT (0.30-0.50)",
                      FindingCode.NAME_UNRESOLVABLE: "UNRESOLVABLE (<0.30)"}
        for ln, npi in zip(ok_lines, npi_of_record):
            if not npi:
                continue
            row = index.get(npi)
            if not row:
                continue
            compared += 1
            submitted = (ln.parsed.get("name") or "").strip()
            lbn = (row.get("Provider Organization Name (Legal Business Name)") or "").strip()
            if not lbn:
                last = (row.get("Provider Last Name (Legal Name)") or "").strip()
                first = (row.get("Provider First Name") or "").strip()
                lbn = f"{first} {last}".strip()
            if submitted and lbn:
                sim = engine.normalizer.similarity_score(submitted, lbn)
                finding, _ded = engine._classify_name_similarity(sim, submitted, lbn)
                bands[band_label.get(finding, str(finding))] += 1
            else:
                bands["NOT_COMPARED (name missing on one side)"] += 1
            cmp_ = compare_to_nppes({
                "address_line": ln.parsed.get("address_line"),
                "address_city": ln.parsed.get("address_city"),
                "address_state": ln.parsed.get("address_state"),
                "address_postalCode": ln.parsed.get("address_postalCode")}, row)
            addr[cmp_.result.value] += 1
            for f in cmp_.field_conflicts:
                addr_conflict_fields[f] += 1
        res["identity_matching"] = {
            "records_with_nppes_record_to_compare": compared,
            "name_similarity_bands (validation_engine thresholds 0.90/0.70/0.50/0.30)": dict(bands),
            "practice_address_comparison (address_comparison.compare_to_nppes, LOCATION only; "
            "postal code absent from the index -> uncompared)": dict(addr),
            "address_conflicting_fields": dict(addr_conflict_fields),
            "basis": "NPPES is the identity authority; name bands are the legacy engine's "
                     "similarity model (methodology Decision D5 pending for the dimension layer); "
                     "a CONFLICT is REVIEW, never FAIL (address_evidence.py)",
        }

    # 6. COVERED-PROVIDER ELIGIBILITY ------------------------------------------
    try:
        from app.Tefca.applicability import _taxonomy_category
        cat_by_record: collections.Counter = collections.Counter()
        cat_by_npi: collections.Counter = collections.Counter()
        for npi in distinct_valid:
            row = index.get(npi)
            if not row:
                continue
            cat = _taxonomy_category(row.get("Healthcare Provider Taxonomy Code_1"), None)
            if (row.get("Entity Type Code") or "").strip() == "1":
                cat = "INDIVIDUAL_PROVIDER"
            cat_by_npi[cat] += 1
            cat_by_record[cat] += per_npi_records[npi]
        prong1 = {"by_distinct_npi": dict(cat_by_npi), "by_record": dict(cat_by_record),
                  "basis": "applicability._taxonomy_category on NPPES primary taxonomy "
                           "(unambiguous NUCC prefixes only; everything else UNKNOWN)"}
    except Exception as exc:  # noqa: BLE001
        prong1 = {"error": repr(exc)}

    prong2: Dict[str, Any] = {"available": False}
    if ppef_enrollment and pathlib.Path(ppef_enrollment).exists():
        enrolled: set = set()
        org_enrolled: set = set()
        t1 = time.perf_counter()
        with open(ppef_enrollment, encoding="latin-1", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                n = (r.get("NPI") or "").strip()
                if n in distinct_valid:
                    enrolled.add(n)
                    if (r.get("ORG_NAME") or "").strip():
                        org_enrolled.add(n)
        rec_enrolled = sum(per_npi_records[n] for n in enrolled)
        prong2 = {"available": True, "file": pathlib.Path(ppef_enrollment).name,
                  "distinct_valid_npis_with_ppef_enrolment": len(enrolled),
                  "of_which_enrolled_as_organisation (ORG_NAME populated)": len(org_enrolled),
                  "records_with_ppef_enrolment": rec_enrolled,
                  "scan_seconds": round(time.perf_counter() - t1, 1),
                  "basis": "CMS PPEF public enrolment extract (quarterly); presence is an "
                           "affirmative Medicare-relevance signal; absence is never a negative one"}
    res["covered_provider_eligibility"] = {
        "prong_1_is_a_health_care_provider": prong1,
        "prong_2_conducts_standard_transactions (PPEF enrolment as the only affirmative signal)": prong2,
        "resolvable_in_principle_population (records with a valid NPI resolved in NPPES)":
            sum(per_npi_records[n] for n in distinct_valid if n in index),
        "irreducibly_unresolved_from_delivered_fields (records with no valid NPI)":
            absent + invalid,
        "basis": "45 CFR 160.103 / 162.408 / 162.410: a covered health care provider conducting "
                 "standard electronic transactions must obtain and use an NPI. No delivered field "
                 "states either prong; NPPES taxonomy and PPEF enrolment are the available "
                 "affirmative signals. Unresolved is a statement about the evidence, not a finding.",
    }
    res["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return res


# ── rendering ────────────────────────────────────────────────────────────────

def _table(rows: List[Dict[str, Any]], cols: Optional[List[str]] = None) -> str:
    if not rows:
        return "_(no rows)_\n"
    cols = cols or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|").replace("\n", " ")[:160]
                                    for c in cols) + " |")
    return "\n".join(out) + "\n"


def _kv(d: Dict[str, Any], indent: int = 0) -> str:
    out = []
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            out.append(f"{pad}- **{k}**:")
            out.append(_kv(v, indent + 1))
        else:
            out.append(f"{pad}- **{k}**: {v}")
    return "\n".join(out)


def render_markdown(label: str, db: Optional[Dict[str, Any]], npi: Optional[Dict[str, Any]],
                    registry: Dict[str, Dict[str, str]]) -> str:
    md = [f"# Exception inventory — {label}", ""]
    md.append("Generated by `scripts/exception_inventory.py`. Aggregates only; no delivered values. "
              "Three populations are reported in three sections and are never summed: processing "
              "failures (a stage errored), verification outcomes (what a source said) and analyst / "
              "review cases (human-facing queue items). Data-quality findings carry both the finding "
              "count and the unique affected records.")
    md.append("")
    if db:
        md.append(f"## Database `{db['database']}`" + (f" — intake `{db['intake_filter']}`" if db["intake_filter"] else " — all deliveries"))
        md.append("")
        md.append(f"Population entities in scope: **{db['context']['population_entities']}**. Deliveries:")
        md.append(_table(db["context"]["intakes"]))
        md.append("Classifier rule versions present:")
        md.append(_table(db["context"]["review_rules"], ["rule_code", "version", "bucket", "priority", "is_active", "retired_date"]))
        md.append("### 1. Processing failures (stage attempts that did not complete)")
        md.append(_table(db["processing"]["stage_events_by_stage_status"]))
        md.append("Failure classes (non-completed, non-skipped stages):")
        md.append(_table(db["processing"]["failure_classes"]))
        md.append("Delivery jobs by state/stage:")
        md.append(_table(db["processing"]["jobs_by_state_stage"]))
        md.append("Rule executions by status (a rule that errored is a processing failure, not a finding):")
        md.append(_table(db["processing"]["rule_execution_by_status"]))
        md.append("### 2. Data-quality findings — rule → stage → cause → findings → unique records")
        rows = []
        for r in db["findings"]["by_rule"]:
            reg = registry.get(r["rule_id"], {})
            rows.append({
                "rule": r["rule_id"], "issue_type": r["issue_type"], "severity": r["severity"],
                "stage": reg.get("stage") or _ISSUE_PREFIX_STAGE.get(r["code_prefix"], "?"),
                "category": reg.get("category", "?"), "rule_version": reg.get("version", "?"),
                "cause": (reg.get("description") or "")[:110],
                "authority": r["correction_authority"], "findings": r["findings"],
                "unique_records": r["unique_records"], "delivery_level": r["delivery_level"],
                "deliveries": r["deliveries"], "open": r["open_findings"], "qa_approved": r["qa_approved"]})
        md.append(_table(rows))
        md.append("By severity:")
        md.append(_table(db["findings"]["by_severity"]))
        md.append(f"Unique records with at least one finding: **{db['findings']['unique_records_any_finding']}**. "
                  "Findings per record distribution:")
        md.append(_table(db["findings"]["records_by_finding_count"]))
        md.append("By delivery:")
        md.append(_table(db["findings"]["by_delivery"]))
        md.append("### 3. Verification outcomes — dimension × source × disposition")
        md.append(_table(db["verification"]["dimension_evidence"]))
        md.append("Per-source distinct entities by outcome (source-state dimensions only; "
                  "REVIEW is reported separately from NOT_FOUND — a potential match is not 'nothing found'):")
        md.append(_table(db["verification"]["per_source_outcome_entities"]))
        md.append("Connector audit rows (`tefca_verifications`):")
        md.append(_table(db["verification"]["verifications"]))
        md.append("### 4. Analyst / review cases — bucket × rule × version")
        md.append(_table(db["cases"]["by_bucket_rule_version"]))
        md.append(f"Entities with more than one review case (repeat cycles): **{db['cases']['entities_with_multiple_cases']}**.")
        md.append("")
        md.append("Decision events (analyst determinations / QA actions):")
        md.append(_table(db["cases"]["decision_events"]))
        md.append("Sample entities by review status:")
        md.append(_table(db["cases"]["sample_entities_by_status"]))
    if npi:
        md.append("## NPI — six separate assessments (delivered file, offline evidence)")
        md.append("")
        md.append(f"File `{npi['file']}` sha256 `{npi['sha256']}`; {npi['physical_lines']} physical "
                  f"lines, {npi['parsed_ok']} parsed to 41 fields, "
                  f"{npi['parse_field_count_mismatch']} field-count mismatches (preserved, not mapped). "
                  "None of the six assessments decides another.")
        md.append("")
        for key, title in (("syntax", "1. Syntax"), ("type_fitness", "2. Type fitness"),
                           ("identity_matching", "3. Identity matching"),
                           ("own_npi_requirement", "4. Own-NPI presence requirement"),
                           ("representative_evidence", "5. Representative-provider evidence"),
                           ("covered_provider_eligibility", "6. Covered-provider eligibility")):
            if key in npi:
                md.append(f"### {title}")
                md.append(_kv(npi[key]))
                md.append("")
        md.append("NPPES evidence: " + json.dumps(npi.get("nppes_evidence")))
        md.append("")
    return "\n".join(md)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--intake", default=None)
    ap.add_argument("--label", required=True, help="e.g. SYNTHETIC-24563 or REAL-2026-07-20")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-db", action="store_true")
    ap.add_argument("--real-file", default=None)
    ap.add_argument("--nppes-index", default=None)
    ap.add_argument("--ppef-enrollment", default=None)
    ap.add_argument("--delimiter", default="|", help="declared delivery delimiter (| , or tab); empty = sniff")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = _rule_registry()
    db = None
    if not args.skip_db:
        if not args.database_url:
            ap.error("--database-url or DATABASE_URL is required unless --skip-db")
        db = asyncio.run(inventory_from_db(args.database_url, args.intake))
    npi = (npi_assessments(args.real_file, args.nppes_index, args.ppef_enrollment,
                           delimiter=args.delimiter) if args.real_file else None)

    payload = {"label": args.label, "generated_by": "scripts/exception_inventory.py",
               "db": db, "npi_assessments": npi}
    stem = "inventory_" + "".join(c if c.isalnum() or c in "-_" else "_" for c in args.label)
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(render_markdown(args.label, db, npi, registry), encoding="utf-8")
    print(f"wrote {out_dir / (stem + '.md')} and .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
