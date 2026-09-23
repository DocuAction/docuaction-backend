"""Property-style invariants without hypothesis (not installed, and adding a
dependency tonight is out of scope). A seeded random generator produces a few
hundred synthetic cases per invariant; failures print the seed and the case."""
from __future__ import annotations

import random
import string

import pytest

from app.core.entity_intelligence.assessment import SystemEvidenceAssessment, assess
from app.core.entity_intelligence.comparison import (CONFLICT_SIGNALS, CORROBORATING_SIGNALS, Dimension,
                                                      compare_all)
from app.core.entity_intelligence.delta import DeltaType, compute_deltas, explain_deltas
from app.core.entity_intelligence.normalize import normalize_address, normalize_name
from app.core.entity_intelligence.observations import (DeliveryPath, EvidenceObservation, LocationRole, NameKind,
                                                        ObservationType, Provenance, SourceAuthority)
from app.evidence_sources.nppes_v2.adapter import SOURCE_ID as NPPES
from ei_fixtures import delivered

SEED = 20260912
WORDS = ["SYNTHETIC", "HEALTH", "CLINIC", "MEDICAL", "GROUP", "CENTER", "CARE", "FAMILY", "REGIONAL", "WEST"]
SUFFIX = ["LLC", "INC", "CORP", "PLLC", "LTD", "L.L.C.", "Incorporated", "Corporation", ""]
PUNCT = [",", ".", "-", "&", "'", "\"", "  ", "\t"]


def rand_name(rng):
    n = rng.randint(1, 4)
    words = [rng.choice(WORDS) for _ in range(n)]
    suffix = rng.choice(SUFFIX)
    return " ".join(words + ([suffix] if suffix else []))


def reformat(rng, name):
    """Apply ONLY formatting changes: case, punctuation, whitespace, suffix spelling."""
    out = []
    for ch in name:
        if ch == " " and rng.random() < 0.3:
            out.append(rng.choice(["  ", " , ", " . ", "\t"]))
        elif ch.isalpha() and rng.random() < 0.5:
            out.append(ch.swapcase())
        else:
            out.append(ch)
    text = "".join(out)
    for a, b in (("LLC", "L.L.C."), ("INC", "Inc."), ("CORP", "Corporation")):
        if text.upper().endswith(a) and rng.random() < 0.5:
            text = text[: -len(a)] + b
    return text


def rand_addr(rng):
    return {"line1": f"{rng.randint(1, 9999)} {rng.choice(WORDS)} {rng.choice(['ST', 'AVE', 'RD', 'PKWY'])}",
            "line2": rng.choice(["", f"STE {rng.randint(1, 900)}", f"FL {rng.randint(1, 20)}"]),
            "city": rng.choice(["BALTIMORE", "FREDERICK", "ROCKVILLE"]), "state": "MD",
            "postal_code": f"{rng.randint(20600, 21999)}{rng.choice(['', '0000', '1234'])}"}


def src(name=None, addr=None, npi="9999900001", source=NPPES, kind=NameKind.LEGAL_BUSINESS_NAME):
    prov = Provenance(source_owner="synthetic", delivery_path=DeliveryPath.FILE_DOWNLOAD)
    common = dict(canonical_entity_id="ent-1", source_id=source, source_authority=SourceAuthority.FEDERAL_REGISTRY,
                  provenance=prov)
    out = [EvidenceObservation(observation_type=ObservationType.IDENTIFIER, role="NPI",
                               observed_value={"value": npi}, **common)]
    if name:
        out.append(EvidenceObservation(observation_type=ObservationType.NAME, role=kind.value,
                                       observed_value={"name": name}, **common))
    if addr:
        out.append(EvidenceObservation(observation_type=ObservationType.LOCATION,
                                       role=LocationRole.PRIMARY_PRACTICE_LOCATION.value,
                                       observed_value=dict(addr), normalized_value=normalize_address(addr), **common))
    return out


@pytest.fixture(scope="module")
def rng():
    return random.Random(SEED)


class TestNormalizationInvariants:
    def test_idempotent(self, rng):
        for _ in range(500):
            n = rand_name(rng)
            assert normalize_name(normalize_name(n)) == normalize_name(n)

    def test_formatting_changes_are_equivalent(self, rng):
        for _ in range(500):
            n = rand_name(rng)
            assert normalize_name(n) == normalize_name(reformat(rng, n)), (n, reformat(rng, n))

    def test_word_change_is_never_equivalent(self, rng):
        for _ in range(500):
            n = rand_name(rng)
            words = n.split()
            i = rng.randrange(len(words))
            replacement = rng.choice([w for w in WORDS + ["FOUNDATION", "MOBILE"] if w != words[i].upper()])
            altered = " ".join(words[:i] + [replacement] + words[i + 1:])
            if normalize_name(altered) == normalize_name(n):
                # only legitimate when the replaced token was a suffix spelled differently
                assert words[i].upper().rstrip(".") in {"LLC", "L.L.C", "INC", "INCORPORATED", "CORP", "CORPORATION"}, (n, altered)

    def test_never_raises_on_garbage(self, rng):
        alphabet = string.printable + "éßÉ—“”​ "
        for _ in range(500):
            junk = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
            normalize_name(junk)
            normalize_address({"line1": junk, "line2": junk, "city": junk, "state": junk, "postal_code": junk})

    def test_zip5_always_five_or_fewer_digits(self, rng):
        for _ in range(300):
            z = normalize_address({"postal_code": "".join(rng.choice("0123456789- ") for _ in range(rng.randint(0, 12)))})["zip5"]
            assert len(z) <= 5 and z.isdigit() or z == ""


class TestEngineInvariants:
    def test_engine_never_raises_and_always_requires_review(self, rng):
        for _ in range(300):
            d_name, d_addr = rand_name(rng), rand_addr(rng)
            s_name = rng.choice([d_name, reformat(rng, d_name), rand_name(rng), None])
            s_addr = rng.choice([d_addr, rand_addr(rng), None])
            cur = delivered(d_name, d_addr if rng.random() < 0.9 else None) + src(s_name, s_addr)
            comps = compare_all(cur, source_id=NPPES, identifier_system="NPI")
            res = assess(comps, [])
            assert res.requires_human_review and isinstance(res.assessment, SystemEvidenceAssessment)
            assert all(c.explanation.endswith("Human review required.") for c in comps)

    def test_identical_inputs_identical_outputs(self, rng):
        for _ in range(100):
            n, a = rand_name(rng), rand_addr(rng)
            cur = delivered(n, a) + src(n, a)
            r1 = assess(compare_all(cur, source_id=NPPES, identifier_system="NPI"), [])
            r2 = assess(compare_all(cur, source_id=NPPES, identifier_system="NPI"), [])
            assert r1.assessment == r2.assessment and r1.basis == r2.basis

    def test_exact_agreement_always_corroborates(self, rng):
        for _ in range(200):
            n, a = rand_name(rng), rand_addr(rng)
            cur = delivered(n, a) + src(n, a)
            res = assess(compare_all(cur, source_id=NPPES, identifier_system="NPI"), [])
            assert res.assessment is SystemEvidenceAssessment.EVIDENCE_CORROBORATES, (n, a, res.basis)

    def test_any_conflict_dominates_regardless_of_agreeing_sources(self, rng):
        for _ in range(200):
            n, a = rand_name(rng), rand_addr(rng)
            k = rng.randint(1, 5)
            cur = delivered(n, a)
            srcs = [f"SRC_{i}" for i in range(k)]
            for sid in srcs:
                cur += src(n, a, source=sid)
            cur += src(n + " FOUNDATION", a, source="SRC_X")
            comps = []
            for sid in srcs + ["SRC_X"]:
                comps += compare_all(cur, source_id=sid, identifier_system="NPI")
            res = assess(comps, [])
            assert res.assessment is SystemEvidenceAssessment.CONFLICTING_EVIDENCE
            assert sum(1 for c in comps if c.signal in CORROBORATING_SIGNALS) >= k  # agreement recorded, not counted

    def test_source_order_does_not_change_assessment(self, rng):
        for _ in range(100):
            n, a = rand_name(rng), rand_addr(rng)
            cur = delivered(n, a) + src(n, a, source="SRC_A") + src(rand_name(rng), rand_addr(rng), source="SRC_B")
            c1 = compare_all(cur, source_id="SRC_A") + compare_all(cur, source_id="SRC_B")
            c2 = compare_all(cur, source_id="SRC_B") + compare_all(cur, source_id="SRC_A")
            assert assess(c1, []).assessment == assess(c2, []).assessment

    def test_delta_of_identical_lists_is_all_unchanged(self, rng):
        for _ in range(100):
            cur = delivered(rand_name(rng), rand_addr(rng))
            deltas = compute_deltas(cur, cur)
            assert deltas and all(d.delta_type is DeltaType.UNCHANGED for d in deltas)

    def test_delta_is_symmetric_in_count(self, rng):
        for _ in range(100):
            p, c = delivered(rand_name(rng), rand_addr(rng)), delivered(rand_name(rng), rand_addr(rng))
            assert len(compute_deltas(p, c)) == len(compute_deltas(c, p))

    def test_observation_hash_stable_and_sensitive(self, rng):
        for _ in range(100):
            n = rand_name(rng)
            a, b = delivered(n)[0], delivered(n)[0]
            assert a.content_hash == b.content_hash
            assert a.content_hash != delivered(n + " X")[0].content_hash
