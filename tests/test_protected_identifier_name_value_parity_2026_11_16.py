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
