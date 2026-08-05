"""Anthropic adapter for the entity resolver.

The resolver itself imports no SDK — it takes an injected client satisfying a
two-method protocol (see entity_resolver.AIClient). This module is the one place
the SDK is named, so swapping vendors or routing through an internal gateway
means writing another adapter, not touching resolution logic.

The `anthropic` package is already a dependency (requirements.txt, and engine.py
uses it for the bulletin classifier), so this adds nothing to the install.

MODEL CHOICE: Sonnet here, Haiku in the bulletin. Deliberate split — headline
classification is high-volume and shallow, while deciding whether two records
describe one organisation is a judgement call on the residue that deterministic
matching could not settle. The volume is low enough (only inconclusive pairs
reach it) that the cost difference is immaterial.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger("docuaction.tefca.ai_client")

# Same environment variable the rest of the application uses. There is exactly
# one Anthropic key in each environment; this must not introduce a second name.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

DEFAULT_MAX_TOKENS = int(os.getenv("AI_ENTITY_RESOLUTION_MAX_TOKENS", "512"))
DEFAULT_TIMEOUT = float(os.getenv("AI_ENTITY_RESOLUTION_TIMEOUT", "30"))


class AnthropicClient:
    """Minimal synchronous adapter satisfying entity_resolver.AIClient."""

    def __init__(self, api_key: Optional[str] = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 timeout: float = DEFAULT_TIMEOUT):
        self.api_key = (api_key if api_key is not None else ANTHROPIC_API_KEY).strip()
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _ensure(self):
        if self._client is None:
            import anthropic  # imported lazily so an absent SDK is not an import-time failure
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
        return self._client

    def complete(self, *, model: str, system: str, prompt: str) -> str:
        """Return the model's raw text. Raises on failure.

        Raising rather than returning "" is correct here: the resolver catches
        the exception, records it in the audit trail with its type, and falls
        back to the deterministic result. Swallowing it would make an outage
        indistinguishable from a genuine "no answer".
        """
        client = self._ensure()
        resp = client.messages.create(
            model=model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = []
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", "") or "")
        return "\n".join(parts).strip()


def build_ai_client() -> Optional[AnthropicClient]:
    """The client the pipeline should use, or None when AI is off.

    Returns None when resolution is disabled or no key is configured, so callers
    get the deterministic-only path without needing to check two things.
    """
    from app.tefca_registry.entity_resolver import resolution_mode, MODE_DISABLED

    if resolution_mode() == MODE_DISABLED:
        return None
    client = AnthropicClient()
    if not client.available:
        logger.warning("AI_ENTITY_RESOLUTION is enabled but ANTHROPIC_API_KEY is "
                       "not set — falling back to deterministic resolution only")
        return None
    return client
