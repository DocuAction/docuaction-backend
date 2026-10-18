"""Reference-snapshot preflight (IQVIA extracts) -- Part B.

Synthetic files only (the same fabricated layout test_iqvia_import.py uses);
no IQVIA content. Proves, for the one enforcement point both ingestion
mechanisms share (`iqvia_import._import_csv`):

  * the structural checks themselves (pure, no DB);
  * the false pass this closes: an extract whose identity column is
    renamed used to import "successfully", reject every row, stage nothing
    and then RECONCILE as an approvable empty snapshot;
  * shadow by default: with the flag off the import behaves as before and
    the preflight result is only RECORDED -- but the snapshot can no longer
    reconcile;
  * enforced with the flag on: refused before any row is staged, as one
    technical issue;
  * a held check (expected column absent) does not block the extract.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401
from test_iqvia_import import HCO_HEADER, write_csv

GOOD_ROWS = [[f"{SYN}-HCO-1", "", "012345", f"{SYN} Clinic One", "02101"],
             [f"{SYN}-HCO-2", "", "", f"{SYN} Clinic Two", "02101"]]


def _codes(result):
    return {f["code"]: f for f in result["findings"]}


# ── pure structural checks ───────────────────────────────────────────────────

def test_a_well_formed_extract_is_clear(tmp_path):
    f = write_csv(tmp_path / "ok.csv", HCO_HEADER, GOOD_ROWS)
    r = rp.preflight_reference_file(f, sm.SOURCE_IQVIA_HCO)
    assert r["gate"] == pm.GATE_CLEAR
    assert r["rows_sampled"] == 2 and r["tail_checked"] is True
    assert r["originals_modified"] is False
    assert set(_codes(r)) == {"REF-SCH-011"}          # extra columns: informational only


def test_missing_identity_column_blocks_as_one_technical_issue(tmp_path):
    header = [h for h in HCO_HEADER if h != "HCO_HCE_ID"]
    f = write_csv(tmp_path / "missing.csv", header, [r[1:] for r in GOOD_ROWS])
    r = rp.preflight_reference_file(f, sm.SOURCE_IQVIA_HCO)
    assert r["gate"] == pm.GATE_BLOCKED
    blocked = [x for x in r["findings"] if x["disposition"] == pm.DISP_BLOCKED]
    assert len(blocked) == 1 and blocked[0]["code"] == "REF-SCH-001"


def test_renamed_identity_column_is_reported_and_never_auto_mapped(tmp_path):
    header = ["hco_hce_id" if h == "HCO_HCE_ID" else h for h in HCO_HEADER]
    f = write_csv(tmp_path / "renamed.csv", header, GOOD_ROWS)
    r = rp.preflight_reference_file(f, sm.SOURCE_IQVIA_HCO)
    assert r["gate"] == pm.GATE_BLOCKED
    assert _codes(r)["REF-SCH-004"]["evidence"] == {"delivered_name": "hco_hce_id",
                                                    "expected_name": "HCO_HCE_ID"}


def test_reordered_columns_are_not_a_fault(tmp_path):
    header = list(reversed(HCO_HEADER))
    f = write_csv(tmp_path / "reordered.csv", header, [list(reversed(r)) for r in GOOD_ROWS])
    assert rp.preflight_reference_file(f, sm.SOURCE_IQVIA_HCO)["gate"] == pm.GATE_CLEAR


def test_missing_expected_column_holds_one_check_and_does_not_block(tmp_path):
    header = [h for h in HCO_HEADER if h != "ORG_NPI"]
    f = write_csv(tmp_path / "no_npi.csv", header, [[r[0]] + r[2:] for r in GOOD_ROWS])
    r = rp.preflight_reference_file(f, sm.SOURCE_IQVIA_HCO)
    assert r["gate"] == pm.GATE_CLEAR_WITH_FINDINGS
    held = _codes(r)["REF-SCH-002"]
    assert held["execution"] == pm.EXEC_HELD and held["disposition"] == pm.DISP_OPEN
    assert r["held_checks"] == [{"column": "ORG_NPI",
                                 "held_check": "NPI-keyed organisation matching"}]


def _text(p, text):
    p.write_text(text, encoding="utf-8", newline="")
    return p


def _bytes(p, data):
    p.write_bytes(data)
    return p


_HDR = ",".join(HCO_HEADER) + "\r\n"
_ROW0 = ",".join(GOOD_ROWS[0]) + "\r\n"
_ROW1 = ",".join(GOOD_ROWS[1]) + "\r\n"


@pytest.mark.parametrize("label,writer,code", [
    ("duplicate_header",
     lambda p: write_csv(p, HCO_HEADER + ["ORG_NPI"], [r + [""] for r in GOOD_ROWS]),
     "REF-SCH-003"),
    ("wrong_delimiter",
     lambda p: _text(p, "|".join(HCO_HEADER) + "\r\n" + "|".join(GOOD_ROWS[0]) + "\r\n"),
     "REF-SCH-009"),
    ("empty_file", lambda p: _text(p, ""), "REF-SCH-005"),
    ("not_utf8", lambda p: _bytes(p, b"HCO_HCE_ID,ORG_NPI\r\n\xff\xfe\xfa,1\r\n"),
     "REF-SCH-006"),
    ("shifted_record",
     lambda p: _text(p, _HDR + _ROW0.rstrip("\r\n") + ",EXTRA\r\n" + _ROW1),
     "REF-SCH-007"),
    ("truncated_final_record", lambda p: _text(p, _HDR + _ROW0 + f"{SYN}-HCO-CUT,12"),
     "REF-SCH-008"),
])
def test_structural_faults_block(tmp_path, label, writer, code):
    f = writer(tmp_path / f"{label}.csv")
    r = rp.preflight_reference_file(f, sm.SOURCE_IQVIA_HCO)
    assert r["gate"] == pm.GATE_BLOCKED, (label, r["findings"])
    assert code in _codes(r), (label, sorted(_codes(r)))


def test_bom_is_informational_and_the_first_column_still_matches(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(b"\xef\xbb\xbf" + (",".join(HCO_HEADER) + "\r\n"
                                     + ",".join(GOOD_ROWS[0]) + "\r\n").encode("utf-8"))
    r = rp.preflight_reference_file(p, sm.SOURCE_IQVIA_HCO)
    assert r["gate"] == pm.GATE_CLEAR and "REF-SCH-010" in _codes(r)
    assert "REF-SCH-001" not in _codes(r)


def test_every_registered_reference_source_has_a_pinned_schema_version():
    for source in (sm.SOURCE_IQVIA_HCO, sm.SOURCE_IQVIA_HCP, sm.SOURCE_IQVIA_AFFILIATION):
        assert rp.REFERENCE_SCHEMAS[source]["schema_version"]
        assert rp.REFERENCE_SCHEMAS[source]["required"]
    with pytest.raises(ValueError):
        rp.preflight_reference_file(__file__, "NOT_A_SOURCE")


# ── the importer: shadow by default, enforced by flag ────────────────────────

async def _staged(db, snapshot_id) -> int:
    return int((await db.execute(select(func.count()).select_from(sm.IqviaHcoObservation).where(
        sm.IqviaHcoObservation.source_snapshot_id == snapshot_id))).scalar() or 0)


@pytest.mark.asyncio
async def test_flag_off_a_renamed_identity_column_no_longer_reconciles_as_an_empty_snapshot(
        rolled_back_db, tmp_path):
    """The false pass, reproduced on the unenforced (default) path."""
    db = rolled_back_db
    assert settings.ENABLE_PREFLIGHT_ENFORCEMENT is False
    header = ["hco_hce_id" if h == "HCO_HCE_ID" else h for h in HCO_HEADER]
    f = write_csv(tmp_path / "renamed.csv", header, GOOD_ROWS)

    summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-ref-pf-off", created_by=SYN)
    # Unchanged legacy behaviour with the flag off: the import completes,
    # every row rejected, nothing staged.
    assert summary.completed and summary.rows_read == 2 and summary.rows_rejected == 2
    assert await _staged(db, summary.snapshot_id) == 0

    snapshot = await db.get(sm.SourceSnapshot, summary.snapshot_id)
    recorded = snapshot.metadata_["reference_preflight"]
    assert recorded["gate"] == pm.GATE_BLOCKED and recorded["enforced"] is False
    assert any(x["code"] == "REF-SCH-004" for x in recorded["findings"])

    # Before 2026-10-04 this read reconciles=True (0 expected == 0 staged).
    check = await ii.verify_snapshot_staged_completely(db, summary.snapshot_id)
    assert check["expected_staged_count"] == 0 and check["staged_row_count"] == 0
    assert check["reconciles"] is False
    assert len(check["refusal_reasons"]) == 2


@pytest.mark.asyncio
async def test_flag_on_a_blocked_extract_is_refused_before_any_row_is_staged(
        rolled_back_db, tmp_path, monkeypatch):
    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_PREFLIGHT_ENFORCEMENT", True)
    header = [h for h in HCO_HEADER if h != "HCO_HCE_ID"]
    f = write_csv(tmp_path / "missing.csv", header, [r[1:] for r in GOOD_ROWS])

    with pytest.raises(rp.ReferencePreflightBlocked, match="REF-SCH-001"):
        await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-ref-pf-on", created_by=SYN)

    snapshot = (await db.execute(select(sm.SourceSnapshot).where(
        sm.SourceSnapshot.snapshot_label == f"{SYN}-ref-pf-on"))).scalars().one()
    assert snapshot.status == sm.SNAPSHOT_PENDING           # never approvable
    assert snapshot.metadata_["reference_preflight"]["enforced"] is True
    assert await _staged(db, snapshot.id) == 0
    assert "rows_rejected" not in (snapshot.metadata_ or {})   # no per-row findings at all
    check = await ii.verify_snapshot_staged_completely(db, snapshot.id)
    assert check["reconciles"] is False


@pytest.mark.asyncio
async def test_flag_on_a_clean_extract_imports_and_reconciles(rolled_back_db, tmp_path, monkeypatch):
    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_PREFLIGHT_ENFORCEMENT", True)
    f = write_csv(tmp_path / "ok.csv", HCO_HEADER, GOOD_ROWS)
    summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-ref-pf-ok", created_by=SYN)
    assert summary.rows_staged == 2
    check = await ii.verify_snapshot_staged_completely(db, summary.snapshot_id)
    assert check["reconciles"] is True and check["refusal_reasons"] == []
    assert check["reference_preflight_gate"] == pm.GATE_CLEAR


@pytest.mark.asyncio
async def test_flag_on_a_held_check_does_not_stop_the_import(rolled_back_db, tmp_path, monkeypatch):
    db = rolled_back_db
    monkeypatch.setattr(settings, "ENABLE_PREFLIGHT_ENFORCEMENT", True)
    header = [h for h in HCO_HEADER if h != "ORG_NPI"]
    f = write_csv(tmp_path / "no_npi.csv", header, [[r[0]] + r[2:] for r in GOOD_ROWS])
    summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-ref-pf-held", created_by=SYN)
    assert summary.rows_staged == 2                       # independent content proceeds
    snapshot = await db.get(sm.SourceSnapshot, summary.snapshot_id)
    assert snapshot.metadata_["reference_preflight"]["gate"] == pm.GATE_CLEAR_WITH_FINDINGS
    assert snapshot.metadata_["reference_preflight"]["held_checks"][0]["column"] == "ORG_NPI"
