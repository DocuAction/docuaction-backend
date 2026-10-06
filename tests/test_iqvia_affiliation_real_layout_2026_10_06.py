"""The IQVIA HCP_AFFIL extract as actually delivered (2026-09-21, 191 columns, 7,551,269 records).

The importer was written against a layout-only guess (`AFFIL_TYPE_CD`). The delivered file has no such
column: a PROVIDER affiliation is described by AFFL_TYP_ID/AFFL_GRP_CD and a CONTACT affiliation by
TITL_TYP_ID/TITL_CATG_CD (10.4% of rows, no AFFL_* value). Left as it was, every row's affiliation_type
was NULL, and PostgreSQL does not treat NULLs as equal in a unique constraint, so a resumed run inserted
the same rows again. Each row also copied all 191 columns into JSONB (~3.4 KB/row, ~26 GB for the file).

Entirely synthetic values; the header shape (names only) is the layout's.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import func, select

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401
from test_iqvia_import import write_csv

pytestmark = pytest.mark.regression

# a representative slice of the 191-column layout: relationship + identifiers, plus descriptive columns that the
# file repeats on every row and that the importer must not copy
HEADER = ["HCP_HCE_ID", "OK_INDV_ID", "FRST_NM", "LAST_NM", "YOB", "NPI", "AFFL_TYP_ID", "AFFL_TYP_DESC",
          "AFFL_GRP_CD", "AFFL_GRP_DESC", "TITL_TYP_ID", "TITL_TYP_DESC", "TITL_CATG_CD", "TITL_CATG_DESC",
          "HCO_HCE_ID", "OK_WKP_ID", "BUS_NM", "ADDR_ID", "ADDR_LN_1_TXT", "ORG_NPI", "ORG_CCN_ID",
          "MON_SLOT_1_STRT_TM", "ORG_SPANISH_IND"]


def _row(hcp, hco, *, typ="", grp="", titl="", extra=None):
    d = dict.fromkeys(HEADER, "")
    d.update(HCP_HCE_ID=hcp, OK_INDV_ID=f"{SYN}-OK{hcp}", FRST_NM="SYNFIRST", LAST_NM="SYNLAST", YOB="1970",
             NPI="1999999999", HCO_HCE_ID=hco, OK_WKP_ID=f"{SYN}-WK{hco}", BUS_NM="SYN ORG", ADDR_ID="777",
             ADDR_LN_1_TXT="1 SYNTHETIC WAY", ORG_NPI="1888888888", MON_SLOT_1_STRT_TM="08:00",
             ORG_SPANISH_IND="Y", AFFL_TYP_ID=typ, AFFL_GRP_CD=grp, TITL_TYP_ID=titl)
    d.update(extra or {})
    return [d[h] for h in HEADER]


class TestAffiliationKind:
    def test_provider_affiliation_is_the_affl_type(self):
        r = dict(zip(HEADER, _row("1", "2", typ="3", grp="ATT")))
        assert ii._affiliation_payload_and_keys(r)["affiliation_type"] == "3"

    def test_contact_affiliation_is_marked_and_never_null(self):
        r = dict(zip(HEADER, _row("1", "2", titl="42")))
        assert ii._affiliation_payload_and_keys(r)["affiliation_type"] == "CONTACT:42"

    def test_neither_is_a_constant_not_null(self):
        r = dict(zip(HEADER, _row("1", "2")))
        assert ii._affiliation_payload_and_keys(r)["affiliation_type"] == "UNTYPED"

    def test_earlier_fixture_spelling_still_accepted(self):
        built = ii._affiliation_payload_and_keys({"HCP_HCE_ID": "1", "HCO_HCE_ID": "2", "AFFIL_TYPE_CD": "ATT"})
        assert built["affiliation_type"] == "ATT" and built["payload"]["AFFIL_TYPE_CD"] == "ATT"

    def test_missing_identity_is_still_rejected(self):
        assert ii._affiliation_payload_and_keys(dict(zip(HEADER, _row("", "2", typ="3")))) is None
        assert ii._affiliation_payload_and_keys(dict(zip(HEADER, _row("1", "", typ="3")))) is None


class TestPayloadIsProjected:
    def test_descriptive_columns_are_not_copied_but_the_full_row_is_hashed(self):
        r = dict(zip(HEADER, _row("1", "2", typ="3", grp="ATT")))
        built = ii._affiliation_payload_and_keys(r)
        for dropped in ("FRST_NM", "LAST_NM", "YOB", "ADDR_LN_1_TXT", "MON_SLOT_1_STRT_TM", "ORG_SPANISH_IND", "BUS_NM"):
            assert dropped not in built["payload"], dropped
        for kept in ("HCP_HCE_ID", "HCO_HCE_ID", "AFFL_TYP_ID", "AFFL_GRP_CD", "TITL_TYP_ID", "NPI", "ORG_NPI"):
            assert kept in built["payload"], kept
        # provenance: the row hash still covers the WHOLE source row, so a stored row verifies against the original
        assert ii._row_sha256(r) == hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def test_statement_size_is_capped(self):
        assert 1 <= ii.MAX_ROWS_PER_STATEMENT <= 200


class TestImportAndResume:
    async def test_import_stages_every_row_and_a_resume_adds_none(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        rows = [_row(str(1000 + i), "5000", typ=str(i % 4)) for i in range(230)]            # > 2 statements
        rows += [_row("2000", "5000", titl="7"), _row("2000", "5000", titl="8"), _row("2001", "5000", titl="7")]
        f = write_csv(tmp_path / "affil_real.csv", HEADER, rows)
        first = await ii.import_affiliation_csv(db, file_path=f, label=f"{SYN}-affil-real", created_by=SYN)
        assert first.rows_read == 233 and first.rows_staged == 233 and first.rows_rejected == 0

        again = await ii.import_affiliation_csv(db, file_path=f, label=f"{SYN}-affil-real", created_by=SYN,
                                                snapshot_id=first.snapshot_id)
        assert again.rows_staged == 0 and again.rows_already_staged == 233, \
            "a resumed run must not insert the contact-affiliation rows a second time"
        total = (await db.execute(select(func.count()).select_from(sm.IqviaAffiliationObservation).where(
            sm.IqviaAffiliationObservation.source_snapshot_id == first.snapshot_id))).scalar()
        assert total == 233
        verdict = await ii.verify_snapshot_staged_completely(db, first.snapshot_id)
        assert verdict["reconciles"] is True
