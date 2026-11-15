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


# ── CodeRabbit on c888a89: a four-digit index, and a quadratic scan (both reproduced before the fix) ──────────────

@pytest.mark.parametrize("name", ["EIN_2026", "EIN_PRIMARY_2026", "TAX_ID_2026", "tin.2025", "FEIN-2024-backup"])
def test_a_short_year_style_index_after_a_protected_name_is_still_a_label(name):
    assert pi.is_protected_field(name), name
    text = f'{{"{name}": "{VALUE}"}}'
    assert "labelled_tax_identifier" in pi.find_in_text(text) and VALUE not in pi.redact_text(text), text


def test_a_long_run_of_label_starts_is_scanned_in_linear_time():
    """`("ein_" * n) + "x"`: every `ein` starts a candidate label. n=8000 took ~5 s and n=32000 ~94 s before the trailing run was bounded."""
    import time
    times = {}
    for n in (4000, 16000, 64000):
        text = ("ein_" * n) + "x"
        t0 = time.process_time()
        pi.find_in_text(text)
        pi.redact_text(text)
        times[n] = time.process_time() - t0
    assert times[64000] < 5.0, times
    assert times[64000] < 40 * max(times[4000], 0.01), f"super-linear growth: {times}"


def test_more_than_eight_trailing_tokens_is_the_documented_limit():
    name = "EIN" + "_x" * 9
    assert pi.is_protected_field(name)
    assert pi.find_in_text(f"{name}: {VALUE}") == [], "stated limit of the bounded trailing run"
    assert "labelled_tax_identifier" in pi.find_in_text("EIN" + "_x" * 8 + f": {VALUE}")


# ── CodeRabbit on #232 (7534eb6): spaced quoted keys, index-vs-value, empty containers (reproduced before 1.5.0) ─────────

@pytest.mark.parametrize("name", ["EIN PRIMARY", "TIN BACKUP", "provider tin id", "FEIN secondary 2"])
def test_a_quoted_serialized_key_with_spaces_is_a_label(name):
    assert pi.is_protected_field(name), name
    for text in (f'{{"{name}":"{VALUE}"}}', f'{{"{name}": {VALUE}}}', f"{{'{name}': '{VALUE}'}}"):
        assert "labelled_tax_identifier" in pi.find_in_text(text), text
        assert VALUE not in pi.redact_text(text), text
    assert pi.redact_mapping({name: VALUE}) == {name: pi.REDACTED}


@pytest.mark.parametrize("name", ["EIN PRIMARY", "tax id"])
async def test_the_gateway_refuses_a_spaced_quoted_key(name, monkeypatch):
    from app.tefca_registry.ai.gateway import TEFCAAIGateway
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-not-a-key"); monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-key")
    sent = []

    async def record(self, model, prompt, max_output, temperature):
        sent.append(prompt)
        return '{"synthetic": true}'

    monkeypatch.setattr(TEFCAAIGateway, "_call_anthropic", record); monkeypatch.setattr(TEFCAAIGateway, "_call_openai", record)
    assert await TEFCAAIGateway().call(f'Compare SYNTHETIC ORG {{"{name}": "{VALUE}"}} with SYNTHETIC ORG LLC') is None and sent == []


@pytest.mark.parametrize("name", ["synthetic note", "einstein primary", "the tax rate", "protein x"])
def test_quoted_keys_that_are_not_protected_names_stay_unmatched(name):
    assert pi.find_in_text(f'{{"{name}": "{VALUE}"}}') == [], name


@pytest.mark.parametrize("text", ["EIN_2 000000001", "EIN_2: 000000001", "EIN_2026 000000001", "tax_id_2 000000001"])
def test_an_index_followed_by_a_complete_value_is_a_label_guard(text):
    """Reported against an older commit; not reproducible on the current head; kept as a guard."""
    assert "labelled_tax_identifier" in pi.find_in_text(text) and VALUE not in pi.redact_text(text)


def test_empty_protected_containers_are_fresh_copies_not_the_callers_objects():
    source = {"EIN": [], "TIN": {}, "FEIN": "", "tax_id": None}
    out = pi.redact_mapping(source)
    assert out["EIN"] == [] and out["TIN"] == {} and out["FEIN"] == "" and out["tax_id"] is None
    assert out["EIN"] is not source["EIN"] and out["TIN"] is not source["TIN"]
    out["EIN"].append("000000001"); out["TIN"]["x"] = "000000001"
    assert source == {"EIN": [], "TIN": {}, "FEIN": "", "tax_id": None}, "writing to the result must not write to the source"


# CodeRabbit Major on #233 (reproduced): `_SPLIT` (the field tokenizer) accepts ANY non-alphanumeric separator, so
# `tax|id` and `tax:id` are protected field names, but the text label recognised only whitespace _ - . /.
PIPED_OR_COLONED = ["tax|id", "tax:id", "TAX|ID", "hco|tin", "org:ein", "ein|primary", "ein:primary", "tax|id|primary",
                    "taxpayer:id", "employer|identification|number"]


@pytest.mark.parametrize("name", PIPED_OR_COLONED)
def test_pipe_and_colon_separated_field_names_label_their_value_in_text(name):
    assert pi.is_protected_field(name), name
    for text in (f"{name}={VALUE}", f"{name}: {VALUE}", f'"{name}":"{VALUE}"', f"{name}|{VALUE}"):
        assert "labelled_tax_identifier" in pi.find_in_text(text), text
        redacted = pi.redact_text(text)
        assert VALUE not in redacted and pi.REDACTED in redacted, (text, redacted)


@pytest.mark.parametrize("name", ["protein|primary", "einstein:primary", "taxonomy|code", "listing:id", "tax|rate"])
def test_pipe_and_colon_do_not_widen_whole_token_boundaries(name):
    assert not pi.is_protected_field(name), name
    assert pi.find_in_text(f"{name}={VALUE}") == [], name
