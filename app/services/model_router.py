"""
Complexity-Based Model Router
Uses heuristics to determine whether a request needs Sonnet (complex) or Haiku (simple).
This avoids calling the expensive model for simple tasks.
"""

import logging

logger = logging.getLogger("docuaction.router")

# Complexity thresholds
SIMPLE_WORD_LIMIT = 3000    # Documents under this → simple
COMPLEX_INDICATORS = [
    'whereas', 'pursuant', 'notwithstanding',  # Legal
    'compliance', 'regulation', 'audit',         # Regulatory
    'multi-year', 'fiscal year', 'budget',       # Financial
    'committee', 'board', 'resolution',          # Governance
    'whereas', 'therefore', 'resolved',          # Formal
    'amendment', 'section', 'subsection',        # Legal structure
    'plaintiff', 'defendant', 'jurisdiction',    # Legal proceedings
]

# Action types that always need Sonnet
COMPLEX_ACTIONS = {'brief', 'insights'}
# Action types that can use Haiku
SIMPLE_ACTIONS = {'summary', 'email'}


def classify_complexity(text: str, action_type: str) -> str:
    """
    Classify request complexity.
    
    Returns: "simple" or "complex"
    
    Logic:
    - Brief and insights always → complex (Sonnet)
    - Short documents with simple actions → simple (Haiku)
    - Documents with legal/regulatory language → complex (Sonnet)
    - Long documents → complex (Sonnet)
    - Multi-speaker transcripts → complex (Sonnet)
    """
    # Action type override
    if action_type in COMPLEX_ACTIONS:
        logger.debug(f"Complex: action_type={action_type} requires Sonnet")
        return "complex"

    word_count = len(text.split())

    # Very short documents → simple
    if word_count < 500 and action_type in SIMPLE_ACTIONS:
        logger.debug(f"Simple: {word_count} words, action={action_type}")
        return "simple"

    # Long documents → complex
    if word_count > 10000:
        logger.debug(f"Complex: {word_count} words exceeds threshold")
        return "complex"

    # Check for complexity indicators
    text_lower = text.lower()
    indicator_count = sum(1 for ind in COMPLEX_INDICATORS if ind in text_lower)

    if indicator_count >= 3:
        logger.debug(f"Complex: {indicator_count} complexity indicators found")
        return "complex"

    # Multi-speaker detection (transcripts)
    speaker_patterns = ['speaker 1', 'speaker 2', 'speaker a', 'speaker b',
                        '[speaker', 'participant:', 'moderator:']
    speaker_count = sum(1 for p in speaker_patterns if p in text_lower)
    if speaker_count >= 2:
        logger.debug(f"Complex: multi-speaker transcript detected")
        return "complex"

    # Actions type with moderate length → could go either way
    if word_count > SIMPLE_WORD_LIMIT:
        logger.debug(f"Complex: {word_count} words above simple limit")
        return "complex"

    logger.debug(f"Simple: {word_count} words, {indicator_count} indicators, action={action_type}")
    return "simple"
