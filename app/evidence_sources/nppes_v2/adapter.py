"""NPPES V2 adapter — observations from preserved files, with provenance.

Produces, per organisational NPI:
    IDENTIFIER  role "NPI"                       NPPES_NPI_OBSERVED
    NAME        LEGAL_BUSINESS_NAME              NPPES_LEGAL_BUSINESS_NAME_OBSERVED
    NAME        DOING_BUSINESS_AS / FORMER_LEGAL_BUSINESS_NAME / OTHER_NAME
                                                 NPPES_OTHER_NAME_OBSERVED (kind from the CMS type code)
    LOCATION    PRIMARY_PRACTICE_LOCATION        NPPES_PRIMARY_PRACTICE_LOCATION_OBSERVED
    LOCATION    ADDITIONAL_PRACTICE_LOCATION     NPPES_ADDITIONAL_PRACTICE_LOCATION_OBSERVED
    LOCATION    MAILING_LOCATION                 NPPES_MAILING_LOCATION_OBSERVED

DBA is asserted ONLY for Other Organization Name Type Code 3. Code 4 is a
former legal business name and code 5 is "Other Name"; both are surfaced with
their own kind, never as DBA.

This adapter has no network code. Acquisition (downloading the CMS zip) is a
separate, later concern; in this sprint the adapter takes file text it is
handed. It is still gated: constructing observations while the feature is off
raises FeatureDisabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.entity_intelligence import flags
from app.core.entity_intelligence.normalize import normalize_address, normalize_name
from app.core.entity_intelligence.observations import (DeliveryPath, EvidenceApplicability,
                                                        EvidenceObservation, LocationRole,
                                                        NameKind, ObservationType, Provenance,
                                                        SourceAuthority)
from app.core.entity_intelligence.observations import ValueHandling
from app.core.entity_intelligence.ports import (AdapterDescriptor, AdapterStatus, DataRights,
                                                 DataRightsClass, RightsStatus)
from app.core.evidence_provenance import RetrievalMethod, SourceVersionRef, file_sha256

from .parser import (NppesOrganizationRow, NppesOtherNameRow, NppesPracticeLocationRow,
                     PARSER_VERSION, parse_main_file, parse_other_name_file,
                     parse_practice_location_file)
from .schema import OTHER_NAME_REFERENCE_POINTER_CODE, SCHEMA_VERSION, other_name_kind

SOURCE_ID = "NPPES_V2"
SOURCE_OWNER = "CMS NPPES (National Plan and Provider Enumeration System)"

OBS_NPI = "NPPES_NPI_OBSERVED"
OBS_LEGAL_NAME = "NPPES_LEGAL_BUSINESS_NAME_OBSERVED"
OBS_OTHER_NAME = "NPPES_OTHER_NAME_OBSERVED"
OBS_PRIMARY_LOCATION = "NPPES_PRIMARY_PRACTICE_LOCATION_OBSERVED"
OBS_ADDITIONAL_LOCATION = "NPPES_ADDITIONAL_PRACTICE_LOCATION_OBSERVED"
OBS_MAILING_LOCATION = "NPPES_MAILING_LOCATION_OBSERVED"
SIGNAL_DBA = "DBA_RELATIONSHIP_IDENTIFIED"   # emitted only for type code 3

#: CMS publishes the NPPES Data Dissemination files as public data for
#: download ("NPPES Downloadable File" — download.cms.gov/nppes). Recorded as
#: PUBLIC with raw storage; no attribution or credential is required.
DATA_RIGHTS = DataRights(
    rights_class=DataRightsClass.PUBLIC, status=RightsStatus.ASSUMED_PUBLIC_DOMAIN,
    storage_allowed=True, raw_storage_allowed=True, display_allowed=True,
    # ASSUMED_PUBLIC_DOMAIN != REVIEWED: retention, historical comparison and
    # redistribution rights stay False until a named AGT decision-maker reviews.
    redistribution_allowed=False, snapshot_retention_allowed=False, historical_comparison_allowed=False,
    derived_observation_permission=True, client_display_allowed=False,
    external_call_allowed=False, credential_required=False,
    attribution_required=False, retention_rule="retain each preserved edition for reproducibility (pending review)",
    value_handling=ValueHandling.RAW_PERMITTED,
    basis="CMS NPPES Data Dissemination public download (NPI_Files.html), readme v.2 May 12, 2026",
    official_reference="CMS NPPES Data Dissemination Notice CMS-6060-N (Federal Register, May 30, 2007); NPI_Files.html",
    reference_version="readme v.2 May 12, 2026", review_date=None, reviewed_by=None,
    limitations="Rights recorded by research; no human review yet")

DESCRIPTOR = AdapterDescriptor(
    source_id=SOURCE_ID, source_owner=SOURCE_OWNER, status=AdapterStatus.IMPLEMENTED,
    feature_flag=flags.NPPES,
    description="NPPES Data Dissemination V2 main, Other Name and Practice Location files.",
    makes_external_calls=False, requires_credential=False, data_rights=DATA_RIGHTS)


@dataclass
class NppesV2Bundle:
    """Parsed content of one preserved V2 bundle (full or weekly)."""
    organizations: Dict[str, NppesOrganizationRow] = field(default_factory=dict)
    other_names: Dict[str, List[NppesOtherNameRow]] = field(default_factory=dict)
    practice_locations: Dict[str, List[NppesPracticeLocationRow]] = field(default_factory=dict)
    reports: List = field(default_factory=list)
    file_hashes: Dict[str, str] = field(default_factory=dict)
    dataset_version: Optional[str] = None        # e.g. "Weekly_083126_090626_V2" or "August_2026_V2"
    #: When the preserved files were obtained. Set by the caller that downloaded
    #: them; defaults to parse time, which is honest for a file handed to us now.
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_texts(cls, *, main_text: str, other_name_text: str = "", practice_location_text: str = "",
                   dataset_version: Optional[str] = None) -> "NppesV2Bundle":
        b = cls(dataset_version=dataset_version)
        orgs, r1 = parse_main_file(main_text)
        b.reports.append(r1)
        for row in orgs:
            if row.parse_note is None:
                b.organizations[row.npi] = row
        b.file_hashes["npidata"] = file_sha256(main_text.encode("utf-8"))
        if other_name_text:
            names, r2 = parse_other_name_file(other_name_text)
            b.reports.append(r2)
            for row in names:
                if row.parse_note is None:
                    b.other_names.setdefault(row.npi, []).append(row)
            b.file_hashes["othername"] = file_sha256(other_name_text.encode("utf-8"))
        if practice_location_text:
            locs, r3 = parse_practice_location_file(practice_location_text)
            b.reports.append(r3)
            for row in locs:
                if row.parse_note is None:
                    b.practice_locations.setdefault(row.npi, []).append(row)
            b.file_hashes["pl"] = file_sha256(practice_location_text.encode("utf-8"))
        return b


class NppesV2Adapter:
    def __init__(self, bundle: NppesV2Bundle):
        self.bundle = bundle

    def describe(self) -> AdapterDescriptor:
        return DESCRIPTOR

    def _provenance(self, file_kind: str, line_number: int, field_name: str) -> Provenance:
        version = SourceVersionRef(
            source=SOURCE_ID, dataset_version=self.bundle.dataset_version,
            retrieval_method=RetrievalMethod.DOWNLOAD,
            retrieved_at=self.bundle.retrieved_at,
            source_file_hash=self.bundle.file_hashes.get(file_kind),
            dataset_identifier=f"NPPES Data Dissemination V2 / {file_kind}",
        )
        return Provenance(source_owner=SOURCE_OWNER, delivery_path=DeliveryPath.FILE_DOWNLOAD,
                          source_version=version, source_file_sha256=self.bundle.file_hashes.get(file_kind),
                          source_record_ref=f"{file_kind}:line {line_number}:{field_name}",
                          parser_version=PARSER_VERSION, schema_version=SCHEMA_VERSION,
                          value_handling=ValueHandling.RAW_PERMITTED)

    def observations_for(self, *, canonical_entity_id: Optional[str], identifier: str,
                         provenance: Optional[Provenance] = None) -> List[EvidenceObservation]:
        """All NPPES observations for one NPI. Empty list when NPPES has no
        Type 2 record for it — an absence, recorded as absence."""
        flags.require_enabled(flags.NPPES, boundary="NppesV2Adapter.observations_for")
        npi = (identifier or "").strip()
        org = self.bundle.organizations.get(npi)
        if org is None:
            return []
        out: List[EvidenceObservation] = []
        common = dict(canonical_entity_id=canonical_entity_id, source_id=SOURCE_ID,
                      source_authority=SourceAuthority.FEDERAL_REGISTRY,
                      source_delivery_id=self.bundle.dataset_version,
                      source_record_id=npi, applicability=EvidenceApplicability.APPLICABLE,
                      observed_at=org.last_update_date, effective_from=org.enumeration_date,
                      effective_to=org.deactivation_date)
        points_to_reference_file = org.other_organization_name_type_code == OTHER_NAME_REFERENCE_POINTER_CODE
        out.append(EvidenceObservation(
            observation_type=ObservationType.IDENTIFIER, role="NPI",
            observed_value={"value": npi, "entity_type": org.entity_type_code, "kind": OBS_NPI,
                            "replacement_npi": org.replacement_npi,
                            "deactivation_date": org.deactivation_date,
                            "reactivation_date": org.reactivation_date,
                            # type code 6: NPPES says other names exist in the reference file.
                            # Stated so an analyst can see it even if that file was not supplied.
                            "other_names_in_reference_file": points_to_reference_file,
                            "other_name_reference_rows_loaded": len(self.bundle.other_names.get(npi, []))},
            source_field="NPI", provenance=self._provenance("npidata", org.line_number, "NPI"), **common))
        if org.legal_business_name:
            out.append(EvidenceObservation(
                observation_type=ObservationType.NAME, role=NameKind.LEGAL_BUSINESS_NAME.value,
                observed_value={"name": org.legal_business_name, "kind": OBS_LEGAL_NAME},
                normalized_value={"name": normalize_name(org.legal_business_name)},
                source_field="Provider Organization Name (Legal Business Name)",
                provenance=self._provenance("npidata", org.line_number, "Provider Organization Name (Legal Business Name)"),
                **common))
        # Other names: the one on the main record plus the reference file rows.
        # Pointer code 6 carries no name (the field is a placeholder); the kinds
        # are stated only in the reference file, so nothing is emitted for it.
        extra = []
        if not points_to_reference_file:
            extra.append((org.other_organization_name, org.other_organization_name_type_code, "npidata",
                          org.line_number, "Provider Other Organization Name", None))
        for row in self.bundle.other_names.get(npi, []):
            extra.append((row.other_organization_name, row.type_code, "othername", row.line_number,
                          "Provider Other Organization Name", row.created_date))
        for name, code, file_kind, line, field_name, created in extra:
            if not name:
                continue
            kind = other_name_kind(code or "")
            value = {"name": name, "kind": OBS_OTHER_NAME, "type_code": code,
                     "type_description": _type_description(code), "created_date": created}
            if kind == NameKind.DOING_BUSINESS_AS.value:
                value["signal"] = SIGNAL_DBA
            out.append(EvidenceObservation(
                observation_type=ObservationType.NAME, role=kind, observed_value=value,
                normalized_value={"name": normalize_name(name)}, source_field=field_name,
                provenance=self._provenance(file_kind, line, field_name), **common))
        if org.primary_practice_location.get("line1"):
            out.append(EvidenceObservation(
                observation_type=ObservationType.LOCATION, role=LocationRole.PRIMARY_PRACTICE_LOCATION.value,
                observed_value={**org.primary_practice_location, "kind": OBS_PRIMARY_LOCATION},
                normalized_value=normalize_address(org.primary_practice_location),
                source_field="Provider Business Practice Location Address",
                provenance=self._provenance("npidata", org.line_number, "Provider Business Practice Location Address"),
                **common))
        for row in self.bundle.practice_locations.get(npi, []):
            if not row.address.get("line1"):
                continue
            out.append(EvidenceObservation(
                observation_type=ObservationType.LOCATION, role=LocationRole.ADDITIONAL_PRACTICE_LOCATION.value,
                observed_value={**row.address, "kind": OBS_ADDITIONAL_LOCATION, "telephone": row.telephone},
                normalized_value=normalize_address(row.address),
                source_field="Provider Secondary Practice Location Address",
                provenance=self._provenance("pl", row.line_number, "Provider Secondary Practice Location Address"),
                **common))
        if org.mailing.get("line1"):
            out.append(EvidenceObservation(
                observation_type=ObservationType.LOCATION, role=LocationRole.MAILING_LOCATION.value,
                observed_value={**org.mailing, "kind": OBS_MAILING_LOCATION},
                normalized_value=normalize_address(org.mailing),
                source_field="Provider Business Mailing Address",
                provenance=self._provenance("npidata", org.line_number, "Provider Business Mailing Address"),
                **common))
        return out


def _type_description(code: Optional[str]) -> Optional[str]:
    from .schema import OTHER_ORG_NAME_TYPE_CODES
    entry = OTHER_ORG_NAME_TYPE_CODES.get((code or "").strip())
    return entry[0] if entry else None
