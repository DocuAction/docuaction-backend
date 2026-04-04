"""
PII Masking Service
Detects and redacts sensitive data BEFORE any content reaches the AI model.
The AI generates outputs from the redacted version only.
"""

import re
import logging
from typing import Tuple

logger = logging.getLogger("docuaction.pii")

# ═══ PII PATTERNS ═══

PATTERNS = [
    # SSN: 123-45-6789 or 123456789
    (r'\b\d{3}-\d{2}-\d{4}\b', '[SSN_REDACTED]', 'SSN'),
    (r'\b\d{9}\b(?=\s|$|[.,;])', '[SSN_REDACTED]', 'SSN'),

    # Credit card: 16 digits with optional separators
    (r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b', '[CC_REDACTED]', 'CREDIT_CARD'),

    # Phone: various US formats
    (r'\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE_REDACTED]', 'PHONE'),

    # Email addresses
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REDACTED]', 'EMAIL'),

    # Date of birth patterns (DOB: MM/DD/YYYY)
    (r'\b(?:DOB|Date of Birth|Born)[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', '[DOB_REDACTED]', 'DOB'),

    # Medical Record Number (MRN)
    (r'\b(?:MRN|Medical Record)[:\s#]*\d{6,10}\b', '[MRN_REDACTED]', 'MRN'),

    # Driver's License
    (r'\b(?:DL|Driver.?s?\s*License)[:\s#]*[A-Z0-9]{6,12}\b', '[DL_REDACTED]', 'DRIVERS_LICENSE'),

    # Bank account numbers (8-17 digits preceded by account keywords)
    (r'\b(?:account|acct)[:\s#]*\d{8,17}\b', '[ACCOUNT_REDACTED]', 'BANK_ACCOUNT'),

    # Routing numbers
    (r'\b(?:routing|ABA)[:\s#]*\d{9}\b', '[ROUTING_REDACTED]', 'ROUTING'),

    # IP addresses
    (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP_REDACTED]', 'IP_ADDRESS'),

    # Passport numbers
    (r'\b(?:passport)[:\s#]*[A-Z0-9]{6,9}\b', '[PASSPORT_REDACTED]', 'PASSPORT'),
]


def mask_pii(text: str, log_types: bool = True) -> Tuple[str, int]:
    """
    Scan text for PII patterns and replace with classification tokens.
    
    Returns:
        Tuple of (masked_text, count_of_items_redacted)
    """
    if not text:
        return text, 0

    masked = text
    total_count = 0
    type_counts = {}

    for pattern, replacement, pii_type in PATTERNS:
        matches = re.findall(pattern, masked, re.IGNORECASE)
        count = len(matches)
        if count > 0:
            masked = re.sub(pattern, replacement, masked, flags=re.IGNORECASE)
            total_count += count
            type_counts[pii_type] = type_counts.get(pii_type, 0) + count

    if total_count > 0 and log_types:
        logger.info(f"PII masked: {total_count} items — {type_counts}")

    return masked, total_count


def get_pii_report(text: str) -> dict:
    """
    Scan text and return a report of PII found without modifying it.
    Used for compliance dashboards.
    """
    report = {"total": 0, "types": {}, "locations": []}

    for pattern, _, pii_type in PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            report["total"] += 1
            report["types"][pii_type] = report["types"].get(pii_type, 0) + 1
            report["locations"].append({
                "type": pii_type,
                "start": match.start(),
                "end": match.end(),
                "length": match.end() - match.start(),
            })

    return report
