"""Two CodeRabbit Majors on #219 (reproduced here before the fix):
(1) a field name the field rule protects with a dot or slash separator (`tax.id`) was not a text label;
(2) a protected name with trailing name tokens (`EIN_PRIMARY`) was not consumed by the text detectors.
Contract: any field name `is_protected_field` accepts must also label its value in serialized text.
Whole-token boundaries are kept: unrelated words (`protein`, `einstein`, `taxonomy`) are not matches."""
import pytest

from app.Tefca import protected_identifiers as pi

pytestmark = pytest.mark.regression
VALUE = "000000001"            # synthetic, bare (no hyphen): only a label can reveal it

DOTTED_OR_SLASHED = ["tax.id", "tax/id", "TAX.ID", "hco.tin", "org/ein", "taxpayer.id", "irs/tax.id",
                     "employer.identification.number", "Tax/Identifier"]
TRAILING = ["EIN_PRIMARY", "ein_primary", "TIN_BACKUP", "FEIN-secondary", "ein.primary", "ein/primary", "einPrimary",
            "orgEinPrimaryContact", "TAX_ID_PRIMARY", "tax.id.primary", "EIN_PRIMARY_2"]


@pytest.mark.parametrize("name", DOTTED_OR_SLASHED + TRAILING)
def test_every_name_the_field_rule_protects_also_labels_its_value_in_text(name):
    assert pi.is_protected_field(name), name
    for text in (f'{{"{name}": "{VALUE}"}}', f"{name}: {VALUE}", f"{name}={VALUE}", f'"{name}":"{VALUE}"'):
        assert "labelled_tax_identifier" in pi.find_in_text(text), text
        redacted = pi.redact_text(text)
        assert VALUE not in redacted and pi.REDACTED in redacted, (text, redacted)


@pytest.mark.parametrize("name", DOTTED_OR_SLASHED + TRAILING)
def test_a_serialized_mapping_is_redacted_whatever_the_name(name):
    assert pi.redact_mapping({name: VALUE}) == {name: pi.REDACTED}


@pytest.mark.parametrize("name", ["protein_primary", "einstein_primary", "taxonomy.code", "listing_id", "routing/number",
                                  "einsteinPrimary", "tax.rate"])
def test_whole_token_boundaries_still_hold(name):
    assert not pi.is_protected_field(name), name
    assert pi.find_in_text(f"{name}: {VALUE}") == [], name


async def test_the_gateway_does_not_dispatch_dotted_slashed_or_trailing_token_names(monkeypatch):
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
    for name in ("tax.id", "tax/id", "EIN_PRIMARY", "einPrimary"):
        assert await gw.call(f'Compare SYNTHETIC ORG {{"{name}": "{VALUE}"}} with SYNTHETIC ORG LLC') is None, name
    assert sent == []


@pytest.mark.parametrize("text", ["EIN_12-3456789", "EIN_123456789", "tax_id_000000001", "EIN_2: 000000001", "ein.primary.2=000000001"])
def test_a_value_attached_to_a_label_is_never_swallowed_by_the_trailing_tokens(text):
    assert "labelled_tax_identifier" in pi.find_in_text(text) or "ein_shaped_value" in pi.find_in_text(text), text
    assert "3456789" not in pi.redact_text(text) and "000000001" not in pi.redact_text(text) and "123456789" not in pi.redact_text(text)
