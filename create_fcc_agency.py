"""
Run this: python create_fcc_agency.py
Creates the FCC agency on your Railway backend
"""
import urllib.request
import json

url = "https://api-prod.docuaction.io/api/v1/bulletin/agencies"

data = {
    "agency_id": "fcc",
    "name": "Federal Communications Commission",
    "rss_feeds": [
        "https://www.fcc.gov/news-events/rss.xml",
        "https://broadbandbreakfast.com/feed/",
        "https://www.lightreading.com/rss.xml",
        "https://www.telecompetitor.com/feed/",
        "https://www.fiercewireless.com/rss/xml",
        "https://www.tvtechnology.com/rss",
        "https://thehill.com/feed/",
        "https://arstechnica.com/feed/",
        "https://www.axios.com/feeds/feed.rss"
    ],
    "topics": [
        "FCC_NEWS", "CONSUMERS", "MEDIA_BROADCASTING",
        "SPACE_POLICY", "PUBLIC_SAFETY_CYBER", "WIRELESS_SPECTRUM",
        "AI_MACHINE_LEARNING", "BUSINESS_TECH", "INTERNATIONAL"
    ],
    "delivery_time": "06:00",
    "timezone": "America/New_York"
}

body = json.dumps(data).encode("utf-8")
req = urllib.request.Request(url, data=body, method="POST")
req.add_header("Content-Type", "application/json")

try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode())
        print("SUCCESS:", json.dumps(result, indent=2))
except urllib.error.HTTPError as e:
    print(f"ERROR {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"ERROR: {e}")
