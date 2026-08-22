"""
P1 — the locked 41-field map for the ONC/RCE delivery.

EVERY ENTRY IS GROUNDED IN THE PROFILED FILE
────────────────────────────────────────────
Profiled from the actual delivery on 2026-08-21:

    onc-snapshot-20260720.csv
    sha256 689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d
    10,042,400 bytes · 23,566 data records · pipe-delimited · UTF-8 · CRLF
    41 fields on 23,566 of 23,566 rows (zero field-count mismatches)

Three kinds of statement are kept separate throughout, because conflating them
is how a column name becomes a compliance conclusion:

    OBSERVED     what the profiled bytes actually contain. Counted, not inferred.
    DOCUMENTED   what the RCE/TEFCA specification says the field means. Where no
                 specification text is in hand, this says so rather than
                 guessing from the column name.
    DOCUACTION   how this system chooses to use the field, and why.

WHERE THE REAL FILE CONTRADICTED THE PRE-DELIVERY FIXTURES
──────────────────────────────────────────────────────────
These are corrections to assumptions the mock fixtures encoded. Each one would
have produced a wrong result if carried forward unexamined:

 1. `id` is the unique per-record key — 23,566 distinct values, zero duplicates.
    `TEFCAID` is NOT unique: 43 values repeat across 241 extra rows, one of them
    69 times. The 69 rows are "Atrium Health" and 68 of its facilities. TEFCAID
    therefore identifies an ORGANISATION FAMILY, not a record. Using it as the
    primary identity key would have merged 241 distinct organisations into 43.
 2. `HCID` is populated on 100% of records, not "missing on some". It is
    `urn:oid:` + `id` on 23,561 of 23,566 rows.
 3. NO MOJIBAKE. Zero rows carry any UTF-8-through-CP1252 marker; the file
    decodes cleanly as strict UTF-8. The encoding-corruption scenario the
    fixtures modelled does not occur in this delivery. The detector is retained
    because a future delivery may differ, but it currently fires on nothing.
 4. Embedded TABs appear in `address_line` (4 cells), not `address_text`.
 5. `organizationNodeType` is `initiating-node`, not initiator/passthrough/no
    node. Present on 2 of 23,566 records.
 6. `initiatoronly` is `OTHER`, not a 0/1 flag. Present on 5 records.
 7. `address_text` is the literal label "Primary" on 17,717 records (75.2%). It
    is not an address on those rows and must never be parsed as one.
 8. ZIP leading zeros are stripped on 1,627 records ("2718" for 02718) —
    the classic spreadsheet round-trip defect.
 9. `orgManagingOrg` resolves to NO record in the file. The 11 QHINs are
    external referents, not rows.
10. `transaction`, `NAIC`, `CCN`, `alias`, `email`, `contact_company` are 100%
    empty across all 23,566 records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Provenance of this map ───────────────────────────────────────────────────

PROFILED_FILE = "onc-snapshot-20260720.csv"
PROFILED_SHA256 = "689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d"
PROFILED_RECORD_COUNT = 23_566
PROFILED_AT = "2026-08-21"

#: Bump when a mapping decision changes. Recorded on every ingestion run and
#: every curated record, so a transformation can be traced to the rules in force.
FIELD_MAP_VERSION = "1.0.0"

RCE_DELIMITER = "|"
RCE_ENCODING = "utf-8"


# ── Classification vocabularies ──────────────────────────────────────────────

class Necessity:
    REQUIRED = "REQUIRED"                    # ingestion cannot proceed without it
    CONDITIONAL = "CONDITIONAL"              # required given some other condition
    OPTIONAL = "OPTIONAL"                    # may be absent, no condition
    LEGITIMATELY_NULLABLE = "LEGITIMATELY_NULLABLE"  # absence is normal & meaningful


class Role:
    IDENTIFIER = "IDENTIFIER"
    RELATIONSHIP = "RELATIONSHIP"
    PII_CONTACT = "PII_CONTACT"
    EVIDENCE_BEARING = "EVIDENCE_BEARING"
    OPERATIONAL_METADATA = "OPERATIONAL_METADATA"
    ADDRESS = "ADDRESS"
    DESCRIPTIVE = "DESCRIPTIVE"


class Target:
    """Where the value lands once promoted to the canonical registry."""
    ENTITY_COLUMN = "ENTITY_COLUMN"
    IDENTIFIER_ROW = "IDENTIFIER_ROW"
    RELATIONSHIP_EDGE = "RELATIONSHIP_EDGE"
    CONTACT_ROW = "CONTACT_ROW"
    ENTITY_JSONB = "ENTITY_JSONB"
    NOT_PROMOTED = "NOT_PROMOTED"     # preserved in Area 1 only


@dataclass(frozen=True)
class FieldSpec:
    """One RCE column, fully specified."""

    name: str
    ordinal: int
    observed: str
    documented: str
    docuaction: str
    necessity: str
    role: str
    target: str
    #: Registry column / identifier type / relationship type, per `target`.
    target_key: Optional[str] = None
    #: Evidence dimensions this field feeds. Empty means it bears no evidence.
    dimensions: Tuple[str, ...] = ()
    #: Data-quality rule ids that evaluate this field (see quality_rules.py).
    validation: Tuple[str, ...] = ()
    #: Observed population count in the profiled delivery.
    populated: int = 0
    #: Observed distinct non-empty values.
    distinct: int = 0

    @property
    def empty(self) -> int:
        return PROFILED_RECORD_COUNT - self.populated

    @property
    def coverage_pct(self) -> float:
        return round(self.populated / PROFILED_RECORD_COUNT * 100, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "ordinal": self.ordinal,
            "observed": self.observed, "documented": self.documented,
            "docuaction": self.docuaction, "necessity": self.necessity,
            "role": self.role, "target": self.target, "target_key": self.target_key,
            "dimensions": list(self.dimensions), "validation": list(self.validation),
            "populated": self.populated, "empty": self.empty,
            "coverage_pct": self.coverage_pct, "distinct": self.distinct,
        }


_UNDOCUMENTED = (
    "No RCE/TEFCA specification text for this field is in DocuAction's "
    "possession. Meaning is NOT inferred from the column name."
)

FIELD_SPECS: Tuple[FieldSpec, ...] = (
    FieldSpec(
        "id", 0,
        observed="23,566/23,566 populated, 23,566 distinct — the only column with "
                 "no duplicates. 23,565 match an OID pattern; 1 is a bare UUID. "
                 "Lengths 17-54.",
        documented=_UNDOCUMENTED + " Structurally an HL7 OID.",
        docuaction="THE canonical source-record identity key. Chosen over TEFCAID "
                   "because it is the only field that is actually unique. Becomes "
                   "the registry's rce_organization_id and the resolution target "
                   "for partOf.",
        necessity=Necessity.REQUIRED, role=Role.IDENTIFIER,
        target=Target.IDENTIFIER_ROW, target_key="rce_org_oid",
        dimensions=("D1_IDENTITY", "D5_TEFCA_ALIGNMENT"),
        validation=("SCH-001", "ID-001"), populated=23566, distinct=23566,
    ),
    FieldSpec(
        "domains", 1,
        observed="Constant 'RCE' on all 23,566 records. Single distinct value.",
        documented=_UNDOCUMENTED,
        docuaction="Delivery-scope marker. Carries no per-entity information, so "
                   "it is preserved in Area 1 and not promoted.",
        necessity=Necessity.OPTIONAL, role=Role.OPERATIONAL_METADATA,
        target=Target.NOT_PROMOTED, validation=("CON-001",),
        populated=23566, distinct=1,
    ),
    FieldSpec(
        "initiatoronly", 2,
        observed="Populated on 5 of 23,566 (0.02%). Sole value 'OTHER' — NOT a "
                 "boolean, contrary to the pre-delivery assumption.",
        documented=_UNDOCUMENTED,
        docuaction="Too sparse and too undocumented to drive any rule. Preserved "
                   "verbatim; no interpretation applied.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.OPERATIONAL_METADATA,
        target=Target.ENTITY_JSONB, target_key="rce_attributes",
        populated=5, distinct=1,
    ),
    FieldSpec(
        "orgManagingOrg", 3,
        observed="23,566/23,566 populated, 11 distinct OIDs. Resolves to ZERO "
                 "records in the file — the QHINs are external referents. "
                 "Largest: 2.16.840.1.113883.4.391.1000 (10,481 records).",
        documented=_UNDOCUMENTED + " Observed to behave as the managing QHIN.",
        docuaction="QHIN attribution. Creates a managed_by_qhin edge to a QHIN "
                   "entity created from this OID. NEVER treated as the "
                   "Participant parent — that is partOf.",
        necessity=Necessity.REQUIRED, role=Role.RELATIONSHIP,
        target=Target.RELATIONSHIP_EDGE, target_key="managed_by_qhin",
        dimensions=("D5_TEFCA_ALIGNMENT", "D6_PROVIDER_ORG_RELATIONSHIP"),
        validation=("INT-001",), populated=23566, distinct=11,
    ),
    FieldSpec(
        "purposesofuse", 4,
        observed="23,154/23,566 (98.25%). 16 distinct combinations, 11 distinct "
                 "tokens. T-TRTMNT on 22,748. Vocabulary is INCONSISTENT: both "
                 "T-TRTMNT (22,748) and T-TREAT (13) occur, as do T-PH (8) and "
                 "T-PH-ECR (1,855). 412 records carry none.",
        documented=_UNDOCUMENTED + " Tokens resemble HL7 PurposeOfUse codes.",
        docuaction="Exchange Purpose for D5. Multi-valued, comma-separated. Never "
                   "inferred and never derived from Medicare data. The "
                   "T-TRTMNT/T-TREAT inconsistency is REPORTED as an issue, not "
                   "silently normalised — collapsing them would assert an "
                   "equivalence the RCE has not stated.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.EVIDENCE_BEARING,
        target=Target.ENTITY_JSONB, target_key="exchange_purposes",
        dimensions=("D5_TEFCA_ALIGNMENT",),
        validation=("CON-002", "BUS-001"), populated=23154, distinct=16,
    ),
    FieldSpec(
        "stateofoperation", 5,
        observed="7 of 23,566 (0.03%). Comma-separated state lists "
                 "('AZ,MN,NM'). Max length 167.",
        documented=_UNDOCUMENTED,
        docuaction="Too sparse to drive applicability. Preserved verbatim.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.DESCRIPTIVE,
        target=Target.ENTITY_JSONB, target_key="rce_attributes",
        populated=7, distinct=7,
    ),
    FieldSpec(
        "doa", 6,
        observed="105 of 23,566 (0.45%), 32 distinct. OID-shaped.",
        documented=_UNDOCUMENTED + " Name suggests a Designated Organisation "
                                   "Authority reference; NOT confirmed.",
        docuaction="Preserved verbatim. No rule depends on it, because its "
                   "meaning is unconfirmed and 99.55% of records lack it.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.OPERATIONAL_METADATA,
        target=Target.ENTITY_JSONB, target_key="rce_attributes",
        populated=105, distinct=32,
    ),
    FieldSpec(
        "transaction", 7,
        observed="EMPTY on all 23,566 records.",
        documented=_UNDOCUMENTED,
        docuaction="Structurally present, semantically absent in this delivery. "
                   "Preserved so a later delivery that populates it is visibly "
                   "different rather than silently new.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.OPERATIONAL_METADATA,
        target=Target.NOT_PROMOTED, validation=("SCH-002",),
        populated=0, distinct=0,
    ),
    FieldSpec(
        "delegationRole", 8,
        observed="2 of 23,566. Sole value 'principal'.",
        documented=_UNDOCUMENTED,
        docuaction="Preserved verbatim; too sparse to interpret.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.OPERATIONAL_METADATA,
        target=Target.ENTITY_JSONB, target_key="rce_attributes",
        populated=2, distinct=1,
    ),
    FieldSpec(
        "organizationNodeType", 9,
        observed="2 of 23,566. Sole value 'initiating-node'. NOT the "
                 "initiator/passthrough/no-node vocabulary previously assumed.",
        documented=_UNDOCUMENTED + " Describes technical exchange behaviour.",
        docuaction="TECHNICAL EXCHANGE BEHAVIOUR ONLY. Never read as the TEFCA "
                   "hierarchy and never used to derive the entity's TEFCA class — "
                   "that is sequoiaorgtype's job exclusively. Enforced by test.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.OPERATIONAL_METADATA,
        target=Target.ENTITY_COLUMN, target_key="org_node_type",
        populated=2, distinct=1,
    ),
    FieldSpec(
        "NPI", 10,
        observed="18,982 of 23,566 (80.55%) populated; 4,584 EMPTY. 18,675 "
                 "distinct. Defects: 4 with length != 10 (one cell holds TWO "
                 "comma-separated NPIs, one is 6 digits, one 9); 2 ten-digit "
                 "values fail the CMS check digit; 307 NPIs are shared by more "
                 "than one record.",
        documented="National Provider Identifier. NPPES is the identity authority.",
        docuaction="An EMPTY NPI is legitimate and never a failure — 19.45% of "
                   "TEFCA entities here hold none. Promoted as an identifier row "
                   "only when present and well-formed; malformed values raise an "
                   "issue and are preserved unaltered.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.IDENTIFIER,
        target=Target.IDENTIFIER_ROW, target_key="npi",
        dimensions=("D1_IDENTITY", "D2_MEDICARE_ENROLLMENT",
                    "D3_EXCLUSION_REVOCATION", "D6_PROVIDER_ORG_RELATIONSHIP"),
        validation=("NPI-001", "NPI-002", "NPI-003"),
        populated=18982, distinct=18675,
    ),
    FieldSpec(
        "NAIC", 11,
        observed="EMPTY on all 23,566 records.",
        documented="Payer identifier (National Association of Insurance "
                   "Commissioners) where supplied.",
        docuaction="Not present in this delivery. No rule depends on it.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.IDENTIFIER,
        target=Target.IDENTIFIER_ROW, target_key="naic",
        validation=("SCH-002",), populated=0, distinct=0,
    ),
    FieldSpec(
        "CCN", 12,
        observed="EMPTY on all 23,566 records.",
        documented="CMS Certification Number where supplied.",
        docuaction="Not present in this delivery. No rule depends on it.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.IDENTIFIER,
        target=Target.IDENTIFIER_ROW, target_key="ccn",
        validation=("SCH-002",), populated=0, distinct=0,
    ),
    FieldSpec(
        "HCID", 13,
        observed="23,566/23,566 populated (100%) — contradicting the "
                 "pre-delivery assumption that HCID is missing on some records. "
                 "23,562 distinct; 4 values duplicated. Equals 'urn:oid:' + id "
                 "on 23,561 of 23,566 rows.",
        documented="Home Community ID.",
        docuaction="Promoted as an hcid identifier row. Because it is fully "
                   "populated here, its absence in a FUTURE delivery would be "
                   "the exception and is flagged INFORMATIONAL, not assumed "
                   "fatal.",
        necessity=Necessity.CONDITIONAL, role=Role.IDENTIFIER,
        target=Target.IDENTIFIER_ROW, target_key="hcid",
        dimensions=("D5_TEFCA_ALIGNMENT",),
        validation=("ID-002", "ID-003"), populated=23566, distinct=23562,
    ),
    FieldSpec(
        "AAID", 14,
        observed="7,447 of 23,566 (31.60%). 7,444 distinct. Equals HCID on "
                 "7,423 of the 7,447 rows where present.",
        documented=_UNDOCUMENTED + " Structurally an OID.",
        docuaction="Promoted as an aaid identifier row when present. Absence is "
                   "normal for 68% of records and raises no issue.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.IDENTIFIER,
        target=Target.IDENTIFIER_ROW, target_key="aaid",
        dimensions=("D5_TEFCA_ALIGNMENT",),
        validation=("ID-004",), populated=7447, distinct=7444,
    ),
    FieldSpec(
        "TEFCAID", 15,
        observed="23,566/23,566 populated, uniform 45-char urn:uuid format, but "
                 "only 23,325 DISTINCT. 43 values repeat across 241 extra rows; "
                 "one value appears 69 times ('Atrium Health' plus 68 of its "
                 "facilities), another 22 times ('Sentara Health' plus "
                 "facilities).",
        documented="TEFCA identifier.",
        docuaction="NOT a per-record identity key — the observed duplication is "
                   "an organisation FAMILY sharing one TEFCAID. Using it as the "
                   "primary key would have merged 241 distinct organisations "
                   "into 43. Promoted as a NON-UNIQUE tefcaid identifier row; "
                   "`id` is the identity key. Shared values raise an "
                   "INFORMATIONAL issue, not a duplicate-entity finding.",
        necessity=Necessity.REQUIRED, role=Role.IDENTIFIER,
        target=Target.IDENTIFIER_ROW, target_key="tefcaid",
        dimensions=("D5_TEFCA_ALIGNMENT",),
        validation=("ID-005", "ID-006"), populated=23566, distinct=23325,
    ),
    FieldSpec(
        "active", 16,
        observed="23,566/23,566. '1' on 22,594 (95.88%), '0' on 972 (4.12%). "
                 "Inactive split: 867 Subparticipants, 105 Participants.",
        documented="Entity active flag.",
        docuaction="active=0 maps to operational_status='inactive' and "
                   "is_active=false. An inactive entity is a legitimate "
                   "reportable state — the record is promoted, never dropped.",
        necessity=Necessity.REQUIRED, role=Role.EVIDENCE_BEARING,
        target=Target.ENTITY_COLUMN, target_key="operational_status",
        dimensions=("D5_TEFCA_ALIGNMENT",),
        validation=("CON-003",), populated=23566, distinct=2,
    ),
    FieldSpec(
        "sequoiaorgtype", 17,
        observed="23,566/23,566. Exactly two values: Subparticipant (12,489) "
                 "and Participant (11,077).",
        documented="Organisational classification within TEFCA.",
        docuaction="THE authority for the entity's TEFCA class. Maps to "
                   "entity_level participant / sub_participant. "
                   "organizationNodeType is never substituted for it.",
        necessity=Necessity.REQUIRED, role=Role.EVIDENCE_BEARING,
        target=Target.ENTITY_COLUMN, target_key="entity_level",
        dimensions=("D5_TEFCA_ALIGNMENT",),
        validation=("REQ-001", "CON-004"), populated=23566, distinct=2,
    ),
    FieldSpec(
        "hl7orgrole", 18,
        observed="60 of 23,566 (0.25%). 5 distinct: provider (52), diagnostics "
                 "(4), agency (2), payer (1), HIE/HIO (1).",
        documented=_UNDOCUMENTED + " Resembles an HL7 organisation role.",
        docuaction="Where present, contributes a NON-PROVIDER signal to "
                   "applicability only (payer / agency / HIE-HIO). It can relax "
                   "a Medicare obligation but can never impose one — a "
                   "0.25%-populated field must not create requirements.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.EVIDENCE_BEARING,
        target=Target.ENTITY_COLUMN, target_key="hl7_org_role",
        dimensions=("D2_MEDICARE_ENROLLMENT",),
        populated=60, distinct=5,
    ),
    FieldSpec(
        "name", 19,
        observed="23,566/23,566. 23,284 distinct — 282 names are shared. "
                 "Lengths 4-99. 9 names match a test-artefact pattern, "
                 "including 'ELLKAY-DOA-TEST'.",
        documented="Organisation name.",
        docuaction="Entity name. Also the key for organisation-level OIG/SAM "
                   "screening when no NPI exists. Never auto-corrected: a name "
                   "change is an identity change and is HUMAN_REQUIRED.",
        necessity=Necessity.REQUIRED, role=Role.EVIDENCE_BEARING,
        target=Target.ENTITY_COLUMN, target_key="name",
        dimensions=("D1_IDENTITY", "D3_EXCLUSION_REVOCATION"),
        validation=("REQ-002", "BUS-002"), populated=23566, distinct=23284,
    ),
    FieldSpec(
        "alias", 20,
        observed="EMPTY on all 23,566 records.",
        documented="Alternate organisation name.",
        docuaction="Not present in this delivery.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.DESCRIPTIVE,
        target=Target.ENTITY_COLUMN, target_key="display_name",
        validation=("SCH-002",), populated=0, distinct=0,
    ),
    FieldSpec(
        "phone", 21,
        observed="84 of 23,566 (0.36%). 82 distinct. Uniform 14-char "
                 "'(NNN) NNN-NNNN'.",
        documented="Organisation phone.",
        docuaction="Preserved. Not evidence-bearing.",
        necessity=Necessity.OPTIONAL, role=Role.DESCRIPTIVE,
        target=Target.ENTITY_JSONB, target_key="rce_attributes",
        populated=84, distinct=82,
    ),
    FieldSpec(
        "email", 22,
        observed="EMPTY on all 23,566 records.",
        documented="Organisation email.",
        docuaction="Not present in this delivery.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.DESCRIPTIVE,
        target=Target.NOT_PROMOTED, validation=("SCH-002",),
        populated=0, distinct=0,
    ),
    FieldSpec(
        "address_text", 23,
        observed="23,566/23,566 'populated' but only 5,549 distinct, and the "
                 "single value 'Primary' occupies 17,717 rows (75.18%). On "
                 "those rows it is a LABEL, not an address.",
        documented=_UNDOCUMENTED,
        docuaction="NOT used as an address. The 75% 'Primary' population makes "
                   "it unusable as one, and parsing it would fabricate "
                   "addresses. The component fields (address_line/city/state/"
                   "postalCode) are the address of record. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.ADDRESS,
        target=Target.ENTITY_JSONB, target_key="rce_attributes",
        validation=("CON-005", "FMT-004"), populated=23566, distinct=5549,
    ),
    FieldSpec(
        "address_line", 24,
        observed="23,566/23,566. 22,936 distinct. 4 cells contain an embedded "
                 "TAB character.",
        documented="Street address.",
        docuaction="The street line of the address of record. Feeds D4.",
        necessity=Necessity.REQUIRED, role=Role.ADDRESS,
        target=Target.ENTITY_COLUMN, target_key="address",
        dimensions=("D4_ADDRESS",),
        validation=("FMT-004", "REQ-003"), populated=23566, distinct=22936,
    ),
    FieldSpec(
        "address_city", 25,
        observed="23,566/23,566. 5,333 distinct.",
        documented="City.",
        docuaction="Address of record. Feeds D4.",
        necessity=Necessity.REQUIRED, role=Role.ADDRESS,
        target=Target.ENTITY_COLUMN, target_key="city",
        dimensions=("D4_ADDRESS",), validation=("REQ-003",),
        populated=23566, distinct=5333,
    ),
    FieldSpec(
        "address_state", 26,
        observed="23,566/23,566. 54 distinct, ALL valid USPS state or territory "
                 "codes. Zero invalid values.",
        documented="State.",
        docuaction="Address of record. Feeds D4 and the ZIP/state consistency "
                   "rule.",
        necessity=Necessity.REQUIRED, role=Role.ADDRESS,
        target=Target.ENTITY_COLUMN, target_key="state",
        dimensions=("D4_ADDRESS",), validation=("FMT-002", "REQ-003"),
        populated=23566, distinct=54,
    ),
    FieldSpec(
        "address_postalCode", 27,
        observed="23,566/23,566. 9,311 distinct. 1,627 values are SHORTER than "
                 "5 characters (1,595 four-char, 32 three-char) — leading zeros "
                 "stripped by a spreadsheet round-trip. No non-numeric values.",
        documented="Postal code.",
        docuaction="Zero-padding to 5 digits is deterministic and "
                   "non-substantive, so it is the one address transformation "
                   "classified AUTO_SAFE. The ORIGINAL value is preserved in "
                   "Area 1 and in the correction record. A ZIP is never used to "
                   "infer or rewrite city/state.",
        necessity=Necessity.REQUIRED, role=Role.ADDRESS,
        target=Target.ENTITY_COLUMN, target_key="zip",
        dimensions=("D4_ADDRESS",),
        validation=("FMT-001", "FMT-003", "REQ-003"),
        populated=23566, distinct=9311,
    ),
    FieldSpec(
        "address_country", 28,
        observed="Constant 'US' on all 23,566 records.",
        documented="Country.",
        docuaction="Preserved. Constant, so it carries no per-entity signal.",
        necessity=Necessity.OPTIONAL, role=Role.ADDRESS,
        target=Target.ENTITY_JSONB, target_key="rce_attributes",
        populated=23566, distinct=1,
    ),
    FieldSpec(
        "partOf", 29,
        observed="23,566/23,566 populated, 300 distinct. Resolves to an `id` in "
                 "the file on 12,474 rows. For all 11,077 Participants, partOf "
                 "EQUALS orgManagingOrg (the QHIN). For 12,474 Subparticipants "
                 "it names a Participant's `id`. 15 Subparticipants have "
                 "partOf == orgManagingOrg, i.e. a QHIN parent.",
        documented="Parent organisation reference.",
        docuaction="The TEFCA hierarchy edge. For a Subparticipant it resolves "
                   "to its Participant and creates sub_participant_of. For a "
                   "Participant it repeats the QHIN and creates NO second edge — "
                   "orgManagingOrg already expresses that relationship, and "
                   "emitting both would double-count the same fact. The 15 "
                   "Subparticipants parented directly to a QHIN raise an issue "
                   "for analyst determination.",
        necessity=Necessity.REQUIRED, role=Role.RELATIONSHIP,
        target=Target.RELATIONSHIP_EDGE, target_key="sub_participant_of",
        dimensions=("D5_TEFCA_ALIGNMENT", "D6_PROVIDER_ORG_RELATIONSHIP"),
        validation=("INT-002", "INT-003", "BUS-003"),
        populated=23566, distinct=300,
    ),
    FieldSpec(
        "contact_company", 30,
        observed="EMPTY on all 23,566 records.",
        documented="Contact organisation name.",
        docuaction="Not present in this delivery.",
        necessity=Necessity.LEGITIMATELY_NULLABLE, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="company",
        validation=("SCH-002",), populated=0, distinct=0,
    ),
    FieldSpec(
        "contact_purpose", 31,
        observed="17,196 of 23,566 (72.97%). Single value 'ADMIN'.",
        documented="Purpose of the contact record.",
        docuaction="Contact row attribute. PII-gated.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="contact_purpose",
        populated=17196, distinct=1,
    ),
    FieldSpec(
        "contact_name", 32,
        observed="18,240 of 23,566 (77.40%). 10,876 distinct. Named individuals.",
        documented="Contact person.",
        docuaction="PII. Stored in the contacts table, gated behind the same "
                   "role floor as other PII projections, and never rendered in "
                   "an unauthenticated response.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="name",
        populated=18240, distinct=10876,
    ),
    FieldSpec(
        "contact_phone", 33,
        observed="17,775 of 23,566 (75.43%) but only 34 distinct — contacts are "
                 "overwhelmingly shared. Lengths 5-19; some are fragments such "
                 "as '-4781'.",
        documented="Contact phone.",
        docuaction="PII. Preserved verbatim; fragment values raise an "
                   "INFORMATIONAL issue and are never reconstructed.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="phone",
        validation=("FMT-005",), populated=17775, distinct=34,
    ),
    FieldSpec(
        "contact_email", 34,
        observed="17,779 of 23,566 (75.44%), 37 distinct.",
        documented="Contact email.",
        docuaction="PII. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="email",
        validation=("FMT-006",), populated=17779, distinct=37,
    ),
    FieldSpec(
        "contact_address_text", 35,
        observed="163 of 23,566 (0.69%), 14 distinct.",
        documented="Contact address, free text.",
        docuaction="PII. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="address_text",
        populated=163, distinct=14,
    ),
    FieldSpec(
        "contact_address_line", 36,
        observed="7,191 of 23,566 (30.51%), 64 distinct.",
        documented="Contact street address.",
        docuaction="PII. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="address_line",
        populated=7191, distinct=64,
    ),
    FieldSpec(
        "contact_address_city", 37,
        observed="7,191 of 23,566 (30.51%), 62 distinct.",
        documented="Contact city.", docuaction="PII. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="address_city",
        populated=7191, distinct=62,
    ),
    FieldSpec(
        "contact_address_state", 38,
        observed="7,191 of 23,566 (30.51%), 26 distinct. Max length 8 — some "
                 "values are not 2-letter codes.",
        documented="Contact state.", docuaction="PII. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="address_state",
        populated=7191, distinct=26,
    ),
    FieldSpec(
        "contact_address_postalCode", 39,
        observed="7,191 of 23,566 (30.51%), 61 distinct. Same leading-zero "
                 "stripping as the entity ZIP.",
        documented="Contact postal code.", docuaction="PII. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="address_postal_code",
        populated=7191, distinct=61,
    ),
    FieldSpec(
        "contact_address_country", 40,
        observed="7,183 of 23,566 (30.48%). Constant 'US'.",
        documented="Contact country.", docuaction="PII. Preserved verbatim.",
        necessity=Necessity.OPTIONAL, role=Role.PII_CONTACT,
        target=Target.CONTACT_ROW, target_key="address_country",
        populated=7183, distinct=1,
    ),
)

#: Column names in delivered order. The schema fingerprint is taken over this.
RCE_FIELDS: Tuple[str, ...] = tuple(spec.name for spec in FIELD_SPECS)
RCE_FIELD_COUNT = len(RCE_FIELDS)

FIELD_BY_NAME: Dict[str, FieldSpec] = {spec.name: spec for spec in FIELD_SPECS}

assert RCE_FIELD_COUNT == 41, f"the RCE map must describe 41 fields, has {RCE_FIELD_COUNT}"


# ── Vocabularies OBSERVED in the delivery ────────────────────────────────────
#
# These are the values the profiled file actually contains. They are NOT
# asserted to be the complete RCE vocabulary — a future delivery may carry a
# value none of these sets holds, which is why every rule that consults them
# treats an unknown value as "raise an issue for a human", never as invalid.

OBSERVED_SEQUOIA_ORG_TYPES = ("Participant", "Subparticipant")
OBSERVED_ACTIVE_VALUES = ("0", "1")
OBSERVED_DOMAINS = ("RCE",)
OBSERVED_CONTACT_PURPOSES = ("ADMIN",)
OBSERVED_ORG_NODE_TYPES = ("initiating-node",)
OBSERVED_DELEGATION_ROLES = ("principal",)
OBSERVED_HL7_ORG_ROLES = ("provider", "diagnostics", "agency", "payer", "HIE/HIO")
OBSERVED_PURPOSE_TOKENS = (
    "T-TRTMNT", "T-PH-ECR", "T-GOVDTRM-ACP", "T-IAS", "T-TREAT", "T-PH",
    "T-GOVDTRM", "T-HCO", "T-PH-ELR", "T-PYMNT", "T-GOVDTRM-SSD",
)

#: Purpose tokens that look like variants of a more common token. Reported, NOT
#: merged: asserting T-TREAT means T-TRTMNT would put a claim in the audit trail
#: that the RCE never made.
SUSPECTED_PURPOSE_VARIANTS = {
    "T-TREAT": "T-TRTMNT",
    "T-PH": "T-PH-ECR",
}

#: hl7orgrole values that indicate a NON-provider organisation. Used only to
#: relax Medicare applicability, never to impose it.
NON_PROVIDER_HL7_ROLES = {"payer", "agency", "HIE/HIO"}

#: The 11 QHIN OIDs observed in orgManagingOrg. None of them is a record in the
#: file; they are external referents and QHIN entities are synthesised for them.
OBSERVED_QHIN_OIDS = (
    "2.16.840.1.113883.4.391.1000",
    "2.16.840.1.113883.3.9960",
    "1.2.840.114350.1.72.69847383.2",
    "2.16.840.1.113883.3.432.0.16.500",
    "1.3.6.1.4.1.52618.1",
    "2.16.840.1.113883.3.3126",
    "2.16.840.1.113883.3.9415",
    "2.16.840.1.113883.3.7204.1.2.1.1.1.1",
    "2.16.840.1.113883.3.2054.6",
    "2.16.840.1.113883.3.13.11.1.1",
    "2.16.840.1.113883.3.3569",
)


# ── Derived views ────────────────────────────────────────────────────────────

def fields_by_necessity(necessity: str) -> List[FieldSpec]:
    return [s for s in FIELD_SPECS if s.necessity == necessity]


def fields_by_role(role: str) -> List[FieldSpec]:
    return [s for s in FIELD_SPECS if s.role == role]


def identifier_fields() -> List[FieldSpec]:
    return [s for s in FIELD_SPECS if s.target == Target.IDENTIFIER_ROW]


def contact_fields() -> List[FieldSpec]:
    return [s for s in FIELD_SPECS if s.target == Target.CONTACT_ROW]


def fields_for_dimension(dimension: str) -> List[FieldSpec]:
    return [s for s in FIELD_SPECS if dimension in s.dimensions]


def empty_in_delivery() -> List[str]:
    """Columns with zero populated values in the profiled delivery."""
    return [s.name for s in FIELD_SPECS if s.populated == 0]


def mapping_table() -> List[Dict[str, Any]]:
    """The P1 map as rows, for the report and the API."""
    return [spec.to_dict() for spec in FIELD_SPECS]


def schema_fingerprint(headers: List[str]) -> str:
    """SHA-256 over the ordered, lower-cased header list.

    Order is part of the fingerprint because a positional parser depends on it.
    Two deliveries with the same columns in a different order are NOT the same
    schema, and treating them as such would silently transpose every value.
    """
    import hashlib

    joined = "|".join(h.strip().lower() for h in headers)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


#: Fingerprint of the profiled delivery's header. A delivery whose fingerprint
#: differs is flagged as schema drift and held for review rather than parsed
#: against a map that may no longer describe it.
EXPECTED_SCHEMA_FINGERPRINT = schema_fingerprint(list(RCE_FIELDS))
