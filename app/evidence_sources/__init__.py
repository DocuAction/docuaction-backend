"""Evidence-source adapters — source-specific, program-agnostic.

Each adapter turns a PRESERVED delivery (a file DocuAction downloaded, or one a
third party handed over) into `app.core.entity_intelligence` observations with
provenance. Adapters live outside Core because Core must not know NPPES, IQVIA,
Google or any state registry exist; they live outside the program modules
because a source is not a program.

Every adapter is gated by the master flag and its own subordinate flag, and an
adapter whose descriptor says `makes_external_calls=False` has no network code
at all — the isolation tests read the source to prove it.
"""
