# T-04 Verification Report

Final verification: 2026-11-18 (Asia/Kolkata). Base commit:
`d2bd3d3880c0bf1962c939bc397459b2bc548ddb`.

**Overall: PASS for T-04 and all three previously reported findings.**
The fixes are implemented and verified with permanent regression tests and the
checks below. This supersedes the earlier FAIL review; none of R1–R3 remains open.
No commit, push, deployment, database deletion, Docker reset or later-ticket
implementation was performed. Awaiting user approval.

## Requirements and scope

Read the full earlier verification report and the exact T-04 prompt/acceptance
criteria in [Feature Ticket List](Feature%20Ticket%20List.md). Cross-checked the
Frontend Specification's component states, accessibility and API conventions,
and the Technical Architecture's web folder layout.

The four T-04 acceptance criteria, verbatim:

- A component showcase page renders every component and state.
- Keyboard focus rings and 44px touch targets are present.
- API client turns the standard error body into a typed error.
- No hard-coded hex values in components.

The original review covered all 53 implementation/configuration/test/documentation
candidates. This follow-up changed only seven existing T-04 source/test files and
this report: AppRoutes, LayoutFrame, Modal, the API client and its tests, Showcase,
and App tests. No dependencies, backend source, infrastructure or routes were added.
“Student” uses the existing `/tenant` preview and `TenantLayout`, as named in T-04.
The complete 54-file T-04 commit manifest is below.

## Resolution of the three findings

### R1 — PASS: keyboard focus survives layout navigation

Route-change focus now lives in `src/app/router.tsx`, above the layouts that
remount. A pathname change focuses the destination `#main-content`. Initial page
load keeps normal browser focus, so the skip link remains the first keyboard stop;
in-page anchors are not intercepted.

`LayoutFrame` marks navigation initiated inside its mobile drawer. The overlay's
optional `onCloseAutoFocus` callback prevents returning focus to the old trigger
only for that navigation. Escape, close-button behavior and same-page drawer
links retain normal trigger-focus restoration. Modified link clicks retain their
browser behavior. The shared Modal default remains Radix's normal restoration.

Eight new permanent focus regression cases verify:

- Keyboard activation through Public → Auth → Tenant → Owner → Admin → Public,
  focusing each destination main and continuing with Tab inside its content.
- Five drawer destinations, including `/` → `/showcase` without layout remounting,
  with focus preserved after drawer cleanup and the next Tab.
- Drawer Escape and same-page links restore trigger focus, including after a
  prior header navigation.
- Browser-style Back/Forward navigation focuses the destination layout.

The main route/drawer tests run inside React StrictMode. Existing modal, drawer
and bottom-sheet focus-trap, Escape and close-button tests also pass.
Live desktop Public → Auth navigation followed by Tab focused the destination's
“Explore the component library” link. At 360px, keyboard navigation through the
mobile drawer to Tenant likewise continued at that destination link, confirming
that drawer dismissal did not reclaim focus.

### R2 — PASS: cancellation during JSON reading is preserved

`src/lib/api-client.ts` now rethrows the body-reading error when its request signal
is aborted, before converting ordinary JSON failures into `INVALID_RESPONSE`.
The outer error handler still maps the client's deadline to `TIMEOUT`.

Six new regression cases cover cancellation and timeout separately during each
of GET response-body reading, CSRF response-body reading and write response-body
reading. They verify the caller's original AbortError object is preserved,
timeouts remain typed `TIMEOUT` errors, no extra write/retry occurs, and slow-request
activity clears without a delayed banner reappearing.

Existing malformed-response, network-error, typed standard error, field filtering,
status, cookie, CSRF, path validation, 204, cancellation-before-headers and
concurrent wake-up tests remain green. Genuine malformed JSON still produces
`INVALID_RESPONSE`; cancellation does not.

### R3 — PASS: Select and Textarea success states are showcased

Added “Successful selection” and “Successful note” with values and success text
using the existing Field implementation, green text token and decorative check
icon. Labels, `aria-describedby`, editability and the textarea character counter
are retained. Neither successful control is marked invalid.

One new showcase regression verifies both accessible descriptions, initial and
edited values, non-error semantics, the counter, success styling and hidden
check icons. Existing default/error/disabled controls remain; hover/focus are
available through normal interaction. All six button variants still expose their
six-state matrix and size examples. No new feature component was introduced.

## Exact final checks

| Check                                                      | Result                                              |
| ---------------------------------------------------------- | --------------------------------------------------- |
| `npm run lint`                                             | PASS; ESLint with zero warnings allowed             |
| `npm run format:check`                                     | PASS                                                |
| `npm run typecheck`                                        | PASS                                                |
| `npm test`                                                 | PASS; 74 tests in 5 files, no failures or skips     |
| `npm run build`                                            | PASS; TypeScript and Vite production build          |
| `npm audit --omit=dev --json`                              | PASS; 0 vulnerabilities at every severity           |
| `npm ls --all --omit=dev`                                  | PASS; exit 0                                        |
| Backend `python -m pytest -ra`, approved disposable target | PASS; 36 tests, no skips, 1 existing warning        |
| Live API health, proxy and SPA deep links                  | PASS                                                |
| Render rule tests and staging/production CLI output        | PASS; local configuration only                      |
| Mobile/tablet/desktop browser measurements                 | PASS at 360, 768 and 1440 CSS pixels                |
| Keyboard/overlay accessibility regressions                 | PASS                                                |
| PostgreSQL/Redis health                                    | PASS; both healthy                                  |
| Changed-file secret/artifact/whitespace checks             | PASS; one synthetic rejection-test fixture reviewed |

Frontend breakdown: **16 App/routes/showcase + 19 base UI + 32 API client +
1 PageHead + 6 Render rules = 74**. This is **15 new tests** over the earlier 59:
eight focus cases, six body-cancellation/deadline cases and one success-state case.
Backend breakdown: **32 unit + 4 database integration = 36**.
The earlier two failing temporary probes are now covered by permanent regressions.

The final full frontend command chain passed after correcting a test-only
unsupported query option and formatting during development. No failed check is
being counted as a pass. Final Vite output: HTML 0.78kB (0.41kB gzip), CSS 24.84kB
(5.95kB gzip), JS 413.50kB (129.99kB gzip). Generated output remains ignored.

## UI, routes, metadata and responsive verification

PASS: React 19, Vite, strict TypeScript, nested Public/Auth/Tenant/Owner/Admin
layouts and Outlet routes remain in the specified folder structure. Routes are
`/`, `/showcase`, `/auth`, `/tenant`, `/owner`, `/admin`, and a wildcard not-found
page with a working return link. These are foundation previews without protected
account data or implemented business actions.

PASS: all required UI components are present: Button, Input, Textarea, Select,
Checkbox, Switch, Badge, Card, Modal, Drawer, BottomSheet, Toast, Skeleton,
EmptyState, ErrorState and Tabs. CSS variables and Tailwind mapping centralize
colours, spacing, radii, shadows and fonts. Playfair Display and Inter use Google
Fonts `display=swap` and fallback stacks. No component contains a hex colour.

PASS: loading, empty, error, retry, toast and wake-up examples remain operational.
Controls have visible labels, helper/error associations, disabled/loading
semantics and named icon buttons. Overlay focus traps and return focus, tab arrow
keys, disabled-tab skipping, form-control keyboard activation and toast dismissal
remain covered by the passing suite. Focus rings use a two-pixel violet outline
with a two-pixel offset. Success feedback uses both an icon and text.

PASS: PageHead tests verify title, description, Open Graph metadata, escaped
JSON-LD and cleanup on navigation. Preview pages remain noindex. Metadata is
client-side; server-rendered social sharing is later-ticket work.

Fresh Brave/Chromium DevTools measurements of the updated showcase at 800px height:

| Width  | Document scroll width | Rendered targets below 44 × 44px | Success feedback present |
| ------ | --------------------- | -------------------------------- | ------------------------ |
| 360px  | 360px                 | 0                                | Input, Select, Textarea  |
| 768px  | 768px                 | 0                                | Input, Select, Textarea  |
| 1440px | 1440px                | 0                                | Input, Select, Textarea  |

Checkbox labels were measured as their clickable target. Measurements cover the
rendered showcase with its default feedback tab, not every overlay permutation.
The mobile textarea success label/check/counter and adjacent error/disabled states
were visually inspected. Live mobile drawer keyboard navigation was also checked.
The established responsive grids, gutters, mobile navigation and desktop sidebar
remain unchanged. Reduced-motion CSS still disables animation and transitions.

The earlier token contrast calculations remain applicable: muted-on-ivory 4.52:1,
muted-on-white 4.76:1, white-on-primary 5.15:1 and status pairs 5.30:1–7.15:1.
No colour/style token changed in this follow-up.

## API security, proxy and Render configuration

PASS: the client uses same-origin `/api/v1`, enforced `credentials: 'include'`
and `redirect: 'error'`. External/traversal/backslash/fragment/double-prefixed
resource paths are rejected. POST/PATCH/PUT/DELETE fetch a fresh CSRF token,
reject a missing token and send `X-CSRF-Token`. Tokens are not cached across
sessions or stored in browser storage. Neither writes nor TanStack Query defaults
retry automatically.

PASS: ApiError preserves standard status, code, message, string field errors,
request ID and Retry-After. JSON serialization, malformed/network error messages,
204 responses and cancellation remain covered. The default timeout is 65 seconds;
the wake-up banner appears after four seconds and clears when slow requests finish.
The new body-reading tests also verify cleanup while cancelling CSRF and writes.

Fresh read-only smoke checks:

- Restricted-login API `/healthz` and `/readyz`: HTTP 200.
- Vite `/api/v1/t04-review-missing`: backend JSON HTTP 404 `NOT_FOUND` with request
  ID, rather than SPA HTML. Vite preserves the prefix and browser Origin.
- Direct `/auth`, `/tenant`, `/owner` and `/admin` URLs: HTTP 200 SPA entry HTML.

PASS for local Render configuration: the six tests and the CLI confirm ordered
`/api/*` → `https://<backend-origin>/api/*`, then `/*` → `/index.html`, both rewrites.
Synthetic staging and production origins were exercised; HTTPS, credential,
path/query/hash validation remains intact. This matches the wildcard/destination
syntax reviewed against [Render's documentation](https://render.com/docs/redirects-rewrites).
No hosted configuration was changed and no deployment was attempted.

## Backend and Docker safety

Used only the existing approved disposable target
`dormiostay_t03_test_t04_ede6bef69966` for tests. The guarded wrapper at
`/private/tmp/dormiostay-t04-backend-review.py` loads credentials privately,
validates both test URLs and the connected database name, and refuses a nonempty
disposable schema. No database was created, deleted or reset.

The disposable schema contained only `alembic_version` before and after the suite.
Its existing fixture ran migration upgrade, Alembic consistency checking and
normal downgrade teardown. These operations affected disposable test tables only.
The development public-table inventory was read-only checked and stayed empty.
Tests exercised actual restricted-login audit permissions and context cleanup;
the fixture removed its temporary test role normally.

All 36 backend tests passed. The one warning is the pre-existing Starlette/httpx
TestClient deprecation. No backend files were modified.

Read-only `docker compose --project-name dormiostay ps` showed
`dormiostay-postgres-1` and `dormiostay-redis-1` healthy on loopback ports 5433 and 6380. No container, network or volume start/stop/recreate/reset command was run.
Local Vite/API verification processes are separate from Docker resources.

## Limitations and later-ticket boundaries

- Authentication, real session cookies, server CSRF/origin enforcement and the
  `/auth/csrf` endpoint are T-06/T-07 work. Client protocol behavior uses mocks;
  the proxy smoke check uses the existing backend's standard not-found endpoint.
- Render rules are a validated local handoff. Actual environment origins,
  publicly reachable destinations, hosted cookie forwarding, hosted deep links
  and cold starts await T-05. No render.yaml, service, cron or deployment added.
- No physical-device, Safari/Firefox, screen-reader, full landscape matrix or
  200% text enlargement run was performed. Reduced-motion behavior was reviewed
  in CSS, not separately emulated. This is not blanket WCAG certification.
- Successful API payload types are compile-time generic assertions, not runtime
  schemas. Page metadata is client-side. Hosted CI was not run.
- No search/listing/visit/map/auth/admin business features or other later-ticket
  functionality were implemented. The three requested findings are resolved;
  these scope limitations are not represented as completed integrations.

## Hygiene and exact files safe to commit

All 54 changed/untracked candidates were checked for exact local credential
values (including decoded DB passwords), private keys/provider tokens,
credential-bearing URLs, generated artifacts and whitespace. No local secret or
generated artifact was found. The sole URL-pattern hit is the intentional
synthetic rejection fixture in `render-routes.test.ts`. No secret values were
printed. Component hex scan and `git diff --check` pass.

The dependency lockfile is unchanged by this follow-up. Its prior review found
68 added package entries, five metadata changes, no existing version changes,
registry.npmjs.org resolutions with SHA-512 integrity and no new install scripts.
The production dependency audit was rerun and returned zero vulnerabilities.

**The following 54 files are safe T-04 commit candidates after user approval.**
No files were staged or committed. The manifest is checked against the complete
modified/untracked file set, including this report.

```text
README.md
apps/web/config/render-routes.test.ts
apps/web/config/render-routes.ts
apps/web/index.html
apps/web/package.json
apps/web/public/favicon.svg
apps/web/scripts/render-routes.ts
apps/web/src/app/App.test.tsx
apps/web/src/app/App.tsx
apps/web/src/app/providers.tsx
apps/web/src/app/router.tsx
apps/web/src/components/seo/PageHead.test.tsx
apps/web/src/components/seo/PageHead.tsx
apps/web/src/components/ui/badge.tsx
apps/web/src/components/ui/bottom-sheet.tsx
apps/web/src/components/ui/button.tsx
apps/web/src/components/ui/card.tsx
apps/web/src/components/ui/checkbox.tsx
apps/web/src/components/ui/drawer.tsx
apps/web/src/components/ui/empty-state.tsx
apps/web/src/components/ui/error-state.tsx
apps/web/src/components/ui/field.tsx
apps/web/src/components/ui/input.tsx
apps/web/src/components/ui/modal.tsx
apps/web/src/components/ui/select.tsx
apps/web/src/components/ui/skeleton.tsx
apps/web/src/components/ui/switch.tsx
apps/web/src/components/ui/tabs.tsx
apps/web/src/components/ui/textarea.tsx
apps/web/src/components/ui/toast.tsx
apps/web/src/components/ui/ui.test.tsx
apps/web/src/components/ui/waking-banner.tsx
apps/web/src/hooks/use-field.ts
apps/web/src/layouts/AdminLayout.tsx
apps/web/src/layouts/AuthLayout.tsx
apps/web/src/layouts/LayoutFrame.tsx
apps/web/src/layouts/OwnerLayout.tsx
apps/web/src/layouts/PublicLayout.tsx
apps/web/src/layouts/TenantLayout.tsx
apps/web/src/lib/api-client.test.tsx
apps/web/src/lib/api-client.ts
apps/web/src/lib/toast-context.ts
apps/web/src/lib/utils.ts
apps/web/src/main.tsx
apps/web/src/pages/LayoutPreview.tsx
apps/web/src/pages/NotFound.tsx
apps/web/src/pages/public/Showcase.tsx
apps/web/src/styles/globals.css
apps/web/src/styles/tokens.css
apps/web/tailwind.config.ts
apps/web/tsconfig.json
apps/web/vite.config.ts
docs/T-04 Verification Report.md
package-lock.json
```

Exclude `.env`, `apps/api/.env`, virtual environments, node_modules, dist, caches,
logs, browser screenshots and temporary scripts/probes. Secrets and build output
remain ignored. T-04 verification is complete; stop here for approval, without
starting T-05.
