"""Local journey server (verification only, not committed).

Runs the real application with:
  * ENTITY_RESOLVER_SOURCE=db  - the documented requirement for a live server;
  * EXTERNAL SOURCE CONNECTORS REPLACED BY DETERMINISTIC STUBS. No request
    leaves this machine for NPPES, OIG LEIE, SAM.gov or CMS. Whatever the
    journey shows about "verification" is therefore a statement about the
    pipeline, never about a real source.

Stub answers (same shapes as tests/test_seeded_corpus_b_2026_10_04.py):
  NPPES   found/active for any NPI given; unavailable when there is no NPI
  LEIE    not excluded
  SAM     not found by name, not excluded
  PPEF    unavailable ("not exercised"); revocation: none found
"""
import os
import sys

os.environ.setdefault("ENTITY_RESOLVER_SOURCE", "db")
os.environ["JOURNEY_STUBBED_CONNECTORS"] = "1"

from app.Tefca import cms_ppef  # noqa: E402
from app.Tefca.connectors import (NPPESConnector, OIGLEIEConnector,  # noqa: E402
                                  SAMGovConnector, SourceResult)


async def nppes(self, npi):
    if not npi:
        return SourceResult.unavailable("NPPES", "no NPI to look up", {"npi": npi})
    return SourceResult.ok("NPPES", {"found": True, "npi": npi, "legal_name": f"SYNTHETIC STUB ORG {npi}",
                                     "enumeration_type": "NPI-2", "status": "A", "addresses": []}, {"npi": npi})


async def leie_npi(self, npi):
    if not npi:
        return SourceResult.unavailable("OIG_LEIE", "no NPI to look up", {"npi": npi})
    return SourceResult.ok("OIG_LEIE", {"excluded": False, "exclusion_found": False, "exclusion_count": 0,
                                        "exclusion_type": None, "exclusion_date": None}, {"npi": npi})


async def leie_name(self, last, first="", org=""):
    return SourceResult.ok("OIG_LEIE", {"excluded": False, "exclusion_found": False, "exclusion_count": 0,
                                        "match_methods": ["exact_business_name", "normalized_business_name"],
                                        "candidates_exact": 0, "candidates_normalized_only": 0,
                                        "list_rows_searched": 5}, {"org": org})


_SAM = {"found": False, "matched_by": "name", "excluded": False, "excluded_known": True,
        "identity_ambiguous": False, "ambiguous": False, "registration_current": None}


async def sam_verify(self, uei="", legal_name=""):
    return SourceResult.ok("SAM_GOV", dict(_SAM), {"legal_name": legal_name})


async def sam_name(self, legal_name):
    return SourceResult.ok("SAM_GOV", dict(_SAM), {"legal_name": legal_name})


async def ppef(self, npi):
    return SourceResult.unavailable(self.SOURCE_NAME, "synthetic: CMS data API not exercised by this journey", {"npi": npi})


async def revocation(self, npi):
    if not npi:
        return SourceResult.unavailable(self.SOURCE_NAME, "no NPI to look up", {"npi": npi})
    return SourceResult.ok(self.SOURCE_NAME, {"checked": True, "matches": [],
                                              "result": "NO_ACTIVE_REVOCATION_RECORD_FOUND"}, {"npi": npi})


NPPESConnector.lookup_by_npi = nppes
OIGLEIEConnector.lookup_by_npi = leie_npi
OIGLEIEConnector.lookup_by_name = leie_name
SAMGovConnector.verify = sam_verify
SAMGovConnector.lookup_by_name = sam_name
cms_ppef.PPEFEnrollmentConnector.lookup_by_npi = ppef
cms_ppef.CMSRevocationConnector.lookup_by_npi = revocation

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000, log_level="warning")
