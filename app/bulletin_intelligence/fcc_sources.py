# fcc_sources.py
# Complete Source List — Appendix B
# FCC Daily News Monitoring Service (Solicitation 7571MN26Q00027)

MAJOR_DAILIES = [
    "New York Times",
    "Wall Street Journal",
    "Washington Post",
    "USA Today",
    "Los Angeles Times",
    "Chicago Tribune",
    "Financial Times",
]

OTHER_DAILIES = [
    "Atlanta Journal Constitution",
    "Austin American-Statesman",
    "Baltimore Sun",
    "Boston Globe",
    "Boston Herald",
    "Chicago Sun-Times",
    "Cleveland Plain Dealer",
    "Dallas Morning News",
    "Detroit Free Press",
    "Houston Chronicle",
    "Kansas City Star",
    "Miami Herald",
    "Minneapolis Star Tribune",
    "New Orleans Times-Picayune",
    "New York Daily News",
    "New York Post",
    "NJ.com",
    "Oregonian",
    "Pittsburgh Post-Gazette",
    "San Jose Mercury News",
    "Seattle Times",
    "St. Louis Post-Dispatch",
    "Tampa Bay Times",
    "Arizona Republic",
    "Washington Times",
]

WIRES = [
    "Associated Press",
    "Bloomberg",
    "Dow Jones",
    "Reuters",
]

TRADES = [
    "Ad Age",
    "Atlantic",
    # "Broadcasting & Cable",  # CEASED PUBLICATION Sept 2024
    "CNET",
    "Daily Beast",
    "Economist",
    "Fierce Wireless",
    "Fierce Cable",
    "Fierce Telecom",
    "Forbes",
    "Fortune",
    "The Hill",
    "Hollywood Reporter",
    "Morning Consult",
    # "Multichannel News",  # CEASED PUBLICATION Sept 2024
    "National Journal",
    "NPR",
    "Politico",
    "Radio World",
    "Roll Call",
    "Variety",
    "Wired",
    "The Wrap",
]

TECH_BLOGS = [
    "Ars Technica",
    "BuzzFeed",
    "Consumerist",
    "DSL Reports",
    "Engadget",
    "Fast Company",
    "Gizmodo",
    "Huffington Post",
    "Mashable",
    "Motherboard",
    "MLex",
    "PC Magazine",
    "Recode",
    "Slate",
    "TechCrunch",
    "VentureBeat",
    "The Verge",
]

# Additional FCC-specific sources (not in Appendix B but essential)
FCC_SPECIFIC = [
    "Communications Daily",
    "Law360",
    "Broadband Breakfast",
    "Light Reading",
    "RCR Wireless",
    "TV Technology",
    "TV News Check",
    "Telecompetitor",
    "Fierce Network",
    "Inside Radio",
    "SpaceNews",
    "Submarine Networks",
]

# Subscription sources that require labeling
SUBSCRIPTION_SOURCES = [
    "Communications Daily",
    "Law360",
    "Wall Street Journal",
    "Bloomberg",
    "Politico Pro",
    "Financial Times",
    "MLex",
    "Inside Cybersecurity",
]

# All sources combined for quick lookup
ALL_SOURCES = (
    MAJOR_DAILIES + OTHER_DAILIES + WIRES +
    TRADES + TECH_BLOGS + FCC_SPECIFIC
)


def is_priority_source(source_name: str) -> bool:
    """Check if a source is in the Appendix B priority list."""
    name_lower = (source_name or "").lower()
    return any(s.lower() in name_lower for s in ALL_SOURCES)


def is_subscription_source(source_name: str) -> bool:
    """Check if a source requires SUBSCRIPTION REQUIRED label."""
    name_lower = (source_name or "").lower()
    return any(s.lower() in name_lower for s in SUBSCRIPTION_SOURCES)


def get_source_tier(source_name: str) -> str:
    """Return the tier of a source for credibility scoring."""
    name_lower = (source_name or "").lower()

    for s in WIRES:
        if s.lower() in name_lower:
            return "wire"

    for s in MAJOR_DAILIES:
        if s.lower() in name_lower:
            return "major_daily"

    for s in TRADES:
        if s.lower() in name_lower:
            return "trade"

    for s in FCC_SPECIFIC:
        if s.lower() in name_lower:
            return "fcc_specific"

    for s in OTHER_DAILIES:
        if s.lower() in name_lower:
            return "regional_daily"

    for s in TECH_BLOGS:
        if s.lower() in name_lower:
            return "tech_blog"

    return "other"
