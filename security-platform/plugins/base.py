"""Scanner plugin contract.

THE LICENSING POLICY IS ENFORCED HERE, NOT IN EACH PLUGIN
    Zero commercial licensing cost is a hard requirement, and a missing tool must
    never fail the run. So `run()` is never called unless `is_available()` returns
    True, and any exception a plugin raises is caught by the manager and recorded as
    a tool error against that plugin alone. A plugin therefore cannot take down a
    scan, whether it is absent, unlicensed, or simply broken.

WRITING A PLUGIN
    Subclass ScannerPlugin, set name/category, implement is_available() and run().
    Drop the file in plugins/ — discovery is automatic, no registry to edit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import Category, Finding, Project, ScanTarget  # noqa: E402


class ScannerPlugin(ABC):
    """Base class for every scanner."""

    #: Stable identifier, used as the config key in a project's "plugins" block.
    name: str = "unnamed"
    #: Human-readable, used in reports.
    display_name: str = ""
    #: Which discipline this plugin contributes to.
    category: Category = Category.SAST
    #: Executable this plugin shells out to (used by the default is_available()).
    required_binary: str = ""
    #: Python module this plugin needs importable (alternative availability check).
    required_module: str = ""
    #: Free tooling only. A plugin that needs a paid licence must declare it so the
    #: report can distinguish "not installed" from "commercially gated".
    commercial: bool = False

    def __init__(self, project: Project, config: Optional[Dict[str, Any]] = None):
        self.project = project
        self.config = config or {}
        self._version: str = ""

    # ── availability ─────────────────────────────────────────────────────────

    def is_available(self) -> tuple[bool, str]:
        """Return (available, reason_if_not).

        Default implementation checks for the declared binary and/or module. Override
        for anything more specific. Must NEVER raise.
        """
        if self.required_binary:
            resolved = shutil.which(self.required_binary) or self._local_bin(self.required_binary)
            if not resolved:
                return False, f"'{self.required_binary}' not found on PATH"
        if self.required_module:
            try:
                __import__(self.required_module)
            except Exception:
                return False, f"python module '{self.required_module}' not importable"
        return True, ""

    def _local_bin(self, binary: str) -> str:
        """Look inside the platform's own tools/ dir before giving up.

        Every tool install is required to live inside security-platform/, so a tool
        vendored there counts as available even when it is not on the system PATH.
        """
        root = Path(__file__).resolve().parent.parent
        candidates = [
            root / "tools" / binary, root / "tools" / f"{binary}.exe",
            root / "tools" / "bin" / binary,
            # The platform's own venv — this is where `pip install semgrep bandit`
            # puts console scripts, and it keeps every tool install inside
            # security-platform/ as required.
            root / ".venv" / "Scripts" / f"{binary}.exe",
            root / ".venv" / "Scripts" / binary,
            root / ".venv" / "bin" / binary,
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        # Also honour a console-script installed alongside the running interpreter.
        scripts = Path(sys.executable).parent
        for c in (scripts / binary, scripts / f"{binary}.exe",
                  scripts / "Scripts" / f"{binary}.exe"):
            if c.exists():
                return str(c)
        return ""

    def binary_path(self) -> str:
        return shutil.which(self.required_binary) or self._local_bin(self.required_binary)

    def version(self) -> str:
        return self._version

    # ── execution ────────────────────────────────────────────────────────────

    @abstractmethod
    def run(self, targets: List[ScanTarget]) -> List[Finding]:
        """Scan the given targets and return findings. Called only when available."""
        raise NotImplementedError

    # ── helpers for subclasses ───────────────────────────────────────────────

    def exec(self, cmd: List[str], cwd: Optional[Path] = None,
             timeout: int = 900, check_rc: bool = False) -> subprocess.CompletedProcess:
        """Run a scanner process.

        Scanners conventionally exit non-zero when they FIND something, so a non-zero
        return code is not an error by default — callers opt in via check_rc.
        """
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        if check_rc and proc.returncode != 0:
            raise RuntimeError(
                f"{cmd[0]} exited {proc.returncode}: {(proc.stderr or '')[:400]}")
        return proc

    def exec_bounded(self, cmd: List[str], timeout: int = 300,
                     cwd: Optional[Path] = None) -> tuple[int, str, str, bool]:
        """Run a process with a HARD deadline. Returns (rc, stdout, stderr, timed_out).

        Two deliberate differences from subprocess.run(timeout=...), both learned the
        hard way from semgrep on Windows:

        1. Output goes to temporary FILES, not pipes. subprocess.run kills the direct
           child on timeout but then keeps reading the pipe until EOF - and EOF never
           arrives while orphaned grandchildren still hold the write handle. The call
           blocks forever despite having a timeout. Files have no such handshake.

        2. On timeout the whole process TREE is killed (taskkill /T on Windows,
           killpg elsewhere). Killing only the direct child leaves workers running
           and consuming CPU for the rest of the session.

        Never raises on process failure; timed_out is reported, not thrown.
        """
        out_f = tempfile.NamedTemporaryFile(delete=False, suffix=".out")
        err_f = tempfile.NamedTemporaryFile(delete=False, suffix=".err")
        out_f.close()
        err_f.close()
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")

        kwargs: Dict[str, Any] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True          # own process group to kill
        elif os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        timed_out = False
        rc = -1
        try:
            with open(out_f.name, "wb") as so, open(err_f.name, "wb") as se:
                proc = subprocess.Popen(cmd, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                                        cwd=str(cwd) if cwd else None, env=env, **kwargs)
                try:
                    rc = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill_tree(proc)
                    rc = -1
            stdout = Path(out_f.name).read_text(encoding="utf-8", errors="replace")
            stderr = Path(err_f.name).read_text(encoding="utf-8", errors="replace")
        finally:
            for f in (out_f.name, err_f.name):
                try:
                    os.unlink(f)
                except OSError:
                    pass
        return rc, stdout, stderr, timed_out

    @staticmethod
    def _kill_tree(proc: "subprocess.Popen") -> None:
        """Terminate a process and every descendant. Best effort, never raises."""
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=30)
            else:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass

    def relative_path(self, absolute: str) -> str:
        """Normalise a scanner's absolute path to a repo-relative POSIX path.

        Findings must be comparable across machines and across scans; an absolute
        Windows path in a fingerprint would make every finding 'new' on CI.
        """
        if not absolute:
            return ""
        p = str(absolute).replace("\\", "/")
        base = Path(self.project.config_path).resolve().parent.parent.parent \
            if self.project.config_path else None
        for target in self.project.targets:
            root = str(Path(os.path.expandvars(target.path)).resolve()).replace("\\", "/")
            if p.lower().startswith(root.lower()):
                rel = p[len(root):].lstrip("/")
                return f"{target.name}/{rel}" if rel else target.name
        if base:
            b = str(base).replace("\\", "/")
            if p.lower().startswith(b.lower()):
                return p[len(b):].lstrip("/")
        return p

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} category={self.category.value}>"
