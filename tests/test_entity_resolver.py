"""Entity resolver tests (6).

The load-bearing guarantees here are governance ones, not accuracy ones: the
system must work with AI switched off, AI must never decide on its own, only
public data may leave, and every call must be audit-logged.
"""
import pytest

from app.tefca_registry import entity_resolver as er
from app.tefca_registry.entity_resolver import EntityResolver, ResolutionResult


class FakeAI:
    """Records what it was asked, returns a scripted answer."""

    def __init__(self, response='{"is_match": true, "confidence": 0.97, "reasoning": "same org"}',
                 raises=False):
        self.response = response
        self.raises = raises
        self.calls = []

    def complete(self, *, model, system, prompt):
        self.calls.append({"model": model, "system": system, "prompt": prompt})
        if self.raises:
            raise RuntimeError("upstream unavailable")
        return self.response


A = {"name": "Mercy Health System, Inc.", "address": "123 N Main St, Springfield, IL 62704",
     "npi": "", "entity_type": "provider"}
B = {"name": "Mercy Health System LLC", "address": "123 North Main Street, Springfield, IL 62704",
     "npi": "", "entity_type": "provider"}


def test_disabled_by_default_never_calls_ai(monkeypatch):
    """The system must be fully functional with AI off — that is the default."""
    monkeypatch.delenv("AI_ENTITY_RESOLUTION", raising=False)
    assert er.resolution_mode() == er.MODE_DISABLED

    ai = FakeAI()
    r = EntityResolver(ai_client=ai).resolve(
        {"name": "Alpha Clinic", "address": "1 A St, Springfield, IL 62704"},
        {"name": "Beta Hospital", "address": "999 Z Blvd, Chicago, IL 60601"})
    assert isinstance(r, ResolutionResult)
    assert ai.calls == [], "AI must not be consulted while disabled"

    # An unrecognized value must fail closed, not silently enable AI.
    monkeypatch.setenv("AI_ENTITY_RESOLUTION", "enabled")
    assert er.resolution_mode() == er.MODE_DISABLED


def test_identifier_match_is_decisive_without_ai():
    ai = FakeAI()
    resolver = EntityResolver(ai_client=ai, mode=er.MODE_PRODUCTION)

    same = resolver.resolve({**A, "npi": "1770626038"}, {**B, "npi": "1770626038"})
    assert same.is_match is True
    assert same.method == "identifier"
    assert same.requires_manual_review is False

    diff = resolver.resolve({**A, "npi": "1770626038"}, {**B, "npi": "1999999999"})
    assert diff.is_match is False
    assert ai.calls == [], "a decisive identifier must short-circuit before AI"


def test_deterministic_agreement_resolves_without_ai():
    """Formatting-only differences must be settled for free."""
    ai = FakeAI()
    r = EntityResolver(ai_client=ai, mode=er.MODE_PRODUCTION).resolve(A, B)
    assert r.is_match is True
    assert r.method == "address+name"
    assert r.requires_manual_review is False
    assert ai.calls == [], "deterministic agreement must not spend an AI call"


def test_ai_only_advises_and_never_decides_in_advisory_mode():
    ai = FakeAI()
    resolver = EntityResolver(ai_client=ai, mode=er.MODE_ADVISORY)
    # Names agree strongly, addresses do not — the inconclusive path.
    r = resolver.resolve(
        {"name": "Mercy Health System", "address": "123 Main St, Springfield, IL 62704"},
        {"name": "Mercy Health Systems", "address": "77 Far Away Rd, Chicago, IL 60601"})

    assert len(ai.calls) == 1
    assert r.ai_consulted is True
    assert r.is_match is None, "advisory mode must never set a verdict"
    assert r.requires_manual_review is True, "a human is always the decision of record"


def test_low_confidence_recommendation_is_discarded():
    """Below 0.70 the recommendation must not reach a reviewer as evidence."""
    ai = FakeAI('{"is_match": true, "confidence": 0.42, "reasoning": "maybe"}')
    resolver = EntityResolver(ai_client=ai, mode=er.MODE_PRODUCTION)
    r = resolver.resolve(
        {"name": "Mercy Health System", "address": "123 Main St, Springfield, IL 62704"},
        {"name": "Mercy Health Systems", "address": "77 Far Away Rd, Chicago, IL 60601"})

    assert r.threshold_applied == "ignored_below_threshold"
    assert r.method != "ai", "a discarded recommendation must not become the result"
    assert r.requires_manual_review is True
    assert resolver.audit_records[-1]["confidence"] == pytest.approx(0.42)


def test_audit_record_is_complete_and_carries_only_public_data():
    ai = FakeAI()
    resolver = EntityResolver(ai_client=ai, mode=er.MODE_PRODUCTION)
    resolver.resolve(
        {"name": "Mercy Health System", "address": "123 Main St, Springfield, IL 62704",
         "ssn": "123-45-6789", "patient_mrn": "MRN-0001", "diagnosis": "E11.9"},
        {"name": "Mercy Health Systems", "address": "77 Far Away Rd, Chicago, IL 60601"})

    rec = resolver.audit_records[-1]
    for key in ("model_id", "prompt_version", "input", "output", "confidence",
                "threshold_applied", "timestamp", "latency_ms", "software_version"):
        assert key in rec, f"audit record missing {key}"

    # PHI must never be sent, and must never appear in the audit trail either.
    blob = str(rec["input"]) + ai.calls[0]["prompt"]
    for leaked in ("123-45-6789", "MRN-0001", "E11.9", "ssn", "patient_mrn", "diagnosis"):
        assert leaked not in blob, f"non-public field leaked: {leaked}"
    assert "Mercy Health System" in blob


def test_ai_failure_falls_back_to_deterministic_result():
    """An AI outage must never break the verification pipeline."""
    ai = FakeAI(raises=True)
    resolver = EntityResolver(ai_client=ai, mode=er.MODE_PRODUCTION)
    r = resolver.resolve(
        {"name": "Mercy Health System", "address": "123 Main St, Springfield, IL 62704"},
        {"name": "Mercy Health Systems", "address": "77 Far Away Rd, Chicago, IL 60601"})

    assert r.requires_manual_review is True
    assert resolver.audit_records[-1]["error"].startswith("RuntimeError")


def test_name_matching_ignores_legal_form_suffixes():
    assert er.compare_names("Mercy Health LLC", "Mercy Health Inc.") > 0.95
    assert er.compare_names("Mercy Health System", "Saint Jude Hospital") < 0.70
    assert er.compare_names(None, "Anything") == 0.0
    assert er.jaro_winkler("identical", "identical") == 1.0
