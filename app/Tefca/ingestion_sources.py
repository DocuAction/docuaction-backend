"""TEFCA's sources, declared against the Core ingestion contract.

WHAT THIS FILE IS FOR
─────────────────────
It answers "does the framework actually fit the other five sources, or only the
one it was built against?" — in the only way that is worth anything, by writing
them down and letting a test check the declarations against the connectors that
already exist.

WHAT IT DELIBERATELY DOES NOT DO
It performs no acquisition. Phase 6 owns real PPEF ingestion, and nothing here
fetches, downloads or bulk-loads anything. These are descriptors and an adapter
shape; the network code stays where it already is, in `connectors.py` and
`cms_ppef.py`, until a phase that owns it says otherwise.

THE DISTINCTION THAT MATTERS
Two of these sources publish a dataset version and two do not, and that is not a
detail. `SourceVersionRef.is_point_in_time` is True only when an observation
could genuinely be reproduced — a preserved artefact, or a stable dataset id plus
a version. A live NPPES lookup has neither, and says so. Recording an API version
in the dataset-version field would make it look reproducible when it is not,
which is the specific defect the provenance model was written to prevent.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.core.evidence_provenance import RetrievalMethod
from app.core.ingestion.contracts import (
    ACQUISITION_FAILED,
    AcquisitionResult,
    SourceDescriptor,
)
from app.core.ingestion.security import UrlPolicy

PROGRAM = "TEFCA"

#: Hosts each source is permitted to reach. Enforced by
#: `security.validate_url`, which resolves the name and checks the ADDRESS —
#: an allow-listed host that resolves to a loopback address is still refused.
HOST_POLICIES: Dict[str, UrlPolicy] = {
    "NPPES": UrlPolicy(allowed_hosts=frozenset({"npiregistry.cms.hhs.gov"})),
    "CMS_PPEF_ENROLLMENT": UrlPolicy(allowed_hosts=frozenset({"data.cms.gov"})),
    "CMS_REVOCATION": UrlPolicy(allowed_hosts=frozenset({"data.cms.gov"})),
    "OIG_LEIE": UrlPolicy(allowed_hosts=frozenset({"oig.hhs.gov"})),
    "SAM_GOV": UrlPolicy(allowed_hosts=frozenset({"api.sam.gov"})),
}


NPPES = SourceDescriptor(
    program=PROGRAM,
    source_name="NPPES",
    source_type="RECORD_LOOKUP",
    authority="https://npiregistry.cms.hhs.gov/api/",
    #: A live query. No artefact is preserved, so an observation from it is NOT
    #: point-in-time reproducible, and the provenance record will say so.
    retrieval_method=RetrievalMethod.API,
    connector_version="1.0",
    description=("NPI Registry lookup by NPI or name. Publishes an API version "
                 "and no dataset version."),
    publishes_version=False,
)

CMS_PPEF_ENROLLMENT = SourceDescriptor(
    program=PROGRAM,
    source_name="CMS_PPEF_ENROLLMENT",
    source_type="BULK_ARTEFACT",
    authority="https://data.cms.gov/data-api/v1/dataset",
    #: A quarterly extract, fetched and preserved. Both a dataset identifier and
    #: a resource version exist, so observations from it ARE reproducible.
    retrieval_method=RetrievalMethod.DOWNLOAD,
    connector_version="1.0",
    description=("CMS Public Provider Enrollment quarterly extract. Ingestion "
                 "belongs to Phase 6; this declares how it plugs in."),
    publishes_version=True,
)

CMS_REVOCATION = SourceDescriptor(
    program=PROGRAM,
    source_name="CMS_REVOCATION",
    source_type="BULK_ARTEFACT",
    authority="https://data.cms.gov/data-api/v1/dataset",
    retrieval_method=RetrievalMethod.DOWNLOAD,
    connector_version="1.0",
    description="CMS revoked-provider extract. Quarterly, versioned.",
    publishes_version=True,
)

OIG_LEIE = SourceDescriptor(
    program=PROGRAM,
    source_name="OIG_LEIE",
    source_type="BULK_ARTEFACT",
    authority="https://oig.hhs.gov/exclusions/",
    #: The exclusions list is a downloaded file, refreshed monthly. Preserving
    #: it is what makes an exclusion observation reproducible.
    retrieval_method=RetrievalMethod.DOWNLOAD,
    connector_version="1.0",
    description="OIG List of Excluded Individuals/Entities. Monthly file.",
    publishes_version=True,
)

SAM_GOV = SourceDescriptor(
    program=PROGRAM,
    source_name="SAM_GOV",
    source_type="RECORD_LOOKUP",
    authority="https://api.sam.gov/entity-information/v3/entities",
    retrieval_method=RetrievalMethod.API,
    connector_version="1.0",
    description=("SAM.gov entity lookup by UEI. Live query, credentialed; the "
                 "API key never appears in a stored URL — see security.redact."),
    publishes_version=False,
)

#: Every TEFCA source, in the order a reviewer would want to read them.
TEFCA_SOURCES: List[SourceDescriptor] = [
    NPPES, CMS_PPEF_ENROLLMENT, CMS_PPEF_ENROLLMENT, CMS_REVOCATION, OIG_LEIE,
    SAM_GOV,
]
# de-duplicated, preserving order
TEFCA_SOURCES = list(dict.fromkeys(TEFCA_SOURCES))


class NotYetWiredConnector:
    """A declared source whose acquisition another phase owns.

    Returns a failed, NON-retryable acquisition naming the phase that owns it.
    That is deliberate. A stub that returned empty-but-successful would let an
    ingestion run report "acquired, 0 records" for a source nobody has
    implemented, and a zero that means "not built" is indistinguishable from a
    zero that means "the source has nothing" — which is exactly the confusion
    `NOTHING_TO_ACQUIRE` exists to prevent.
    """

    def __init__(self, descriptor: SourceDescriptor, owning_phase: str) -> None:
        self._descriptor = descriptor
        self.owning_phase = owning_phase

    def describe(self) -> SourceDescriptor:
        return self._descriptor

    async def acquire(self, **_request: Any) -> AcquisitionResult:
        return AcquisitionResult(
            descriptor=self._descriptor,
            status=ACQUISITION_FAILED,
            error=(f"{self._descriptor.source_name} acquisition is owned by "
                   f"{self.owning_phase} and is not wired in Phase 5"),
            retryable=False,
        )


def register_all(registry) -> List[SourceDescriptor]:
    """Declare TEFCA's sources on a registry.

    Only the RCE delivery has a working connector in Phase 5. The rest are
    registered as not-yet-wired so the registry tells the truth about what can
    actually run, rather than being silent about sources the program has.
    """
    from app.tefca_registry.rce.ingestion_adapter import (
        RCE_DESCRIPTOR,
        RceDeliveryConnector,
    )

    registered: List[SourceDescriptor] = []
    registered.append(
        registry.register(RCE_DESCRIPTOR, lambda: RceDeliveryConnector()))

    owners = {
        "NPPES": "the existing connectors.py lookup path",
        "CMS_PPEF_ENROLLMENT": "Phase 6 — PPEF ingestion",
        "CMS_REVOCATION": "Phase 6 — PPEF ingestion",
        "OIG_LEIE": "the existing connectors.py lookup path",
        "SAM_GOV": "the existing connectors.py lookup path",
    }
    for descriptor in TEFCA_SOURCES:
        registered.append(registry.register(
            descriptor,
            lambda d=descriptor: NotYetWiredConnector(d, owners[d.source_name])))
    return registered
