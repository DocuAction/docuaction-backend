# Active falsification — future extension (DESIGN NOTE, NOT BUILT)

## Idea

Today the engine asks "does available evidence corroborate the delivered value?" A falsification pass would ask the opposite: "what evidence, if it existed, would show the delivered value is wrong — and do we have it?" For each dimension the engine would enumerate the specific disconfirming observations it looked for and state whether each was found, absent, or not obtainable.

Example output for LOCATION: "Looked for: an NPPES primary or additional practice location matching the delivered street and ZIP (found); a USPS DPV 'vacant' indication for the delivered address (not consulted — Google not approved); a state registry principal office elsewhere (not consulted — no connector)." The analyst sees what was and was not tested, not only what agreed.

## Why it fits this program

- It makes "no evidence of a problem" and "we did not look" different sentences.
- It is still descriptive: a disconfirming observation is a CONFLICT, already in the vocabulary; the pass only adds the explicit list of checks.
- It strengthens the argument that 25,000 source records do not imply 25,000 reviews: the checks that were actually possible are enumerated per entity.

## Why not tonight

- It needs sources that do not exist yet (Google, state registries) to be meaningful; with NPPES alone the list is short and the current comparisons already cover it.
- It would add a new output structure (`checks_performed[]`) to the run record and therefore a persistence and UI change.
- The methodology question — whether an enumerated "not consulted" list is helpful or alarming to analysts and to ONC — has not been asked.

## Shape if approved

```
FalsificationCheck(dimension, description, source_id, outcome: FOUND | ABSENT | NOT_CONSULTED | SOURCE_UNAVAILABLE, observation_ids)
EntityIntelligenceRun.checks_performed: List[FalsificationCheck]
```

No arithmetic, no score; the assessment rules are unchanged. A FOUND disconfirming check is already a CONFLICT signal; the list is explanatory only.
