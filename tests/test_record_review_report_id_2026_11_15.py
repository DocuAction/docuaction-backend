"""Two record-review jobs whose UUIDs share their first eight characters must not share a report_id
(CodeRabbit Major on #225): the shared id made the second rendering the next artifact_version of the first."""
import uuid

import pytest

from app.reports import export_runner

pytestmark = pytest.mark.regression


def test_report_ids_differ_for_jobs_that_share_an_eight_character_prefix():
    a = uuid.UUID("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    b = uuid.UUID("12345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    ra, rb = export_runner.record_review_report_id(a), export_runner.record_review_report_id(b)
    assert ra != rb
    assert ra == "RECORD-REVIEW-" + str(a).upper() and len(ra) <= 64
    assert export_runner.record_review_report_id(str(a)) == ra


def test_the_runner_builds_its_report_id_through_that_function():
    import inspect
    src = inspect.getsource(export_runner)
    assert "record_review_report_id(job_id)" in src and "[:8]" not in src.split("record_review_report_id(job_id)")[0][-400:]
