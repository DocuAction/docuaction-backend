# september_2026_purpose_npi_distribution.csv — provenance

Descriptive distribution of the September 2, 2026 ONC/RCE snapshot (`onc-snapshot-20260902.csv`,
sha256 `6ee7907e437b6f6859c5c98607fcbce16d500e0fb2fc669ae192b031e94264ac`, 24,589 records), as
registered on DEV (intake `4417b334-7440-4c30-9afa-05f4268f17c7`), read-only, 2026-10-26 ~00:55 UTC.

One row per distinct (exact purpose set, NPI supplied, `active`, `sequoiaorgtype`, `hl7orgrole`
populated) combination with its record count. `purpose_set` is the sorted, de-duplicated token set
of the delivered `purposesofuse` cell (split on `, ; |` and whitespace); the 15 distinct raw cells in
the file are all plain comma-separated lists, so this grouping loses nothing the rule reads.

It is INPUT data for `tests/test_npi_applicability_six_cases_2026_10_26.py`: the test applies the
rule to each row and compares the resulting case totals with the independently verified descriptive
counts (methodology v1.4 "NPI Decision Cases" sheet; `DocuAction_Sept_Records_Logic_Draft (5).xlsx`).
No expected case is stored in this file. It contains no names, identifiers or addresses.

Query: `qa-evidence/2026-10-26-release-closeout/EVIDENCE_DOCS/RAW_OUTPUTS_2026-10-26/sept_case_distribution.py`.
