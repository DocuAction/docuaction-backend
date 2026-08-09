"""
DocuAction TEFCA Review Protocol
Mock TEFCA entity data Data — 30 entities covering all 4 buckets
Replaced by the ONC-provided entity dataset once it is loaded.
Email: the ONC contract point of contact
"""

from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# BUCKET 1 — No Discrepancy (10 entities)
# All fields clean — will validate successfully against NPPES/LEIE/SAM
# ─────────────────────────────────────────────────────────────────

BUCKET_1_ENTITIES = [
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-001",
        "meta": {"profile": ["urn:docuaction:tefca/StructureDefinition/RCEOrganization"]},
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1003000126"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-001"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Riverside Community Health Network",
        "alias": ["RCHN"],
        "telecom": [
            {"system": "phone", "value": "410-555-0101"},
            {"system": "email", "value": "tefca@rchn.org"}
        ],
        "address": [{
            "use": "work",
            "line": ["1200 Health Center Drive"],
            "city": "Baltimore",
            "state": "MD",
            "postalCode": "21201",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-ehealthexchange"},
        "_qhin": "eHealth Exchange",
        "_expected_bucket": 1,
        "_test_note": "Clean entity — all fields match authoritative sources"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-002",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1023011403"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-002"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Capital Region Medical Associates",
        "telecom": [{"system": "phone", "value": "301-555-0202"}],
        "address": [{
            "line": ["4500 Medical Parkway"],
            "city": "Rockville",
            "state": "MD",
            "postalCode": "20850",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-commonwell"},
        "_qhin": "CommonWell Health Alliance",
        "_expected_bucket": 1,
        "_test_note": "Clean entity — physician group"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-003",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1033126539"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-001"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Northern Virginia Urgent Care Centers LLC",
        "telecom": [{"system": "phone", "value": "703-555-0303"}],
        "address": [{
            "line": ["8200 Greensboro Drive", "Suite 100"],
            "city": "McLean",
            "state": "VA",
            "postalCode": "22102",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-002"},
        "_qhin": "CommonWell Health Alliance",
        "_expected_bucket": 1,
        "_test_note": "Clean subparticipant"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-004",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1043211843"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-003"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Chesapeake Health Information Exchange",
        "alias": ["Chesapeake HIE"],
        "telecom": [
            {"system": "phone", "value": "443-555-0404"},
            {"system": "fax", "value": "443-555-0405"}
        ],
        "address": [{
            "line": ["100 East Pratt Street"],
            "city": "Baltimore",
            "state": "MD",
            "postalCode": "21202",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-medallies"},
        "_qhin": "MedAllies",
        "_expected_bucket": 1,
        "_test_note": "Clean HIE participant"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-005",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1053310247"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-002"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Pediatric Specialists of Virginia PA",
        "telecom": [{"system": "phone", "value": "571-555-0505"}],
        "address": [{
            "line": ["2415 Eisenhower Avenue"],
            "city": "Alexandria",
            "state": "VA",
            "postalCode": "22314",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-001"},
        "_qhin": "eHealth Exchange",
        "_expected_bucket": 1,
        "_test_note": "Clean pediatric subparticipant"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-006",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1063411651"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-004"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "MidAtlantic Behavioral Health Services Inc",
        "telecom": [{"system": "phone", "value": "202-555-0606"}],
        "address": [{
            "line": ["1325 G Street NW"],
            "city": "Washington",
            "state": "DC",
            "postalCode": "20005",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-healthgorilla"},
        "_qhin": "Health Gorilla",
        "_expected_bucket": 1,
        "_test_note": "Clean behavioral health participant"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-007",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1073514055"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-005"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Annapolis Regional Hospital Corporation",
        "alias": ["Annapolis Regional Hospital"],
        "telecom": [{"system": "phone", "value": "410-555-0707"}],
        "address": [{
            "line": ["2001 Medical Drive"],
            "city": "Annapolis",
            "state": "MD",
            "postalCode": "21401",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-surescripts"},
        "_qhin": "Surescripts",
        "_expected_bucket": 1,
        "_test_note": "Clean hospital participant"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-008",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1083617459"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-003"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Frederick County Home Health Agency",
        "telecom": [{"system": "phone", "value": "301-555-0808"}],
        "address": [{
            "line": ["350 Montevue Lane"],
            "city": "Frederick",
            "state": "MD",
            "postalCode": "21702",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-007"},
        "_qhin": "Surescripts",
        "_expected_bucket": 1,
        "_test_note": "Clean home health subparticipant"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-009",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1093718863"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-006"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Potomac Valley Radiology Group",
        "telecom": [{"system": "phone", "value": "240-555-0909"}],
        "address": [{
            "line": ["11110 Medical Campus Road"],
            "city": "Hagerstown",
            "state": "MD",
            "postalCode": "21742",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-konza"},
        "_qhin": "KONZA National Network",
        "_expected_bucket": 1,
        "_test_note": "Clean radiology group"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b1-010",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1104921267"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-007"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Eastern Shore Rural Health Commission",
        "telecom": [{"system": "phone", "value": "410-555-1010"}],
        "address": [{
            "line": ["500 Delmarva Drive"],
            "city": "Salisbury",
            "state": "MD",
            "postalCode": "21801",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-kno2"},
        "_qhin": "Kno2",
        "_expected_bucket": 1,
        "_test_note": "Clean rural health commission"
    },
]


# ─────────────────────────────────────────────────────────────────
# BUCKET 2 — Minor or Administrative Discrepancy (8 entities)
# Small name/address variances — real but administrative
# ─────────────────────────────────────────────────────────────────

BUCKET_2_ENTITIES = [
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-001",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1114022671"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-008"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Memorial Health System",
        "telecom": [{"system": "phone", "value": "410-555-1101"}],
        "address": [{
            "line": ["800 Memorial Drive"],
            "city": "Columbia",
            "state": "MD",
            "postalCode": "21044",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-ehealthexchange"},
        "_qhin": "eHealth Exchange",
        "_expected_bucket": 2,
        "_test_note": "Name variance: submitted 'Memorial Health System' but NPPES shows 'Memorial Healthcare System LLC' — DBA vs legal name"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-002",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1124124075"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-004"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Prince Georges Orthopedic and Sports Medicine",
        "telecom": [{"system": "phone", "value": "301-555-1102"}],
        "address": [{
            "line": ["6500 Rivertech Court"],
            "city": "Riverdale",
            "state": "MD",
            "postalCode": "20737",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-004"},
        "_qhin": "MedAllies",
        "_expected_bucket": 2,
        "_test_note": "Address variance: submitted '6500 Rivertech Court' vs NPPES '6500 Rivertech Ct Suite 200' — unit number difference"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-003",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1134226479"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-009"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "St. Agnes Medical Center",
        "telecom": [{"system": "phone", "value": "410-555-1103"}],
        "address": [{
            "line": ["900 Caton Avenue"],
            "city": "Baltimore",
            "state": "MD",
            "postalCode": "21229",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-commonwell"},
        "_qhin": "CommonWell Health Alliance",
        "_expected_bucket": 2,
        "_test_note": "Name variance: 'St. Agnes' in RCE vs 'Saint Agnes' in NPPES — abbreviation difference"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-004",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1144328883"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-005"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Howard County Mental Health Authority",
        "telecom": [{"system": "phone", "value": "410-555-1104"}],
        "address": [{
            "line": ["9250 Bendix Road"],
            "city": "Columbia",
            "state": "MD",
            "postalCode": "21045",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-006"},
        "_qhin": "Health Gorilla",
        "_expected_bucket": 2,
        "_test_note": "Phone number discrepancy — submitted differs from NPPES by one digit, likely data entry error"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-005",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1154431287"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-010"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Southern Maryland Hospital Center",
        "alias": ["SMHC"],
        "telecom": [{"system": "phone", "value": "301-555-1105"}],
        "address": [{
            "line": ["7503 Surratts Road"],
            "city": "Clinton",
            "state": "MD",
            "postalCode": "20735",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-surescripts"},
        "_qhin": "Surescripts",
        "_expected_bucket": 2,
        "_test_note": "Street spelling: 'Surratts Road' vs 'Surratt's Road' in NPPES — punctuation difference"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-006",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1164533691"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-006"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Western Maryland Physicians LLC",
        "telecom": [{"system": "phone", "value": "301-555-1106"}],
        "address": [{
            "line": ["12500 Willowbrook Road"],
            "city": "Cumberland",
            "state": "MD",
            "postalCode": "21502",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-009"},
        "_qhin": "KONZA National Network",
        "_expected_bucket": 2,
        "_test_note": "Name variance: 'Western Maryland Physicians LLC' vs 'Western Maryland Physicians Group LLC' — minor corporate suffix difference"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-007",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1174636095"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-011"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Carroll County General Hospital",
        "telecom": [{"system": "phone", "value": "410-555-1107"}],
        "address": [{
            "line": ["200 Memorial Avenue"],
            "city": "Westminster",
            "state": "MD",
            "postalCode": "21157",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-kno2"},
        "_qhin": "Kno2",
        "_expected_bucket": 2,
        "_test_note": "Zip+4: submitted '21157' vs NPPES '21157-5009' — ZIP code format difference only"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b2-008",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1184738499"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-007"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Calvert Health Medical Center",
        "telecom": [{"system": "phone", "value": "410-555-1108"}],
        "address": [{
            "line": ["100 Hospital Road"],
            "city": "Prince Frederick",
            "state": "MD",
            "postalCode": "20678",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-007"},
        "_qhin": "Surescripts",
        "_expected_bucket": 2,
        "_test_note": "Historical LEIE record from 2019 with confirmed reinstatement 2020 — resolved exclusion, no current issue"
    },
]


# ─────────────────────────────────────────────────────────────────
# BUCKET 3 — Inexplicable Discrepancy (7 entities)
# Conflicts that cannot be attributed to administrative variance
# ─────────────────────────────────────────────────────────────────

BUCKET_3_ENTITIES = [
    {
        "resourceType": "Organization",
        "id": "rce-org-b3-001",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1194840903"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-012"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Atlantic Coast Health Partners",
        "telecom": [{"system": "phone", "value": "410-555-1201"}],
        "address": [{
            "line": ["3300 Clipper Mill Road"],
            "city": "Baltimore",
            "state": "MD",
            "postalCode": "21211",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-ehealthexchange"},
        "_qhin": "eHealth Exchange",
        "_expected_bucket": 3,
        "_test_note": "NPI found in NPPES but under completely different organization name — 'Chesapeake Physical Therapy Associates'. Cannot reconcile."
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b3-002",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1204943307"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-008"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Bay Area Medical Services Corp",
        "telecom": [{"system": "phone", "value": "443-555-1202"}],
        "address": [{
            "line": ["1450 South Rolling Road"],
            "city": "Halethorpe",
            "state": "MD",
            "postalCode": "21227",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-004"},
        "_qhin": "MedAllies",
        "_expected_bucket": 3,
        "_test_note": "Conflicting addresses across NPPES (Virginia) and SAM.gov (Maryland) — cannot determine correct state"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b3-003",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1215045711"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-013"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Chesapeake Community Physicians Group",
        "telecom": [{"system": "phone", "value": "410-555-1203"}],
        "address": [{
            "line": ["600 North Eutaw Street"],
            "city": "Baltimore",
            "state": "MD",
            "postalCode": "21201",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-healthgorilla"},
        "_qhin": "Health Gorilla",
        "_expected_bucket": 3,
        "_test_note": "Entity type mismatch — submitted as Participant (health system) but NPPES taxonomy shows individual practitioner NPI-1"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b3-004",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1225148115"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-009"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Harford County Health Alliance",
        "telecom": [{"system": "phone", "value": "410-555-1204"}],
        "address": [{
            "line": ["One Technology Drive"],
            "city": "Bel Air",
            "state": "MD",
            "postalCode": "21014",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-001"},
        "_qhin": "eHealth Exchange",
        "_expected_bucket": 3,
        "_test_note": "Missing NPI field in submission — cannot validate against NPPES. Organization name not uniquely identifiable."
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b3-005",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1235250519"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-014"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Garrett Regional Medical Center Partners",
        "telecom": [{"system": "phone", "value": "301-555-1205"}],
        "address": [{
            "line": ["251 North Fourth Street"],
            "city": "Oakland",
            "state": "MD",
            "postalCode": "21550",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-konza"},
        "_qhin": "KONZA National Network",
        "_expected_bucket": 3,
        "_test_note": "SAM.gov registration expired 14 months ago — no renewal on record. Cannot verify current operational status."
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b3-006",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1245352923"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-010"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Talbot County Community Health Corp",
        "telecom": [{"system": "phone", "value": "410-555-1206"}],
        "address": [{
            "line": ["218 East Dover Street"],
            "city": "Easton",
            "state": "MD",
            "postalCode": "21601",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-010"},
        "_qhin": "Kno2",
        "_expected_bucket": 3,
        "_test_note": "Organization claims hospital taxonomy but PECOS enrollment shows it as a lab — functional mismatch unexplained"
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b3-007",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1255455327"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-015"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Regional Health Network of Maryland",
        "telecom": [{"system": "phone", "value": "301-555-1207"}],
        "address": [{
            "line": ["1800 Research Boulevard"],
            "city": "Rockville",
            "state": "MD",
            "postalCode": "20850",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-kno2"},
        "_qhin": "Kno2",
        "_expected_bucket": 3,
        "_test_note": "Three different legal names across NPPES, SAM.gov, and RCE submission — source conflict, no clear authoritative match"
    },
]


# ─────────────────────────────────────────────────────────────────
# BUCKET 4 — Non-Compliant (5 entities)
# Active exclusions, deactivated NPIs, SAM debarment
# ─────────────────────────────────────────────────────────────────

BUCKET_4_ENTITIES = [
    {
        "resourceType": "Organization",
        "id": "rce-org-b4-001",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1265557731"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-011"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Coastal Medical Supply and Services Inc",
        "telecom": [{"system": "phone", "value": "410-555-1301"}],
        "address": [{
            "line": ["3000 Mariner Drive"],
            "city": "Ocean City",
            "state": "MD",
            "postalCode": "21842",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-001"},
        "_qhin": "eHealth Exchange",
        "_expected_bucket": 4,
        "_test_note": "ACTIVE OIG LEIE EXCLUSION — Exclusion Type: Mandatory, Date: 2023-08-15, No reinstatement. DME fraud conviction."
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b4-002",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1275660135"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-016"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "Summit Healthcare Management LLC",
        "telecom": [{"system": "phone", "value": "410-555-1302"}],
        "address": [{
            "line": ["400 Summit Avenue"],
            "city": "Towson",
            "state": "MD",
            "postalCode": "21204",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-commonwell"},
        "_qhin": "CommonWell Health Alliance",
        "_expected_bucket": 4,
        "_test_note": "NPI DEACTIVATED — NPI 1275660135 was deactivated in NPPES on 2024-03-01. Organization no longer enrolled."
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b4-003",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1285762539"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-012"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Meridian Healthcare Consulting Group",
        "telecom": [{"system": "phone", "value": "202-555-1303"}],
        "address": [{
            "line": ["1701 Pennsylvania Avenue NW"],
            "city": "Washington",
            "state": "DC",
            "postalCode": "20006",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-006"},
        "_qhin": "Health Gorilla",
        "_expected_bucket": 4,
        "_test_note": "SAM.GOV ACTIVE DEBARMENT — Excluded from federal procurement programs. Exclusion effective 2024-01-15."
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b4-004",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "9999999993"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-017"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "PARTICIPANT"}]}],
        "name": "National Healthcare Partners Network",
        "telecom": [{"system": "phone", "value": "301-555-1304"}],
        "address": [{
            "line": ["Suite 1000", "7200 Wisconsin Avenue"],
            "city": "Bethesda",
            "state": "MD",
            "postalCode": "20814",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-qhin-surescripts"},
        "_qhin": "Surescripts",
        "_expected_bucket": 4,
        "_test_note": "NPI NOT FOUND — NPI 9999999993 does not exist in NPPES registry. Invalid NPI submitted."
    },
    {
        "resourceType": "Organization",
        "id": "rce-org-b4-005",
        "identifier": [
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1295864943"},
            {"system": "urn:docuaction:tefca/identifier", "value": "SUBPART-013"}
        ],
        "active": True,
        "type": [{"coding": [{"system": "urn:docuaction:tefca/entity-type", "code": "SUBPARTICIPANT"}]}],
        "name": "Advantage Health Supply Corporation",
        "telecom": [{"system": "phone", "value": "410-555-1305"}],
        "address": [{
            "line": ["2600 East Biddle Street"],
            "city": "Baltimore",
            "state": "MD",
            "postalCode": "21213",
            "country": "US"
        }],
        "partOf": {"reference": "Organization/rce-org-b1-007"},
        "_qhin": "Surescripts",
        "_expected_bucket": 4,
        "_test_note": "PECOS PAYMENT SUSPENSION — Active CMS payment suspension flag. Federal program integrity concern."
    },
]


# ─────────────────────────────────────────────────────────────────
# Combined dataset
# ─────────────────────────────────────────────────────────────────

ALL_MOCK_ENTITIES = (
    BUCKET_1_ENTITIES +
    BUCKET_2_ENTITIES +
    BUCKET_3_ENTITIES +
    BUCKET_4_ENTITIES
)

MOCK_ENTITY_INDEX = {e["id"]: e for e in ALL_MOCK_ENTITIES}

MOCK_STATS = {
    "total": len(ALL_MOCK_ENTITIES),
    "bucket_1": len(BUCKET_1_ENTITIES),
    "bucket_2": len(BUCKET_2_ENTITIES),
    "bucket_3": len(BUCKET_3_ENTITIES),
    "bucket_4": len(BUCKET_4_ENTITIES),
    "qhins_represented": [
        "eHealth Exchange", "CommonWell Health Alliance", "MedAllies",
        "Health Gorilla", "Surescripts", "KONZA National Network", "Kno2"
    ]
}


def get_mock_entity_by_npi(npi: str) -> dict | None:
    """Lookup mock entity by NPI."""
    for entity in ALL_MOCK_ENTITIES:
        for identifier in entity.get("identifier", []):
            if identifier.get("system") == "http://hl7.org/fhir/sid/us-npi":
                if identifier.get("value") == npi:
                    return entity
    return None


def get_mock_entities_by_qhin(qhin_name: str) -> list[dict]:
    """Get all mock entities for a specific QHIN."""
    return [e for e in ALL_MOCK_ENTITIES if e.get("_qhin") == qhin_name]
