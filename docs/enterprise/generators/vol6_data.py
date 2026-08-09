"""AGT-DA-001 — Volume VI, Enterprise Data Architecture."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

S = pathlib.Path(__file__).parent
OUT = pathlib.Path(r"C:\Imran_Coding projects\DocuAction\backend\docs\enterprise")
OUT.mkdir(parents=True, exist_ok=True)
SCHEMA = json.loads((S / "schema.json").read_text(encoding="utf-8"))

# Tables documented in full, grouped by domain, in reading order.
DOMAINS = [
    ("Entity Domain", [
        "tefca_reg_entities", "tefca_entity_identifiers",
        "tefca_entity_relationships", "tefca_entity_endpoints",
        "tefca_entity_versions", "tefca_entity_findings",
        "tefca_import_batches", "tefca_entities",
    ]),
    ("Verification Domain", [
        "tefca_verifications", "tefca_verification_jobs",
        "tefca_verification_checks", "tefca_source_cache",
        "tefca_connector_logs",
    ]),
    ("Review Domain", [
        "review_rules", "review_records", "review_samples", "sample_entities",
        "review_cycles", "review_reports", "tefca_priority_cases",
    ]),
    ("Platform Domain", ["users", "tefca_reg_audit_log"]),
    ("Bulletin Domain", [
        "bulletin_articles", "bulletin_briefings", "bulletin_source_registry",
        "bulletin_source_outcome", "bulletin_cost_logs",
        "bulletin_search_profiles", "bulletin_run_log", "bulletin_audit_log",
    ]),
]

# Privacy classification per table. Assigned deliberately, not by pattern match.
CLASS = {
    "tefca_reg_entities": ("CUI / PII", "Moderate", "Yes", "Yes",
                           "Organisation names and NPIs identify real providers"),
    "tefca_entities": ("CUI / PII", "Moderate", "Yes", "Yes",
                       "Legacy entity store; same content class"),
    "tefca_entity_identifiers": ("CUI / PII", "Moderate", "Yes", "Yes",
                                 "NPI, TEFCAID, HCID are direct identifiers"),
    "tefca_entity_relationships": ("CUI", "Moderate", "Yes", "Yes",
                                   "Reveals participation hierarchy"),
    "tefca_entity_endpoints": ("CUI", "Moderate", "Yes", "Yes",
                               "Exchange endpoints are security-relevant"),
    "tefca_entity_versions": ("CUI / PII", "Moderate", "Yes", "Yes",
                              "Point-in-time snapshots of entity data"),
    "tefca_entity_findings": ("CUI", "Moderate", "Yes", "Yes",
                              "Findings reference identified entities"),
    "tefca_import_batches": ("INTERNAL", "Low", "Yes", "Yes",
                             "Operational metadata; error text may quote entity "
                             "names"),
    "tefca_verifications": ("CUI", "Moderate", "Yes", "Yes",
                            "Third-party responses about identified providers"),
    "tefca_verification_jobs": ("INTERNAL", "Low", "Yes", "Yes",
                                "Job orchestration metadata"),
    "tefca_verification_checks": ("CUI", "Moderate", "Yes", "Yes",
                                  "Per-source check outcomes"),
    "tefca_source_cache": ("CUI", "Moderate", "Yes", "Yes",
                           "Cached authoritative-source payloads"),
    "tefca_connector_logs": ("INTERNAL", "Low", "No", "Yes",
                             "Connector operational logging"),
    "review_rules": ("INTERNAL", "Low", "No", "Yes",
                     "Methodology configuration; no entity data"),
    "review_records": ("CUI", "Moderate", "Yes", "Yes",
                       "Classification outcomes for identified entities"),
    "review_samples": ("CUI", "Moderate", "Yes", "Yes",
                       "Sampling parameters and drawn population"),
    "sample_entities": ("CUI", "Moderate", "Yes", "Yes",
                        "Links sample to identified entities"),
    "review_cycles": ("INTERNAL", "Low", "Yes", "Yes",
                      "Cycle orchestration metadata"),
    "review_reports": ("CUI", "Moderate", "Yes", "Yes",
                       "Archived report content references entity data"),
    "tefca_priority_cases": ("CUI", "Moderate", "Yes", "Yes",
                             "Priority review subjects and findings"),
    "users": ("PII", "Moderate", "Yes", "Yes",
              "Email addresses and credential hashes"),
    "tefca_reg_audit_log": ("INTERNAL / CUI", "Moderate", "Yes", "Yes",
                            "Actor, IP and entity references"),
    "bulletin_articles": ("PUBLIC", "Low", "No", "No",
                          "Published news content"),
    "bulletin_briefings": ("PUBLIC", "Low", "No", "No",
                           "Assembled public news briefings"),
    "bulletin_source_registry": ("PUBLIC", "Low", "No", "No",
                                 "Feed configuration"),
    "bulletin_source_outcome": ("INTERNAL", "Low", "No", "No",
                                "Collector operational telemetry"),
    "bulletin_cost_logs": ("INTERNAL", "Low", "No", "Yes",
                           "Provider spend; commercially sensitive"),
    "bulletin_search_profiles": ("INTERNAL", "Low", "No", "No",
                                 "Boolean search configuration"),
    "bulletin_run_log": ("INTERNAL", "Low", "No", "Yes",
                         "Collection run telemetry"),
    "bulletin_audit_log": ("INTERNAL", "Low", "No", "Yes",
                           "Editorial action audit"),
}

STEWARD = {
    "Entity Domain": ("ONC Technical Lead", "AGT Data Analyst",
                      "ONC-supplied registry extract", "Per review cycle"),
    "Verification Domain": ("AGT Technical Lead", "AGT Data Analyst",
                            "NPPES / PECOS / OIG LEIE", "On verification"),
    "Review Domain": ("ONC Methodology Reviewer", "AGT Programme Manager",
                      "DocuAction platform", "Per review cycle"),
    "Platform Domain": ("AGT Chief Executive Officer", "AGT Technical Lead",
                        "DocuAction platform", "On change"),
    "Bulletin Domain": ("AGT Programme Manager", "AGT Data Analyst",
                        "Public RSS and news APIs", "Daily"),
}


def pgtype(t):
    t = t.upper()
    for k, v in (("UUID", "uuid"), ("JSONB", "jsonb"), ("VARCHAR", "varchar"),
                 ("TEXT", "text"), ("BOOLEAN", "boolean"), ("INTEGER", "integer"),
                 ("FLOAT", "double precision"), ("DOUBLE", "double precision"),
                 ("DATETIME", "timestamp"), ("TIMESTAMP", "timestamp"),
                 ("DATE", "date"), ("NUMERIC", "numeric"), ("SERIAL", "serial")):
        if k in t:
            return v + (t[t.index("("):] if "(" in t and k == "VARCHAR" else "")
    return t.lower()


def business_name(col):
    return col.replace("_", " ").replace(" id", " identifier").strip().capitalize()


D = AGTDoc(doc_id="AGT-DA-001",
           title="DocuAction TEFCA ARC Platform",
           subtitle="Prepared for the Assistant Secretary for Technology Policy / "
                    "Office of the National Coordinator for Health IT (ASTP/ONC)",
           version="1.0", date="August 2026")
D.cover("Volume VI — Enterprise Data Architecture")
D.doc_control([
    ("Document ID", "AGT-DA-001"),
    ("Document Title", "Volume VI — Enterprise Data Architecture"),
    ("Version", "1.0"), ("Status", "Released"), ("Date", "August 2026"),
    ("Contract Number", "7571MN26F80064"),
    ("Contractor", "Alliance Global Tech, Inc. (AGT)"),
    ("CAGE / UEI", "8ERE8 / MP2FLV1MAW93"),
    ("Author", "Imran Siddiqui, Chief Executive Officer"),
    ("Database Platform", "PostgreSQL 16 — Azure Database for PostgreSQL "
                          "Flexible Server"),
    ("Schema Source", "Extracted programmatically from the deployed application "
                      "models and DDL"),
    ("Classification", "CONFIDENTIAL — Controlled Unclassified Information (CUI)"),
    ("Related Documents", "AGT-EX-001, AGT-REQ-001"),
])
D.page_break()
D.toc()

# ── 1 ────────────────────────────────────────────────────────────────────────
D.h1("1. Data Architecture Overview")
D.p("The DocuAction data architecture is organised into five domains. The "
    "boundaries follow ownership and change cadence rather than technical "
    "convenience: entity data is supplied by ONC and changes per cycle, "
    "verification data is produced by third parties and changes on every run, "
    "and review data is produced by the platform and by human reviewers.")
D.table(["Domain", "Purpose", "Tables", "Primary Classification"],
        [("Entity", "The TEFCA registry population, its identifiers, hierarchy "
                    "and version history", "8", "CUI / PII"),
         ("Verification", "What each authoritative source returned, when, and "
                          "with what payload hash", "5", "CUI"),
         ("Review", "Rules, classifications, samples, cycles and archived "
                    "reports", "7", "CUI"),
         ("Platform", "Users, roles and the immutable audit trail", "2",
          "PII / INTERNAL"),
         ("Bulletin", "Regulatory news monitoring — a separate AGT capability "
                      "sharing the platform", "8", "PUBLIC / INTERNAL")],
        widths=(1.1, 3.0, 0.7, 1.7))
D.callout("The Bulletin domain is documented here because it shares the "
          "database, not because it is a TEFCA ARC deliverable. It is out of "
          "contract scope (AGT-EX-001 §4.2) and carries no CUI.", "SCOPE")

D.h2("1.1 Architectural properties")
D.bullets([
    "Every classification is reproducible: the rule version, source responses "
    "and response hashes are all retained.",
    "Review records are append-only. A resolution is recorded as new state, not "
    "as an update over the original classification.",
    "Reports are archived as delivered. Exports read the archived payload and "
    "never recompute, so two renderings of one report cannot disagree.",
    "Entity deletion is soft. Review records, verifications and sample "
    "membership reference entities, and a hard delete would orphan evidence "
    "that has already been reported.",
    "Identifiers are normalised into their own table rather than held as "
    "columns, because an entity may hold several identifiers of a type and only "
    "one is primary.",
])
D.page_break()

# ── 2 ────────────────────────────────────────────────────────────────────────
D.h1("2. Conceptual Data Model")
D.p("Entities and relationships only — no attributes. Read top to bottom as the "
    "path a single registry record takes through the platform.")
D.table(["From", "Relationship", "To", "Meaning"],
        [("Entity", "is verified by", "Verification",
          "Each verification run records what every source said about one "
          "entity"),
         ("Verification", "is evaluated against", "Review Rule",
          "The active rule set is applied to the verification result"),
         ("Review Rule", "produces", "Classification",
          "The first matching rule, in priority order, determines the bucket"),
         ("Classification", "is recorded as", "Review Record",
          "One immutable record per classified entity per cycle"),
         ("Entity", "is selected into", "Sample",
          "Cochran sampling draws entities from the population"),
         ("Sample", "contains", "Sample Entity",
          "Membership of the drawn sample"),
         ("Sample", "is reported in", "Review Report",
          "A report cites the sample that backs it"),
         ("Review Report", "belongs to", "Review Cycle",
          "A cycle ties one sample to one report"),
         ("Review Record", "may require", "Human Resolution",
          "B3 classifications are resolved by a reviewer, never automatically"),
         ("Entity", "has", "Identifier",
          "NPI, TEFCAID, HCID and others; one primary per type"),
         ("Entity", "relates to", "Entity",
          "QHIN to Participant to Sub-Participant hierarchy"),
         ("Entity", "has", "Version",
          "A snapshot is written on creation and on change"),
         ("Every action", "writes", "Audit Entry",
          "Actor, action, entity, IP address and timestamp")],
        widths=(1.15, 1.15, 1.15, 3.05))
D.page_break()

# ── 3 ────────────────────────────────────────────────────────────────────────
D.h1("3. Logical Data Model")
D.h2("3.1 Cardinality")
D.table(["Parent", "Child", "Cardinality", "Delete behaviour"],
        [("Entity", "Verification", "1 : N",
          "Soft delete on parent; verifications retained"),
         ("Entity", "Identifier", "1 : N", "Cascade"),
         ("Entity", "Endpoint", "1 : N", "Cascade"),
         ("Entity", "Version", "1 : N", "Retained — version history is evidence"),
         ("Entity", "Finding", "1 : N", "Retained"),
         ("Entity", "Review Record", "1 : N", "Retained"),
         ("Entity", "Entity (hierarchy)", "1 : N",
          "Relationship row retained; orphaning prevented"),
         ("Sample", "Sample Entity", "1 : N", "Cascade"),
         ("Review Cycle", "Sample", "1 : 1", "Retained"),
         ("Review Cycle", "Review Report", "1 : 1", "Retained"),
         ("Review Rule", "Review Record", "1 : N",
          "Rule retired, never deleted — records cite it"),
         ("Import Batch", "Entity", "1 : N", "Batch retained")],
        widths=(1.4, 1.4, 1.0, 2.7))
D.callout("Two deliberate departures from a naive cascade design. First, "
          "retiring rules rather than deleting them: a classification recorded "
          "last quarter cites the rule version that produced it, and deleting "
          "that row would leave the record pointing at nothing. Second, soft "
          "entity deletion: verifications, review records and sample membership "
          "all reference the entity, and reported evidence must remain "
          "resolvable.", "DESIGN")
D.page_break()

# ── 4 ────────────────────────────────────────────────────────────────────────
D.h1("4. Physical Data Model")
D.h2("4.1 Platform conventions")
D.table(["Concern", "Implementation", "Rationale"],
        [("Primary keys", "uuid, application-generated",
          "Allows a parent and its children to be constructed before any flush"),
         ("Semi-structured data", "jsonb",
          "Source payloads and report snapshots vary in shape by provider and "
          "over time"),
         ("Timestamps", "timestamp, UTC",
          "Cross-cycle comparison requires a single reference frame"),
         ("Soft delete", "is_deleted boolean plus deleted_at timestamp",
          "Distinct from is_active, which means 'not currently operating' — a "
          "legitimate state for a real participant"),
         ("Booleans with defaults", "server_default",
          "A column added to a populated table needs a value for existing rows"),
         ("Schema evolution", "ADD COLUMN IF NOT EXISTS at startup",
          "create_all() cannot add a column to a table that already exists"),
         ("Encoding", "UTF-8", "Organisation names contain non-ASCII characters")],
        widths=(1.2, 2.0, 3.3))

D.h2("4.2 jsonb columns and why they are not normalised")
D.p("Three column groups are stored as jsonb rather than decomposed into "
    "tables. In each case the shape is set by a third party or is a point-in-"
    "time snapshot, so a normalised schema would have to change whenever an "
    "external provider changed theirs.")
D.table(["Table.Column", "Content", "Why jsonb"],
        [("tefca_verifications.result", "Raw authoritative-source payload",
          "NPPES, PECOS, OIG LEIE and SAM return different shapes, and those "
          "shapes change without notice"),
         ("review_rules.conditions", "Rule condition tree",
          "Rules are data, editable without deployment; a fixed schema would "
          "constrain the methodology"),
         ("review_reports.report_data", "Archived report snapshot",
          "The report as delivered must never change, even if the code that "
          "generates reports does"),
         ("tefca_entity_versions.snapshot_data", "Entity state at a version",
          "A snapshot must survive later schema changes to the live table"),
         ("tefca_import_batches.errors", "Per-row import error detail",
          "Variable length; queried rarely, read as a whole")],
        widths=(1.6, 1.9, 3.0))
D.page_break()

# ── 5 ────────────────────────────────────────────────────────────────────────
D.h1("5. Enterprise Data Dictionary")
D.p("Every column of every documented table, extracted programmatically from "
    "the deployed models and DDL rather than transcribed by hand. Privacy "
    "classification, sensitivity, encryption and audit requirements are "
    "assigned per table in section 8 and apply to all columns of that table "
    "unless a column note states otherwise.")

DICT_COLS = ["Business Name", "Technical Name", "Type", "Null", "Key",
             "Default", "Notes"]
DICT_W = (1.35, 1.35, 1.05, 0.42, 0.42, 0.85, 1.16)

NOTES = {
    "id": "System-generated identifier",
    "created_at": "Set on insert",
    "updated_at": "Maintained on update",
    "is_deleted": "Soft delete flag — see §4.1",
    "deleted_at": "Set when soft-deleted",
    "confidence_score": "Null means never verified; 0.0 means verified and all "
                        "sources disagreed",
    "npi": "Ten digits; CMS check digit validated on import",
    "response_hash": "SHA-256 of the source payload for audit reproducibility",
    "rule_version": "Version of the rule that produced the classification",
    "errors": "Per-row import error detail",
    "password_hash": "bcrypt; never logged or exported",
    "tokens_revoked_at": "Sessions issued before this instant are invalid",
    "seed": "Reproduces the drawn sample exactly",
    "retired_date": "Set when a rule version is superseded; the row is kept",
    "effective_date": "Date from which this rule version applies",
}

documented = 0
for domain, tables in DOMAINS:
    D.h2(f"5.{DOMAINS.index((domain, tables)) + 1} {domain}")
    for tname in tables:
        meta = SCHEMA.get(tname)
        if not meta:
            D.h3(tname)
            D.p("Not present in the extracted schema.", italic=True)
            continue
        D.h3(f"{tname}  ({len(meta['columns'])} columns)")
        cls = CLASS.get(tname)
        if cls:
            D.p(f"Classification: {cls[0]}  ·  Sensitivity: {cls[1]}  ·  "
                f"Encryption required: {cls[2]}  ·  Audit required: {cls[3]}",
                bold=True, size=8.5)
        rows = []
        for c in meta["columns"]:
            note = NOTES.get(c["name"], "")
            if c["fk"]:
                note = (note + " " if note else "") + f"FK → {c['fk'][0]}"
            rows.append((business_name(c["name"]), c["name"], pgtype(c["type"]),
                         "N" if not c["nullable"] else "Y",
                         "PK" if c["pk"] else ("FK" if c["fk"] else ""),
                         (c["default"] or "")[:18], note))
        D.table(DICT_COLS, rows, DICT_W, font_size=7)
        documented += 1
D.page_break()

# ── 6 ────────────────────────────────────────────────────────────────────────
D.h1("6. Source-to-Target Data Mapping")
D.table(["Source", "Source Field", "Target", "Target Field", "Transform",
         "Validation"],
        [("ONC CSV", "TEFCAID", "tefca_entity_identifiers", "identifier_value",
          "type='tefcaid', is_primary=true", "Required; duplicates skipped"),
         ("ONC CSV", "HCID", "tefca_entity_identifiers", "identifier_value",
          "type='hcid'", "Required"),
         ("ONC CSV", "EntityName", "tefca_reg_entities", "name", "Trimmed",
          "Required, non-empty"),
         ("ONC CSV", "EntityLevel", "tefca_reg_entities", "entity_level",
          "Lowercased", "QHIN | participant | sub_participant"),
         ("ONC CSV", "NPI", "tefca_entity_identifiers", "identifier_value",
          "type='npi'", "CMS check digit — flags, never rejects"),
         ("ONC CSV", "ParentTEFCAID", "tefca_entity_relationships",
          "parent_entity_id", "Resolved to uuid in a second pass",
          "Unresolved parent recorded as a batch error"),
         ("ONC CSV", "State / City / ZIP", "tefca_reg_entities",
          "state / city / zip", "Direct", "State is two characters"),
         ("NPPES", "result_count", "tefca_verifications", "result.found",
          "> 0 becomes found=true", "Query success is not a verdict"),
         ("NPPES", "basic.organization_name", "tefca_verifications",
          "result.legal_name", "Direct", "Compared for name variance"),
         ("NPPES", "basic.status", "tefca_verifications", "result.status",
          "Mapped to active statuses", "'A' means active"),
         ("PECOS", "enrolment record", "tefca_verifications",
          "result.enrolled", "Resolved via the CMS NPI dataset",
          "No separate key-less PECOS endpoint exists"),
         ("OIG LEIE", "exclusion row", "tefca_verifications",
          "result.excluded", "Presence with no reinstatement date",
          "Absence means clear — the good outcome"),
         ("SAM.gov v3", "entityRegistration.registrationStatus",
          "tefca_verifications", "result.registration_current",
          "'ACTIVE' becomes true", "Requires a registered API key"),
         ("SAM.gov v4", "excludedEntity[]", "tefca_verifications",
          "result.excluded", "Any result means excluded",
          "Queried independently of v3 — see AGT-EX-001 §6.1"),
         ("TEFCA entity data", "ONC-provided extract", "tefca_reg_entities",
          "(various)", "Not implemented", "No direct access")],
        widths=(0.85, 1.35, 1.25, 1.05, 1.15, 1.35), font_size=7.5)
D.page_break()

# ── 7 ────────────────────────────────────────────────────────────────────────
D.h1("7. Data Ownership Matrix")
D.table(["Domain", "Business Owner", "Data Steward", "Source of Truth",
         "Update Frequency"],
        [(dom, *STEWARD[dom]) for dom, _ in DOMAINS],
        widths=(1.15, 1.5, 1.35, 1.55, 1.0))
D.p("Business Owner names the party accountable for the data being correct; "
    "Data Steward names the party who maintains it day to day. For the Entity "
    "domain these differ: ONC owns the correctness of the registry population "
    "because ONC supplies it, while AGT stewards it within the platform. AGT "
    "cannot correct a registry entry, only report it.")
D.page_break()

# ── 8 ────────────────────────────────────────────────────────────────────────
D.h1("8. Data Classification")
D.table(["Table", "Classification", "Sensitivity", "Encrypt", "Audit", "Basis"],
        [(t, *CLASS[t]) for _, ts in DOMAINS for t in ts if t in CLASS],
        widths=(1.45, 0.95, 0.7, 0.5, 0.45, 2.45), font_size=7.5)
D.callout("Classification is assigned per table by content, not inferred from "
          "the table name. review_rules holds methodology configuration and no "
          "entity data, so it is INTERNAL even though it sits in the Review "
          "domain; bulletin_cost_logs holds no personal data but is "
          "commercially sensitive and is audited for that reason.", "METHOD")
D.page_break()

# ── 9 ────────────────────────────────────────────────────────────────────────
D.h1("9. Data Retention and Archival")
D.table(["Table / Group", "Retention", "Archive Policy", "Purge Policy",
         "Basis"],
        [("review_records", "7 years", "Immutable; archived in place",
          "No purge before 7 years", "FAR / NARA contract records"),
         ("review_reports", "7 years", "Archived snapshot, never regenerated",
          "No purge before 7 years", "FAR / NARA"),
         ("review_samples / sample_entities", "7 years", "Archived in place",
          "With parent cycle", "Evidence for reported results"),
         ("tefca_reg_audit_log", "7 years", "Immutable, append-only",
          "No purge", "NIST AU-11"),
         ("tefca_verifications", "7 years", "Archived in place",
          "With parent entity", "Evidence for classifications"),
         ("tefca_reg_entities", "7 years after contract end",
          "Soft-deleted rows retained", "Government direction at closeout",
          "CUI disposition"),
         ("tefca_import_batches", "7 years", "Archived in place", "No purge",
          "Chain of custody for ingested data"),
         ("users", "Duration of access plus 3 years", "Deactivate, do not delete",
          "On government direction", "Audit attribution"),
         ("bulletin_articles", "2 years", "Rolling", "Automated purge",
          "Operational data, no contract obligation"),
         ("bulletin_run_log / outcome", "1 year", "Rolling", "Automated purge",
          "Telemetry")],
        widths=(1.45, 1.15, 1.35, 1.25, 1.3), font_size=7.5)
D.callout("Retention periods are specified but NOT yet enforced by automated "
          "archival or purge jobs. This is recorded as gap G-10 in AGT-REQ-001 "
          "Part F. Stating a retention policy that no mechanism implements "
          "would misrepresent the control.", "STATUS")
D.page_break()

# ── 10 ───────────────────────────────────────────────────────────────────────
D.h1("10. Data Lineage")
D.p("The path of a single registry record from ONC through to a delivered "
    "report. Each step names the table that holds the result, so any figure in "
    "a report can be traced back to the source response that produced it.")
D.table(["Step", "Action", "Lands in", "Retains"],
        [("1", "ONC supplies a CSV extract",
          "tefca_import_batches", "File checksum, size, row count, per-row "
          "errors"),
         ("2", "Rows parsed and validated", "tefca_reg_entities",
          "Entity core attributes"),
         ("3", "Identifiers normalised", "tefca_entity_identifiers",
          "NPI, TEFCAID, HCID with primary flag"),
         ("4", "Hierarchy resolved", "tefca_entity_relationships",
          "Parent-child links and relationship type"),
         ("5", "Initial snapshot written", "tefca_entity_versions",
          "Full entity state at version 1"),
         ("6", "Sample drawn", "review_samples / sample_entities",
          "Population, sample size, seed, drawn timestamp"),
         ("7", "Authoritative sources queried", "tefca_verifications",
          "Per-source status, raw payload, response hash, timestamp"),
         ("8", "Rules evaluated", "review_records",
          "Bucket, rule code, rule version, rationale"),
         ("9", "B3 resolved by a reviewer", "review_records",
          "Resolution, rationale, reviewer, timestamp, effective bucket"),
         ("10", "Report assembled", "review_reports",
          "Complete archived snapshot including limitations"),
         ("11", "Cycle closed", "review_cycles",
          "Links sample, report and period into one auditable unit"),
         ("12", "Every step above", "tefca_reg_audit_log",
          "Actor, action, entity, IP address, timestamp")],
        widths=(0.45, 1.9, 1.55, 2.6))
D.p("The property this delivers: given any classification in any delivered "
    "report, an auditor can retrieve the rule version that produced it, the "
    "exact source responses it was based on, the hash proving those responses "
    "are unaltered, and the sample and cycle that placed the entity under "
    "review.")
D.page_break()

# ── 11 ───────────────────────────────────────────────────────────────────────
D.h1("11. Validation Rules")
D.table(["Rule ID", "Table", "Field", "Rule", "On failure"],
        [("VAL-001", "tefca_entity_identifiers", "identifier_value (npi)",
          "Ten digits passing the CMS Luhn check digit",
          "Flagged and audited; the entity still imports"),
         ("VAL-002", "tefca_reg_entities", "entity_level",
          "One of QHIN, participant, sub_participant",
          "Row rejected; recorded in batch errors"),
         ("VAL-003", "tefca_reg_entities", "operational_status",
          "Transition permitted by the lifecycle state machine",
          "Refused with 400; the refusal is audited"),
         ("VAL-004", "review_records", "(row)",
          "Append-only; no update or delete",
          "Operation not exposed"),
         ("VAL-005", "tefca_verifications", "result",
          "Confidence counts only operational connectors",
          "Unimplemented sources report not_checked"),
         ("VAL-006", "tefca_entity_identifiers", "(identifier_type, value, "
          "system_uri)", "Unique", "Duplicate skipped; recorded as a batch "
          "error"),
         ("VAL-007", "tefca_reg_entities", "name", "Non-empty after trimming",
          "Row rejected"),
         ("VAL-008", "tefca_reg_entities", "state",
          "Two-character code where present", "Stored as supplied; not blocking"),
         ("VAL-009", "review_rules", "priority",
          "Disqualifying rules must sort before all others",
          "Enforced by seeded priority 5"),
         ("VAL-010", "review_rules", "version",
          "A superseded version is retired, never deleted",
          "Retirement sets retired_date and clears is_active"),
         ("VAL-011", "review_samples", "seed",
          "Present and reproduces the sample", "Sample rejected without a seed"),
         ("VAL-012", "review_samples", "sample_size",
          "Cochran value with finite population correction",
          "Computed, never supplied by hand"),
         ("VAL-013", "review_reports", "report_data.limitations",
          "Non-empty", "Report generation fails"),
         ("VAL-014", "review_reports", "report_data",
          "Immutable once written", "Exports read the archive, never recompute"),
         ("VAL-015", "users", "password_hash", "bcrypt, never plaintext",
          "Set only through the hashing helper"),
         ("VAL-016", "users", "email", "Unique, case-insensitive",
          "Duplicate rejected"),
         ("VAL-017", "tefca_reg_entities", "is_deleted",
          "Delete is soft and not silently idempotent",
          "Re-deleting returns 409"),
         ("VAL-018", "tefca_reg_entities", "confidence_score",
          "Null when nothing answered", "Never defaulted to 0.0"),
         ("VAL-019", "tefca_verifications", "status",
          "One of verified, not_found, not_checked, unavailable, failed",
          "Unknown status rejected"),
         ("VAL-020", "tefca_verifications", "response_hash",
          "SHA-256 of the payload", "Recorded on every source response"),
         ("VAL-021", "tefca_import_batches", "errors",
          "Length must equal error_count",
          "A count with no detail is unusable to an auditor"),
         ("VAL-022", "tefca_entity_relationships", "parent_entity_id",
          "Must resolve to an existing entity",
          "Unresolved parent recorded as a batch error"),
         ("VAL-023", "tefca_reg_audit_log", "(row)",
          "Append-only; an audit write never aborts its transaction",
          "Failure logged, action proceeds"),
         ("VAL-024", "tefca_entity_versions", "snapshot_data",
          "Written on creation and on change", "Missing snapshot is a defect")],
        widths=(0.62, 1.35, 1.25, 1.85, 1.43), font_size=7.5)
D.page_break()

D.h1("Revision History")
D.table(["Version", "Date", "Author", "Description"],
        [("1.0", "August 2026", "Imran Siddiqui", "Initial release")],
        widths=(0.9, 1.3, 1.7, 2.6))

path = OUT / "AGT-DA-001_Data_Architecture.docx"
D.save(path)
print(f"saved {path.name}: {path.stat().st_size:,} bytes")
print(f"  tables documented in the data dictionary: {documented}")
