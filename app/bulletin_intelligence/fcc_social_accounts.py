# fcc_social_accounts.py
# Social Media Monitoring Accounts — Appendix B
# FCC Daily News Monitoring Service (Solicitation 7571MN26Q00027)

# ── FCC Commissioner Accounts (Primary Monitoring) ────────────────────────────
COMMISSIONER_ACCOUNTS = {
    "x_twitter": [
        {"handle": "@BrendanCarrFCC", "name": "Brendan Carr", "role": "Chairman", "party": "R"},
        {"handle": "@OliviaTrustyFCC", "name": "Olivia Trusty", "role": "Commissioner", "party": "R"},
        {"handle": "@AGomezFCC", "name": "Anna Gomez", "role": "Commissioner", "party": "D"},
    ],
    "bluesky": [
        {"handle": "@brendancarrfcc.bsky.social", "name": "Brendan Carr", "role": "Chairman"},
        {"handle": "@agomezfcc.bsky.social", "name": "Anna Gomez", "role": "Commissioner"},
    ],
}

# ── Related Federal Agency Accounts ───────────────────────────────────────────
FEDERAL_AGENCY_ACCOUNTS = [
    {"handle": "@FCC", "platform": "x_twitter", "name": "Federal Communications Commission"},
    {"handle": "@FTC", "platform": "x_twitter", "name": "Federal Trade Commission"},
    {"handle": "@CFPB", "platform": "x_twitter", "name": "Consumer Financial Protection Bureau"},
    {"handle": "@FEMA", "platform": "x_twitter", "name": "Federal Emergency Management Agency"},
    {"handle": "@NWS", "platform": "x_twitter", "name": "National Weather Service"},
]

# ── Social Media Platforms to Monitor ─────────────────────────────────────────
PLATFORMS = {
    "x_twitter": {
        "name": "X (Twitter)",
        "search_queries": [
            "FCC",
            "Federal Communications Commission",
            "Brendan Carr FCC",
            "@BrendanCarrFCC",
            "@OliviaTrustyFCC",
            "@AGomezFCC",
        ],
        "enabled": True,
        "api_required": True,
        "note": "X API is paid — deferred until budget approved",
    },
    "reddit": {
        "name": "Reddit",
        "subreddits": [
            "r/technology",
            "r/telecom",
            "r/cordcutters",
            "r/broadband",
            "r/privacy",
            "r/netsec",
        ],
        "search_queries": ["FCC", "Federal Communications Commission"],
        "enabled": True,
        "api_required": True,
        "note": "Reddit OAuth — blocked at app registration",
    },
    "bluesky": {
        "name": "BlueSky",
        "search_queries": [
            "FCC",
            "Federal Communications Commission",
            "Brendan Carr",
        ],
        "enabled": True,
        "api_required": False,
        "note": "BlueSky API is free — ingest via bluesky_ingest.py",
    },
    "youtube": {
        "name": "YouTube",
        "search_queries": [
            "FCC hearing",
            "FCC Chairman",
            "Federal Communications Commission",
            "FCC ruling",
            "FCC spectrum",
            "FCC broadband",
        ],
        "enabled": True,
        "api_required": True,
        "note": "YouTube Data API v3 — YOUTUBE_API_KEY set in Railway",
    },
    "linkedin": {
        "name": "LinkedIn",
        "search_queries": [
            "FCC",
            "Federal Communications Commission",
            "telecom policy",
        ],
        "enabled": False,
        "api_required": True,
        "note": "LinkedIn API restricted — monitor manually or via Talkwalker when available",
    },
}

# ── All handles flat list (for quick matching) ────────────────────────────────
ALL_SOCIAL_HANDLES = [
    "@BrendanCarrFCC",
    "@OliviaTrustyFCC",
    "@AGomezFCC",
    "@FCC",
    "@FTC",
    "@CFPB",
    "@FEMA",
    "@NWS",
]

# ── Commissioner name detection ───────────────────────────────────────────────
COMMISSIONER_NAMES = [
    "Brendan Carr",
    "Olivia Trusty",
    "Anna Gomez",
]


def mentions_commissioner(text: str) -> list:
    """Return list of commissioner names mentioned in text."""
    text_lower = (text or "").lower()
    return [name for name in COMMISSIONER_NAMES if name.lower() in text_lower]


def is_commissioner_post(handle: str) -> bool:
    """Check if a social media handle belongs to an FCC commissioner."""
    handle_lower = (handle or "").lower()
    for platform_accounts in COMMISSIONER_ACCOUNTS.values():
        for account in platform_accounts:
            if account["handle"].lower() == handle_lower:
                return True
    return False
