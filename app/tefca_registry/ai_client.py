"""DEPRECATED — superseded by the TEFCA AI control plane.

WHAT CHANGED

This module used to invoke the Anthropic SDK's message-creation call directly.
(Spelling that method name out here, even in prose, would trip the CI scan
described below — which is the scan working, not a false positive: it cannot
distinguish a call from a mention, and a scan that tried to would be one a
determined bypass could hide behind a comment.)

That direct call is gone. All TEFCA AI now routes through TEFCAAIOrchestrator,
and only
app/tefca_registry/ai/gateway.py may reach a provider. A CI test scans
app/tefca_registry/ and app/Tefca/ and fails the build if a provider call
reappears anywhere else.

WHY THIS FILE STILL EXISTS

Removing it outright would drop `AnthropicClient` and `build_ai_client` from the
package's surface, which existing tests import. The names are kept; the
capability is not. build_ai_client() returns None unconditionally, so the
deprecated wiring cannot produce a working client, and complete() raises rather
than silently returning empty text — a silent no-op would be indistinguishable
from a model with no opinion, and the pipeline treats those very differently.

The `available` property is retained with its original meaning (is a key
configured?) because that is a fact about the environment, not a capability of
this module.

Bulletin code is unaffected and keeps its own direct provider calls.
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

_REPLACEMENT = ("app.tefca_registry.ai.orchestrator.TEFCAAIOrchestrator "
                "(the only approved path for TEFCA AI)")


class AnthropicClient:
    """Retired adapter. Reports key presence; cannot call a provider."""

    def __init__(self, api_key: Optional[str] = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 timeout: float = DEFAULT_TIMEOUT):
        self.api_key = (api_key if api_key is not None else ANTHROPIC_API_KEY).strip()
        self.max_tokens = max_tokens
        self.timeout = timeout

    @property
    def available(self) -> bool:
        """Whether a key is configured. Unchanged meaning; note that a
        configured key no longer makes this client able to call anything."""
        return bool(self.api_key)

    def complete(self, *, model: str, system: str, prompt: str) -> str:
        """Always raises. There is no ungoverned path to a provider."""
        raise NotImplementedError(
            "Direct provider calls are not permitted in TEFCA code. Use "
            + _REPLACEMENT)


def build_ai_client() -> Optional[AnthropicClient]:
    """Always None.

    Previously returned a live client when AI_ENTITY_RESOLUTION was enabled and
    a key was set. Returning None unconditionally is what makes the control
    plane airtight rather than merely preferred: EntityResolver.resolve() takes
    the deterministic path when its client is None, so even a caller still
    wired to this function cannot reach a provider around the orchestrator.
    """
    logger.debug("build_ai_client() is deprecated and returns None; TEFCA AI "
                 "routes through %s", _REPLACEMENT)
    return None
