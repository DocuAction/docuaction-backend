# Configuration

## `config/projects/<name>.json`

| Key | Meaning |
|---|---|
| `name`, `display_name`, `description` | Identity |
| `targets[]` | `{name, path, language, package_manager, manifest, enabled}` |
| `exclude_patterns[]` | Merged with `Project.DEFAULT_EXCLUDES` |
| `plugins{}` | Per-plugin config. **Unlisted plugins default to ENABLED** |
| `gate_policy{}` | **Overrides `config/gate_policy.json` key-by-key** |
| `compliance_profiles[]` | Frameworks to report |

Keys beginning `_` are documentation and are stripped before evaluation.

## Gate policy

| Key | Default | Effect |
|---|---|---|
| `block_on_critical` | true | Any Critical fails the gate |
| `block_on_high` | true | Any High fails |
| `min_security_score` | 70 | Score floor |
| `max_critical_cves` / `max_high_cves` | 0 / 5 | Dependency limits |
| `block_on_secrets` | true | Any secret finding fails |
| `require_sbom` | false | Verified by artefact existence |
| `require_at_least_one_scanner` | true | No evidence => no pass |
| `require_owasp_coverage` | 80 | **Detection** coverage, not code coverage |
| `warn_only` | false | Downgrade failures to warnings |

**The project block wins.** Editing only `config/gate_policy.json` has no effect if the
project defines the same key.

## `config/dast.json`

`target_url`, `dev_url`, `never_test[]`, `rate_limit`, `allow_write_tests`,
`credentials{}`. Note `never_test` can only ADD forbidden patterns - the allow-list in
`dast/config.py` is code, not configuration, and cannot be widened from JSON.
