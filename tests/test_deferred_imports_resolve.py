"""
Every deferred `from app... import X` must actually resolve.

WHY THIS EXISTS
`app/tefca_registry/rce/routes.py` contained:

    from app.core.security import get_client_ip

`get_client_ip` lives in `app.core.client_ip`. Because the import sat inside a
function body it raised nothing at import time, nothing at application startup,
and nothing in any test that merely imported the router. It failed only when a
delivery was actually uploaded — surfacing to the caller as a bare HTTP 500 with
a generic message, since the handler correctly refuses to leak internals.

It was found by ingesting the real ONC delivery into DEV. Nothing earlier in the
pipeline could have caught it, and it would have failed identically in
production, on the first Government delivery.

Deferred imports are used deliberately throughout this codebase to keep module
import cheap and to avoid cycles. That is a reasonable pattern; the cost is that
a typo in one is invisible until the line executes. This test pays that cost
once, statically, for every module that uses the pattern.

WHAT IT DOES
Walks the AST of the route modules, collects every `from app.* import name`
that appears inside a function body, and asserts the module imports and actually
carries that attribute. It resolves names via the AST rather than a regex, so
`import x as y` and multi-name imports are handled correctly rather than
producing false failures.
"""

from __future__ import annotations

import ast
import importlib
import os
import pathlib

import pytest

pytestmark = pytest.mark.regression

os.environ.setdefault("SECRET_KEY", "q" * 70)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/d")

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Modules whose deferred imports are checked. Route modules first: they are the
#: ones where a broken import becomes a 500 for a user rather than a crash at
#: boot that somebody notices immediately.
TARGETS = [
    "app/tefca_registry/rce/routes.py",
    "app/reports/routes.py",
    "app/api/admin.py",
]


def _deferred_imports(path: pathlib.Path):
    """(module, name) for every `from app.x import y` inside a function body."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom) and (inner.module or "").startswith("app."):
                for alias in inner.names:
                    out.append((inner.module, alias.name))
    return out


@pytest.mark.parametrize("relpath", TARGETS)
def test_every_deferred_app_import_resolves(relpath):
    path = ROOT / relpath
    if not path.exists():
        pytest.skip(f"{relpath} not present")

    broken = []
    for module_name, attr in _deferred_imports(path):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            broken.append(f"{module_name} (import failed: {type(exc).__name__})")
            continue
        if not hasattr(module, attr):
            broken.append(f"{module_name}.{attr}")

    assert not broken, (
        f"{relpath} defers imports that do not resolve: {broken}. "
        f"A deferred import only fails when its line runs, so this reaches a "
        f"user as an HTTP 500 rather than a startup error.")


def test_the_specific_regression_is_fixed():
    """The exact defect: client IP helper imported from the wrong module."""
    import app.core.client_ip as correct

    assert hasattr(correct, "get_client_ip")

    import app.core.security as wrong

    assert not hasattr(wrong, "get_client_ip"), (
        "get_client_ip reappeared in app.core.security; the delivery route "
        "should import it from app.core.client_ip")

    src = (ROOT / "app/tefca_registry/rce/routes.py").read_text(encoding="utf-8")
    assert "from app.core.client_ip import get_client_ip" in src
    assert "from app.core.security import get_client_ip" not in src


def test_client_ip_helper_is_callable_on_the_delivery_route():
    """Behavioural, not just structural: the helper must actually run.

    A request object with no client is the realistic edge (test clients, some
    proxy configurations) and must not raise.
    """
    import app.tefca_registry.rce.routes as routes

    class _Req:
        headers: dict = {}
        client = None

    routes._client_ip(_Req())  # must not raise
