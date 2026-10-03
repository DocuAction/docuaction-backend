"""Proves, executably, the SAM.gov asymmetry between the two real callers of
`BucketClassifier.classify()`:

  * `arc_pipeline.verify_and_classify` (bulk/RCE path) DOES query SAM.gov
    (`connectors.query_all_sources` -> `SAMGovConnector.verify()`), and a
    confirmed-or-pending exclusion disqualifies the entity (SEED_RULES_V3).
  * `review_service.probe_sources` (manual single-entity path) NEVER queries
    SAM.gov at all -- `SAM_GOV` is seeded into the `NO_CONNECTOR` dict and the
    connector loop only iterates `("nppes", "pecos", "leie")`
    (`review_service.py:128`), so `sources["sam_gov"]` is always the same
    static `NOT_CHECKED` stub, regardless of what a real SAM check would have
    found.

This means: an entity that IS excluded/debarred in SAM.gov, run through the
MANUAL review path today, is classified with ZERO SAM signal -- not
"unavailable" (a source that tried and failed), but a permanent, disclosed
"not_checked: API key configured... under investigation" that never changes
no matter the ground truth. This file does not fix the asymmetry (that is a
real behavior change requiring its own authorization) -- it proves the
asymmetry exists, reproducibly, so it cannot be mistaken for a theoretical
concern.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _FakeResult:
    """Mimics SourceResult: success means the query completed, not the answer."""
    def __init__(self, success=True, data=None, error=None):
        self.success, self.data, self.error = success, data, error


def _valid_npi() -> str:
    """A structurally valid (Luhn-passing) NPI, so probe_sources' own
    pre-connector validity gate does not short-circuit every source to
    not_checked before the SAM question is even reached."""
    from app.services.npi_validator import CMS_PREFIX, _luhn_total

    base = "120588014"
    for d in range(10):
        candidate = base + str(d)
        if _luhn_total(CMS_PREFIX + candidate) % 10 == 0:
            return candidate
    raise AssertionError("no valid check digit found — fixture bug")


class _FakeDB:
    """Enough of an AsyncSession for probe_sources: the only query it issues
    before the connector loop is 'does this entity have an NPI identifier'."""
    def __init__(self, npi: str):
        self._npi = npi

    async def execute(self, *_a, **_k):
        class R:
            def __init__(self, npi):
                self._npi = npi

            def scalar_one_or_none(self):
                return self._npi
        return R(self._npi)


def _clean_nppes_pecos_leie_mgr():
    """A connector manager whose nppes/pecos/leie all report a clean,
    positive result -- isolates the test to the SAM effect, exactly like the
    existing e2e test's `_clean_nppes_leie` helper."""

    class _Conn:
        async def lookup_by_npi(self, npi):
            return _FakeResult(success=True, data={
                "found": True, "excluded": False, "legal_name": "Test Org",
                "enumeration_type": "NPI-2", "status": "A",
            })

    class _Mgr:
        nppes = pecos = leie = _Conn()
        # A real SAM-aware manager WOULD also expose `.sam_gov` here -- but
        # review_service.probe_sources's connector loop
        # (`for key, attr in (("nppes","nppes"),("pecos","pecos"),
        # ("oig_leie","leie"))`) never looks it up, so even attaching one
        # here (see below) proves it is never consulted.
        sam_gov = _Conn()

    return _Mgr()


async def test_manual_review_never_queries_sam_even_when_a_connector_exists(monkeypatch):
    """The core proof. Attach a SAM connector to the manager that WOULD report
    a debarment if it were ever called -- probe_sources must still return the
    static not_checked stub, never touching it."""
    from app.tefca_registry import review_service as svc
    import app.Tefca.connectors as conns

    calls = {"sam_gov": 0}

    class _SamThatWouldFlag:
        async def verify(self, uei="", legal_name=""):
            # If this is ever called, the test below would fail on the call
            # count assertion -- this method intentionally reports the worst
            # possible real-world finding, to prove the asymmetry is not an
            # artifact of a lenient mock.
            calls["sam_gov"] += 1
            return _FakeResult(success=True, data={
                "excluded": True, "debarred": True, "matched_by": "uei",
                "identity_ambiguous": False,
            })

    mgr = _clean_nppes_pecos_leie_mgr()
    mgr.sam_gov = _SamThatWouldFlag()
    monkeypatch.setattr(conns, "SourceConnectorManager", lambda: mgr)

    db = _FakeDB(npi=_valid_npi())
    sources = await svc.probe_sources(db, "entity-would-be-excluded")

    assert calls["sam_gov"] == 0, (
        "probe_sources invoked the SAM connector -- the asymmetry this test "
        "guards against has been fixed; update this test's purpose, do not "
        "just relax this assertion")
    assert sources["sam_gov"]["status"] == svc.NOT_CHECKED
    assert "sam_gov" not in ("nppes", "pecos", "oig_leie")  # sanity: distinct key
    # The real NPPES/PECOS/LEIE results DID come through -- proves the gap is
    # specific to sam_gov, not a general connector failure.
    assert sources["nppes"]["status"] in (svc.VERIFIED, "clear")
    assert sources["oig_leie"]["status"] == "clear"


async def test_classification_on_the_manual_path_is_unaffected_by_what_sam_would_say(monkeypatch):
    """Feeds probe_sources' output into the REAL v3 classifier (the same one
    the RCE/bulk path uses) and shows the result is identical whether SAM
    would have said "debarred" or said nothing at all -- because the manual
    path never lets the classifier see a SAM answer either way."""
    from app.tefca_registry import review_service as svc
    from app.tefca_registry.bucket_classifier import BucketClassifier, SEED_RULES_V3
    import app.Tefca.connectors as conns

    classifier = BucketClassifier(rules=SEED_RULES_V3)

    async def _classify_with_sam_mock(sam_would_flag: bool):
        mgr = _clean_nppes_pecos_leie_mgr()

        class _Sam:
            async def verify(self, uei="", legal_name=""):
                if sam_would_flag:
                    return _FakeResult(success=True, data={"excluded": True, "debarred": True})
                return _FakeResult(success=True, data={"excluded": False, "debarred": False})
        mgr.sam_gov = _Sam()
        monkeypatch.setattr(conns, "SourceConnectorManager", lambda: mgr)

        db = _FakeDB(npi=_valid_npi())
        sources = await svc.probe_sources(db, "entity")
        results = {"sources": sources, "fields": {}, "confidence_score": None}
        return classifier.classify(results)

    result_if_sam_would_flag = await _classify_with_sam_mock(sam_would_flag=True)
    result_if_sam_silent = await _classify_with_sam_mock(sam_would_flag=False)

    assert result_if_sam_would_flag.bucket == result_if_sam_silent.bucket, (
        "Expected IDENTICAL classification regardless of the SAM mock's "
        "answer -- this is the asymmetry: the manual path's bucket cannot "
        "depend on SAM at all today, because SAM is never actually queried.")
    assert result_if_sam_would_flag.rule_code != "RULE-005", (
        "A confirmed debarment did NOT disqualify this entity on the manual "
        "path -- exactly the gap this test documents, not fixed here.")
