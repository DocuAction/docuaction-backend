"""Unit-level test for scripts/verapdf_validate.py's zip-introspection logic
(2026-10-03) -- isolated from any network call, so this runs in any CI
environment regardless of internet access. The actual end-to-end download +
CLI invocation was verified manually against a real downloaded installer and
a real PDF (see FORK-FINDINGS.md Part 8); that network-dependent path is not
re-exercised here by design.
"""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from verapdf_validate import _find_installer_jar_name  # noqa: E402


def _make_zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, b"")
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_finds_the_izpack_installer_jar_among_other_entries():
    z = _make_zip([
        "verapdf-greenfield-1.30.2/",
        "verapdf-greenfield-1.30.2/verapdf-install",
        "verapdf-greenfield-1.30.2/verapdf-install.bat",
        "verapdf-greenfield-1.30.2/verapdf-izpack-installer-1.30.2.jar",
    ])
    assert _find_installer_jar_name(z) == (
        "verapdf-greenfield-1.30.2/verapdf-izpack-installer-1.30.2.jar")


def test_raises_a_clear_error_when_no_installer_jar_is_present():
    z = _make_zip(["readme.txt", "some-other-file.jar"])
    try:
        _find_installer_jar_name(z)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "izpack-installer" in str(exc)
