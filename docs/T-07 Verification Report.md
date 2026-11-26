# T-07 Verification Report — CSRF and Origin Protection

**Date:** 2026-11-26 (Asia/Kolkata)<br>
**Ticket:** T-07 (CSRF and Origin Protection Implementation)<br>
**Branch:** `feature/t07-csrf-origin-protection`<br>
**Parent HEAD:** `b345b9884439d99141c03918754d930f40676fc3` (T-06 canonical commit)
**Overall Status: LOCAL IMPLEMENTATION VERIFIED — PROVISIONAL SECURITY IMPLEMENTATION; INDEPENDENT SECURITY REVIEW, RATE LIMITING (T-08) AND POSTGRESQL INTEGRATION PENDING**

> [!CAUTION]
> **Production Readiness Notice:**
> Do NOT label T-07 production-ready.
> T-07 implements the core CSRF token generation, session binding, anonymous bootstrap, Web Origin verification, strict native bearer exemption, and React Web API client integration. The public authentication endpoints remain guarded by `auth_endpoints_enabled = False` (returning HTTP 503 Service Unavailable).
> Live production deployment requires:
> 1. Independent application security and code review of T-07 changes.
> 2. Implementation and verification of T-08 (Upstash / Redis rate limiting across `/login`, `/signup`, `/reset`).
> 3. Execution of live PostgreSQL integration tests under concurrent loads.
> 4. Verification of Gate 5 Render deployment after independent Codex Astra approval for T-05 is granted.

---

## 1. Executive Summary

| Verification Category | Status | Details |
|---|---|---|
| **Local Implementation Verification** | **PASS** | Cryptographic CSRF tokens (HMAC-SHA256), anonymous bootstrap, session-bound tokens, exact Web Origin verification, native bearer CSRF exemption, and React Web API client integration are fully implemented and verified locally. |
| **Origin Verification Policy** | **PASS** | Enforces exact, approved Web Origin (`FRONTEND_ORIGIN`). Rejects foreign Origins, `null` Origins, and missing Origins on Web writes with HTTP 403 (`FORBIDDEN_ORIGIN`). |
| **Anonymous Web CSRF Bootstrap** | **PASS** | `GET /api/v1/auth/csrf` issues short-lived anonymous bootstrap tokens (`anon.<ts>.<nonce>.<sig>`) for pre-auth writes (`/signup`, `/login`). |
| **Session-Bound CSRF Protection** | **PASS** | Authenticated Web requests require tokens bound to the active session (`sess.<ts>.<nonce>.<sig>`). Anonymous tokens cannot be used on authenticated writes; session tokens cannot be used across sessions or after session rotation/logout. |
| **Native Bearer Exemption** | **PASS** | Only successfully validated, cookie-free Bearer requests (`Authorization: Bearer <token>`) are exempt from CSRF. Invalid or expired tokens fail with HTTP 401 and are never exempt. |
| **Native Pre-Auth Disambiguation** | **PASS** | Native pre-auth requires `X-Client-Type: native`, `Content-Type: application/json`, zero cookies, and strictly forbids browser `Origin` headers (rejecting with HTTP 403 `NATIVE_ORIGIN_PROHIBITED`). |
| **CSRF Secret Deployment Hardening** | **PASS** | Supports canonical `CSRF_SECRET` alias (`.env.example`). Enforces deployment-time validation: `Settings` fails fast if `auth_endpoints_enabled=True` in staging/production without an explicit key; `get_csrf_signing_key()` strictly fails closed. |
| **React Web Client Integration** | **PASS** | `apps/web/src/lib/api-client.ts` integrates `bootstrapCsrf()`, in-memory token storage, `clearCsrfToken()`, and automatic `X-CSRF-Token` header attachment on all mutations without exposing raw session credentials. |
| **T-06 Compatibility & Regression** | **PASS** | All 56 existing T-06 authentication unit tests pass in isolation and in combined suites. Isolation discrepancy resolved via monkeypatch teardown fixture. |
| **Unit Test Suites** | **PASS** | **23 passed T-07 security tests**; **56 passed T-06 tests**; **518 passed total API unit tests**; **84 passed Web Vitest tests**. |
| **Static Analysis & Builds** | **PASS** | Ruff check clean; Ruff format clean (43 files); strict Mypy clean (45 files); Web TypeScript strict check (`tsc --noEmit`) clean; ESLint clean (0 warnings); Prettier check 100% clean; Web production build (`vite build`) succeeded. |
| **Activation Guard** | **DISABLED BY DEFAULT** | `auth_endpoints_enabled` defaults to `False`; `/auth/csrf` and auth endpoints return HTTP 503 until T-08 rate limiting is active. |
| **T-05 Baseline Protection** | **PROTECTED** | T-05 Gate 4 live sequence-aware baseline verified; Astra sign-off PENDING; Gate 5 Render deployment NOT STARTED; zero T-05 files modified. |

---

## 2. Security Architecture & Implementation Details

### 2.1 CSRF Token Generation & Cryptographic Verification

Implemented in [apps/api/app/core/csrf.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/core/csrf.py):

1. **Anonymous Bootstrap Tokens:**
   - **Format:** `anon.<issued_at>.<nonce>.<signature>`
   - **Signature:** Computed over `anon:<issued_at>:<nonce>` using HMAC-SHA256 with the server signing key.
   - **Expiration:** Enforces lifetime (default 3600s / 1 hour). Expired tokens fail with `CSRF_TOKEN_EXPIRED`.
   - **Clock Skew:** Enforces maximum future drift ($\le 60$s).

2. **Session-Bound Tokens:**
   - **Format:** `sess.<issued_at>.<nonce>.<signature>`
   - **Signature:** Computed over `sess:<session_token_hash>:<issued_at>:<nonce>`, binding the token cryptographically to the exact database session row (`user_sessions.token_hash`).
   - **Session Isolation:** A token issued for Session A cannot be used on Session B because the signature verification reconstructs the message using Session B's `token_hash`, failing with `CSRF_TOKEN_INVALID`.
   - **Session Rotation:** Re-login creates a new session with a new `token_hash`, immediately invalidating prior session CSRF tokens.
   - **Logout / Revocation:** A revoked session fails session validation (HTTP 401 `SESSION_REVOKED`), preventing any state-changing execution.

3. **Mutual Non-Confusion Invariant:**
   - Anonymous tokens cannot authenticate state-changing requests on active cookie sessions (rejected with HTTP 403 `CSRF_TOKEN_INVALID`).
   - Session-bound tokens cannot be used as anonymous bootstrap tokens.

### 2.2 Origin Verification Policy

- **Mandatory Web Origin:** All state-changing Web requests (`POST`, `PUT`, `PATCH`, `DELETE`) require an `Origin` header. Missing `Origin` headers are rejected with HTTP 403 (`FORBIDDEN_ORIGIN`).
- **Exact Whitelist Matching:** The incoming `Origin` is normalized (lowercased, trailing slashes removed) and compared against `settings.frontend_origin`.
- **Disallowed / Malicious Origins:** Any mismatch, foreign domain (e.g. `https://attacker.evil.com`), or untrusted literal (e.g. `null` from sandboxed iframes) is rejected with HTTP 403 (`FORBIDDEN_ORIGIN`).
- **CORS Allowlist:** Maintained strictly in `CORSMiddleware` in [apps/api/app/main.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/main.py), adding `"X-CSRF-Token"` and `"X-Client-Type"` to `allow_headers`.

### 2.3 Native Bearer & Pre-Authentication Disambiguation

1. **Validated Bearer Exemption:**
   - Requests presenting `Authorization: Bearer <token>` are exempt from CSRF and Origin checks **only after successful database session validation**.
   - If the token is invalid, expired, or revoked, the request fails with HTTP 401 (`SESSION_INVALID` / `SESSION_EXPIRED` / `SESSION_REVOKED`). It is **never** exempt.
2. **Mixed Credential Rejection:**
   - Requests presenting both a session cookie and an `Authorization` header are rejected with HTTP 400 (`AMBIGUOUS_CREDENTIALS`).
3. **Malformed Bearer Rejection:**
   - Malformed `Authorization` headers fail with HTTP 401 (`INVALID_AUTHORIZATION_HEADER`), never falling back to cookie authentication.
4. **Native Pre-Authentication (`client_type: native`):**
   - Must carry `X-Client-Type: native` header.
   - Must use `Content-Type: application/json` (rejecting `application/x-www-form-urlencoded` with HTTP 415 `INVALID_CONTENT_TYPE`).
   - Must NOT carry an ambient session cookie (rejecting with HTTP 400 `AMBIGUOUS_CREDENTIALS`).
   - Must NOT carry a browser `Origin` header. If an `Origin` header is present (indicating browser execution attempting to spoof native mode), the request is rejected with HTTP 403 (`NATIVE_ORIGIN_PROHIBITED`).
   - Client markers alone are never proof of identity; credentials, account status, and rate limits still apply.

### 2.4 CSRF Secret Configuration & Deployment-Time Validation

Implemented in [apps/api/app/core/config.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/core/config.py) and [apps/api/app/core/csrf.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/core/csrf.py):

1. **Canonical Variable Aliasing:**
   - Configured with `AliasChoices("csrf_secret_key", "CSRF_SECRET", "CSRF_SECRET_KEY")`, matching `.env.example` line 25 and Technical Architecture Document Table 354.
2. **Deployment-Time Activation Validation:**
   - In production or staging environments (`app_env in ("production", "staging")`), `Settings` enforces via a Pydantic `@model_validator(mode="after")` that `CSRF_SECRET` must be explicitly configured whenever `auth_endpoints_enabled=True`. Startup fails fast with `ValueError` if missing.
   - When `auth_endpoints_enabled=False` (default pre-activation state), staging and production settings initialize cleanly without requiring secrets.
3. **Fail-Closed Runtime Guard:**
   - `get_csrf_signing_key(settings)` raises `RuntimeError("CSRF_SECRET_KEY_REQUIRED_IN_PRODUCTION")` if called in staging or production when auth is active without an explicit secret key, preventing any accidental fallback to database URL derivation.
   - In local development and testing, derives a deterministic HMAC key from `database_url` for zero-configuration developer convenience.

### 2.5 React Web Client API Integration

Updated in [apps/web/src/lib/api-client.ts](file:///Users/manish/Desktop/dormiostay/apps/web/src/lib/api-client.ts):

- **In-Memory Token Storage:** `storedCsrfToken` holds the active CSRF token in client memory; it is never persisted to `localStorage`, `sessionStorage`, or cookies.
- **Bootstrap API:** `bootstrapCsrf(signal?: AbortSignal): Promise<string>` fetches `/auth/csrf` and caches the token.
- **Token Clearing:** `clearCsrfToken(): void` flushes in-memory token state on logout or account change.
- **Automatic Write Header:** State-changing requests (`POST`, `PUT`, `PATCH`, `DELETE`) automatically fetch a fresh CSRF token before dispatching, attaching `X-CSRF-Token` with `credentials: 'include'`.
- **Zero Credential Exposure:** Raw server session tokens remain confined to the `HttpOnly` cookie; JavaScript only sees the opaque CSRF token string.

---

## 3. Test Evidence & Investigation Resolution

### 3.1 Automated Test Execution Summary

```text
============================= test session starts ==============================
platform darwin -- Python 3.12.16, pytest-9.0.2, pluggy-1.7.0
rootdir: /Users/manish/Desktop/dormiostay/apps/api
configfile: pyproject.toml
plugins: anyio-4.16.0

apps/api/tests/unit/test_t07_csrf.py: 23 passed in 0.95s
apps/api/tests/unit/test_t06_auth.py (isolated): 56 passed in 1.65s
Combined (test_t07_csrf.py + test_t06_auth.py): 79 passed in 2.21s
Combined (test_t06_auth.py + test_t07_csrf.py): 79 passed in 2.20s
apps/api/tests/unit/ (Full API Suite): 518 passed in 29.54s

apps/web (Vitest Suite): 84 passed in 1.39s
npm run format:check (Prettier): All matched files use Prettier code style!
================================================================================
```

### 3.2 Root Cause Analysis: Isolated T-06 Test Discrepancy

**Issue Reported:** During independent review, running `pytest tests/unit/test_t07_csrf.py tests/unit/test_t06_auth.py` produced 2 failures in `test_t06_auth.py`:
- `test_genuine_repository_update_session_activity[asyncio]`
- `test_genuine_repository_revoke_session[asyncio]`
However, both tests passed when `test_t06_auth.py` was executed in isolation, and the complete 515-test unit suite passed.

**Investigation Findings:**
1. **Pytest Collection Integrity:** Confirmed that all 56 tests in `test_t06_auth.py` were collected and executed in the full suite run.
2. **Execution Order Discrepancy:** In the full suite (`tests/unit/`), pytest executes files in alphabetical order. Because `test_t06_auth.py` precedes `test_t07_csrf.py`, `test_t06_auth.py` ran before any T-07 code was loaded.
3. **Root Cause Identified:** In `apps/api/tests/unit/test_t07_csrf.py`, the test setup helper `setup_csrf_test_app()` created `monkeypatch = pytest.MonkeyPatch()` and patched repository methods on `session_repo_mod` (`update_session_activity`, `revoke_session`) without registering a teardown cleanup fixture. When `test_t07_csrf.py` ran before `test_t06_auth.py`, those module-level patches remained in place globally, redirecting genuine SQLAlchemy repository calls to the T-07 in-memory test store (`InMemoryCsrfDb`), which failed on SQLite UUID queries.

**Resolution:**
- Added `_ACTIVE_MONKEYPATCHES: list[pytest.MonkeyPatch] = []` and an `autouse=True` teardown fixture `cleanup_csrf_monkeypatches()` in `test_t07_csrf.py`.
- Registered every monkeypatch instance to `_ACTIVE_MONKEYPATCHES` during `setup_csrf_test_app()`.
- The fixture executes `mp.undo()` after every test, strictly isolating T-07 mocks.
- **Verification:** Verified clean passes in both execution orders:
  - `pytest test_t07_csrf.py test_t06_auth.py` $\rightarrow$ **79 passed** (0 failures).
  - `pytest test_t06_auth.py test_t07_csrf.py` $\rightarrow$ **79 passed** (0 failures).
  - `pytest test_t06_auth.py` (isolated) $\rightarrow$ **56 passed** (0 failures).

### 3.3 T-07 23-Scenario Coverage Matrix

| # | Scenario | Test Function | Result |
|---|---|---|---|
| 1 | Valid Web CSRF token & correct Origin | `test_01_valid_web_csrf_token_and_correct_origin` | **PASS** (HTTP 200) |
| 2 | Missing CSRF token | `test_02_missing_csrf_token` | **PASS** (HTTP 403 `CSRF_TOKEN_MISSING`) |
| 3 | Incorrect / tampered CSRF token | `test_03_incorrect_csrf_token` | **PASS** (HTTP 403 `CSRF_TOKEN_INVALID`) |
| 4 | Expired CSRF token | `test_04_expired_csrf_token` | **PASS** (HTTP 403 `CSRF_TOKEN_EXPIRED`) |
| 5 | Foreign, null, or missing Origin | `test_05_foreign_or_null_origin` | **PASS** (HTTP 403 `FORBIDDEN_ORIGIN`) |
| 6 | Anonymous Web CSRF bootstrap (`GET /csrf`) | `test_06_anonymous_web_csrf_bootstrap` | **PASS** (HTTP 200, prefix `anon.`) |
| 7 | Token binding after login | `test_07_token_binding_after_login` | **PASS** (anon token fails on post-login; `sess.` token succeeds) |
| 8 | Session rotation and token invalidation | `test_08_session_rotation_and_token_invalidation` | **PASS** (re-login revokes old CSRF token) |
| 9 | Logout and revoked session | `test_09_logout_and_revoked_session` | **PASS** (HTTP 401 `SESSION_REVOKED`) |
| 10 | Valid native bearer write without CSRF | `test_10_valid_native_bearer_write_without_csrf` | **PASS** (HTTP 200, exempt from CSRF) |
| 11 | Invalid or expired native bearer | `test_11_invalid_or_expired_native_bearer` | **PASS** (HTTP 401 `SESSION_INVALID`/`SESSION_EXPIRED`) |
| 12 | Mixed cookie and bearer credentials | `test_12_mixed_cookie_and_bearer` | **PASS** (HTTP 400 `AMBIGUOUS_CREDENTIALS`) |
| 13 | Spoofed native client marker | `test_13_spoofed_native_client_marker` | **PASS** (HTTP 400 `AMBIGUOUS_CREDENTIALS`) |
| 14 | Native pre-auth carrying browser Origin | `test_14_native_preauth_request_with_browser_origin` | **PASS** (HTTP 403 `NATIVE_ORIGIN_PROHIBITED`) |
| 15 | Native pre-auth with invalid Content-Type | `test_15_native_preauth_request_with_invalid_content_type` | **PASS** (HTTP 415 `INVALID_CONTENT_TYPE`) |
| 16 | GET requests remaining safe & unaffected | `test_16_get_requests_remaining_unaffected` | **PASS** (HTTP 200 on `/me` and `/healthz`) |
| 17 | Browser fetch credential & CSRF integration | `test_17_browser_fetch_credential_and_csrf_integration` | **PASS** (full signup $\rightarrow$ login $\rightarrow$ csrf $\rightarrow$ logout flow) |
| 18 | No credential or token leakage in errors | `test_18_no_credential_or_token_leakage_in_errors` | **PASS** (no secrets, signatures, or hashes in error JSON) |
| 19 | T-06 regression with CSRF enabled | `test_19_regression_coverage_existing_t06_with_csrf` | **PASS** (native login $\rightarrow$ me $\rightarrow$ logout) |
| 20 | Activation guard default disabled | `test_20_activation_guard_disabled_by_default` | **PASS** (HTTP 503 `SERVICE_UNAVAILABLE` on `/csrf` and `/login`) |
| 21 | Deployment validation: CSRF secret required in prod/staging | `test_21_csrf_secret_required_in_production_when_auth_enabled` | **PASS** (fails fast on `ValueError` if missing) |
| 22 | Configuration: CSRF_SECRET alias resolution | `test_22_csrf_secret_alias_resolution` | **PASS** (resolves `CSRF_SECRET` from environment) |
| 23 | Runtime guard: signing key fails closed in prod/staging | `test_23_csrf_signing_key_fails_closed_in_production` | **PASS** (raises `RuntimeError` on invalid activation state) |

### 3.4 Static Analysis & Formatting Audit

- **Ruff Check:** `./apps/api/.venv/bin/ruff check app tests` $\rightarrow$ `All checks passed!` (0 lint errors).
- **Ruff Format:** `./apps/api/.venv/bin/ruff format --check app tests` $\rightarrow$ `43 files already formatted` (0 discrepancies).
- **Mypy Strict Check:** `(cd apps/api && .venv/bin/mypy app tests alembic)` $\rightarrow$ `Success: no issues found in 45 source files` (0 type errors).
- **Web Typecheck:** `npm run typecheck --workspace @dormiostay/web` $\rightarrow$ `tsc --noEmit` clean (0 type errors).
- **Web Linter:** `npm run lint --workspace @dormiostay/web` $\rightarrow$ `eslint . --max-warnings 0` clean (0 warnings).
- **Prettier Code Style:** `npm run format:check` $\rightarrow$ `All matched files use Prettier code style!` (100% formatted across repo).
- **Web Build:** `npm run build --workspace @dormiostay/web` $\rightarrow$ `dist/` built successfully in 162ms.
- **Git Whitespace:** `git diff --check` $\rightarrow$ Clean (0 whitespace anomalies).

---

## 4. Offline Verification Boundaries & PostgreSQL Integration Limitations

The following items are explicitly recorded as **NOT EXECUTED** in this local verification scope:

1. **No Live PostgreSQL Execution:** All tests executed against in-memory session mocks and SQLite adapters. Real PostgreSQL execution via `asyncpg` was not performed.
2. **No Rate Limiting Execution:** T-08 (Redis rate limiting) is deferred; auth endpoints are not rate limited yet.
3. **No Network / Cloud Mutation:** Zero external connections to Neon or Render; zero database migrations applied.

---

## 5. Remaining Ticket Dependencies

- **T-08 (Rate Limiting):** Distributed Upstash Redis rate limiting on `/login`, `/signup`, `/reset` is **NOT IMPLEMENTED**. Public endpoints must remain disabled (`auth_endpoints_enabled = False`) until T-08 is completed.
- **T-13 (Admin MFA):** Multi-factor authentication for Admin accounts is **NOT IMPLEMENTED**.
- **Native Client UI:** Expo React Native client screens and SecureStore integration are deferred to T-14M.

---

## 6. Upstream Protected Gate Boundaries

- **T-05 DevSecOps Baseline:**
  - Gate 1: Approved by Codex Astra.
  - Gate 2: Completed.
  - Gate 3: Completed.
  - Gate 4 (Sequence-Aware Table Grants): Sequence-aware live baseline **VERIFIED COMPLIANT**; independent Codex Astra review remains **PENDING**.
  - Gate 5 (Render Deployment): **NOT STARTED**.
- **Cloud State:** Zero cloud mutations, zero migrations, zero GRANT/REVOKE commands executed.

---

## 7. Proposed Commit Details

- **Commit Message:** `feat(security): implement CSRF and Origin protection across API and Web client`
- **Changed Files (11 total):**
  - `apps/api/app/api/deps.py`
  - `apps/api/app/api/v1/auth.py`
  - `apps/api/app/core/config.py`
  - `apps/api/app/core/csrf.py`
  - `apps/api/app/main.py`
  - `apps/api/app/schemas/auth.py`
  - `apps/api/tests/unit/test_t06_auth.py`
  - `apps/api/tests/unit/test_t07_csrf.py`
  - `apps/web/src/lib/api-client.test.tsx`
  - `apps/web/src/lib/api-client.ts`
  - `docs/T-07 Verification Report.md`

---

## 8. Verdict

```text
LOCAL IMPLEMENTATION VERIFIED — PROVISIONAL SECURITY IMPLEMENTATION;
INDEPENDENT SECURITY REVIEW, RATE LIMITING (T-08) AND POSTGRESQL INTEGRATION PENDING
```
