"""Locked platform dependency: TEFCA → CORE allowed; CORE → TEFCA prohibited.

    DOCUACTION PLATFORM → CORE → PROGRAM CONFIGURATION → TEFCA / ARC MODULE

Core may expose interfaces that TEFCA implements; Core does not know TEFCA
exists. Enforced statically (AST) over every module under app/core, including
the bypasses a grep would miss: dynamic imports via importlib/__import__ with
module strings, runtime module-string constants, and service-locator style
lookups that name a TEFCA module.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression]

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "app" / "core"

#: Module prefixes Core must never depend on.
FORBIDDEN_PREFIXES = ("app.Tefca", "app.tefca_registry", "Tefca", "tefca_registry")
FORBIDDEN_MODULE_RE = re.compile(r"^(app\.)?(Tefca|tefca_registry)(\.|$)")


def _core_modules():
    for path in sorted(CORE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _forbidden(name: str) -> bool:
    return bool(name) and (FORBIDDEN_MODULE_RE.match(name) is not None)


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.violations = []

    def _add(self, node, kind, what):
        self.violations.append(f"{self.path.relative_to(ROOT)}:{node.lineno}: {kind} {what!r}")

    def visit_Import(self, node):
        for alias in node.names:
            if _forbidden(alias.name):
                self._add(node, "import", alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module and _forbidden(node.module):
            self._add(node, "from-import", node.module)
        self.generic_visit(node)

    def visit_Call(self, node):
        # importlib.import_module("app.Tefca…"), __import__("app.Tefca…"), and
        # service-locator style get_module("…tefca…") with a literal string.
        func = node.func
        fname = ""
        if isinstance(func, ast.Attribute):
            fname = func.attr
        elif isinstance(func, ast.Name):
            fname = func.id
        if fname in ("import_module", "__import__", "load_module", "find_spec", "spec_from_file_location"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _forbidden(arg.value):
                    self._add(node, f"dynamic import via {fname}", arg.value)
            for kw in node.keywords:
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str) and _forbidden(kw.value.value):
                    self._add(node, f"dynamic import via {fname}", kw.value.value)
        self.generic_visit(node)

    def visit_Constant(self, node):
        # Runtime module strings ("app.Tefca.x") anywhere in code — but not in
        # docstrings, which are Expr(Constant) statements handled by the caller.
        if isinstance(node.value, str) and FORBIDDEN_MODULE_RE.match(node.value) and node.value.count(".") >= 1:
            self._add(node, "module string constant", node.value)


def _strip_docstrings(tree: ast.AST) -> None:
    """Remove docstring nodes so prose that mentions TEFCA paths is not a violation."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]


def test_core_has_modules_to_scan():
    assert len(list(_core_modules())) > 20, "the walk found too few Core modules; the guard would pass vacuously"


def test_core_never_imports_tefca_statically_or_dynamically():
    violations = []
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _strip_docstrings(tree)
        s = _Scanner(path)
        s.visit(tree)
        violations.extend(s.violations)
    assert not violations, "CORE → TEFCA dependency detected:\n" + "\n".join(violations)


def test_core_does_not_reference_tefca_environment_or_registry_names():
    """A softer service-locator check: no Core module builds a TEFCA module
    path from string parts (e.g. 'app.' + 'Tefca')."""
    hits = []
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _strip_docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for v in node.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str) and re.search(r"\bTefca\b|tefca_registry", v.value):
                        hits.append(f"{path.relative_to(ROOT)}:{node.lineno}: f-string mentions a TEFCA module")
    assert not hits, "\n".join(hits)


def test_tefca_may_import_core():
    """The permitted direction exists and is exercised (sanity: the rule is not
    trivially true because nobody imports anything)."""
    tefca = ROOT / "app" / "Tefca"
    found = False
    for path in tefca.rglob("*.py"):
        if "from app.core" in path.read_text(encoding="utf-8", errors="ignore"):
            found = True
            break
    assert found


def test_entity_intelligence_core_stays_program_agnostic():
    """Program vocabularies live in adapters and observation roles, never in
    Core enums (kept alongside the platform-wide rule)."""
    import enum
    import importlib
    import inspect
    banned = ("QHIN", "TEFCA", "MEDICARE", "PECOS", "NPPES")
    for mod_name in ("app.core.entity_intelligence.observations", "app.core.entity_intelligence.comparison",
                     "app.core.entity_intelligence.delta", "app.core.entity_intelligence.profile"):
        mod = importlib.import_module(mod_name)
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, enum.Enum) and cls.__module__ == mod.__name__:
                for m in cls:
                    assert not any(b in m.name or b in str(m.value) for b in banned), (cls.__name__, m)
