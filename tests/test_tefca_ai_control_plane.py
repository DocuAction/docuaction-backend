"""TEFCA AI control plane.

These tests assert the control properties, not the AI. Nothing here contacts a
provider: every gateway is a double, and the point of each test is that a
specific bad outcome is structurally impossible rather than merely unlikely.

The load-bearing ones are the negatives — malformed policy denies everything,
a perfect result still requires a human, no module outside gateway.py can reach
a provider. Those are the properties an auditor asks about.
"""
from __future__ import annotations

import json
import pathlib
import textwrap

import pytest

from app.tefca_registry.ai.agent_boundary import (
    AgentBoundaryViolation, TEFCAAgentBoundary,
)
from app.tefca_registry.ai.gateway import (
    ALLOWED_MODELS, TEMPERATURE, TOKEN_LIMITS, GatewayError, GatewayResponse,
    TEFCAAIGateway,
)
from app.tefca_registry.ai.human_gate import TEFCAHumanGate
from app.tefca_registry.ai.orchestrator import TEFCAAIOrchestrator
from app.tefca_registry.ai.policy_engine import TEFCAPolicyEngine
from app.tefca_registry.ai.prompt_registry import TEFCAPromptRegistry
from app.tefca_registry.ai.validation import (
    TEFCAEvidenceQualityEngine, TEFCAValidationEngine,
)

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]

VALID_OUTPUT = json.dumps({
    "match": True,
    "confidence": 0.91,
    "rationale": "The addresses normalize to the same value and the names appear to match.",
})


def _context(fields=("name", "address", "npi", "entity_type")):
    return {
        "submitted": {"name": "Mercy Health LLC", "address": "1 Main St"},
        "registry": {"name": "Mercy Health Inc", "address": "1 Main Street"},
        "fields_sent": list(fields),
    }


class _StubGateway:
    """Records what it was asked and returns a scripted answer."""

    def __init__(self, response=None, dual_available=False, agree=None):
        self._response = response
        self.dual_available = dual_available
        self._agree = agree
        self.calls = []

    async def call(self, prompt, context=None, tier="standard",
                   temperature=TEMPERATURE, task="entity_match"):
        self.calls.append(("call", prompt, context))
        return self._response

    async def call_dual(self, prompt, context=None, tier="standard",
                        temperature=TEMPERATURE, task="entity_match"):
        from app.tefca_registry.ai.gateway import DualResponse
        self.calls.append(("call_dual", prompt, context))
        return DualResponse(self._response, self._response, self._agree)


def _response(text=VALID_OUTPUT):
    return GatewayResponse(text=text, provider="claude", model="claude-sonnet-4-6",
                           latency_ms=42.0)


def _orchestrator(gateway=None, policy=None):
    policy = policy or TEFCAPolicyEngine()
    return TEFCAAIOrchestrator(
        policy=policy,
        gateway=gateway or _StubGateway(_response()),
        validator=TEFCAValidationEngine(policy=policy),
        evidence=TEFCAEvidenceQualityEngine(policy=policy),
    )


def _write_policy(tmp_path, monkeypatch, body: str) -> TEFCAPolicyEngine:
    path = tmp_path / "policy.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("TEFCA_AI_POLICY_PATH", str(path))
    return TEFCAPolicyEngine()


# ── Policy engine ────────────────────────────────────────────────────────────

def test_policy_loaded_from_yaml():
    """The shipped policy file parses and carries the expected scope."""
    policy = TEFCAPolicyEngine()
    assert policy.loaded, policy.load_error
    assert policy.policy["scope"] == "tefca_entity_resolution_only"
    assert policy.version == "pre-production-v1"
    # Placeholder weights: the score must not gate anything until ONC approves
    # calibrated values, and this is the flag that says so.
    assert policy.is_calibrated() is False


def test_permitted_task_allowed():
    decision = TEFCAPolicyEngine().check_permission("compare_entity_names")
    assert decision.allowed is True


def test_prohibited_task_denied():
    """A prohibited task is reported as prohibited, not merely unlisted — the
    stronger statement, and the one an audit row should carry."""
    decision = TEFCAPolicyEngine().check_permission("approve_entity")
    assert decision.allowed is False
    assert "prohibited" in decision.reason


def test_unknown_task_denied():
    """Default-deny: a task nobody approved cannot reach AI by omission."""
    decision = TEFCAPolicyEngine().check_permission("invent_a_new_capability")
    assert decision.allowed is False
    assert "not in approved task list" in decision.reason


def test_missing_yaml_disables_ai(tmp_path, monkeypatch):
    monkeypatch.setenv("TEFCA_AI_POLICY_PATH", str(tmp_path / "absent.yaml"))
    policy = TEFCAPolicyEngine()

    assert policy.loaded is False
    # Every task, including permitted ones. Fail closed, never fail open.
    assert policy.check_permission("compare_entity_names").allowed is False
    # And no field is authorized for egress, rather than all of them.
    assert policy.get_public_fields() == []


def test_malformed_yaml_disables_ai(tmp_path, monkeypatch):
    policy = _write_policy(tmp_path, monkeypatch, """
        permitted_tasks: [compare_entity_names
        public_fields: {this is not
    """)
    assert policy.loaded is False
    assert policy.check_permission("compare_entity_names").allowed is False


def test_incomplete_yaml_disables_ai(tmp_path, monkeypatch):
    """Valid YAML missing a required key is corrupt, not partially applied. A
    policy with no public_fields must not be read as 'no restrictions'."""
    policy = _write_policy(tmp_path, monkeypatch, """
        version: "broken"
        permitted_tasks: [compare_entity_names]
    """)
    assert policy.loaded is False
    assert "missing required key" in policy.load_error


def test_contradictory_yaml_disables_ai(tmp_path, monkeypatch):
    """A task on both lists means the author contradicted themselves. Guessing
    which line they meant would be the control plane inventing policy."""
    policy = _write_policy(tmp_path, monkeypatch, """
        version: "contradictory"
        permitted_tasks: [compare_entity_names, approve_entity]
        prohibited_tasks: [approve_entity]
        public_fields: [name]
        risk_tier: {human_review: always}
        evidence_quality: {weights: {npi_exact_match: 0.3}}
    """)
    assert policy.loaded is False
    assert "both permitted and prohibited" in policy.load_error


def test_public_fields_enforced():
    """The egress allowlist is exactly the approved public directory fields —
    no PHI-bearing field is reachable by default."""
    fields = set(TEFCAPolicyEngine().get_public_fields())
    assert fields == {"name", "address", "npi", "entity_type", "state", "tefcaid"}


# ── Gateway ──────────────────────────────────────────────────────────────────

@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-claude")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")


async def test_primary_called_first(keyed, monkeypatch):
    seen = []

    async def fake(self, provider, prompt, tier, task, temperature):
        seen.append(provider)
        return _response()

    monkeypatch.setattr(TEFCAAIGateway, "_call_provider", fake)
    gateway = TEFCAAIGateway(primary="claude")

    assert (await gateway.call("prompt")) is not None
    # Primary answered, so the fallback is never contacted.
    assert seen == ["claude"]


async def test_fallback_on_failure(keyed, monkeypatch):
    seen = []

    async def fake(self, provider, prompt, tier, task, temperature):
        seen.append(provider)
        return None if provider == "claude" else _response()

    monkeypatch.setattr(TEFCAAIGateway, "_call_provider", fake)
    result = await TEFCAAIGateway(primary="claude").call("prompt")

    assert seen == ["claude", "openai"]
    assert result is not None


async def test_both_fail_returns_none(keyed, monkeypatch):
    """None is a first-class outcome, not an exception. The caller's response
    is to continue deterministically, which is a supported state."""
    async def fake(self, provider, prompt, tier, task, temperature):
        return None

    monkeypatch.setattr(TEFCAAIGateway, "_call_provider", fake)
    assert (await TEFCAAIGateway().call("prompt")) is None


async def test_token_limits_enforced(keyed, monkeypatch):
    dispatched = []

    async def fake(self, provider, prompt, tier, task, temperature):
        dispatched.append(provider)
        return _response()

    monkeypatch.setattr(TEFCAAIGateway, "_call_provider", fake)
    cap = TOKEN_LIMITS["entity_match"]["max_input"]
    oversized = "x" * (cap * 8)  # comfortably past the 4-chars-per-token estimate

    assert (await TEFCAAIGateway().call(oversized)) is None
    # Refused locally: an oversized payload never leaves the process.
    assert dispatched == []


async def test_temperature_always_zero(keyed, monkeypatch):
    """Determinism is a compliance property, so a caller cannot opt out of it."""
    assert TEMPERATURE == 0.0
    gateway = TEFCAAIGateway()

    with pytest.raises(GatewayError):
        await gateway.call("prompt", temperature=0.7)
    with pytest.raises(GatewayError):
        await gateway.call_dual("prompt", temperature=1.0)

    # And the value actually handed to the provider adapter is 0.0.
    seen = {}

    async def fake(self, provider, prompt, tier, task, temperature):
        seen["temperature"] = temperature
        return _response()

    monkeypatch.setattr(TEFCAAIGateway, "_call_provider", fake)
    await gateway.call("prompt")
    assert seen["temperature"] == 0.0


async def test_circuit_breaker_opens_after_repeated_failures(keyed, monkeypatch):
    """Five consecutive failures stop the provider being tried at all, so an
    outage costs one round of retries rather than one per request."""
    attempts = []

    async def always_fails(self, model, prompt, max_output, temperature):
        attempts.append(model)
        raise RuntimeError("provider down")

    monkeypatch.setattr(TEFCAAIGateway, "_call_anthropic", always_fails)
    monkeypatch.setattr(TEFCAAIGateway, "_call_openai", always_fails)
    gateway = TEFCAAIGateway(primary="claude", fallback_enabled=False)

    for _ in range(5):
        assert (await gateway.call("prompt")) is None

    before = len(attempts)
    assert gateway.provider_available("claude") is False
    assert (await gateway.call("prompt")) is None
    assert len(attempts) == before  # circuit open: nothing dispatched


def test_only_allowlisted_models_are_reachable():
    """A model absent from the allowlist cannot be reached through the gateway,
    so 'which model produced this determination' stays answerable from git."""
    assert TEFCAAIGateway.resolve_model("claude", "standard") == "claude-sonnet-4-6"
    assert TEFCAAIGateway.resolve_model("claude", "fast") == "claude-haiku-4-5"
    with pytest.raises(GatewayError):
        TEFCAAIGateway.resolve_model("claude", "unlimited")
    with pytest.raises(GatewayError):
        TEFCAAIGateway.resolve_model("some-other-vendor", "standard")


# ── Validation ───────────────────────────────────────────────────────────────

def test_entity_match_json_required():
    """No brace-scraping salvage: prose around the JSON means the model left
    its instructions, and repairing that hides the drift worth detecting."""
    validator = TEFCAValidationEngine()
    result = validator.validate_entity_match("Sure! Here is the answer: yes.", {})
    assert result.passed is False
    assert "Invalid JSON" in result.errors


def test_entity_match_missing_fields_rejected():
    validator = TEFCAValidationEngine()
    result = validator.validate_entity_match(json.dumps({"match": True}), {})

    assert result.passed is False
    # All failures collected in one pass, not just the first.
    assert "Missing: confidence" in result.errors
    assert "Missing: rationale" in result.errors


def test_banned_assertions_caught():
    """An advisory system must not be able to manufacture certainty it has no
    basis for: it compares directory records, it cannot 'confirm' identity."""
    validator = TEFCAValidationEngine()
    for phrase in ("This is definitely the same entity.",
                   "I can confirm these match.",
                   "There is no doubt about it.",
                   "This entity is clearly the same one.",
                   "I am certain."):
        result = validator.validate_entity_match(
            json.dumps({"match": True, "confidence": 0.9, "rationale": phrase}), {})
        assert result.passed is False, phrase
        assert any("Prohibited assertion" in e for e in result.errors), phrase


def test_confidence_range_enforced():
    validator = TEFCAValidationEngine()
    for bad in (1.5, -0.2):
        result = validator.validate_entity_match(
            json.dumps({"match": True, "confidence": bad, "rationale": "ok"}), {})
        assert result.passed is False
        assert any("outside [0,1]" in e for e in result.errors)

    result = validator.validate_entity_match(
        json.dumps({"match": True, "confidence": "high", "rationale": "ok"}), {})
    assert result.passed is False
    assert any("is not a number" in e for e in result.errors)


def test_unauthorized_fields_caught():
    """The independent second egress check. The orchestrator filters, this
    verifies — separate code paths, so a bug in the filter is caught here."""
    validator = TEFCAValidationEngine()
    context = _context(fields=["name", "npi", "ssn", "date_of_birth"])
    result = validator.validate_entity_match(VALID_OUTPUT, context)

    assert result.passed is False
    unauthorized = [e for e in result.errors if "Unauthorized fields sent" in e]
    assert unauthorized and "ssn" in unauthorized[0] and "date_of_birth" in unauthorized[0]


def test_valid_output_passes_and_confidence_is_quarantined():
    """A clean response passes, and its confidence is reachable only under a
    name that makes decision use obviously wrong."""
    result = TEFCAValidationEngine().validate_entity_match(VALID_OUTPUT, _context())
    assert result.passed is True, result.errors
    assert result.ai_raw_confidence == 0.91


def test_evidence_score_uses_objective_signals_not_model_confidence():
    """The score moves with measurable facts and is blind to what the model
    said about itself — the distinction the whole design rests on."""
    engine = TEFCAEvidenceQualityEngine()

    assert engine.calculate_score({}) == 0.0
    assert engine.calculate_score(None) == 0.0

    npi_only = engine.calculate_score({"npi_exact_match": True})
    assert npi_only == pytest.approx(0.30)

    # A model claiming total certainty contributes nothing.
    assert engine.calculate_score({"confidence": 1.0, "ai_raw_confidence": 1.0}) == 0.0

    # Weights accumulate and cap at 1.0.
    everything = engine.calculate_score({
        "npi_exact_match": True, "address_normalized_match": True,
        "usps_zip4_match": True, "name_similarity": 0.99,
        "entity_type_match": True, "source_agreement": True,
        "no_conflicting_fields": True, "models_agree": True,
    })
    assert everything == 1.0

    # The threshold is strict: 0.85 exactly does not fire.
    assert engine.calculate_score({"name_similarity": 0.85}) == 0.0
    assert engine.calculate_score({"name_similarity": 0.86}) == pytest.approx(0.15)


# ── Human gate ───────────────────────────────────────────────────────────────

async def test_tefca_always_requires_human_review():
    decision = await TEFCAHumanGate().evaluate(None, None, None)
    assert decision.proceed is False
    assert decision.human_review_required is True
    assert decision.reason == "tefca_always_requires_human_review"
    assert decision.action == "queue_for_human_review"


async def test_dual_agree_still_requires_human():
    """Two models trained on overlapping data agreeing is weak evidence.
    Treating it as authorization is the failure this architecture prevents."""
    gate = TEFCAHumanGate()
    agreeing = type("Dual", (), {"agree": True, "primary": _response(),
                                 "secondary": _response()})()
    assert (await gate.evaluate(agreeing, None, None)).proceed is False


async def test_high_evidence_still_requires_human():
    """Perfect evidence, perfect validation, perfect confidence — still a human.
    This is a compliance requirement, not a threshold."""
    perfect_validation = type("V", (), {"passed": True, "errors": []})()
    perfect_result = _response()
    perfect_result.confidence = 1.0

    decision = await TEFCAHumanGate().evaluate(perfect_result, perfect_validation, None)
    assert decision.proceed is False


async def test_human_gate_has_no_configurable_bypass():
    """There is no threshold constant and no settings lookup to loosen — the
    gate's decision cannot depend on its inputs, so it cannot be argued with."""
    gate = TEFCAHumanGate()
    for args in ((None, None, None), ("anything", "at", "all"), (_response(), {}, {})):
        assert (await gate.evaluate(*args)).proceed is False

    # Asserted on the AST rather than the source text, so the module's own
    # prose about thresholds does not match. The property under test is
    # structural: evaluate()'s body contains no branch and no comparison, so
    # its result provably cannot depend on its arguments. A gate that cannot
    # branch cannot be talked into a different answer by a better-looking input.
    import ast

    tree = ast.parse(
        (BACKEND_ROOT / "app" / "tefca_registry" / "ai" / "human_gate.py")
        .read_text(encoding="utf-8"))
    evaluate = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "evaluate")

    branching = [n for n in ast.walk(evaluate)
                 if isinstance(n, (ast.If, ast.IfExp, ast.Compare, ast.Match))]
    assert not branching, (
        "TEFCAHumanGate.evaluate must not branch — human review is an "
        "invariant, not a decision")

    # And no configuration lookup that a deploy could flip.
    names = {n.id for n in ast.walk(evaluate) if isinstance(n, ast.Name)}
    assert not (names & {"os", "settings", "environ"})


# ── Orchestrator ─────────────────────────────────────────────────────────────

async def test_full_pipeline_returns_needs_review():
    result = await _orchestrator().resolve_entity(
        entity_name="Mercy Health LLC", entity_address="1 Main St",
        entity_npi="1234567890", entity_type="organization",
        registry_name="Mercy Health Inc", registry_address="1 Main Street",
        registry_npi="1234567890", registry_type="organization",
        evidence_signals={"npi_exact_match": True, "name_similarity": 0.97},
    )

    assert result.status == "needs_review"
    assert result.human_review_required is True
    assert result.validation_passed is True
    assert result.text == VALID_OUTPUT
    assert result.prompt_version == "entity-match-v1.2"
    assert result.evidence_quality_score == pytest.approx(0.45)  # NPI 0.30 + name 0.15
    # Logged, never used to decide.
    assert result.ai_raw_confidence == 0.91


async def test_ai_unavailable_returns_deterministic():
    result = await _orchestrator(gateway=_StubGateway(None)).resolve_entity(
        entity_name="A", registry_name="B",
        evidence_signals={"npi_exact_match": True},
    )

    assert result.status == "ai_unavailable"
    assert result.fallback == "deterministic"
    assert result.text is None
    # Review is still required, and the deterministic evidence still scores.
    assert result.human_review_required is True
    assert result.evidence_quality_score == pytest.approx(0.30)


async def test_policy_denied_returns_denied(tmp_path, monkeypatch):
    """A broken policy denies the call before a provider is contacted."""
    monkeypatch.setenv("TEFCA_AI_POLICY_PATH", str(tmp_path / "gone.yaml"))
    gateway = _StubGateway(_response())
    result = await _orchestrator(gateway=gateway,
                                 policy=TEFCAPolicyEngine()).resolve_entity(
        entity_name="A", registry_name="B")

    assert result.status == "denied"
    assert result.human_review_required is True
    assert gateway.calls == []  # nothing was dispatched


async def test_invalid_output_is_withheld_from_the_reviewer():
    """Failed validation must not surface text. Unvalidated output sitting in a
    review queue reads as evidence regardless of any flag beside it."""
    bad = _response(json.dumps({"match": True, "confidence": 0.9,
                                "rationale": "I can confirm this is the same entity."}))
    result = await _orchestrator(gateway=_StubGateway(bad)).resolve_entity(
        entity_name="A", registry_name="B")

    assert result.status == "needs_review"
    assert result.validation_passed is False
    assert result.text is None
    assert any("Prohibited assertion" in e for e in result.validation_errors)


async def test_only_public_fields_leave_the_system():
    """PHI handed to the orchestrator never reaches the payload, and the
    reported fields_sent reflects the payload rather than the allowlist."""
    gateway = _StubGateway(_response())
    orchestrator = _orchestrator(gateway=gateway)
    # ssn is not an accepted parameter at all; the strongest form of the
    # guarantee is that there is no argument through which it could arrive.
    with pytest.raises(TypeError):
        await orchestrator.resolve_entity(entity_name="A", ssn="123-45-6789")

    await orchestrator.resolve_entity(
        entity_name="Mercy Health", entity_address="1 Main St",
        entity_npi="1234567890", entity_type="organization",
        registry_name="Mercy Health Inc")

    _, prompt, context = gateway.calls[0]
    assert set(context["fields_sent"]) <= set(TEFCAPolicyEngine().get_public_fields())
    assert "123-45-6789" not in prompt
    # fields_sent is derived from what survived filtering: the registry record
    # supplied only a name, so only fields actually present are reported.
    assert context["registry"] == {"name": "Mercy Health Inc"}


async def test_audit_log_written():
    orchestrator = _orchestrator()
    result = await orchestrator.resolve_entity(
        entity_name="Mercy Health", entity_npi="1234567890",
        registry_name="Mercy Health Inc", registry_npi="1234567890",
        evidence_signals={"npi_exact_match": True})

    assert len(result.audit_records) == 1
    row = result.audit_records[0]

    assert row["event"] == "ai_entity_resolution"
    assert row["policy_decision"] == "needs_review"
    assert row["human_review_required"] is True
    assert row["prompt_version"] == "entity-match-v1.2"
    assert row["provider"] == "claude"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["validation_passed"] is True
    assert row["evidence_quality_score"] == pytest.approx(0.30)
    assert row["ai_raw_confidence"] == 0.91
    assert row["latency_ms"] == pytest.approx(42.0)
    # A SHA-256 of the payload, not the payload — reproducible proof of what was
    # sent, without becoming a second copy of the data to govern.
    assert len(row["input_hash"]) == 64
    assert "Mercy Health" not in json.dumps(row["input_hash"])


async def test_denials_are_audited_too(tmp_path, monkeypatch):
    """A refusal is exactly the event worth seeing. A trail of only successful
    calls hides the interesting half."""
    monkeypatch.setenv("TEFCA_AI_POLICY_PATH", str(tmp_path / "gone.yaml"))
    result = await _orchestrator(policy=TEFCAPolicyEngine()).resolve_entity(
        entity_name="A", registry_name="B")

    assert len(result.audit_records) == 1
    assert result.audit_records[0]["policy_decision"] == "denied"
    assert result.audit_records[0]["human_review_required"] is True


async def test_audit_never_raises_when_the_write_fails(monkeypatch):
    """A lost audit row is recoverable; a 500 during entity verification is not."""
    from app.tefca_registry.ai import audit_logger as mod

    class _ExplodingSession:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("database gone")

    def boom(*_args, **_kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr("app.tefca_registry.audit.record", boom)
    logger = mod.TEFCAAIAuditLogger(session=_ExplodingSession())
    detail = await logger.log(policy_decision="needs_review")

    assert detail["policy_decision"] == "needs_review"
    assert len(logger.records) == 1


# ── Prompt registry ──────────────────────────────────────────────────────────

def test_prompt_is_versioned_and_renders_absent_fields_visibly():
    prompt = TEFCAPromptRegistry.get("entity_match")
    assert prompt.version == "entity-match-v1.2"
    assert "VERSION: entity-match-v1.2" in prompt.template

    rendered = prompt.render({"submitted": {"name": "A"}, "registry": {"name": "B"}})
    # An absent NPI must read as an absence, not as the string "None", which a
    # model could mistake for a value.
    assert "NPI: (not provided)" in rendered
    assert "None" not in rendered

    with pytest.raises(KeyError):
        TEFCAPromptRegistry.get("some_unregistered_task")


# ── Agent boundary ───────────────────────────────────────────────────────────

def test_allowed_capability_passes():
    boundary = TEFCAAgentBoundary()
    for action in ("read_nppes", "read_oig_leie", "compare_structured_records",
                   "prepare_evidence_package", "recommend_next_action"):
        assert boundary.check_capability(action) is True


def test_prohibited_capability_raises():
    """Raises rather than returning False: a prohibited action is a bug or an
    escalation attempt, and both need a stack trace, not a value to ignore."""
    boundary = TEFCAAgentBoundary()
    for action in ("approve_entity", "bypass_human_review", "delete_records",
                   "change_policies", "change_prompts", "modify_workflow_state"):
        with pytest.raises(AgentBoundaryViolation):
            boundary.check_capability(action)

    # Unknown is simply unapproved — default-deny already covers it.
    assert boundary.check_capability("some_future_capability") is False


def test_agent_boundary_lists_do_not_overlap():
    assert not (TEFCAAgentBoundary.ALLOWED_CAPABILITIES
                & TEFCAAgentBoundary.PROHIBITED_CAPABILITIES)


# ── CI/CD enforcement ────────────────────────────────────────────────────────

def test_no_direct_llm_calls_in_tefca():
    """Only app/tefca_registry/ai/gateway.py may call a provider SDK.

    Stops a bypass being reintroduced later. Scanning source text rather than
    imports is deliberate: it catches a call reached through a lazy import or an
    aliased module, which an import-graph check would miss.

    SCOPE: app/tefca_registry/ and app/Tefca/ only.
    app/bulletin_intelligence/ is explicitly NOT scanned — the bulletin module
    keeps its existing direct calls and is out of scope for TEFCA governance.
    """
    gateway = BACKEND_ROOT / "app" / "tefca_registry" / "ai" / "gateway.py"
    assert gateway.is_file(), "the one permitted provider call site is missing"

    forbidden = ("messages.create", "chat.completions.create")
    offenders = []

    for root in ("app/tefca_registry", "app/Tefca"):
        for py in (BACKEND_ROOT / root).rglob("*.py"):
            if py.resolve() == gateway.resolve():
                continue
            if "__pycache__" in py.parts:
                continue
            source = py.read_text(encoding="utf-8", errors="ignore")
            for marker in forbidden:
                if marker in source:
                    offenders.append(f"{py.relative_to(BACKEND_ROOT)}: {marker}")

    assert not offenders, (
        "Direct LLM calls found in TEFCA code outside gateway.py. All TEFCA AI "
        "must route through TEFCAAIOrchestrator:\n  " + "\n  ".join(offenders))


def test_bulletin_module_is_untouched_by_tefca_governance():
    """The companion assertion to the scan above: bulletin still holds direct
    calls, confirming the scan's scope is a real boundary and not vacuous."""
    bulletin = BACKEND_ROOT / "app" / "bulletin_intelligence" / "engine.py"
    if not bulletin.is_file():
        pytest.skip("bulletin engine not present in this checkout")
    assert "messages.create" in bulletin.read_text(encoding="utf-8", errors="ignore")


def test_ai_remains_disabled_by_default(monkeypatch):
    """Nothing in this work turns AI on. The default is still 'disabled', and
    an unrecognised value still disables rather than guessing."""
    from app.tefca_registry import entity_resolver as er

    monkeypatch.delenv("AI_ENTITY_RESOLUTION", raising=False)
    assert er.resolution_mode() == er.MODE_DISABLED

    monkeypatch.setenv("AI_ENTITY_RESOLUTION", "enabled_please")
    assert er.resolution_mode() == er.MODE_DISABLED


def test_deprecated_ai_client_cannot_reach_a_provider(monkeypatch):
    """The retired adapter keeps its names but not its capability, so a caller
    still wired to it takes the deterministic path rather than a private route
    to a provider."""
    import app.tefca_registry.ai_client as ac

    monkeypatch.setenv("AI_ENTITY_RESOLUTION", "advisory")
    monkeypatch.setattr(ac, "ANTHROPIC_API_KEY", "sk-test")
    assert ac.build_ai_client() is None

    with pytest.raises(NotImplementedError):
        ac.AnthropicClient(api_key="sk-test").complete(
            model="m", system="s", prompt="p")


async def test_resolver_consults_ai_only_for_inconclusive_pairs():
    """Deterministic-first is preserved: a pair settled by NPI never reaches the
    control plane at all."""
    from app.tefca_registry.entity_resolver import EntityResolver, MODE_ADVISORY

    class _Normalizer:
        def compare(self, a, b):
            from dataclasses import dataclass

            @dataclass
            class _M:
                is_match: bool = False
                confidence: float = 0.0
            return _M()

    class _CountingOrchestrator:
        def __init__(self):
            self.calls = 0

        async def resolve_entity(self, **_kwargs):
            self.calls += 1
            from app.tefca_registry.ai.orchestrator import OrchestratorResult
            return OrchestratorResult(status="ai_unavailable", fallback="deterministic")

    orchestrator = _CountingOrchestrator()
    resolver = EntityResolver(normalizer=_Normalizer(), mode=MODE_ADVISORY)

    decisive = await resolver.resolve_with_orchestrator(
        {"name": "A", "npi": "1234567890"}, {"name": "B", "npi": "1234567890"},
        orchestrator=orchestrator)
    assert decisive.method == "identifier"
    assert orchestrator.calls == 0

    # Names close enough to be plausible, addresses that do not normalize:
    # genuinely inconclusive, and the only case that consults AI.
    await resolver.resolve_with_orchestrator(
        {"name": "Mercy Health System"}, {"name": "Mercy Health Systems"},
        orchestrator=orchestrator)
    assert orchestrator.calls == 1


async def test_resolver_survives_an_exploding_control_plane():
    """AI failure is a degraded capability, never degraded verification."""
    from app.tefca_registry.entity_resolver import EntityResolver, MODE_ADVISORY

    class _Normalizer:
        def compare(self, a, b):
            from dataclasses import dataclass

            @dataclass
            class _M:
                is_match: bool = False
                confidence: float = 0.0
            return _M()

    class _Exploding:
        async def resolve_entity(self, **_kwargs):
            raise RuntimeError("control plane is down")

    result = await EntityResolver(
        normalizer=_Normalizer(), mode=MODE_ADVISORY
    ).resolve_with_orchestrator(
        {"name": "Mercy Health System"}, {"name": "Mercy Health Systems"},
        orchestrator=_Exploding())

    assert result.method == "inconclusive"
    assert result.requires_manual_review is True
    assert result.threshold_applied == "orchestrator_error"
