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


def _runner_uses_the_full_id(src: str) -> bool:
    """The RUNNER body (not the helper definition): report_id is assigned from the helper and that same report_id is what
    load_dataset and the artifact receive; no eight-character slice anywhere in the runner."""
    return ("report_id = record_review_report_id(job_id)" in src and "report_id=report_id" in src and "[:8]" not in src)


def test_the_runner_builds_its_report_id_through_that_function():
    import inspect
    assert _runner_uses_the_full_id(inspect.getsource(export_runner.run_record_review_export_job))


def test_the_runner_check_would_catch_a_regression_to_an_eight_character_id():
    """Mutation guard: the previous assertion inspected the 400 characters before the HELPER DEFINITION and still passed
    when the runner sliced the UUID; this check must fail for such a runner."""
    import inspect
    good = inspect.getsource(export_runner.run_record_review_export_job)
    assert not _runner_uses_the_full_id(good.replace("record_review_report_id(job_id)", "str(job_id)[:8]"))
    assert not _runner_uses_the_full_id(good.replace("report_id=report_id", "report_id='X'"))
