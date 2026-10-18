"""Real veraPDF PDF/UA-1 validation, without the IzPack GUI/console installer
wizard (2026-10-03).

The IzPack installer's interactive wizard has no non-interactive path usable
in a headless/non-TTY shell (confirmed: its unattended `auto-install.xml` mode
still requires ONE successful interactive run to generate that file first --
there is no documented way to create it without a live session). The fix:
IzPack installer jars embed each install "pack" as a plain, independently
readable zip entry under `resources/packs/pack-<Name>` inside the installer
jar itself -- including a dedicated `pack-veraPDF CLI` entry that is the full
CLI application (jars' class files, flattened into one archive) with nothing
installer-specific about it. Extracting that one entry and putting it on a
classpath runs the real CLI directly, with zero installer interaction.

Usage:
    python scripts/verapdf_validate.py <pdf-path> [--flavour ua1]

Requires a JRE on PATH (or JAVA_HOME set) and network access on first run
(to fetch veraPDF's official installer distribution, from which only the
embedded CLI pack is used -- the GUI/doc/plugin packs are never touched).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import urllib.request
import zipfile

VERAPDF_INSTALLER_URL = "https://software.verapdf.org/rel/verapdf-installer.zip"
CACHE_DIR = pathlib.Path(__file__).parent / ".verapdf_cache"
INSTALLER_ZIP = CACHE_DIR / "verapdf-installer.zip"
CLI_PACK_ZIP = CACHE_DIR / "verapdf-cli-pack.zip"
MAIN_CLASS = "org.verapdf.apps.GreenfieldCliWrapper"


def _find_installer_jar_name(outer: zipfile.ZipFile) -> str:
    for name in outer.namelist():
        if name.endswith(".jar") and "izpack-installer" in name:
            return name
    raise RuntimeError(f"no izpack-installer jar found inside {outer.filename}")


def ensure_cli_pack() -> pathlib.Path:
    """Downloads veraPDF's official installer (if not already cached) and
    extracts ONLY its embedded CLI pack -- never runs the installer."""
    CACHE_DIR.mkdir(exist_ok=True)
    if CLI_PACK_ZIP.exists():
        return CLI_PACK_ZIP

    if not INSTALLER_ZIP.exists():
        urllib.request.urlretrieve(VERAPDF_INSTALLER_URL, INSTALLER_ZIP)

    with zipfile.ZipFile(INSTALLER_ZIP) as outer:
        inner_jar_name = _find_installer_jar_name(outer)
        inner_jar_bytes = outer.read(inner_jar_name)

    inner_path = CACHE_DIR / "installer.jar"
    inner_path.write_bytes(inner_jar_bytes)

    with zipfile.ZipFile(inner_path) as inner:
        pack_bytes = inner.read("resources/packs/pack-veraPDF CLI")
    CLI_PACK_ZIP.write_bytes(pack_bytes)
    inner_path.unlink()
    return CLI_PACK_ZIP


def java_executable() -> str:
    """`$JAVA_HOME/bin/java` when JAVA_HOME is set, else the user-scoped JRE
    this project installs under `~/.jre/` (not on PATH -- the normal case on
    the project's Windows hosts), else the bare `java` on PATH: exactly what
    the module docstring promises. (2026-10-03: the first cut called bare
    `java` and failed with WinError 2 in a fresh shell; patch adopted from
    the stopped Lane R, extended with the ~/.jre fallback.)"""
    exe = "java.exe" if os.name == "nt" else "java"
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = pathlib.Path(home) / "bin" / exe
        if candidate.exists():
            return str(candidate)
    for candidate in sorted(pathlib.Path.home().glob(f".jre/*/bin/{exe}")):
        return str(candidate)
    return "java"


def validate(pdf_path: str, flavour: str = "ua1", fmt: str = "xml") -> subprocess.CompletedProcess:
    cli_pack = ensure_cli_pack()
    return subprocess.run(
        [java_executable(), "-cp", str(cli_pack), MAIN_CLASS,
         "-f", flavour, "--format", fmt, pdf_path],
        capture_output=True, text=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--flavour", default="ua1")
    parser.add_argument("--format", default="xml", dest="fmt")
    args = parser.parse_args()

    result = validate(args.pdf_path, args.flavour, args.fmt)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    sys.exit(result.returncode)
