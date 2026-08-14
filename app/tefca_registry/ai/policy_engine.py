"""Policy enforcement for the TEFCA AI control plane.

The policy lives in config/tefca_ai_policies.yaml. This module reads it and
answers questions about it. It does not encode policy of its own — every
decision below is a lookup, so the answer to "why was this denied?" is a line
in a YAML file a reviewer can read rather than a branch in Python.

FAIL CLOSED is the whole design. Every failure mode — missing file, unparseable
YAML, missing required key, PyYAML not installed — lands in the same place: a
policy object that denies every task. There is deliberately no code path that
produces a permissive default, because the alternative to AI here is the
deterministic pipeline, which is fully functional. Losing AI is a degraded
capability; losing governance is a compliance finding.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("docuaction.tefca.ai.policy")

# Resolved from this file rather than the process working directory. Azure App
# Service and pytest start the application from different directories, and a
# relative path would silently resolve to "missing" in one of them — which
# fails closed, but for the wrong reason and without an honest error.
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = _PACKAGE_ROOT / "config" / "tefca_ai_policies.yaml"


def policy_path() -> Path:
    """The policy file to load. Overridable for tests and staged rollouts."""
    override = (os.getenv("TEFCA_AI_POLICY_PATH") or "").strip()
    return Path(override) if override else DEFAULT_POLICY_PATH


@dataclass
class PolicyDecision:
    """The answer to "may this task run?" plus the reason, for the audit row."""
    allowed: bool
    reason: str = ""
    task: str = ""


# Keys without which the policy cannot be enforced. A file missing any of them
# is treated as corrupt rather than partially applied: a policy with no
# public_fields list would otherwise read as "no field is allowed OR every
# field is allowed" depending on which way the reader leans, and that ambiguity
# is exactly what must not exist in an egress control.
REQUIRED_KEYS = (
    "version",
    "permitted_tasks",
    "prohibited_tasks",
    "public_fields",
    "risk_tier",
    "evidence_quality",
)


class TEFCAPolicyEngine:
    """Reads config/tefca_ai_policies.yaml and enforces it.

    Construction never raises. A failed load produces an engine whose
    `loaded` is False and which denies everything, so callers do not need a
    try/except around instantiation to stay safe — the safe behaviour is the
    default, not something a caller has to remember to opt into.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else policy_path()
        self.policy: Dict[str, Any] = {}
        self.load_error: str = ""
        self.loaded: bool = False
        self._load()

    # ── Loading ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            import yaml  # imported here so an absent PyYAML disables AI rather
                         # than breaking import of the whole registry package
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            self._fail(f"PyYAML unavailable: {exc}")
            return

        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._fail(f"policy file not found: {self.path}")
            return
        except OSError as exc:
            self._fail(f"policy file unreadable: {exc}")
            return

        try:
            data = yaml.safe_load(raw)
        except Exception as exc:  # noqa: BLE001 — any parse failure fails closed
            self._fail(f"policy file is not valid YAML: {type(exc).__name__}: {exc}")
            return

        if not isinstance(data, dict):
            self._fail("policy file did not parse to a mapping")
            return

        self.policy = data
        problem = self._validate_policy_schema()
        if problem:
            self.policy = {}
            self._fail(problem)
            return

        self.loaded = True
        logger.info("TEFCA AI policy loaded: version=%s scope=%s",
                    data.get("version"), data.get("scope"))

    def _fail(self, reason: str) -> None:
        self.loaded = False
        self.load_error = reason
        logger.error("TEFCA AI policy NOT loaded — AI is disabled. %s", reason)

    def _validate_policy_schema(self) -> str:
        """Return a problem description, or "" when the policy is usable."""
        for key in REQUIRED_KEYS:
            if key not in self.policy:
                return f"policy is missing required key '{key}'"

        for key in ("permitted_tasks", "prohibited_tasks", "public_fields"):
            value = self.policy.get(key)
            if not isinstance(value, list) or not value:
                return f"policy key '{key}' must be a non-empty list"
            if not all(isinstance(v, str) for v in value):
                return f"policy key '{key}' must contain only strings"

        # A task appearing on both lists is not a judgement call to resolve at
        # runtime — it means the policy author contradicted themselves, and
        # guessing which line they meant would be the control plane inventing
        # policy. Refuse the file.
        overlap = set(self.policy["permitted_tasks"]) & set(self.policy["prohibited_tasks"])
        if overlap:
            return f"tasks appear as both permitted and prohibited: {sorted(overlap)}"

        if not isinstance(self.policy.get("risk_tier"), dict):
            return "policy key 'risk_tier' must be a mapping"

        evidence = self.policy.get("evidence_quality")
        if not isinstance(evidence, dict):
            return "policy key 'evidence_quality' must be a mapping"
        if not isinstance(evidence.get("weights"), dict):
            return "policy key 'evidence_quality.weights' must be a mapping"

        scope = self.policy.get("scope")
        if scope and scope != "tefca_entity_resolution_only":
            return (f"policy scope is {scope!r}; this engine only enforces "
                    "'tefca_entity_resolution_only'")

        return ""

    # ── Enforcement ──────────────────────────────────────────────────────
    def check_permission(self, task: str) -> PolicyDecision:
        """May `task` be sent to AI?

        Prohibited is checked before permitted so that a prohibited task is
        always reported as prohibited — the stronger and more useful statement
        in an audit trail — even if a future policy edit mistakenly lists it in
        both places.
        """
        if not self.loaded:
            return PolicyDecision(
                allowed=False, task=task,
                reason=f"AI policy unavailable ({self.load_error or 'not loaded'}) "
                       f"— failing closed")

        if task in self.policy["prohibited_tasks"]:
            return PolicyDecision(False, f"'{task}' is prohibited by policy", task)
        if task not in self.policy["permitted_tasks"]:
            return PolicyDecision(False, f"'{task}' not in approved task list", task)
        return PolicyDecision(True, f"'{task}' is permitted by policy "
                                    f"{self.policy.get('version')}", task)

    def get_review_requirement(self) -> str:
        """Always "required". Pre-production TEFCA has no unreviewed path."""
        return "required"

    def get_public_fields(self) -> List[str]:
        """The egress allowlist. Empty when the policy failed to load, which
        makes every field unauthorized rather than every field allowed."""
        if not self.loaded:
            return []
        return list(self.policy["public_fields"])

    def is_calibrated(self) -> bool:
        """True once evidence weights carry approved values instead of the
        CALIBRATION_REQUIRED placeholder. Nothing consumes this as a gate yet;
        it exists so the uncalibrated state is queryable rather than folklore."""
        if not self.loaded:
            return False
        weights = self.policy["evidence_quality"]["weights"]
        return weights.get("status") != "CALIBRATION_REQUIRED"

    def dual_verify_required(self) -> bool:
        if not self.loaded:
            return False
        return bool(self.policy["risk_tier"].get("dual_verify"))

    def get_weights(self) -> Dict[str, float]:
        """Numeric evidence weights only; the `status` marker is not a weight."""
        if not self.loaded:
            return {}
        return {k: float(v)
                for k, v in self.policy["evidence_quality"]["weights"].items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}

    @property
    def version(self) -> str:
        return str(self.policy.get("version", "unloaded"))
