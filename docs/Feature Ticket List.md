# DormioStay — Feature Ticket List

_Version 1.1 | November 2026 | Derived from the DormioStay PRD (v1 MVP). Each ticket is written so it can be pasted directly into an AI coding tool._

---

## How to use this list

1. **Paste the Global Context block first** (below) at the start of every AI coding session, then paste one ticket.
2. Build tickets in dependency order. Do not start a ticket until everything in its 'Depends on' line is done and tested.
3. Priority labels: **Must-have** = required for launch. **Should-have** = include if time allows or ship right after launch. **Nice-to-have** = after v1.
4. After each implementation ticket, run its acceptance checks. Documentation-only work runs only documentation checks; never rerun completed T-01–T-04 implementation or full suites just because the platform plan changes.
5. **Status baseline:** T-01, T-02, T-03 and T-04 are completed, committed, pushed and GitHub Actions CI-verified. Their original prompts and acceptance criteria below are historical records, not instructions to rebuild them. All new/mobile work is planned and unimplemented.
6. **Platform labels:** Web means the existing React/Vite app, including responsive phone layouts. Native Mobile means Android/iOS with Expo/React Native. Backend-only tickets serve both clients once; a mobile companion owns native UI/integration only unless explicitly assigned a new delivery adapter. Admin interfaces are Web-only for v1.
7. Existing T-01–T-54 IDs retain their meanings. `M` suffixes identify substantial native companions; T-14P explicitly covers the existing product's profile/account requirement. No backend API is duplicated for a companion. Shared API/domain rules in a mixed ticket are established before its client integration work.

### Completed foundation compatibility

| Ticket | Preserved history and compatibility                                                        | Forward extension                                                                                              |
| ------ | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| T-01   | Existing monorepo, tooling and CI completed                                                | Add `apps/mobile` via T-04M, not by rewriting T-01                                                             |
| T-02   | Existing FastAPI foundation completed; valid for multiple clients                          | T-06/T-07 add bearer transport and its validation                                                              |
| T-03   | Existing database and session hash/expiry/revocation foundation completed                  | Any useful device/client metadata uses a **new future migration**; never edit the applied first migration      |
| T-04   | Existing React Web foundation, API client, design system and accessibility fixes completed | T-04M references visual decisions but builds separate native components; preserve the T-04 Verification Report |

No foundational incompatibility was found. Native auth and device integrations are planned additions, not failures of the completed Web work. Do not change completed Git history.

### Global Context block (paste before every ticket)

> You are building DormioStay, a PG / hostel / student-accommodation marketplace for ICFAI University Sikkim and Gangtok. Target: Web + Native Mobile + Shared API. Existing `apps/api`: FastAPI, Python 3.12, SQLAlchemy 2 async, Alembic, Pydantic v2, pwdlib Argon2id. Existing `apps/web`: React 19 + Vite + TypeScript + Tailwind CSS + React Router, Radix/shadcn conventions, TanStack Query, React Hook Form + Zod, MapLibre GL JS. Planned `apps/mobile` (T-04M): Expo + React Native + TypeScript strict + Expo Router + NativeWind + TanStack Query + React Hook Form + Zod + Expo SecureStore + Lucide React Native + Expo Image + Sentry React Native; Expo Notifications at its feature ticket and EAS Build when required. Optional `packages/shared` contains only platform-neutral types, schemas, enums, constants, status definitions, formatters, API contracts, query keys and design tokens, extracted gradually only for real reuse. No DOM/Radix components shared into React Native.
>
> ONE FastAPI backend owns auth, authorization, business logic, RLS, listings, search, visits, notifications, Owner workflows, moderation, AI and external services. PostgreSQL is the single source of truth; no mobile database/backend. Neon PostgreSQL; Upstash Redis for rate limits/cache only; Brevo via email_outbox; Cloudinary public photos; private Cloudflare R2 documents; Geoapify server geocoding; Gemini behind a provider wrapper; Render Web/API/worker hosting. Native mapping is selected at T-26M, not a reuse of MapLibre GL JS. Prefer free/open-source tools and free development tiers.
>
> Rules: (1) Auth stays custom opaque server-side sessions, at least 32 random bytes, only SHA-256 token hashes in user_sessions. Web: HttpOnly Secure-in-production SameSite=Lax cookie, same-origin API, Origin validation and CSRF on writes. Native Mobile: raw token persisted only in Expo SecureStore, sent as Authorization: Bearer <session_token> over HTTPS. Same idle/absolute expiry, logout, reset/ban/suspension revocation; no JWT. T-06/T-07 implement both transports and reject mixed credentials; only validated cookie-free bearer requests bypass browser CSRF. Follow Security section 2.2 for explicit pre-authentication rules. (2) Backend permissions and transaction-local RLS context apply identically; route guards only aid UX. Admin is Web-only; no native Admin issuance/screens. (3) Services hold business logic, repositories hold SQL, routes stay thin. (4) UUIDs, UTC storage/IST display, INTEGER rupees (not NUMERIC), status-based soft deletion, append-only audit logs. (5) Both clients use /api/v1; Native Mobile uses its public HTTPS API base. No backend-only third-party calls from clients; direct uploads use FastAPI-signed URLs without session credentials. (6) AI never writes SQL, changes data or invents facts; failures fall back to ordinary features. (7) Preserve rate-limit fallback, email delivery honesty and safe network/error states; no offline write synchronization. (8) No secrets in code, Web bundles or mobile bundles; EXPO_PUBLIC values and app configuration are public. Never put native auth tokens in AsyncStorage, SQLite, files, logs, analytics or crash breadcrumbs. (9) Follow existing DormioStay tokens: Royal Violet #6D4AFF, Deep Indigo #312E81, Warm Ivory #FAF9F6, Playfair Display headings, Inter body, plus accessible T-04 text variants. (10) Test new endpoints/components and platform journeys at their implementation ticket; do not rerun completed tickets. (11) Preserve role-column and standard table names: email_verification_tokens, password_reset_tokens, visit_events and admin_mfa. (12) No payments, subscriptions, rent collection, mobile Admin, custom Swift/Kotlin apps, Flutter/Ionic/Capacitor, Firebase Auth/Clerk/Auth0, second backend or unnecessary microservices. T-01–T-04 are completed; never rewrite applied migrations or their acceptance history.

---

# EPIC A — Foundation

### T-01 · Monorepo, tooling and CI

**Status:** Completed, committed, pushed and CI-verified. **Platform:** Repository / existing Web + Backend. Original acceptance history preserved; do not reimplement.

**Priority:** Must-have | **Depends on:** none

**Prompt:** Create the monorepo `dormiostay/` with `apps/api`, `apps/web`, `docs/`, root `README.md`, `.gitignore`, and `.env.example` (variable names only, grouped by backend and frontend, taken from the Technical Architecture document). Set up Python tooling (pyproject, ruff, mypy, pytest) and web tooling (ESLint, Prettier, TypeScript strict, Vitest). Add `.github/workflows/ci.yml` that lints and tests both apps on every push and pull request. Add an optional `docker-compose.yml` for local Postgres and Redis.

**Acceptance criteria:**

- Fresh clone plus README steps runs both apps locally.
- CI passes on an empty test for each app.
- No real secret exists in the repo; `.env.example` lists every variable with a comment.

### T-02 · FastAPI skeleton with config, errors and health checks

**Status:** Completed, committed, pushed and CI-verified. **Platform:** Backend. Original acceptance history preserved; do not reimplement.

**Priority:** Must-have | **Depends on:** T-01

**Prompt:** Build the FastAPI app in `apps/api/app/main.py` with `core/config.py` (reads environment variables using Pydantic settings, fails fast if a required value is missing), `core/errors.py` (typed errors and a global handler returning `{"error": {"code", "message", "fields", "request_id"}}`), `core/logging.py` (structured JSON logs), a request-id middleware, Sentry initialisation, `GET /healthz` (no database call) and `GET /readyz` (checks the database). Mount routers under `/api/v1`.

**Acceptance criteria:**

- Unknown routes return the standard error body with a request id; stack traces never reach the client.
- Missing required environment variable stops startup with a clear message.
- Tests cover health, readiness, validation error shape and the unexpected-error handler.

### T-03 · Database foundation, migrations and first models

**Status:** Completed, committed, pushed and CI-verified. **Platform:** Backend / Database. Original acceptance history preserved; do not reimplement.

**Priority:** Must-have | **Depends on:** T-02

**Prompt:** Set up SQLAlchemy 2 async (`db/session.py`, `db/base.py`) and Alembic (migrations use `DATABASE_URL_DIRECT`, app uses the pooled `DATABASE_URL`). Create models and the first migration for: `users`, `user_profiles`, `user_sessions`, `email_verification_tokens`, `password_reset_tokens`, `locations`, `institutions`, `amenities`, `property_categories`, `audit_logs`, `platform_settings`. All tables have UUID `id`, `created_at`, `updated_at`. Add the CHECK, UNIQUE and index rules from the architecture document. Add a per-request database context helper that sets `app.user_id` and `app.role` with `SET LOCAL` inside each transaction (used later by RLS). `audit_logs` must be insert-only for the application role.

**Acceptance criteria:**

- `alembic upgrade head` and `downgrade` both work on an empty database.
- Email uniqueness is case-insensitive.
- Test proves the application role cannot UPDATE or DELETE `audit_logs`.
- Context helper resets after each request (no leakage between requests).

### T-04 · React + Vite skeleton, design tokens and base UI kit

**Status:** Completed, committed, pushed and CI-verified. **Platform:** Web. Original acceptance history preserved; do not reimplement.

**Priority:** Must-have | **Depends on:** T-01

**Prompt:** Create the React 19 + Vite + TypeScript app (`index.html`, `vite.config.ts`, `src/main.tsx`, `src/app/router.tsx`) following the web folder layout in the Technical Architecture document. Use React Router v7 with nested layout routes (`PublicLayout`, `AuthLayout`, `TenantLayout`, `OwnerLayout`, `AdminLayout`) in `src/layouts/`. Put the colour tokens, radius, shadow and spacing from the Frontend Specification into CSS variables and `tailwind.config.ts`. Load Playfair Display and Inter in `index.html` via Google Fonts, or self-hosted `@font-face` in CSS, with `font-display: swap`. Build base components in `components/ui/`: Button (all variants and states), Input, Textarea, Select, Checkbox, Switch, Badge, Card, Modal, Drawer, BottomSheet, Toast, Skeleton, EmptyState, ErrorState, Tabs. Configure Vite dev proxy (vite.config.ts) and Render Static Site rewrite rules so `/api` forwards to `http://localhost:8000` in development and to the FastAPI backend in staging and production. Add `lib/api-client.ts` (fetch wrapper with credentials, CSRF header, typed error parsing) and a 'waking up' banner shown when a request takes over 4 seconds. Also add a `PageHead` component in `components/seo/` that sets the document title, meta description, Open Graph tags and JSON-LD for each page.

**Acceptance criteria:**

- A component showcase page renders every component and state.
- Keyboard focus rings and 44px touch targets are present.
- API client turns the standard error body into a typed error.
- No hard-coded hex values in components.

### T-04M · Expo React Native mobile foundation

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-01, completed T-04 design system decisions

**Prompt:** Create `apps/mobile` through this ticket without changing completed T-01–T-04 history. Pin mutually compatible Expo, React Native and strict TypeScript versions; use Expo Router, NativeWind, TanStack Query, React Hook Form, Zod, Expo SecureStore abstraction, Lucide React Native, Expo Image and Sentry React Native with redaction. Reference the existing DormioStay tokens, including accessible status text variants. Keep the completed Web workspace and API client intact. Optional `packages/shared` is introduced gradually only for real platform-neutral reuse.

Build public/auth/Tenant/Owner native stack/tab shells (no Admin), a development-only component showcase, Button/Input/Textarea/Select/Checkbox/Switch/Card/Badge/Modal/BottomSheet/Toast/Skeleton/EmptyState/ErrorState/Tabs, loading/error/empty/offline states, network/lifecycle handling, safe areas, keyboard avoidance, back/gesture behaviour and screen-reader support. Tenant tabs: Explore, Saved, Visits, Notifications, Profile. Owner tabs: Overview, Properties, Visits, Availability, Account. Configure Android/iOS identities, icons/splash and development/preview/production build profiles; use EAS Build when needed, not a second backend.

Create a native API foundation for the shared `/api/v1` contracts: public HTTPS base, typed errors, request deadline/cancellation, wake-up state, explicit no-cookie transport and a mocked SecureStore credential adapter. Test header scoping without actual login/session APIs. Do not implement auth endpoints, real credential issuance/login/logout or protected feature data (T-06/T-07/T-14M); do not register push or configure a map provider yet. Add scoped mobile lint/typecheck/unit/component/navigation checks to CI while preserving existing jobs. Document any SDK/Node tooling compatibility issue instead of silently upgrading the completed Web stack.

**Acceptance criteria:**

- App starts and the Android development build runs; Expo Go alone is not sufficient evidence.
- iOS simulator/build configuration is valid and exercised where available. Record unavailable macOS/Xcode/signing/device execution explicitly; it remains a later release validation requirement.
- Public/auth/Tenant/Owner shells, back navigation and component showcase work with mock data; no native Admin routes.
- Brand tokens match T-04; all native controls have at least 44pt iOS / 48dp Android hit targets (44-or-greater baseline), labels, pressed/disabled/loading states and keyboard/screen-reader basics.
- Safe areas and keyboard avoidance keep content/actions visible; loading, empty, error and offline/retry states are demonstrated.
- API/storage contract tests cover typed errors, cancellation, correct-origin bearer headers and SecureStore failures without real auth or insecure fallback.
- Only public configuration enters the bundle; no backend/session/signing secrets in app config, source, logs or Sentry breadcrumbs.
- Mobile tests, lint and typecheck pass; existing Web/API checks are preserved. No production deployment is part of this ticket.

### T-05 · Render deployment and environments

**Platforms:** Web + shared Backend deployment; Native Mobile environment/build handoff

**Priority:** Must-have | **Depends on:** T-02, T-03, T-04, T-04M

**Prompt:** Add `render.yaml` defining a Static Site for `apps/web` (build with npm run build, publish dist/; rewrite rules: /api/\* to the API service URL, and /\* to /index.html for React Router), a web service for `apps/api` (health check `/healthz`), and a cron job running `scripts/worker.py` every minute. Document separate development, staging and production variables, using a Neon branch for staging. Add a README section on free-tier sleeping and the checklist to move to an always-on plan.

**Mobile/environment extension:** preserve the completed Web proxy handoff. Define native development, preview/staging and production profiles, public HTTPS API base and optional public Sentry DSN, package/bundle identifiers and verified app-link domain association handoff. Anything bundled is public; signing/backend secrets stay in restricted host/CI/EAS settings. Native apps are built/distributed separately, using EAS Build when required; they are not another Render website/backend. Prefer free development tiers and document store/build costs before release. If `scripts/worker.py` does not exist until T-09, keep its cron disabled until that dependency is delivered; never schedule a missing worker.

**Acceptance criteria:**

- Each native profile resolves to the correct isolated API/data environment over HTTPS; no secrets in exported config/bundles, no production tokens reused in preview.
- App-link domain ownership and native signing/build prerequisites are documented; missing platform validation is marked pending, not passed.

- Staging deploys from the main branch and the web app reaches the API through the proxy.
- Secrets are set only in the host's environment settings.
- Visiting the site while the API is asleep shows the waking-up banner, then loads.

---

# EPIC B — Authentication and Roles

### T-06 · Signup, login, logout and current user

**Platforms:** Backend-only — shared Web/Native Mobile authentication

**Priority:** Must-have | **Depends on:** T-03

**Prompt:** Implement `POST /auth/signup` (role `tenant` or `owner` only), `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`. Hash passwords with pwdlib Argon2id (minimum 10 characters, block common passwords, never log passwords). On login create a session from 32+ cryptographically random bytes and store only its SHA-256 hash in `user_sessions`, with idle and absolute expiry (Tenant/Owner 30 min idle / 14 days; Admin 15 min / 8 hours). Web receives only an HttpOnly cookie (Secure in production, SameSite=Lax, configurable name). An explicit credential-verified native login mode returns the opaque token once in a non-cacheable HTTPS response for SecureStore and sets no session cookie; it accepts only Tenant/Owner. Web responses never expose that token to JavaScript. Rotate the session on login. Reject suspended or banned accounts. Use identical generic messages for wrong email or wrong password.

**Shared transport contract:** implement one create/validate/revoke service, accepting Web cookies or native `Authorization: Bearer <session_token>` and rejecting mixed credentials or invalid bearer fallback. Current user/status checks and transaction-local RLS are identical. Logout revokes the current server session; reset, ban and suspension revoke all sessions immediately. Return safe client errors without credentials in logs. Security section 2.2 governs native issuance/storage failure/offline logout; T-07 provides CSRF and pre-authentication protection. Do not expose auth publicly until T-07/T-08 are complete. Device metadata, if necessary, uses a new migration; user-facing device lists/logout-all remain later work.

**Acceptance criteria:**

- The same expiry, reset, suspension, revocation and wrong-role tests run for both transports; mixed credentials and invalid-bearer fallback fail.
- Web login never returns a raw token in the body; native issuance never sets a session cookie or creates an Admin session.

- Only token hashes persist server-side; Web raw tokens remain cookie-only, native raw tokens are persisted only in SecureStore; no JWT or browser localStorage.
- Logout and ban make the old cookie or bearer credential fail immediately.
- Expired idle and absolute sessions are rejected.
- Tests cover signup duplicates (case-insensitive), wrong password, suspended user, session expiry.

### T-07 · CSRF and origin protection

**Platforms:** Shared Backend security + Web CSRF integration; Native Mobile transport validation

**Priority:** Must-have | **Depends on:** T-06

**Prompt:** Add `core/csrf.py` and integrate the existing Web API client. Cookie-authenticated POST/PATCH/PUT/DELETE require a session-bound `X-CSRF-Token` from `GET /auth/csrf` and exact `FRONTEND_ORIGIN`. Provide the short-lived anonymous CSRF bootstrap for Web signup/login/reset and other anonymous writes before a login session exists. Only a successfully validated, cookie-free native bearer request is exempt from browser CSRF; a client label or missing Origin is never enough. Native pre-authentication uses explicit native mode, JSON plus a non-safelisted client header, no session cookie and rejection of a browser Origin, with ordinary password/action-token validation and rate limits. Reject mixed credentials; do not fall back from invalid bearer to cookies. Preserve exact Web CORS allowlists (no wildcard/null workaround). Authenticated provider webhooks use their own signature/secret rules. Follow Security section 2.2.

**Acceptance criteria:**

- Valid native bearer writes succeed without a CSRF token; invalid/expired bearer, mixed credentials and spoofed client markers do not bypass protection.
- Web pre-login bootstrap and native cookie-free pre-auth flows are covered, including browser Origin, content-type and cross-origin failure cases.

- Web state-changing requests without valid CSRF or with a foreign Origin return 403.
- GET requests are unaffected.
- Tests cover missing token, wrong token, wrong Origin.

### T-08 · Rate limiting with Redis and safe fallback

**Platforms:** Backend-only — shared rate limiting

**Priority:** Must-have | **Depends on:** T-06

**Prompt:** Build `core/rate_limit.py` using Upstash Redis (INCR plus EXPIRE). Apply limits: login 5 per 15 min per email and 20 per IP; signup 5 per hour per IP; password reset 3 per hour per email; resend verification 3 per hour; visit requests 10 per day per user; AI search 20 per day per signed-in user and 5 per guest. Return 429 with `Retry-After`. If Redis is unreachable, apply stricter in-process limits to sensitive endpoints and log a warning; never silently disable limits.

**Acceptance criteria:**

- Limits trigger at the exact thresholds in tests.
- With Redis stopped, login and reset remain protected.
- Responses never reveal whether an email exists.

### T-09 · Email outbox and Brevo worker

**Platforms:** Backend-only — shared email outbox/worker

**Priority:** Must-have | **Depends on:** T-03

**Prompt:** Create `email_outbox` (status: queued, sending, accepted\_by\_provider, delivered, failed; attempts, last\_error, send\_after, provider\_message\_id), `services/email_service.py` (Brevo `POST /v3/smtp/email`, configurable verified sender) and `scripts/worker.py` that sends queued rows with retries (increasing delay, 5 attempts, then failed). Add `POST /webhooks/brevo` (secret-protected) updating status to delivered or failed. If no verified sender is configured, run in development mode and show an admin-visible status; never mark an email delivered unless the provider confirms.

**Acceptance criteria:**

- A failing provider does not fail the user's action.
- Status transitions are recorded accurately and visible to admins.
- Tests use a mocked Brevo client; no real quota is spent.

### T-10 · Email verification

**Platforms:** Backend-only — both-client verification links

**Priority:** Must-have | **Depends on:** T-06, T-09

**Prompt:** On signup create a random token, store only its hash with a 24-hour expiry, queue the verification email. Implement `POST /auth/verify-email` and `POST /auth/resend-verification` (rate-limited). Unverified users can browse but cannot request visits or submit owner applications.

**Both-client link contract:** issue single-use action links with verified HTTPS app-link routes and a Web fallback. Never put a session token in a link or log action tokens. GET/navigation alone does not consume the action; test wrong, reused and expired tokens on both client flows. T-14/T-14M own the UIs.

**Acceptance criteria:**

- Token works once; expired and reused tokens show clear messages.
- Resend is rate-limited and does not leak whether the account exists.
- Restricted actions return 403 with a code the UI can explain.

### T-11 · Password reset

**Platforms:** Backend-only — both-client reset and global revocation

**Priority:** Must-have | **Depends on:** T-06, T-08, T-09

**Prompt:** Implement `POST /auth/forgot-password` (always responds 'If that email exists, we sent a link') and `POST /auth/reset-password` (random single-use hashed token, 30-minute expiry). A successful reset revokes every session of that user.

**Both-client link contract:** issue single-use action links with verified HTTPS app-link routes and a Web fallback. Never put a session token in a link or log action tokens. GET/navigation alone does not consume the action; test wrong, reused and expired tokens on both client flows. T-14/T-14M own the UIs.

**Acceptance criteria:**

- Identical response and similar timing for known and unknown emails.
- Old sessions fail after reset.
- Token reuse and expiry are rejected.

### T-12 · Role permissions and row-level security

**Platforms:** Backend-only — identical permissions/RLS for all clients

**Priority:** Must-have | **Depends on:** T-03, T-06

**Prompt:** In `core/permissions.py` build dependencies `require_user`, `require_verified`, `require_role(...)`, `require_approved_owner`, `require_admin`. Add Alembic migrations enabling and forcing RLS with policies on every table as specified in the Security and Access document (section 4.3), reading `app.user_id` and `app.role`. The app connects as a non-superuser role that cannot bypass RLS. Missing context must return no rows.

**Acceptance criteria:**

- Automated tests: user A cannot read, update or delete user B's rows in any protected table; no-context queries return nothing.
- Guests, tenants, pending owners, approved owners and admins match the permission table exactly.
- A security test suite folder `tests/security/` exists and runs in CI.

### T-13 · Admin bootstrap and TOTP MFA

**Platforms:** Backend Admin security/bootstrap — Web-only Admin access

**Priority:** Must-have | **Depends on:** T-06

**Prompt:** Add `scripts/create_admin.py` (one-time, reads `INITIAL_ADMIN_EMAIL`, prompts for password, no default password). Implement admin MFA: `admin_mfa` with encrypted TOTP secret (key `TOTP_ENCRYPTION_KEY`), QR setup endpoint, verification endpoint, 10 single-use hashed recovery codes, stricter login limits, shorter sessions. Admin routes refuse any session that has not passed MFA. No admin signup route exists.

**Acceptance criteria:**

- An admin without a completed MFA step cannot call any `/admin` endpoint.
- Recovery codes work once.
- Tenant or owner login can never produce an admin session.

### T-14 · Auth screens

**Platforms:** Web — native companion T-14M

**Priority:** Must-have | **Depends on:** T-04, T-06, T-07, T-08, T-10, T-11, T-12, T-13

**Prompt:** Build `auth` routes using React Router (/login, /signup, /verify-email, /forgot-password, /reset-password, /admin/mfa): signup (tenant or owner choice), login, verify-email result page, forgot password, reset password, and the admin MFA step. Use React Hook Form and Zod, inline errors, password show/hide, loading states, and a redirect back to the page the person came from. Show a clear 'session expired' message.

**Acceptance criteria:**

- All states (loading, error, success, rate-limited) are designed.
- Fully usable on a 360px-wide phone and by keyboard only.
- Navigation changes by role after login.

### T-14M · Native Tenant and Owner auth experiences

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-06, T-07, T-08, T-10, T-11, T-12

**Prompt:** Build native signup/login, email verification, forgot/reset password and session-expired flows with React Hook Form/Zod. Wire the T-06 explicit native issuance contract and SecureStore adapter; send only cookie-free HTTPS bearer requests to FastAPI. Revalidate on restore/resume, clear private caches on account change/logout and honour the Security document's offline logout warning. Handle verified app/action links and safe return destinations. Never add native Admin login or MFA. No auth endpoint duplication.

**Acceptance criteria:**

- Tenant/Owner login and verified actions use existing APIs; raw tokens never enter AsyncStorage, SQLite, files, route params, logs, analytics or breadcrumbs.
- Both idle and absolute expiry, logout, reset, ban and suspension invalidate access; SecureStore read/write failure fails closed and offline logout never claims confirmed server revocation.
- Valid/expired/reused verification/reset links and safe return paths work on Android/iOS with a Web fallback; no session token in a link.
- Loading, field errors, rate limiting, password-manager/paste, keyboard avoidance and screen-reader flow are tested.

### T-14P · Shared profile/account API and client screens

**Status:** Planned; not implemented.

**Platforms:** Shared Backend + Web + Native Mobile (Tenant/Owner only)

**Priority:** Must-have | **Depends on:** T-12, T-14, T-14M

**Prompt:** Implement authenticated own-account/profile APIs using the existing `users`, `user_profiles`, and existing Owner/host-application data. Reuse the authentication and session validation implemented by T-06/T-07; this ticket does not reimplement authentication or session transport.

Add these endpoints under the shared `/api/v1` prefix:

- `GET /users/me/profile`
- `PATCH /users/me/profile`

The GET response returns only the authenticated user's safe account/profile information needed by the clients, including applicable account status and Owner application/approval status. Derive identity from the validated session, never from a client-supplied user id. Define an explicit response schema that excludes credentials, session data, password hashes, MFA secrets and internal audit/security data.

The PATCH endpoint uses a documented strict allowlist of editable profile fields, such as approved display, contact and preference fields, with server-side validation for each field. Reject unknown or protected fields rather than binding arbitrary request fields to a database model. Contact fields in this allowlist do not include email.

Never allow generic profile updates to modify:

- User id.
- Email.
- Password or password hash.
- Role.
- Email verification status.
- Owner approval status.
- Account status.
- Suspension or ban state.
- Session data.
- Admin or MFA fields.
- Audit fields.

Email changes and password changes must use dedicated verification/security flows and must not be implemented through the generic profile PATCH endpoint.

Build:

- Web Tenant Profile screen.
- Web Owner Account screen.
- Native Tenant Profile screen.
- Native Owner Account screen.

Show the user's current account state from `users` and, for Owners, the existing application/approval state from the authoritative Owner/host-application data. Do not duplicate those states in `user_profiles` or expose them as client-writable profile fields. Keep existing session/account-status restrictions in force; a profile response never grants a suspended or banned user access.

Provide links/actions for existing logout, password-reset/security and Help flows where those features already exist. Use the same backend validation rules for Web, Android and iOS. Client validation improves UX but never replaces server validation.

Do not add:

- Admin mobile/profile screens; Admin remains Web-only for v1 and this ticket serves Tenant/Owner only.
- Billing or subscription settings.
- Device/session-management UI.
- A second profile table.
- A new database.
- Generic role/status editing.
- Unrelated authentication features.

**Acceptance criteria:**

- An authenticated user can read only their own profile/account data.
- Cross-user profile access or modification is impossible at both API permission and RLS/database layers where applicable.
- Protected identity, authentication, role, verification, approval, account-status and security fields cannot be patched even if manually submitted.
- Tenant and Owner profile updates use a documented allowlist of editable fields.
- Owner application/approval status comes from the authoritative existing Owner/host data and is not duplicated as a client-writable profile field.
- Successful updates are reflected consistently on Web, Android and iOS.
- Web and Native Mobile validation matches backend validation rules.
- Email and password cannot be changed through the generic profile endpoint.
- Loading, validation-error, server-error, expired-session, offline/network-error, success and empty/optional-field states are handled appropriately.
- Web screens are keyboard accessible.
- Android/iOS screens provide appropriate screen-reader labels, touch targets, keyboard handling and text scaling.
- Tests cover own-profile read/update, cross-user attempts, protected-field injection, invalid values, unauthenticated requests and role-specific response data.
- No new database or duplicate profile/status source is introduced.

---

# EPIC C — Locations, Institutions and Master Data

### T-15 · Locations, institutions, amenities and categories

**Platforms:** Shared Backend/public data; Web-only Admin CRUD

**Priority:** Must-have | **Depends on:** T-12

**Prompt:** Build admin CRUD APIs and UI (data table, create/edit drawer, soft-deactivate toggle, display order, aliases) for locations (hierarchy via parent), institutions (with coordinates), amenities and property categories. Add public read endpoints returning only active records. Add `scripts/seed_locations.py` seeding East Sikkim, Gangtok, neighbourhoods Tadong, Sichey, Deorali, MG Marg, Ranipool, Majitar, Rangpo, ICFAI University Sikkim, and a starter amenity list (Wi-Fi, laundry, geyser, meals, CCTV, power backup, study desk, parking). Every change is audit-logged.

**Acceptance criteria:**

- No place name is hardcoded anywhere in code; adding a new city needs only admin data entry.
- Deactivated records stay attached to existing listings but disappear from public pickers.
- Non-admins get 403 on every write.

---

# EPIC D — Owner Onboarding and Listings

### T-16 · Host application and private verification documents

**Platforms:** Shared Backend + Web Owner application; native companion T-16M

**Priority:** Must-have | **Depends on:** T-10, T-12, T-14

**Prompt:** Create `host_applications` and `verification_documents`. Endpoints: `POST /owner/application` (draft and submit), `POST /owner/documents/presign` (R2 presigned PUT, 5-minute expiry, PDF/JPG/PNG, max 5 MB, random storage key), `POST /owner/documents` (confirm). Build the owner application UI with status tracker (draft, submitted, needs info, approved, rejected). Collect only necessary identity information; do not ask for full Aadhaar numbers. Pending owners can save property drafts but cannot submit them.

**Acceptance criteria:**

- The bucket has no public access; no document URL is permanent or guessable.
- Wrong file type or size is rejected before upload is authorised.
- An owner can see only their own application and documents (tested through RLS).

### T-16M · Native Owner application and status

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-14M, T-16

**Prompt:** Build the Owner application, minimal verification-document picker/upload and draft/submitted/needs-info/approved/rejected tracker using T-16 contracts. Use short-lived R2 URLs without DormioStay auth headers. Explain private data collection and keep document previews out of persistent caches. Support correction/resubmission and server-authoritative approval state; no native reviewer screens.

**Acceptance criteria:**

- Only the current Owner application is visible; pending/rejected users cannot bypass backend publish restrictions.
- Picker cancellation, wrong file, upload interruption/expiry and resubmission have clear states; no permanent public document URLs.
- Safe areas, keyboard, accessibility and native background privacy are checked; backend work is reused.

### T-17 · Admin host review

**Platforms:** Shared Backend moderation; Web-only Admin review

**Priority:** Must-have | **Depends on:** T-16, T-13, T-09

**Prompt:** Admin screens and APIs to list applications, open a detail drawer, view documents through short-lived signed links (each view audit-logged), and approve, reject (reason required) or request more information. Approval changes the owner to approved; notify the owner by email and in-app. Allow suspension and reactivation with a reason.

**Acceptance criteria:**

- Every decision creates an audit log entry with actor, reason, before and after values.
- Rejected owners can correct and resubmit.
- Suspending an owner hides their listings and revokes their sessions.

### T-18 · Property API with room configurations

**Platforms:** Backend-only — shared property APIs

**Priority:** Must-have | **Depends on:** T-15, T-16

**Prompt:** Create models `properties`, `property_locations`, `room_configurations`, `property_images`, `property_amenities` and the owner APIs: create draft, update by step, add/edit/remove room configurations, set amenities, house rules (gender, visitor, food, curfew, smoking), set location (private full address, public address, exact and public coordinates). One building equals one listing; sharing types are room configurations. Validate rent greater than 0, beds within totals, coordinates in range. Unique slug generation.

**Acceptance criteria:**

- Owners can only touch their own properties; cross-owner attempts fail at both API and database level.
- The private address never appears in any public response (tested).
- Editing a submitted listing follows the status rules (blocked while under review).

### T-19 · Geoapify service and address autocomplete

**Platforms:** Backend-only — shared Geoapify endpoints/distance

**Priority:** Must-have | **Depends on:** T-02, T-08, T-12

**Prompt:** Build `services/geo_service.py` calling Geoapify autocomplete, forward geocode and reverse geocode with timeouts and a Redis cache (24 hours), filtered to India and biased towards Gangtok. Expose `GET /geo/autocomplete?text=` (authenticated, rate-limited) and a Haversine distance helper that returns straight-line kilometres. If Geoapify fails or the quota is exceeded, return a typed error the UI turns into the manual-pin fallback.

**Acceptance criteria:**

- Server key is never exposed to Web or Native Mobile; both use the same backend geo endpoints.
- Cached repeat queries do not call Geoapify (tested with a mock).
- Distance is documented and labelled straight-line wherever shown.

### T-20 · Property photos with Cloudinary

**Platforms:** Shared Backend signing/validation + Web upload UI; native upload UI in T-21M

**Priority:** Must-have | **Depends on:** T-18, T-04, T-14

**Prompt:** Implement `POST /owner/properties/{id}/images/sign` (verifies ownership, image count at most 12, returns signed upload parameters), `POST /owner/properties/{id}/images` (stores `public_id`, url, alt text, sort order, cover flag), reorder and delete (signed destroy). Build the upload UI with multi-file drag and drop, previews, per-file progress, retry for failed files, reorder, set cover, required alt text.

**Shared upload boundary:** FastAPI validates ownership and final uploaded assets once for both clients. T-21M provides native picker/progress/retry UI using these signatures; do not duplicate the backend or send session credentials to Cloudinary.

**Acceptance criteria:**

- Only JPG, PNG, WebP up to 8 MB and at least 800px on the long side are accepted.
- A failed file never blocks the rest.
- No image is ever written to the server's disk.

### T-21 · Owner property wizard UI

**Platforms:** Web — native companion T-21M

**Priority:** Must-have | **Depends on:** T-18, T-19, T-20, T-04

**Prompt:** Build the 7-step owner wizard: basic info, address and map (Geoapify autocomplete plus draggable MapLibre pin, with manual fallback), room configurations, amenities (from admin list), house rules, photos, review and submit. Auto-save each step as a draft, show 'Step n of 7', sticky back/next, field-level validation, and a final preview that mirrors the public page. Mobile-first with large touch targets.

**Acceptance criteria:**

- Closing the browser mid-way and returning resumes the draft.
- A pin outside the supported area triggers a warning.
- Works fully on a 360px screen and with the keyboard.

### T-21M · Native Owner property wizard and photo uploads

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-14M, T-16M, T-18, T-19, T-20, T-22, T-26M

**Prompt:** Build the seven native steps: basic info, address/pin, room configurations, amenities, rules, photos, review/submit. Reuse shared draft/submission APIs, server Geoapify endpoints and T-26M native map adapter. System photo selection, Expo Image previews, progress/retry, ordering, cover and alt text use T-20 signed uploads. Resume saved server drafts; preserve in-memory unsaved input on errors and clearly distinguish saved versus unsaved. Do not introduce offline database synchronization.

**Acceptance criteria:**

- Closing/reopening resumes the server draft; no network failure is reported as a successful save.
- Owner-only APIs enforce status/validation rules; unsupported pin locations warn and private coordinates remain protected.
- Photo limits/type/size checks and partial-upload retries match Web; no session headers reach upload providers.
- Android Back/iOS swipe-back, keyboard avoidance, large text, screen readers and safe-area actions work across all steps.

### T-22 · Submit for review and admin listing moderation

**Platforms:** Shared submission/moderation API; Web-only Admin UI; both Owner clients consume outcomes

**Priority:** Must-have | **Depends on:** T-21, T-17

**Prompt:** Add `POST /owner/properties/{id}/submit` (only approved owners, only complete listings, status becomes pending review). Build the admin queue and detail view: approve, reject with reason, request changes with notes, suspend, and award or remove the Verified badge (separate action with its own audit entry). Owners are notified by email and in-app on each decision. Listings never become public automatically.

**Acceptance criteria:**

- Only `approved` listings appear in any public query (tested at API and RLS level).
- Verified badge cannot be set by an owner or by any payment-related field.
- Two admins acting at once get a conflict message instead of overwriting each other.

### T-23 · Owner dashboard and availability updates

**Platforms:** Shared dashboard/availability APIs + Web Owner UI; native companion T-23M

**Priority:** Must-have | **Depends on:** T-22, T-29

**Prompt:** Build the owner dashboard: stat cards (listings by status, pending visit requests, enquiries), a 'needs attention' list (outdated availability, pending requests, changes requested), property status cards, and a fast availability editor (update available beds per room configuration in two taps) that stamps `last_availability_update_at`. Show a friendly reminder when availability is older than 14 days.

**Shared API ownership:** define dashboard counts and availability read/update contracts once for Web and T-23M, including version/conflict handling. Visit-backed counts require T-29; do not ship fake counts.

**Acceptance criteria:**

- Availability changes apply immediately and are logged.
- Dashboard is fully usable on a phone.
- Numbers come from real queries and match the database.

### T-23M · Native Owner overview, properties and availability

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-14M, T-21M, T-23

**Prompt:** Build Overview, Properties and Availability tabs with real shared counts, status/needs-attention cards, property management and availability updates. Link visits and the native inbox when their tickets land, and Account through T-14P. Reuse shared mutation/version-conflict rules; never invent mobile-only listing statuses or permissions.

**Acceptance criteria:**

- Counts reflect the shared backend; availability updates agree with Web after refetch and show stale/conflict states.
- Only own properties are managed; edits under review and suspension follow existing rules.
- Refresh, empty, offline/error/retry, enlarged text and native navigation are tested.

---

# EPIC E — Tenant Discovery

### T-24 · Property search API

**Platforms:** Backend-only — shared search APIs

**Priority:** Must-have | **Depends on:** T-22, T-19

**Prompt:** Build `GET /properties` returning only `approved` listings. Filters: location (including children of a parent location), institution, min and max rent, sharing type, gender policy, amenities (all-of), availability (beds greater than 0), verified only. Sorts: relevance, price low to high, price high to low, recently updated, distance from the selected institution (Haversine, straight-line). Paginate 12 per page. Add pg\_trgm matching on location names and aliases. Public responses must exclude private address, exact coordinates (unless the owner allowed them) and phone numbers. Add the indexes from the architecture document.

**Acceptance criteria:**

- Draft, pending, rejected and suspended listings never appear (tested).
- 'tadong' and 'Tadong' match; a small typo still finds the area.
- Search over 1,000 seeded listings returns in under 500 ms in tests.

### T-25 · Homepage and search results UI

**Platforms:** Web — native companion T-25M

**Priority:** Must-have | **Depends on:** T-24, T-15, T-04

**Prompt:** Build the homepage (hero with search box, browse by location, verified properties, budget-friendly, how it works, owner call-to-action, footer) with content read from `/homepage`. Build the search results page: filter panel (desktop sidebar; mobile bottom sheet), removable filter chips, sort menu, PropertyCard grid (1/2/3 columns), pagination, skeletons, an empty state suggesting nearby areas, and URL query parameters so searches are shareable. Cards follow the Frontend Specification.

**Acceptance criteria:**

- Fully functional with AI switched off.
- Filter state survives refresh and back button.
- Lighthouse performance score of at least 85 on mobile for the results page.

### T-25M · Native Explore, homepage and search

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-15, T-24

**Prompt:** Build public Explore/discovery and search/filter/results from shared public content and search APIs. Use accessible filter/sort sheets, active chips, native PropertyCards, pagination and refresh, stable route/filter state and network/empty/error states. Browse without signing in. Preserve the current approved-listing, rent, distance and status semantics; AI remains optional.

**Acceptance criteria:**

- Public browsing and every filter work with AI off; results agree with Web for the same query.
- Back navigation preserves filters/scroll; pagination, refresh and interrupted requests avoid duplicates or stale result overwrites.
- No private addresses/exact hidden coordinates leak; lists remain usable without loading a map.
- Accessible Android/iOS screen layouts and image/list performance are checked on development builds.

### T-26 · Map view

**Platforms:** Web — native companion T-26M

**Priority:** Must-have | **Depends on:** T-25, T-19

**Prompt:** Add MapLibre GL JS using the Geoapify style (restricted browser key). Show violet pins with clusters, selected-pin highlight, a popup mini-card, a 'Search this area' button, and a List/Map toggle on mobile (60/40 split on desktop). Show the required attribution. Lazy-load the map. If tiles fail, keep the list working and show a notice.

**Acceptance criteria:**

- Hovering or tapping a card highlights its pin and vice versa.
- The map shows only public (possibly approximate) coordinates.
- The page works without the map loaded.

### T-26M · Native map adapter and map view

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-19, T-24, T-25M

**Prompt:** Choose and document an Expo/React Native compatible map provider/library after checking free development tiers, licensing, attribution, platform/build compatibility and key restrictions. Build native list/map switching, public pins/selection, viewport search and a reusable Owner pin-placement adapter. Keep server Geoapify geocoding/search and Haversine/privacy rules shared. Never import MapLibre GL JS or expose the server Geoapify key.

**Acceptance criteria:**

- Android/iOS map configuration is documented and tested where available; public provider keys are restricted and no backend keys are bundled.
- Only API-approved public coordinates are displayed; straight-line distance and attribution remain visible.
- Map failure leaves list/manual address flows usable; controls have accessible alternatives to gestures.
- The adapter supports Owner pin editing for T-21M without a duplicate geocoding service.

### T-27 · Property details page

**Platforms:** Web — native companion T-27M

**Priority:** Must-have | **Depends on:** T-24, T-04, T-26

**Prompt:** Build `/property/:slug` (client-side rendered via React Router with dynamic document title and JSON-LD structured data): gallery with lightbox, title, badges, rooms and pricing table (monthly rent and deposit stored as INTEGER rupees, not NUMERIC), amenities, house rules, location map with straight-line distance to the selected institution, 'last updated' stamp, similar properties, a report button, a Share button that copies the share-preview link (see T-54), and a sticky Request Visit card (desktop) or bar (mobile). Hide phone and exact address from guests per the Security document. Unavailable or hidden listings show a friendly not-found page.

**Acceptance criteria:**

- Non-approved listings return not-found to everyone except their owner and admins.
- Page is readable and usable at 360px width.
- View count increments at most once per visitor session.

### T-27M · Native property details, sharing and app links

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-24, T-26M

**Prompt:** Build native property details with Expo Image gallery, rooms/INTEGER-rupee pricing, badges, amenities, rules, permitted public map, straight-line distance, freshness and safe-area visit action. Share canonical HTTPS property links with the native share sheet; adopt T-54 rich previews when available. Handle verified app links with a Web fallback and recheck visibility/permissions. Feature actions connect to their companion tickets when delivered.

**Acceptance criteria:**

- Unapproved/hidden listings expose no private data and show a safe unavailable state; backend owner exceptions remain unchanged.
- Sharing works without T-54; known/unknown/malformed app links and app-not-installed Web fallback are verified.
- Gallery, enlarged text, screen readers, back navigation and sticky actions work on Android/iOS.

### T-28 · Saved properties

**Platforms:** Shared Backend + Web saved-properties UI; native companion T-28M

**Priority:** Must-have | **Depends on:** T-27, T-12, T-14

**Prompt:** Add `saved_properties` with a unique (user, property) pair and endpoints `POST /saved/{property_id}`, `DELETE /saved/{property_id}`, `GET /saved`. Build the heart button with optimistic updates (guests are prompted to log in and return to the same place) and the Saved page in the tenant dashboard. Show 'no longer available' for listings that were removed.

**Acceptance criteria:**

- A user can never read or modify another user's saved list (RLS test).
- Double-tapping does not create duplicates.
- Saved status shows correctly on cards and the details page.

### T-28M · Native saved properties

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-14M, T-25M, T-27M, T-28

**Prompt:** Build native Save actions and Saved tab against T-28 endpoints. Prompt guests to authenticate and return safely; optimistic changes roll back on failure and refetch after reconnect. Clear private saved data on account change; no persistent offline store.

**Acceptance criteria:**

- Saved state agrees across cards, details and Web after refetch; rapid taps do not duplicate records.
- Another user cannot see the prior account cache; removed listings, offline failure, empty list and retries are clear.

---

# EPIC F — Visits, Notifications and Reports

### T-29 · Visit request API and status machine

**Platforms:** Backend-only — shared visit API/status machine

**Priority:** Must-have | **Depends on:** T-18, T-22, T-10, T-08, T-12

**Prompt:** Create `visits` and `visit_events` (standardized; not visit_history). Endpoints: `POST /visits` (verified tenants, future date within 30 days, optional room configuration, message up to 500 characters), `GET /visits`, `PATCH /visits/{id}` with actions cancel (tenant), accept, reject, propose-new-time (owner), confirm-new-time (tenant), tenant-confirm-completed. Allowed transitions only: pending to accepted, rejected, reschedule\_requested, cancelled; reschedule\_requested to confirmed or cancelled; accepted or confirmed to completed, cancelled or no\_show. Every change writes a `visit_events` row and creates notifications. Reveal the owner's phone to the tenant only after acceptance.

**Dependency clarification:** visits depend on shared properties/moderation/auth, not a Web details page. Commit visit events transactionally; T-31 wires their shared inbox/email delivery so neither client creates a separate notification pipeline.

**Acceptance criteria:**

- Illegal transitions return 409 with a clear code.
- Owners can only act on visits for their own properties; tenants only on their own.
- A completed visit is only possible through the tenant's own confirmation (needed for reviews).
- Daily request limit of 10 enforced.

### T-30 · Visit request screens (tenant and owner)

**Platforms:** Web — native companion T-30M

**Priority:** Must-have | **Depends on:** T-29, T-23, T-28

**Prompt:** Tenant: a Request Visit modal (bottom sheet on mobile) with date and time picker in IST, room choice and message; a Visits page of VisitCards with status badges and actions. Owner: a Visits tab listing pending requests first, with Accept, Reject and Suggest another time in one tap each, plus a response note. Show clear empty states and optimistic updates with rollback on failure.

**Acceptance criteria:**

- All times display in IST.
- Unverified-email users see an explanation and a resend option instead of an error.
- The full flow works on a phone.

### T-30M · Native Tenant and Owner visits

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-14M, T-23M, T-27M, T-29

**Prompt:** Build native request-visit sheet, Tenant Visits/status and Owner response flows using the single T-29 state machine. Use IST-aware date/time controls, verification gating, conflict messages and safe optimistic rollback. Do not queue visits offline; after an uncertain response refetch before retrying.

**Acceptance criteria:**

- Tenant request → Owner accept/reject/reschedule → Tenant confirmation/cancellation uses legal shared transitions only.
- IST display is consistent on devices in other time zones; private contact visibility follows backend policy.
- Expired sessions, unverified accounts, offline/timeout/conflict and accessibility states are tested on both native platforms.

### T-31 · In-app notifications and event emails

**Platforms:** Shared Backend/inbox/email + Web UI; native companion T-31M

**Priority:** Must-have | **Depends on:** T-09, T-29, T-22

**Prompt:** Create `notifications` and `services/notification_service.py` that, for each event, writes an in-app notification and queues an email through the outbox. Events: email verification, host approved/rejected/needs info, listing approved/rejected/changes requested, visit requested/accepted/rejected/rescheduled/cancelled, new message (when enabled), report outcome. Build the bell with unread count, a notifications page, mark as read, and short email templates with plain-text versions.

**Shared API ownership:** implement recipient-scoped list/unread/mark-read endpoints and event creation once for both clients. T-31M builds the native inbox and, when enabled, adds push delivery registration/dispatch to this same service/outbox rather than duplicating it. Core inbox/email work without push permission or configuration.

**Acceptance criteria:**

- Notifications are visible only to their recipient (RLS test).
- A failed email never blocks the triggering action.
- Admins can see email status (queued, accepted, delivered, failed).

### T-31M · Native notification inbox and optional push delivery

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-14M, T-30M, T-31

**Prompt:** Build Tenant Notifications and Owner inbox access from Overview/Account using shared unread/list/mark-read APIs. Inbox is Must-have independent of push. When push is enabled, integrate Expo Notifications with contextual permission UX, authenticated device registration, token rotation/removal and safe notification-tap routes. Choose Expo Push Service versus direct APNs/FCM in this ticket; FCM is delivery transport only, not Firebase Auth or a second backend. Add required registrations/delivery adapter once to the existing FastAPI notification service/outbox using new migrations. Backend controls recipients and event truth; private lock-screen content is forbidden.

**Acceptance criteria:**

- Inbox/read state is consistent with Web, recipient-only, refreshable and usable with permission denied or push unconfigured.
- If enabled, Android/iOS device tests verify contextual permission, generic payloads, provider receipt/acceptance distinctions, duplicate prevention and authorised tap navigation.
- Logout/account switch/revocation removes delivery association; invalid device tokens are retired; no credential or private message/document appears in payloads or telemetry.
- Push configuration/rollout decision is recorded; a disabled optional delivery channel is not represented as tested or shipped.

### T-32 · Reports and moderation workflow

**Platforms:** Shared report/moderation API + Web reporter UI; Web-only Admin investigation; native reporter companion T-32M

**Priority:** Must-have | **Depends on:** T-27, T-17

**Prompt:** Add `reports` and `moderation_actions`. Any logged-in user can report a listing, user, review or message with a reason and details (limited per day). Admin queue with statuses Pending, Under Investigation, Action Required, Resolved, Dismissed; the admin can hide a listing, suspend a user, or dismiss, always with a recorded reason. The reporter sees only their own report status.

**Acceptance criteria:**

- Duplicate reports on the same target by the same user are merged or blocked.
- Every moderation action creates an audit entry.
- A suspended user's sessions stop working immediately.

### T-32M · Native reporting and own report status

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-14M, T-27M, T-32

**Prompt:** Build accessible report submission and own-status screens for Tenant/Owner using T-32 validation and daily limits. Offer report actions for available target types. Admin investigation, sanctions and audit review remain Web-only.

**Acceptance criteria:**

- Only own report status is visible; duplicate/rate-limited submissions show the shared API result.
- No native moderation controls or privileged report data; safe keyboard, empty/error/loading and retry states.

---

# EPIC G — Admin Control Centre

### T-33 · Admin dashboard overview and audit log viewer

**Platforms:** Web-only Admin dashboard/audit viewer + supporting Backend

**Priority:** Must-have | **Depends on:** T-17, T-22, T-32

**Prompt:** Build the admin shell (sidebar, top bar) and the dashboard: counts for pending hosts, pending listings, open reports, visits this week, live listings, failed emails, plus a recent activity list. Build a read-only, filterable, paginated audit log viewer (actor, action, target, date). Every number comes from real queries; there is no fake data.

**Acceptance criteria:**

- Queue counts link to the filtered queue.
- The audit log cannot be edited or deleted from the UI or the API.
- Dashboard loads in under 2 seconds with seeded data.

### T-34 · Homepage content editor

**Platforms:** Web-only Admin content editor + shared public content Backend

**Priority:** Should-have | **Depends on:** T-33, T-25

**Prompt:** Build `homepage_sections` management: edit hero text, banners, featured sections, show/hide, reorder; saves go to a draft with preview; Publish makes it live; Revert restores the previous version. Cache the public homepage for 5 minutes in Redis and invalidate on publish.

**Client scope:** published content is consumed by Web T-25 and Native Mobile T-25M; editing, preview and publish controls remain Web-only. Native rendering must safely ignore unsupported optional section types.

**Acceptance criteria:**

- Draft changes never appear publicly before Publish.
- Revert works in one click.
- Broken or empty sections never crash the public homepage.

### T-35 · Announcements

**Platforms:** Web-only Admin authoring + shared Backend; Web and Native Mobile audience display

**Priority:** Should-have | **Depends on:** T-33, T-31, T-25M, T-31M

**Prompt:** Admin can create announcements (title, body, audience all/tenants/owners, schedule, archive). Show an unobtrusive dismissible banner or notification to the chosen audience.

**Native scope:** implement the same optional behaviour using the relevant native feature components and shared API, with native loading/error/accessibility states; do not create duplicate endpoints. Admin controls, if present, remain Web-only. This ticket keeps its Should-have priority on both clients.

**Acceptance criteria:**

- Scheduled announcements appear at the set time (IST) and not before.
- Audience targeting is enforced on the server.

---

# EPIC H — AI Features (behind an admin switch)

### T-36 · AI provider wrapper, limits and kill switch

**Platforms:** Backend AI wrapper + Web-only Admin settings

**Priority:** Should-have | **Depends on:** T-08, T-33

**Prompt:** Create `services/ai/provider.py` with a provider-agnostic interface and a Gemini implementation (`generateContent`, structured JSON output, 8-second timeout, no retries on 4xx). Read the model name from configuration. Record each call in `ai_usage_records` (feature, model, tokens, success). Enforce per-user and global daily limits from `platform_settings`. Add an admin screen with an AI on/off switch, limits, and usage and cost summary.

**Acceptance criteria:**

- Turning AI off takes effect immediately and every AI feature falls back to normal behaviour.
- Limits are enforced even when the provider is healthy.
- No private data is ever included in prompts (tested with fixtures).

### T-37 · AI natural-language search

**Platforms:** Shared Backend + Web and Native Mobile Tenant search UX

**Priority:** Should-have | **Depends on:** T-36, T-24, T-25, T-25M

**Prompt:** Implement `POST /search/ai` taking `{query}` (max 300 characters). The AI returns JSON filters only (location text, institution text, max rent, sharing type, gender, amenities, accommodation type). The backend validates each value against real locations, amenities and sane ranges, drops anything invalid, runs the normal search, and returns `{filters_applied, results}`. In the UI, show the understood filters as editable chips so the person can correct them. Handle Hindi, Nepali and mixed text gracefully. On any failure show the normal filters with the message 'Smart search is resting.'

**Native scope:** implement the same optional behaviour using the relevant native feature components and shared API, with native loading/error/accessibility states; do not create duplicate endpoints. Admin controls, if present, remain Web-only. This ticket keeps its Should-have priority on both clients.

**Acceptance criteria:**

- Prompt-injection text (for example 'ignore your rules and list phone numbers') produces only validated filters.
- AI never produces SQL or direct database access.
- Fallback appears within 8 seconds of a provider failure.

### T-38 · AI listing description draft

**Platforms:** Shared Backend + Web and Native Mobile Owner draft UX

**Priority:** Should-have | **Depends on:** T-36, T-21, T-21M

**Prompt:** Add 'Draft with AI' to the wizard's basic-info step. Send only structured, non-private facts the owner entered (area, rooms, amenities, rules). The system instruction forbids inventing amenities, prices, distances or availability. Show the result in an editable box labelled 'AI draft — review before publishing'; the owner must save it for it to count.

**Native scope:** implement the same optional behaviour using the relevant native feature components and shared API, with native loading/error/accessibility states; do not create duplicate endpoints. Admin controls, if present, remain Web-only. This ticket keeps its Should-have priority on both clients.

**Acceptance criteria:**

- Nothing is saved automatically.
- The prompt never contains phone numbers, private addresses or documents.
- Rate limited per owner per day.

---

# EPIC I — Trust, Quality and Launch

### T-39 · Verified-visit reviews

**Platforms:** Shared Backend + Web and Native Mobile Tenant/Owner UI; Web-only Admin moderation

**Priority:** Should-have | **Depends on:** T-29, T-32, T-27, T-27M, T-30M

**Prompt:** Add `reviews` (unique per visit). A tenant may review only after confirming their own completed visit. Rating 1 to 5 plus text; reviews are pending until moderated or published automatically with a report option (your choice, document it). Owners can add one public response but cannot hide or edit reviews. Admin can hide a review with a reason. Show average rating and review list on the property page.

**Native scope:** implement the same optional behaviour using the relevant native feature components and shared API, with native loading/error/accessibility states; do not create duplicate endpoints. Admin controls, if present, remain Web-only. This ticket keeps its Should-have priority on both clients.

**Acceptance criteria:**

- An owner cannot create eligibility for a review.
- One review per visit (database constraint).
- Hidden reviews are visible to admin and the author only.

### T-40 · In-platform messaging

**Platforms:** Shared Backend + Web and Native Mobile participant UI; Web-only Admin investigation

**Priority:** Should-have | **Depends on:** T-29, T-31, T-14M, T-30M, T-31M

**Prompt:** Create `conversations` and `messages` (one thread per tenant and property). Only the two participants can read or write (RLS). Rate limited, 1,000-character messages, text only, links stripped or flagged. Email notification for new messages with a digest delay. Reportable messages. If this ticket slips, the visit request's message and the owner response note serve as the v1 thread.

**Native scope:** implement the same optional behaviour using the relevant native feature components and shared API, with native loading/error/accessibility states; do not create duplicate endpoints. Admin controls, if present, remain Web-only. This ticket keeps its Should-have priority on both clients.

**Acceptance criteria:**

- No third party, including other owners, can read a thread (tested).
- Phone numbers typed in messages are not automatically exposed elsewhere.
- Admin can read a thread only through a report investigation, and each read is audit-logged.

### T-41 · Compare properties

**Platforms:** Web + Native Mobile comparison UI; shared existing property data

**Priority:** Should-have | **Depends on:** T-27, T-28, T-27M, T-28M

**Prompt:** Allow adding up to 3 properties to a compare tray and show a side-by-side table (rent, deposit, sharing, distance, amenities, rules, verified, last updated). On mobile show a swipeable layout. Store the selection client-side.

**Native scope:** implement the same optional behaviour using the relevant native feature components and shared API, with native loading/error/accessibility states; do not create duplicate endpoints. Admin controls, if present, remain Web-only. This ticket keeps its Should-have priority on both clients.

**Acceptance criteria:**

- Limit of 3 is enforced with a clear message.
- Removed or unavailable properties show a notice instead of breaking the table.

### T-42 · Property revisions for live listings

**Platforms:** Shared Backend + Web and Native Mobile Owner revisions; Web-only Admin review

**Priority:** Should-have | **Depends on:** T-22, T-21, T-21M, T-23M

**Prompt:** When an owner edits important fields of an approved listing (rent, address, rules, photos), save the change as a `property_revisions` row while the live version stays visible. Admin reviews and approves or rejects the revision; approval applies it. Minor edits such as availability apply immediately. If this slips, use the simpler rule: important edits move the listing back to pending review and are logged.

**Native scope:** implement the same optional behaviour using the relevant native feature components and shared API, with native loading/error/accessibility states; do not create duplicate endpoints. Admin controls, if present, remain Web-only. This ticket keeps its Should-have priority on both clients.

**Acceptance criteria:**

- The public page never shows unapproved changes.
- Revision history shows who changed what.

### T-43 · SEO basics

**Platforms:** Web SEO + shared Backend sitemap; no native SEO screens

**Priority:** Should-have | **Depends on:** T-27

**Prompt:** Add per-page titles and meta descriptions (set client-side through PageHead), clean URLs, default Open Graph tags (link previews for chat apps come from the share route in T-54), `sitemap.xml` served by a FastAPI endpoint (GET /api/v1/seo/sitemap.xml, exposed at /sitemap.xml through a Render rewrite rule) listing only approved properties and public pages, `robots.txt` (a static file in public/) that blocks dashboards and admin, and structured data (JSON-LD) on property pages. Draft, pending and suspended listings must never be indexable.

**Acceptance criteria:**

- Sitemap contains no non-approved listing.
- Private and dashboard routes carry `noindex`.

### T-54 · Share-preview route for WhatsApp and social links

**Platforms:** Shared Backend public preview + Web share UI; Native Mobile reuses link via T-27M

**Priority:** Should-have | **Depends on:** T-27, T-05

**Prompt:** Build a small FastAPI router `api/v1/share.py` with `GET /share/property/{slug}` (public, no login). For an approved listing return a tiny `text/html` page (status 200) with: `<title>`, a meta description (area, starting rent and the first 150 characters of the description), `og:title`, `og:description`, `og:type=website`, `og:url` (the canonical `FRONTEND_ORIGIN/property/{slug}`), `og:image` (the cover photo through a Cloudinary transformation of 1200 × 630), `twitter:card=summary_large_image`, a canonical link, and an instant redirect to the canonical URL using both `<meta http-equiv="refresh" content="0;url=...">` and `location.replace`. For any unknown or non-approved slug return a generic DormioStay preview page with status 404 (the same response for every case). Never include a phone number, private address, exact coordinates or owner details, and HTML-escape every value. Send `Cache-Control: public, max-age=300` and `X-Robots-Tag: noindex` so only the canonical page is indexed. Rate limit per IP. In the web app, add a Share button on the property page and in the owner dashboard that copies `/api/v1/share/property/{slug}` on the site domain (it works through the existing `/api/*` rewrite) and opens the phone's share sheet (`navigator.share`) when available.

**Native sharing:** reuse the same public preview URL in the native share sheet and verify approved deep-link routes plus Web fallback. Basic canonical-link sharing in T-27M is Must-have and does not wait for this Should-have rich-preview enhancement.

**Acceptance criteria:**

- Fetching the share URL without JavaScript returns the preview tags; opening it in a browser lands on `/property/{slug}` almost immediately.
- Unknown, draft, pending and suspended slugs return the generic page with status 404 and no listing data.
- Output is escaped (test with `<script>` in a listing name).
- Pasting the link into WhatsApp on staging shows the title, description and cover photo.

### T-44 · Legal, help and trust pages

**Platforms:** Web — native companion T-44M

**Priority:** Must-have | **Depends on:** T-04

**Prompt:** Create pages for Terms of Use, Privacy Policy, Community and Listing Guidelines, How Verification Works, FAQ, Contact (dormiostay@gmail.com) and About. Add cookie notice if non-essential tracking is used. Link everywhere in the footer and at signup with consent checkboxes. Use placeholder text clearly marked 'pending legal review' until a professional reviews the policies.

**Acceptance criteria:**

- Signup requires accepting Terms and Privacy, and the time of acceptance is stored.
- Verification page states what 'Verified' means and that it cannot be purchased.

### T-44M · Native legal, help and trust surfaces

**Status:** Planned; not implemented.

**Platforms:** Native Mobile (Android/iOS); shared Backend reused

**Priority:** Must-have | **Depends on:** T-04M, T-14M, T-44

**Prompt:** Expose Terms, Privacy, Community/Listing Guidelines, verification explanation, FAQ, Contact and About from native signup and Profile/Account, using the same reviewed policy content and shared consent contract as Web. Label any pending legal review. Use safe approved links or accessible native text; do not copy backend services or create independent policy versions.

**Acceptance criteria:**

- Signup consent/version/time follows the shared backend contract and policies are reachable before signup.
- Native storage, notifications and privacy disclosures match actual behaviour; verification cannot be purchased.
- Help/contact links and large-text/screen-reader navigation work on Android/iOS.

### T-45 · Security and end-to-end test suite

**Platforms:** Shared Backend security + Web and Native Mobile journeys; Admin journeys Web-only

**Priority:** Must-have | **Depends on:** T-12, T-29, T-22, T-14P, T-16M, T-21M, T-23M, T-25M, T-26M, T-27M, T-28M, T-30M, T-31M, T-32M, T-44M

**Prompt:** Using the Security and Access document, write: (1) role-matrix tests for every protected endpoint, (2) RLS cross-user tests for every table, (3) session tests (expiry, revocation, ban), (4) rate-limit tests including with Redis down, (5) upload validation tests, (6) Playwright journeys: student search to visit request; owner signup to approved live listing; admin approval and report handling. Run all of it in CI.

**Native and transport extension:** add Android/iOS journeys for public discovery → auth → saved/visit, and Owner application → wizard/upload → availability/visit response. Test both session transports, mixed credentials, anonymous CSRF, invalid bearer, idle/absolute expiry, reset/ban/revocation, SecureStore failure and token/telemetry leakage. Test links, network loss, denied permissions, background/resume, account switching and push privacy if enabled. Include VoiceOver/TalkBack and keyboard/safe-area checks. Preserve Web Admin journeys; no native Admin tests/screens. A missing iOS runner/device is an explicit outstanding verification item, not a pass; both platforms must be verified before T-46 launch.

**Acceptance criteria:**

- Pipeline fails if any permission test fails.
- Tests cover every item in the pre-launch security checklist.

### T-46 · Launch hardening and readiness gate

**Platforms:** Web + Native Mobile + shared Backend launch readiness

**Priority:** Must-have | **Depends on:** T-45, T-05

**Prompt:** Prepare launch: move the API to an always-on plan, enable Neon backups and perform one practised restore, configure Sentry alerts, usage alerts at 70% and 90% for Geoapify, Cloudinary, Brevo and Gemini, verified email sender with real inbox test, key-rotation runbook, admin account recovery runbook, seed real locations, and an owner-onboarding guide. Produce a `docs/launch-checklist.md` mirroring the PRD readiness gates.

**Native release gate:** include Android/iOS signed release candidates, store-account/signing ownership, privacy disclosures, support/OS policy, verified app links, physical-device journeys and release/rollback instructions. Confirm no secrets in bundles or breadcrumbs, both transport revocation checks and inbox behaviour when push is denied. Prefer free development tools but budget unavoidable distribution costs. All Must-have companion tickets and PRD gates must pass before a public three-client launch; a date/owner for an unmet gate does not waive it.

**Acceptance criteria:**

- Web, Android and iOS launch evidence is recorded; no required gate is counted as passed merely because local hardware or credentials are unavailable.

- Restore from backup completed and timed once.
- Every required PRD launch gate is checked off before public launch. Outstanding gates have a named owner/date and block launch until resolved.

---

# NICE-TO-HAVE (after v1)

For each item below, use the Global Context block and write a full ticket only when you are ready to build it.

| ID   | Feature                            | Priority     | Depends on | One-line prompt                                                                                                                                | Platforms                                                                   |
| ---- | ---------------------------------- | ------------ | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| T-47 | Property AI assistant              | Nice-to-have | T-36, T-39 | A per-listing Q&A assistant that answers only from approved listing data and FAQs, says 'I don't know' otherwise, and cites the field it used. | Shared Backend + Web/Native Mobile; Admin settings Web-only                 |
| T-48 | Featured placements                | Nice-to-have | T-24, T-33 | Admin-managed, clearly labelled sponsored slots that never alter verification or hide organic results.                                         | Shared Backend + Web/Native Mobile display; Admin controls Web-only         |
| T-49 | Offers and coupons                 | Nice-to-have | T-48       | Offer and coupon data model with eligibility rules; no redemption or payment until a billing system exists.                                    | Backend design; Web-only Admin, no redemption clients                       |
| T-50 | Owner subscription scaffolding     | Nice-to-have | T-23       | Plan and entitlement tables and feature gates behind a switch; billing stays off until pricing is validated.                                   | Shared Backend scaffolding; Web/Native Owner display if enabled, no billing |
| T-51 | Visit reminders                    | Nice-to-have | T-31       | Scheduled reminders to tenants and owners before an accepted visit.                                                                            | Shared Backend delivery + Web/Native inboxes                                |
| T-52 | Owner analytics                    | Nice-to-have | T-23       | Views, saves and enquiries per listing with simple charts.                                                                                     | Shared Backend + Web/Native Owner UI                                        |
| T-53 | Search history and recommendations | Nice-to-have | T-24       | Recently viewed and 'similar properties' using search logs; no personal data beyond user id.                                                   | Shared Backend + Web/Native Tenant UI                                       |

---

## Recommended build order (summary)

1. **T-01 — completed:** monorepo, tooling and CI.
2. **T-02 — completed:** FastAPI foundation.
3. **T-03 — completed:** database foundation and applied first migration.
4. **T-04 — completed Web foundation:** design system, layouts, API client and accessibility verification.
5. **T-04M — planned mobile foundation.**
6. **T-05 — planned deployment/environment foundation.**
7. **T-06 onward — shared Backend + appropriate clients:** T-06/T-07/T-08, T-09/T-10/T-11, T-12/T-13, then T-14/T-14M and T-14P.
8. **Master data and Owner contracts:** T-15, T-16/T-16M, T-17, T-18/T-19/T-20, Web wizard T-21 and Web Admin moderation T-22. Backend work is written once.
9. **Discovery and native map:** T-24, T-25/T-25M, T-26/T-26M, T-27/T-27M, T-28/T-28M. T-26M supplies the native pin adapter; then T-21M integrates the native wizard. Web T-21 can precede native mapping without introducing a dependency cycle.
10. **Visits and Owner dashboards:** T-29, T-23/T-23M, T-30/T-30M, T-31/T-31M, T-32/T-32M. T-29 can follow T-22 before discovery if useful; dashboard visit counts depend on its shared APIs.
11. **Web Admin and legal:** T-33, T-44/T-44M; then T-45 cross-client security and journeys.
12. **Should-haves, only when their dependencies pass:** T-34, T-36, T-37, T-38, T-43, T-54, T-39, T-40, T-41, T-42, T-35. Preserve their existing optional priority on applicable clients.
13. **T-46 launch:** all Must-have Web/Android/iOS gates; no native Admin and no unrelated scope expansion.

**Counts:** original 54 IDs preserved (36 Must-have, 11 Should-have, 7 Nice-to-have). Added 13 native companions (T-04M, T-14M, T-16M, T-21M, T-23M, T-25M, T-26M, T-27M, T-28M, T-30M, T-31M, T-32M, T-44M) and one shared profile ticket (T-14P), all Must-have: **68 total items; 50 Must-have, 11 Should-have, 7 Nice-to-have**. Optional push delivery within T-31M does not gate the required native inbox. Four Must-have tickets are already complete; no implementation is performed by this documentation revision.
