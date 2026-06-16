"""
DocuAction Bulletin Intelligence — Boolean Search Filter
FULLY IMPLEMENTS Appendix A (Boolean_search_AGT.docx)

9 FCC sections with complete Boolean expressions + additional search terms.
Every keyword from Appendix A is included — nothing omitted.
"""

import re
from typing import List, Tuple

# ── 9 official FCC Daily News Briefing sections (Appendix A order) ─────────────
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
    "fcc_news":           "General",
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

# ── Boolean expressions — COMPLETE from Appendix A ────────────────────────────
# Every single keyword and phrase from the client's document is included.

FCC_BOOLEAN = {

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: GENERAL (formerly "FCC News")
    # Appendix A: Commissioner names + FCC title terms + Search Terms list
    # ═══════════════════════════════════════════════════════════════════════════
    "fcc_news": (
        # Commissioner names (always match)
        '("Brendan Carr" OR "Olivia Trusty" OR "Anna Gomez") '
        # Title-based FCC terms
        'OR (title:"FCC" OR title:"Federal Communications" '
        'OR title:"Federal Communications Commission" '
        'OR "FCC Chairman" OR "FCC Commissioner" OR "FCC Acting Chairman" '
        'OR "Federal Communications Commission Chairman" '
        'OR "Federal Communications Commission Commissioner") '
        # Enforcement
        'OR ("Enforcement" AND ("FCC" OR "Federal Communications Commission")) '
        # Additional Search Terms from Appendix A
        'OR "Federal Communications Commission" OR "Federal Registrar" '
        'OR "Spectrum" OR "Wireless" OR "Broadband" '
        'OR "Mobile Phone" OR "Robocalls" OR "Spoofing" OR "Telehealth" '
        'OR "Wireless emergency alert" OR "Emergency alert" '
        'OR "5G" OR "Telemedicine" OR "Robotext" OR "Satellite"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: CONSUMERS
    # Appendix A: Full Boolean + Search Terms
    # ═══════════════════════════════════════════════════════════════════════════
    "consumers": (
        # Title-based terms
        'title:"TCPA" OR title:"robocalls" OR title:"robocall" '
        'OR title:"spoofing" OR title:"phone scam" '
        'OR title:"accessible communications" '
        'OR title:"deaf" OR title:"deaf-blind" '
        'OR title:"closed captioning" OR title:"video description services" '
        'OR title:"video relay" OR title:"autodialer" '
        'OR title:"caller ID" OR title:"cramming" '
        # Phrase terms
        'OR "STIR-SHAKEN" OR "Robocall Mitigation Database" '
        'OR "auto warranty scam" OR "one ring scam" OR "robotexts" '
        'OR ("scam" AND "text") OR ("fraud" AND "text") '
        'OR "phone unlocking" OR "porting" OR "port out scam" '
        # Additional Search Terms from Appendix A
        'OR "Disability rights" OR "Consumer education" '
        'OR "outreach to state" OR "outreach to local" OR "outreach to Tribal" '
        'OR "consumer protection" OR "consumer complaint"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: MEDIA & BROADCASTING
    # Appendix A: (FCC) AND (media terms) + cable/broadcast/radio/satellite
    # ═══════════════════════════════════════════════════════════════════════════
    "media_broadcasting": (
        '(("FCC" OR "Federal Communications Commission") AND '
        '("Media ownership" OR "cable merger" OR "cable company" '
        'OR "broadcast television" OR "broadcast station" '
        'OR "radio station" OR "radio license" OR "broadcast license" '
        'OR "profanity on the air" OR "satellite television" '
        'OR "broadcast tv" OR "satellite tv" OR "cable tv" '
        'OR "set-top box" OR "FM translator" OR "FM radio" OR "AM radio" '
        'OR ("tv" AND "rescan") OR ("antenna" AND "rescan") '
        'OR "calm act" OR "loud commercials")) '
        # Additional Search Terms from Appendix A
        'OR "Cable television" OR "broadcast television" '
        'OR "satellite services" OR "satellite radio"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: SPACE POLICY
    # Appendix A: (FCC) AND (space terms) + Top Sources + additional terms
    # ═══════════════════════════════════════════════════════════════════════════
    "space_policy": (
        '(("FCC" OR "Federal Communications Commission") AND '
        '("space" OR "satellite" OR "satellites" OR "GSO" OR "NGSO" '
        'OR "space economy" OR "ISAM" '
        'OR "In-Space Servicing Assembly Manufacturing" '
        'OR ("launch" AND "spectrum") '
        'OR "earth station" OR "space station" '
        'OR ("space bureau" AND "FCC"))) '
        # Top Sources from Appendix A
        'OR title:"blue origin" OR title:"starlink" '
        'OR title:"spacemobile" OR title:"intelsat" '
        # Top source content w/o FCC from Appendix A
        'OR "NASA" OR "Launch" OR "Rocket" '
        'OR "X-band" OR "E-band" OR "V-Band" OR "Ka-Band" OR "Ku-Band" OR "S-Band" '
        'OR "Direct to device" OR "Satellite to handset" '
        'OR "Cislunar communications" OR "Lunar communications" '
        'OR "Space policy" OR "Orbital debris" '
        'OR "LEO" OR "MEO" OR "GSO" OR "NGSO"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: PUBLIC SAFETY / CYBERSECURITY / PRIVACY
    # Appendix A: (FCC) AND (safety terms) + Search Terms
    # ═══════════════════════════════════════════════════════════════════════════
    "public_safety": (
        '(("FCC" OR "Federal Communications Commission") AND '
        '("911" OR "e911" OR "psap" OR "911 call center" '
        'OR "phone outage" OR "submarine cables" '
        'OR "cybersecurity" OR "outage reporting" OR "Data breach" '
        'OR "Emergency Alert System" OR "Wireless Emergency Alert" '
        'OR "emergency alert" OR "wireless alert" '
        'OR "Online privacy" OR "broadband privacy" '
        'OR "data sharing" OR "personally identifiable information")) '
        # Additional Search Terms from Appendix A
        'OR "First responder communications" '
        'OR "public safety communications" '
        'OR "undersea cable" OR "subsea cable"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: WIRELESS & SPECTRUM
    # Appendix A: (FCC) AND (wireless terms) + Search Terms
    # ═══════════════════════════════════════════════════════════════════════════
    "wireless_spectrum": (
        '(("FCC" OR "Federal Communications Commission") AND '
        '("Broadband" OR "Connectivity" OR "Wireless" OR "spectrum" '
        'OR "mobile phones" OR "cell phones" OR "data services" '
        'OR "telecom" OR "telecommunications" OR "calling cards" '
        'OR "cell service" OR "privacy" OR "communications policy" '
        'OR "signal interference" OR "cell tower" OR "5G" OR "small cells")) '
        # Additional Search Terms from Appendix A
        'OR "CBRS" OR "Citizens Broadband Radio Service" '
        'OR "midband spectrum" OR "C-band" OR "6 GHz" '
        'OR "millimeter wave" OR "NTN" OR "non-terrestrial network" '
        'OR "Microwave links" OR "Mobile broadband" '
        'OR "spectrum auction" OR "AWS-3" '
        'OR "tower registration" '
        'OR "Mobile Wireless Competition Report" OR "Spectrum Dashboard" '
        'OR "rip and replace" OR "rip-and-replace" '
        'OR "DOCSIS" OR "fiber broadband" OR "pole attachment"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: ARTIFICIAL INTELLIGENCE / MACHINE LEARNING
    # Appendix A: COMPLETE list of all AI search terms (hyphenated → spaced)
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_ml": (
        # Core AI terms
        '"generative ai" OR "agentic ai" OR "artificial intelligence" OR "machine learning" '
        # Executive orders / policy
        'OR "ai executive order" OR "ai executive orders" '
        # Cybersecurity
        'OR "ai in cybersecurity" '
        # Data management
        'OR "ai in data management" '
        # Bias / Ethics / Governance
        'OR "ai bias mitigation" OR "ai governance" OR "ai ethics" '
        'OR "ai risk management" OR "responsible ai" OR "explainable ai" '
        # Workforce
        'OR "ai workforce training" OR "ai in federal hiring" '
        'OR "ai hiring initiatives" OR "ai innovation fellows" '
        # Emergency / Safety / Law enforcement
        'OR "ai in emergency response" OR "ai and national security" '
        'OR "ai and public safety" OR "ai in law enforcement" '
        'OR "ai in disaster response" OR "ai for fraud detection" '
        # Federal agency
        'OR "federal agency ai" OR "government ai policy" '
        'OR "federal ai strategy" OR "national ai initiative" '
        'OR "federal ai roadmap" OR "ai use cases government" '
        # Defense / Intelligence
        'OR "ai in defense" OR "ai in intelligence" '
        'OR "dod ai strategy" OR "ai task force" '
        # Healthcare / Education
        'OR "ai in healthcare federal" OR "ai in education federal" '
        # Procurement / Budget
        'OR "ai procurement" OR "ai contracting" '
        'OR "ai rd investments" OR "ai budget requests" '
        # Privacy / Regulation / Compliance
        'OR "ai and privacy" OR "ai regulation federal" OR "ai regulation" '
        'OR "ai compliance standards" OR "ai oversight committees" '
        # Infrastructure
        'OR "ai infrastructure modernization" OR "ai and cloud computing" '
        # Policy leaders
        'OR "white house ai policy" OR "white house ai" '
        'OR "ocio ai priorities" '
        # Telecom-specific
        'OR "ai telecommunications" OR "ai policy" '
        'OR "ai telecom" OR "ai broadband"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: BUSINESS & TECH
    # Appendix A: (FCC) AND (business terms)
    # ═══════════════════════════════════════════════════════════════════════════
    "business_tech": (
        '(("FCC" OR "Federal Communications Commission") AND '
        '("Internet policy" OR "net neutrality" OR "open internet" '
        'OR "social media" OR "tech innovation" OR "silicon valley" '
        'OR "investors" OR "wall street" OR "online privacy" '
        'OR "web tracking" OR "throttling" OR "internet traffic" '
        'OR "telecom sector" OR "telecom jobs" '
        'OR "communications industry" OR "telecom industry")) '
        'OR "FCC database" OR "FCC filing"'
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # TOPIC: INTERNATIONAL
    # Appendix A: (FCC) AND (telecom AND region/org terms)
    # ═══════════════════════════════════════════════════════════════════════════
    "international": (
        '(("FCC" OR "Federal Communications Commission") AND '
        '(("telecommunications" OR "telecom" OR "telecoms" OR "telecomm" OR "telecomms") AND '
        '("Europe" OR "Asia" OR "Africa" OR "Australia" '
        'OR "South America" OR "Central America" '
        'OR "Caribbean" OR "Scandinavia" '
        'OR "undersea cable" OR "subsea cable" '
        'OR "Submarine communications cable" '
        'OR ("treaty" AND ("internet" OR "broadband" OR "cables")) '
        'OR "International Telecommunication Union" OR "ITU" '
        'OR "World Radiocommunication Conference"))) '
        # Additional from Appendix A
        'OR ("Office of International Affairs" AND "FCC")'
    ),
}


# ── Appendix B: Source priority list ──────────────────────────────────────────
# Used by engine.py for source credibility scoring and prioritization.
APPENDIX_B_SOURCES = {
    "major_dailies": [
        "Chicago Tribune", "Financial Times", "Los Angeles Times", "New York Times",
        "Philadelphia Inquirer", "SF Chronicle", "USA Today", "Wall Street Journal",
        "Washington Post",
    ],
    "other_dailies": [
        "Atlanta Journal Constitution", "Austin American-Statesman", "Baltimore Sun",
        "Boston Globe", "Boston Herald", "Chicago Sun-Times", "Cleveland Plain Dealer",
        "Dallas Morning News", "Detroit Free Press", "Houston Chronicle",
        "Kansas City Star", "Miami Herald", "Minneapolis Star Tribune",
        "New Orleans Times-Picayune", "New York Daily News", "New York Post",
        "NJ.com", "Oregonian", "Pittsburgh Post-Gazette", "San Jose Mercury News",
        "Seattle Times", "St. Louis Post-Dispatch", "Tampa Bay Times",
        "Arizona Republic", "Washington Times",
    ],
    "wires": [
        "Associated Press", "Bloomberg", "Dow Jones", "Reuters",
    ],
    "trades": [
        "Ad Age", "Atlantic", "Broadcasting & Cable", "CNET", "Daily Beast",
        "Economist", "Fierce Wireless", "Fierce Cable", "Fierce Telecom",
        "Forbes", "Fortune", "The Hill", "Hollywood Reporter", "Morning Consult",
        "Multichannel News", "National Journal", "NPR", "Politico", "Radio World",
        "Roll Call", "Variety", "Wired", "The Wrap",
    ],
    "blogs": [
        "Ars Technica", "BuzzFeed", "Consumerist", "DSL Reports", "Engadget",
        "Fast Company", "Gizmodo", "Huffington Post", "Mashable", "Motherboard",
        "MLex", "PC Magazine", "Recode", "Slate", "TechCrunch",
        "VentureBeat", "The Verge",
    ],
    "social_accounts": [
        "@BrendanCarrFCC", "@OliviaTrustyFCC", "@AGomezFCC",
        "@FTC", "@CFPB", "@FEMA", "@NWS",
    ],
}

# Flat list for quick lookup
ALL_PRIORITY_SOURCES = []
for sources in APPENDIX_B_SOURCES.values():
    ALL_PRIORITY_SOURCES.extend(sources)


# Priority order for section assignment when article matches multiple.
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
class _Parser:
    def __init__(self, tokens, title_lc: str, body_lc: str):
        self.toks = tokens
        self.i = 0
        self.title = title_lc
        self.body = body_lc

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
        if kind is not None:
            self._next()
        return False


def matches(section: str, title: str, summary: str) -> bool:
    """Return True if article satisfies the section's Boolean expression."""
    expr = FCC_BOOLEAN.get(section)
    if not expr:
        return False
    title_lc = (title or "").lower()
    body_lc = f"{title_lc} {(summary or '').lower()}"
    tokens = _tokenize(expr)
    return _Parser(tokens, title_lc, body_lc).parse()


def assign_section(title: str, summary: str) -> Tuple[str, List[str]]:
    """Assign article to exactly one FCC section using Boolean priority."""
    hits = [s for s in FCC_SECTIONS if matches(s, title, summary)]
    if not hits:
        return "other", []
    for s in SECTION_PRIORITY:
        if s in hits:
            return s, hits
    return hits[0], hits


def is_fcc_relevant(title: str, summary: str) -> bool:
    """True if article matches ANY FCC section."""
    return any(matches(s, title, summary) for s in FCC_SECTIONS)


def is_priority_source(source_name: str) -> bool:
    """True if source is in Appendix B priority list."""
    name_lc = (source_name or "").lower()
    return any(s.lower() in name_lc for s in ALL_PRIORITY_SOURCES)
