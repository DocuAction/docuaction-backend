"""Versioned prompts for TEFCA entity resolution.

One prompt, one version string, held in code. A prompt change is a code change
that goes through git — which is the point: the audit row records
`prompt_version`, so "what exactly was this model asked?" for any past
determination resolves to a specific commit. A prompt loaded from a database or
an environment variable could not answer that question.

`change_system_prompt` is on the prohibited-task list in the policy, so AI
cannot reach this module's contents through any governed path either.

VERSIONING RULE: any change to `template` requires a new `version`. Reusing a
version string across different text silently invalidates every audit row that
cites it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Prompt:
    """Frozen: a registry entry must not be mutable at runtime."""
    version: str
    template: str

    def render(self, context: Dict[str, Any]) -> str:
        """Fill the template from an orchestrator-built context.

        Reads the nested {"submitted": {...}, "registry": {...}} shape the
        orchestrator produces after egress filtering. Missing keys render as
        "(not provided)" rather than raising: a record with no NPI is ordinary
        directory data, and it must reach the model as a visible absence rather
        than crash the pipeline or, worse, silently render as "None" and be read
        by the model as a value.
        """
        submitted = (context or {}).get("submitted", {}) or {}
        registry = (context or {}).get("registry", {}) or {}

        def field(source: Dict[str, Any], key: str) -> str:
            value = source.get(key)
            return str(value).strip() if value not in (None, "") else "(not provided)"

        return self.template.format(
            submitted_name=field(submitted, "name"),
            submitted_address=field(submitted, "address"),
            submitted_npi=field(submitted, "npi"),
            submitted_type=field(submitted, "entity_type"),
            registry_name=field(registry, "name"),
            registry_address=field(registry, "address"),
            registry_npi=field(registry, "npi"),
            registry_type=field(registry, "entity_type"),
        )


ENTITY_MATCH_V1_2 = Prompt(
    version="entity-match-v1.2",
    template="""VERSION: entity-match-v1.2
ROLE: You compare two healthcare organization records.

ABSOLUTE RULES:
1. Respond ONLY in JSON format.
2. Required fields: match, confidence, rationale
3. confidence must be between 0.0 and 1.0
4. You are ADVISORY. You do NOT decide if entities match.
5. A human reviewer makes the final determination.
6. DO NOT say "this is definitely" or "I can confirm"
7. Say "likely the same entity" or "appears to match"
8. If NPIs are identical -> match=true, confidence=1.0
9. If NPIs differ -> match=false regardless of name
10. If uncertain -> confidence below 0.5, match=null

YOUR OUTPUT IS VALIDATED. Invalid JSON or missing
fields cause rejection. Banned assertions cause
rejection. Stay within your role as advisory.

SUBMITTED ENTITY:
Name: {submitted_name}
Address: {submitted_address}
NPI: {submitted_npi}
Type: {submitted_type}

REGISTRY RECORD:
Name: {registry_name}
Address: {registry_address}
NPI: {registry_npi}
Type: {registry_type}
""",
)


class TEFCAPromptRegistry:
    """The complete set of prompts TEFCA may send. One entry."""

    PROMPTS: Dict[str, Prompt] = {
        "entity_match": ENTITY_MATCH_V1_2,
    }

    @classmethod
    def get(cls, task_type: str) -> Prompt:
        """Raise on an unknown task rather than substituting a default.

        A KeyError here is a programming error caught at the call site. Falling
        back to some other prompt would mean a determination was made by a
        prompt nobody chose for it.
        """
        try:
            return cls.PROMPTS[task_type]
        except KeyError:
            raise KeyError(
                f"no registered TEFCA prompt for task type {task_type!r}; "
                f"known: {sorted(cls.PROMPTS)}") from None

    @classmethod
    def versions(cls) -> Dict[str, str]:
        """task -> version, for health checks and compliance evidence."""
        return {task: prompt.version for task, prompt in cls.PROMPTS.items()}
