# Frontend Performance Review

> Static source review of `frontend/src` (Next.js 16 App Router, **static export**, React 18). Read-only.

## Dependency & bundle weight
- **11 runtime deps + 6 devDeps** (`frontend/package.json`) — lean count, but several are **heavyweight and statically imported into every bundle that touches them**:
  - `recharts@3.9.0` (charts — large), `xlsx` (SheetJS, pinned to a **CDN tarball**, line 22 — very large), `jspdf@4.2.1` + `html2canvas-pro@2.2.0` (client PDF/canvas — heavy), `docx@9.7.1`, `date-fns@4.4.0`, `lucide-react@1.23.0`.
  - **`@tanstack/react-table@8.21.3` — installed but 0 imports in `src/`.** Dead dependency (also flagged in Part 4 DS-11). Remove.
- No AI SDKs in the frontend (AI is server-side) ✅.

## Code splitting — **NONE**
- **0 `next/dynamic`, 0 `React.lazy`/`lazy()`** across `src/`. Nothing is lazily loaded.
- The heavy libs (recharts / xlsx / jspdf / html2canvas / docx) are **statically imported**, so they land in initial/route chunks rather than deferring to the route or user interaction that needs them (export buttons, chart panels).
- **This is the single biggest frontend performance lever.** Wrapping the export/report and charting modules in `next/dynamic` would cut initial JS materially.

## React render optimization
- **110** `useMemo`/`useCallback`/`memo` occurrences across 34 files — concentrated in dashboard/analytics widgets. Several large list/table pages have low counts (e.g. `tefca-registry/entities/page.js` ~2; some `tefca-arc` list pages ~1).
- `platform/components/DataTable.js:36-50` re-sorts the **entire** row array (`[...rows].sort()`, line 41) inside `useMemo` on each dependency change — functionally correct, but pairs badly with the pagination issue below.

## Large lists / virtualization — **NONE**
- **0 `react-window`/`react-virtual`/`useVirtualizer`.** No virtualization anywhere.
- `platform/components/DataTable.js` is the universal table and has a **latent full-render bug:** `pageSize` **defaults to 0** (line 30); when 0, `visible = sorted` (lines 52-57) → **every row renders**. The `tefca-arc` adapter passes `pageSize=25`, but any platform-level caller relying on the default renders all rows.
- Even when paginated, the table **holds the full dataset client-side** and sorts all of it in JS; pagination is a client `.slice()` (line 56). The **server returns the whole set** — no server-side paging. At 100K+ rows this ships and sorts everything in the browser.
- Unbounded result `.map()` in `tefca-arc/search/page.js:110,129,142` (no cap); `GlobalSearch.js:40` correctly caps at 20.

## Images
- **0 `next/image`, 0 raw `<img>`** — no image-optimization concern (and static export disables next image optimization regardless). ✅

## API call patterns
- **No SWR / react-query** (0 usages) → **no client-side caching or request dedup**; every mount re-fetches. 222 `useEffect`/`fetch` occurrences across 95 files; heavy pages fire **multiple fetches on mount** (`dashboard/page.js`, `trust/page.js`, `documents/page.js`, `decisions/page.js`, bulletin `DailyBriefingTab.js`).
- **Race-guard fix (good pattern):** `tefca-arc/components/GlobalSearch.js` — 300ms debounce + `active` race guard + cleanup (lines 21-42).
- **Remaining unguarded race:** `tefca-arc/search/page.js:28-41` — has the debounce but **no race guard**; `run()` calls `setRes()` unconditionally, so a slow earlier request can overwrite a newer result. (The `lib/api.js:157-168` 30s AbortController does not cancel superseded searches.) → apply the GlobalSearch pattern here.

## Static export implications
- Confirmed `output: 'export'`, `trailingSlash: true`, `images: unoptimized` (`next.config.js:39-41`).
- Consequences: **no SSR/ISR, no API routes, no next/image optimization, no middleware.** All data is fetched **client-side at runtime** against `https://api-prod.docuaction.io`.
- Net: first paint is fast (pure static HTML/CSS on SWA CDN), but **every data view is a client round-trip with no server cache in front** — which is exactly why the missing client cache (SWR) and missing backend cache (Part: caching) compound.

## Frontend performance verdict
The static-export shell is fast to paint, but the app **ships too much JS up front** (no code splitting on heavy libs + a dead table lib) and **re-fetches everything on every mount** (no client cache), while the universal table **has no virtualization and paginates client-side**. None of these bite at today's data volumes; all three become real at scale or on slow networks. Highest-value fixes: (1) `next/dynamic` the export/chart modules, (2) fix `DataTable` default `pageSize` + add server-side paging/virtualization for large sets, (3) add SWR (or a thin cache) + the race guard on the search page, (4) drop `@tanstack/react-table`.
