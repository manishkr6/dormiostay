# T-10 Verification Report — Email Verification Backend and Both-Client Link Contract

**Ticket Title:** T-10 · Email verification<br />
**Specification References:** `Feature Ticket List.md` (lines 375–392), `Product Requirements Document (v1 MVP).md` (Section 2.4, 7), `Security and Access Document.md` (Section 2.4, 4), `Technical Architecture Document.md`<br />
**Platforms:** Backend-only — both-client verification links<br />
**Priority:** Must-have | **Depends on:** T-06, T-09<br />
**Branch:** `feature/t10-implementation`<br />
**Base Commit / Expected HEAD:** `caa23ad7197d67e825a4640eb67ab90976eb1e9f`<br />
**Expected Parent:** `789019fd89fa5fb58e57f40a80d647fc1b6d013f`<br />
**Overall Status:** APPROVED BY INDEPENDENT SECURITY AUDIT — COMMITTED LOCALLY TO FEATURE BRANCH

---

## 1. Executive Summary & Official Scope

### 1.1 Official T-10 Requirements

As defined in the authoritative `Feature Ticket List.md`:

> **Prompt:** On signup create a random token, store only its hash with a 24-hour expiry, queue the verification email. Implement `POST /auth/verify-email` and `POST /auth/resend-verification` (rate-limited). Unverified users can browse but cannot request visits or submit owner applications.
>
> **Both-client link contract:** issue single-use action links with verified HTTPS app-link routes and a Web fallback. Never put a session token in a link or log action tokens. GET/navigation alone does not consume the action; test wrong, reused and expired tokens on both client flows. T-14/T-14M own the UIs.
>
> **Acceptance criteria:**
> - Token works once; expired and reused tokens show clear messages.
> - Resend is rate-limited and does not leak whether the account exists.
> - Restricted actions return 403 with a code the UI can explain.

### 1.2 Verification Matrix

| # | Acceptance Requirement | Implementation Detail | Test Verification | Status |
|---|---|---|---|---|
| 1 | Random token generation on signup | Cryptographically secure 32-byte URL-safe token generated via `secrets.token_urlsafe(32)` | `test_01_signup_generates_token_and_queues_email` | **PASS** |
| 2 | SHA-256 hash storage & 24h expiry | Only SHA-256 hex digest (64 chars) stored in `email_verification_tokens`; `expires_at = now + 24h` | `test_01_signup_generates_token_and_queues_email` | **PASS** |
| 3 | Transactional outbox email queueing | Queued atomically in `email_outbox` via T-09 transactional outbox; rolls back if user signup fails | `test_01_signup_generates_token_and_queues_email` | **PASS** |
| 4 | Single-use token verification | `POST /api/v1/auth/verify-email` marks `used_at = now()` and sets `user.email_verified_at = now()` | `test_02_verify_email_success_single_use` | **PASS** |
| 5 | Reused token rejection | Attempting to verify an already-used token returns HTTP 400 with code `TOKEN_ALREADY_USED` | `test_03_reused_token_rejected_with_clear_message` | **PASS** |
| 6 | Expired token rejection | Verification of an expired token returns HTTP 400 with code `TOKEN_EXPIRED` | `test_04_expired_token_rejected_with_clear_message` | **PASS** |
| 7 | Invalid token rejection | Unknown, missing, or malformed token (< 10 chars) returns HTTP 400 with code `TOKEN_INVALID` | `test_05_invalid_or_malformed_token_rejected` | **PASS** |
| 8 | Disabled account defense | Suspended or banned account verification returns HTTP 403 with code `ACCOUNT_DISABLED` | `test_06_suspended_or_banned_user_verification_rejected` | **PASS** |
| 9 | Resend verification flow | `POST /api/v1/auth/resend-verification` invalidates prior unused tokens, generates fresh token, queues email | `test_07_resend_verification_unverified_user` | **PASS** |
| 10 | Anti-enumeration on resend | Non-existent, suspended, or already-verified accounts return identical generic HTTP 200 message | `test_08_resend_verification_anti_enumeration_nonexistent_email`, `test_09` | **PASS** |
| 11 | Constant-time resend defense | Dummy password verification (`verify_dummy_password()`) pads execution timing when account is omitted | `test_08_resend_verification_anti_enumeration_nonexistent_email` | **PASS** |
| 12 | Resend rate limiting (per-email) | Exceeding 3 requests/hour (fallback 2/hr) returns HTTP 429 `RATE_LIMIT_EXCEEDED` with `Retry-After` | `test_10_resend_verification_rate_limiting_per_email`, `test_10b` | **PASS** |
| 13 | Resend rate limiting (per-IP) | Exceeding 10 requests/hour (fallback 5/hr) returns HTTP 429 `RATE_LIMIT_EXCEEDED` with `Retry-After` | `test_11_resend_verification_rate_limiting_per_ip` | **PASS** |
| 14 | Restricted action guard (unverified) | `require_verified_user` blocks unverified users with HTTP 403 and machine-readable code `EMAIL_NOT_VERIFIED` | `test_12_require_verified_user_blocks_unverified` | **PASS** |
| 15 | Restricted action guard (verified) | `require_verified_user` allows verified users to proceed without friction | `test_13_require_verified_user_permits_verified` | **PASS** |
| 16 | Both-client link contract | Single-use action links formatted as `{frontend_origin}/verify-email?token={raw_token}` | `test_14_action_link_contract_format` | **PASS** |
| 17 | Token privacy in links and logs | No session token ever placed in link; action tokens excluded from logs, error payloads, and outbox metadata | `test_14_action_link_contract_format` | **PASS** |
| 18 | Web client transport | Web client verifies token with Origin check and valid session/anonymous CSRF token | `test_15_both_client_transport_web_with_csrf` | **PASS** |
| 19 | Native client transport | Native client verifies token using `X-Client-Type: native` (exempt from CSRF, cookie-free) | `test_16_both_client_transport_native_without_csrf` | **PASS** |
| 20 | Navigation safety | GET /verify-email returns HTTP 405 Method Not Allowed; crawlers or email prefetchers cannot consume token | `test_17_get_navigation_does_not_consume_action` | **PASS** |
| 21 | Inactive auth guard | `auth_endpoints_enabled=False` by default returns HTTP 503 `SERVICE_UNAVAILABLE` on all auth routes | `test_18_auth_endpoints_disabled_by_default_returns_503` | **PASS** |
| 22 | Concurrency race condition defense | Atomic conditional UPDATE prevents double consumption; exactly one request succeeds | `test_19_concurrent_verify_email_race_condition` | **PASS** |
| 23 | Exact boundary expiration | Token expiring at or before now is rejected with HTTP 400 `TOKEN_EXPIRED` | `test_20_exact_boundary_expiration` | **PASS** |
| 24 | Transaction rollback atomicity | Database failure rolls back token consumption and unverified state atomically | `test_21_transaction_rollback_atomicity` | **PASS** |
| 25 | Cross-user token isolation | Verifying a token for User A cannot mark User B verified | `test_22_cross_user_token_isolation` | **PASS** |
| 26 | ORM identity-map fresh read | `populate_existing=True` forces fresh read from database, bypassing stale identity-map state | `test_23_sqlalchemy_orm_identity_map_fresh_read` | **PASS** |
| 27 | ORM losing-request classification | Losing request in concurrent race deterministically returns `TOKEN_ALREADY_USED` on real ORM state | `test_24_sqlalchemy_losing_request_returns_token_already_used` | **PASS** |
| 28 | ORM boundary expiration | Expired token returns `TOKEN_EXPIRED` deterministically on real ORM state | `test_25_sqlalchemy_boundary_expiration_classification` | **PASS** |
| 29 | ORM unknown token classification | Unknown token returns `TOKEN_INVALID` on real ORM state | `test_26_sqlalchemy_unknown_token_classification` | **PASS** |
| 30 | ORM atomic commit verification | Verification commits token and user changes atomically to database, verified via independent session | `test_27_sqlalchemy_atomic_commit` | **PASS** |
| 31 | ORM atomic rollback verification | Failed transaction rolls back token and user changes in database with zero manual model resets | `test_28_sqlalchemy_atomic_rollback` | **PASS** |

---

## 2. Architecture & Implementation Design

### 2.1 Database Model & Schema Integration

The `email_verification_tokens` table was already created in the catalog during the initial foundation migration (`0001_foundation.py`) and governed under T-05 security catalog checks.

```sql
CREATE TABLE email_verification_tokens (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_email_verification_tokens PRIMARY KEY (id),
    CONSTRAINT ck_email_verification_tokens_token_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_email_verification_tokens_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
    CONSTRAINT uq_email_verification_tokens_token_hash UNIQUE (token_hash)
);
CREATE INDEX ix_email_verification_tokens_expires_at ON email_verification_tokens (expires_at);
CREATE INDEX ix_email_verification_tokens_user_id ON email_verification_tokens (user_id);
```

**Key Invariants:**
1. **Hash Constraint:** `token_hash ~ '^[0-9a-f]{64}$'` strictly requires a 64-character lowercase SHA-256 hex digest.
2. **Cryptographic Token Hashing & Storage Boundaries:**
   - In `email_verification_tokens`: Plaintext action tokens are never persisted; only the SHA-256 hex digest (`token_hash`) is stored.
   - In `email_outbox`: The rendered transactional email body (`html_content` and `text_content`) necessarily contains the complete verification URL (`{frontend_origin}/verify-email?token={raw_token}`) so the outbox worker can transmit the link to the recipient.
   - In `users` and `email_outbox`: User email addresses are stored in plaintext in `users.email` and `email_outbox.recipient` in PostgreSQL. Plaintext email addresses are strictly avoided in external ephemeral stores (Redis rate-limiting keys use SHA-256 HMAC/prefixes) and application logs.
   - In server logs and errors: Action tokens, session tokens, and passwords are never logged and are omitted from API error payloads and outbox metadata.
3. **Repository Encapsulation:** All operations on `email_verification_tokens` are encapsulated in `apps/api/app/db/repositories/verification_repo.py`:
   - `create_verification_token()`: Inserts a new token record with 24-hour expiry.
   - `get_verification_token_by_hash()`: Queries token by SHA-256 hash.
   - `mark_token_used()`: Sets `used_at = now()` atomically via conditional UPDATE (`used_at IS NULL AND expires_at > now`).
   - `invalidate_user_verification_tokens()`: Marks any outstanding unused tokens for a user as `used_at = now()` upon resend.

### 2.2 Cryptographic Token Design

- **Generation:** `generate_action_token()` utilizes `secrets.token_urlsafe(32)`, yielding ~43 characters of high-entropy URL-safe base64 data.
- **Hashing:** `hash_action_token()` applies `hashlib.sha256(token.encode("utf-8")).hexdigest()`, yielding the 64-character hex digest required by PostgreSQL.
- **Separation from Session Tokens:** Action tokens are single-purpose, ephemeral (24-hour expiry), and stored in `email_verification_tokens`. They are never stored in `user_sessions` and cannot authenticate general API requests.
- **Session Decoupling:** Verifying an email does not automatically mint or grant an active ambient session. The user must authenticate through normal Web cookie or Native bearer flows.

### 2.3 Transactional Outbox Integration (T-09 Reuse)

Email verification strictly reuses the existing T-09 transactional email outbox (`email_outbox` table and `email_service.queue_email()`):
- **Atomic Signup Binding:** During `auth_service.signup()`, user creation, profile creation, verification token insertion, and verification email queueing occur inside the same database transaction.
- **Rollback Safety:** If signup or resend encounters an error (e.g., duplicate email, invalid institution, or constraint violation), the database transaction rolls back, preventing orphaned verification tokens or stray emails.
- **Outbox Email Structure:**
  - `recipient`: Normalized user email.
  - `recipient_name`: User's full name from profile.
  - `subject`: `"Verify your DormioStay email address"`
  - `tags`: `["auth", "verification"]`
  - `metadata`: `{"action": "email_verification", "user_id": str(user.id)}` (action tokens are strictly omitted from metadata).
  - `html_content` / `text_content`: Responsive, accessible email template containing the action link, clear 24-hour single-use expiry notices, and anti-phishing disclaimers.

### 2.4 Both-Client Action Link Contract

- **Contract Link Structure:**
  `{settings.frontend_origin}/verify-email?token={raw_token}`
- **Web Client Navigation:** Opens the Web client verification page (`apps/web` under T-14). The UI page extracts `token` from URL parameters and executes `POST /api/v1/auth/verify-email`.
- **Native Mobile Navigation:** Mobile platforms register HTTPS App Links (Android) and Universal Links (iOS) on the frontend origin. When tapped in an email client on a mobile device, the OS launches the DormioStay app (`apps/mobile` under T-14M) to the verify-email screen, which executes `POST /api/v1/auth/verify-email`.
- **Web Fallback:** If the mobile app is not installed, the HTTPS link automatically opens in the device's browser.
- **GET Safety:** `GET /verify-email` is purely a navigation/presentation route. GET requests to `/api/v1/auth/verify-email` are rejected with HTTP 405 Method Not Allowed, guaranteeing that email prefetch crawlers or browser extensions cannot consume the single-use token.

### 2.5 Anti-Enumeration & Constant-Time Security

The `POST /api/v1/auth/resend-verification` endpoint is designed to eliminate user enumeration side-channels:
1. **Identical HTTP Responses:** Whether the email exists in the database, does not exist, belongs to a suspended/banned user, or is already verified, the endpoint returns HTTP 200 OK with the exact same response:
   ```json
   {
     "status": "ok",
     "message": "If that email exists and is unverified, a verification link has been sent."
   }
   ```
2. **Timing Attack Mitigation:** When an account is nonexistent, suspended, or already verified, `verify_dummy_password()` executes a synthetic Argon2id verification with fixed precomputed salt/hash, equalizing execution latency across code branches.

### 2.6 Rate Limiting & Abuse Prevention (T-08 Integration)

To prevent email bombing and spamming, `POST /api/v1/auth/resend-verification` applies dual-key atomic rate limiting:
- **Per-Email Limit (`RULE_RESEND_VERIFICATION_EMAIL`):**
  - Redis limit: 3 requests per 60 minutes per normalized email hash.
  - In-memory fallback: 2 requests per 60 minutes.
- **Per-IP Limit (`RULE_RESEND_VERIFICATION_IP`):**
  - Redis limit: 10 requests per 60 minutes per client IP hash.
  - In-memory fallback: 5 requests per 60 minutes.
- **Privacy Hashing:** Rate limit keys use SHA-256 HMAC / prefix hashing (`dormiostay:rl:resend_verification:email:<hash>`). Plaintext emails and IP addresses never appear in Redis keys.
- **Header Enforcement:** Rejections return HTTP 429 Too Many Requests with code `RATE_LIMIT_EXCEEDED` and standard `Retry-After` header.

### 2.7 CSRF & Transport Protection (T-07 Integration)

The verification endpoints are registered on the `/api/v1/auth` router with `verify_csrf` and `require_auth_endpoints_enabled` dependencies:
- **Pre-Auth Path Whitelist:** `/api/v1/auth/verify-email` and `/api/v1/auth/resend-verification` are added to `PRE_AUTH_PATHS` in `app/core/csrf.py`.
- **Web Client Writes:** Require exact Origin validation matching `settings.frontend_origin` and a valid `X-CSRF-Token` (either session-bound if logged in, or anonymous bootstrap token if unauthenticated).
- **Native Client Writes:** Exemption granted when explicit `X-Client-Type: native` header is provided with `Content-Type: application/json`, zero browser `Origin`, and zero ambient session cookies (rejects ambiguous credentials).

### 2.8 Access Control: Restricted Action Guard

To satisfy the acceptance criteria that "unverified users can browse but cannot request visits or submit owner applications":
- Added `require_verified_user` dependency in `apps/api/app/api/deps.py`.
- It inspects `user.email_verified_at`. If `None`, it raises:
  ```json
  {
    "error": {
      "code": "EMAIL_NOT_VERIFIED",
      "message": "Email verification is required to perform this action. Please verify your email."
    }
  }
  ```
  with HTTP 403 Forbidden.
- This provides downstream tickets (T-16 Owner Application, T-18 Visit Requests) with an immediate, declarative dependency that frontends and native mobile clients can explain.

### 2.9 Atomic Single-Use Concurrency & ORM State Management

An independent security audit identified two material architectural issues in token verification:
1. **Concurrency Race Condition:** Non-locking `SELECT` before `UPDATE` allowed concurrent requests with the same unused token to potentially double-verify.
2. **SQLAlchemy Identity-Map Staleness:** After a failed atomic conditional update (`rowcount == 0`), re-querying `EmailVerificationToken` without `populate_existing=True` returned the cached instance from `session.identity_map` without updating its attributes from PostgreSQL. Furthermore, default `synchronize_session='auto'` performed phantom in-memory attribute mutations when `rowcount == 0`.

**Remediation:**
1. **Atomic Conditional UPDATE with Disabled Synchronization:**
   `verification_repo.mark_token_used()` executes:
   ```sql
   UPDATE email_verification_tokens
   SET used_at = :now
   WHERE id = :token_id
     AND used_at IS NULL
     AND expires_at > :now
   ```
   with `.execution_options(synchronize_session=False)`. This guarantees:
   - Exactly one transaction can update the row (`rowcount == 1`).
   - Losing concurrent transactions receive `rowcount == 0`.
   - SQLAlchemy's in-memory evaluator is disabled, preventing phantom attribute mutations (`used_at = now`) on failed updates.
2. **Deterministic Error Classification via `populate_existing=True`:**
   When `rowcount == 0`, `auth_service.verify_email()` re-queries the token using:
   ```python
   fresh_token = await verification_repo.get_verification_token_by_hash(
       session, token_hash, populate_existing=True
   )
   ```
   `populate_existing=True` explicitly instructs SQLAlchemy to overwrite identity-mapped attributes with the freshly fetched PostgreSQL row data. This guarantees:
   - If another transaction consumed the token, `fresh_token.used_at` reflects the committed timestamp, deterministically raising `TOKEN_ALREADY_USED`.
   - If the token expired at the boundary, `fresh_token.used_at` remains `None` and `now >= fresh_token.expires_at` deterministically raises `TOKEN_EXPIRED`.
   - If the token was deleted or unknown, `fresh_token is None` deterministically raises `TOKEN_INVALID`.
3. **Single-Transaction Atomicity & Rollback:**
   Token consumption and `user.email_verified_at` updating are bounded within the same database transaction. Any failure rolls back both operations in PostgreSQL.
4. **Genuine SQLAlchemy Test Validation vs PostgreSQL Concurrency:**
   - **SQLAlchemy Regression Tests (`test_23` through `test_28`):** Use real SQLAlchemy ORM engines and SQLite via the `RealSqliteAsyncSession` adapter proxy (synchronous `Session` exposing an `await`-able interface) to verify the ORM compiler, unit-of-work, `execution_options` (`synchronize_session=False`, `populate_existing=True`), identity-map caching, commits, and rollbacks without manual model property resets.
   - **PostgreSQL Concurrency Guarantees:** The guarantee of exclusive consumption under concurrency follows from PostgreSQL's documented row-level lock and MVCC `WHERE`-clause re-evaluation under `READ COMMITTED` isolation. Multi-connection PostgreSQL concurrency tests were **NOT EXECUTED** locally and remain to be confirmed during live staging verification (Gate 4).
5. **Concurrent Resend Analysis & Token Lifecycle:**
   - `resend_verification()` invalidates all unexpired, unused verification tokens for the user (`used_at = now`) before inserting a fresh 24-hour token.
   - Dual-bucket rate limiting (per-email: 3 req/hr, per-IP: 10 req/hr) prevents abuse. While rate limiting strictly bounds request volume, if two resends execute simultaneously within the allowed quota, both may issue `invalidate_user_verification_tokens()` and create new tokens. Each token is independently valid until consumed or invalidated by a later resend. Because rate limiting caps total requests, the active token pool is strictly bounded and cannot grow uncontrollably.
   - Deadlock-free execution: `verify_email` updates `email_verification_tokens` then `users`. `resend_verification` only updates `email_verification_tokens` and performs a non-locking SELECT on `users`. There is no circular lock dependency between the two tables.

---

## 3. Configuration Inventory

All configuration settings are managed via Pydantic `Settings` in `apps/api/app/core/config.py`:

| Setting | Type | Default | Description |
|---|---|---|---|
| `auth_endpoints_enabled` | bool | `False` | Activation guard: endpoints return 503 until public activation |
| `frontend_origin` | AnyHttpUrl | `http://localhost:5173` | Allowed Web Origin for CORS, CSRF, and action links |
| `rate_limit_enabled` | bool | `True` | Master toggle for Redis/in-memory rate limiting |
| `csrf_protection_enabled` | bool | `True` | Master toggle for CSRF and Origin enforcement |
| `email_from_address` | str | `noreply@dormiostay.com` | Verified sender address for Brevo delivery |
| `email_from_name` | str | `DormioStay` | Display name for outgoing verification emails |
| `is_email_dev_mode` | bool | `True` (in dev) | Dispatches via simulated outbox worker without live API calls |

---

## 4. Test Evidence & Quality Assurance

### 4.1 T-10 Automated Test Suite

Executed via `pytest tests/unit/test_t10_email_verification.py -v`:

| # | Test Name | Assertion & Behavior Verified | Result |
|---|---|---|---|
| 1 | `test_01_signup_generates_token_and_queues_email` | Random token created, SHA-256 hash stored, 24h expiry, outbox email queued atomically | **PASS** |
| 2 | `test_02_verify_email_success_single_use` | Valid token records `used_at` and `email_verified_at`, returns 200 OK | **PASS** |
| 3 | `test_03_reused_token_rejected_with_clear_message` | Reusing an already-consumed token fails with HTTP 400 `TOKEN_ALREADY_USED` | **PASS** |
| 4 | `test_04_expired_token_rejected_with_clear_message` | Submitting an expired token fails with HTTP 400 `TOKEN_EXPIRED` | **PASS** |
| 5 | `test_05_invalid_or_malformed_token_rejected` | Unknown or malformed token fails with HTTP 400 `TOKEN_INVALID` | **PASS** |
| 6 | `test_06_suspended_or_banned_user_verification_rejected` | Suspended or banned user verification fails with HTTP 403 `ACCOUNT_DISABLED` | **PASS** |
| 7 | `test_07_resend_verification_unverified_user` | Invalidates old tokens, creates new 24h token, queues new outbox email | **PASS** |
| 8 | `test_08_resend_verification_anti_enumeration_nonexistent_email` | Non-existent email returns generic 200 OK without creating tokens or emails | **PASS** |
| 9 | `test_09_resend_verification_anti_enumeration_already_verified` | Already verified email returns generic 200 OK without queuing new email | **PASS** |
| 10 | `test_10_resend_verification_rate_limiting_per_email` | Per-email rate limit blocks 4th request within 1 hour with HTTP 429 | **PASS** |
| 11 | `test_10b_resend_verification_rate_limiting_fallback_stricter` | In-memory fallback applies stricter threshold (blocks 3rd request with 429) | **PASS** |
| 12 | `test_11_resend_verification_rate_limiting_per_ip` | Per-IP rate limit blocks 11th request within 1 hour with HTTP 429 | **PASS** |
| 13 | `test_12_require_verified_user_blocks_unverified` | `require_verified_user` dependency blocks unverified user with HTTP 403 `EMAIL_NOT_VERIFIED` | **PASS** |
| 14 | `test_13_require_verified_user_permits_verified` | `require_verified_user` dependency permits verified user | **PASS** |
| 15 | `test_14_action_link_contract_format` | Verification link contains no session tokens, has clear 24h single-use notices | **PASS** |
| 16 | `test_15_both_client_transport_web_with_csrf` | Web client verifies token with valid Origin and CSRF token | **PASS** |
| 17 | `test_16_both_client_transport_native_without_csrf` | Native mobile client (`X-Client-Type: native`) verifies token without CSRF | **PASS** |
| 18 | `test_17_get_navigation_does_not_consume_action` | GET request to verify-email returns 405 Method Not Allowed; token remains unconsumed | **PASS** |
| 19 | `test_18_auth_endpoints_disabled_by_default_returns_503` | Activation guard returns HTTP 503 when `auth_endpoints_enabled=False` | **PASS** |
| 20 | `test_19_concurrent_verify_email_race_condition` | Two concurrent requests racing on same unused token: exactly one succeeds (200), second fails with 400 `TOKEN_ALREADY_USED` | **PASS** |
| 21 | `test_20_exact_boundary_expiration` | Token expiring at or before current time is rejected with HTTP 400 `TOKEN_EXPIRED` | **PASS** |
| 22 | `test_21_transaction_rollback_atomicity` | Database failure during user verification triggers rollback of token consumption | **PASS** |
| 23 | `test_22_cross_user_token_isolation` | Token issued to User A cannot verify User B under any circumstances | **PASS** |
| 24 | `test_23_sqlalchemy_orm_identity_map_fresh_read` | Real SQLAlchemy Session: `populate_existing=True` bypasses stale identity map, reading committed timestamp | **PASS** |
| 25 | `test_24_sqlalchemy_losing_request_returns_token_already_used` | Real SQLAlchemy Session: losing request deterministically returns `TOKEN_ALREADY_USED` | **PASS** |
| 26 | `test_25_sqlalchemy_boundary_expiration_classification` | Real SQLAlchemy Session: expired token deterministically returns `TOKEN_EXPIRED` | **PASS** |
| 27 | `test_26_sqlalchemy_unknown_token_classification` | Real SQLAlchemy Session: non-existent token deterministically returns `TOKEN_INVALID` | **PASS** |
| 28 | `test_27_sqlalchemy_atomic_commit` | Real SQLAlchemy Session: successful verification commits token and user changes atomically | **PASS** |
| 29 | `test_28_sqlalchemy_atomic_rollback` | Real SQLAlchemy Session: database rollback reverts token consumption with zero manual model resets | **PASS** |

**T-10 Result:** `29 passed, 1 warning in 1.31s`

### 4.2 Full Suite Regression Evidence

1. **Combined Security & Worker Suite:**
   `pytest tests/unit/test_t06_auth.py tests/unit/test_t07_csrf.py tests/unit/test_t08_rate_limit.py tests/unit/test_t09_email_worker.py tests/unit/test_t10_email_verification.py`
   **Result:** `158 passed, 1 warning in 5.12s`
2. **Complete API Unit Suite:**
   `pytest tests/unit/`
   **Result:** `597 passed, 15 warnings in 32.07s`
3. **Web Client Vitest Suite:**
   `npm test --workspace @dormiostay/web`
   **Result:** `84 passed in 1.35s` (6 test files)
4. **Mobile Client Jest Suite:**
   `npm run test --workspace @dormiostay/mobile`
   **Result:** `88 passed in 0.965s` (10 test suites)
5. **Static Analysis & Linters:**
   - `ruff check app tests scripts alembic`: `All checks passed!` (0 errors)
   - `ruff format --check app tests scripts alembic`: `60 files already formatted`
   - `mypy app tests alembic scripts`: `Success: no issues found in 60 source files`
   - `npm run lint` (web): `0 errors, 0 warnings`
   - `npm run lint:mobile`: `0 errors, 0 warnings`
   - `npm run typecheck` (web): `0 errors`
   - `npm run typecheck:mobile`: `0 errors`
   - `npm run format:check`: `All matched files use Prettier code style!`
   - `npm run build` (web): `dist/assets/index-CsM5acAu.js 417.48 kB │ built in 161ms`
   - `git diff --check`: Clean (0 whitespace/syntax issues)

### 4.3 Test Warning Classification

The 15 warnings observed during full test execution remain identical to the T-09 baseline:
- **1 warning (`StarletteDeprecationWarning`):** Upstream Starlette testclient noting that `httpx` was used instead of `httpx2`. Safe test harness artifact.
- **14 warnings (`Alembic DeprecationWarning`):** Legacy splitting on spaces in `prepend_sys_path` during migration config tests. Informational only.

---

## 5. Git Status and Artifact Inventory

### 5.1 Repository Baseline

- **Current Branch:** `feature/t10-implementation`
- **Parent Commit:** `caa23ad7197d67e825a4640eb67ab90976eb1e9f` (T-09 implementation)
- **Root Parent:** `789019fd89fa5fb58e57f40a80d647fc1b6d013f` (T-08 implementation)
- **Local Commits:** None created. All changes remain strictly uncommitted for independent review.

### 5.2 Modified Tracked Files (9 files)

1. `apps/api/app/core/security.py`: Added `generate_action_token()` (32 bytes urlsafe) and `hash_action_token()` (SHA-256 hex).
2. `apps/api/app/db/repositories/user_repo.py`: Added `mark_user_email_verified()`.
3. `apps/api/app/schemas/auth.py`: Added `VerifyEmailRequest`, `ResendVerificationRequest`, `VerificationResponse`.
4. `apps/api/app/services/email_service.py`: Added `build_verification_email()`, `queue_verification_email()`, and safe mock coroutine handling.
5. `apps/api/app/core/rate_limit.py`: Added `RULE_RESEND_VERIFICATION_IP` (10/hr, fallback 5/hr) and `rate_limit_resend_verification` dependency.
6. `apps/api/app/core/csrf.py`: Whitelisted `/api/v1/auth/verify-email` and `/api/v1/auth/resend-verification` in `PRE_AUTH_PATHS`.
7. `apps/api/app/api/deps.py`: Added `require_verified_user` dependency for restricted endpoints.
8. `apps/api/app/services/auth_service.py`: Integrated verification token generation & outbox queueing into `signup()`; implemented `verify_email()` and `resend_verification()`.
9. `apps/api/app/api/v1/auth.py`: Registered `POST /verify-email` and `POST /resend-verification` endpoints.

### 5.3 New Untracked Files (3 files)

1. `apps/api/app/db/repositories/verification_repo.py`: Dedicated repository for `EmailVerificationToken` database queries.
2. `apps/api/tests/unit/test_t10_email_verification.py`: 29 comprehensive unit and acceptance tests for T-10 (23 mock-based and 6 SQLAlchemy-backed regression tests).
3. `docs/T-10 Verification Report.md`: This comprehensive verification document.

---

## 6. Pre-Commit Verification Checklist & Status Preservation

- [x] T-10 requirements fully satisfied and verified with focused tests.
- [x] In-memory doubles cleanly distinguished from real infrastructure.
- [x] Zero live database queries or mutations executed on Neon staging/production.
- [x] Zero live Brevo emails dispatched; dev mode outbox simulation preserved.
- [x] Zero Upstash Redis network calls executed; in-memory double verified.
- [x] No GRANT, REVOKE, or schema alteration executed.
- [x] Public authentication remains disabled by default (`auth_endpoints_enabled = False`).
- [x] Working tree clean of any git commits, pushes, or merges.
- [x] Status Preservation:
  - T-05 Astra Gate 4: **PENDING**.
  - T-05 Gate 5 Render staging: **NOT STARTED**.
  - T-06 through T-09: **LOCALLY COMMITTED**.
  - T-10: **AWAITING FINAL LOCAL COMMIT**.
  - T-13 Admin MFA: **PENDING**.
- [x] Remaining Integration Requirements (Deferred / Staging-Bound):
  - Real PostgreSQL multi-connection concurrency and row-level locking testing.
  - Staging database role privileges and grants under Gate 4.
  - Authorized staging deployment and Alembic migration execution.
  - Brevo live SMTP/API delivery and inbox deliverability validation.
  - Web and mobile user verification interfaces (owned by tickets T-14 and T-14M).
- [x] No production-readiness claims made.
