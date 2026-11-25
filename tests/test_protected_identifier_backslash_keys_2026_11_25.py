"""Quoted keys with backslashes (protected-identifier parser 1.6.1). Synthetic values only.

The quoted-key scanner used to treat a quote as escaped whenever the ONE character before it was a backslash and skip
it. `json.dumps` writes a key that ends in a backslash as `"EIN PRIMARY\\\\"`: the closing quote follows a backslash, was
skipped, and the value after it was neither found nor redacted, so the AI-prompt guard did not refuse the text.

A quote after a backslash is now AMBIGUOUS: it is tried as the closer and the scan goes on to the next one. That covers
JSON, JSON carried inside a JSON string, and serializations where a backslash is literal - without assuming which one
the text is.
"""
from __future__ import annotations

import json
import time

import pytest

from app.Tefca import protected_identifiers as pi
from app.Tefca.protected_identifiers import REDACTED, find_in_text, is_protected_field, redact_text
from test_protected_identifier_name_value_parity_2026_11_16 import _assert_linear

BS = "\\"
PROTECTED_KEYS = ["EIN PRIMARY", "Tax ID owner", "ein owner's copy", "Tax Identifier", "employer identification number"]
OTHER_KEYS = ["NPI PRIMARY", "name", "zip code", "taxonomy code", "routing note"]
VALUES = ["000000001", "12-3456789", "12 3456789"]


def _refused(text: str) -> bool:
    try:
        pi.assert_safe_for_ai(text)
    except Exception:
        return True
    return False


def _digits(value: str) -> str:
    return value.replace("-", "").replace(" ", "")[-7:]


def _cases(keys, values=VALUES):
    out = []
    for key in keys:
        for n in range(5):
            for inner in ("", ' "x" '):
                for value in values:
                    out.append((key + inner + BS * n, value))
    return out


@pytest.mark.parametrize("key,value", _cases(PROTECTED_KEYS))
def test_a_protected_key_is_found_whatever_backslashes_it_ends_in(key, value):
    assert is_protected_field(key), key
    text = json.dumps({key: value})
    assert find_in_text(text), text
    out = redact_text(text)
    assert _digits(value) not in out and REDACTED in out, (text, out)
    assert _refused(text), text


#: The written EIN shape NN-NNNNNNN is flagged with no label at all (a separate, older rule), so it cannot show that a
#: key was NOT taken for a protected one.
@pytest.mark.parametrize("key,value", _cases(OTHER_KEYS, ["000000001", "12 3456789"]))
def test_a_key_the_field_rule_does_not_protect_stays_unflagged(key, value):
    assert not is_protected_field(key), key
    text = json.dumps({key: value})
    assert find_in_text(text) == [], text
    assert redact_text(text) == text
    assert not _refused(text), text


def test_the_reported_inputs():
    for text in ('{"EIN PRIMARY' + BS * 2 + '": "000000001"}', '{"Tax ID owner' + BS * 2 + '": "12 3456789"}',
                 '{"EIN PRIMARY' + BS * 4 + '": "000000001"}'):
        assert find_in_text(text), text
        assert REDACTED in redact_text(text) and _refused(text), text


@pytest.mark.parametrize("key", PROTECTED_KEYS)
def test_json_carried_inside_a_json_string_is_found(key):
    """Every quote of the inner document is written `\\"`; none of them is 'unescaped'."""
    text = json.dumps(json.dumps({key: "000000001"}))
    assert find_in_text(text), text
    assert "000000001" not in redact_text(text) and _refused(text), text
    twice = json.dumps(text)
    assert find_in_text(twice) and "000000001" not in redact_text(twice), twice


def test_a_literal_backslash_before_the_closing_quote_is_not_an_escape():
    """YAML single-quoted keys, SQL strings, Windows paths: the backslash is content and the quote closes the key."""
    for text in ("'EIN PRIMARY" + BS + "': \"000000001\"", "\"EIN PRIMARY" + BS + "\": 000000001",
                 "'Tax ID owner" + BS * 3 + "' = '12-3456789'"):
        assert find_in_text(text), text
        assert REDACTED in redact_text(text), text


def test_what_the_old_rule_found_is_still_found():
    """An escaped quote INSIDE the key, and an even run of backslashes before a quote that does not close it."""
    for text in ('{"EIN ' + BS + '"primary' + BS + '" copy": "000000001"}',
                 '"EIN' + BS * 2 + '" x": "000000001"',
                 '{"EIN owner' + "'" + 's copy": "000000001"}', '"EIN PRIMARY' + "'" + ': 000000001'):
        assert find_in_text(text), text
        assert "000000001" not in redact_text(text), text


@pytest.mark.parametrize("text", [
    '{"EIN note": "see C:' + BS * 2 + '", "zip": "123456789"}',
    '{"path": "C:' + BS * 2 + 'EIN' + BS * 2 + '", "zip": "123456789"}',
    '{"NPI PRIMARY' + BS * 2 + '": "000000001"}',
    json.dumps(json.dumps({"NPI owner": "000000001", "zip code": "123456789"})),
])
def test_backslashes_do_not_make_an_unprotected_value_a_hit(text):
    assert find_in_text(text) == [], text
    assert redact_text(text) == text


def test_trying_ambiguous_quotes_keeps_the_scan_linear():
    started = time.perf_counter()
    for text in ((BS + '"') * 200_000 + ' "EIN PRIMARY' + BS * 2 + '": "000000001"',
                 ('"' + BS * 79) * 20_000 + ' "EIN PRIMARY' + BS * 2 + '": "000000001"'):
        assert find_in_text(text) and "000000001" not in redact_text(text)
    assert time.perf_counter() - started < 20.0
    _assert_linear(lambda n: ((BS + '"') * 40) * n, [80, 160, 320])
    _assert_linear(lambda n: ('"' + BS * 79) * n, [80, 160, 320])


def test_the_parser_version_names_this_change():
    assert pi.PROTECTED_IDENTIFIERS_VERSION == "1.6.1"
