# Proposal: Excel/CSV header-and-value identifier check before upload

**PROPOSAL ONLY - NOT DELIVERED - AI workspace ON HOLD**

**DRAFT - NOT FINAL - INDEPENDENT QA PENDING**

Date: 2026-11-14. Status: proposal for an owner decision. This document adds no
code, no route, no screen, no migration and no configuration. Nothing described
here exists in the application.

## Why this is a proposal and not a change

The protected-identifier guard in PR #219 (`app/Tefca/protected_identifiers.py`)
checks text on its way to the TEFCA AI gateway. Its text rule catches labelled
values and the written `NN-NNNNNNN` shape. It does not catch nine bare digits or
an SSN-shaped value that carries no label.

A spreadsheet is the case where that limit matters most: the label is the column
heading and the value sits in a cell hundreds of rows below it, so the value has
no label next to it in the text. A check that reads the heading and the cells of
the same column together would close that gap for tabular files.

That check is only useful at the point where a person hands a file to an AI
feature. The only planned surface of that kind is the AI File Workspace, which
is ON HOLD: not wired, not deployed, not enabled. Building the screen now would
build an entry point for a feature that has no approval to run. It is therefore
recorded here and listed as not delivered.

## Proposed scope (for decision, not built)

1. **Header check.** Each column heading of an uploaded `.xlsx` / `.csv` is
   tested with the existing `is_protected_field`. A column whose heading names a
   tax identifier is marked protected.
2. **Value check under a protected heading.** Every non-empty cell in a
   protected column is treated as a protected value, whatever its shape. This is
   what catches nine bare digits.
3. **Value check everywhere else.** Every other cell is tested with the existing
   `find_in_text`, which catches labelled values and the `NN-NNNNNNN` shape.
4. **Result shown before anything is sent.** The screen lists the protected
   columns and the count of flagged cells by column and rule. It never shows a
   flagged value.
5. **Fail closed.** A file with any protected column or flagged cell cannot be
   passed to an AI feature. Whether a redacted copy may proceed is an open
   decision (below).
6. **Server side.** The check runs on the server. A browser-only check is not a
   control.

## What is NOT delivered

- No upload screen, route, parser or storage for this purpose.
- No change to `/tefca-arc/cases/import`, `/tefca-arc/deliveries`,
  `/tefca-arc/iqvia` or `/tefca-arc/import`. None of them sends a file to an AI
  service, and none is changed by this proposal.
- No detection of unlabelled nine-digit or SSN-shaped values in free text. That
  limit of PR #219 stays as stated in that PR.
- No coverage of the modules outside the TEFCA AI gateway that call AI
  providers directly.
- No AI File Workspace work of any kind.

## Open decisions (owner)

| # | Decision | Why it is needed |
|---|---|---|
| 1 | Is the AI File Workspace approved to proceed at all? | This screen has no purpose without it. |
| 2 | May a file continue with its protected columns removed, or is the whole file refused? | Removal is convenient; refusal is simpler to audit. |
| 3 | Should unlabelled SSN-shaped values (`NNN-NN-NNNN`) in any cell block a file? | The shape also matches other numbers; blocking it will refuse some clean files. |
| 4 | Who may see which columns were flagged, and is the event audited? | No permission for this exists today; none is invented here. |
| 5 | Is there executed BAA evidence for any AI provider? | `docs/compliance/AI_EGRESS_PHI.md` lists it as a required action; without it no file content should reach a provider. |
| 6 | Are uploaded files stored, and for how long? | A file that contains a tax identifier becomes a stored copy of it. |

## Dependencies

- PR #219 (the guard and its vocabulary) merged.
- A decision on item 1 above.

## Test approach if approved

Synthetic files only. Identifier-shaped test values use prefixes that are not
issued. No real delivered file is opened by an AI coding assistant or sent to
any AI service.
