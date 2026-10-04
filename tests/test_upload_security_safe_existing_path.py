"""Targeted tests for app.core.upload_security.safe_existing_path and
open_no_follow -- written 2026-10-04 after independent review reproduced a
containment gap: an allowed-directory entry that is a SYMLINK to a file
OUTSIDE the directory passed the prior version's `Path.is_file()` check
(which follows symlinks) and was returned, read-able outside every
allowed directory.

Uses real, synthetic files only -- no fixture, delivery, or application
data. Symlink-dependent cases use real `os.symlink` (the genuine attack
primitive) and skip (not fail, not fake-pass) on a platform where creating
one requires a privilege this session does not have (e.g. local Windows
without Developer Mode) -- CI runs Linux, where they run for real.
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from app.core.upload_security import open_no_follow, safe_existing_path


def _symlink_or_skip(target, link_path):
    try:
        os.symlink(target, link_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this platform/account cannot create symlinks without "
                    f"elevated privilege ({exc}); the real check runs on "
                    f"Linux in CI")


class TestNormalFiles:
    def test_a_regular_file_in_the_allowed_directory_is_returned(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        (allowed / "report.csv").write_text("a,b,c\n1,2,3\n")

        result = safe_existing_path("report.csv", str(allowed))

        assert result == (allowed / "report.csv").resolve()
        assert result.is_file()

    def test_the_second_of_two_allowed_directories_is_still_checked(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        (second / "only-here.csv").write_text("x")

        result = safe_existing_path("only-here.csv", str(first), str(second))

        assert result == (second / "only-here.csv").resolve()

    def test_open_no_follow_reads_a_regular_file_normally(self, tmp_path):
        p = tmp_path / "plain.txt"
        p.write_bytes(b"hello world")

        with open_no_follow(p, "rb") as fh:
            assert fh.read() == b"hello world"


class TestOutsideTargetSymlinks:
    def test_a_symlinked_entry_pointing_outside_the_allowed_directory_is_refused(self, tmp_path):
        outside = tmp_path / "outside"
        allowed = tmp_path / "allowed"
        outside.mkdir()
        allowed.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("TOP SECRET, OUTSIDE EVERY ALLOWED DIRECTORY")
        link = allowed / "evil.csv"
        _symlink_or_skip(str(secret), str(link))

        with pytest.raises(HTTPException) as exc_info:
            safe_existing_path("evil.csv", str(allowed))

        assert exc_info.value.status_code == 400
        assert "symlink" in str(exc_info.value.detail).lower()

    def test_a_symlink_to_an_outside_file_is_never_opened_even_if_the_check_were_skipped(self, tmp_path):
        """Directly exercises open_no_follow (the TOCTOU-closing layer),
        independent of safe_existing_path, in case some future call site
        reaches a path without going through that check first."""
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("TOP SECRET")
        link = tmp_path / "link-to-secret"
        _symlink_or_skip(str(secret), str(link))

        with pytest.raises(OSError):
            open_no_follow(str(link), "rb")

    def test_a_symlinked_directory_entry_does_not_leak_its_target_s_contents(self, tmp_path):
        """The outside file itself is a directory-escape target one level up
        -- same primitive, confirms it is not special-cased away."""
        outside = tmp_path / "outside"
        allowed = tmp_path / "allowed"
        outside.mkdir()
        allowed.mkdir()
        (outside / "inner.csv").write_text("inner, outside content")
        link = allowed / "looks-local.csv"
        _symlink_or_skip(str(outside / "inner.csv"), str(link))

        with pytest.raises(HTTPException):
            safe_existing_path("looks-local.csv", str(allowed))

    def test_a_file_swapped_for_a_symlink_after_validation_is_refused_at_open_time(self, tmp_path):
        """The actual TOCTOU race, not just a pre-existing symlink: a
        legitimate regular file passes safe_existing_path's check, and only
        THEN -- simulating the window between that check and a caller's
        read -- is the same path replaced with a symlink to a file outside
        every allowed directory. open_no_follow on the now-swapped path must
        refuse, not silently read through to the outside content; this is
        the specific guarantee the module's docstrings claim for it."""
        outside = tmp_path / "outside"
        allowed = tmp_path / "allowed"
        outside.mkdir()
        allowed.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("TOP SECRET, OUTSIDE EVERY ALLOWED DIRECTORY")
        real = allowed / "report.csv"
        real.write_text("a,b,c\n1,2,3\n")

        validated = safe_existing_path("report.csv", str(allowed))
        assert validated.read_text() == "a,b,c\n1,2,3\n"  # genuinely a regular file at this point

        # The race: something with write access to `allowed` (see
        # upload_security.py's deployment-design note on who that can be)
        # replaces the validated path with a symlink before it is read.
        validated.unlink()
        _symlink_or_skip(str(secret), str(validated))

        with pytest.raises(OSError):
            open_no_follow(validated, "rb")


class TestInvalidPaths:
    def test_empty_candidate_is_refused(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        with pytest.raises(HTTPException) as exc_info:
            safe_existing_path("", str(allowed))
        assert exc_info.value.status_code == 400

    def test_dot_and_dotdot_are_refused(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        for candidate in (".", ".."):
            with pytest.raises(HTTPException) as exc_info:
                safe_existing_path(candidate, str(allowed))
            assert exc_info.value.status_code == 400

    def test_a_traversal_attempt_never_escapes_to_a_real_file_outside(self, tmp_path):
        outside = tmp_path / "outside"
        allowed = tmp_path / "allowed"
        outside.mkdir()
        allowed.mkdir()
        (outside / "passwd").write_text("root:x:0:0")

        # Path(...).name strips every directory component, so this resolves
        # to requesting a file literally named "passwd" INSIDE `allowed` --
        # which does not exist there, so this must 422, never return the
        # outside file.
        with pytest.raises(HTTPException) as exc_info:
            safe_existing_path("../outside/passwd", str(allowed))
        assert exc_info.value.status_code == 422

    def test_a_nonexistent_filename_is_refused_with_422_not_400(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        with pytest.raises(HTTPException) as exc_info:
            safe_existing_path("never-created.csv", str(allowed))
        assert exc_info.value.status_code == 422

    def test_a_requested_name_that_is_itself_a_directory_is_not_returned(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        (allowed / "a-directory.csv").mkdir()
        with pytest.raises(HTTPException) as exc_info:
            safe_existing_path("a-directory.csv", str(allowed))
        assert exc_info.value.status_code == 422
