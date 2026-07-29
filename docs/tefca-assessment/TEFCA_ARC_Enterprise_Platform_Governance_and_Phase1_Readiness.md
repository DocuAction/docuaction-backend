# TEFCA ARC — Enterprise Federal Platform Governance & Phase-1 Pre-Implementation Explanation
### Governing principles for all UI work + the mandated "explain-before-code" gate for Phase 1

**Prepared:** 2026-07-09
**Status:** Design approved (APPROVED WITH MINOR CHANGES). This document sets the governing lens and provides the **required per-screen explanation** *before* any UI code is written. **No code is produced here.**
**Frozen & unchanged:** backend, APIs, database, tables, stored procedures, business logic, connectors, validation/review engines, scheduler, authentication, authorization, deployment, infrastructure. No Dev-vs-Production / Azure / microservices / Kubernetes discussion. Fully backward-compatible. *(UI implementation, when it begins, is a frontend concern; this backend repository remains untouched.)*

---

## 1. Enterprise Federal Platform Principle — design implications
ARC is **Module 1** of a future **Enterprise Federal Compliance Platform** (potential modules: Healthcare Claims Validation, Provider Credential Verification, Identity Assurance, Program Integrity, CMS/Medicaid/Medicare Compliance, Fraud Review, Grant Compliance, Audit/Case/Evidence Management, Executive Analytics). Every UI recommendation is therefore evaluated for **multi-agency, multi-module scalability**.

**What the approved blueprint already gets right for this `[VERIFIED design]`:**
- A **single design system** (Fluent tokens, components) → reusable across all future modules unchanged.
- A **role-aware application shell** (rail + command bar + provenance banner) → the shell is module-agnostic; modules plug into it.
- **Lifecycle-based IA** (Import→Review→QA→Disposition→Report→Closeout) → a *pattern* other compliance programs share, not TEFCA-specific plumbing.
- **Clean product identity** (no procurement IDs in operational UI) → the platform isn't branded to one contract.

**Governing adjustments to lock in before build `[RECOMMENDED]`:**
- **Platform shell vs. module content separation:** the left rail's top level must accommodate a future **module switcher** (Azure-Portal/Microsoft-365-style "waffle") — ARC is one entry today; design the shell so adding "Program Integrity" later is a config, not a redesign.
- **Design tokens, not hard-coded styles:** all color/type/spacing via tokens so a future module (or agency theme) can re-skin without touching components.
- **Generic domain vocabulary in shared components** (Entity, Source, Finding, Evidence, Disposition, Cycle, Report) — these already generalize beyond TEFCA; keep component names program-neutral.
- **Extensible role model in the UI** (role→landing map is data-driven) so new roles/agencies map to landings without code forks.
- **Per-module data-provenance & classification labeling** reusing ARC's MOCK/PRODUCTION banner pattern.

**Test:** *"Would this still look appropriate if the platform served 25 federal agencies?"* → The shell/design-system/IA pass; the only requirement is the module-switcher seam above.

---

## 2. FedRAMP / NIST / 508 future-readiness — "leave room, don't implement"
FedRAMP Moderate (→High where practical), NIST SP 800-53 Rev.5, FIPS 199/140-3, Zero Trust, Section 508 / **WCAG 2.2 AA**, EO 14028, OMB M-21-31 & M-22-09, CISA Secure-by-Design, HHS guidance are **future** targets. **No controls are implemented; the UX simply leaves room for them.** Concrete UX affordances to reserve now (design placeholders, not implementations):

| Future control area | UX room to reserve (no implementation) |
|---|---|
| **Audit logging (AU / M-21-31)** | An **audit timeline** surface + exportable audit views already in the blueprint → the UI *shows* what the backend already records (`tefca_qa_audit`, source cache) `[VERIFIED data exists]`. Reserve space; wire later. |
| **Access control / Zero Trust (AC / M-22-09)** | Role-aware landings + least-privilege surfacing + explicit permission-denied UX; auth screens designed to accommodate **MFA / phishing-resistant** steps and re-auth without layout change. |
| **Session management (AC-11/12)** | Reserve a session-status/timeout affordance and re-authentication modal pattern in the shell. |
| **Data classification / provenance (FIPS 199 / RA)** | Reuse the **provenance banner**; add a placeholder for data-sensitivity labels per view. |
| **System-use notification (AC-8)** | Reserve a login-notice / government-system-use banner slot. |
| **Accessibility (508 / WCAG 2.2 AA)** | Bump the design-system target from 2.1→**2.2 AA** (adds focus-appearance, target-size, dragging alternatives, consistent help) — folded into Phase 1. |
| **Configuration/audit of admin actions (CM)** | Admin → System Information + activity feed reserve space for change history. |

**Rule restated:** design so these *fit without rework*; **do not build compliance controls now.**

---

## 3. The six design-principle tests, applied to the blueprint
| Test | Verdict |
|---|---|
| Would **Microsoft** build it? | ✅ Fluent-2 tokens, rail+blades, command bar, KPI tiles, drill-through |
| Would **ServiceNow** build it? | ✅ case lifecycle, SLAs, queues, audit timeline |
| Would **Salesforce Gov Cloud** build it? | ✅ role homepages, 508, provenance |
| Appropriate for **25 agencies**? | 🟡 add the **module-switcher seam** (§1) |
| Appropriate **after FedRAMP**? | ✅ affordances reserved (§2); no control gaps in the UX shell |
| Comfortable for an **HHS exec to present to ONC**? | ✅ executive dashboards + exec-quality reports (once template applied to all 7) |
Net: passes with the single structural addition (module switcher) and the already-listed minor changes.

---

## 4. The mandated per-screen explanation protocol
**Before any UI code for a screen, the team documents these five — implementation begins only after:**
1. **Business objective** — what program outcome the screen advances.
2. **HHS user objective** — which role, what decision/task, how often.
3. **Usability improvement** — why the redesign is faster/clearer than today.
4. **Blueprint alignment** — which approved IA/design-system/workspace pattern it uses.
5. **FedRAMP/enterprise compatibility** — which future-control affordances it reserves and how it stays multi-agency-scalable.

This protocol is applied below to Phase 1, and will be applied to each screen in Phases 2–5 before its build.

---

## 5. Phase 1 — pre-implementation explanation (foundation: Navigation · IA · Design System · Typography · Icons · Colors · Spacing · Accessibility)
Phase 1 ships **no feature screens** — it establishes the shell, design language, and accessibility baseline every later screen inherits. Applying the five-point protocol to the Phase-1 scope:

**5.1 Navigation & Information Architecture**
1. *Business:* a single coherent, lifecycle-organized platform reduces training cost and errors and makes ARC extensible to future modules.
2. *HHS user:* every role reaches its work in ≤3 clicks; the rail groups by Review / Cycles & Sampling / Analytics & Reports / Administration.
3. *Usability:* retires the current dual-nav + duplicate destinations `[VERIFIED problem]`; adds command bar + global search `[VERIFIED /search]` + breadcrumb/as-of.
4. *Blueprint:* implements the single role-aware rail (UX Plan §5).
5. *FedRAMP/enterprise:* reserves the **module-switcher seam**, a **system-use notice slot**, and a **session-status affordance**; role→landing is data-driven for multi-agency reuse.

**5.2 Design System (typography, icons, colors, spacing, elevation, components)**
1. *Business:* one enterprise design language = consistent, professional, reusable across all future modules; lowers build cost per screen.
2. *HHS user:* legible dense data, clear status semantics, predictable interactions.
3. *Usability:* Segoe UI Variable type scale, 4-pt spacing, Fluent components (KPI tile, faceted grid, blade, command bar), semantic status = ARC buckets **+ first-class Indeterminate** `[VERIFIED fail-closed]`.
4. *Blueprint:* UX Plan §4; **plus the two gate punch-list items folded in here: add Dark Mode tokens** and **bump accessibility to WCAG 2.2 AA**.
5. *FedRAMP/enterprise:* tokens-not-hardcoded (agency theming/dark mode without component changes); status conveyed by color **+ icon + label** (508/2.2); provenance/classification banner reserved.

**5.3 Accessibility baseline**
1. *Business:* 508/WCAG 2.2 AA is mandatory for a federal platform and de-risks future ATO.
2. *HHS user:* keyboard-first, screen-reader-labeled, contrast-safe for all roles.
3. *Usability:* focus-appearance, target-size, dragging alternatives, consistent help (2.2 additions); chart data-table fallbacks; accessible grids.
4. *Blueprint:* UX Plan §4.5 (upgraded to 2.2).
5. *FedRAMP/enterprise:* conformance-artifact slot reserved in Trust Center (the "508 Compliant" claim must be backed by a real VPAT/artifact — a standing non-design condition `[VERIFIED gap]`).

**Phase-1 exit criteria:** shell + tokens + components + accessibility baseline pass a design-system review and a keyboard/SR smoke test; module-switcher and control affordances present as reserved seams; Dark Mode tokens defined.

---

## 6. Phasing confirmation (each phase gated by the 5-point explanation; each backward-compatible)
| Phase | Scope | Gate before build |
|---|---|---|
| **P1** | Navigation, IA, Design System, Typography, Icons, Colors, Spacing, Accessibility | §5 (this doc) ✅ |
| **P2** | Executive Dashboard · Reviewer Workspace · QA Workspace | 5-point per screen + **Reviewer-Workspace prototype & usability test** (gate punch-list #2) |
| **P3** | Reporting · Analytics · Import Wizard · Search · Advanced Filtering | 5-point per screen + exec-report template applied to all 7 types |
| **P4** | Operations · Administration · Audit · Configuration | 5-point per screen + Admin→System Information holds all contract metadata |
| **P5** | AI Assistance · Predictive Analytics · Executive Insights | 5-point per screen + human-in-the-loop guardrails (AI is decision-support only) |
Every phase preserves the frozen backend and existing endpoints.

---

## 7. Readiness statement
The governing principles (enterprise multi-agency platform + FedRAMP-readiness) and the design tests are satisfied by the approved blueprint with **one structural addition (module-switcher seam)** and the previously-agreed minor changes (Dark Mode, WCAG 2.2, workspace prototype, exec report template, Support-role scope, KPI pruning + flagship data fix). The **per-screen explanation protocol is defined and applied to Phase 1**.

**Recommendation:** Phase 1 is **cleared to enter UI implementation** (frontend, backend untouched) once the folded-in Phase-1 items (module-switcher seam, Dark Mode tokens, WCAG 2.2 baseline) are reflected in the design-system spec. Phases 2–5 proceed screen-by-screen, each preceded by its five-point explanation. Two standing **non-design** conditions remain open in parallel and do not block Phase 1: the **508 conformance artifact** and the **RFQ/SOW/Proposal/D2 documents** for contract-traceability certification.

*No code, backend, API, database, authentication, connectors, scheduler, deployment, or infrastructure was modified or discussed; no Dev-vs-Production, Azure, microservices, or Kubernetes discussion; no compliance controls implemented — only UX room reserved. `[VERIFIED]` items are grounded in `app/Tefca/` source or the live app; `[RECOMMENDED]` items are design proposals.*
