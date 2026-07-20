# fcc_boolean_search.py
# Complete Boolean Search Terms — Appendix A
# FCC Daily News Monitoring Service (Solicitation 7571MN26Q00027)

FCC_SEARCH_TOPICS = {

    "FCC_NEWS": {
        "label": "General",
        "boolean": """
        (FCC OR "Federal Communications Commission")
        AND
        (
            "FCC Chairman"
            OR "FCC Commissioner"
            OR "FCC Acting Chairman"
            OR "Federal Communications Commission Chairman"
            OR "Federal Communications Commission Commissioner"
            OR "Brendan Carr"
            OR "Olivia Trusty"
            OR "Anna Gomez"
            OR Enforcement
            OR "ownership cap"
            OR "national cap"
            OR "39 percent"
            OR USAC
            OR "Universal Service"
            OR "open meeting"
            OR "tentative agenda"
        )
        """,

        "keywords": [
            "Federal Communications Commission",
            "Federal Register",
            "Spectrum",
            "Wireless",
            "Tech",
            "Broadband",
            "Mobile Phone",
            "Robocalls",
            "Spoofing",
            "Telehealth",
            "Wireless Emergency Alert",
            "Emergency Alert",
            "5G",
            "Telemedicine",
            "Robotext",
            "Satellite"
        ]
    },

    "CONSUMERS": {
        "label": "Consumers",
        "boolean": """
        title:TCPA OR title:robocalls OR title:robocall
        OR title:spoofing
        OR title:"phone scam"
        OR title:"accessible communications"
        OR title:deaf
        OR title:"deaf-blind"
        OR title:"closed captioning"
        OR title:"video description services"
        OR title:"video relay"
        OR title:autodialer
        OR title:"caller ID"
        OR title:cramming
        OR "STIR-SHAKEN"
        OR "Robocall Mitigation Database"
        OR "auto warranty scam"
        OR "one ring scam"
        OR robotexts
        OR ("scam" AND "text")
        OR ("fraud" AND "text")
        OR "phone unlocking"
        OR porting
        OR "port out scam"
        OR "Robocall Mitigation Database"
        OR RMD
        OR "Know Your Upstream"
        OR "broadband label"
        OR "CALM Act"
        """,

        "keywords": [
            "Disability Rights",
            "Consumer Education",
            "Outreach",
            "State Governments",
            "Local Governments",
            "Tribal Governments"
        ]
    },

    "MEDIA_BROADCASTING": {
        "label": "Media & Broadcasting",
        "boolean": """
        (FCC OR "Federal Communications Commission")
        AND
        (
            "Media ownership"
            OR "cable merger"
            OR "cable company"
            OR "broadcast television"
            OR "broadcast station"
            OR "radio station"
            OR "radio license"
            OR "broadcast license"
            OR "profanity on the air"
            OR "satellite television"
            OR TV
            OR "broadcast TV"
            OR "satellite TV"
            OR "cable TV"
            OR "set-top box"
            OR "FM translator"
            OR "FM radio"
            OR "AM radio"
            OR (TV AND rescan)
            OR (antenna AND rescan)
            OR "CALM Act"
            OR "loud commercials"
            OR "The View"
            OR "bona fide news"
            OR "equal time"
            OR "license revocation"
            OR "broadcast license"
            OR Disney
            OR ABC
            OR iHeartMedia
            OR payola
            OR "station totals"
            OR "ownership cap"
            OR "national cap"
        )
        """,

        "keywords": [
            "Cable television",
            "Broadcast television",
            "Radio",
            "Satellite services",
            "Satellite TV",
            "Satellite Radio"
        ]
    },

    "SPACE_POLICY": {
        "label": "Space Policy",
        "boolean": """
        (FCC OR "Federal Communications Commission")
        AND
        (
            space
            OR satellite
            OR satellites
            OR GSO
            OR NGSO
            OR "space economy"
            OR ISAM
            OR "In-Space Servicing Assembly Manufacturing"
            OR (launch AND spectrum)
            OR "earth station"
            OR "space station"
            OR ("space bureau" AND FCC)
            OR Gen3
            OR "100,000 satellites"
            OR "Part 100"
            OR "licensing assembly line"
            OR "direct-to-device"
            OR D2D
            OR SpaceMobile
            OR "W-band"
            OR "D-band"
        )
        """,

        "priority_sources": [
            "Starlink",
            "Blue Origin",
            "AST SpaceMobile",
            "SpaceMobile",
            "Intelsat"
        ],

        "keywords_no_fcc_required": [
            "NASA",
            "Launch",
            "Rocket",
            "X-band",
            "E-band",
            "Satellite",
            "Earth station",
            "Space station",
            "ISAM",
            "GSO",
            "NGSO",
            "LEO",
            "MEO",
            "Satellite Earth stations",
            "Direct to device",
            "Satellite to handset",
            "Cislunar communications",
            "Lunar communications",
            "Space policy",
            "Orbital debris",
            "V-Band",
            "Ka-Band",
            "Ku-Band",
            "S-Band"
        ]
    },

    "PUBLIC_SAFETY_CYBER": {
        "label": "Public Safety / Cybersecurity / Privacy",
        "boolean": """
        (FCC OR "Federal Communications Commission")
        AND
        (
            911
            OR E911
            OR PSAP
            OR "911 call center"
            OR "phone outage"
            OR "submarine cables"
            OR cybersecurity
            OR "outage reporting"
            OR "data breach"
            OR "Emergency Alert System"
            OR "Wireless Emergency Alert"
            OR "emergency alert"
            OR "wireless alert"
            OR "Online privacy"
            OR "broadband privacy"
            OR "data sharing"
            OR "personally identifiable information"
            OR "covered equipment"
            OR "covered list"
            OR "drone import"
            OR "surveillance equipment"
            OR "DA 26-742"
            OR Typhoon
            OR hurricane
            OR "disaster report"
            OR NG911
        )
        """,

        "keywords": [
            "911",
            "Emergency alerts",
            "First responder communications"
        ]
    },

    "WIRELESS_SPECTRUM": {
        "label": "Wireless & Spectrum",
        "boolean": """
        (FCC OR "Federal Communications Commission")
        AND
        (
            Broadband
            OR Connectivity
            OR Wireless
            OR spectrum
            OR "mobile phones"
            OR "cell phones"
            OR "data services"
            OR telecom
            OR telecommunications
            OR "calling cards"
            OR "cell service"
            OR privacy
            OR "communications policy"
            OR "signal interference"
            OR "cell tower"
            OR 5G
            OR "small cells"
            OR "Upper C-Band"
            OR "3.98 GHz"
            OR "spectrum pipeline"
            OR "mid-band"
            OR NEPA
        )
        """,

        "keywords": [
            "Microwave links",
            "Mobile broadband services",
            "Licenses",
            "Auctions",
            "Tower registration",
            "Mobile Wireless Competition Report",
            "Spectrum Dashboard"
        ]
    },

    "AI_MACHINE_LEARNING": {
        "label": "Artificial Intelligence / Machine Learning",
        "keywords": [
            "generative ai",
            "agentic ai",
            "ai executive order",
            "ai executive orders",
            "ai in cybersecurity",
            "ai in data management",
            "ai bias mitigation",
            "ai workforce training",
            "ai in federal hiring",
            "ai in emergency response",
            "ai and national security",
            "federal agency ai",
            "government ai policy",
            "responsible ai",
            "explainable ai",
            "ai governance",
            "ai ethics",
            "ai risk management",
            "federal ai strategy",
            "ai in defense",
            "ai in intelligence",
            "ai and public safety",
            "ai in healthcare federal",
            "ai in education federal",
            "ai procurement",
            "ai contracting",
            "ai innovation fellows",
            "ai rd investments",
            "ai budget requests",
            "ai and privacy",
            "ai regulation federal",
            "ai regulation",
            "ai infrastructure modernization",
            "ai and cloud computing",
            "federal ai roadmap",
            "ai use cases government",
            "ai compliance standards",
            "ai oversight committees",
            "ai hiring initiatives",
            "ai in law enforcement",
            "ai in disaster response",
            "ai for fraud detection",
            "ai task force",
            "dod ai strategy",
            "national ai initiative",
            "ocio ai priorities",
            "white house ai policy",
            "ai telecommunications",
            "artificial intelligence",
            "machine learning",
            "ai disclosure",
            "ai-generated calls"
        ]
    },

    "BUSINESS_TECH": {
        "label": "Business & Tech",
        "boolean": """
        (FCC OR "Federal Communications Commission")
        AND
        (
            "Internet policy"
            OR "net neutrality"
            OR "open internet"
            OR "social media"
            OR "tech innovation"
            OR "silicon valley"
            OR investors
            OR "wall street"
            OR "online privacy"
            OR "web tracking"
            OR throttling
            OR "internet traffic"
            OR "telecom sector"
            OR "telecom jobs"
            OR "communications industry"
            OR "telecom industry"
            OR "USF contribution"
            OR "E-Rate"
            OR "rural health care"
        )
        """,

        "keywords": [
            "Internet policy",
            "Net neutrality",
            "Open internet",
            "Social media",
            "Tech innovation",
            "Silicon valley",
            "Investors",
            "Wall street",
            "Online privacy",
            "Web tracking",
            "Throttling",
            "Internet traffic",
            "Telecom sector",
            "Telecom jobs",
            "Communications industry"
        ]
    },

    "INTERNATIONAL": {
        "label": "International",
        "boolean": """
        (FCC OR "Federal Communications Commission")
        AND
        (telecommunications OR telecom OR telecoms OR telecomm OR telecomms)
        AND
        (
            Europe
            OR Asia
            OR Africa
            OR Australia
            OR "South America"
            OR "Central America"
            OR Caribbean
            OR Scandinavia
            OR "undersea cable"
            OR "subsea cable"
            OR "Submarine communications cable"
            OR (treaty AND (internet OR broadband OR cables))
            OR "International Telecommunication Union"
            OR ITU
            OR "World Radiocommunication Conference"
            OR ("Office of International Affairs" AND FCC)
            OR "submarine cable"
            OR "landing license"
            OR SLTE
        )
        """
    }
}
