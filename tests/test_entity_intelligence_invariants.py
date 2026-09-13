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
        keys = {"line1", "line2", "street", "city", "state", "zip5", "zip_status", "country", "normalization_version"}
        junk_values = [None, 0, -7, 12345, 1.5, True, b"bytes", ["x"], {"y": 1}, ("z",), object()]
        for _ in range(500):
            junk = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
            n = normalize_name(junk)
            assert isinstance(n, str) and n == normalize_name(n)          # text, idempotent
            fields = {k: rng.choice([junk, rng.choice(junk_values)]) for k in ("line1", "line2", "city", "state", "postal_code", "country_code")}
            a = normalize_address(fields)
            assert set(a) == keys and all(isinstance(v, str) for v in a.values())
            assert a["zip5"] == "" or (len(a["zip5"]) == 5 and a["zip5"].isascii() and a["zip5"].isdigit())
            assert a["state"] == "" or (len(a["state"]) == 2 and a["state"].isalpha())
            assert normalize_address(fields) == a                          # deterministic
        for bad in junk_values:
            assert isinstance(normalize_name(bad), str) and (isinstance(bad, int) or normalize_name(bad) == "")
            assert isinstance(normalize_address(bad), dict)  # type: ignore[arg-type]

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


class TestGoverningInvariants:
    """Executable form of governing invariants that were PROBE-only in
    docs/governance/DOCUACTION_INVARIANT_MAP.md (AUD-20). Numbering follows the
    map; nothing here redefines an invariant."""

    ADDR = {"line1": "100 SYNTHETIC WAY", "line2": "STE 200", "city": "BALTIMORE", "state": "MD", "postal_code": "212010000"}

    @staticmethod
    def _src(name, addr, *, source=NPPES, path=DeliveryPath.FILE_DOWNLOAD, method=None, authority=SourceAuthority.FEDERAL_REGISTRY,
             kind=NameKind.LEGAL_BUSINESS_NAME, role=LocationRole.PRIMARY_PRACTICE_LOCATION, npi="9999900001"):
        from app.core.evidence_provenance import RetrievalMethod, SourceVersionRef
        method = method or RetrievalMethod.DOWNLOAD
        prov = Provenance(source_owner="synthetic", delivery_path=path,
                          source_version=SourceVersionRef(source=source, dataset_version="v", retrieval_method=method,
                                                          retrieved_at="2026-09-13T00:00:00Z"))
        common = dict(canonical_entity_id="ent-1", source_id=source, source_authority=authority, provenance=prov)
        out = []
        if npi:
            out.append(EvidenceObservation(observation_type=ObservationType.IDENTIFIER, role="NPI",
                                           observed_value={"value": npi, "entity_type": "2"}, **common))
        if name:
            out.append(EvidenceObservation(observation_type=ObservationType.NAME, role=kind.value, observed_value={"name": name}, **common))
        if addr:
            out.append(EvidenceObservation(observation_type=ObservationType.LOCATION, role=role.value, observed_value=dict(addr),
                                           normalized_value=normalize_address(addr), **common))
        return out

    def _assess(self, cur, sources):
        comps = []
        for s in sources:
            comps += compare_all(cur, source_id=s)
        return assess(comps, []), comps

    def test_I31_acquisition_path_does_not_change_interpretation(self):
        from app.core.evidence_provenance import RetrievalMethod
        variants = {}
        for label, (path, method) in {"file": (DeliveryPath.FILE_DOWNLOAD, RetrievalMethod.DOWNLOAD),
                                      "api": (DeliveryPath.DIRECT_QUERY, RetrievalMethod.API),
                                      "third_party": (DeliveryPath.THIRD_PARTY_DELIVERY, RetrievalMethod.DOWNLOAD),
                                      "upload": (DeliveryPath.OPERATOR_UPLOAD, RetrievalMethod.DOWNLOAD)}.items():
            cur = delivered("S CLINIC", self.ADDR) + self._src("S HEALTHCARE LLC", self.ADDR, path=path, method=method) + \
                  self._src("S CLINIC", None, path=path, method=method, kind=NameKind.DOING_BUSINESS_AS)
            res, comps = self._assess(cur, [NPPES])
            variants[label] = (res.assessment, tuple(c.signal for c in comps), tuple(res.basis))
        assert len(set(variants.values())) == 1, variants

    def test_I33_multiple_agreeing_sources_are_corroboration_not_truth(self):
        cur = delivered("S LLC", self.ADDR)
        for i in range(4):
            cur += self._src("S LLC", self.ADDR, source=f"AGREE_{i}")
        res, _ = self._assess(cur, [f"AGREE_{i}" for i in range(4)])
        assert res.assessment is SystemEvidenceAssessment.EVIDENCE_CORROBORATES
        vocabulary = {a.value for a in SystemEvidenceAssessment}
        for truth_term in ("TRUE", "VERIFIED", "CONFIRMED", "VALID", "PROVEN", "ESTABLISHED"):
            assert not any(truth_term in v for v in vocabulary)
        assert res.requires_human_review

    def test_I34_source_disagreement_is_conflicting_evidence_not_a_contractual_discrepancy(self):
        cur = delivered("S LLC", self.ADDR) + self._src("OTHER LLC", {"line1": "55 TEST CLINIC RD", "city": "FREDERICK", "state": "MD", "postal_code": "21701"})
        res, _ = self._assess(cur, [NPPES])
        assert res.assessment is SystemEvidenceAssessment.CONFLICTING_EVIDENCE
        text = str(res.to_dict()).upper()
        for category_term in ("DISCREPANC", "NON-COMPLIANT", "NON_COMPLIANT", "INEXPLICABLE", "MINOR ADMINISTRATIVE", "FINDING"):
            assert category_term not in text, category_term

    def test_I36_automated_corroboration_is_an_assessment_not_a_determination(self):
        import inspect
        import re
        from app.core.entity_intelligence import service as service_module
        cur = delivered("S LLC", self.ADDR) + self._src("S LLC", self.ADDR)
        res, _ = self._assess(cur, [NPPES])
        assert res.assessment is SystemEvidenceAssessment.EVIDENCE_CORROBORATES
        d = res.to_dict()
        assert not any("determin" in k.lower() for k in d), list(d)
        source = inspect.getsource(service_module)
        assert not re.search(r"(determination|review_decision|qa_decision|category)\s*=\s*[^=]", source)
        assert "System evidence assessment only" in source

    def test_13_agreeing_weak_or_duplicate_sources_never_outvote_a_conflicting_source(self):
        # four agreeing sources plus the same four repeated (duplicates) vs one conflicting source
        cur = delivered("S LLC", self.ADDR)
        weak = [f"WEAK_{i}" for i in range(4)]
        for s in weak:
            cur += self._src("S LLC", self.ADDR, source=s, authority=SourceAuthority.SUPPLEMENTAL)
            cur += self._src("S LLC", self.ADDR, source=s, authority=SourceAuthority.SUPPLEMENTAL)   # duplicate rows
        cur += self._src("OTHER LLC", self.ADDR, source="AUTHORITATIVE", authority=SourceAuthority.FEDERAL_REGISTRY)
        res, comps = self._assess(cur, weak + weak + ["AUTHORITATIVE"])     # even asking twice per source
        assert res.assessment is SystemEvidenceAssessment.CONFLICTING_EVIDENCE
        assert sum(1 for c in comps if c.signal in CORROBORATING_SIGNALS) >= 4     # agreement recorded, never counted
        assert sum(1 for c in comps if c.signal in CONFLICT_SIGNALS) >= 1

    def test_14_no_floating_point_or_pseudo_precision_confidence_anywhere(self):
        import json
        import re
        cur = delivered("S LLC", self.ADDR) + self._src("S LLC", self.ADDR) + self._src("S LLC", self.ADDR, source="B")
        res, comps = self._assess(cur, [NPPES, "B"])
        blob = json.dumps({"assessment": res.to_dict(), "comparisons": [c.to_dict() for c in comps]}).lower()
        for term in ("confidence", "score", "probability", "likelihood", "weight", "%"):
            assert term not in blob, term
        def _floats(node):
            if isinstance(node, float):
                yield node
            elif isinstance(node, dict):
                for v in node.values():
                    yield from _floats(v)
            elif isinstance(node, list):
                for v in node:
                    yield from _floats(v)
        assert list(_floats(res.to_dict())) == [] and all(list(_floats(c.to_dict())) == [] for c in comps)
        assert not re.search(r"\b0\.\d+\b|\b\d{1,3}(\.\d+)?\s?%", blob)
