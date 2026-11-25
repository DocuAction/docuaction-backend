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

    @pytest.mark.parametrize("count", [65, 1_000, 100_000])
    async def test_a_padded_label_does_not_get_a_bare_value_dispatched(self, gateway, count):
        """1.2.0: more than 64 separators inside the label used to pass the guard."""
        prompt = f"Compare SYNTHETIC ORG, tax{'_' * count}id {SYN_EIN_BARE}, with SYNTHETIC ORG LLC"
        assert await gateway.call(prompt) is None
        out = await gateway._call_provider(gateway.primary_provider, prompt,
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


# ── review findings on #219 (CodeRabbit, 2026-11-13) ─────────────────────────
# Every value below is BARE (no hyphen in EIN position) unless the case is about
# a written format, so the unlabelled NN-NNNNNNN rule cannot hide a label miss.

SERIALIZED = [
    '{"EIN": "000000001"}',
    "{'ein': '000000001'}",
    '{"ORG_TAX_ID": "000000001", "BUS_NM": "SYNTHETIC ORG"}',
    "ORG_TAX_ID: 000000001",
    "ORG_TAX_ID=000000001",
    "org_tax_id\t000000001",
    "orgTaxId: 000000001",
    '<taxId>000000001</taxId>',
    "hco.tin = 000000001",
    "FED_TAX_ID|000000001",
    "TAXPAYER_IDENTIFICATION_NUMBER: 000000001",
    "provider_ein => 000000001",
    '"fein":"000000001"',
    "IRS_NUMBER 000000001",
    "tax id value 000000001",
]
ITIN_WRITTEN = [
    "ITIN: 900-00-0001",
    '{"itin": "900-00-0001"}',
    "TIN 900-00-0001",
    "taxpayer id 900-00-0001",
]
NOT_LABELS = [
    "routing 000000001", "PROTEIN: 000000001", "protein 000000001",
    "taxonomy 207Q00000X", "TAXONOMY_CD: 000000001", "listing 000000001",
    "destination: 000000001", "continent 000000001", "record 000000001",
    "tax_status: 000000001", "NPI: 1234567893", "ZIP: 021011234",
    "phone 617-555-0100", "SSN-shaped but unlabelled 900-00-0001",
    "retain 000000001", "certain: 000000001", "margin=000000001",
]


class TestSerializedLabels:
    @pytest.mark.parametrize("text", SERIALIZED + ITIN_WRITTEN)
    def test_a_labelled_value_is_found_whatever_the_serialization(self, text):
        assert "labelled_tax_identifier" in pi.find_in_text(text), text

    @pytest.mark.parametrize("text", SERIALIZED + ITIN_WRITTEN)
    def test_and_is_redacted_with_no_digit_of_the_value_left(self, text):
        out = pi.redact_text(text)
        assert pi.REDACTED in out, out
        assert "000000001" not in out and "900-00-0001" not in out, out
        assert pi.find_in_text(out) == [], out

    @pytest.mark.parametrize("text", NOT_LABELS)
    def test_words_that_only_contain_a_label_are_not_labels(self, text):
        assert pi.find_in_text(text) == [], text
        assert pi.redact_text(text) == text

    def test_the_label_is_kept_so_a_reader_knows_what_was_withheld(self):
        assert pi.redact_text("ORG_TAX_ID: 000000001") == f"ORG_TAX_ID: {pi.REDACTED}"

    def test_redact_mapping_covers_a_serialized_record_inside_a_string_value(self):
        out = pi.redact_mapping({"note": 'raw row {"ORG_TAX_ID": "000000001"}'})
        assert "000000001" not in out["note"]


class TestTheGatewayRefusesSerializedAndItinForms(TestTheTefcaAiGatewayRefuses):
    @pytest.mark.parametrize("text", SERIALIZED + ITIN_WRITTEN)
    async def test_not_dispatched(self, gateway, text):
        prompt = f"Compare SYNTHETIC ORG using this record: {text}"
        assert await gateway.call(prompt) is None
        assert (await gateway.call_dual(prompt)).primary is None
        assert await gateway._call_provider(gateway.primary_provider, prompt,
                                            "standard", "entity_match", 0.0) is None
        assert gateway.sent == []


# ── the whole path: a record with tax identifiers -> the prompt that would be sent ──

PROTECTED_VALUES = ("00-0000005", "000000006", "000000007", "000000008", "00-0000009")


def _record(name, address):
    """A record as the verification step holds it, PLUS every protected field a
    delivery or a reference file could carry. None of the extras is on an
    allowlist; the test is that none of them travels."""
    return {"name": name, "address": address, "npi": "1234567893",
            "entity_type": "organization", "state": "MA", "tefcaid": "T-SYN-1",
            "ein": PROTECTED_VALUES[0], "tin": PROTECTED_VALUES[1],
            "ORG_TAX_ID": PROTECTED_VALUES[2], "tax_id": PROTECTED_VALUES[3],
            "payload": {"ORG_TAX_ID": PROTECTED_VALUES[2], "FEIN": PROTECTED_VALUES[4]},
            "raw_line": f"x|y|{PROTECTED_VALUES[1]}|z"}


class TestARecordsProtectedFieldsNeverReachThePrompt:
    """The one AI path the Review Hub's screens can reach: verification ->
    EntityResolver.resolve_with_orchestrator -> orchestrator -> gateway. The orchestrator is
    real; only the network transport is replaced, by a recorder."""

    @pytest.fixture
    def path(self, monkeypatch):
        from app.tefca_registry.entity_resolver import EntityResolver
        from test_tefca_ai_control_plane import _StubGateway, _orchestrator, _response

        monkeypatch.setenv("AI_ENTITY_RESOLUTION", "advisory")
        gateway = _StubGateway(_response())
        return EntityResolver(), _orchestrator(gateway=gateway), gateway

    async def _run(self, path, a, b):
        resolver, orchestrator, gateway = path
        result = await resolver.resolve_with_orchestrator(a, b, orchestrator=orchestrator)
        return result, gateway

    async def test_only_allowlisted_fields_are_in_the_prompt_and_its_context(self, path, caplog):
        import json
        import logging

        a = _record("SYNTHETIC ALPHA REGIONAL HEALTH", "1 Synthetic Way, Testville, MA 02101")
        b = _record("SYNTHETIC ALPHA REGNL HLTH SYSTEM", "900 Other Road, Elsewhere, MA 02199")
        b.pop("npi")                     # no shared identifier, so the deterministic steps
        b.pop("tefcaid")                 # disagree and the AI step is the one consulted
        with caplog.at_level(logging.DEBUG):
            _result, gateway = await self._run(path, a, b)
        assert gateway.calls, ("the AI step was not reached, so this run proves nothing "
                               "about what it would send")
        for _kind, prompt, context in gateway.calls:
            sent = prompt + json.dumps(context, default=str)
            for value in PROTECTED_VALUES:
                assert value not in sent, value
            for key in ("ORG_TAX_ID", "FEIN", "raw_line", "payload", '"ein"', '"tin"', "tax_id"):
                assert key not in sent, key
            assert set(context.get("fields_sent") or []) <= {"name", "address", "npi", "entity_type"}
            assert pi.find_in_text(sent) == []
        for value in PROTECTED_VALUES:
            assert value not in caplog.text, value

    async def test_the_legacy_direct_client_cannot_be_built(self):
        """The pre-control-plane transport: no client is ever constructed, and
        the class refuses to call a provider even if one is."""
        from app.tefca_registry import ai_client

        assert ai_client.build_ai_client() is None
        with pytest.raises(NotImplementedError):
            ai_client.AnthropicClient().complete(model="m", system="s", prompt="p")

    async def test_ai_off_means_no_prompt_is_built_at_all(self, path, monkeypatch):
        from app.tefca_registry.entity_resolver import EntityResolver

        monkeypatch.delenv("AI_ENTITY_RESOLUTION", raising=False)
        _resolver, orchestrator, gateway = path
        a = _record("SYNTHETIC ALPHA REGIONAL HEALTH", "1 Synthetic Way, Testville, MA 02101")
        b = _record("SYNTHETIC ALPHA REGNL HLTH SYSTEM", "900 Other Road, Elsewhere, MA 02199")
        b.pop("npi")                     # no shared identifier, so the deterministic steps
        b.pop("tefcaid")                 # disagree and the AI step is the one consulted
        await EntityResolver().resolve_with_orchestrator(a, b, orchestrator=orchestrator)
        assert gateway.calls == []


class TestNoOtherAiTransportInTheReviewModules:
    """A static inventory, so a new direct provider call in the modules behind
    the Review Hub fails a test instead of passing unnoticed."""

    def test_only_the_gateway_names_a_provider_endpoint_or_sdk(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        scanned = [root / "tefca_registry", root / "Tefca", root / "reports"]
        pattern = re.compile(r"^\s*(?:import|from)\s+(?:anthropic|openai)\b"
                             r"|api\.anthropic\.com|api\.openai\.com", re.M)
        allowed = {"tefca_registry/ai/gateway.py"}
        found = set()
        for base in scanned:
            for path in base.rglob("*.py"):
                if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                    found.add(path.relative_to(root).as_posix())
        assert found <= allowed, sorted(found - allowed)
        assert "tefca_registry/ai/gateway.py" in found


# -- follow-up review finding on #219: one vocabulary for names AND for text --

def _every_protected_spelling():
    """Every name the field rule accepts, built from its own word lists."""
    names = set(pi._SINGLE_TOKENS)
    for head in ("tax", "taxpayer", "irs"):
        for suffix in pi._ID_SUFFIXES:
            names.add(f"{head}_{suffix}")
    names.add("employer_identification")
    names.add("employer_identification_number")
    return sorted(names)


class TestNamesAndTextUseOneVocabulary:
    def test_tax_code_with_a_bare_value_is_found_redacted_and_refused(self):
        text = '{"TAX_CODE": "000000001"}'
        assert pi.is_protected_field("TAX_CODE")
        assert "labelled_tax_identifier" in pi.find_in_text(text)
        assert "000000001" not in pi.redact_text(text)
        with pytest.raises(pi.ProtectedIdentifierEgressRefused):
            pi.assert_safe_for_ai(text)

    @pytest.mark.parametrize("name", _every_protected_spelling())
    def test_every_name_the_field_rule_protects_also_labels_a_value_in_text(self, name):
        assert pi.is_protected_field(name), name
        for form in (name, name.upper(), f"ORG_{name.upper()}", name.replace("_", " "),
                     name.replace("_", "")):
            for text in (f'{{"{form}": "000000001"}}', f"{form}: 000000001", f"{form}=000000001"):
                assert pi.find_in_text(text), text
                assert "000000001" not in pi.redact_text(text), text

    @pytest.mark.parametrize("text", [
        "taxonomy_code: 000000001", "TAXONOMY_CD=000000001", "tax_status: 000000001",
        "syntax_code: 000000001", "airs_number: 000000001", "zip_code: 021011234",
        "postal code 000000001", "status_cd: 000000001",
    ])
    def test_near_misses_still_do_not_match(self, text):
        assert pi.find_in_text(text) == [], text


class TestTheGatewayRefusesEveryProtectedSpelling(TestTheTefcaAiGatewayRefuses):
    @pytest.mark.parametrize("name", ["TAX_CODE", "TAX_CD", "IRS_CODE", "TAXPAYER_NR", "tax ident"])
    async def test_not_dispatched(self, gateway, name):
        prompt = f'Compare SYNTHETIC ORG using {{"{name}": "000000001"}}'
        assert await gateway.call(prompt) is None
        assert (await gateway.call_dual(prompt)).primary is None
        assert gateway.sent == []


class TestASingleTokenFollowedByAnIdentifierWord:
    """Second follow-up on #219: `PROVIDER_EIN_ID` is a protected name, and the
    text rule did not accept an identifier word after a single-token label."""

    @pytest.mark.parametrize("name", ["PROVIDER_EIN_ID", "EIN_NUMBER", "TIN_NBR", "FEIN_CD",
                                      "orgEinId", "ITIN_NO", "TAXPAYER_ID", "taxid_number"])
    def test_found_redacted_and_consistent_with_the_field_rule(self, name):
        assert pi.is_protected_field(name), name
        for text in (f"{name}: 000000001", f'{{"{name}": "000000001"}}'):
            assert "labelled_tax_identifier" in pi.find_in_text(text), text
            assert "000000001" not in pi.redact_text(text), text

    @pytest.mark.parametrize("text", ["protein_id: 000000001", "routing_number: 000000001",
                                      "margin_no: 000000001", "retain_id=000000001"])
    def test_words_ending_in_a_label_still_do_not_match(self, text):
        assert pi.find_in_text(text) == [], text


class TestALongSeparatorRunInsideALabelIsNotABypass:
    """Owner instruction 2026-11-14: labels with more than 64 separators must
    not bypass the guard.

    The run of separators inside a label (`TAX___ID`, `tax - payer   id`) was
    bounded at 64 in version 1.1.0, and "more than 64 apart is not a label" was
    pinned as an accepted limit. It was a way round the guard: pad the label and
    a bare nine-digit value passed `find_in_text`, `redact_text` and
    `assert_safe_for_ai`. The run is now of ANY length and is still matched
    without backtracking (possessive), so no input is slow.

    What is unchanged, and still a stated limit: the gap between a label and its
    VALUE (`_GAP`, at most 16 characters of punctuation / joining words)."""

    RUNS = [65, 66, 1_000, 100_000]

    def test_the_run_is_unbounded_and_possessive(self):
        assert not hasattr(pi, "LABEL_SEPARATOR_MAX")
        assert pi._SEP.endswith("*+") and "{0," not in pi._SEP
        assert pi.PROTECTED_IDENTIFIERS_VERSION == "1.6.1"

    @pytest.mark.parametrize("count", RUNS)
    @pytest.mark.parametrize("sep", ["_", " ", "-", " _-", "\t", "\r\n", " \n\t_-\r"])
    def test_found_redacted_and_refused_whatever_the_run(self, sep, count):
        run = (sep * count)[:count]
        text = f"before tax{run}id: {SYN_EIN_BARE} after"
        assert pi.find_in_text(text) == ["labelled_tax_identifier"]
        redacted = pi.redact_text(text)
        assert SYN_EIN_BARE not in redacted
        # Nothing but the value is replaced: the label, its whole run and the
        # text on both sides are still there.
        assert redacted == f"before tax{run}id: {pi.REDACTED} after"
        assert pi.redact_text(redacted) == redacted            # idempotent
        assert pi.find_in_text(redacted) == []
        with pytest.raises(pi.ProtectedIdentifierEgressRefused) as refused:
            pi.assert_safe_for_ai(text, where="synthetic")
        assert SYN_EIN_BARE not in str(refused.value)

    @pytest.mark.parametrize("count", RUNS)
    @pytest.mark.parametrize("template", [
        '{{"tax{run}id": "{value}"}}',                         # JSON-ish
        '{{"org": {{"tax{run}payer{run}id": "{value}"}}}}',    # nested, three-word label
        "tax{run}id={value}",                                  # key=value
        "TAX{run}IDENTIFICATION{run}NUMBER = {value}",
        "employer{run}identification{run}number: {value}",
        "ein{run}number: {value}",
        "the IRS{run}number is {value} for this organisation", # prose, joining word
        "orgTax{run}Id: {value}",                              # camelCase start
    ])
    def test_every_form_of_label_and_serialization(self, template, count):
        text = template.format(run="_" * count, value=SYN_EIN_BARE)
        assert "labelled_tax_identifier" in pi.find_in_text(text), template
        redacted = pi.redact_text(text)
        assert SYN_EIN_BARE not in redacted and pi.REDACTED in redacted
        assert len(redacted) == len(text) - len(SYN_EIN_BARE) + len(pi.REDACTED)
        with pytest.raises(pi.ProtectedIdentifierEgressRefused):
            pi.assert_safe_for_ai(text)

    @pytest.mark.parametrize("value", ["00-0000000", "00 0000000", "000000000", "900-70-0000"])
    def test_every_written_form_of_the_value_after_a_padded_label(self, value):
        text = "tax" + "_" * 5_000 + "id: " + value
        assert "labelled_tax_identifier" in pi.find_in_text(text)
        assert not any(ch.isdigit() for ch in pi.redact_text(text))

    @pytest.mark.parametrize("text", [
        # Long runs and nine digits, but no label: not newly flagged.
        "_" * 5_000 + " 000000000",
        "section " + "-" * 5_000 + " 000000000 " + "_" * 5_000,
        "tax" + "_" * 5_000 + "onomy 000000000",              # not an identifier word
        "taxation" + " " * 5_000 + "id: 000000000",           # runs on into another word
        "syntax" + "_" * 5_000 + "id: 000000000",
        "routing" + "_" * 5_000 + "number: 000000000",
        "id" + "_" * 5_000 + "tax: 000000000",                # wrong order
        # A padded label with WORDS between it and the nine digits is not that
        # label's value. (Padding alone between them is no longer a way out:
        # see TestPaddingBetweenALabelAndItsValue.)
        "tax" + "_" * 5_000 + "id was not supplied; the count was 000000000",
    ])
    def test_long_runs_without_a_label_or_far_from_the_value_are_not_flagged(self, text):
        assert pi.find_in_text(text) == [], text[:40]
        assert pi.redact_text(text) == text

    def test_what_is_newly_flagged_is_stated(self):
        """The false-positive behaviour, precisely: the words of a label may now
        be ANY number of spaces, tabs, line breaks, underscores or hyphens apart
        (and nothing else). So prose in which `tax` ends one paragraph and `ID`
        opens a later one, with only blank lines between and nine digits right
        after, is read as a labelled value. One other character in between and
        it is not."""
        blank = "\n" * 200
        assert pi.find_in_text(f"we discussed tax{blank}ID 000000000") == ["labelled_tax_identifier"]
        # 1.4.0: dot and slash are separators too (the field rule's tokeniser accepts them: `tax.id`);
        # any OTHER character in between still ends the label.
        assert pi.find_in_text(f"we discussed tax.{blank}ID 000000000") == ["labelled_tax_identifier"]
        assert pi.find_in_text(f"we discussed tax:{blank}ID 000000000") == []
        assert pi.find_in_text(f"we discussed tax{blank}Notes. ID 000000000") == []

    def test_long_separator_input_finishes_well_inside_a_fixed_budget(self):
        # CPU seconds of this process, not wall-clock: a busy or paging runner
        # must not fail a test about the amount of work done.
        import time

        hostile = [
            "tax" + "_" * 200_000,
            ("tax" + " -_" * 20_000) * 3,
            "employer" + " " * 200_000 + "x",
            ("tax" + "_" * 70) * 3_000,
            "tax" + "-" * 100_000 + "id" + "-" * 100_000,
            ("tax_id" + "_" * 70 + "x ") * 3_000,
            "-" * 400_000,
        ]
        started = time.process_time()
        for text in hostile:
            assert pi.find_in_text(text) == []
            assert pi.redact_text(text) == text
        seconds = time.process_time() - started
        assert seconds < 5.0, f"{seconds:.2f} CPU s for {sum(map(len, hostile)):,} characters"

    def test_the_unbounded_run_is_linear_on_inputs_built_against_it(self):
        """Inputs aimed at the new path: every label start is followed by a long
        run that ends in something that is NOT the next label word, so a
        backtracking matcher would retry each run from every shorter length.
        Four times the input must stay far below sixteen times the time."""
        import time

        def build(n):
            return [
                ("tax" + "_" * 2_000 + "payer" + "_" * 2_000 + "x ") * n,
                ("tax" + " " * 2_000 + "id" + " " * 2_000 + "id" + " " * 2_000 + "x ") * n,
                ("employer" + "-" * 2_000 + "identification" + "-" * 2_000 + "x ") * n,
                ("ein" + "\n" * 2_000 + "number" + "\n" * 2_000 + "0000 ") * n,
                ("irs" + "_" * 2_000) * (2 * n),
                "tax" + "_" * (4_000 * n) + "id" + "_" * (4_000 * n) + "number" + "_" * (4_000 * n),
            ]

        def run(texts):
            best = float("inf")
            for _ in range(3):                       # best of three: a loaded runner is noisy
                started = time.process_time()
                for text in texts:
                    assert pi.find_in_text(text) == []
                    assert pi.redact_text(text) == text
                best = min(best, time.process_time() - started)
            return best

        small, large = build(10), build(40)          # four times the input
        t_small, t_large = run(small), run(large)
        total = sum(map(len, large))
        assert total > 1_300_000
        assert t_large < 5.0, f"{t_large:.2f} CPU s for {total:,} characters"
        # Linear work is 4x; quadratic would be 16x.
        assert t_large < 9 * t_small + 0.3, (t_small, t_large)


# ── padding between the label and its value (2026-11-14) ─────────────────────
#
# Closing the padded-label bypass left its sibling open: the gap between a
# label and its value allowed 16 characters in total, so 17 spaces (or tabs,
# underscores, hyphens) after `tax_id` let a bare value through. Padding is
# not content: a run of horizontal white space, underscores or hyphens of any
# length is now free, and the limit of 16 counts only punctuation and joining
# words.

class TestPaddingBetweenALabelAndItsValue:
    PADS = [" ", "\t", "_", "-", " \t", "_-", " _-\t"]
    RUNS = [17, 18, 1_000, 100_000]

    @pytest.mark.parametrize("pad", PADS)
    @pytest.mark.parametrize("count", RUNS)
    def test_padding_of_any_length_does_not_hide_the_value(self, pad, count):
        run = (pad * count)[:count]
        for text in (f"tax_id{run}{SYN_EIN_BARE}",
                     f"ORG_TAX_ID:{run}{SYN_EIN_BARE} for SYNTHETIC ORG",
                     f'"ein"{run}:{run}"{SYN_EIN_BARE}"',
                     f"provider ein is{run}{SYN_EIN_BARE}"):
            found = pi.find_in_text(text)
            assert found, (pad, count, text[:30])
            redacted = pi.redact_text(text)
            assert SYN_EIN_BARE not in redacted
            assert redacted == text.replace(SYN_EIN_BARE, redacted[text.index(SYN_EIN_BARE):
                                            len(redacted) - (len(text) - text.index(SYN_EIN_BARE)
                                                             - len(SYN_EIN_BARE))])
            assert pi.redact_text(redacted) == redacted
            with pytest.raises(Exception):
                pi.assert_safe_for_ai(text)

    def test_the_limit_still_counts_punctuation_and_joining_words(self):
        """Padding is free; seventeen punctuation marks are still not a label's value."""
        assert pi.find_in_text("tax_id" + ":" * 16 + SYN_EIN_BARE)
        assert not pi.find_in_text("tax_id" + ":" * 17 + SYN_EIN_BARE)
        assert not pi.find_in_text("tax_id" + ": " * 17 + SYN_EIN_BARE)
        assert pi.find_in_text("tax_id" + (":" + " " * 500) * 16 + SYN_EIN_BARE)

    def test_a_word_between_is_still_not_padding(self):
        assert not pi.find_in_text(f"tax_id   belongs to someone else   {SYN_EIN_BARE}")
        assert not pi.find_in_text(f"tax_id {' ' * 500} see invoice {SYN_EIN_BARE}")

    def test_a_line_break_between_label_and_value_is_treated_as_before(self):
        """Unchanged by this fix and pinned so a later change is deliberate: the
        gap does not cross a line feed."""
        before = bool(pi.find_in_text(f"tax_id\n{SYN_EIN_BARE}"))
        assert before is False
        assert bool(pi.find_in_text(f"tax_id{' ' * 500}\n{SYN_EIN_BARE}")) is False

    def test_padding_heavy_text_stays_linear(self):
        import time

        hostile = [
            "tax_id" + " " * 400_000 + "x",
            ("tax_id" + " " * 50 + ":") * 8_000,
            ("tax id " + "-" * 40) * 10_000 + "z",
            "tax_id" + (":" + "_" * 20_000) * 17 + SYN_EIN_BARE,
            " " * 400_000 + "tax_id" + "\t" * 400_000,
        ]
        started = time.process_time()
        for text in hostile:
            pi.find_in_text(text)
            pi.redact_text(text)
        assert time.process_time() - started < 5.0
