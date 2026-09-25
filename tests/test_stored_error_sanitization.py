"""Stored error text is masked on READ (2026-09-25 remediation, item 4).

Rows written before `safe_exception_text` (PR #85) can still carry the raw
SQLAlchemy rendering of a failed statement — the SQL, the bound parameters
(delivered values) and the driver message — in `rce_delivery_jobs.error_reason`
and `stage_detail`. The rows are evidence and are not rewritten; the job's
`to_dict` masks them on the way out. Synthetic strings only: no real record
value appears here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.core.logging_config import sanitize_stored_error, sanitize_stored_error_tree
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

LEGACY = (
    "CURATION: DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) "
    "<class 'asyncpg.exceptions.StringDataRightTruncationError'>: value too long for "
    "type character varying(500) "
    "[SQL: INSERT INTO rce_curated_records (id, source_intake_id, name) "
    "VALUES ($1::UUID, $2::UUID, $3::VARCHAR)] "
    "[parameters: ('11111111-1111-1111-1111-111111111111', "
    "'22222222-2222-2222-2222-222222222222', 'SYNTHETIC ORGANISATION NAME THAT IS "
    "VERY LONG AND WOULD BE A DELIVERED VALUE')] "
    "(Background on this error at: https://sqlalche.me/e/20/9h9h)"
)

FORBIDDEN = ("INSERT INTO", "[SQL:", "[parameters:", "SYNTHETIC ORGANISATION",
             "value too long", "Background on this error")


def test_legacy_driver_text_keeps_the_stage_and_class_names_and_nothing_else():
    out = sanitize_stored_error(LEGACY)
    for fragment in FORBIDDEN:
        assert fragment not in out, out
    assert out.startswith("CURATION: DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) "
                          "<class 'asyncpg.exceptions.StringDataRightTruncationError'>")
    assert "withheld" in out
    assert sanitize_stored_error(out) == out          # idempotent


def test_sql_fragment_without_a_driver_class_is_still_cut():
    out = sanitize_stored_error("PROMOTION: OperationalError: connection lost "
                                "[SQL: SELECT 1 FROM rce_curated_records] [parameters: ()]")
    assert out == "PROMOTION: OperationalError: connection lost"
    assert "[SQL:" not in out and "[parameters:" not in out


def test_controlled_application_strings_pass_through_unchanged():
    for value in ("worker_stopped_without_reporting",
                  "PARSING: ValueError: declared delimiter not found",
                  "QUALITY: RuntimeError (message withheld from evidence; see the server log "
                  "for correlation id abc)",
                  ""):
        assert sanitize_stored_error(value) == value
    assert sanitize_stored_error(None) is None
    assert sanitize_stored_error(42) == 42


def test_tree_masks_nested_error_strings_and_copies_the_structure():
    detail = {"CURATION": {"error": LEGACY, "completed": False, "count": 3},
              "PROMOTION": {"notes": ["fine", LEGACY]},
              "RECONCILIATION": {"passed": True}}
    out = sanitize_stored_error_tree(detail)
    assert out["CURATION"]["completed"] is False and out["CURATION"]["count"] == 3
    assert out["RECONCILIATION"] == {"passed": True}
    assert out["PROMOTION"]["notes"][0] == "fine"
    for fragment in FORBIDDEN:
        assert fragment not in out["CURATION"]["error"]
        assert fragment not in out["PROMOTION"]["notes"][1]
    assert detail["CURATION"]["error"] == LEGACY       # the source is not mutated
    assert out is not detail and out["CURATION"] is not detail["CURATION"]


def test_job_to_dict_masks_error_reason_and_stage_detail_without_touching_the_row():
    now = datetime.utcnow()
    job = RceDeliveryJob(
        id=uuid.uuid4(), identity="x" * 64, original_filename="synthetic.csv",
        storage_path="(synthetic)", sha256="0" * 64, file_size_bytes=1,
        state=RceDeliveryJob.STATE_FAILED, stage=RceDeliveryJob.STAGE_CURATION,
        registered_by="synthetic@test.local", created_at=now, failed_at=now,
        attempt_count=1, error_reason=LEGACY,
        stage_detail={"CURATION": {"error": LEGACY, "completed": False},
                      "PARSING": {"record_count": 2}})
    out = job.to_dict()
    for fragment in FORBIDDEN:
        assert fragment not in out["error_reason"]
        assert fragment not in out["stage_detail"]["CURATION"]["error"]
    assert "StringDataRightTruncationError" in out["error_reason"]
    assert out["error_reason"].startswith("CURATION: DBAPIError:")
    assert out["stage_detail"]["PARSING"] == {"record_count": 2}
    # The row itself is untouched: no database mutation, on read or otherwise.
    assert job.error_reason == LEGACY
    assert job.stage_detail["CURATION"]["error"] == LEGACY
