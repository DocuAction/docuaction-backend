"""The CI skip guard rejects un-permitted skips whatever their wording, and accepts the
explicitly permitted ones. Pure (no database)."""
from __future__ import annotations

import importlib.util
import pathlib
import xml.etree.ElementTree as ET

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ci_skip_guard", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ci_skip_guard.py")
guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard)


def _xml(*cases):
    body = "".join(
        f'<testcase classname="{c}" name="{n}">' + (f'<skipped message="{m}"/>' if m is not None else "") + "</testcase>"
        for c, n, m in cases)
    return ET.fromstring(f"<testsuites><testsuite>{body}</testsuite></testsuites>")


def test_the_original_capitalised_message_is_rejected():
    skips = guard.find_skips(_xml(("tests.test_x", "test_a", "No database reachable at DATABASE_URL.")))
    bad, _ = guard.evaluate(skips)
    assert [t for t, _ in bad] == ["tests/test_x.py::test_a"]


@pytest.mark.parametrize("msg", ["no database reachable for the migration test: boom",
                                 "no sandbox database for a concurrency test", "anything else at all"])
def test_lowercase_and_reworded_skips_are_rejected_too(msg):
    bad, _ = guard.evaluate(guard.find_skips(_xml(("tests.test_x", "test_a", msg))))
    assert len(bad) == 1


def test_a_new_skip_in_a_class_is_identified_with_its_class():
    bad, _ = guard.evaluate(guard.find_skips(_xml(("tests.test_x.TestThing", "test_a", "m"))))
    assert bad[0][0] == "tests/test_x.py::TestThing::test_a"


def test_exactly_the_three_recorded_skips_are_permitted_regardless_of_message():
    cases = [("tests.test_qhin_sampling_operational", "test_two_concurrent_finalizations_produce_one_official_sample",
              "no sandbox database for a concurrency test: x"),
             ("tests.test_iqvia_affil_org_indexes_2026_10_08", "test_org_indexes_exist_are_partial_owned_and_used",
              "no database reachable for the migration test: No module named 'psycopg2'"),
             ("tests.test_iqvia_affil_org_indexes_2026_10_08",
              "test_interrupted_build_leaves_an_invalid_index_that_is_detected_and_repaired", "reworded")]
    bad, stale = guard.evaluate(guard.find_skips(_xml(*cases)))
    assert bad == [] and stale == []


def test_a_fourth_skip_alongside_the_permitted_ones_fails():
    cases = [("tests.test_record_comments_db_2026_11_12", "test_new", "No database reachable")]
    bad, _ = guard.evaluate(guard.find_skips(_xml(*cases)))
    assert len(bad) == 1


def test_passing_tests_and_a_cleared_permitted_skip_pass_and_flag_the_stale_entry():
    bad, stale = guard.evaluate(guard.find_skips(_xml(("tests.test_x", "test_ok", None))))
    assert bad == [] and len(stale) == len(guard.PERMITTED_SKIPS)


def test_the_script_exit_codes(tmp_path, capsys):
    f = tmp_path / "r.xml"
    f.write_text('<testsuites><testsuite><testcase classname="tests.test_x" name="t"><skipped message="No database reachable"/></testcase></testsuite></testsuites>')
    assert guard.main(["g", str(f)]) == 1
    f.write_text('<testsuites><testsuite><testcase classname="tests.test_x" name="t"/></testsuite></testsuites>')
    assert guard.main(["g", str(f)]) == 0
    assert guard.main(["g"]) == 2
