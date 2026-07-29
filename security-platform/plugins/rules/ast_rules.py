"""AST-based checks for Python.

These exist because regex genuinely cannot answer the questions here. Deciding
whether an endpoint is authenticated requires knowing how its *router* was
constructed - Phase 0's AUTHZ-01 was exactly this shape: 22 PHI endpoints that each
looked fine in isolation while the router carrying them had no dependencies at all.
A line-oriented matcher structurally cannot see that; an AST walk can.

Pure stdlib (`ast`). Never raises: a file that fails to parse is skipped and noted.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.models import Category, ComplianceMapping, Confidence, Severity

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Callables that establish identity. Names, not full paths, because import style
# varies across this codebase (get_current_user, require_role, guard, ...).
AUTH_CALLABLES = {
    "get_current_user", "require_role", "require_admin", "get_current_active_user",
    "guard", "verify_token", "authenticate", "get_current_user_optional",
    "require_permission", "check_auth", "current_user",
}

# Routes that are legitimately public.
PUBLIC_PATH_HINTS = ("/health", "/healthz", "/ready", "/live", "/metrics", "/docs",
                     "/openapi", "/login", "/signup", "/register", "/token",
                     "/forgot-password", "/reset-password", "/refresh", "/callback",
                     "/webhook", "/public", "/demo", "/preview")


class AstFinding:
    """Lightweight carrier; converted to a core Finding by the plugin."""

    def __init__(self, rule_id: str, title: str, severity: Severity, line: int,
                 snippet: str, description: str, remediation: str,
                 compliance: ComplianceMapping, confidence: Confidence,
                 effort: str = ""):
        self.rule_id = rule_id
        self.title = title
        self.severity = severity
        self.line = line
        self.snippet = snippet
        self.description = description
        self.remediation = remediation
        self.compliance = compliance
        self.confidence = confidence
        self.effort = effort


def _decorator_parts(dec: ast.AST) -> Tuple[str, str, List[ast.keyword], List[ast.expr]]:
    """Return (object_name, attribute, keywords, args) for @obj.attr(...) decorators."""
    call = dec if isinstance(dec, ast.Call) else None
    func = call.func if call else dec
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr,
                list(call.keywords) if call else [], list(call.args) if call else [])
    return ("", "", [], [])


def _mentions_auth(node: Optional[ast.AST], known: Optional[set] = None) -> bool:
    """True if any Name/Attribute in the subtree is a known auth callable."""
    if node is None:
        return False
    names = known if known is not None else AUTH_CALLABLES
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in names:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in names:
            return True
    return False


def _module_auth_callables(tree: ast.AST) -> set:
    """AUTH_CALLABLES plus locally-defined wrappers that delegate to one.

    Real codebases rarely call get_current_user directly on every route. This one
    defines module-local gatekeepers such as

        async def require_ats_access(user=Depends(get_current_user)): ...

    and then uses `user=Depends(require_ats_access)` on 23 endpoints. Without
    resolving that indirection every one of those routes looks unauthenticated, which
    is a false positive severe enough to bury the real findings.

    Two passes so a wrapper-of-a-wrapper also resolves; that is as deep as this
    codebase goes and it keeps the analysis single-file and cheap.
    """
    known = set(AUTH_CALLABLES)
    for _ in range(2):
        added = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in known:
                continue
            # A function whose own parameter defaults depend on an auth callable is
            # itself an auth callable.
            if _mentions_auth(node.args, known):
                known.add(node.name)
                added = True
        if not added:
            break
    return known


def _protected_routers(tree: ast.AST, known: Optional[set] = None) -> Dict[str, bool]:
    """Map router variable name -> whether it was built with auth dependencies.

    Catches `APIRouter(..., dependencies=[Depends(get_current_user)])`, which is the
    router-level pattern used to remediate AUTHZ-01.
    """
    protected: Dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "APIRouter":
            continue
        has_auth = any(kw.arg == "dependencies" and _mentions_auth(kw.value, known)
                       for kw in node.value.keywords)
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                protected[tgt.id] = has_auth
    return protected


def _route_path(args: List[ast.expr]) -> str:
    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        return args[0].value
    return ""


def check_endpoint_auth(tree: ast.AST, source_lines: List[str]) -> List[AstFinding]:
    """Flag FastAPI endpoints with no authentication at route or router level."""
    out: List[AstFinding] = []
    known = _module_auth_callables(tree)
    routers = _protected_routers(tree, known)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            obj, attr, keywords, args = _decorator_parts(dec)
            if attr not in HTTP_METHODS or not obj:
                continue

            path = _route_path(args)
            if any(h in path.lower() for h in PUBLIC_PATH_HINTS):
                continue

            # Route-level dependencies=[...]
            if any(kw.arg == "dependencies" and _mentions_auth(kw.value, known)
                   for kw in keywords):
                continue
            # Router-level dependencies
            if routers.get(obj):
                continue
            # Parameter-level: `user = Depends(get_current_user)` in the signature.
            # Walking node.args covers both positional defaults and kw-only defaults,
            # as well as Annotated[...] dependency declarations.
            if _mentions_auth(node.args, known):
                continue

            line = getattr(dec, "lineno", node.lineno)
            snippet = source_lines[line - 1].strip() if 0 < line <= len(source_lines) else ""
            out.append(AstFinding(
                rule_id="AGT-AUTHZ-001",
                title=f"Endpoint '{attr.upper()} {path or node.name}' has no authentication dependency",
                severity=Severity.HIGH,
                line=line,
                snippet=snippet,
                description=(
                    "No authentication dependency was found at route level, on the "
                    "router, or in the handler signature. Phase 0 finding AUTHZ-01 was "
                    "this exact shape: an entire PHI router mounted without "
                    "dependencies, where each individual route looked unremarkable."),
                remediation=(
                    "Add dependencies=[Depends(get_current_user)] to the route or the "
                    "APIRouter, plus a role/ownership check where the resource is "
                    "user-scoped. If the endpoint is intentionally public, that is a "
                    "deliberate decision worth recording."),
                compliance=ComplianceMapping(
                    cwe=["306", "862"], owasp_top10=["A01:2021"],
                    owasp_api_top10=["API5:2023"], owasp_asvs=["V4.1.1"],
                    nist_800_53=["AC-3", "IA-2"],
                    hipaa=["164.312(a)(1)", "164.312(d)"], cwe_top25=True),
                confidence=Confidence.MEDIUM,
                effort="0.5-1d",
            ))
    return out


def check_pydantic_open_models(tree: ast.AST, source_lines: List[str]) -> List[AstFinding]:
    """Flag Pydantic models that accept arbitrary extra fields (mass assignment)."""
    out: List[AstFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                 for b in node.bases}
        if "BaseModel" not in bases:
            continue
        for sub in ast.walk(node):
            allow = False
            if isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name) and t.id == "extra" and \
                            isinstance(sub.value, ast.Constant) and \
                            sub.value.value == "allow":
                        allow = True
            if isinstance(sub, ast.keyword) and sub.arg == "extra" and \
                    isinstance(sub.value, ast.Constant) and sub.value.value == "allow":
                allow = True
            if allow:
                line = getattr(sub, "lineno", node.lineno)
                out.append(AstFinding(
                    rule_id="AGT-MASSASSIGN-001",
                    title=f"Pydantic model '{node.name}' accepts arbitrary extra fields",
                    severity=Severity.MEDIUM,
                    line=line,
                    snippet=source_lines[line - 1].strip() if 0 < line <= len(source_lines) else "",
                    description="extra='allow' lets a client submit fields the model does "
                                "not declare, which is mass assignment / over-posting.",
                    remediation="Use extra='forbid' (or the default 'ignore') and declare "
                                "every accepted field explicitly.",
                    compliance=ComplianceMapping(
                        cwe=["915"], owasp_top10=["A08:2021"],
                        owasp_api_top10=["API6:2023"], owasp_asvs=["V5.1.2"],
                        nist_800_53=["SI-10"]),
                    confidence=Confidence.HIGH,
                    effort="0.5d",
                ))
                break
    return out


def check_sql_fstring(tree: ast.AST, source_lines: List[str]) -> List[AstFinding]:
    """AST-accurate raw-SQL check: text()/execute() called with an f-string.

    More precise than the regex equivalent because it sees the actual JoinedStr node
    rather than guessing from characters on the line.
    """
    out: List[AstFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name not in ("text", "execute", "executemany"):
            continue
        for arg in node.args:
            interpolated = isinstance(arg, ast.JoinedStr) or (
                isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)))
            if not interpolated:
                continue
            line = getattr(node, "lineno", 0)
            out.append(AstFinding(
                rule_id="AGT-SQL-002",
                title="SQL statement built with an f-string or concatenation",
                severity=Severity.HIGH,
                line=line,
                snippet=source_lines[line - 1].strip() if 0 < line <= len(source_lines) else "",
                description="The SQL text passed to text()/execute() is interpolated at "
                            "runtime. If any interpolated value is request-derived this "
                            "is SQL injection.",
                remediation="Bind parameters: text('... :p').bindparams(p=value), or use "
                            "the ORM query API.",
                compliance=ComplianceMapping(
                    cwe=["89"], owasp_top10=["A03:2021"],
                    owasp_api_top10=["API8:2023"], owasp_asvs=["V5.3.4"],
                    nist_800_53=["SI-10"], cwe_top25=True),
                confidence=Confidence.HIGH,
                effort="0.5d",
            ))
            break
    return out


AST_CHECKS = (check_endpoint_auth, check_pydantic_open_models, check_sql_fstring)


def run_ast_checks(path: Path) -> Tuple[List[AstFinding], str]:
    """Run every AST check on one Python file. Returns (findings, parse_error)."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], f"syntax error line {exc.lineno}"
    except Exception as exc:
        return [], str(exc)[:120]

    lines = source.splitlines()
    out: List[AstFinding] = []
    for check in AST_CHECKS:
        try:
            out.extend(check(tree, lines))
        except Exception:
            continue        # one broken check must not lose the others
    return out, ""
