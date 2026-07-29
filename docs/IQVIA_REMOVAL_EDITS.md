# IQVIA Reference Removal — Task 2 Review Methodology Document

**Date:** 2026-07-29
**Status:** Edits documented for manual application. **No .docx was modified.**

---

## Why this is a manual list rather than a completed change

The Task 2 Review Methodology document **is not in either repository.** All five
target phrases were searched for across `docuaction-backend` and
`docuaction-frontend`, including every `.md`, `.py`, `.json` and text file:

| Phrase | Files containing it |
|---|--:|
| "including the IQVIA data extract AGT will receive from ONC" | 0 |
| "COR-provided entity data, including the IQVIA data extract" | 0 |
| "IQVIA entity data (COR-provided)" | 0 |
| "ONC/IQVIA extract" | 0 |
| "ONC/IQVIA data extract (population identification only)" | 0 |
| The string `ONC/IQVIA` in any form | 0 |

The document is a `.docx` held outside version control. Apply the edits below in
Word.

---

## The five edits

Use Word's Find and Replace (Ctrl+H). Enable **Match case**.

| # | Find | Replace with |
|--:|---|---|
| 1 | `including the IQVIA data extract AGT will receive from ONC` | `including the entity data extract AGT will receive from ONC` |
| 2 | `COR-provided entity data, including the IQVIA data extract` | `COR-provided entity data, including the entity data extract` |
| 3 | `IQVIA entity data (COR-provided)` | `COR-provided entity data` |
| 4 | `ONC/IQVIA extract` | `ONC entity extract` |
| 5 | `ONC/IQVIA data extract (population identification only)` | `ONC-provided data extract (population identification only)` |

Apply **4 and 5 in that order won't work** — edit 4 would consume part of the
phrase in edit 5. Run **edit 5 first**, then edit 4, then 1–3 in any order.

After replacing, search the document for `IQVIA` once more. Any remaining hit is
a phrasing this list did not anticipate and needs a judgement call.

---

## IMPORTANT — do not strip IQVIA from the codebase

A repository-wide search finds **26 backend files and 13 frontend files**
containing "IQVIA". **None of them are the claim being corrected.** They all refer
to **IQVIA OneKey**, a planned third-party data connector:

```
app/…/IQVIAOneKeyConnector          class name
IQVIA_ONEKEY_API_KEY                environment variable
docs/architecture/system-overview.md  "IQVIA OneKey | Pending | Integration in progress"
docs/compliance/SECRETS_MANAGEMENT.md listed among API keys
docs/api/api-overview.md              "IQVIA OneKey (pending)"
```

That is a **different subject**: a commercial provider-hierarchy data source the
platform is built to integrate with, currently unconfigured and marked pending.
It is not a claim that ONC supplies AGT with an IQVIA extract.

A global find-and-replace on "IQVIA" across the repository would rename a
connector class, break an environment variable that configuration code reads by
name, and corrupt the architecture documentation — while fixing nothing about the
Task 2 document. **These references were deliberately left untouched.**

If AGT genuinely has no IQVIA relationship of any kind, then the OneKey connector
is planned work that should be cancelled through a normal change, not deleted by
text substitution. That is a product decision and is flagged here rather than
made.

---

## Verification after applying the edits

1. Search the .docx for `IQVIA` — expect zero hits.
2. Confirm no sentence lost its meaning, particularly edits 3 and 5 where the
   replacement is shorter than the original.
3. Confirm the document still attributes the data to **ONC / the COR**, which is
   the factual position being restored.
