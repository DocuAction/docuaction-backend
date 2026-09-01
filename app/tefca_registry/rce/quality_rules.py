"""
P4 — the data-quality rule set. Configuration-driven, versioned, deterministic.

SEVERITY IS DERIVED FROM THE PROFILE, NOT FROM INTUITION
────────────────────────────────────────────────────────
Every severity here is justified by what P0 actually measured. Two decisions in
particular were made against the profile rather than against the column name:

  MISSING NPI — 4,584 of 23,566 records (19.45%) carry no NPI. That is a fifth
  of the TEFCA population, and health information networks, clearinghouses and
  public health agencies have no reason to hold one. Severity INFORMATIONAL,
  and the rule NEVER converts absence into an entity failure. Whether Medicare
  evidence applies is an APPLICABILITY question decided in D2, not a data-
  quality verdict.

  MISSING HCID / purposesofuse — HCID is populated on 100% of records and
  purposesofuse on 98.25%. Neither has a documented business requirement in
  DocuAction's possession, so both default to INFORMATIONAL. They are
  configuration values (`SEVERITY_OVERRIDES`) precisely so that a real
  requirement, once established, is a config change and not a code change.

  ZIP — the profile found 1,627 records whose ZIP lost its leading zero to a
  spreadsheet round-trip. Zero-padding is deterministic and non-substantive, so
  it is AUTO_SAFE. What is NOT done is inferring or rewriting a city or state
  from a ZIP: that would change an address on the strength of a lookup table,
  which is a substantive identity edit.

CONFIDENCE IS NOT AUTHORITY
A rule may be certain what a value should be and still have no authority to
change it. `suggested_confidence` and `correction_authority` are separate fields
and are never derived from one another. Identity, organisation name and
relationship edits are HUMAN_REQUIRED at any confidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.tefca_registry.rce.field_map import (
    NON_PROVIDER_HL7_ROLES,
    OBSERVED_ACTIVE_VALUES,
    OBSERVED_SEQUOIA_ORG_TYPES,
    SUSPECTED_PURPOSE_VARIANTS,
)

RULE_SET_VERSION = "1.1.0"

# ── categories ───────────────────────────────────────────────────────────────

CAT_SCHEMA = "SCH"
CAT_IDENTIFIER = "ID"
CAT_NPI = "NPI"
CAT_REQUIRED = "REQ"
CAT_FORMAT = "FMT"
CAT_CONTENT = "CON"
CAT_INTEGRITY = "INT"
CAT_BUSINESS = "BUS"
CAT_RECONCILIATION = "REC"

# ── severities and authorities ───────────────────────────────────────────────

CRITICAL, HIGH, MEDIUM, LOW, INFO = (
    "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")

AUTO_SAFE = "AUTO_SAFE"
HUMAN_REQUIRED = "HUMAN_REQUIRED"
QA_REQUIRED = "QA_REQUIRED"
NO_CORRECTION = "NO_CORRECTION"

#: Severity overrides, by rule id. THE configuration surface: a business
#: requirement that makes HCID mandatory is one line here, reviewed and
#: versioned, rather than an edit buried in rule logic.
SEVERITY_OVERRIDES: Dict[str, str] = {}

USPS_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY "
    "DC PR VI GU AS MP".split()
)

_TEST_NAME_PATTERN = re.compile(
    r"(^|[\s\-_])test([\s\-_]|$)|doa[-_]test|donotuse|do[-_]not[-_]use|dummy",
    re.IGNORECASE)


@dataclass
class Finding:
    """One rule firing on one record."""

    rule_id: str
    issue_type: str
    severity: str
    description: str
    correction_authority: str
    field_name: Optional[str] = None
    original_value: Optional[str] = None
    suggested_value: Optional[str] = None
    suggested_source: Optional[str] = None
    suggested_confidence: Optional[str] = None


@dataclass
class Rule:
    """One versioned quality rule."""

    rule_id: str
    category: str
    version: str
    description: str
    #: (record_context) -> list of Findings. Pure and deterministic.
    evaluate: Callable[["RecordContext"], List[Finding]]
    default_severity: str = MEDIUM

    def severity(self) -> str:
        return SEVERITY_OVERRIDES.get(self.rule_id, self.default_severity)


@dataclass
class RecordContext:
    """Everything a rule may see about one record.

    Cross-record facts (duplicate identifiers, parent resolution) arrive in
    `dataset` — precomputed once per run rather than re-derived per record,
    which is what keeps 23,566 records × 25 rules tractable.
    """

    line_number: int
    parse_status: str
    field_count: int
    values: Dict[str, str]
    dataset: Dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> str:
        return (self.values.get(name) or "").strip()


# ── SCH — schema ─────────────────────────────────────────────────────────────

def _sch_001(ctx: RecordContext) -> List[Finding]:
    if ctx.parse_status == "ok":
        return []
    return [Finding(
        "SCH-001", "FIELD_COUNT_MISMATCH", CRITICAL,
        f"Line {ctx.line_number} split into {ctx.field_count} fields; the "
        f"delivered schema has {ctx.dataset.get('expected_field_count', 41)}. "
        f"The row is preserved verbatim and is NOT mapped positionally, because "
        f"a shifted mapping silently corrupts every value past the defect.",
        NO_CORRECTION, field_name=None,
        original_value=f"{ctx.field_count} fields")]


def _sch_002(ctx: RecordContext) -> List[Finding]:
    """Columns delivered entirely empty. Reported ONCE per run, not per record —
    23,566 identical issues would bury the ledger. Handled by the engine as a
    dataset-level finding; this per-record rule is intentionally a no-op."""
    return []


# ── ID — identifiers ─────────────────────────────────────────────────────────

def _id_001(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("id")
    if not value:
        return [Finding(
            "ID-001", "MISSING_SOURCE_ID", CRITICAL,
            "The record carries no `id`. `id` is the only field observed to be "
            "unique in the delivery and is this system's identity key; without "
            "it the record cannot be resolved or promoted.",
            NO_CORRECTION, field_name="id")]
    return []


def _id_002(ctx: RecordContext) -> List[Finding]:
    if ctx.get("HCID"):
        return []
    return [Finding(
        "ID-002", "MISSING_HCID", INFO,
        "No HCID supplied. HCID was populated on 100% of records in the "
        "profiled delivery, so absence is unusual — but no business requirement "
        "establishing HCID as mandatory is in DocuAction's possession, so this "
        "is INFORMATIONAL. Raise the severity in SEVERITY_OVERRIDES if a "
        "requirement is established.",
        NO_CORRECTION, field_name="HCID")]


def _id_003(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("HCID")
    dupes = ctx.dataset.get("hcid_duplicates") or {}
    if value and value in dupes:
        return [Finding(
            "ID-003", "DUPLICATE_HCID", MEDIUM,
            f"HCID {value} appears on {dupes[value]} records in this delivery. "
            f"An HCID is expected to identify one home community; a shared "
            f"value requires analyst determination and is not auto-merged.",
            HUMAN_REQUIRED, field_name="HCID", original_value=value)]
    return []


def _id_004(ctx: RecordContext) -> List[Finding]:
    return []  # AAID absence is normal (68.4% of records); nothing to report.


def _id_005(ctx: RecordContext) -> List[Finding]:
    if ctx.get("TEFCAID"):
        return []
    return [Finding(
        "ID-005", "MISSING_TEFCAID", HIGH,
        "No TEFCAID supplied. TEFCAID was populated on 100% of records in the "
        "profiled delivery.",
        NO_CORRECTION, field_name="TEFCAID")]


def _id_006(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("TEFCAID")
    dupes = ctx.dataset.get("tefcaid_duplicates") or {}
    if not value or value not in dupes:
        return []
    return [Finding(
        "ID-006", "SHARED_TEFCAID", INFO,
        f"TEFCAID {value} is shared by {dupes[value]} records. Observed in the "
        f"delivery to indicate an organisation FAMILY — one TEFCAID covering a "
        f"health system and its facilities — not a duplicate entity. Recorded "
        f"so the relationship is visible; the records are NOT merged, because "
        f"merging them would collapse distinct organisations.",
        NO_CORRECTION, field_name="TEFCAID", original_value=value)]


# ── NPI ──────────────────────────────────────────────────────────────────────

def _npi_001(ctx: RecordContext) -> List[Finding]:
    """A missing NPI is a FACT, never a failure.

    19.45% of the delivered population carries none. Whether Medicare evidence
    applies is decided by the applicability engine in D2, not here.
    """
    if ctx.get("NPI"):
        return []
    return [Finding(
        "NPI-001", "NPI_NOT_SUPPLIED", INFO,
        "No NPI supplied. This is legitimate for a large share of TEFCA "
        "entities — 4,584 of 23,566 records (19.45%) in the profiled delivery — "
        "and is never treated as a verification failure. Medicare applicability "
        "is determined separately in D2.",
        NO_CORRECTION, field_name="NPI")]


def _npi_002(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("NPI")
    if not value:
        return []
    findings: List[Finding] = []
    if "," in value:
        findings.append(Finding(
            "NPI-002", "MULTIPLE_NPI_IN_ONE_FIELD", HIGH,
            f"The NPI field holds more than one value ({value!r}). Splitting it "
            f"automatically would assert which NPI belongs to this entity — an "
            f"identity decision. Held for analyst determination.",
            HUMAN_REQUIRED, field_name="NPI", original_value=value,
            suggested_confidence="LOW"))
    elif len(value) != 10 or not value.isdigit():
        findings.append(Finding(
            "NPI-002", "NPI_MALFORMED", HIGH,
            f"NPI {value!r} is not 10 digits (length {len(value)}). Preserved "
            f"unaltered; an NPI is an identity field and is never repaired "
            f"automatically.",
            HUMAN_REQUIRED, field_name="NPI", original_value=value))
    return findings


def _npi_003(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("NPI")
    if not value or len(value) != 10 or not value.isdigit():
        return []
    try:
        from app.services.npi_validator import validate_npi
        ok, message = validate_npi(value)
    except Exception:  # noqa: BLE001
        return []
    if ok:
        return []
    return [Finding(
        "NPI-003", "NPI_CHECK_DIGIT_FAILED", MEDIUM,
        f"NPI {value} fails the CMS check digit ({message}). The value is "
        f"preserved; NPPES remains the identity authority and will be consulted "
        f"during verification.",
        HUMAN_REQUIRED, field_name="NPI", original_value=value)]


# ── REQ — required fields ────────────────────────────────────────────────────

def _req_001(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("sequoiaorgtype")
    if not value:
        return [Finding(
            "REQ-001", "MISSING_SEQUOIA_ORG_TYPE", CRITICAL,
            "No sequoiaorgtype. This is the authority for the entity's TEFCA "
            "class; without it the entity cannot be placed in the hierarchy. "
            "organizationNodeType is NOT a substitute — it describes technical "
            "exchange behaviour, not organisational classification.",
            NO_CORRECTION, field_name="sequoiaorgtype")]
    if value not in OBSERVED_SEQUOIA_ORG_TYPES:
        return [Finding(
            "REQ-001", "UNKNOWN_SEQUOIA_ORG_TYPE", HIGH,
            f"sequoiaorgtype {value!r} is outside the values observed in the "
            f"profiled delivery {list(OBSERVED_SEQUOIA_ORG_TYPES)}. Treated as "
            f"unknown rather than invalid — the delivery may legitimately carry "
            f"a value the profile did not see.",
            HUMAN_REQUIRED, field_name="sequoiaorgtype", original_value=value)]
    return []


def _req_002(ctx: RecordContext) -> List[Finding]:
    if ctx.get("name"):
        return []
    return [Finding(
        "REQ-002", "MISSING_NAME", CRITICAL,
        "No organisation name. The name is required to create an entity and is "
        "the only key available for organisation-level exclusion screening when "
        "no NPI exists.",
        NO_CORRECTION, field_name="name")]


def _req_003(ctx: RecordContext) -> List[Finding]:
    missing = [f for f in ("address_line", "address_city", "address_state",
                           "address_postalCode") if not ctx.get(f)]
    if not missing:
        return []
    return [Finding(
        "REQ-003", "INCOMPLETE_ADDRESS", MEDIUM,
        f"Address components missing: {', '.join(missing)}. The address of "
        f"record is built from the component fields; address_text is not used "
        f"because it holds the literal label 'Primary' on 75% of records.",
        NO_CORRECTION, field_name=missing[0])]


# ── FMT — format ─────────────────────────────────────────────────────────────

def _fmt_001(ctx: RecordContext) -> List[Finding]:
    """ZIP leading-zero restoration. The one AUTO_SAFE address transformation."""
    value = ctx.get("address_postalCode")
    if not value or "-" in value:
        return []
    digits = value.strip()
    if not digits.isdigit() or len(digits) >= 5:
        return []
    return [Finding(
        "FMT-001", "ZIP_LEADING_ZERO_STRIPPED", LOW,
        f"Postal code {value!r} is {len(digits)} digits. A leading zero was "
        f"almost certainly lost to a spreadsheet round-trip — 1,627 records in "
        f"the profiled delivery show the same pattern. Zero-padding to five "
        f"digits is deterministic and non-substantive, so it is AUTO_SAFE. The "
        f"original value is preserved in Area 1 and in the correction record.",
        AUTO_SAFE, field_name="address_postalCode", original_value=value,
        suggested_value=digits.zfill(5), suggested_source="ZERO_PAD",
        suggested_confidence="HIGH")]


def _fmt_002(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("address_state")
    if not value:
        return []
    upper = value.strip().upper()
    if upper in USPS_STATES:
        if value != upper:
            return [Finding(
                "FMT-002", "STATE_CASE_NOT_CANONICAL", LOW,
                f"State code {value!r} is not upper-case. Case standardisation "
                f"of a state code is deterministic and non-substantive.",
                AUTO_SAFE, field_name="address_state", original_value=value,
                suggested_value=upper, suggested_source="UPPERCASE",
                suggested_confidence="HIGH")]
        return []
    return [Finding(
        "FMT-002", "STATE_NOT_USPS_CODE", MEDIUM,
        f"State {value!r} is not a USPS state or territory code. Preserved; a "
        f"state is part of the address of record and is not rewritten "
        f"automatically.",
        HUMAN_REQUIRED, field_name="address_state", original_value=value)]


def _fmt_003(ctx: RecordContext) -> List[Finding]:
    """ZIP/state plausibility.

    REPORTS a disagreement. It does NOT correct one: a ZIP-to-state table would
    let a typo in a ZIP rewrite the state, or vice versa, and there is no basis
    in the data for deciding which of the two is wrong.
    """
    zip_value = ctx.get("address_postalCode")
    state = ctx.get("address_state").upper()
    if not zip_value or not state or state not in USPS_STATES:
        return []
    digits = zip_value.split("-")[0].strip()
    if not digits.isdigit():
        return []
    prefix = int(digits.zfill(5)[:3])
    expected = _ZIP3_TO_STATES.get(prefix)
    if expected is None or state in expected:
        return []
    return [Finding(
        "FMT-003", "ZIP_STATE_MISMATCH", MEDIUM,
        f"ZIP {zip_value} has a 3-digit prefix normally allocated to "
        f"{'/'.join(sorted(expected))}, but the record states {state}. Both "
        f"values are preserved — nothing in the record establishes which of the "
        f"two is wrong, and inferring one from the other would fabricate an "
        f"address.",
        HUMAN_REQUIRED, field_name="address_postalCode",
        original_value=f"{zip_value} / {state}")]


def _fmt_004(ctx: RecordContext) -> List[Finding]:
    findings: List[Finding] = []
    for name in ("address_line", "address_text", "address_city", "name"):
        raw = ctx.values.get(name) or ""
        if "\t" in raw:
            findings.append(Finding(
                "FMT-004", "EMBEDDED_TAB", LOW,
                f"{name} contains an embedded TAB. Whitespace normalisation is "
                f"deterministic and non-substantive.",
                AUTO_SAFE, field_name=name, original_value=raw,
                suggested_value=re.sub(r"\s+", " ", raw).strip(),
                suggested_source="WHITESPACE_NORMALISE",
                suggested_confidence="HIGH"))
    return findings


def _fmt_005(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("contact_phone")
    if not value:
        return []
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 10:
        return []
    return [Finding(
        "FMT-005", "CONTACT_PHONE_FRAGMENT", INFO,
        f"Contact phone {value!r} holds {len(digits)} digits — too few for a "
        f"complete number. Preserved as delivered; a partial number is never "
        f"reconstructed.",
        NO_CORRECTION, field_name="contact_phone", original_value=value)]


def _fmt_007(ctx: RecordContext) -> List[Finding]:
    """Organisation phone completeness. The exact counterpart of FMT-005.

    `contact_phone` has had a digit-count observation since 1.0.0; the
    organisation's own `phone` had none, so a fragment in one field was recorded
    and the identical fragment in the other was not. The delivery populates
    `phone` on 84 of 23,566 records, which is why the omission never surfaced.

    This OBSERVES and never repairs: a partial number is preserved exactly as
    delivered, the finding is INFORMATIONAL, and it carries NO_CORRECTION. It
    says nothing about the organisation — only that the delivered value is too
    short to be a complete number.
    """
    value = ctx.get("phone")
    if not value:
        return []
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 10:
        return []
    return [Finding(
        "FMT-007", "ORG_PHONE_FRAGMENT", INFO,
        f"Organisation phone {value!r} holds {len(digits)} digits — too few for "
        f"a complete number. Preserved as delivered; a partial number is never "
        f"reconstructed.",
        NO_CORRECTION, field_name="phone", original_value=value)]


def _fmt_006(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("contact_email")
    if not value or re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        return []
    return [Finding(
        "FMT-006", "CONTACT_EMAIL_MALFORMED", LOW,
        f"Contact email {value!r} is not a well-formed address. Preserved.",
        NO_CORRECTION, field_name="contact_email", original_value=value)]


# ── CON — content ────────────────────────────────────────────────────────────

def _con_001(ctx: RecordContext) -> List[Finding]:
    return []  # `domains` is constant 'RCE'; nothing per-record to report.


def _con_002(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("purposesofuse")
    if not value:
        return [Finding(
            "CON-002", "MISSING_PURPOSES_OF_USE", INFO,
            "No Exchange Purpose supplied (412 of 23,566 records in the "
            "profiled delivery). No business requirement establishing it as "
            "mandatory is in DocuAction's possession, so this is "
            "INFORMATIONAL. Exchange Purpose is never inferred, and never "
            "derived from Medicare data.",
            NO_CORRECTION, field_name="purposesofuse")]
    findings: List[Finding] = []
    for token in (t.strip() for t in value.split(",")):
        if token in SUSPECTED_PURPOSE_VARIANTS:
            findings.append(Finding(
                "CON-002", "PURPOSE_TOKEN_VARIANT", INFO,
                f"Exchange Purpose token {token!r} resembles "
                f"{SUSPECTED_PURPOSE_VARIANTS[token]!r}, which is far more "
                f"common in the delivery. REPORTED, not merged: treating them "
                f"as equivalent would assert a mapping the RCE has not stated.",
                NO_CORRECTION, field_name="purposesofuse", original_value=token,
                suggested_value=SUSPECTED_PURPOSE_VARIANTS[token],
                suggested_source="OBSERVED_FREQUENCY",
                suggested_confidence="LOW"))
    return findings


def _con_003(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("active")
    if value not in OBSERVED_ACTIVE_VALUES:
        return [Finding(
            "CON-003", "UNKNOWN_ACTIVE_VALUE", MEDIUM,
            f"active={value!r} is outside the observed values "
            f"{list(OBSERVED_ACTIVE_VALUES)}.",
            HUMAN_REQUIRED, field_name="active", original_value=value)]
    if value == "0":
        return [Finding(
            "CON-003", "INACTIVE_RECORD", INFO,
            "The record is marked inactive (active=0). A legitimate, reportable "
            "state — the entity is promoted with operational_status='inactive' "
            "and is never dropped.",
            NO_CORRECTION, field_name="active", original_value=value)]
    return []


def _con_004(ctx: RecordContext) -> List[Finding]:
    """organizationNodeType must never be read as hierarchy.

    Fires when the field is present, purely to put the distinction on the
    record. The rule exists because the failure it guards against is silent: a
    system that read `initiating-node` as a TEFCA class would reorganise the
    hierarchy without any error appearing anywhere.
    """
    node = ctx.get("organizationNodeType")
    if not node:
        return []
    return [Finding(
        "CON-004", "NODE_TYPE_IS_NOT_HIERARCHY", INFO,
        f"organizationNodeType={node!r} describes TECHNICAL EXCHANGE BEHAVIOUR. "
        f"It is recorded on the entity and is never used to derive the TEFCA "
        f"class, which comes exclusively from sequoiaorgtype "
        f"({ctx.get('sequoiaorgtype')!r}).",
        NO_CORRECTION, field_name="organizationNodeType", original_value=node)]


def _con_005(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("address_text")
    if value and value.lower() in ("primary", "secondary", "billing", "mailing"):
        return [Finding(
            "CON-005", "ADDRESS_TEXT_IS_A_LABEL", INFO,
            f"address_text holds the label {value!r} rather than an address — "
            f"the pattern on 17,717 of 23,566 records (75.18%). The address of "
            f"record is built from the component fields instead; parsing this "
            f"as an address would fabricate one.",
            NO_CORRECTION, field_name="address_text", original_value=value)]
    return []


# ── INT — referential integrity ──────────────────────────────────────────────

def _int_001(ctx: RecordContext) -> List[Finding]:
    if ctx.get("orgManagingOrg"):
        return []
    return [Finding(
        "INT-001", "MISSING_ORG_MANAGING_ORG", HIGH,
        "No orgManagingOrg. QHIN attribution is missing, so the entity cannot "
        "be placed under a QHIN.",
        NO_CORRECTION, field_name="orgManagingOrg")]


def _int_002(ctx: RecordContext) -> List[Finding]:
    value = ctx.get("partOf")
    if not value:
        return [Finding(
            "INT-002", "MISSING_PART_OF", HIGH,
            "No partOf. Every record in the profiled delivery carried one.",
            NO_CORRECTION, field_name="partOf")]
    known_ids = ctx.dataset.get("known_source_ids") or set()
    qhin_oids = ctx.dataset.get("qhin_oids") or set()
    if value in known_ids or value in qhin_oids:
        return []
    return [Finding(
        "INT-002", "PART_OF_UNRESOLVED", MEDIUM,
        f"partOf {value!r} does not resolve to any record in this delivery, nor "
        f"to a QHIN named in orgManagingOrg. The parent may legitimately sit "
        f"outside the delivered scope; recorded for analyst determination "
        f"rather than treated as a broken hierarchy.",
        HUMAN_REQUIRED, field_name="partOf", original_value=value)]


def _int_003(ctx: RecordContext) -> List[Finding]:
    """A Subparticipant whose parent is a QHIN rather than a Participant.

    Observed on 15 records. The TEFCA hierarchy runs QHIN → Participant →
    Subparticipant, so this shape skips a level.
    """
    if ctx.get("sequoiaorgtype") != "Subparticipant":
        return []
    part_of = ctx.get("partOf")
    if not part_of or part_of != ctx.get("orgManagingOrg"):
        return []
    return [Finding(
        "INT-003", "SUBPARTICIPANT_PARENTED_TO_QHIN", MEDIUM,
        f"A Subparticipant whose partOf equals its orgManagingOrg ({part_of}), "
        f"i.e. parented directly to a QHIN rather than to a Participant. "
        f"Observed on 15 records in the profiled delivery. Recorded for analyst "
        f"determination; the hierarchy is not rewritten.",
        HUMAN_REQUIRED, field_name="partOf", original_value=part_of)]


# ── BUS — TEFCA / business ───────────────────────────────────────────────────

def _bus_001(ctx: RecordContext) -> List[Finding]:
    """Non-provider organisations and Medicare relevance.

    Records the signal ONLY. It can relax a Medicare obligation downstream; it
    can never impose one, because hl7orgrole is populated on 0.25% of records
    and a field that sparse must not create requirements.
    """
    role = ctx.get("hl7orgrole")
    if role and role in NON_PROVIDER_HL7_ROLES:
        return [Finding(
            "BUS-001", "NON_PROVIDER_ORGANISATION", INFO,
            f"hl7orgrole={role!r} indicates a non-provider organisation. Used "
            f"only to relax Medicare applicability in D2 — never to impose it.",
            NO_CORRECTION, field_name="hl7orgrole", original_value=role)]
    return []


def _bus_002(ctx: RecordContext) -> List[Finding]:
    name = ctx.get("name")
    if not name or not _TEST_NAME_PATTERN.search(name):
        return []
    return [Finding(
        "BUS-002", "TEST_RECORD_SUSPECTED", MEDIUM,
        f"Organisation name {name!r} matches a test-artefact pattern. Flagged "
        f"for analyst determination and promoted with is_test_record=true. The "
        f"record is NEVER dropped — a real organisation may legitimately carry "
        f"'Test' in its name, and deleting on a substring is exactly the silent "
        f"loss this pipeline exists to prevent.",
        HUMAN_REQUIRED, field_name="name", original_value=name)]


def _bus_003(ctx: RecordContext) -> List[Finding]:
    """A Participant whose partOf repeats its QHIN. The NORMAL shape here.

    Reported at INFORMATIONAL so the promotion decision — emit one QHIN edge,
    not two — is visible in the ledger rather than buried in code.
    """
    if ctx.get("sequoiaorgtype") != "Participant":
        return []
    if ctx.get("partOf") != ctx.get("orgManagingOrg"):
        return []
    return [Finding(
        "BUS-003", "PARTICIPANT_PARENT_IS_QHIN", INFO,
        "A Participant whose partOf equals its orgManagingOrg — the shape on "
        "all 11,077 Participants in the profiled delivery. One managed_by_qhin "
        "edge is created; no second sub_participant_of edge is emitted, because "
        "both fields express the same fact and two edges would double-count it.",
        NO_CORRECTION, field_name="partOf", original_value=ctx.get("partOf"))]


# ── the rule set ─────────────────────────────────────────────────────────────

RULES: Tuple[Rule, ...] = (
    Rule("SCH-001", CAT_SCHEMA, "1.0.0",
         "Field count matches the delivered schema", _sch_001, CRITICAL),
    Rule("SCH-002", CAT_SCHEMA, "1.0.0",
         "Columns delivered entirely empty (dataset-level)", _sch_002, INFO),
    Rule("ID-001", CAT_IDENTIFIER, "1.0.0",
         "Source `id` present", _id_001, CRITICAL),
    Rule("ID-002", CAT_IDENTIFIER, "1.0.0", "HCID present", _id_002, INFO),
    Rule("ID-003", CAT_IDENTIFIER, "1.0.0", "HCID unique", _id_003, MEDIUM),
    Rule("ID-004", CAT_IDENTIFIER, "1.0.0", "AAID present", _id_004, INFO),
    Rule("ID-005", CAT_IDENTIFIER, "1.0.0", "TEFCAID present", _id_005, HIGH),
    Rule("ID-006", CAT_IDENTIFIER, "1.0.0",
         "TEFCAID shared across records", _id_006, INFO),
    Rule("NPI-001", CAT_NPI, "1.0.0", "NPI supplied", _npi_001, INFO),
    Rule("NPI-002", CAT_NPI, "1.0.0", "NPI well-formed", _npi_002, HIGH),
    Rule("NPI-003", CAT_NPI, "1.0.0", "NPI check digit", _npi_003, MEDIUM),
    Rule("REQ-001", CAT_REQUIRED, "1.0.0",
         "sequoiaorgtype present and known", _req_001, CRITICAL),
    Rule("REQ-002", CAT_REQUIRED, "1.0.0", "Name present", _req_002, CRITICAL),
    Rule("REQ-003", CAT_REQUIRED, "1.0.0",
         "Address components present", _req_003, MEDIUM),
    Rule("FMT-001", CAT_FORMAT, "1.0.0",
         "ZIP leading zero preserved", _fmt_001, LOW),
    Rule("FMT-002", CAT_FORMAT, "1.0.0", "State is a USPS code", _fmt_002, MEDIUM),
    Rule("FMT-003", CAT_FORMAT, "1.0.0", "ZIP/state consistency", _fmt_003, MEDIUM),
    Rule("FMT-004", CAT_FORMAT, "1.0.0", "No embedded tabs", _fmt_004, LOW),
    Rule("FMT-005", CAT_FORMAT, "1.0.0",
         "Contact phone complete", _fmt_005, INFO),
    Rule("FMT-006", CAT_FORMAT, "1.0.0",
         "Contact email well-formed", _fmt_006, LOW),
    Rule("FMT-007", CAT_FORMAT, "1.0.0",
         "Organisation phone complete", _fmt_007, INFO),
    Rule("CON-001", CAT_CONTENT, "1.0.0", "domains value", _con_001, INFO),
    Rule("CON-002", CAT_CONTENT, "1.0.0",
         "Exchange Purpose present and canonical", _con_002, INFO),
    Rule("CON-003", CAT_CONTENT, "1.0.0", "active flag", _con_003, INFO),
    Rule("CON-004", CAT_CONTENT, "1.0.0",
         "organizationNodeType is not hierarchy", _con_004, INFO),
    Rule("CON-005", CAT_CONTENT, "1.0.0",
         "address_text is a label, not an address", _con_005, INFO),
    Rule("INT-001", CAT_INTEGRITY, "1.0.0",
         "orgManagingOrg present", _int_001, HIGH),
    Rule("INT-002", CAT_INTEGRITY, "1.0.0", "partOf resolves", _int_002, MEDIUM),
    Rule("INT-003", CAT_INTEGRITY, "1.0.0",
         "Subparticipant parented to a Participant", _int_003, MEDIUM),
    Rule("BUS-001", CAT_BUSINESS, "1.0.0",
         "Non-provider organisation signal", _bus_001, INFO),
    Rule("BUS-002", CAT_BUSINESS, "1.0.0",
         "Test-artefact detection", _bus_002, MEDIUM),
    Rule("BUS-003", CAT_BUSINESS, "1.0.0",
         "Participant parent is its QHIN", _bus_003, INFO),
)

RULE_BY_ID: Dict[str, Rule] = {rule.rule_id: rule for rule in RULES}

#: Rules whose findings may ever be applied without a human. Enforced in
#: `curation.py`; listed here so the set is reviewable in one place.
AUTO_SAFE_RULES = frozenset({"FMT-001", "FMT-002", "FMT-004"})


class DuplicateRuleId(RuntimeError):
    """Two rules claim the same rule_id. Raised at import, never at run time."""


def _assert_rule_ids_unique() -> None:
    """Refuse to load a rule set containing a duplicate rule_id.

    WHY THIS IS AN IMPORT-TIME FAILURE AND NOT A WARNING
    ────────────────────────────────────────────────────
    `RULE_BY_ID` is a dict comprehension over `RULES`, so a duplicate id is
    silently deduplicated with last-definition-wins — while `quality_engine`
    iterates `RULES` itself and would execute BOTH. The three consequences
    compound, and none of them announces itself as a duplicate:

      * both rules run over all 23,566 records, doubling the work
      * `per_rule_evaluated` / `per_rule_issues` are keyed by rule_id, so the
        two rules' counters MERGE and the execution history becomes unreadable
      * every issue from either rule is stamped with the LATER rule's severity,
        because `_issue_row` resolves severity through `RULE_BY_ID`

    The run then dies at `db.commit()` on `uq_rce_rule_exec_run_rule`, after the
    full pass has completed — an expensive, late failure whose message says
    nothing about rule ids. Failing at import turns that into a one-line error
    before anything runs.

    NOTE FOR WHOEVER ADDS THE NEXT RULE: FMT-005, FMT-006 and FMT-007 are
    ALREADY TAKEN ("Contact phone complete", "Contact email well-formed",
    "Organisation phone complete"). Ask `next_available_rule_ids()` rather than
    reading this line — it is derived, this note is not.
    """
    seen: Dict[str, int] = {}
    for rule in RULES:
        seen[rule.rule_id] = seen.get(rule.rule_id, 0) + 1
    duplicates = sorted(rid for rid, n in seen.items() if n > 1)
    if duplicates:
        raise DuplicateRuleId(
            f"duplicate rule_id in RULES: {duplicates}. Each rule_id must appear "
            f"exactly once; a duplicate is silently deduplicated by RULE_BY_ID "
            f"but executed twice by quality_engine. Next free ids: "
            f"{next_available_rule_ids()}")


def next_available_rule_ids() -> Dict[str, str]:
    """The next unused id per category prefix. Derived, never hand-maintained."""
    highest: Dict[str, int] = {}
    for rule in RULES:
        prefix, _, number = rule.rule_id.rpartition("-")
        if prefix and number.isdigit():
            highest[prefix] = max(highest.get(prefix, 0), int(number))
    return {prefix: f"{prefix}-{n + 1:03d}" for prefix, n in sorted(highest.items())}


_assert_rule_ids_unique()


def rule_config_hash() -> str:
    """SHA-256 over the rule set's identity, versions and effective severities.

    Recorded on every ingestion run. Two runs with different hashes produced
    their issue counts under different configurations, which is the difference
    between "the data changed" and "we changed the rules".
    """
    payload = json.dumps(
        [{"rule_id": r.rule_id, "version": r.version, "severity": r.severity(),
          "category": r.category} for r in RULES],
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: 3-digit ZIP prefix → states. Deliberately PARTIAL: only unambiguous
#: allocations are listed, and an unlisted prefix produces no finding. A
#: half-remembered table that fired false mismatches on valid addresses would be
#: worse than no check at all.
_ZIP3_TO_STATES: Dict[int, set] = {}


def _load_zip3() -> None:
    ranges = [
        ((0o0, 0), None),
    ]
    # (start, end, state) — contiguous, well-known allocations.
    table = [
        (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"), (39, 49, "ME"),
        (50, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"),
        (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"),
        (200, 205, "DC"), (206, 219, "MD"), (220, 246, "VA"),
        (247, 268, "WV"), (270, 289, "NC"), (290, 299, "SC"),
        (300, 319, "GA"), (320, 349, "FL"), (350, 369, "AL"),
        (370, 385, "TN"), (386, 397, "MS"), (400, 427, "KY"),
        (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"),
        (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"),
        (570, 577, "SD"), (580, 588, "ND"), (590, 599, "MT"),
        (600, 629, "IL"), (630, 658, "MO"), (660, 679, "KS"),
        (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"),
        (730, 749, "OK"), (750, 799, "TX"), (800, 816, "CO"),
        (820, 831, "WY"), (832, 838, "ID"), (840, 847, "UT"),
        (850, 865, "AZ"), (870, 884, "NM"), (889, 898, "NV"),
        (900, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"),
        (980, 994, "WA"), (995, 999, "AK"),
    ]
    for start, end, state in table:
        for prefix in range(start, end + 1):
            _ZIP3_TO_STATES.setdefault(prefix, set()).add(state)


_load_zip3()
