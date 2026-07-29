# Quickstart - first scan in five minutes

```bash
cd security-platform
python cli.py discover     # inventory the codebase, confirm paths are right
python cli.py scan         # run every available scanner
python cli.py report       # JSON / Markdown / CSV / HTML
python cli.py gate         # release decision, exit 1 on FAIL
```

Or all of it:

```bash
python cli.py full
```

Open `dashboard/index.html` in a browser - it is self-contained, needs no server and
no network.

## Reading the first result

Three things routinely surprise people on a first run:

1. **A scanner marked SKIPPED is not a pass.** The report states which capability was
   lost. Judge coverage from the scanner table, not the finding count.
2. **The first scan has no delta.** Every finding reads as NEW because there is
   nothing to compare against. Run twice before trusting `diff`.
3. **The gate fails a scan where nothing ran.** That is deliberate - a score of 100
   over zero executed scanners is not a clean bill of health.
