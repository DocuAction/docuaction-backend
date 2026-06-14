"""
DocuAction Bulletin Intelligence — Boolean Search Filter
Implements Appendix A (Boolean_search_AGT.docx) section-assignment logic.

Each FCC section has a Boolean expression supporting:
  - "quoted phrase"        → substring match (case-insensitive)
  - title:"phrase"         → match against title only
  - AND / OR               → logical operators
  - ( ... )                → grouping / precedence
  - bare tokens            → treated as OR'd phrase matches

This is the deterministic spine of section assignment. The LLM classifier
is only used as a tie-breaker / relevance scorer AFTER Boolean assignment,
never as the primary router. This guarantees the output matches the
official FCC Daily News Briefing taxonomy and prevents single-source
collapse (e.g. the all-"Radio World" bug).
"""

import re
from typing import List, Tuple

# ── 9 official FCC Daily News Briefing sections (Appendix A order) ─────────────
# Keys are the canonical section ids used throughout the engine.
FCC_SECTIONS = [
    "fcc_news",
    "consumers",
    "media_broadcasting",
    "space_policy",
    "public_safety",
    "wireless_spectrum",
    "ai_ml",
    "business_tech",
    "international",
]

FCC_SECTION_LABELS = {
    "fcc_news":           "FCC News",
    "consumers":          "Consumers",
    "media_broadcasting": "Media & Broadcasting",
    "space_policy":       "Space Policy",
    "public_safety":      "Public Safety / Cybersecurity / Privacy",
    "wireless_spectrum":  "Wireless & Spectrum",
    "ai_ml":              "Artificial Intelligence / Machine Learning",
    "business_tech":      "Business & Tech",
    "international":      "International",
    "other":              "Other",
}

# ── Boolean expressions transcribed from Appendix A ───────────────────────────
# Cleaned of the OCR artifacts (doubled "OR OR", smart quotes, "spoofing"
# typo as "spooﬁng"). Each expression is evaluated against title + summary.
FCC_BOOLEAN = {
    "fcc_news": (
        '("Brendan Carr" OR "Olivia Trusty" OR "Anna Gomez") '
        'OR (title:"FCC" OR title:"Federal Communications" OR title:"Federal Communications Commission" '
        'OR "FCC Chairman" OR "FCC Commissioner" OR "FCC Acting Chairman" '
        'OR "Federal Communications Commission Chairman" OR "Federal Communications Commission Commissioner") '
        'OR ("Enforcement" AND ("FCC" OR "Federal Communications Commission"))'
    ),
    "consumers": (
        'title:"TCPA" OR title:"robocalls" OR title:"robocall" OR title:"spoofing" '
        'OR title:"phone scam" OR title:"accessible communications" OR title:"deaf" '
        'OR title:"deaf-blind" OR title:"closed captioning" OR title:"video description services" '
        'OR title:"video relay" OR title:"autodialer" OR title:"caller ID" OR title:"cramming" '
        'OR "STIR-SHAKEN" OR "Robocall Mitigation Database" OR "auto warranty scam" '
        'OR "one ring scam" OR "robotexts" OR ("scam" AND "text") OR ("fraud" AND "text") '
        'OR "phone unlocking" OR "porting" OR "port out scam"'
    ),
    "media_broadcasting": (
        '("FCC" OR "Federal Communications Commission") AND '
        '("Media ownership" OR "cable merger" OR "cable company" OR "broadcast television" '
        'OR "broadcast station" OR "radio station" OR "radio license" OR "broadcast license" '
        'OR "profanity on the air" OR "satellite television" OR "broadcast tv" OR "satellite tv" '
        'OR "cable tv" OR "set-top box" OR "FM translator" OR "FM radio" OR "AM radio" '
        'OR ("tv" AND "rescan") OR ("antenna" AND "rescan") OR "calm act" OR "loud commercials")'
    ),
    "space_policy": (
        '("FCC" OR "Federal Communications Commission") AND '
        '("space" OR "satellite" OR "satellites" OR "GSO" OR "NGSO" OR "space economy" '
        'OR "ISAM" OR "In-Space Servicing Assembly Manufacturing" OR ("launch" AND "spectrum") '
        'OR "earth station" OR "space station" OR "space bureau" OR "starlink" '
        'OR "blue origin" OR "spacemobile" OR "intelsat" OR "orbital debris")'
    ),
    "public_safety": (
        '("FCC" OR "Federal Communications Commission") AND '
        '("911" OR "e911" OR "psap" OR "911 call center" OR "phone outage" OR "submarine cables" '
        'OR "cybersecurity" OR "outage reporting" OR "Data breach" OR "Emergency Alert System" '
        'OR "Wireless Emergency Alert" OR "emergency alert" OR "wireless alert" OR "Online privacy" '
        'OR "broadband privacy" OR "data sharing" OR "personally identifiable information")'
    ),
    "wireless_spectrum": (
        '("FCC" OR "Federal Communications Commission") AND '
        '("Broadband" OR "Connectivity" OR "Wireless" OR "spectrum" OR "mobile phones" '
        'OR "cell phones" OR "data services" OR "telecom" OR "telecommunications" '
        'OR "calling cards" OR "cell service" OR "communications policy" OR "signal interference" '
        'OR "cell tower" OR "5G" OR "small cells" OR "spectrum auction" OR "AWS-3")'
    ),
    "ai_ml": (
        '"generative ai" OR "agentic ai" OR "ai executive order" OR "ai in cybersecurity" '
        'OR "ai governance" OR "ai ethics" OR "ai risk management" OR "federal ai strategy" '
        'OR "responsible ai" OR "explainable ai" OR "ai regulation" OR "ai policy" '
        'OR "artificial intelligence" OR "machine learning" OR "ai and privacy" '
        'OR "national ai initiative" OR "white house ai" OR "ai telecommunications"'
    ),
    "business_tech": (
        '("FCC" OR "Federal Communications Commission") AND '
        '("Internet policy" OR "net neutrality" OR "open internet" OR "social media" '
        'OR "tech innovation" OR "silicon valley" OR "investors" OR "wall street" '
        'OR "online privacy" OR "web tracking" OR "throttling" OR "internet traffic" '
        'OR "telecom sector" OR "telecom jobs" OR "communications industry" OR "telecom industry")'
    ),
    "international": (
        '("FCC" OR "Federal Communications Commission") AND '
        '(("telecommunications" OR "telecom" OR "telecoms") AND '
        '("Europe" OR "Asia" OR "Africa" OR "Australia" OR "South America" OR "Central America" '
        'OR "Caribbean" OR "Scandinavia" OR "undersea cable" OR "subsea cable" '
        'OR "Submarine communications cable" OR ("treaty" AND ("internet" OR "broadband" OR "cables")) '
        'OR "International Telecommunication Union" OR "ITU" OR "World Radiocommunication Conference"))'
    ),
}

# Priority order for assignment when an article matches multiple sections.
# FCC News is a catch-all for agency-level stories, so it is evaluated LAST
# to avoid swallowing more specific sections.
SECTION_PRIORITY = [
    "space_policy",
    "public_safety",
    "ai_ml",
    "international",
    "consumers",
    "media_broadcasting",
    "wireless_spectrum",
    "business_tech",
    "fcc_news",
]


# ── Tokenizer ─────────────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(
    r'''\s*(?:
        (?P<lparen>\()|
        (?P<rparen>\))|
        (?P<op>\bAND\b|\bOR\b)|
        title:"(?P<title>[^"]*)"|
        "(?P<phrase>[^"]*)"|
        (?P<bare>[^\s()]+)
    )''',
    re.IGNORECASE | re.VERBOSE,
)


def _tokenize(expr: str):
    tokens = []
    for m in _TOKEN_RE.finditer(expr):
        if m.group("lparen"):
            tokens.append(("LP", "("))
        elif m.group("rparen"):
            tokens.append(("RP", ")"))
        elif m.group("op"):
            tokens.append(("OP", m.group("op").upper()))
        elif m.group("title") is not None:
            tokens.append(("TITLE", m.group("title").lower()))
        elif m.group("phrase") is not None:
            tokens.append(("PHRASE", m.group("phrase").lower()))
        elif m.group("bare"):
            b = m.group("bare").strip().lower()
            if b:
                tokens.append(("PHRASE", b))
    return tokens


# ── Recursive-descent parser → evaluator ──────────────────────────────────────
# Grammar:
#   expr   := term (OR term)*
#   term   := factor (AND factor)*
#   factor := "(" expr ")" | PHRASE | TITLE
class _Parser:
    def __init__(self, tokens, title_lc: str, body_lc: str):
        self.toks = tokens
        self.i = 0
        self.title = title_lc
        self.body = body_lc  # title + summary combined

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self) -> bool:
        if not self.toks:
            return False
        return self._expr()

    def _expr(self) -> bool:
        val = self._term()
        while self._peek() == ("OP", "OR"):
            self._next()
            rhs = self._term()
            val = val or rhs
        return val

    def _term(self) -> bool:
        val = self._factor()
        while self._peek() == ("OP", "AND"):
            self._next()
            rhs = self._factor()
            val = val and rhs
        # Implicit AND: two adjacent factors with no operator (defensive)
        while self._peek()[0] in ("PHRASE", "TITLE", "LP"):
            rhs = self._factor()
            val = val and rhs
        return val

    def _factor(self) -> bool:
        kind, value = self._peek()
        if kind == "LP":
            self._next()
            val = self._expr()
            if self._peek() == ("RP", ")"):
                self._next()
            return val
        if kind == "PHRASE":
            self._next()
            return value in self.body
        if kind == "TITLE":
            self._next()
            return value in self.title
        # Unexpected token — consume and treat as False
        if kind is not None:
            self._next()
        return False


def matches(section: str, title: str, summary: str) -> bool:
    """Return True if the article satisfies the section's Boolean expression."""
    expr = FCC_BOOLEAN.get(section)
    if not expr:
        return False
    title_lc = (title or "").lower()
    body_lc = f"{title_lc} {(summary or '').lower()}"
    tokens = _tokenize(expr)
    return _Parser(tokens, title_lc, body_lc).parse()


def assign_section(title: str, summary: str) -> Tuple[str, List[str]]:
    """
    Assign an article to exactly one FCC section using Boolean priority.
    Returns (section_id, all_matching_sections).
    Falls back to 'other' if nothing matches.
    """
    hits = [s for s in FCC_SECTIONS if matches(s, title, summary)]
    if not hits:
        return "other", []
    for s in SECTION_PRIORITY:
        if s in hits:
            return s, hits
    return hits[0], hits


def is_fcc_relevant(title: str, summary: str) -> bool:
    """True if the article matches ANY FCC section (used to drop noise)."""
    return any(matches(s, title, summary) for s in FCC_SECTIONS)
