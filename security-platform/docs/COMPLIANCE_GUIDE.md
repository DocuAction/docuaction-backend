# How compliance mapping works

Each `Finding` carries a `ComplianceMapping` with `cwe`, `owasp_top10`,
`owasp_api_top10`, `owasp_asvs`, `nist_800_53`, `hipaa` and a `cwe_top25` flag.
Plugins set these; `core/compliance.py` rolls them up; `core/compliance_reports.py`
produces the framework matrices and evidence packages.

## Two different things are called "coverage"

| Term | Means | Used by |
|---|---|---|
| **detection coverage** | Share of a framework the RULESET can detect | the release gate |
| **controls with findings** | Where issues actually exist (higher is worse) | the matrices |

Conflating them produces a number that sounds like assurance but is not. Both are
computed and both are labelled.

## The honesty rule

A control with no automated finding is **NOT ASSESSED**, never "compliant". Automated
analysis can show a control is BROKEN; it can almost never show one is SATISFIED.
Whole NIST families (AT, PS, PE, MA, MP, PL, PM), all HIPAA Physical safeguards and
most Administrative safeguards cannot be evidenced by any scanner, and the matrices say
so explicitly.

## Adding a framework

1. Add the field to `ComplianceMapping` in `core/models.py`.
2. Populate it in the plugins that can speak to it.
3. Add a catalogue and a matrix builder in `core/compliance.py`.
4. Add a writer in `core/compliance_reports.py`.
