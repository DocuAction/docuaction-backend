"""
DocuAction AI — Case Management
PHI De-identification for third-party AI egress (finding DP-02)

WHAT THIS DOES
    Strips the patient's DIRECT identifiers from prompt text before it is sent to
    the Anthropic API, and restores them in the generated output. Replacement is
    EXACT-VALUE, not pattern-based: the caller already knows the real values from
    patient_context, so we substitute those specific strings rather than guessing
    with regex. That makes it reliable — no false negatives on unusual formats and
    no false positives mangling clinical vocabulary.

WHAT THIS DOES *NOT* DO — read before relying on it
    The clinical narrative (symptoms, diagnoses, medications, lab values, the voice
    transcript itself) is STILL SENT to Anthropic in full. It cannot be masked: it
    is the input the note is generated from, and redacting it would destroy the
    feature. Under HIPAA that narrative remains PHI even with the name removed.

    So this module is DATA MINIMIZATION / DEFENCE IN DEPTH. It does not de-identify
    under the §164.514(b)(2) Safe Harbor and it does not close DP-02. The
    controlling safeguard for clinical content is a signed BAA plus zero-retention
    confirmation with Anthropic — contractual, not code. See
    docs/compliance/AI_EGRESS_PHI.md.

    Also NOT removed (values not present in patient_context, so exact-value
    replacement cannot see them):
      - third-party names spoken in the transcript ("her daughter Emily")
      - provider and facility names ("Dr. Reyes", "Austin Regional Clinic")
      - identifiers in a format other than the one stored (a DOB stored as
        "1951-03-14" but dictated as "March 14th 1951")

KNOWN OVER-REDACTION — accepted trade-off, verified behaviour
    Matching is case-INSENSITIVE, so a surname that collides with clinical
    vocabulary is also replaced where it is used clinically. A patient named
    Stone turns "kidney stone" into "kidney [PATIENT_LAST]" in the prompt, which
    can make the generated note subtly wrong. Other colliding surnames: Rash,
    Long, Short, Gray, Bell, Cross, Marsh, Back, Head.

    This direction was chosen deliberately. Over-redaction is VISIBLE to the
    clinician at the mandatory review gate (every note in this module is returned
    requires_review and cannot be signed unreviewed), whereas a PHI leak to a
    third party is invisible and irreversible. Privacy-first plus human review
    beats the alternative.

    If a deployment would rather accept the leak risk than the accuracy risk,
    make _pattern_for() case-SENSITIVE for TOKEN_PATIENT_FIRST / _LAST only:
    names are capitalised in both structured fields and dictation, while the
    clinical usage is lower-case. Do not make MRN/DOB/SSN/phone case-sensitive —
    there is no upside and it only creates misses.

WHY NOT app/services/pii_masking.py
    That module is regex-based and built for financial/contract documents. Measured
    against the actual prompt these engines build, it redacts ZERO items: it has no
    name pattern at all (Safe Harbor identifier #1) and its DOB pattern requires a
    keyword prefix plus MM/DD/YYYY, so it misses the bare ISO dates these engines
    interpolate. It is left untouched here because ai_engine.py depends on it.
"""

import re
import logging
from typing import Dict, Tuple

logger = logging.getLogger("docuaction.case_management.phi")

# Tokens the model sees in place of the real values. Chosen to read as obvious
# placeholders so a clinician reviewing an un-restored note spots them immediately.
TOKEN_PATIENT_FIRST = "[PATIENT_FIRST]"
TOKEN_PATIENT_LAST = "[PATIENT_LAST]"
TOKEN_PATIENT_FULL = "[PATIENT]"
TOKEN_MRN = "[MRN]"
TOKEN_DOB = "[DOB]"
TOKEN_SSN = "[SSN]"
TOKEN_PHONE = "[PHONE]"

# A value shorter than this is not substituted. Guards against a 2-character
# first name ("Al", "Jo") matching inside ordinary clinical words and corrupting
# the narrative — the redaction must never be able to damage clinical meaning.
_MIN_VALUE_LEN = 3

# patient_context field -> token. Ordering here does not matter; redact() sorts by
# value length descending so "Sarah Johnson" is consumed before "Johnson".
_FIELD_TOKENS = (
    ("mrn", TOKEN_MRN),
    ("date_of_birth", TOKEN_DOB),
    ("ssn", TOKEN_SSN),
    ("phone", TOKEN_PHONE),
    ("first_name", TOKEN_PATIENT_FIRST),
    ("last_name", TOKEN_PATIENT_LAST),
)


def build_phi_map(patient_context: dict) -> Dict[str, str]:
    """
    Build {real_value: token} from the identifiers present in patient_context.

    Returns an empty dict when patient_context is None/empty or holds no usable
    identifier — callers can pass the result through unconditionally and get a
    no-op rather than having to branch.
    """
    if not patient_context:
        return {}

    phi_map: Dict[str, str] = {}

    first = str(patient_context.get("first_name") or "").strip()
    last = str(patient_context.get("last_name") or "").strip()

    # Full name first: the engines interpolate "{first} {last}", and mapping the
    # combined form to a single token keeps the prompt readable for the model
    # (one placeholder for the patient rather than two adjacent ones).
    if len(first) >= _MIN_VALUE_LEN and len(last) >= _MIN_VALUE_LEN:
        phi_map[f"{first} {last}"] = TOKEN_PATIENT_FULL

    for field, token in _FIELD_TOKENS:
        value = str(patient_context.get(field) or "").strip()
        if len(value) >= _MIN_VALUE_LEN and value not in phi_map:
            phi_map[value] = token

    return phi_map


def _pattern_for(value: str) -> re.Pattern:
    """
    Case-insensitive matcher for one exact value.

    Word boundaries are applied only when the value starts/ends with a word
    character — an MRN like "4478812" gets them, a phone number like
    "(512) 555-0143" does not, since \\b next to punctuation would never match.
    """
    escaped = re.escape(value)
    prefix = r"\b" if value[:1].isalnum() or value[:1] == "_" else ""
    suffix = r"\b" if value[-1:].isalnum() or value[-1:] == "_" else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def redact(text: str, phi_map: Dict[str, str]) -> Tuple[str, int]:
    """
    Replace every occurrence of each known identifier with its token.

    Longest value first, so "Sarah Johnson" is replaced before "Johnson" can
    partially consume it and leave "[PATIENT_FIRST] [PATIENT_LAST]" fragments
    interleaved with real text.

    Returns (redacted_text, distinct_identifiers_replaced).
    """
    if not text or not phi_map:
        return text, 0

    redacted = text
    replaced = 0
    for value in sorted(phi_map, key=len, reverse=True):
        pattern = _pattern_for(value)
        redacted, n = pattern.subn(phi_map[value], redacted)
        if n:
            replaced += 1
    return redacted, replaced


def restore(text: str, phi_map: Dict[str, str]) -> str:
    """
    Substitute the real values back for their tokens in generated output.

    Token -> value is applied longest-token-first so [PATIENT_FIRST] is not
    partially matched by a shorter token. If the model paraphrased instead of
    echoing a token, nothing is restored for it and the note simply reads
    "the patient" — acceptable, and every note in this module is already
    flagged requires_review before it can be signed.
    """
    if not text or not phi_map:
        return text

    token_to_value = {token: value for value, token in phi_map.items()}
    restored = text
    for token in sorted(token_to_value, key=len, reverse=True):
        restored = restored.replace(token, token_to_value[token])
    return restored


def log_masked(count: int, context: str = "") -> None:
    """
    Record HOW MANY identifiers were masked — never which, and never their values.
    Logging the values here would recreate the PHI-in-logs problem (finding DP-01)
    at the exact point we are trying to remove it.
    """
    if count:
        logger.info(
            "phi_identifiers_masked: %d%s", count, f" ({context})" if context else ""
        )
