"""CodeRabbit round 5 (4581490304): `EIN_a_b_c_d_e_f_g_h_i:000000001` - the long-name parity pass consumed the value.

THE COMMON CAUSE of three successive defects in the text label scan (the 1.4.1 bound, the 1.5.1 separators, this): the text
scanner re-implements the FIELD rule's tokenizer by pattern, so each input shape the field rule accepts but the pattern does
not is found one report at a time. The parity oracle is `is_protected_field`: whatever name it protects must, when a
value follows it in ANY of the serializations below, be detected by `find_in_text` and removed by `redact_text` (label
kept). This test enumerates name shapes x connectors x value shapes with a fixed seed instead of one input at a time.
Synthetic values only (000000001 / 12-3456789 / 123-45-6789 are not real identifiers)."""
import random
import time

import pytest

from app.Tefca.protected_identifiers import REDACTED, find_in_text, is_protected_field, redact_text

pytestmark = pytest.mark.regression

JOIN = ["_", "-", ".", "/", "|", ":"]
SEEDS = ["EIN", "ein", "TIN", "FEIN", "ITIN", "tax_id", "TaxID", "taxId", "tax.id", "tax-number", "taxpayer_id",
         "employer_identification", "employer.identification.number", "irs_no"]
WORDS = ["a", "b", "primary", "org", "hco", "x", "legal", "entity", "2", "2026", "unit", "n", "col"]
VALUES = ["000000001", "12-3456789", "123-45-6789"]
# how a serializer may attach the value to a name: joined directly, or through punctuation / padding
CONNECTORS = [":", "=", ": ", " = ", "_", "-", '":"', "': '", "=\"", " ", ".", "/", "|"]


def _names(rng):
    out = []
    for n_tail in (0, 1, 3, 7, 8, 9, 12, 20, 60):
        for seed in SEEDS:
            tail = "".join(rng.choice(JOIN) + rng.choice(WORDS) for _ in range(n_tail))
            out.append(seed + tail)
    return out


def _cases():
    rng = random.Random(20261116)
    cases = []
    for name in _names(rng):
        if not is_protected_field(name):
            continue
        for conn in CONNECTORS:
            cases.append((name, conn, rng.choice(VALUES)))
    return cases


CASES = _cases()


def test_the_case_set_is_not_vacuous():
    assert len(CASES) > 800 and any(len(n) > 60 for n, _c, _v in CASES)


@pytest.mark.parametrize("name,conn,value", CASES)
def test_every_name_the_field_rule_protects_is_detected_and_redacted_in_text(name, conn, value):
    text = f"x {name}{conn}{value} y"
    assert find_in_text(text), text
    out = redact_text(text)
    assert value not in out and REDACTED in out, (text, out)
    assert name in out or name.lower() in out.lower(), (text, out)        # the label is kept, only the value is replaced


def test_the_exact_reported_input_and_its_joiner_variants():
    for joiner in (":", "_", "-", ".", "/", "|", "="):
        text = f"EIN_a_b_c_d_e_f_g_h_i{joiner}000000001"
        assert find_in_text(text), text
        assert "000000001" not in redact_text(text), text


@pytest.mark.parametrize("name", ["routing_a_b_c_d_e_f_g_h_i", "taxonomy_code_a_b_c_d_e_f_g_h", "protein_a_b_c_d_e_f_g_h_i",
                                  "einstein_a_b_c_d_e_f_g_h_i", "listing_a_b_c_d_e_f_g_h_i", "tax_a_b_c_d_e_f_g_h_i"])
def test_names_the_field_rule_does_not_protect_are_not_labels(name):
    assert not is_protected_field(name)
    text = f"{name}:000000001"
    assert not find_in_text(text) and redact_text(text) == text


def test_a_long_run_stays_linear_with_a_value_attached():
    started = time.perf_counter()
    text = "EIN" + "_a" * 100_000 + ":000000001"
    assert find_in_text(text) and "000000001" not in redact_text(text)
    assert time.perf_counter() - started < 5.0


# ═══ Round 6 (owner instruction 2026-11-16): ONE name parser; quoted names, opposite quotes, punctuation, joined values ═══
# CodeRabbit 4581709510 (opposite quote character inside a quoted key), 4581709517 (terminal punctuation after a joined
# value), 4581490304 (long-name parity). The oracle stays `is_protected_field`: the text scanner must not parse names by a
# second implementation, so every shape below is generated from the field rule's own vocabulary.
PUNCT = [".", ",", ";", ")", "]", "}", "!", "?", "\"", "'", ">"]
KEY_NAMES = ["EIN", "EIN PRIMARY", "ein owner's copy", "tax id", "Tax Identifier", "TAX_ID", "employer identification number",
             "FEIN \"legal\" copy", "taxpayer id", "org tin", "irs number"]
VALUE_QUOTES = ["", "\"", "'"]
KEY_QUOTES = ["\"", "'"]


def _keyed_cases():
    out = []
    for name in KEY_NAMES:
        assert is_protected_field(name), name
        for kq in KEY_QUOTES:
            if kq in name:
                shown = name.replace(kq, "\\" + kq)         # a serialized escape of the key's own quote
            else:
                shown = name                                  # the OPPOSITE quote character appears raw inside the key
            for sep in (": ", ":", "=", " = ", ":\t"):
                for vq in VALUE_QUOTES:
                    out.append(f"{{{kq}{shown}{kq}{sep}{vq}000000001{vq}}}")
    return out


KEYED = _keyed_cases()


@pytest.mark.parametrize("text", KEYED)
def test_a_quoted_key_is_judged_by_the_field_rule_with_either_quote_character(text):
    assert find_in_text(text), text
    out = redact_text(text)
    assert "000000001" not in out and REDACTED in out, (text, out)


def test_the_exact_reported_examples():
    exact = [
        '{"EIN owner\'s copy": "000000001"}',                           # 4581709510
        "EIN_a_b_c_d_e_f_g_h_i:000000001.",                             # 4581709517
        "EIN_a_b_c_d_e_f_g_h_i:000000001",                              # 4581490304
        '"EIN PRIMARY": "000000001"',
        "tax|id=000000001", "tax:id=000000001", "EIN000000001", "EIN_2 000000001", "EIN_2026 000000001",
        "TAX___ID: 000000001", "orgTaxId: 000000001",
    ]
    for text in exact:
        assert find_in_text(text), text
        out = redact_text(text)
        assert "000000001" not in out and REDACTED in out, (text, out)


@pytest.mark.parametrize("punct", PUNCT)
@pytest.mark.parametrize("name", ["EIN", "EIN_a_b_c_d_e_f_g_h_i", "TAX_ID", "taxpayer_identification_number_x_y_z_1_2_3"])
@pytest.mark.parametrize("conn", [":", "=", "_", "-", ".", "/", "|"])
def test_terminal_punctuation_never_hides_a_joined_value(punct, name, conn):
    text = f"{name}{conn}000000001{punct} tail"
    assert find_in_text(text), text
    out = redact_text(text)
    assert "000000001" not in out and REDACTED in out and out.endswith(f"{punct} tail"), (text, out)


@pytest.mark.parametrize("name", ["EIN", "tin", "FEIN", "ITIN", "tax_id", "taxId", "EIN_a_b_c_d_e_f_g_h_i"])
def test_a_value_joined_straight_onto_the_name_is_found(name):
    for text in (f"{name}000000001", f"{name}12-3456789", f"x {name}000000001 y"):
        if is_protected_field(name):
            assert find_in_text(text), text
            assert "000000001" not in redact_text(text) and "3456789" not in redact_text(text), text


@pytest.mark.parametrize("text", [
    "tax: id 000000001", "Tax: ID 000000001", "routing_a_b_c_d_e_f_g_h_i:000000001.",
    '{"NPI owner\'s copy": "000000001"}', '{"zip owner\'s copy": "000000001"}', "{'taxonomy code': '000000001'}",
    "'EIN\" ", "protein_a_b_c_d_e_f_g_h_i|000000001", "einstein: 000000001",
])
def test_deliberate_non_matches_are_kept(text):
    assert find_in_text(text) == [], text
    assert redact_text(text) == text


def test_mismatched_quotes_around_a_label_are_still_a_label():
    for text in ("\"EIN': 000000001", "'EIN\": 000000001", "`EIN`: 000000001"):
        assert find_in_text(text), text
        assert "000000001" not in redact_text(text), text


def _timed(make, n):
    text = make(n)
    started = time.perf_counter()
    find_in_text(text)
    redact_text(text)
    return time.perf_counter() - started


def _assert_linear(make, small, big):
    """Doubling the input must roughly double the time (a quadratic scan would quadruple it), and stay inside a fixed budget."""
    _timed(make, small)                                       # warm up
    t_small, t_big = _timed(make, small), _timed(make, big)
    assert t_big < 8.0, t_big
    assert t_big < 3.2 * max(t_small, 0.02) * (big / small) / 2 + 0.5, (t_small, t_big)


def test_quoted_key_scanning_is_linear():
    started = time.perf_counter()
    text = "\"" * 300_000 + "EIN\": \"000000001\"" + "'" * 300_000
    assert find_in_text(text) and "000000001" not in redact_text(text)
    assert time.perf_counter() - started < 8.0
    _assert_linear(lambda n: ("{\"" + "ein_x " * 12 + "\"") * n, 2_000, 4_000)


def test_a_run_of_values_inside_one_long_chain_is_linear():
    text = "EIN" + "_000000001" * 50_000
    started = time.perf_counter()
    assert find_in_text(text)
    out = redact_text(text)
    assert "000000001" not in out
    assert time.perf_counter() - started < 8.0
    _assert_linear(lambda n: "EIN" + "_000000001" * n, 20_000, 40_000)


# ── Round 6 (4581709517 / 4581709510): terminal punctuation after the value; the OTHER quote inside a quoted key ──────
TERMINATORS = ["", ".", ",", ";", ")", "]", "}", "'", '"', "\n", ".\n", "!)", "..", ")."]


def _long_name_cases():
    rng = random.Random(20261117)
    cases = []
    for name in _names(rng):
        if is_protected_field(name) and len(name) > 8:
            for conn in (":", "_", "=", "-", " = ", "\":\""):
                for term in TERMINATORS:
                    cases.append((name, conn, rng.choice(VALUES), term))
    return cases


TERM_CASES = _long_name_cases()


@pytest.mark.parametrize("name,conn,value,term", TERM_CASES)
def test_a_value_followed_by_terminal_punctuation_is_still_detected_and_redacted(name, conn, value, term):
    text = f"x {name}{conn}{value}{term} y"
    assert find_in_text(text), text
    out = redact_text(text)
    assert value not in out and REDACTED in out, (text, out)
    assert out.endswith(f"{term} y"), (text, out)                       # the punctuation after the value is preserved


QUOTED_KEYS = ["EIN'S", "EIN\"X", "tax_id'", "FEIN\"", "ORG'S_EIN", "employer'identification\"number", "a'b\"EIN"]


@pytest.mark.parametrize("key", QUOTED_KEYS)
@pytest.mark.parametrize("kq,vq", [('"', '"'), ("'", "'"), ('"', "'"), ("'", '"'), ('"', ""), ("'", "")])
def test_a_quoted_key_containing_the_other_quote_character_is_judged_by_the_field_rule(key, kq, vq):
    if kq in key:                                         # a key cannot contain the quote that delimits it
        pytest.skip("key contains its own delimiter")
    protected = is_protected_field(key)
    text = f"{{{kq}{key}{kq}: {vq}000000001{vq}}}"
    assert bool(find_in_text(text)) is protected, (text, protected)
    out = redact_text(text)
    assert ("000000001" not in out) is protected, (text, out)
