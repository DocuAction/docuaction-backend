"""Protected tax identifiers: detection, redaction, AI-egress refusal.

Every value here is SYNTHETIC. `00-0000000` style values use prefixes the IRS
does not issue (00, 07, 08, 09, 17, 18, 19 ...), so no test value can be a real
employer's number. No database, no network.
"""

from __future__ import annotations

import pytest

from app.Tefca import protected_identifiers as pi

#: IRS campus prefixes 00, 07, 89 are not issued: these cannot be real EINs.
SYN_EIN = "00-0000001"
SYN_EIN_BARE = "000000001"


class TestFieldNames:
    @pytest.mark.parametrize("name", [
        "EIN", "ein", "TIN", "FEIN", "ITIN", "ORG_TAX_ID", "org_tax_id", "TaxId",
        "taxID", "Tax Identifier", "TAX_IDENTIFICATION_NUMBER", "tax-number",
        "TAX_NBR", "Taxpayer ID", "TAXPAYER_IDENTIFICATION_NUMBER", "provider_ein",
        "Employer Identification Number", "IRS_NUMBER", "hco.tin", "FED_TAX_ID",
    ])
    def test_tax_identifier_names_are_protected(self, name):
        assert pi.is_protected_field(name), name

    @pytest.mark.parametrize("name", [
        "NPI", "ORG_NPI", "ORG_CCN_ID", "UEI", "TAXONOMY", "taxonomy_code",
        "routing", "listing_id", "protein", "BUS_NM", "ZIP5_CD", "tax_status",
        "tax_exempt", "continent", "destination", "", None, "id", "number",
        "TELEPHN_NBR", "rce_org_oid",
    ])
    def test_other_names_are_not(self, name):
        assert not pi.is_protected_field(name), name

    def test_the_stored_iqvia_affiliation_projection_has_exactly_one(self):
        """The importer keeps ORG_TAX_ID in the stored payload. If a second
        protected column is ever added to the projection this fails and the
        addition gets looked at."""
        from app.tefca_registry.rce import iqvia_import as ii

        cols = (ii.AFFILIATION_KEYS_AND_IDS + ii.AFFILIATION_ORG_DESCRIPTORS
                + ii.AFFILIATION_PERSON_NAMES)
        assert pi.protected_fields(cols) == ["ORG_TAX_ID"]


class TestRedaction:
    def test_values_under_protected_keys_are_replaced_at_any_depth(self):
        record = {"BUS_NM": "SYNTHETIC ORG", "ORG_TAX_ID": SYN_EIN_BARE,
                  "nested": [{"ein": SYN_EIN, "NPI": "1234567893"}],
                  "payload": {"Tax Identifier": SYN_EIN_BARE}}
        out = pi.redact_mapping(record)
        assert out["ORG_TAX_ID"] == pi.REDACTED
        assert out["nested"][0]["ein"] == pi.REDACTED
        assert out["payload"]["Tax Identifier"] == pi.REDACTED
        assert out["nested"][0]["NPI"] == "1234567893"
        assert out["BUS_NM"] == "SYNTHETIC ORG"
        assert SYN_EIN not in repr(out) and SYN_EIN_BARE not in repr(out)

    def test_the_input_is_not_modified(self):
        record = {"ORG_TAX_ID": SYN_EIN_BARE}
        pi.redact_mapping(record)
        assert record == {"ORG_TAX_ID": SYN_EIN_BARE}

    @pytest.mark.parametrize("empty", [None, ""])
    def test_an_absent_value_stays_absent(self, empty):
        """Replacing an empty cell would claim an identifier was delivered."""
        assert pi.redact_mapping({"EIN": empty}) == {"EIN": empty}

    def test_a_value_in_free_text_is_replaced(self):
        out = pi.redact_mapping({"note": f"caller gave EIN {SYN_EIN} by phone"})
        assert SYN_EIN not in out["note"] and pi.REDACTED in out["note"]

    def test_no_partial_value_survives(self):
        """A mask that keeps the first or last digits keeps part of the value."""
        out = pi.redact_mapping({"ORG_TAX_ID": "001234567"})
        assert not any(ch.isdigit() for ch in out["ORG_TAX_ID"])


class TestFreeText:
    @pytest.mark.parametrize("text", [
        f"EIN: {SYN_EIN}", f"ein {SYN_EIN_BARE}", f"FEIN#{SYN_EIN}",
        f"TIN = {SYN_EIN_BARE}", f"Tax ID: {SYN_EIN}", f"tax id number {SYN_EIN_BARE}",
        f"taxpayer identification number is {SYN_EIN}",
        f"Employer Identification Number {SYN_EIN}",
        f"the organisation ({SYN_EIN}) was matched",
    ])
    def test_found(self, text):
        assert pi.find_in_text(text), text

    @pytest.mark.parametrize("text", [
        "NPI 1234567893", "ZIP 02101-1234", "phone 617-555-0100",
        "OID 2.16.840.1.113883.3.1234", "received 2026-11-13",
        "record 000000001 of the delivery", "CCN 220071", "", None,
        "UEI ABCDEFGHJKL1", "sha256 9f86d081884c7d659a2feaa0c55ad015",
        "the entity has no tax identifier on file",
    ])
    def test_not_found(self, text):
        assert pi.find_in_text(text) == [], text

    def test_refusal_names_the_rule_and_never_the_value(self):
        with pytest.raises(pi.ProtectedIdentifierEgressRefused) as exc:
            pi.assert_safe_for_ai(f"Compare SYNTHETIC ORG, EIN {SYN_EIN}", where="unit")
        message = str(exc.value)
        assert SYN_EIN not in message and "0000001" not in message
        assert "labelled_tax_identifier" in message

    def test_clean_text_passes(self):
        pi.assert_safe_for_ai("Compare SYNTHETIC ORG, NPI 1234567893, Boston MA")


class TestOneVocabulary:
    def test_every_government_restricted_identifier_is_a_protected_name(self):
        from app.Tefca.identifier_boundary import GOVERNMENT_RESTRICTED

        for identifier in GOVERNMENT_RESTRICTED:
            assert pi.is_protected_field(identifier)
            assert pi.is_protected_field(f"ORG_{identifier}")

    def test_the_markers_the_shadow_reassessment_uses_are_all_covered(self):
        from app.tefca_registry.rce.shadow_reassessment import _EIN_MARKERS

        for marker in _EIN_MARKERS:
            if marker == "irs":  # a source name, not an identifier column
                continue
            assert pi.is_protected_field(marker), marker


class TestTheTefcaAiGatewayRefuses:
    """No network: every provider call is replaced, and the assertion is that
    it is never reached."""

    DIRTY = f"Compare SYNTHETIC ORG (EIN {SYN_EIN}) with SYNTHETIC ORG LLC"
    CLEAN = ("Compare SYNTHETIC ORG, NPI 1234567893, with SYNTHETIC ORG LLC, "
             "Boston MA 02101-1234")

    @pytest.fixture
    def gateway(self, monkeypatch):
        from app.tefca_registry.ai.gateway import TEFCAAIGateway

        monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-not-a-key")
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-key")
        sent = []

        async def record(self, model, prompt, max_output, temperature):
            sent.append(prompt)
            return '{"synthetic": true}'

        monkeypatch.setattr(TEFCAAIGateway, "_call_anthropic", record)
        monkeypatch.setattr(TEFCAAIGateway, "_call_openai", record)
        gw = TEFCAAIGateway()
        gw.sent = sent
        return gw

    async def test_call_does_not_dispatch(self, gateway):
        assert await gateway.call(self.DIRTY) is None
        assert gateway.sent == []

    async def test_call_dual_does_not_dispatch(self, gateway):
        result = await gateway.call_dual(self.DIRTY)
        assert result.primary is None and result.secondary is None
        assert gateway.sent == []

    async def test_the_dispatch_point_itself_refuses(self, gateway):
        """A future caller that bypasses `call` still cannot send it."""
        out = await gateway._call_provider(gateway.primary_provider, self.DIRTY,
                                           "standard", "entity_match", 0.0)
        assert out is None and gateway.sent == []
        assert not gateway._breakers[gateway.primary_provider].is_open

    async def test_a_refusal_does_not_log_the_value_or_the_prompt(self, gateway, caplog):
        import logging

        with caplog.at_level(logging.DEBUG):
            await gateway.call(self.DIRTY)
        assert SYN_EIN not in caplog.text and "0000001" not in caplog.text
        assert "SYNTHETIC ORG" not in caplog.text
        assert "protected tax identifier" in caplog.text

    async def test_a_clean_prompt_is_still_dispatched(self, gateway):
        """The guard must not stop the governed use it protects."""
        assert await gateway.call(self.CLEAN) is not None
        assert gateway.sent == [self.CLEAN]
