# Continuous synthetic journey (EVIDENCE BRANCH ONLY)

Not part of the product and not for merge. `journey_server.py` runs the real application with
`ENTITY_RESOLVER_SOURCE=db` and with the external source connectors (NPPES, OIG LEIE, SAM.gov, CMS)
REPLACED BY DETERMINISTIC STUBS. Nothing it shows is live-source validation.
`journey.py` drives one synthetic delivery through the real routes and workers and exits non-zero
unless every step passes. `seed_users.py` creates synthetic users and mints tokens with the
application's own functions. Synthetic data only.
