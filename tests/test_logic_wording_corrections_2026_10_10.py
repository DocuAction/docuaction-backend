"""Logic wording corrections (2026-10-10), pinned so they cannot regress.

1. INT-003: a Subparticipant technically parented straight to a QHIN is an
   OBSERVATION for analyst determination. Direct technical parenting alone
   does not prove an invalid agreement chain. Condition, severity (MEDIUM)
   and disposition (HUMAN_REQUIRED) are unchanged from 1.0.0; only the
   wording changed, so the rule is versioned 1.1.0 and the rule set 1.4.0.
2. Shared NPI / HCID / TEFCAID across registry entities: a shared identifier
   ALONE does not establish prohibited Node duplication. The registry
   verifier records it as a MEDIUM observation (entity `in_review`), never
   as an `exception` on the identifier match by itself. ID-006 (shared
   TEFCAID inside one delivery) stays INFO; ID-003 (shared HCID) stays
   MEDIUM / human-required.
3. No report template or report-data module makes an unqualified
   "fully verified" claim; the only "verified" wording is the split
   `verified / verified — checks incomplete / verified — checks not recorded`.

Pure unit tests: no database, no network.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.tefca_registry import verification as v
from app.tefca_registry.rce import field_map as fm
from app.tefca_registry.rce import quality_rules as qr
from app.tefca_registry.rce.reader import PARSE_OK

ROOT = Path(__file__).resolve().parents[1]


# ── INT-003 ──────────────────────────────────────────────────────────────────

def _ctx(**over):
    values = {f: "" for f in fm.RCE_FIELDS}
    values.update({
        "id": "2.16.840.1.113883.3.9999.1",
        "sequoiaorgtype": "Subparticipant",
        "orgManagingOrg": "2.16.840.1.113883.3.9960",
        "partOf": "2.16.840.1.113883.3.9960",
    })
    values.update(over)
    dataset = {"expected_field_count": 41, "known_source_ids": set(),
               "qhin_oids": set(fm.OBSERVED_QHIN_OIDS),
               "tefcaid_duplicates": {}, "hcid_duplicates": {}, "npi_duplicates": {}}
    return qr.RecordContext(line_number=2, parse_status=PARSE_OK,
                            field_count=len(values), values=values, dataset=dataset)


def test_int003_is_versioned_1_1_0_and_rule_set_1_4_0():
    rule = qr.RULE_BY_ID["INT-003"]
    assert rule.version == "1.1.0"
    assert tuple(int(x) for x in qr.RULE_SET_VERSION.split(".")) >= (1, 4, 0)


def test_int003_wording_does_not_assert_an_invalid_agreement_chain():
    out = qr.RULE_BY_ID["INT-003"].evaluate(_ctx()) or []
    assert len(out) == 1
    f = out[0]
    assert f.issue_type == "SUBPARTICIPANT_PARENTED_TO_QHIN"
    assert f.severity == qr.MEDIUM
    assert f.correction_authority == qr.HUMAN_REQUIRED
    assert "technically parented" in f.description
    assert "does not by itself prove an invalid agreement chain" in f.description
    assert "analyst determination" in f.description
    assert "invalid" not in f.description.replace("invalid agreement chain", "")
    assert "the hierarchy is not rewritten" in f.description


def test_int003_condition_unchanged_participant_and_other_parent_do_not_fire():
    assert not qr.RULE_BY_ID["INT-003"].evaluate(_ctx(sequoiaorgtype="Participant"))
    assert not qr.RULE_BY_ID["INT-003"].evaluate(_ctx(partOf="2.16.840.1.113883.3.1234"))
    assert not qr.RULE_BY_ID["INT-003"].evaluate(_ctx(partOf=""))


# ── shared identifiers inside one delivery (RCE rules) ───────────────────────

def test_id006_shared_tefcaid_stays_informational_and_id003_stays_human_required():
    assert qr.RULE_BY_ID["ID-006"].severity() == qr.INFO
    assert qr.RULE_BY_ID["ID-003"].severity() == qr.MEDIUM
    c = _ctx(TEFCAID="TEFCA-SHARED-1", HCID="2.16.840.1.113883.3.7777")
    c.dataset["tefcaid_duplicates"] = {"TEFCA-SHARED-1": 3}
    c.dataset["hcid_duplicates"] = {"2.16.840.1.113883.3.7777": 2}
    id006 = qr.RULE_BY_ID["ID-006"].evaluate(c) or []
    id003 = qr.RULE_BY_ID["ID-003"].evaluate(c) or []
    assert id006 and id006[0].severity == qr.INFO and id006[0].correction_authority == qr.NO_CORRECTION
    assert "NOT merged" in id006[0].description
    assert id003 and id003[0].severity == qr.MEDIUM and id003[0].correction_authority == qr.HUMAN_REQUIRED


def test_no_rce_rule_turns_a_shared_npi_alone_into_a_finding():
    c = _ctx(NPI="1234567893")
    c.dataset["npi_duplicates"] = {"1234567893": 4}
    for rule in qr.RULES:
        for f in (rule.evaluate(c) or []):
            assert "shared" not in f.description.lower() or f.rule_id in ("ID-003", "ID-006"), (rule.rule_id, f.description)


# ── shared identifiers across registry entities (registry verifier) ──────────

def _ident(kind, value, status="active"):
    return SimpleNamespace(identifier_type=kind, identifier_value=value,
                           identifier_status=status, end_date=None)


def _entity():
    return SimpleNamespace(id=uuid.uuid4(), is_active=True, entity_type="provider",
                           exchange_purposes={"purposes": ["treatment"]})


def _registry_ctx(e, idents, *, npi_owners=None, hcid_owners=None, tefcaid_owners=None):
    ctx = v._Ctx()
    ctx.idents_by_entity = {e.id: idents}
    ctx.npi_owners = npi_owners or {}
    ctx.hcid_owners = hcid_owners or {}
    ctx.tefcaid_owners = tefcaid_owners or {}
    return ctx


def test_shared_npi_and_tefcaid_alone_are_medium_observations_not_exceptions():
    e = _entity()
    other = uuid.uuid4()
    idents = [_ident("tefcaid", "TEFCA-1"), _ident("hcid", "2.16.840.1.113883.3.1"),
              _ident("npi", "1234567893")]
    ctx = _registry_ctx(e, idents,
                        npi_owners={"1234567893": {e.id, other}},
                        hcid_owners={"2.16.840.1.113883.3.1": {e.id, other}},
                        tefcaid_owners={"TEFCA-1": {e.id, other}})
    findings = v._check_identity(e, ctx)
    kinds = {f["finding_type"]: f for f in findings}
    assert {"npi_duplicate", "hcid_duplicate", "identifier_conflict"} <= set(kinds)
    for k in ("npi_duplicate", "hcid_duplicate", "identifier_conflict"):
        assert kinds[k]["severity"] == "medium", kinds[k]
        assert "determination" in kinds[k]["description"].lower()
        assert kinds[k]["evidence"]["evidence_basis"] == "shared_identifier_only"
    assert "does not establish prohibited Node duplication" in kinds["npi_duplicate"]["description"]
    assert "does not establish prohibited Node duplication" in kinds["identifier_conflict"]["description"]
    assert "Directory SOP v1.1" in kinds["identifier_conflict"]["description"]
    # The identifier match by itself never makes the entity an exception.
    assert v._entity_status(findings) == "in_review"


def test_unshared_identifiers_raise_nothing_and_entity_is_verified():
    e = _entity()
    idents = [_ident("tefcaid", "TEFCA-2"), _ident("hcid", "2.16.840.1.113883.3.2"),
              _ident("npi", "1234567893")]
    ctx = _registry_ctx(e, idents, npi_owners={"1234567893": {e.id}},
                        hcid_owners={"2.16.840.1.113883.3.2": {e.id}},
                        tefcaid_owners={"TEFCA-2": {e.id}})
    findings = v._check_identity(e, ctx)
    assert findings == []
    assert v._entity_status(findings) == "verified"


def test_genuine_identity_defects_still_raise_high_or_critical():
    """Unchanged: a Luhn-invalid NPI and several active NPIs on ONE entity
    remain high; a missing mandatory TEFCAID/HCID remains critical."""
    e = _entity()
    idents = [_ident("npi", "1234567890"), _ident("npi", "1234567893")]
    ctx = _registry_ctx(e, idents, npi_owners={"1234567890": {e.id}, "1234567893": {e.id}})
    findings = v._check_identity(e, ctx)
    sev = {(f["finding_type"], f["severity"]) for f in findings}
    assert ("npi_invalid", "high") in sev
    assert ("identifier_conflict", "high") in sev  # multiple active NPIs on one entity
    assert ("identifier_missing", "critical") in sev
    assert v._entity_status(findings) == "exception"


# ── no unqualified "fully verified" claim ────────────────────────────────────

_FORBIDDEN = re.compile(r"fully verified|all sources verified|fully cleared|all checks passed", re.I)


def test_report_templates_and_data_modules_make_no_unqualified_fully_verified_claim():
    offenders = []
    for folder in ("app/reports/templates", "app/reports/data"):
        for p in (ROOT / folder).rglob("*"):
            if p.suffix in (".html", ".py") and _FORBIDDEN.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], offenders


def test_verified_counts_are_split_by_completeness_in_the_report_data_service():
    from app.reports.data import report_data_service as rds
    from app.tefca_registry.rce import verification_completeness as vc
    assert vc.VERIFIED_CHECKS_INCOMPLETE == "verified_checks_incomplete"
    assert vc.VERIFIED_CHECKS_NOT_RECORDED == "verified_checks_not_recorded"
    src = Path(rds.__file__).read_text(encoding="utf-8")
    assert "split_verified_counts" in src or "verified_checks_not_recorded" in src
