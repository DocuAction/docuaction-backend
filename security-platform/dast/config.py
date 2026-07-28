"""DAST target configuration and the production guard.

THE PRODUCTION GUARD IS THE MOST IMPORTANT CODE IN THIS PACKAGE
    Everything else here is test plumbing. This module's job is to make it
    structurally impossible to point an active security test at production.

    Three properties make the guard trustworthy:

    1. The blocklist is a module-level frozenset, not configuration. Config can ADD
       forbidden patterns; it can never remove one. There is no flag, environment
       variable, or argument that disables the check - `assert_safe_target` has no
       override parameter, so there is nothing to pass.

    2. It is allow-list AND deny-list. A target must both (a) not match any forbidden
       pattern and (b) match an explicitly permitted host. A new production hostname
       nobody remembered to blocklist still fails, because it was never allow-listed.

    3. It is checked at construction AND before every single request, so a redirect,
       a config reload, or a mutated attribute cannot smuggle a request through.

    The substring "prod" is forbidden anywhere in the URL. That is deliberately blunt
    and will reject harmless names like "product-api" - a false refusal costs a config
    edit, a false permit costs an attack on a live healthcare system.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

PLATFORM_ROOT = Path(__file__).resolve().parent.parent


class ProductionTargetError(RuntimeError):
    """Raised when a target is, or might be, production. Never caught internally."""


# ── Immutable safety constants (NOT configurable) ─────────────────────────────

#: Substrings that forbid a target outright, matched case-insensitively against the
#: whole URL. Frozen at import; config may extend the effective list but never shrink it.
FORBIDDEN_PATTERNS: frozenset = frozenset({
    "prod",
    "api-prod.docuaction.io",
    "app.docuaction.io",
    "api.docuaction.io",           # the real production backend (memory: prod host)
    "docuaction.io",               # any apex/production domain
    "zesty-ambition",              # legacy Railway production service
    "interchange.proxy.rlwy.net",  # production Postgres proxy
    "witty-tree",                  # production SWA default host
})

#: Hosts an active test may target. Anything else is refused even if it matches no
#: forbidden pattern - unknown is not the same as safe.
ALLOWED_HOSTS: frozenset = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.docker.internal",
    "docuaction-dev.azurewebsites.net",
})

#: Private/loopback ranges accepted as local targets.
_PRIVATE_RE = re.compile(
    r"^(?:127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|localhost$|\[?::1\]?$)")


def assert_safe_target(url: str, extra_forbidden: Optional[List[str]] = None) -> str:
    """Return the URL if it is safe to test; otherwise raise ProductionTargetError.

    Intentionally has no `force`, `allow_production`, or `override` parameter. There
    is no supported way to test production with this framework.
    """
    if not url or not str(url).strip():
        raise ProductionTargetError("empty target URL")

    raw = str(url).strip()
    low = raw.lower()

    forbidden = set(FORBIDDEN_PATTERNS) | {
        str(p).lower() for p in (extra_forbidden or []) if str(p).strip()}
    for pat in sorted(forbidden):
        if pat in low:
            raise ProductionTargetError(
                f"REFUSED: target {raw!r} matches forbidden pattern {pat!r}. "
                f"Active security testing against production is not supported by this "
                f"framework and cannot be enabled by configuration.")

    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ProductionTargetError(f"REFUSED: cannot parse a hostname from {raw!r}")

    if host in ALLOWED_HOSTS or _PRIVATE_RE.match(host):
        return raw

    raise ProductionTargetError(
        f"REFUSED: host {host!r} is not in the allow-list "
        f"{sorted(ALLOWED_HOSTS)}. Unknown hosts are refused rather than assumed "
        f"safe - add it to ALLOWED_HOSTS in dast/config.py only if you have "
        f"confirmed it is a non-production environment you are authorised to test.")


def is_safe_target(url: str) -> bool:
    try:
        assert_safe_target(url)
        return True
    except ProductionTargetError:
        return False


# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "target_url": "http://localhost:8000",
    "dev_url": "https://docuaction-dev.azurewebsites.net",
    "never_test": ["https://api-prod.docuaction.io", "https://app.docuaction.io", "prod"],
    "rate_limit": {"max_requests_per_window": 8, "window_seconds": 6},
    "timeout_seconds": 45,
    "evidence_dir": "evidence",
    # Writes to a dev database are permitted (the safety rules forbid PRODUCTION
    # database modification). Set false to keep the run strictly read-only.
    "allow_write_tests": True,
    "test_account_prefix": "agt-dast-",
}


@dataclass
class RateLimit:
    max_requests_per_window: int = 8
    window_seconds: float = 6.0


@dataclass
class DastConfig:
    target_url: str = ""
    dev_url: str = ""
    never_test: List[str] = field(default_factory=list)
    rate_limit: RateLimit = field(default_factory=RateLimit)
    timeout_seconds: float = 45.0
    evidence_dir: str = "evidence"
    allow_write_tests: bool = True
    test_account_prefix: str = "agt-dast-"
    credentials: Dict[str, Dict[str, str]] = field(default_factory=dict)
    resolved_target: str = ""
    target_kind: str = ""          # "localhost" | "dev"

    @classmethod
    def load(cls, path: Optional[Path] = None,
             override_target: Optional[str] = None) -> "DastConfig":
        raw = dict(DEFAULT_CONFIG)
        p = Path(path) if path else (PLATFORM_ROOT / "config" / "dast.json")
        if p.exists():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                raw.update(loaded.get("dast", loaded))
            except Exception:
                pass

        rl = raw.get("rate_limit") or {}
        cfg = cls(
            target_url=raw.get("target_url", ""),
            dev_url=raw.get("dev_url", ""),
            never_test=list(raw.get("never_test") or []),
            rate_limit=RateLimit(
                int(rl.get("max_requests_per_window", 8)),
                float(rl.get("window_seconds", 6))),
            timeout_seconds=float(raw.get("timeout_seconds", 45)),
            evidence_dir=raw.get("evidence_dir", "evidence"),
            allow_write_tests=bool(raw.get("allow_write_tests", True)),
            test_account_prefix=raw.get("test_account_prefix", "agt-dast-"),
            credentials=raw.get("credentials") or {},
        )
        if override_target:
            cfg.target_url = override_target
        return cfg

    def candidate_targets(self) -> List[tuple]:
        out = []
        if self.target_url:
            out.append(("localhost" if _PRIVATE_RE.match(
                (urlparse(self.target_url).hostname or "")) else "configured",
                self.target_url))
        if self.dev_url:
            out.append(("dev", self.dev_url))
        return out

    def validate(self) -> None:
        """Refuse the whole run if any configured target is unsafe."""
        for _kind, url in self.candidate_targets():
            assert_safe_target(url, self.never_test)
