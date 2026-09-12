"""Controlled explanation templates.

Every explanation an analyst reads comes from this table — deterministic,
reviewable, and citing only what the evidence contains. No model generates
prose here; a template that needs a fact it was not given renders "unstated".
Every template ends with the human-review sentence because a system signal is
never a determination.
"""
from __future__ import annotations

HUMAN_REVIEW = " Human review required."

TEMPLATES = {
    "SOURCE_UNAVAILABLE": "{source} could not be consulted; this is a fact about access, not about the entity.",
    "MISSING_IDENTIFIER": "The delivered record carries no {system}, so no identifier corroboration is possible.",
    "NO_SOURCE_RECORD": "{source} returned no record for the delivered {system}.",
    "IDENTIFIER_CORROBORATED": "The delivered {system} resolves to one {source} record (entity type: {entity_type}). Identifier corroboration does not establish licensure or program compliance.",
    "MULTIPLE_CANDIDATE_ENTITIES": "The delivered {system} resolves to {count} {source} records; the match is not unique.",
    "IDENTIFIER_CONFLICT": "{source} records a different {system} for this entity than the one delivered.",
    "NO_DELIVERED_NAME": "The delivered record carries no organisation name.",
    "NO_SOURCE_NAMES": "{source} provides no name observations for this entity.",
    "DIRECT_NAME_MATCH": "The delivered organisation name matches the {source} legal business name exactly.",
    "NORMALIZED_NAME_MATCH": "The delivered organisation name matches the {source} legal business name after formatting normalisation.",
    "DBA_MATCH_IDENTIFIED": "The delivered organisation name differs from the {source} legal business name ({legal_name}). Available {source} evidence associates the delivered name with an Other Name record classified as Doing Business As for the matched organisational NPI.",
    "FORMER_NAME_MATCH": "The delivered organisation name differs from the {source} legal business name ({legal_name}). Available {source} evidence associates the delivered name with a record classified as a former legal business name.",
    "OTHER_NAME_MATCH": "The delivered organisation name differs from the {source} legal business name ({legal_name}). Available {source} evidence associates the delivered name with an Other Name record of type {kind}; the source does not classify it as Doing Business As.",
    "AMBIGUOUS_NAME_KINDS": "The delivered organisation name matches more than one {source} Other Name record with different type codes ({kinds}).",
    "AMBIGUOUS_NAME_SUFFIX": "The delivered organisation name and the {source} legal business name share the same core words but differ in organisational suffix; the evidence does not establish that they are the same organisation.",
    "NAME_CONFLICT": "The delivered organisation name does not match the {source} legal business name ({legal_name}) or any of its {other_count} other name record(s).",
    "NO_DELIVERED_ADDRESS": "The delivered record carries no usable address (a first line plus city/state or ZIP is needed).",
    "NO_SOURCE_ADDRESSES": "{source} provides no usable location observations for this entity.",
    "PRIMARY_LOCATION_MATCH": "The delivered location matches the {source} primary practice location.",
    "ADDITIONAL_PRACTICE_LOCATION_MATCH": "The delivered location differs from the {source} primary practice location but corresponds to an available non-primary practice-location record for the matched NPI.",
    "MAILING_LOCATION_MATCH": "The delivered location matches the {source} mailing address, not a practice location.",
    "NORMALIZED_LOCATION_MATCH": "The delivered location matches a {source} location ({role}) after address normalisation.",
    "AMBIGUOUS_LOCATION": "The delivered location shares a locality (city/state or ZIP) with {count} {source} location(s) but the street line differs.",
    "LOCATION_CONFLICT": "The delivered location does not correspond to any of the {count} {source} location(s) on record.",
    "NO_DELIVERED_RELATIONSHIP": "The delivered record carries no relationship of kind {kind}.",
    "NO_SOURCE_RELATIONSHIP": "{source} provides no relationship of kind {kind}.",
    "RELATIONSHIP_NOT_COMPARABLE": "{source} relationships ({other_kinds}) are of a different kind from the delivered relationship ({kind}) and are not compared; a corporate relationship is not a program relationship.",
    "RELATIONSHIP_CORROBORATED": "{source} records the same {kind} relationship as delivered.",
    "RELATIONSHIP_CONFLICT": "{source} records a different {kind} relationship from the one delivered.",
    # deltas
    "DELTA_UNCHANGED": "No change in {what} between the prior and current delivery.",
    "DELTA_CHANGED": "{what} changed between the prior and current delivery: '{before}' → '{after}'.",
    "DELTA_NEW_VALUE": "A new {what} appears in the current delivery: '{after}'.",
    "DELTA_REMOVED_VALUE": "A {what} present in the prior delivery is absent from the current one: '{before}'.",
    "DELTA_NEW_ENTITY": "The entity has no prior delivery to compare against.",
    "DELTA_SOURCE_CHANGED": "The {what} is stated by a different source than before ({before} → {after}).",
    "EXPLAINABLE_VARIATION": "The change is consistent with independent evidence ({signal}); it may be explainable. Human review required.",
    "UNEXPLAINED_VARIATION": "No available evidence explains the change ({signal}). Human review required.",
}


def explain(key: str, **facts: object) -> str:
    template = TEMPLATES[key]
    safe = {k: ("unstated" if v is None else v) for k, v in facts.items()}
    try:
        text = template.format(**safe)
    except KeyError as missing:  # a template asked for a fact it was not given
        text = template.replace("{" + missing.args[0] + "}", "unstated").format_map(
            _Default(safe))
    if key.startswith("DELTA_") or key.endswith("_VARIATION"):
        return text
    return text + HUMAN_REVIEW


class _Default(dict):
    def __missing__(self, key):
        return "unstated"
