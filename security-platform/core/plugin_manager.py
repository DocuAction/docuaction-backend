"""Plugin discovery and safe execution.

Auto-discovers every ScannerPlugin subclass under plugins/ and runs the ones a
project enables. The manager owns the failure contract: a plugin that is missing,
unlicensed, broken, or slow is isolated to its own ToolStatus row and never
propagates an exception into the scan.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from core.models import Category, Finding, Project, ScanTarget, ToolStatus

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))


class PluginManager:
    """Discovers, filters, and executes scanner plugins."""

    def __init__(self, project: Project, verbose: bool = False):
        self.project = project
        self.verbose = verbose
        self._classes: Dict[str, Type] = {}
        self._load_errors: List[str] = []

    # ── discovery ────────────────────────────────────────────────────────────

    def discover(self) -> Dict[str, Type]:
        """Import every module under plugins/ and collect ScannerPlugin subclasses.

        A plugin module that fails to import is recorded and skipped - one bad file
        must not prevent the other scanners from running.
        """
        from plugins.base import ScannerPlugin

        self._classes.clear()
        self._load_errors.clear()

        pkg_dir = PLATFORM_ROOT / "plugins"
        if not pkg_dir.exists():
            return self._classes

        for mod in pkgutil.iter_modules([str(pkg_dir)]):
            if mod.name in ("base", "__init__") or mod.ispkg:
                continue
            try:
                module = importlib.import_module(f"plugins.{mod.name}")
            except Exception as exc:
                self._load_errors.append(f"plugins.{mod.name}: {exc}")
                continue
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (issubclass(obj, ScannerPlugin) and obj is not ScannerPlugin
                        and not inspect.isabstract(obj)):
                    name = getattr(obj, "name", "")
                    if name and name != "unnamed":
                        self._classes[name] = obj
        return self._classes

    @property
    def load_errors(self) -> List[str]:
        return list(self._load_errors)

    def available_plugin_names(self) -> List[str]:
        return sorted(self._classes)

    # ── selection ────────────────────────────────────────────────────────────

    def select(self, categories: Optional[List[Category]] = None) -> List[Any]:
        """Instantiate the plugins this project enables, optionally filtered by
        category (that is what `scan --sast` / `--deps` / `--secrets` do)."""
        if not self._classes:
            self.discover()
        chosen = []
        for name, cls in sorted(self._classes.items()):
            if not self.project.plugin_enabled(name):
                continue
            if categories and cls.category not in categories:
                continue
            try:
                chosen.append(cls(self.project, self.project.plugin_config(name)))
            except Exception as exc:
                self._load_errors.append(f"{name}: instantiation failed: {exc}")
        return chosen

    # ── execution ────────────────────────────────────────────────────────────

    def run_all(self, targets: List[ScanTarget],
                categories: Optional[List[Category]] = None,
                ) -> tuple[List[Finding], List[ToolStatus]]:
        """Run every selected plugin. Returns (findings, per-tool status).

        Never raises. Every outcome - ran, skipped, errored, timed out - becomes a
        ToolStatus so the report can state exactly which capability was lost.
        """
        plugins = self.select(categories)
        findings: List[Finding] = []
        statuses: List[ToolStatus] = []

        # Surface plugins that could not even be imported.
        for err in self._load_errors:
            tool_name = err.split(":", 1)[0]
            statuses.append(ToolStatus(name=tool_name, available=False,
                                       skipped_reason=f"plugin failed to load: {err}"))

        for plugin in plugins:
            status = ToolStatus(name=plugin.name)
            started = time.time()
            try:
                ok, reason = plugin.is_available()
            except Exception as exc:                        # defensive: must not raise
                ok, reason = False, f"availability check raised: {exc}"

            status.available = ok
            if not ok:
                status.skipped_reason = reason + (
                    " (commercial tool - skipped by policy)" if plugin.commercial else "")
                statuses.append(status)
                self._log(f"SKIP  {plugin.name}: {reason}")
                continue

            try:
                status.version = plugin.version() or ""
                result = plugin.run(targets) or []
                status.ran = True
                status.findings_count = len(result)
                findings.extend(result)
                self._log(f"OK    {plugin.name}: {len(result)} findings")
            except subprocess.TimeoutExpired:
                status.error = "timed out"
                self._log(f"ERROR {plugin.name}: timed out")
            except Exception as exc:
                status.error = f"{type(exc).__name__}: {exc}"
                self._log(f"ERROR {plugin.name}: {status.error}")
                if self.verbose:
                    traceback.print_exc()
            finally:
                status.duration_seconds = round(time.time() - started, 2)
                if not status.version:
                    try:
                        status.version = plugin.version() or ""
                    except Exception:
                        pass
                statuses.append(status)

        return findings, statuses

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [plugin] {msg}")
