"""Historical delta — WHAT CHANGED between a prior and a current observation set.

Compares like with like: same observation type, same source, same role,
normalised value. A name change from the delivering program is a delta about
the SUBJECT; a change in what NPPES says is a delta about the EVIDENCE. Both
are reported, each labelled with its source.

Then, given the current comparison signals, says whether the change is
POTENTIALLY explainable — NAME_CHANGED + DBA_MATCH_IDENTIFIED is an
explainable-variation SIGNAL, not a conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .comparison import (ComparisonResult, Dimension, EXPLAINING_LOCATION_SIGNALS,
                         EXPLAINING_NAME_SIGNALS, CORROBORATING_SIGNALS)
from .explanations import explain
from .normalize import normalize_address, normalize_name
from .observations import EvidenceObservation, ObservationType, SourceAuthority

DELTA_RULES_VERSION = "1.0"


class DeltaType(str, Enum):
    NAME_CHANGED = "NAME_CHANGED"
    ADDRESS_CHANGED = "ADDRESS_CHANGED"
    IDENTIFIER_CHANGED = "IDENTIFIER_CHANGED"
    RELATIONSHIP_CHANGED = "RELATIONSHIP_CHANGED"
    NEW_VALUE = "NEW_VALUE"
    REMOVED_VALUE = "REMOVED_VALUE"
    NEW_ENTITY = "NEW_ENTITY"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    UNCHANGED = "UNCHANGED"


class VariationSignal(str, Enum):
    EXPLAINABLE_VARIATION_SIGNAL = "EXPLAINABLE_VARIATION_SIGNAL"
    EXPLAINABLE_LOCATION_VARIATION_SIGNAL = "EXPLAINABLE_LOCATION_VARIATION_SIGNAL"
    UNEXPLAINED_VARIATION = "UNEXPLAINED_VARIATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"          # nothing changed


@dataclass(frozen=True)
class HistoricalDelta:
    delta_type: DeltaType
    observation_type: ObservationType
    source_id: str
    role: Optional[str]
    before: Optional[Dict[str, Any]]
    after: Optional[Dict[str, Any]]
    prior_observation_id: Optional[str]
    current_observation_id: Optional[str]
    explanation: str
    variation: VariationSignal = VariationSignal.NOT_APPLICABLE
    variation_basis: List[str] = field(default_factory=list)   # comparison signals relied on
    #: True when the delta is about the SUBJECT (the program delivery). A
    #: change in what an evidence source says is reported but never analysed
    #: as a "variation" of the delivered identity.
    subject: bool = False
    rules_version: str = DELTA_RULES_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {"delta_type": self.delta_type.value, "observation_type": self.observation_type.value,
                "source_id": self.source_id, "role": self.role, "before": self.before, "after": self.after,
                "prior_observation_id": self.prior_observation_id,
                "current_observation_id": self.current_observation_id,
                "explanation": self.explanation, "variation": self.variation.value,
                "variation_basis": list(self.variation_basis), "subject": self.subject,
                "rules_version": self.rules_version}


_CHANGE_TYPE = {ObservationType.NAME: DeltaType.NAME_CHANGED,
                ObservationType.LOCATION: DeltaType.ADDRESS_CHANGED,
                ObservationType.IDENTIFIER: DeltaType.IDENTIFIER_CHANGED,
                ObservationType.RELATIONSHIP: DeltaType.RELATIONSHIP_CHANGED}
_WHAT = {ObservationType.NAME: "name", ObservationType.LOCATION: "address",
         ObservationType.IDENTIFIER: "identifier", ObservationType.RELATIONSHIP: "relationship"}


def _norm(o: EvidenceObservation) -> str:
    if o.observation_type is ObservationType.NAME:
        return normalize_name(o.observed_value.get("name"))
    if o.observation_type is ObservationType.LOCATION:
        n = normalize_address(o.observed_value)
        return "|".join([n["line1"], n["line2"], n["city"], n["state"], n["zip5"]])
    if o.observation_type is ObservationType.IDENTIFIER:
        return str(o.observed_value.get("value", "")).strip()
    return normalize_name(o.observed_value.get("related_entity_name")) + "|" + str(o.observed_value.get("related_entity_id", ""))


def _display(o: Optional[EvidenceObservation]) -> Optional[str]:
    if o is None:
        return None
    v = o.observed_value
    if o.observation_type is ObservationType.NAME:
        return v.get("name")
    if o.observation_type is ObservationType.LOCATION:
        return ", ".join(str(v.get(k)) for k in ("line1", "city", "state", "postal_code") if v.get(k))
    if o.observation_type is ObservationType.IDENTIFIER:
        return v.get("value")
    return v.get("related_entity_name")


def _slots(observations: List[EvidenceObservation]) -> Dict[Tuple[str, str, Optional[str]], List[EvidenceObservation]]:
    out: Dict[Tuple[str, str, Optional[str]], List[EvidenceObservation]] = {}
    for o in observations:
        if o.observed_value.get("unavailable"):
            continue
        out.setdefault((o.source_id, o.observation_type.value, o.role), []).append(o)
    return out


def _is_subject(*obs: Optional[EvidenceObservation]) -> bool:
    return any(o is not None and o.source_authority is SourceAuthority.PROGRAM_DELIVERY for o in obs)


def compute_deltas(prior: List[EvidenceObservation], current: List[EvidenceObservation]) -> List[HistoricalDelta]:
    """Slot = (source, type, role). Within a slot, values are compared by
    normalised content; single-valued slots produce CHANGED, multi-valued slots
    produce NEW_VALUE / REMOVED_VALUE per member."""
    if not prior:
        return [HistoricalDelta(DeltaType.NEW_ENTITY, ObservationType.IDENTIFIER, "", None, None, None,
                                None, None, explain("DELTA_NEW_ENTITY"))]
    before, after = _slots(prior), _slots(current)
    deltas: List[HistoricalDelta] = []
    for key in sorted(set(before) | set(after), key=str):
        source_id, otype, role = key
        kind = ObservationType(otype)
        what = _WHAT[kind]
        p, c = before.get(key, []), after.get(key, [])
        p_by = {_norm(o): o for o in p}
        c_by = {_norm(o): o for o in c}
        if p_by.keys() == c_by.keys():
            for n, o in c_by.items():
                deltas.append(HistoricalDelta(DeltaType.UNCHANGED, kind, source_id, role,
                                              {"value": _display(p_by[n])}, {"value": _display(o)},
                                              p_by[n].observation_id, o.observation_id,
                                              explain("DELTA_UNCHANGED", what=what), subject=_is_subject(o)))
            continue
        if len(p) == 1 and len(c) == 1:
            deltas.append(HistoricalDelta(_CHANGE_TYPE[kind], kind, source_id, role,
                                          {"value": _display(p[0])}, {"value": _display(c[0])},
                                          p[0].observation_id, c[0].observation_id,
                                          explain("DELTA_CHANGED", what=what, before=_display(p[0]), after=_display(c[0])),
                                          subject=_is_subject(p[0], c[0])))
            continue
        for n, o in c_by.items():
            if n not in p_by:
                deltas.append(HistoricalDelta(DeltaType.NEW_VALUE, kind, source_id, role, None,
                                              {"value": _display(o)}, None, o.observation_id,
                                              explain("DELTA_NEW_VALUE", what=what, after=_display(o)), subject=_is_subject(o)))
        for n, o in p_by.items():
            if n not in c_by:
                deltas.append(HistoricalDelta(DeltaType.REMOVED_VALUE, kind, source_id, role,
                                              {"value": _display(o)}, None, o.observation_id, None,
                                              explain("DELTA_REMOVED_VALUE", what=what, before=_display(o)), subject=_is_subject(o)))
        for n, o in c_by.items():
            if n in p_by:
                deltas.append(HistoricalDelta(DeltaType.UNCHANGED, kind, source_id, role,
                                              {"value": _display(p_by[n])}, {"value": _display(o)},
                                              p_by[n].observation_id, o.observation_id,
                                              explain("DELTA_UNCHANGED", what=what), subject=_is_subject(o)))
    return deltas


def explain_deltas(deltas: List[HistoricalDelta], comparisons: List[ComparisonResult]) -> List[HistoricalDelta]:
    """Attach a variation signal to each change in the DELIVERED values, based
    on the current comparison signals. Evidence-side deltas (a source changed
    what it says) are reported as-is; explaining them is the analyst's task."""
    by_dim = {c.dimension: c for c in comparisons}
    out: List[HistoricalDelta] = []
    for d in deltas:
        if d.delta_type in (DeltaType.UNCHANGED, DeltaType.NEW_ENTITY) or not d.subject:
            out.append(d)          # evidence-side deltas are reported, not "explained"
            continue
        if d.observation_type is ObservationType.NAME:
            comp = by_dim.get(Dimension.NAME_IDENTITY)
            explaining = EXPLAINING_NAME_SIGNALS
            signal = VariationSignal.EXPLAINABLE_VARIATION_SIGNAL
        elif d.observation_type is ObservationType.LOCATION:
            comp = by_dim.get(Dimension.LOCATION_IDENTITY)
            explaining = EXPLAINING_LOCATION_SIGNALS
            signal = VariationSignal.EXPLAINABLE_LOCATION_VARIATION_SIGNAL
        else:
            comp, explaining, signal = None, set(), VariationSignal.EXPLAINABLE_VARIATION_SIGNAL
        if comp is not None and (comp.signal in explaining or comp.signal in CORROBORATING_SIGNALS):
            out.append(HistoricalDelta(**{**d.__dict__, "variation": signal,
                                          "variation_basis": [comp.signal.value],
                                          "explanation": d.explanation + " " + explain("EXPLAINABLE_VARIATION", signal=comp.signal.value)}))
        else:
            basis = [comp.signal.value] if comp is not None else []
            out.append(HistoricalDelta(**{**d.__dict__, "variation": VariationSignal.UNEXPLAINED_VARIATION,
                                          "variation_basis": basis,
                                          "explanation": d.explanation + " " + explain("UNEXPLAINED_VARIATION", signal=(comp.signal.value if comp else "no comparison"))}))
    return out
