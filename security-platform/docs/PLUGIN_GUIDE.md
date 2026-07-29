# Writing a scanner plugin

```python
from core.models import Category, Finding, ScanTarget
from plugins.base import ScannerPlugin


class MyScanner(ScannerPlugin):
    name = "myscanner"              # config key in the project's "plugins" block
    display_name = "My Scanner"
    category = Category.SAST
    required_binary = "myscanner"   # default availability check looks for this

    def run(self, targets: list[ScanTarget]) -> list[Finding]:
        rc, out, err, timed_out = self.exec_bounded(
            [self.binary_path(), "--json", str(targets[0].path)], timeout=900)
        ...
        return findings
```

Drop it in `plugins/`. Discovery is automatic - there is no registry to edit.

## Rules that matter

**Use `exec_bounded`, never `subprocess.run(timeout=)`.** `subprocess.run` kills the
direct child on timeout, then blocks forever reading a pipe that orphaned grandchildren
still hold open. `exec_bounded` writes to files instead of pipes and kills the whole
process tree.

**Probe capability, do not assume it.** `semgrep --version` succeeds on Windows even
though the scanner hangs forever. If a version check can lie, run a real, tiny,
bounded scan in `is_available()`.

**Return `[]`, never raise, for "nothing found".** Raise only for genuine failure - the
manager records it as a tool error and continues with the other scanners.

**Set confidence honestly.** A regex match is not proof. A LOW-confidence finding
presented as certain trains people to ignore the report.

**Normalise severity** through `Severity.coerce()` so cross-tool counts mean something.
