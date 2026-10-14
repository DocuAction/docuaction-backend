"""The job dict carries the delivery's calendar date as a date-only `received_on`,
next to the (unchanged) `received_date` instant, so a client need not reinterpret
'2026-09-02T00:00:00+00:00' (an instant) as a date and show September 1."""
from datetime import datetime

from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob


def test_received_on_is_the_calendar_date_and_received_date_is_unchanged():
    job = RceDeliveryJob(received_date=datetime(2026, 9, 2))
    out = job.to_dict()
    assert out["received_on"] == "2026-09-02"
    assert out["received_date"] == "2026-09-02T00:00:00+00:00"


def test_received_on_is_none_when_no_date_was_given():
    assert RceDeliveryJob(received_date=None).to_dict()["received_on"] is None
