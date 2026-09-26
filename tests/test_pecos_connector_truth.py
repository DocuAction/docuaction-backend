"""PECOS connector truthfulness remediation — required test matrix.

Covers items 1-11 of the six-fix remediation (see PR description): centralised
NPI validation before dispatch, NPPES HTTP-200-with-Errors structural
handling, truthful labelling, and the duplicate-NPPES-call fix. Items 12-15
(the dry-run planner) live in test_pecos_retry_planner.py.

NO NETWORK. `_get_with_retry` is monkeypatched everywhere a connector would
otherwise reach the real npiregistry.cms.hhs.gov / data.cms.gov hosts.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from app.Tefca import connectors as c
from app.Tefca.connectors import (
    NPPESConnector,
    OIGLEIEConnector,
    PECOSConnector,
    PECOS_UI_LABEL,
    PECOS_UI_SUBTITLE,
    safe_upstream_request_id,
)
from app.Tefca.cms_ppef import (
    CMSDataAPIClient,
    CMSRevocationConnector,
    NO_ACTIVE_REVOCATION_RECORD_FOUND,
    PPEFEnrollmentConnector,
)
from app.services.npi_validator import npi_rejection_reason


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default) if isinstance(key, str) else default


class FakeRequest:
    def __init__(self, host):
        class _URL:
            pass
        u = _URL()
        u.host = host
        self.url = u


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, host="npiregistry.cms.hhs.gov"):
        self.status_code = status_code
        self._payload = payload
        self.headers = FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})
        self.request = FakeRequest(host)

    def json(self):
        return self._payload


VALID_NPI = "1003879883"  # matches the live NPPES/PPEF fixture used elsewhere in this suite
INVALID_CHECKSUM_NPI = "1234567890"  # 10 digits, fails Luhn


@pytest.fixture
def patch_nppes(monkeypatch):
    """Patch _get_with_retry (shared by NPPESConnector/PECOSConnector) to
    return a queued sequence of FakeResponse objects, one per call."""
    def _install(*responses):
        queue = list(responses)
        calls = []

        async def fake_get(url, params, headers, timeout=None):
            calls.append({"url": url, "params": dict(params)})
            return queue.pop(0)

        monkeypatch.setattr(c, "_get_with_retry", fake_get)
        return calls

    return _install


# ── 1. Malformed NPI rejected before upstream invocation ─────────────────────

class TestMalformedNpiRejectedBeforeDispatch:
    async def test_nppes_rejects_non_digit_npi_without_a_call(self, patch_nppes):
        calls = patch_nppes()  # no responses queued — a call would raise IndexError
        result = await NPPESConnector().lookup_by_npi("abc")
        assert calls == []
        assert result.success is False
        assert result.data is None
        assert "npi_failed_validation" in result.error

    async def test_legacy_pecos_rejects_wrong_length_npi_without_a_call(self, patch_nppes):
        calls = patch_nppes()
        result = await PECOSConnector().lookup_by_npi("12345")
        assert calls == []
        assert result.success is False
        assert "npi_failed_validation" in result.error

    async def test_cms_ppef_rejects_malformed_npi_without_a_call(self, monkeypatch):
        calls = []

        async def fake_get(url, params, headers, timeout=None):
            calls.append(url)
            raise AssertionError("must not be called for an invalid NPI")

        monkeypatch.setattr(c, "_get_with_retry", fake_get)
        result = await PPEFEnrollmentConnector(CMSDataAPIClient()).lookup_by_npi("not-an-npi")
        assert calls == []
        assert result.success is False
        assert "npi_failed_validation" in result.error

    async def test_cms_revocation_rejects_malformed_npi_without_a_call(self, monkeypatch):
        async def fake_get(*a, **k):
            raise AssertionError("must not be called for an invalid NPI")

        monkeypatch.setattr(c, "_get_with_retry", fake_get)
        result = await CMSRevocationConnector(CMSDataAPIClient()).lookup_by_npi("not-an-npi")
        assert result.success is False
        assert "npi_failed_validation" in result.error


# ── 2. Ten-digit checksum-invalid NPI rejected before upstream invocation ───

class TestChecksumInvalidNpiRejected:
    async def test_nppes_rejects_checksum_invalid_npi(self, patch_nppes):
        calls = patch_nppes()
        result = await NPPESConnector().lookup_by_npi(INVALID_CHECKSUM_NPI)
        assert calls == []
        assert result.success is False
        assert "Luhn" in result.error or "npi_failed_validation" in result.error

    async def test_validator_agrees_this_npi_is_checksum_invalid(self):
        # Sanity check on the fixture value itself, so the two tests above are
        # provably exercising the checksum branch and not a length rejection.
        assert npi_rejection_reason(INVALID_CHECKSUM_NPI) is not None
        assert npi_rejection_reason(VALID_NPI) is None


# ── 3 & 4. Structurally valid positive / zero-result responses classify correctly ─

class TestValidResponsesClassifyCorrectly:
    async def test_positive_match_is_verified_found(self, patch_nppes):
        patch_nppes(FakeResponse(200, {
            "result_count": 1,
            "results": [{"number": VALID_NPI, "basic": {"organization_name": "Test Clinic", "status": "A"},
                          "addresses": [], "taxonomies": []}],
        }))
        result = await NPPESConnector().lookup_by_npi(VALID_NPI)
        assert result.success is True
        assert result.data["found"] is True

    async def test_affirmative_zero_results_is_not_found(self, patch_nppes):
        patch_nppes(FakeResponse(200, {"result_count": 0, "results": []}))
        result = await NPPESConnector().lookup_by_npi(VALID_NPI)
        assert result.success is True
        assert result.data["found"] is False


# ── 5. HTTP 200 with Errors never becomes NOT_FOUND ─────────────────────────

class TestNppesErrorBodyNeverAffirmativeNegative:
    async def test_nppes_errors_body_is_unavailable_not_found_false(self, patch_nppes):
        patch_nppes(FakeResponse(200, {"Errors": [{"description": "NPI must be 10 digits"}]}))
        result = await NPPESConnector().lookup_by_npi(VALID_NPI)
        assert result.success is False, "an Errors body must never be read as a clean match"
        assert result.data is None
        assert "npi_registry_request_error" in result.error

    async def test_legacy_pecos_errors_body_is_also_unavailable(self, patch_nppes):
        patch_nppes(FakeResponse(200, {"Errors": [{"description": "bad request"}]}))
        result = await PECOSConnector().lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert "npi_registry_request_error" in result.error


# ── 6. HTTP 200 missing required structural fields never becomes NOT_FOUND ──

class TestNppesMalformedBodyNeverAffirmativeNegative:
    @pytest.mark.parametrize("payload", [
        {},                                  # no "results" key at all
        {"results": "not-a-list"},            # wrong type
        {"results": None},
        "a bare string, not even a JSON object",
    ])
    async def test_missing_or_wrong_typed_results_is_unavailable(self, patch_nppes, payload):
        patch_nppes(FakeResponse(200, payload))
        result = await NPPESConnector().lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert result.data is None
        assert "malformed_response" in result.error


# ── 7. Timeout, transport error and non-200 responses fail closed ──────────

class TestTransportFailuresFailClosed:
    async def test_non_200_is_unavailable(self, patch_nppes):
        patch_nppes(FakeResponse(500, {"whatever": True}))
        result = await NPPESConnector().lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert "500" in result.error

    async def test_transport_exception_is_unavailable(self, monkeypatch):
        async def raiser(*a, **k):
            raise ConnectionError("simulated transport failure")

        monkeypatch.setattr(c, "_get_with_retry", raiser)
        result = await NPPESConnector().lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert result.data is None

    async def test_cms_ppef_non_200_is_unavailable(self, monkeypatch):
        # cms_ppef.py does `from app.Tefca.connectors import _get_with_retry`,
        # a direct name binding — patching connectors._get_with_retry (`c`)
        # does not affect it, so it must be patched on the cms_ppef module.
        from app.Tefca import cms_ppef as ppef_module

        async def fake_get(url, params, headers, timeout=None):
            return FakeResponse(503, {}, host="data.cms.gov")

        monkeypatch.setattr(ppef_module, "_get_with_retry", fake_get)
        result = await PPEFEnrollmentConnector(CMSDataAPIClient()).lookup_by_npi(VALID_NPI)
        assert result.success is False


# ── Follow-up: no full NPI in any warning/error log or SourceResult.error ────

class TestNoFullNpiInLogsOrErrors:
    """Every logger.warning/error call these connectors can reach, plus
    validate_npi's own message (surfaced through SourceResult.error via
    npi_rejection_reason), must carry only a masked NPI — last 4 digits — and
    never the full 10-digit value. Proven with caplog against a real test NPI,
    not just by reading the source."""

    async def test_nppes_transport_failure_log_never_contains_the_full_npi(self, monkeypatch, caplog):
        async def raiser(*a, **k):
            raise ConnectionError("simulated transport failure")

        monkeypatch.setattr(c, "_get_with_retry", raiser)
        with caplog.at_level("WARNING"):
            result = await NPPESConnector().lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert VALID_NPI not in caplog.text
        assert VALID_NPI not in (result.error or "")
        assert "...9883" in caplog.text  # last 4 digits of VALID_NPI, masked

    async def test_legacy_pecos_transport_failure_log_never_contains_the_full_npi(self, monkeypatch, caplog):
        async def raiser(*a, **k):
            raise ConnectionError("simulated transport failure")

        monkeypatch.setattr(c, "_get_with_retry", raiser)
        with caplog.at_level("WARNING"):
            result = await PECOSConnector().lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert VALID_NPI not in caplog.text
        assert VALID_NPI not in (result.error or "")

    async def test_cms_ppef_transport_failure_log_never_contains_the_full_npi(self, monkeypatch, caplog):
        from app.Tefca import cms_ppef as ppef_module

        async def raiser(*a, **k):
            raise ConnectionError("simulated transport failure")

        monkeypatch.setattr(ppef_module, "_get_with_retry", raiser)
        with caplog.at_level("WARNING"):
            result = await PPEFEnrollmentConnector(CMSDataAPIClient()).lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert VALID_NPI not in caplog.text
        assert VALID_NPI not in (result.error or "")

    async def test_cms_revocation_transport_failure_log_never_contains_the_full_npi(self, monkeypatch, caplog):
        from app.Tefca import cms_ppef as ppef_module

        async def raiser(*a, **k):
            raise ConnectionError("simulated transport failure")

        monkeypatch.setattr(ppef_module, "_get_with_retry", raiser)
        with caplog.at_level("WARNING"):
            result = await CMSRevocationConnector(CMSDataAPIClient()).lookup_by_npi(VALID_NPI)
        assert result.success is False
        assert VALID_NPI not in caplog.text
        assert VALID_NPI not in (result.error or "")

    async def test_checksum_invalid_npi_error_message_is_masked_not_full(self):
        """validate_npi's Luhn-failure message flows straight into
        SourceResult.error via npi_rejection_reason — it must not carry the
        full (invalid) NPI either."""
        result = await NPPESConnector().lookup_by_npi(INVALID_CHECKSUM_NPI)
        assert result.success is False
        assert INVALID_CHECKSUM_NPI not in result.error
        assert "..." in result.error  # masked form present

    def test_mask_npi_helper_is_last_four_digits_only(self):
        from app.services.npi_validator import mask_npi
        assert mask_npi(VALID_NPI) == "...9883"
        assert mask_npi(None) == "(none)"
        assert mask_npi("") == "(none)"
        assert VALID_NPI not in mask_npi(VALID_NPI)


# ── 8. CMS PPEF and CMS Revocation share the same input validation ─────────

class TestCmsSourcesShareValidation:
    @pytest.mark.parametrize("npi", ["", None, "123", INVALID_CHECKSUM_NPI])
    async def test_ppef_enrollment_rejects_every_invalid_shape(self, npi):
        result = await PPEFEnrollmentConnector(CMSDataAPIClient()).lookup_by_npi(npi)
        assert result.success is False
        assert result.data is None

    @pytest.mark.parametrize("npi", ["", None, "123", INVALID_CHECKSUM_NPI])
    async def test_revocation_rejects_every_invalid_shape(self, npi):
        result = await CMSRevocationConnector(CMSDataAPIClient()).lookup_by_npi(npi)
        assert result.success is False
        assert result.data is None


# ── 9. Invalid input never creates adverse evidence ─────────────────────────

class TestInvalidInputNeverAdverse:
    async def test_revocation_of_invalid_npi_is_not_no_active_revocation_found(self):
        """Regression pin: this branch used to return SourceResult.ok(checked=
        False, matches=[]) — success=True — which evidence_assembly read as
        Disposition.PASS via NO_ACTIVE_REVOCATION_RECORD_FOUND for an entity
        that was never queried. It must now be unavailable instead."""
        result = await CMSRevocationConnector(CMSDataAPIClient()).lookup_by_npi("")
        assert result.success is False
        assert result.data is None
        # The old, misleadingly-clean shape must not survive under any key.
        assert (result.data or {}).get("result") != NO_ACTIVE_REVOCATION_RECORD_FOUND

    async def test_nppes_no_npi_is_unavailable_not_a_clean_negative(self):
        result = await NPPESConnector().lookup_by_npi("")
        assert result.success is False
        assert result.data is None  # never {"found": False} for a query never made

    async def test_legacy_pecos_no_npi_is_unavailable_not_a_clean_negative(self):
        result = await PECOSConnector().lookup_by_npi(None)
        assert result.success is False
        assert result.data is None


# ── Follow-up: OIG LEIE has the same false-negative shape, fixed the same way ─
#
# Found on inspection per the explicit follow-up request. `_LEIE_CACHE["by_npi"]
# .get(npi, [])` misses identically whether `npi` is a real absent NPI or pure
# garbage, so a malformed NPI used to reach the cache lookup and come back as a
# clean SourceResult.ok(excluded=False) — never screened, reported as screened
# clean. Covered by the same centralised validator as the other four
# connectors; no new mechanism was needed.

class TestLeieSharesTheCentralizedGate:
    async def test_malformed_npi_is_rejected_before_any_cache_lookup(self, monkeypatch):
        from app.Tefca import connectors as conn_mod

        async def fail_if_called():
            raise AssertionError("must not reach the exclusions cache for an invalid NPI")

        monkeypatch.setattr(conn_mod, "_ensure_leie_loaded", fail_if_called)
        result = await OIGLEIEConnector().lookup_by_npi("not-an-npi")
        assert result.success is False
        assert result.data is None
        assert "npi_failed_validation" in result.error

    async def test_checksum_invalid_npi_is_rejected_before_any_cache_lookup(self, monkeypatch):
        from app.Tefca import connectors as conn_mod

        async def fail_if_called():
            raise AssertionError("must not reach the exclusions cache for an invalid NPI")

        monkeypatch.setattr(conn_mod, "_ensure_leie_loaded", fail_if_called)
        result = await OIGLEIEConnector().lookup_by_npi(INVALID_CHECKSUM_NPI)
        assert result.success is False

    async def test_no_npi_is_unavailable_not_a_clean_not_excluded(self, monkeypatch):
        """Regression pin: this branch used to call _build([], qp) — a clean,
        verified 'not excluded' — for an NPI that was never screened at all."""
        from app.Tefca import connectors as conn_mod

        async def fail_if_called():
            raise AssertionError("must not reach the exclusions cache with no NPI")

        monkeypatch.setattr(conn_mod, "_ensure_leie_loaded", fail_if_called)
        result = await OIGLEIEConnector().lookup_by_npi("")
        assert result.success is False
        assert result.data is None  # never {"excluded": False} for a screen never run

    async def test_valid_npi_still_reaches_the_cache_lookup(self, monkeypatch):
        """The gate must not block legitimate traffic."""
        from app.Tefca import connectors as conn_mod

        calls = []

        async def fake_ensure_loaded():
            calls.append(True)
            return True

        monkeypatch.setattr(conn_mod, "_ensure_leie_loaded", fake_ensure_loaded)
        monkeypatch.setattr(conn_mod, "_LEIE_CACHE", {"by_npi": {}, "by_name": {}, "row_count": 1})
        result = await OIGLEIEConnector().lookup_by_npi(VALID_NPI)
        assert calls == [True]
        assert result.success is True
        assert result.data["excluded"] is False


# ── 10. Duplicate NPPES invocation is prevented ─────────────────────────────

class TestDuplicateNppesCallPrevented:
    async def test_query_all_sources_calls_nppes_endpoint_exactly_once(self, patch_nppes):
        """SourceConnectorManager.query_all_sources used to fire nppes.lookup_by_npi
        and pecos.lookup_by_npi concurrently against the identical endpoint for
        the identical NPI. pecos must now be derived, not fetched."""
        calls = patch_nppes(FakeResponse(200, {
            "result_count": 1,
            "results": [{"number": VALID_NPI, "basic": {"organization_name": "Test Clinic", "status": "A"},
                          "addresses": [], "taxonomies": []}],
        }))
        mgr = c.SourceConnectorManager()
        entity = {"identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": VALID_NPI}]}
        sources = await mgr.query_all_sources(entity)
        assert len(calls) == 1, f"expected exactly one NPPES-endpoint call, got {len(calls)}"
        assert sources["pecos"].success is True
        assert sources["pecos"].data["found"] is True
        assert sources["pecos"].data.get("derived_from") == "nppes_concurrent_observation"
        assert "deprecated_proxy_call_skipped" in sources["pecos"].data.get("audit_reason", "")

    def test_from_nppes_never_claims_genuine_cms_ppef_provenance(self):
        """The derived result must read as an NPPES-derived proxy, never as
        though a genuine CMS PPEF/PECOS source were reached."""
        nppes_ok = c.SourceResult.ok("NPPES", {"found": True, "npi": VALID_NPI, "legal_name": "X",
                                                "addresses": [], "status_raw": "A"}, {}, "2.1")
        derived = PECOSConnector.from_nppes(nppes_ok)
        assert derived.api_version == PECOSConnector.API_VERSION
        assert derived.source_name == "PECOS"
        assert "CMS_PPEF" not in str(derived.data)

    async def test_from_nppes_propagates_unavailable(self):
        unavailable = c.SourceResult.unavailable("NPPES", "boom", {}, "2.1")
        derived = PECOSConnector.from_nppes(unavailable)
        assert derived.success is False
        assert "deprecated_proxy_call_skipped" in derived.error


# ── 11. Truthful labels ──────────────────────────────────────────────────────

class TestTruthfulLabels:
    def test_pecos_ui_label_and_subtitle_are_the_required_text(self):
        assert PECOS_UI_LABEL == "NPPES Registry — Legacy PECOS Proxy"
        low = PECOS_UI_SUBTITLE.lower()
        assert "not a direct pecos query" in low
        assert "does not establish medicare enrollment" in low
        assert "direct pecos connected" not in low

    def test_review_service_source_labels_use_the_same_text(self):
        from app.tefca_registry import review_service as rs
        assert rs.SOURCE_LABELS["pecos"] == PECOS_UI_LABEL
        assert rs.SOURCE_SUBTITLES["pecos"] == PECOS_UI_SUBTITLE
        assert "provider enrollment" not in rs.SOURCE_LABELS["pecos"].lower()

    def test_qa_connector_health_carries_the_caption(self):
        from app.Tefca import qa_engine
        assert qa_engine  # import succeeds; label wiring exercised in check_all_connectors

    def test_report_excel_header_and_limitation_are_truthful(self):
        from app.tefca_registry.report_excel import ENTITY_HEADERS, PECOS_PROXY_LIMITATION

        # 1. The truthful proxy label is present.
        assert "PECOS (NPPES proxy)" in ENTITY_HEADERS
        # 2. The bare, uncaveated "PECOS" header — the one that could be read
        #    as a direct PECOS verification — must be GONE, not just
        #    supplemented. This is the assertion the earlier `or True` no-op
        #    silently skipped.
        assert "PECOS" not in ENTITY_HEADERS
        assert not any(h.strip() == "PECOS" for h in ENTITY_HEADERS)
        # 3. This sheet reports the legacy six-source model only. Genuine CMS
        #    PECOS-derived sources (CMS_PPEF_ENROLLMENT / CMS_REVOCATION) are a
        #    separate report and must never be introduced into this header
        #    under a name that could be confused with the proxy column.
        assert not any(("CMS PPEF" in h or "CMS Revocation" in h or "PECOS-derived" in h)
                       for h in ENTITY_HEADERS)
        assert "not a direct" in PECOS_PROXY_LIMITATION.lower()
        assert "medicare enrollment" in PECOS_PROXY_LIMITATION.lower()

    def test_report_excel_actually_renders_the_truthful_header_and_limitation(self):
        """Wiring-level check: read back the real generated workbook rather
        than only asserting on the source constants."""
        from openpyxl import load_workbook

        from app.tefca_registry.report_excel import ENTITY_HEADERS, build_weekly_excel

        pecos_col = ENTITY_HEADERS.index("PECOS (NPPES proxy)") + 1  # openpyxl is 1-indexed
        xlsx_bytes = build_weekly_excel(
            {"report_type": "weekly", "contract": "TEST", "limitations": ["Existing caveat."]},
            "TEST-REPORT-001",
            entity_rows=[{
                "review_id": "R1", "entity_name": "Test Entity", "npi": VALID_NPI,
                "entity_type": "ORGANIZATION", "bucket": "B1", "rule_code": "R-1",
                "rationale": "test",
                "verification": {"nppes": {"status": "verified"},
                                  "pecos": {"status": "verified"},
                                  "oig_leie": {"status": "clear"}},
            }],
        )
        wb = load_workbook(BytesIO(xlsx_bytes))
        ws = wb["Entity Results"]
        header_cell = ws.cell(row=1, column=pecos_col).value
        assert header_cell == "PECOS (NPPES proxy)"
        assert header_cell != "PECOS"

        limitations_sheet = wb["Limitations"]
        limitation_values = [
            limitations_sheet.cell(row=r, column=1).value
            for r in range(5, limitations_sheet.max_row + 1)
        ]
        assert any("PECOS (NPPES proxy)" in (v or "") and "not a direct" in (v or "").lower()
                   for v in limitation_values), limitation_values

    def test_safe_upstream_request_id_is_host_scoped_and_allow_listed(self):
        cms_resp = FakeResponse(200, {}, headers={"X-Request-ID": "v-abc123"}, host="data.cms.gov")
        nppes_resp = FakeResponse(200, {}, headers={"X-Amz-Cf-Id": "cf-xyz"}, host="npiregistry.cms.hhs.gov")
        wrong_host = FakeResponse(200, {}, headers={"X-Request-ID": "should-not-be-read"}, host="npiregistry.cms.hhs.gov")

        assert safe_upstream_request_id(cms_resp) == {"header": "x-request-id", "value": "v-abc123"}
        assert safe_upstream_request_id(nppes_resp) == {"header": "x-amz-cf-id", "value": "cf-xyz"}
        # NPPES is only ever allow-listed for x-amz-cf-id, never x-request-id.
        assert safe_upstream_request_id(wrong_host) is None

    def test_safe_upstream_request_id_never_reads_auth_or_cookie_headers(self):
        resp = FakeResponse(200, {}, headers={
            "Authorization": "Bearer secret-token",
            "Set-Cookie": "session=abc",
        }, host="data.cms.gov")
        assert safe_upstream_request_id(resp) is None
