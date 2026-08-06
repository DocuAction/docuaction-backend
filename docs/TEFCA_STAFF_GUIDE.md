# TEFCA Registry — Staff Guide

**Contract:** 7571MN26F80064 · **Audience:** reviewers and operators, not developers

This is a walkthrough of the work: what a verification does, how to read its
result, and what to do when something looks wrong. It assumes no knowledge of
the codebase.

---

## 1. What the system actually does

For each entity in the registry it asks three authoritative sources whether that
organisation is who it claims to be, then classifies the answer into one of four
buckets (B1-B4). A human reviews the classification. **The system never has the
last word — you do.**

| Source | What it answers | Operated by |
|---|---|---|
| **NPPES** | Does this NPI exist, and whose is it? | CMS / HHS |
| **PECOS** | Is this provider enrolled in Medicare? | CMS |
| **OIG LEIE** | Is this provider excluded from federal programs? | OIG / HHS |

Three further sources appear in results as `not_checked` **with a stated
reason**. That is deliberate — a source silently missing from a result reads as
an oversight, whereas a disclosed gap is a decision someone made:

| Source | Why it is not checked |
|---|---|
| SAM.gov | API key configured; entity lookup endpoints returning 404, API version under investigation |
| State registry | Connector not implemented |
| IRS | Connector not implemented — IRS data is keyed on EIN, which the registry does not hold |

---

## 2. The five verification states

Reading these correctly matters more than anything else in this guide, because
two of them look similar and mean opposite things.

| State | Meaning | Counts against the entity? |
|---|---|---|
| `verified` | The source confirmed the entity | No — this is the good outcome |
| `not_found` | The source answered, and has no record | **Yes** — a statement about the entity |
| `unavailable` | The source did not answer (outage, timeout) | **No** — a statement about the *source* |
| `not_checked` | No connector, or nothing to look up | No |
| `excluded` | LEIE has an **active** exclusion | **Yes** — disqualifying |

> **`not_found` and `unavailable` must never be conflated.** One says "this
> organisation is not in the register"; the other says "we could not reach the
> register today". Treating an outage as a finding turns a bad afternoon at CMS
> into an accusation against a provider.

`excluded` counts only **active** exclusions. A reinstated provider is not
currently excluded.

---

## 3. Reading a review

Each review produces a bucket (B1-B4), the rule that fired, and a rationale. The
rules engine is the sole classifier.

**Entity resolution** appears alongside the classification and is **advisory
only**. It compares the registry's name and address against what the sources
returned, in order:

1. **Exact identifier** — same NPI/TEFCAID is decisive either way
2. **Address** — normalised to USPS Publication 28 form, so "123 North Main
   Street, Suite 400" and "123 N MAIN ST STE 400" are recognised as one address
3. **Name** — similarity scoring that ignores legal-form suffixes, so
   "Mercy Health LLC" and "Mercy Health Inc." match
4. **AI** — only when steps 1-3 disagree, and only if it has been switched on

Most differences are formatting and are settled at steps 2-3 at no cost. If you
see `requires_manual_review: true`, the deterministic checks disagreed and the
call is yours.

A differing ZIP or state means **not a match**, regardless of how similar
everything else looks — two suites in one building share almost every word, and
so do two branches of one chain in different cities.

---

## 4. When AI is involved

AI is **off by default** (`AI_ENTITY_RESOLUTION=disabled`) and the system is
fully functional without it. When enabled:

- It never decides. It produces a recommendation; a reviewer accepts or rejects.
- Confidence **≥ 0.95** → recommendation shown
- **0.70-0.94** → mandatory manual review, recommendation is context only
- **< 0.70** → **discarded entirely** — you will not see it, because a
  low-confidence guess dressed as evidence is worse than no guess
- Only public directory data is ever sent: organisation name, business address,
  NPI, entity type. **Never PHI, patient data, or SSNs.**
- Every call is logged with model, prompt version, input, output, confidence,
  threshold, latency and build version.

Full detail: `docs/AI_GOVERNANCE.md`.

---

## 5. Common situations

**"A source says unavailable."**
Not a finding. Re-run the verification later. If it persists across days, raise
it — the source may have changed its API.

**"The entity name doesn't match but it's obviously the same organisation."**
Expected — that is what resolution is for. Check `entity_resolution.method`. If
it says `address+name` and `is_match: true`, the system agrees with you.

**"Resolution says requires_manual_review."**
The deterministic checks disagreed: usually the name matches but the address
does not, or vice versa. Look at the addresses yourself. A different ZIP is
usually a genuinely different site.

**"An article/entity looks far too old."**
For the registry, check the source's own data. For the **bulletin**, a hard
48-hour rail rejects anything older regardless of source, and undated items are
marked "No Date" in the QA sheet rather than being given today's date.

**"The classification looks wrong."**
The rule that fired is recorded on the review (`classification_rule` and its
version). Quote that when raising it — it identifies the exact logic.

---

## 6. Running things by hand

Both require an admin token.

**Collect the bulletin now**, instead of waiting for 00:01 ET:

```
POST /api/v1/bulletin/collect
{"agency_id": "fcc", "dry_run": true}
```

`dry_run: true` reports what the window would admit and collects nothing. Drop
it to run for real. It never sends anything — collection and delivery are
separate.

**Check Perigon's quota** (the news provider has a 150 requests/month tier):

```
GET /api/v1/bulletin/perigon/health
```

Reports budget remaining, calls today, and cache hits. Read-only by default —
`?probe=true` makes a live call, which spends from the quota it reports.

---

## 7. The two Excel downloads

| Button | Who it is for | Contents |
|---|---|---|
| **Download FCC Bulletin** | The FCC | Three sheets: the bulletin (columns A-K), a Google News cross-check, and a summary |
| **Download QA Review** | Internal only — requires login | The same columns plus QA Score, Duplicate Flag, URL Status, Word Count, Google News Match |

In the QA sheet:

- **QA Score `REVIEW`** — the summary is outside the 60-100 word target
- **Duplicate Flag `AMP`** — an AMP copy of a story kept elsewhere
- **URL Status `No Date`** — the source gave no usable date; you decide whether
  to keep it
- **Google News Match `ADDED`** — Google News had this and our own sources did
  not. The QA pass caught it.

The **Google News Cross-Check** sheet lists stories Google News carried that our
sources missed. If it says *"All Google News articles matched"*, the check ran
and found nothing — that is a pass, not an empty sheet.

---

## 8. What to escalate

- A source `unavailable` for more than two consecutive days
- Any entity classified B4 (disqualifying) — always worth a second pair of eyes
- Bulletin articles older than two days appearing despite the freshness rail
- Perigon reporting `quota_exceeded` before month end
- Anything where the recorded rationale does not match what you can see in the
  source data
