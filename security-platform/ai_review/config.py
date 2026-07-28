"""Phase 2G configuration - cost and safety controls.

Gated twice on purpose: the key must exist AND the run must be explicitly requested.
An AI review that fires automatically in CI is a recurring bill nobody approved.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

PLATFORM_ROOT = Path(__file__).resolve().parent.parent

# Opus-class review is not needed to spot a missing auth check; Sonnet is the cost/
# quality balance here. Override with AI_REVIEW_MODEL.
MODEL = os.getenv("AI_REVIEW_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("AI_REVIEW_MAX_TOKENS", "4000"))

# Hard caps. A review pass must not be able to run away with spend.
MAX_FILES = int(os.getenv("AI_REVIEW_MAX_FILES", "8"))
MAX_SNIPPET_CHARS = int(os.getenv("AI_REVIEW_SNIPPET_CHARS", "6000"))
REQUESTS_PER_MINUTE = int(os.getenv("AI_REVIEW_RPM", "10"))

# Default targets: the highest-risk surfaces identified by Phases 0-2.
DEFAULT_TARGETS: List[str] = [
    "app/api/routes.py",
    "app/api/meeting_routes.py",
    "app/core/security.py",
    "app/case_management/routes.py",
    "app/tefca_registry/routes.py",
    "app/api/admin_users.py",
]

# Anything matching these is never sent. Belt-and-braces on top of the scrubber.
NEVER_SEND = ("*.env", "*.env.*", "*secret*", "*credential*", "*.pem", "*.key",
              "*settings.local.json", "*.db", "*.sqlite*")


def api_key() -> str:
    """Key from the environment, or the backend .env as a fallback. Never logged."""
    k = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k
    env = Path(r"C:/Imran_Coding projects/DocuAction/backend/.env")
    try:
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("ANTHROPIC_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""
