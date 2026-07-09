"""
Presentation-layer PII masking (NIST 800-53 AC-3 / SC-28(1) / MP-6 — least-
privilege display of protected data).

This masks PII ONLY in API/UI responses. It NEVER touches stored data, never
changes the database, and does not alter which records are returned — it only
transforms how identifying fields are rendered for a given caller's role.

Business rule (unchanged): reviewers and above see full data; administrators see
full data. Masking therefore applies only to roles BELOW "reviewer". Because
every TEFCA endpoint already requires reviewer-minimum, this is a NO-OP for all
current TEFCA callers (zero regression) — it exists so that if a lower-privilege
context is ever granted read access, PII is automatically minimized rather than
exposed. Set PII_MASKING_ENABLED=false to disable entirely.

Example:  "John Smith" → "J*** S****"
"""
import os

# Roles permitted to see unmasked PII. Mirrors the review business rule
# (reviewer and above) plus the always-full administrative/oversight roles.
FULL_PII_ROLES = {
    "reviewer", "senior_analyst", "qalead", "program_manager",
    "admin", "cor", "operations",
}

PII_MASKING_ENABLED = os.getenv("PII_MASKING_ENABLED", "true").strip().lower() != "false"


def should_mask(role) -> bool:
    """True when the caller's role must receive masked PII."""
    if not PII_MASKING_ENABLED:
        return False
    return str(role or "").strip().lower() not in FULL_PII_ROLES


def mask_name(value) -> str:
    """Mask each whitespace-separated token to its initial + asterisks.
    'John Smith' -> 'J*** S****'. Empty/short values are handled gracefully."""
    if value is None:
        return value
    text = str(value)
    if not text.strip():
        return text
    out = []
    for token in text.split(" "):
        if not token:
            out.append(token)
        elif len(token) == 1:
            out.append(token)
        else:
            out.append(token[0] + ("*" * (len(token) - 1)))
    return " ".join(out)


def mask_npi(value) -> str:
    """Mask an NPI/identifier, revealing only the last 4 characters."""
    if value is None:
        return value
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return ("*" * (len(text) - 4)) + text[-4:]


def mask_generic(value) -> str:
    """Conservative fallback mask for free-form identifying strings."""
    if value is None:
        return value
    text = str(value)
    if len(text) <= 2:
        return "*" * len(text)
    return text[0] + ("*" * (len(text) - 1))


# Field-name → masker. Extendable without touching call sites.
_FIELD_MASKERS = {
    "entity_name": mask_name,
    "legal_name": mask_name,
    "organization_name": mask_name,
    "name": mask_name,
    "npi": mask_npi,
}


def mask_record(record: dict, role, fields=None) -> dict:
    """Return a shallow copy of `record` with PII fields masked for `role`.
    No-op (returns the record unchanged) for full-PII roles or when disabled.
    Only the named fields (default: the known PII fields present) are masked;
    all other fields — and the set of records — are untouched."""
    if not isinstance(record, dict) or not should_mask(role):
        return record
    target = fields if fields is not None else [k for k in _FIELD_MASKERS if k in record]
    if not target:
        return record
    masked = dict(record)
    for f in target:
        if f in masked and masked[f] is not None:
            masker = _FIELD_MASKERS.get(f, mask_generic)
            masked[f] = masker(masked[f])
    return masked


def mask_records(records, role, fields=None):
    """Apply mask_record across a list. No-op for full-PII roles."""
    if not should_mask(role) or not isinstance(records, list):
        return records
    return [mask_record(r, role, fields) for r in records]
