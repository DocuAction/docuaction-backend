"""
What each identifier actually establishes, and where AGT's authority stops.

THE CONFUSION THIS PREVENTS
───────────────────────────
An NPI that resolves cleanly in NPPES feels like the organisation has been
verified. It has not. An NPI establishes that a provider identifier exists and
which organisation CMS associates with it. It says nothing about the entity's
taxpayer identity, and the two are routinely conflated because both are
"the organisation's number".

Confirming that a TIN/EIN belongs to a named organisation requires IRS
authority. There is no public IRS API for verifying a for-profit entity; TEOS
covers only tax-exempt organisations, and IRS data is keyed on EIN, which the
delivered records do not carry. That is a permanent boundary, not a connector
waiting to be built.

THE FOUR RULES
──────────────
An entity whose verification would require restricted Government access:

  1. MUST NOT become PASS because some other identifier matched.
  2. MUST NOT become FAIL because AGT lacks IRS access.
  3. MUST NOT become NO_MATCH — nothing was asked, so nothing was not found.
  4. MUST remain explicitly unresolved, pending Government verification.

WHY NO NEW VOCABULARY
─────────────────────
The five-layer vocabulary already carries almost all of this. Layer 1 has
`INSUFFICIENT_IDENTIFIER` for "we lacked the key" and `LOOKUP_NOT_APPLICABLE`
for "this lookup does not apply". Layer 3 has `INSUFFICIENT_EVIDENCE`, which is
neither a pass nor a failure.

One value was genuinely missing.
`SourceApplicability.PENDING_GOVERNMENT_VERIFICATION` was added because
`NOT_APPLICABLE` means "asking is meaningless for this entity", and this case is
"asking is meaningful and AGT is not permitted to ask". Recording the second as
the first would tell a reader the question does not matter. It matters.

That is one member added to an existing enum, not a new vocabulary layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.evidence_vocabulary import ObservationState
from app.Tefca.evidence_dimensions import Disposition
from app.Tefca.source_applicability import SourceApplicability

IDENTIFIER_BOUNDARY_VERSION = "1.0.0"


@dataclass(frozen=True)
class IdentifierAuthority:
    """One identifier type: what it proves, what it does not, and who can ask."""

    identifier: str
    name: str
    #: The body that can authoritatively confirm it.
    authority: str
    establishes: List[str]
    does_not_establish: List[str]
    #: True when AGT can actually query an authoritative source for it.
    contractor_verifiable: bool
    #: Why not, when not.
    access_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "authority": self.authority,
            "establishes": list(self.establishes),
            "does_not_establish": list(self.does_not_establish),
            "contractor_verifiable": self.contractor_verifiable,
            "access_note": self.access_note,
        }


#: Contractor-accessible. These are the corroborations AGT may actually make.
NPI = IdentifierAuthority(
    identifier="NPI",
    name="National Provider Identifier",
    authority="CMS / NPPES",
    establishes=[
        "That the identifier exists and is well formed",
        "Which organisation CMS associates with it",
        "The registered address CMS holds for it",
        "Taxonomy and enumeration type",
    ],
    does_not_establish=[
        "Taxpayer identity (TIN/EIN/FEIN)",
        "Legal corporate registration",
        "Tax-exempt status",
        "That the organisation is the one the delivered record intended",
    ],
    contractor_verifiable=True,
)

#: NOT contractor-accessible. This is the boundary the phase exists to certify.
TIN = IdentifierAuthority(
    identifier="TIN",
    name="Taxpayer Identification Number",
    authority="Internal Revenue Service",
    establishes=[
        "Taxpayer identity, when confirmed by the IRS",
    ],
    does_not_establish=[
        "Anything at all when unconfirmed — an unverified TIN is a string",
    ],
    contractor_verifiable=False,
    access_note=(
        "No public IRS API exists for verifying a for-profit entity. IRS TEOS "
        "covers only tax-exempt organisations, and IRS data is keyed on EIN, "
        "which the delivered records do not carry. AGT has no authority to "
        "confirm a TIN and will not acquire one under this contract."),
)

EIN = IdentifierAuthority(
    identifier="EIN",
    name="Employer Identification Number",
    authority="Internal Revenue Service",
    establishes=["Taxpayer identity, when confirmed by the IRS"],
    does_not_establish=["Anything at all when unconfirmed"],
    contractor_verifiable=False,
    access_note=TIN.access_note,
)

FEIN = IdentifierAuthority(
    identifier="FEIN",
    name="Federal Employer Identification Number",
    authority="Internal Revenue Service",
    establishes=["Taxpayer identity, when confirmed by the IRS"],
    does_not_establish=["Anything at all when unconfirmed"],
    contractor_verifiable=False,
    access_note=TIN.access_note,
)

UEI = IdentifierAuthority(
    identifier="UEI",
    name="Unique Entity Identifier",
    authority="GSA / SAM.gov",
    establishes=[
        "Federal registration status, when SAM.gov can be queried",
        "Debarment and exclusion status recorded by GSA",
    ],
    does_not_establish=[
        "Taxpayer identity",
        "Clinical or provider credentials",
    ],
    contractor_verifiable=True,
    access_note=(
        "Requires a SAM.gov credential, which has not been issued. Until it is, "
        "SAM.gov answers are recorded as SOURCE_UNAVAILABLE — a fact about the "
        "lookup, never about the entity."),
)

AUTHORITIES = {a.identifier: a for a in (NPI, TIN, EIN, FEIN, UEI)}

#: Identifiers only the Government can confirm.
GOVERNMENT_RESTRICTED = frozenset({"TIN", "EIN", "FEIN"})


def is_government_restricted(identifier: str) -> bool:
    return (identifier or "").strip().upper() in GOVERNMENT_RESTRICTED


def authority_for(identifier: str) -> Optional[IdentifierAuthority]:
    return AUTHORITIES.get((identifier or "").strip().upper())


@dataclass(frozen=True)
class BoundaryState:
    """The controlled five-layer representation of a restricted lookup.

    Constructed only by `government_verification_state`, and deliberately
    carrying every layer at once so a caller cannot pick up the applicability
    and lose the disposition.
    """

    applicability: SourceApplicability
    observation_state: ObservationState
    disposition: Disposition
    rationale: str
    identifier: str
    authority: str

    @property
    def is_resolved(self) -> bool:
        """Always False. That is the entire point."""
        return False

    @property
    def is_adverse(self) -> bool:
        """Always False. Absence of AGT access is not evidence against anyone."""
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applicability": self.applicability.value,
            "observation_state": self.observation_state.value,
            "disposition": self.disposition.value,
            "rationale": self.rationale,
            "identifier": self.identifier,
            "authority": self.authority,
            "is_resolved": self.is_resolved,
            "is_adverse": self.is_adverse,
            "boundary_version": IDENTIFIER_BOUNDARY_VERSION,
        }


def government_verification_state(identifier: str) -> BoundaryState:
    """The only sanctioned representation of a Government-restricted lookup.

    Returns the same three-layer combination every time, because the answer does
    not depend on the entity — it depends on who AGT is.

      Applicability   PENDING_GOVERNMENT_VERIFICATION
      Layer 1         LOOKUP_NOT_APPLICABLE  (AGT cannot perform this lookup)
      Layer 3         INSUFFICIENT_EVIDENCE  (neither pass nor failure)

    Layer 1 is LOOKUP_NOT_APPLICABLE rather than SOURCE_UNAVAILABLE on purpose.
    SOURCE_UNAVAILABLE means a source AGT may query did not answer — a transient
    fact that invites a retry. This is not transient and there is nothing to
    retry: the lookup is not one AGT may perform at all.
    """
    authority = authority_for(identifier)
    if authority is None or authority.contractor_verifiable:
        raise ValueError(
            f"{identifier!r} is not a Government-restricted identifier. This "
            f"state is only for lookups AGT has no authority to perform; using "
            f"it elsewhere would hide a lookup that should have happened.")

    return BoundaryState(
        applicability=SourceApplicability.PENDING_GOVERNMENT_VERIFICATION,
        observation_state=ObservationState.LOOKUP_NOT_APPLICABLE,
        disposition=Disposition.INSUFFICIENT_EVIDENCE,
        rationale=(
            f"{authority.name} verification requires "
            f"{authority.authority} authority, which AGT does not hold. "
            f"{authority.access_note} The entity is UNRESOLVED on this point: "
            f"it is not a pass, not a failure, and no match was attempted."),
        identifier=authority.identifier,
        authority=authority.authority)


def boundary_disclosure() -> Dict[str, Any]:
    """What a report must say about identifier verification.

    Reports disclose this whether or not any entity happened to carry a TIN,
    because the limit is on AGT's authority rather than on the data.
    """
    return {
        "boundary_version": IDENTIFIER_BOUNDARY_VERSION,
        "contractor_verifiable": [
            a.to_dict() for a in AUTHORITIES.values() if a.contractor_verifiable],
        "government_restricted": [
            a.to_dict() for a in AUTHORITIES.values()
            if not a.contractor_verifiable],
        "statement": (
            "NPI verification and TIN/EIN/FEIN verification are not equivalent. "
            "An NPI that resolves in NPPES establishes the provider identifier "
            "and the organisation CMS associates with it; it establishes "
            "nothing about taxpayer identity. AGT holds no authority to confirm "
            "a TIN, EIN or FEIN, and the absence of that authority is never "
            "reported as an adverse finding against an entity."),
        "prohibited": [
            "Reporting an entity as verified because its NPI matched",
            "Reporting an entity adversely because AGT lacks IRS access",
            "Recording a restricted lookup as NO_MATCH_OBSERVED",
            "Recording a restricted lookup as SOURCE_UNAVAILABLE, which implies "
            "a retry would help",
        ],
    }
