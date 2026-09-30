#!/usr/bin/env python
"""Deterministic synthetic QA fixture pack for ONC/RCE delivery scenarios.

SYNTHETIC / NOT GOVERNMENT DATA. Never contains real NPIs, names, or addresses.

WHAT THIS PRODUCES
================================================================================
One pipe-delimited delivery file per QA scenario, each carrying EXACTLY the
locked 41-column ONC/RCE header, plus a MANIFEST.json that names every file,
its sha256, its row count and the rule codes / workflow states it is expected
to produce when registered through the existing endpoint

    POST /api/tefca/rce/official-deliveries        (program_manager or admin)

This script writes FILES ONLY. It opens no network connection, no database
session and no HTTP client; registering the files on DEV is a human step and
is described in docs/qa/fixtures/QA_FIXTURE_PACK.md.

WHY EVERYTHING HERE IS UNMISTAKABLY SYNTHETIC
---------------------------------------------
  * Every `id` lives under the unassigned OID arc 9.99.777.QA<TAG>.<n>. The
    first arc of a real OID is 0, 1 or 2; nothing real can collide with 9.
  * Every organisation name contains the word SYNTHETIC.
  * Every NPI is generated: a 9-digit base starting with 9 followed by the CMS
    check digit (Luhn over 80840 + base). CMS assigns NPIs beginning with 1 or
    2 only, so a 9xxxxxxxxx value cannot be a real provider. Luhn-INVALID
    values are the same bases with the check digit deliberately shifted, so
    they are still 10 digits and still cannot be real.
  * The address is "1 Synthetic Way, Testville, AK 99999, USA". The state is
    AK rather than MA because rule FMT-003 allocates the ZIP prefix 999 to
    Alaska; "MA 99999" would raise ZIP_STATE_MISMATCH on every row and no
    scenario could be clean.

DETERMINISM
-----------
Same inputs -> byte-identical files. There is no randomness and no timestamp
inside any file or in the manifest; the defect mix of SEPTEMBER_SCALE is a
fixed function of the row index. Re-running the script and diffing the output
directory is a valid check that nothing changed.

GUARANTEES
----------
  * REFUSES TO RUN WITH ENVIRONMENT=production. The script touches no
    database, but the files it writes are meant to be uploaded, and a fixture
    pack generated inside a production shell is the first step of an upload
    into the wrong environment. Same ENVIRONMENT check as
    scripts/seed_qa_test_data.py.
  * No value in any cell contains the delimiter, a carriage return or a line
    feed; the writer asserts it.

USAGE
-----
    python scripts/qa_fixture_pack.py                       # all scenarios
    python scripts/qa_fixture_pack.py --out C:/tmp/fx       # elsewhere
    python scripts/qa_fixture_pack.py --scale 2000          # smaller SCALE file
    python scripts/qa_fixture_pack.py --only CLEAN_B1,LUHN_INVALID_NPI
    python scripts/qa_fixture_pack.py --list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

GENERATOR_VERSION = "1.0.0"

#: The rule set these expectations were derived from
#: (app/tefca_registry/rce/quality_rules.py RULE_SET_VERSION).
TARGET_RULE_SET_VERSION = "1.3.0"

#: The locked 41-column ONC/RCE header, verbatim and in delivered order.
RCE_COLUMNS: Tuple[str, ...] = (
    "id", "domains", "initiatoronly", "orgManagingOrg", "purposesofuse",
    "stateofoperation", "doa", "transaction", "delegationRole",
    "organizationNodeType", "NPI", "NAIC", "CCN", "HCID", "AAID", "TEFCAID",
    "active", "sequoiaorgtype", "hl7orgrole", "name", "alias", "phone", "email",
    "address_text", "address_line", "address_city", "address_state",
    "address_postalCode", "address_country", "partOf", "contact_company",
    "contact_purpose", "contact_name", "contact_phone", "contact_email",
    "contact_address_text", "contact_address_line", "contact_address_city",
    "contact_address_state", "contact_address_postalCode",
    "contact_address_country",
)
assert len(RCE_COLUMNS) == 41

DELIMITER = "|"
LINE_TERMINATOR = "\r\n"
ENCODING = "utf-8"
OID_ARC = "9.99.777"
FILE_PREFIX = "SYNTHETIC-QA-"
FILE_SUFFIX = ".psv"
MANIFEST_NAME = "MANIFEST.json"
DEFAULT_SCALE = 25_000

SYNTHETIC_ADDRESS = {
    "address_text": "1 Synthetic Way, Testville, AK 99999, USA",
    "address_line": "1 Synthetic Way",
    "address_city": "Testville",
    "address_state": "AK",
    "address_postalCode": "99999",
    "address_country": "USA",
}

#: The ISO documentation arc (2.999) — syntactically a valid OID that no
#: registry, delivery or QHIN will ever resolve. Used only to provoke DOA-002.
EXTERNAL_DOA_OID = "2.999.777.1"


# ── production guard ─────────────────────────────────────────────────────────

def _is_production() -> bool:
    return (os.getenv("ENVIRONMENT", "") or "").strip().lower() == "production"


def refuse_production() -> None:
    """Hard stop before any file is written."""
    if _is_production():
        sys.stderr.write(
            "REFUSED: qa_fixture_pack.py must never run with ENVIRONMENT=production.\n"
            "The files it writes are synthetic deliveries meant for DEV upload;\n"
            "generating them inside a production shell invites the wrong upload.\n"
            "Set ENVIRONMENT to a non-production value to generate the pack.\n")
        raise SystemExit(2)


# ── synthetic NPIs ───────────────────────────────────────────────────────────
#
# Self-contained copy of the CMS check-digit arithmetic (45 CFR 162.406): Luhn
# over the constant 80840 plus the 9 base digits. The project's own
# app/services/npi_validator.py is the authority; tests/test_qa_fixture_pack.py
# checks every value this module emits against it. It is duplicated here only
# so the generator imports nothing from `app` and can run anywhere.

_CMS_PREFIX = "80840"


def _luhn_total(number: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(number)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total


def _check_digit(base9: str) -> str:
    remainder = _luhn_total(_CMS_PREFIX + base9 + "0") % 10
    return str((10 - remainder) % 10)


def synthetic_npi(n: int) -> str:
    """A Luhn-valid 10-digit NPI that cannot be real: base 9xxxxxxxx."""
    base = f"9{n:08d}"
    return base + _check_digit(base)


def luhn_invalid_npi(n: int) -> str:
    """Ten digits, same synthetic base, check digit shifted by one: fails Luhn."""
    valid = synthetic_npi(n)
    return valid[:9] + str((int(valid[9]) + 1) % 10)


# ── finding catalogue ────────────────────────────────────────────────────────
#
# (rule_id, issue_type) -> (severity, correction_authority). The subset of the
# 1.3.0 rule set these fixtures deliberately provoke. The test suite asserts
# every entry against the live rule functions, so a drift in quality_rules.py
# fails the test rather than silently mis-describing a fixture.

FINDING_CATALOG: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("NPI-001", "NPI_NOT_SUPPLIED"): ("INFORMATIONAL", "NO_CORRECTION"),
    ("NPI-002", "NPI_LENGTH_INVALID"): ("HIGH", "HUMAN_REQUIRED"),
    ("NPI-002", "MULTIPLE_NPI_IN_ONE_FIELD"): ("HIGH", "HUMAN_REQUIRED"),
    ("NPI-004", "NPI_FORMAT_INVALID"): ("HIGH", "HUMAN_REQUIRED"),
    ("NPI-003", "NPI_CHECKSUM_INVALID"): ("HIGH", "HUMAN_REQUIRED"),
    ("REQ-001", "UNKNOWN_SEQUOIA_ORG_TYPE"): ("HIGH", "HUMAN_REQUIRED"),
    ("INT-002", "MISSING_PART_OF"): ("HIGH", "NO_CORRECTION"),
    ("INT-002", "PART_OF_UNRESOLVED"): ("MEDIUM", "HUMAN_REQUIRED"),
    ("CON-003", "INACTIVE_RECORD"): ("INFORMATIONAL", "NO_CORRECTION"),
    ("CON-003", "ACTIVE_FORMAT_NORMALIZED"): ("INFORMATIONAL", "NO_CORRECTION"),
    ("CON-003", "MISSING_ACTIVE_VALUE"): ("HIGH", "HUMAN_REQUIRED"),
    ("CON-003", "UNSUPPORTED_ACTIVE_VALUE"): ("HIGH", "HUMAN_REQUIRED"),
    ("CON-004", "NODE_TYPE_IS_NOT_HIERARCHY"): ("INFORMATIONAL", "NO_CORRECTION"),
    ("CON-004", "UNKNOWN_ORG_NODE_TYPE"): ("HIGH", "HUMAN_REQUIRED"),
    ("ACT-001", "INACTIVE_NEW_ENTRANT"): ("HIGH", "HUMAN_REQUIRED"),
    ("SCH-003", "DUPLICATE_SOURCE_ID"): ("CRITICAL", "HUMAN_REQUIRED"),
    ("SO-002", "MISSING_NAIC_FOR_PAYER"): ("HIGH", "HUMAN_REQUIRED"),
    ("BUS-001", "NON_PROVIDER_ORGANISATION"): ("INFORMATIONAL", "NO_CORRECTION"),
    ("BUS-002", "TEST_RECORD_SUSPECTED"): ("MEDIUM", "HUMAN_REQUIRED"),
    ("BUS-003", "PARTICIPANT_PARENT_IS_QHIN"): ("INFORMATIONAL", "NO_CORRECTION"),
    ("PUR-001", "UNKNOWN_PURPOSE_TOKEN"): ("HIGH", "HUMAN_REQUIRED"),
    ("DOA-001", "DOA_NOT_AN_OID"): ("MEDIUM", "HUMAN_REQUIRED"),
    ("DOA-002", "EXTERNAL_REFERENCE_UNVERIFIED"): ("MEDIUM", "HUMAN_REQUIRED"),
    ("FMT-001", "ZIP_LEADING_ZERO_STRIPPED"): ("LOW", "AUTO_SAFE"),
}

#: Written by promotion, not by the quality engine; listed so the manifest can
#: name it for the CONFLICTING_EVIDENCE pair. Not asserted by the DB-free test.
PROMOTION_CONFLICT = ("NPI-008", "NPI_EXISTING_VALUE_CONFLICT", "HIGH", "HUMAN_REQUIRED")

Finding = Tuple[str, str]

QHIN_ROW_FINDINGS: Tuple[Finding, ...] = (
    ("REQ-001", "UNKNOWN_SEQUOIA_ORG_TYPE"),
    ("INT-002", "MISSING_PART_OF"),
    ("NPI-001", "NPI_NOT_SUPPLIED"),
)
PARTICIPANT_FINDINGS: Tuple[Finding, ...] = (("BUS-003", "PARTICIPANT_PARENT_IS_QHIN"),)


# ── rows ─────────────────────────────────────────────────────────────────────

@dataclass
class Line:
    """One delivered data line and the per-record findings it should raise."""

    values: Dict[str, str]
    expected: List[Finding] = field(default_factory=list)
    kind: str = "clean"

    @property
    def id(self) -> str:
        return self.values["id"]


class RowFactory:
    """Rows for one scenario, all inside 9.99.777.QA<TAG>."""

    def __init__(self, tag: str):
        if not tag.isalnum() or not tag.isupper():
            raise ValueError(f"tag must be upper-case alphanumeric, got {tag!r}")
        self.tag = tag
        self.prefix = f"{OID_ARC}.QA{tag}"
        self.qhin_id = f"{self.prefix}.1"

    def oid(self, n: int) -> str:
        return f"{self.prefix}.{n}"

    def _base(self, n: int, *, sequoia: str, label: str, npi: str,
              part_of: str) -> Dict[str, str]:
        row = {c: "" for c in RCE_COLUMNS}
        oid = self.oid(n)
        row.update({
            "id": oid,
            "domains": "RCE",
            "orgManagingOrg": self.qhin_id,
            "purposesofuse": "T-TRTMNT",
            "stateofoperation": "AK",
            "NPI": npi,
            "HCID": f"urn:oid:{oid}",
            "TEFCAID": f"SYN-{self.tag}-TEFCAID-{n:05d}",
            "active": "1",
            "sequoiaorgtype": sequoia,
            "name": f"SYNTHETIC {self.tag} {label} {n}",
            "alias": f"SYNTHETIC {self.tag} ALIAS {n}",
            "phone": "(555) 010-0100",
            "email": f"synthetic.{n}@example.invalid",
            "partOf": part_of,
            "contact_purpose": "ADMIN",
            "contact_name": f"SYNTHETIC CONTACT {n}",
            "contact_phone": "(555) 010-0101",
            "contact_email": f"synthetic.contact.{n}@example.invalid",
            "contact_address_text": SYNTHETIC_ADDRESS["address_text"],
            "contact_address_line": SYNTHETIC_ADDRESS["address_line"],
            "contact_address_city": SYNTHETIC_ADDRESS["address_city"],
            "contact_address_state": SYNTHETIC_ADDRESS["address_state"],
            "contact_address_postalCode": SYNTHETIC_ADDRESS["address_postalCode"],
            "contact_address_country": SYNTHETIC_ADDRESS["address_country"],
        })
        row.update(SYNTHETIC_ADDRESS)
        return row

    def qhin(self) -> Line:
        """The QHIN as a delivered row: sequoiaorgtype=QHIN, partOf empty.

        Rule set 1.3.0 knows only Participant and Subparticipant (REQ-001) and
        requires a partOf on every record (INT-002), because in a real
        delivery the QHIN is an external referent that promotion synthesises
        from orgManagingOrg. A QHIN delivered as a row is therefore ALWAYS
        HELD. It is included so the members' partOf/orgManagingOrg resolve
        inside the file and the hierarchy root stays in the synthetic arc.
        """
        row = self._base(1, sequoia="QHIN", label="QHIN", npi="", part_of="")
        row["orgManagingOrg"] = self.qhin_id
        row["name"] = f"SYNTHETIC {self.tag} QHIN"
        row["alias"] = f"SYNTHETIC {self.tag} QHIN ALIAS"
        return Line(row, list(QHIN_ROW_FINDINGS), kind="qhin")

    def participant(self, n: int, **over: str) -> Line:
        row = self._base(n, sequoia="Participant", label="PARTICIPANT",
                         npi=synthetic_npi(n), part_of=self.qhin_id)
        row.update(over)
        expected = list(PARTICIPANT_FINDINGS) if row["partOf"] == row["orgManagingOrg"] else []
        return Line(row, expected, kind="participant")

    def subparticipant(self, n: int, parent_n: int, **over: str) -> Line:
        row = self._base(n, sequoia="Subparticipant", label="SUBPARTICIPANT",
                         npi=synthetic_npi(n), part_of=self.oid(parent_n))
        row.update(over)
        return Line(row, [], kind="subparticipant")


# ── defects ──────────────────────────────────────────────────────────────────
#
# Each defect mutates one Line and appends the findings the rule set raises
# for that mutation. `ACT-001` is CONDITIONAL: it fires only when the
# environment already holds an earlier delivery (quality_engine builds
# `new_entrant_ids` from the previous intake); on a fresh database the first
# registration is a baseline and only CON-003 INACTIVE_RECORD appears.

def d_missing_npi(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["NPI"] = ""
    line.expected.append(("NPI-001", "NPI_NOT_SUPPLIED"))
    line.kind = "missing_npi"


def d_length_invalid(line: Line, _f: RowFactory, n: int) -> None:
    line.values["NPI"] = synthetic_npi(n)[:9] if n % 2 else synthetic_npi(n) + "1"
    line.expected.append(("NPI-002", "NPI_LENGTH_INVALID"))
    line.kind = "npi_length_invalid"


def d_format_invalid(line: Line, _f: RowFactory, n: int) -> None:
    line.values["NPI"] = "9" + f"{n:07d}" + "X" + "9"
    line.expected.append(("NPI-004", "NPI_FORMAT_INVALID"))
    line.kind = "npi_format_invalid"


def d_multiple_npi(line: Line, _f: RowFactory, n: int) -> None:
    line.values["NPI"] = f"{synthetic_npi(n)},{synthetic_npi(n + 1)}"
    line.expected.append(("NPI-002", "MULTIPLE_NPI_IN_ONE_FIELD"))
    line.kind = "npi_multiple"


def d_checksum_invalid(line: Line, _f: RowFactory, n: int) -> None:
    line.values["NPI"] = luhn_invalid_npi(n)
    line.expected.append(("NPI-003", "NPI_CHECKSUM_INVALID"))
    line.kind = "npi_checksum_invalid"


def d_missing_partof(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["partOf"] = ""
    line.expected = [f for f in line.expected if f[0] != "BUS-003"]
    line.expected.append(("INT-002", "MISSING_PART_OF"))
    line.kind = "missing_partof"


def d_unresolved_partof(line: Line, f: RowFactory, _n: int) -> None:
    line.values["partOf"] = f.oid(0)
    line.expected = [x for x in line.expected if x[0] != "BUS-003"]
    line.expected.append(("INT-002", "PART_OF_UNRESOLVED"))
    line.kind = "unresolved_partof"


def d_inactive(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["active"] = "0"
    line.expected += [("CON-003", "INACTIVE_RECORD"), ("ACT-001", "INACTIVE_NEW_ENTRANT")]
    line.kind = "inactive"


def d_inactive_float(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["active"] = "0.0"
    line.expected += [("CON-003", "ACTIVE_FORMAT_NORMALIZED"), ("CON-003", "INACTIVE_RECORD"),
                      ("ACT-001", "INACTIVE_NEW_ENTRANT")]
    line.kind = "inactive_float"


def d_active_empty(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["active"] = ""
    line.expected.append(("CON-003", "MISSING_ACTIVE_VALUE"))
    line.kind = "active_empty"


def d_active_unsupported(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["active"] = "yes"
    line.expected.append(("CON-003", "UNSUPPORTED_ACTIVE_VALUE"))
    line.kind = "active_unsupported"


def d_active_float_one(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["active"] = "1.0"
    line.expected.append(("CON-003", "ACTIVE_FORMAT_NORMALIZED"))
    line.kind = "active_float_one"


def d_node_type_known(line: Line, _f: RowFactory, n: int) -> None:
    vocabulary = ("initiating-node", "no-node", "passthrough-node")
    line.values["organizationNodeType"] = vocabulary[n % 3]
    line.expected.append(("CON-004", "NODE_TYPE_IS_NOT_HIERARCHY"))
    line.kind = "node_type_known"


def d_node_type_unknown(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["organizationNodeType"] = "root-node"
    line.expected.append(("CON-004", "UNKNOWN_ORG_NODE_TYPE"))
    line.kind = "node_type_unknown"


def d_unknown_purpose(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["purposesofuse"] = "T-TRTMNT,T-SYNTHETIC"
    line.expected.append(("PUR-001", "UNKNOWN_PURPOSE_TOKEN"))
    line.kind = "unknown_purpose"


def d_payer_no_naic(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["hl7orgrole"] = "payer"
    line.values["NAIC"] = ""
    line.expected += [("SO-002", "MISSING_NAIC_FOR_PAYER"), ("BUS-001", "NON_PROVIDER_ORGANISATION")]
    line.kind = "payer_no_naic"


def d_doa_not_oid(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["doa"] = "SYNTHETIC-DOA-REF"
    line.expected.append(("DOA-001", "DOA_NOT_AN_OID"))
    line.kind = "doa_not_oid"


def d_doa_external(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["doa"] = EXTERNAL_DOA_OID
    line.expected.append(("DOA-002", "EXTERNAL_REFERENCE_UNVERIFIED"))
    line.kind = "doa_external"


def d_test_name(line: Line, f: RowFactory, n: int) -> None:
    line.values["name"] = f"SYNTHETIC {f.tag} TEST ORG {n}"
    line.expected.append(("BUS-002", "TEST_RECORD_SUSPECTED"))
    line.kind = "test_name"


def d_zip_leading_zero(line: Line, _f: RowFactory, _n: int) -> None:
    line.values["address_postalCode"] = "9999"
    line.expected.append(("FMT-001", "ZIP_LEADING_ZERO_STRIPPED"))
    line.kind = "zip_leading_zero"


Defect = Callable[[Line, RowFactory, int], None]

#: The SEPTEMBER_SCALE defect rotation. Order is part of the contract: the
#: defect for row i is SCALE_DEFECTS[(i // 5) % len(SCALE_DEFECTS)].
SCALE_DEFECTS: Tuple[Tuple[str, Defect], ...] = (
    ("missing_npi", d_missing_npi),
    ("npi_length_invalid", d_length_invalid),
    ("npi_format_invalid", d_format_invalid),
    ("npi_multiple", d_multiple_npi),
    ("npi_checksum_invalid", d_checksum_invalid),
    ("missing_partof", d_missing_partof),
    ("unresolved_partof", d_unresolved_partof),
    ("inactive", d_inactive),
    ("node_type_known", d_node_type_known),
    ("node_type_unknown", d_node_type_unknown),
    ("unknown_purpose", d_unknown_purpose),
    ("payer_no_naic", d_payer_no_naic),
    ("doa_not_oid", d_doa_not_oid),
    ("doa_external", d_doa_external),
    ("test_name", d_test_name),
    ("zip_leading_zero", d_zip_leading_zero),
)


# ── scenarios ────────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    name: str
    tag: str
    summary: str
    build: Callable[[RowFactory, int], List[Line]]
    notes: List[str] = field(default_factory=list)
    workflow_uses: List[str] = field(default_factory=list)
    #: Findings written by later stages (promotion), not by the quality engine.
    later_stage_findings: List[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{FILE_PREFIX}{self.name}{FILE_SUFFIX}"

    def lines(self, scale: int) -> List[Line]:
        return self.build(RowFactory(self.tag), scale)


def _family(f: RowFactory) -> List[Line]:
    """QHIN + 2 Participants + 2 Subparticipants, the CLEAN_B1 shape."""
    return [f.qhin(), f.participant(2), f.participant(3),
            f.subparticipant(4, 2), f.subparticipant(5, 3)]


def build_clean_b1(f: RowFactory, _scale: int) -> List[Line]:
    return _family(f)


def build_missing_npi(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    for i, line in enumerate(lines[1:], start=2):
        d_missing_npi(line, f, i)
    return lines


def build_malformed_npi(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    d_length_invalid(lines[1], f, 3)      # 9 digits
    d_length_invalid(lines[2], f, 2)      # 11 digits
    d_format_invalid(lines[3], f, 4)      # 10 characters, one letter
    d_multiple_npi(lines[4], f, 5)        # two NPIs in one cell
    return lines


def build_luhn_invalid_npi(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    d_checksum_invalid(lines[1], f, 2)
    d_checksum_invalid(lines[3], f, 4)
    return lines


def build_missing_partof(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    d_missing_partof(lines[1], f, 2)      # Participant with no partOf
    d_missing_partof(lines[3], f, 4)      # Subparticipant with no partOf
    extra = f.subparticipant(6, 3)
    d_unresolved_partof(extra, f, 6)      # contrast: present but unresolved
    return lines + [extra]


def build_inactive_new_entrant(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    d_inactive(lines[1], f, 2)
    d_inactive(lines[3], f, 4)
    d_inactive_float(lines[4], f, 5)
    return lines


def build_multiple_high(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    d_checksum_invalid(lines[3], f, 4)    # IDENTITY  (NPI-003)
    d_node_type_unknown(lines[3], f, 4)   # METHODOLOGY (CON-004)
    d_missing_partof(lines[3], f, 4)      # held, no case (INT-002 NO_CORRECTION)
    d_checksum_invalid(lines[4], f, 5)    # IDENTITY  (NPI-003)
    d_payer_no_naic(lines[4], f, 5)       # DQ        (SO-002)
    d_checksum_invalid(lines[2], f, 3)    # IDENTITY  (NPI-003)
    d_unknown_purpose(lines[2], f, 3)     # METHODOLOGY (PUR-001)
    return lines


def build_node_type(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    d_node_type_known(lines[1], f, 0)     # initiating-node
    d_node_type_known(lines[2], f, 1)     # no-node
    d_node_type_known(lines[3], f, 2)     # passthrough-node
    d_node_type_unknown(lines[4], f, 5)   # root-node -> HELD
    s6, s7, s8 = f.subparticipant(6, 2), f.subparticipant(7, 3), f.subparticipant(8, 2)
    d_active_empty(s6, f, 6)
    d_active_unsupported(s7, f, 7)
    d_active_float_one(s8, f, 8)
    return lines + [s6, s7, s8]


def build_duplicate_id(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    dup = f.subparticipant(5, 2)
    dup.values["id"] = lines[3].id          # same `id` as line 4 ...
    dup.values["HCID"] = f"urn:oid:{f.oid(5)}"   # ... distinct HCID/TEFCAID/name,
    dup.values["name"] = f"SYNTHETIC {f.tag} SUBPARTICIPANT 4 DUPLICATE LINE"  # so SCH-003 is isolated
    dup.kind = "duplicate_id"
    lines[3].kind = "duplicate_id"
    for line in (lines[3], dup):
        line.expected.append(("SCH-003", "DUPLICATE_SOURCE_ID"))
    return lines[:4] + [dup]


def build_conflict_a(f: RowFactory, _scale: int) -> List[Line]:
    return _family(f)


def build_conflict_b(f: RowFactory, _scale: int) -> List[Line]:
    lines = _family(f)
    lines[1].values["NPI"] = synthetic_npi(102)   # P2: registered NPI(2) -> NPI(102)
    lines[1].kind = "npi_changed"
    lines[3].values["NPI"] = synthetic_npi(3)     # S4: now declares P3's registered NPI
    lines[3].kind = "npi_taken_by_other_entity"
    s6 = f.subparticipant(6, 3, NPI=synthetic_npi(5))  # new entity, NPI already S5's
    s6.kind = "new_entity_shared_npi"
    return lines + [s6]


def build_relationship_a(f: RowFactory, _scale: int) -> List[Line]:
    return [f.qhin(), f.participant(2), f.participant(3),
            f.subparticipant(4, 2), f.subparticipant(5, 2)]


def build_relationship_b(f: RowFactory, _scale: int) -> List[Line]:
    lines = build_relationship_a(f, _scale)
    lines[3] = f.subparticipant(4, 3)             # S4 moves from P2 to P3
    lines[3].kind = "partof_moved"
    return lines


def build_september_scale(f: RowFactory, scale: int) -> List[Line]:
    if scale < 2:
        raise ValueError("--scale must be at least 2 (one QHIN row plus one member)")
    lines: List[Line] = [f.qhin()]
    parent = 2
    for i in range(2, scale + 1):
        if i == 2 or i % 3 == 0:
            line = f.participant(i)
            parent = i
        else:
            line = f.subparticipant(i, parent)
        if i % 5 == 0:
            name, defect = SCALE_DEFECTS[(i // 5) % len(SCALE_DEFECTS)]
            defect(line, f, i)
            line.kind = name
        lines.append(line)
    return lines


_ACT_NOTE = ("ACT-001 INACTIVE_NEW_ENTRANT is CONDITIONAL: it fires only when the "
             "environment already holds an earlier delivery (quality_engine derives "
             "new_entrant_ids from the previous intake). On a fresh database the first "
             "registration is a baseline and only CON-003 INACTIVE_RECORD appears.")
_QHIN_NOTE = ("Line 2 (the QHIN row) is HELD by design: rule set 1.3.0 accepts only "
              "Participant/Subparticipant as sequoiaorgtype (REQ-001, HIGH, HUMAN_REQUIRED) "
              "and requires partOf (INT-002 MISSING_PART_OF, HIGH, NO_CORRECTION). Leave "
              "that hold in place; promotion synthesises the QHIN entity from "
              "orgManagingOrg regardless, and the member rows attach to it.")
_SCH002_NOTE = ("Every file also raises the dataset-level SCH-002 COLUMN_EMPTY_IN_DELIVERY "
                "(INFORMATIONAL) once, because transaction/NAIC/CCN/contact_company are "
                "empty as in the profiled delivery.")
_NPPES_NOTE = ("Synthetic 9xxxxxxxxx NPIs do not exist in NPPES. If automated "
               "verification runs, expect NPI-005 NPI_NOT_FOUND (MEDIUM, HUMAN_REQUIRED) "
               "or NPI-009 when the source is unavailable; that is verification-time "
               "behaviour, not a fixture defect.")

SCENARIOS: Tuple[Scenario, ...] = (
    Scenario(
        "CLEAN_B1", "CLEANB1",
        "1 QHIN + 2 Participants + 2 Subparticipants; every member row valid with a "
        "Luhn-valid synthetic NPI and every profiled field populated.",
        build_clean_b1,
        notes=[
            "Member rows (lines 3-6) raise NO HUMAN_REQUIRED finding and curate CLEAN; "
            "Participants carry BUS-003 PARTICIPANT_PARENT_IS_QHIN (INFORMATIONAL) only.",
            _QHIN_NOTE,
            "Because the QHIN row is held, the job outcome is 'Completed - With Exceptions' "
            "(1 held), not 'Completed - Clean'. 'Clean' requires zero held records; it is "
            "achievable only by disposing the QHIN row's REQ-001 finding (ACCEPT/WAIVE) and "
            "recomputing holds, which is itself a useful QA path.",
            _SCH002_NOTE, _NPPES_NOTE,
        ],
        workflow_uses=["baseline delivery", "reconciliation PASS", "clean review state",
                       "QA-outcome PASS after the QHIN hold is dispositioned"],
    ),
    Scenario(
        "MISSING_NPI", "MISSINGNPI",
        "All four member rows deliver an empty NPI with an empty hl7orgrole.",
        build_missing_npi,
        notes=[
            "NPI-001 NPI_NOT_SUPPLIED is INFORMATIONAL / NO_CORRECTION: no hold, no review "
            "case. Absence of an NPI is a recorded fact, never a failure.",
            "Setting hl7orgrole=provider on such a row would escalate NPI-001 to "
            "NPI_REQUIRED (HIGH, HUMAN_REQUIRED); this file deliberately does not.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["informational-only findings", "no-hold path"],
    ),
    Scenario(
        "MALFORMED_NPI", "MALFORMEDNPI",
        "9-digit, 11-digit, lettered 10-character and comma-separated NPI cells.",
        build_malformed_npi,
        notes=[
            "One primary NPI finding per value: NPI-002 NPI_LENGTH_INVALID (lines 3, 4), "
            "NPI-004 NPI_FORMAT_INVALID (line 5), NPI-002 MULTIPLE_NPI_IN_ONE_FIELD (line 6). "
            "All HIGH / HUMAN_REQUIRED -> HELD, one IDENTITY review case each.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["HELD records", "IDENTITY review cases", "CORRECT disposition"],
    ),
    Scenario(
        "LUHN_INVALID_NPI", "LUHNNPI",
        "Two rows carry a 10-digit NPI whose CMS check digit is wrong; two are valid controls.",
        build_luhn_invalid_npi,
        notes=[
            "NPI-003 NPI_CHECKSUM_INVALID (HIGH, HUMAN_REQUIRED) on lines 3 and 5 -> HELD, "
            "IDENTITY review cases. Lines 4 and 6 are valid controls and curate CLEAN.",
            _QHIN_NOTE, _SCH002_NOTE, _NPPES_NOTE,
        ],
        workflow_uses=["terminal finding (dispose -> RESOLVED, then a second decision is "
                       "refused with TERMINAL_FINDING_MESSAGE)",
                       "reopen attempt", "maker/checker on a HIGH finding"],
    ),
    Scenario(
        "MISSING_PARTOF", "MISSINGPARTOF",
        "A Participant and a Subparticipant with empty partOf; one Subparticipant whose "
        "partOf names an id nothing resolves.",
        build_missing_partof,
        notes=[
            "INT-002 MISSING_PART_OF is HIGH but NO_CORRECTION: the record is HELD "
            "(severity-based) yet NO review case is created (the bridge queues only "
            "HUMAN_REQUIRED/QA_REQUIRED). Lines 3 and 5.",
            "Line 7 contrasts with INT-002 PART_OF_UNRESOLVED (MEDIUM, HUMAN_REQUIRED): "
            "not held, but a RELATIONSHIP review case.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["held-without-case records", "RELATIONSHIP review cases",
                       "unresolved_parents in the promotion summary"],
    ),
    Scenario(
        "INACTIVE_NEW_ENTRANT", "INACTIVE",
        "Three member rows delivered active=0 (one as the spreadsheet form 0.0).",
        build_inactive_new_entrant,
        notes=[
            "CON-003 INACTIVE_RECORD (INFORMATIONAL) on lines 3, 5, 6; line 6 also "
            "CON-003 ACTIVE_FORMAT_NORMALIZED for the '0.0' round-trip form.",
            _ACT_NOTE,
            "Register this file ONCE per environment: a second registration finds the ids "
            "in the previous delivery / registry and ACT-001 no longer applies.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["ACT-001 held new entrants", "METHODOLOGY review cases",
                       "operational_status=inactive after promotion"],
    ),
    Scenario(
        "MULTIPLE_HIGH", "MULTIHIGH",
        "Three records each carrying two or more HIGH HUMAN_REQUIRED findings of "
        "different case classes.",
        build_multiple_high,
        notes=[
            "Line 4: NPI-003 (IDENTITY) + PUR-001 (METHODOLOGY). Line 5: NPI-003 (IDENTITY) "
            "+ CON-004 UNKNOWN_ORG_NODE_TYPE (METHODOLOGY) + INT-002 MISSING_PART_OF "
            "(held, no case). Line 6: NPI-003 (IDENTITY) + SO-002 MISSING_NAIC_FOR_PAYER (DQ).",
            "The DQ review bridge opens ONE case per (run, record, classification), so "
            "each of these records yields two cases of different classes.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["multi-finding records", "hold survives one disposition while a "
                       "second HIGH finding is undecided", "multi-class review cases"],
    ),
    Scenario(
        "NODE_TYPE", "NODETYPE",
        "organizationNodeType inside and outside the supported vocabulary, plus the "
        "`active` flag forms CON-003 checks.",
        build_node_type,
        notes=[
            "CON-004: lines 3-5 carry initiating-node / no-node / passthrough-node -> "
            "NODE_TYPE_IS_NOT_HIERARCHY (INFORMATIONAL); line 6 'root-node' -> "
            "UNKNOWN_ORG_NODE_TYPE (HIGH, HUMAN_REQUIRED, HELD).",
            "CON-003 is the `active` rule, not a node-type rule: line 7 empty -> "
            "MISSING_ACTIVE_VALUE (HIGH), line 8 'yes' -> UNSUPPORTED_ACTIVE_VALUE (HIGH), "
            "line 9 '1.0' -> ACTIVE_FORMAT_NORMALIZED (INFORMATIONAL).",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["METHODOLOGY review cases", "vocabulary holds"],
    ),
    Scenario(
        "DUPLICATE_ID", "DUPID",
        "Two lines share one `id` (distinct HCID, TEFCAID and name so only SCH-003 fires).",
        build_duplicate_id,
        notes=[
            "SCH-003 DUPLICATE_SOURCE_ID (CRITICAL, HUMAN_REQUIRED) on both lines 5 and 6 "
            "-> both HELD, IDENTITY review cases.",
            "snapshot_effects.assert_ids_unique refuses the snapshot (SnapshotRefused); the "
            "runner records a FAILED snapshot row, so reconciliation's 'Snapshot effects "
            "completed' check fails and the outcome is 'Partially Processed' until the "
            "duplicate is decided and POST .../snapshot-effects/retry completes.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["failed reconciliation", "snapshot-effects retry",
                       "CRITICAL review cases"],
    ),
    Scenario(
        "CONFLICTING_EVIDENCE_A", "CONFLICT",
        "First of a pair: the family promoted with its original NPIs.",
        build_conflict_a,
        notes=[
            "Register BEFORE CONFLICTING_EVIDENCE_B with an earlier received_date. It is a "
            "clean family (member rows CLEAN); its purpose is to register the NPIs that B "
            "then contradicts.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["baseline for identifier conflicts"],
    ),
    Scenario(
        "CONFLICTING_EVIDENCE_B", "CONFLICT",
        "Second of a pair: same ids as A; one entity changes its NPI, one declares an NPI "
        "registered to a different entity, one new entity declares an NPI already taken.",
        build_conflict_b,
        notes=[
            "No quality-engine finding is raised by these rows (the values are Luhn-valid). "
            "The conflicts are PROMOTION findings: NPI-008 NPI_EXISTING_VALUE_CONFLICT "
            "(HIGH, HUMAN_REQUIRED) on line 3 (registered NPI differs) and line 5 (delivers "
            "P3's registered NPI, which differs from its own registered value). Both are "
            "HELD (HELD_IDENTIFIER_CONFLICT) and open IDENTITY cases with "
            "submitted_value/existing_value side by side.",
            "Line 7 is a NEW entity declaring an NPI already registered to line 6's entity. "
            "On the create path promotion withholds the NPI identifier row "
            "(identifier_rows_skipped_shared_value) and raises NO NPI-008: two organisations "
            "declaring one NPI remain two entities. Documented so nobody expects a finding.",
            "Register AFTER A; the same received_date as A is acceptable for conflicts "
            "(no relationship edge changes), but a later date keeps the delta ordering sane.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        later_stage_findings=[" ".join(PROMOTION_CONFLICT)],
        workflow_uses=["identifier conflict (CONFIRM_EXISTING / CONFIRM_SUBMITTED)",
                       "held-by-conflict reconciliation check", "maker/checker"],
    ),
    Scenario(
        "RELATIONSHIP_A", "REL",
        "First of a pair: QHIN, P2, P3, and two Subparticipants both under P2.",
        build_relationship_a,
        notes=[
            "Register BEFORE RELATIONSHIP_B, with a received_date STRICTLY EARLIER (date "
            "granularity): the relationship boundary is received_at.date(), and a delivery "
            "whose boundary is not after the current edge's effective_date is refused as "
            "SNAPSHOT_MISMATCH.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["baseline snapshot", "snapshot approve (qalead)", "rollback baseline"],
    ),
    Scenario(
        "RELATIONSHIP_B", "REL",
        "Second of a pair: identical to A except line 5 (S4) moves its partOf from P2 to P3.",
        build_relationship_b,
        notes=[
            "Expected delta: CHANGED=1 (S4, material: PART_OF_CHANGED), UNCHANGED=4, NEW=0, "
            "NOT_PRESENT=0. Promotion: relationships_superseded=1, one ASSERTED edge with "
            "supersedes_relationship_id; the old edge gets end_date=boundary, status "
            "historical. If S4's entity has an ARC review record, an ArcStaleMark "
            "PART_OF_CHANGED is written.",
            "Rollback: scripts/rce_snapshot_rollback.py --intake <B intake> --plan then "
            "--apply restores the A edge (RESTORED) and retires the B edge (ROLLED_BACK). "
            "It fails closed if B's snapshot has been APPROVED and a later snapshot exists.",
            _QHIN_NOTE, _SCH002_NOTE,
        ],
        workflow_uses=["changed / stale", "relationship supersession", "snapshot rollback",
                       "stale-mark resolution"],
    ),
    Scenario(
        "SEPTEMBER_SCALE", "SCALE",
        "Large performance variant: one QHIN row plus (scale - 1) members, 80% clean and "
        "20% rotating through sixteen defect kinds.",
        build_september_scale,
        notes=[
            "Row 2 and every row whose index is a multiple of 3 is a Participant (about a "
            "third, close to the profiled 47%); the others are Subparticipants under the "
            "most recent Participant. Rows whose index is a multiple of 5 carry defect "
            "SCALE_DEFECTS[(index // 5) % 16], so Participants and Subparticipants share "
            "the same 20% defect rate.",
            "Duplicate ids are deliberately EXCLUDED from the mix: one SCH-003 refuses "
            "snapshot effects for the whole delivery and would mask the timing result.",
            "zip_leading_zero rows are AUTO_SAFE corrected (record_status CORRECTED); "
            "unresolved_partof rows reference <prefix>.0, which is never delivered.",
            _ACT_NOTE, _QHIN_NOTE, _SCH002_NOTE, _NPPES_NOTE,
        ],
        workflow_uses=["performance / capacity", "list and detail latency",
                       "bulk review-case creation"],
    ),
)

SCENARIO_BY_NAME: Dict[str, Scenario] = {s.name: s for s in SCENARIOS}

#: Workflow states that are produced by ACTIONS on a registered delivery, not
#: by file content, and the scenario file each should be driven on.
WORKFLOW_STATE_DRIVERS: Dict[str, Dict[str, str]] = {
    "reconciliation_pass": {
        "drive_on": "CLEAN_B1",
        "how": "Register; wait for SUCCEEDED; GET .../delivery-jobs/{id}/detail shows the "
               "persisted snapshot passed=true.",
    },
    "reconciliation_fail_missing_snapshot": {
        "drive_on": "DUPLICATE_ID",
        "how": "SCH-003 makes assert_ids_unique refuse snapshot effects; the FAILED snapshot "
               "tip fails the 'Snapshot effects completed' reconciliation check. Decide "
               "both SCH-003 findings, then POST .../deliveries/{intake}/snapshot-effects/retry.",
    },
    "stale_snapshot_changed_record": {
        "drive_on": "RELATIONSHIP_A then RELATIONSHIP_B",
        "how": "Create an ARC review on S4's entity after A; register B with a later "
               "received_date; GET .../deliveries/{B intake}/stale-marks lists PART_OF_CHANGED.",
    },
    "snapshot_rollback": {
        "drive_on": "RELATIONSHIP_B",
        "how": "scripts/rce_snapshot_rollback.py --intake <B> --plan, then --apply --actor "
               "<email> --role program_manager --reason <why>.",
    },
    "terminal_finding": {
        "drive_on": "LUHN_INVALID_NPI",
        "how": "POST .../issues/{issue}/dispositions with ACCEPT or REJECT and a reason; the "
               "finding reaches a terminal resolution. A second POST on the same finding is "
               "refused with TERMINAL_FINDING_MESSAGE (HTTP 409 path) and the attempt is audited.",
    },
    "reopen": {
        "drive_on": "LUHN_INVALID_NPI",
        "how": "There is NO reopen endpoint at this revision; TERMINAL_FINDING_MESSAGE names "
               "'the approved reopen workflow' as a separate, human-governed process. The QA "
               "case is therefore: confirm the refusal, and confirm the audit row of the attempt.",
    },
    "maker_checker": {
        "drive_on": "CONFLICTING_EVIDENCE_B (or any QA_REQUIRED finding)",
        "how": "transition_issue refuses APPROVED on a QA_REQUIRED finding without a qa_actor "
               "distinct from the reviewer; snapshot approval refuses the registrant and the "
               "system as approver (POST .../deliveries/{intake}/snapshot/approve, role qalead).",
    },
    "review_case_lifecycle": {
        "drive_on": "MULTIPLE_HIGH",
        "how": "Each record opens two DQ review cases of different classes; claim, determine "
               "and QA them independently and observe the hold released only when every "
               "HIGH finding on the record is decided.",
    },
    "qa_outcome_pass": {
        "drive_on": "CLEAN_B1",
        "how": "Dispose the QHIN row's REQ-001 (WAIVE/ACCEPT), recompute holds, re-run "
               "reconciliation: outcome 'Completed - Clean', review state advances.",
    },
    "qa_outcome_fail": {
        "drive_on": "MALFORMED_NPI",
        "how": "Leave the four HELD records undecided: outcome stays "
               "'Completed - With Exceptions'; the exception ledger lists four IDENTITY holds.",
    },
    "performance": {
        "drive_on": "SEPTEMBER_SCALE",
        "how": "Register the default 25,000-row file; time the job stages and the list/detail "
               "endpoints; ~20% of rows carry findings.",
    },
}


# ── writing ──────────────────────────────────────────────────────────────────

def render(lines: Sequence[Line]) -> bytes:
    """The delivery bytes: header + one line per row, CRLF, UTF-8, no BOM."""
    out: List[str] = [DELIMITER.join(RCE_COLUMNS)]
    for line in lines:
        values = [line.values[c] for c in RCE_COLUMNS]
        for value in values:
            if DELIMITER in value or "\r" in value or "\n" in value:
                raise ValueError(f"cell {value!r} on {line.id} would break the delimited format")
        out.append(DELIMITER.join(values))
    return (LINE_TERMINATOR.join(out) + LINE_TERMINATOR).encode(ENCODING)


def _expected_by_line(lines: Sequence[Line]) -> Dict[str, List[str]]:
    """Line number (header is line 1) -> sorted 'RULE issue_type' strings."""
    return {str(index): sorted(f"{rule} {issue}" for rule, issue in line.expected)
            for index, line in enumerate(lines, start=2)}


def _summarise(lines: Sequence[Line]) -> Dict[str, object]:
    rule_codes: Dict[str, int] = {}
    kinds: Dict[str, int] = {}
    held = 0
    human_required = 0
    for line in lines:
        kinds[line.kind] = kinds.get(line.kind, 0) + 1
        line_holds = False
        for rule, issue in line.expected:
            key = f"{rule} {issue}"
            rule_codes[key] = rule_codes.get(key, 0) + 1
            severity, authority = FINDING_CATALOG[(rule, issue)]
            if severity in ("CRITICAL", "HIGH"):
                line_holds = True
            if authority == "HUMAN_REQUIRED":
                human_required += 1
        held += 1 if line_holds else 0
    return {
        "row_count": len(lines),
        "expected_rule_codes": dict(sorted(rule_codes.items())),
        "expected_held_records": held,
        "expected_human_required_findings": human_required,
        "rows_by_kind": dict(sorted(kinds.items())),
    }


def generate(out_dir: Path, *, scale: int = DEFAULT_SCALE,
             only: Optional[Iterable[str]] = None) -> Dict[str, object]:
    """Write every selected scenario and the manifest. Returns the manifest."""
    selected = list(SCENARIOS)
    if only is not None:
        wanted = [name.strip() for name in only if name.strip()]
        unknown = sorted(set(wanted) - set(SCENARIO_BY_NAME))
        if unknown:
            raise ValueError(f"unknown scenario(s) {unknown}; --list shows the names")
        selected = [s for s in SCENARIOS if s.name in wanted]

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, object] = {
        "generator": "scripts/qa_fixture_pack.py",
        "generator_version": GENERATOR_VERSION,
        "target_rule_set_version": TARGET_RULE_SET_VERSION,
        "synthetic": True,
        "statement": "SYNTHETIC / NOT GOVERNMENT DATA. Never contains real NPIs, names, "
                     "or addresses.",
        "oid_arc": OID_ARC,
        "delimiter": "pipe",
        "line_terminator": "CRLF",
        "encoding": ENCODING,
        "header": list(RCE_COLUMNS),
        "scale": scale,
        "scenarios": {},
        "workflow_state_drivers": WORKFLOW_STATE_DRIVERS,
        "finding_catalog": {f"{rule} {issue}": {"severity": sev, "correction_authority": auth}
                            for (rule, issue), (sev, auth) in FINDING_CATALOG.items()},
        "registration": {
            "endpoint": "POST /api/tefca/rce/official-deliveries",
            "role": "program_manager (or admin)",
            "form": {"file": "<the .psv>", "delivery_period": "<label>",
                     "source": "SYNTHETIC-QA (not ONC/RCE)", "received_date": "YYYY-MM-DD",
                     "notes": "<free text>", "delimiter": "pipe"},
            "idempotency": "job identity = sha256(file) + delivery_period + received_date; "
                           "re-registering the same triple returns the existing job.",
            "documentation": "docs/qa/fixtures/QA_FIXTURE_PACK.md",
        },
    }
    scenarios: Dict[str, object] = manifest["scenarios"]  # type: ignore[assignment]
    for scenario in selected:
        lines = scenario.lines(scale)
        blob = render(lines)
        path = out_dir / scenario.filename
        path.write_bytes(blob)
        factory = RowFactory(scenario.tag)
        entry: Dict[str, object] = {
            "filename": scenario.filename,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "size_bytes": len(blob),
            "oid_prefix": factory.prefix,
            "summary": scenario.summary,
            "notes": list(scenario.notes),
            "workflow_uses": list(scenario.workflow_uses),
            "later_stage_findings": list(scenario.later_stage_findings),
        }
        entry.update(_summarise(lines))
        if scenario.name != "SEPTEMBER_SCALE":
            entry["expected_by_line"] = _expected_by_line(lines)
            entry["ids_by_line"] = {str(i): line.id for i, line in enumerate(lines, start=2)}
        scenarios[scenario.name] = entry

    (out_dir / MANIFEST_NAME).write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(ENCODING))
    return manifest


# ── CLI ──────────────────────────────────────────────────────────────────────

def _default_out() -> Path:
    return Path(__file__).resolve().parents[1] / "qa-fixtures" / "out"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=_default_out(),
                        help="output directory (default: <repo>/qa-fixtures/out)")
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE,
                        help=f"row count for SEPTEMBER_SCALE (default {DEFAULT_SCALE})")
    parser.add_argument("--only", type=str, default=None,
                        help="comma-separated scenario names to generate")
    parser.add_argument("--list", action="store_true",
                        help="list the scenarios and exit; writes nothing")
    args = parser.parse_args(argv)

    if args.list:
        width = max(len(s.name) for s in SCENARIOS)
        for scenario in SCENARIOS:
            print(f"{scenario.name:<{width}}  {scenario.filename:<42}  {scenario.summary}")
        return 0

    refuse_production()
    only = args.only.split(",") if args.only else None
    manifest = generate(args.out, scale=args.scale, only=only)
    scenarios: Dict[str, Dict[str, object]] = manifest["scenarios"]  # type: ignore[assignment]
    print(f"wrote {len(scenarios)} scenario file(s) + {MANIFEST_NAME} to {args.out.resolve()}")
    for name, entry in scenarios.items():
        print(f"  {entry['filename']:<42} rows={entry['row_count']:<6} "
              f"held={entry['expected_held_records']:<5} sha256={str(entry['sha256'])[:12]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
