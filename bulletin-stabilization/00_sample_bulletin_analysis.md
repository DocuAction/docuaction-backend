# 00 — Sample Bulletin Analysis

**Phase 0, read-only.**

---

## Status: BLOCKED — the FCC sample bulletin is not available

The brief conditions this document on *"If FCC sample bulletin is available"*. It is not.

Searched, with no match:

```
find . -iname "*sample*daily*"  -o -iname "*sample*bulletin*"
        -o -iname "*FCC*Sample*" -o -iname "*daily*news*summary*"
        (both repos, excluding node_modules / pydeps / .next / out / .git)
/mnt/user-data/uploads/   →  does not exist
```

No `.docx`, `.pdf`, `.html`, or image artifact of a reference bulletin exists in either
repository or the assessment tree.

**I am not going to reconstruct a "sample" from our own output and then grade ourselves
against it.** That would be circular: our renderer would score 100 % against a target
derived from our renderer, and Phase 8's success criterion — *"match or exceed FCC sample
quality"* — would be certified on no evidence. Given this platform is heading for a federal
ATO, a fabricated compliance baseline is a worse outcome than an open gap.

---

## What *is* known about the intended format, from code and in-repo specs

This is the substitute evidence available. It is second-hand — derived from how the code
describes the client's requirements — not from the sample itself.

### The six client display buckets

`engine.py:97` — *"Client display sections (the 6 buckets in the AGT FCC Daily News email)"*.
Section assignment logic lives in `boolean_filter.py` with nine candidate sections:

```
fcc_news · consumers · media_broadcasting · space_policy · public_safety
wireless_spectrum · ai_ml · business_tech · international
```

and a deterministic precedence order when an article matches several.

### Rendering features already implemented

| Feature | Where | Note |
|---|---|---|
| HTML render | `_render_agt_html` (`engine.py:2424`) | ~100 lines |
| Word render with live hyperlinks | `_render_agt_docx` + `_docx_hyperlink` (`engine.py:2527`) | comment: *"matches the client's fcc_digest.py input"* |
| PDF render | `pdf_generator.py` | 185 lines |
| Excel export | `bulletin_download_routes.py` | `/download-excel` |
| Paywall / subscription badge | `_is_paywalled_url` (`engine.py:2192`) | matches brief's "Subscription Required badge" |
| Leadership tagging | `_leadership_prefix` (`engine.py:2173`) | Chairman/Commissioner prefixes |
| Headline cleaning | `_clean_headline` (`engine.py:2212`) | |
| Similar-stories grouping | `_cluster_stories` → `_collect_sections` | one summary, many publishers |
| Preview URL / archive link | `_briefing_preview_url`, `_latest_preview_url` | |
| Summary email wrapper | `_build_summary_email_html` (`engine.py:3504`) | |

Reference to *"the client's Appendix A spec"* (`engine.py:1987`, Boolean section matching)
and *"Appendix B Sources"* (`engine.py:483`, RSS) — **neither appendix is in the repo**.

### Cannot be assessed without the sample

- Table of Contents presence and depth
- "Back to Top" anchors
- Page numbers in Word/PDF
- Outlook-specific HTML safety (table layout, inline CSS, VML fallbacks)
- Mobile responsiveness of the email
- Social Media Summary section
- Header/logo placement, footer content
- Per-article field order and typography
- **Whether our output "matches or exceeds"** — the Phase 8 acceptance criterion

---

## What I need to complete this document

Any **one** of the following unblocks it:

1. The FCC Sample Daily News Summary (`.docx`, `.pdf`, or the original email `.msg`/`.eml`).
2. Appendix A (Boolean spec) and Appendix B (source list) referenced in code.
3. Written confirmation that `_render_agt_html` / `_render_agt_docx` output **is** the
   approved format — in which case Phase 8 becomes "preserve, don't regress", and its
   success criterion should be reworded away from "match or exceed the sample".

**Recommended interim scope for Phase 8:** treat current rendered output as the baseline,
capture a golden-file snapshot of HTML/DOCX/PDF, and gate future changes on *no regression*
against that snapshot. That is verifiable today and remains valid once the real sample
arrives.
