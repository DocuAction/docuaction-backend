"""Follow-up remediation for two defects reproduced during the 2026-09-20
authenticated DEV smoke test (both are narrow, same-root-cause residuals of
already-fixed defect IDs — no new defect IDs, per the follow-up scope):

QA-010 (residual): the Delivery Overview's Identity block ("Completed")
showed a stored timestamp four hours later than the identical event's
timestamp in Audit History / Processing Timeline. The original QA-010 fix
covered `rce_issues.created_at` (exception ledger) and the audit route; it
did not cover `RceDeliveryJob`'s own naive columns, which feed the Overview
block. Fixed here via one shared serializer, `app.core.time_utils.
utc_isoformat`, used at that call site.

QA-040 (residual): Supervisor Operations' "Analyst workload" table printed
the raw `assigned_to_user_id` UUID. The backend already resolves and returns
a `principal` field (email/display name/role) for each bucket; only that one
frontend table's column was never wired to read it (the case-drawer Holder
field and the queue's Holder column already did). No backend change was
needed for QA-040; this file adds the backend serializer test only.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest


# ── QA-010: the shared serializer itself ──────────────────────────────────────

def test_utc_isoformat_labels_a_naive_datetime_as_utc_without_shifting_it():
    from app.core.time_utils import utc_isoformat

    naive = datetime(2026, 9, 20, 2, 27, 35)  # UTC-by-construction, no tzinfo
    out = utc_isoformat(naive)
    assert out == "2026-09-20T02:27:35+00:00"
    # The digits are untouched -- this is a label, never a shift.
    assert out.startswith("2026-09-20T02:27:35")


def test_utc_isoformat_converts_an_aware_datetime_to_utc():
    from app.core.time_utils import utc_isoformat

    # An aware value in a different offset must be normalised to UTC, not
    # merely re-labelled -- e.g. a hypothetical EDT-aware value.
    from datetime import timedelta

    edt = timezone(timedelta(hours=-4))
    aware = datetime(2026, 9, 19, 22, 27, 35, tzinfo=edt)
    out = utc_isoformat(aware)
    assert out == "2026-09-20T02:27:35+00:00"


def test_utc_isoformat_is_idempotent_on_an_already_utc_aware_value():
    from app.core.time_utils import utc_isoformat

    aware_utc = datetime(2026, 9, 20, 2, 27, 35, tzinfo=timezone.utc)
    assert utc_isoformat(aware_utc) == "2026-09-20T02:27:35+00:00"


def test_utc_isoformat_handles_none_and_plain_date():
    from app.core.time_utils import utc_isoformat

    assert utc_isoformat(None) is None
    assert utc_isoformat(date(2026, 9, 19)) == "2026-09-19"


def test_no_hardcoded_four_hour_adjustment_is_applied():
    """The fix must not encode a fixed offset (e.g. always -4h/+4h); it must
    only ever ADD the correct label to whatever instant was actually stored."""
    from app.core.time_utils import utc_isoformat

    naive = datetime(2026, 9, 20, 2, 27, 35)
    out = utc_isoformat(naive)
    hour = int(out[11:13])
    assert hour == 2, "the serializer changed the wall-clock hour; it must only label it"


# ── QA-010: the actual regression -- RceDeliveryJob.to_dict() ────────────────

def test_delivery_job_to_dict_reports_the_same_instant_as_a_naive_stage_event():
    """The exact reproduction: a naive `completed_at` (written via
    `datetime.utcnow()`, as `delivery_jobs.finish_succeeded` does) must
    serialize to the SAME instant a `DateTime(timezone=True)` stage-event
    column reports for the identical wall-clock moment -- not four hours
    apart, which is what QA-010 observed live on DEV (Overview
    `2026-09-20 02:27:35 UTC` vs. Audit History `2026-09-19 22:27:35 UTC`,
    the same event).
    """
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    same_instant = datetime(2026, 9, 19, 22, 27, 35)  # naive, UTC-by-construction
    job = RceDeliveryJob(
        id=uuid.uuid4(), identity=f"test-{uuid.uuid4().hex[:8]}",
        original_filename="synthetic.csv", sha256="0" * 64, file_size_bytes=1,
        registered_by="qa010-followup@synthetic.test",
        state="SUCCEEDED", stage="READY",
        created_at=same_instant, started_at=same_instant,
        completed_at=same_instant, failed_at=None,
    )
    out = job.to_dict()
    # An aware column reporting the identical wall-clock moment.
    aware_equivalent = same_instant.replace(tzinfo=timezone.utc).isoformat()
    assert out["completed_at"] == aware_equivalent
    assert out["started_at"] == aware_equivalent
    assert out["created_at"] == aware_equivalent
    # Every field the Overview/Identity block reads carries an explicit offset.
    for key in ("created_at", "started_at", "completed_at"):
        assert out[key].endswith("+00:00"), f"{key} has no explicit UTC offset"


def test_delivery_job_to_dict_handles_a_missing_failed_at_and_a_date_only_received_date():
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    job = RceDeliveryJob(
        id=uuid.uuid4(), identity=f"test-{uuid.uuid4().hex[:8]}",
        original_filename="synthetic.csv", sha256="1" * 64, file_size_bytes=1,
        registered_by="qa010-followup@synthetic.test",
        state="QUEUED", stage="REGISTERED",
        received_date=date(2026, 9, 19),
    )
    out = job.to_dict()
    assert out["failed_at"] is None
    assert out["started_at"] is None
    assert out["received_date"] == "2026-09-19"
