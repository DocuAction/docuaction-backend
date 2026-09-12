"""IQVIA OneKey — RCE-PROVIDED evidence delivery. STATUS = AWAITING_SCHEMA.

PROGRAM CONTEXT
    ONC has indicated an IQVIA-related file is expected from the RCE/vendor.
    DocuAction will RECEIVE it; DocuAction does not query IQVIA, hold an IQVIA
    credential, or purchase OneKey. The existing `app/Tefca/connectors.py
    IQVIAOneKeyConnector` (a keyed API client pending an ODC) is a different,
    older path and is not used here.

WHAT THIS ADAPTER DOES NOW
    Preserve and describe. Given the delivered file it computes the hash,
    fingerprints the schema and inventories the fields. It produces NO
    observations, because the authoritative RCE-delivered layout is not known
    and inventing one would be fabricating proprietary data. Mapping is a
    HUMAN step: `propose_mapping` returns an empty proposal with the fields
    to be mapped; `observations_for` refuses until an approved mapping exists.

PROVENANCE (future)
    source_owner = "IQVIA OneKey", delivery_path = THIRD_PARTY_DELIVERY (RCE),
    received_by = "AGT / DocuAction". Never "DocuAction queried IQVIA".
    The OneKey identifier is preserved as a SOURCE identifier observation; it
    is never DocuAction's canonical entity id.
"""
from __future__ import annotations

import csv
import hashlib
import io
from typing import Dict, List, Optional

from app.core.entity_intelligence import flags
from app.core.entity_intelligence.observations import EvidenceObservation, Provenance
from app.core.entity_intelligence.ports import AdapterDescriptor, AdapterStatus, SchemaInventory

SOURCE_ID = "IQVIA_ONEKEY_RCE_DELIVERY"
SOURCE_OWNER = "IQVIA OneKey"
DELIVERY_PATH_NOTE = "RCE-provided delivery, received by AGT / DocuAction"
STATUS = AdapterStatus.AWAITING_SCHEMA

DESCRIPTOR = AdapterDescriptor(
    source_id=SOURCE_ID, source_owner=SOURCE_OWNER, status=STATUS, feature_flag=flags.IQVIA,
    description="Awaiting the authoritative RCE-delivered file layout. Preserve + inventory only.",
    makes_external_calls=False, requires_credential=False)

#: Publicly described OneKey CONCEPTS (IQVIA OneKey Reference Data fact sheet,
#: 2025): persistent OneKey ID for HCPs/HCOs; HCO names; addresses; corporate
#: parents; affiliations (HCP↔HCO, organisation↔organisation); IDNs. These are
#: POTENTIAL capabilities, listed so a future mapping has a vocabulary to
#: target. They are NOT assumed delivered columns.
POTENTIAL_CONCEPTS = ("onekey_id", "hco_name", "legal_name", "address", "organization_classification",
                      "affiliation", "corporate_parent", "other_identifier")


class SchemaUnknown(RuntimeError):
    """Raised when someone asks for observations before a mapping is approved."""


class IQVIAOneKeyDeliveryAdapter:
    def __init__(self, approved_mapping: Optional[Dict[str, str]] = None):
        #: {delivered column name → core concept}. None until a human approves one.
        self.approved_mapping = approved_mapping

    def describe(self) -> AdapterDescriptor:
        return DESCRIPTOR

    @staticmethod
    def preserve(file_bytes: bytes) -> Dict[str, str]:
        """Hash the delivered bytes exactly as received. Nothing else."""
        return {"sha256": hashlib.sha256(file_bytes).hexdigest(), "byte_size": str(len(file_bytes))}

    @staticmethod
    def inventory(text: str, *, sample_rows: int = 3) -> SchemaInventory:
        """Fingerprint the header and inventory the fields for human review."""
        reader = csv.reader(io.StringIO(text))
        header = next(reader, []) or []
        fp = hashlib.sha256("".join(header).encode("utf-8")).hexdigest()
        samples: Dict[str, List[str]] = {h: [] for h in header}
        count = 0
        for row in reader:
            count += 1
            if count <= sample_rows:
                for h, v in zip(header, row):
                    samples[h].append(v)
        return SchemaInventory(source_id=SOURCE_ID, schema_fingerprint=fp, fields=header,
                               record_count=count, sample_values=samples,
                               note="Inventory only. Mapping requires human review and approval.")

    def propose_mapping(self, inventory: SchemaInventory) -> Dict[str, Optional[str]]:
        """An EMPTY proposal: every delivered field listed, no concept assigned.
        Automatic mapping is deliberately not implemented."""
        return {f: None for f in inventory.fields}

    def observations_for(self, *, canonical_entity_id: Optional[str], identifier: str,
                         provenance: Provenance) -> List[EvidenceObservation]:
        flags.require_enabled(flags.IQVIA, boundary="IQVIAOneKeyDeliveryAdapter.observations_for")
        if not self.approved_mapping:
            raise SchemaUnknown(f"{SOURCE_ID}: no approved field mapping; status {STATUS.value}")
        raise SchemaUnknown(f"{SOURCE_ID}: observation production is not implemented until the "
                            f"RCE-delivered layout is known and a mapping is approved")
