# Page Review 01 — Login

- **Route:** `/login`
- **Component File:** `src/app/login/page.tsx`
- **Lines of Code:** 105

## Scores (1–10)
| Dimension | Score |
|---|:--:|
| Layout | 7 |
| Navigation | 6 |
| Visual Hierarchy | 7 |
| Info Density | 8 |
| User Workflow | 8 |
| Consistency | **4** |
| Loading States | 7 |
| Empty States | N/A |
| Error States | 7 |
| Table Usability | N/A |
| Form Usability | 8 |
| Dark Mode | **4** |
| Responsive | 7 |
| Accessibility | 7 |
| **OVERALL** | **6.5** |

## Strengths (top 3)
1. **Solid, accessible form:** `<label htmlFor>` on every field, `autoComplete="username/current-password"`, `autoFocus`, `required`, disabled-while-loading, inline `aria-label` on show/hide password, spinner + "Signing in…" state.
2. **Good workflow:** show/hide password, forgot-password link, create-account link, email normalized (`trim().toLowerCase()`), submit disabled until both fields present.
3. **Clear error surface:** `alert-error` region renders the server message.

## Improvements Needed (top 3)
1. **Styling divergence** — uses a **separate CSS-class system** (`login-card`, `form-group`, `btn-primary`, `alert-error`) instead of the platform token system used by the app. *[Priority: High]* — this is the root of the app's biggest consistency gap.
2. **Dark mode not guaranteed** — as a class-styled page outside the token/theme scope, dark mode likely doesn't apply (auth screen appears light-only). *[Priority: Medium]*
3. **No visible SSO entry point** — Entra ID SSO exists (`auth/callback`) but the login page shows only email/password; add a "Sign in with Microsoft" button for the federal SSO path. *[Priority: Medium]*

*Note:* `if (user) window.location.href=...` redirect-in-render is a minor anti-pattern (should be an effect); hard `window.location.href` navigation (vs router) causes a full reload.
