# T-11 Verification Report — Password Reset Backend and Both-Client Link Contract

**Ticket Title:** T-11 · Password reset<br />
**Specification References:** `Feature Ticket List.md` (lines 227–242), `Product Requirements Document (v1 MVP).md` (Section 2.4, 7), `Security and Access Document.md` (Section 2.4, 4), `Technical Architecture Document.md`<br />
**Platforms:** Backend-only — both-client reset links<br />
**Priority:** Must-have | **Depends on:** T-06, T-09<br />
**Branch:** `feature/t11-implementation`<br />
**Base Commit / Expected HEAD:** `7ffc59cce71895856f895d24e226c6a65ec04ba7`<br />
**Expected Parent:** `caa23ad7197d67e825a4640eb67ab90976eb1e9f`<br />
**Canonical T-08 Commit:** `789019fd89fa5fb58e57f40a80d647fc1b6d013f`<br />
**Canonical T-05 Commit:** `b5ceae221d8800d8b22937ef187274946fcfda7a`<br />
**Overall Status:** REMEDIATED & READY FOR INDEPENDENT SECURITY RE-AUDIT — UNCOMMITTED ON FEATURE BRANCH

---

## 1. Executive Summary & Official Scope

### 1.1 Official T-11 Requirements

As defined in the authoritative `Feature Ticket List.md` (lines 227–242):

> **Prompt:** Implement `POST /auth/forgot-password` (always responds 'If that email exists, we sent a link') and `POST /auth/reset-password` (random single-use hashed token, 30-minute expiry). A successful reset revokes every session of that user.
>
> **Both-client link contract:** issue single-use action links with verified HTTPS app-link routes and a Web fallback. Never put a session token in a link or log action tokens. GET/navigation alone does not consume the action; test wrong, reused and expired tokens on both client flows. T-14/T-14M own the UIs.
>
> **Acceptance criteria:**
> - Identical response and similar timing for known and unknown emails.
> - Old sessions fail after reset.
> - Token reuse and expiry are rejected.

### 1.2 Independent Audit Findings & Verified Remediations

Gemini 4 Hydrogen performed an independent READ-ONLY security audit of the initial T-11 implementation and identified five specific findings, which have all been remediated:

1. **HIGH — Concurrent Login & Password Reset Race Condition:**
   - *Audit Finding:* A login request verifying an old password with Argon2id (~60-90ms) could resume after a concurrent password reset had revoked active sessions, inserting a brand-new unrevoked session in `user_sessions` and committing via `update_login_success`.
   - *Remediation:* `user_repo.update_login_success` now accepts `expected_password_hash` and conditionally updates `users` only if `User.password_hash == expected_password_hash`. In `auth_service.login`, `update_login_success` is executed *before* `session_repo.create_session`, locking the `users` row. If the password was changed concurrently by a reset, `rowcount == 0`, returning `False`. `login` immediately raises HTTP 401 `AUTHENTICATION_FAILED`, rolling back the transaction with zero sessions created in `user_sessions`.
2. **MEDIUM — Failure to Invalidate Outstanding Reset Tokens on Reset:**
   - *Audit Finding:* `auth_service.reset_password` only marked the submitted token used, leaving other unexpired reset tokens for that user usable.
   - *Remediation:* `reset_password` now acquires the user row lock (`get_user_by_id(..., for_update=True)`) and invokes `password_reset_repo.invalidate_user_reset_tokens(session, user.id, now)` inside the reset transaction. This serializes all resets for a user, invalidates all prior and parallel reset tokens, and causes any racing reset with a different token to deterministically fail with `TOKEN_ALREADY_USED`.
3. **MEDIUM — Inverted Forgot-Password Timing Leak:**
   - *Audit Finding:* Non-existent accounts executed `verify_dummy_password()` (~75ms Argon2id computation), while existing active accounts executed only fast database queries (~3-5ms), creating an inverted ~15x timing discrepancy.
   - *Remediation:* Removed `verify_dummy_password()` from `forgot_password`. Equalized token generation and hashing overhead across all paths (`generate_action_token()` + `hash_action_token()`). All account paths now execute within ~2-5ms locally.
4. **LOW — Ruff Formatting Failure:**
   - *Audit Finding:* `app/core/rate_limit.py` failed `ruff format --check`.
   - *Remediation:* Ran `ruff format` across `app/` and `tests/`. All 62 files now pass `ruff format --check` and `ruff check`.
5. **LOW — Verification Report Documentation Inaccuracies:**
   - *Audit Finding:* Line references cited T-16 lines instead of T-11 lines 227–242; commit lineage conflated T-05 with T-08; mock-based concurrency was not explicitly distinguished from real ORM tests; token/email storage realities needed clarification.
   - *Remediation:* All citations, commit references, test classifications, and storage boundaries have been fully corrected in this report.
6. **HIGH — Session Rotation Lock-Order Inversion (Deadlock Elimination):**
   - *Audit Finding:* In `auth_service.login()`, when an ambient web session cookie was presented, `session_repo.revoke_session` locked `user_sessions` before `user_repo.update_login_success` locked `users`. Meanwhile, `reset_password` locked `users` before `user_sessions`. Under concurrent execution, this created a PostgreSQL circular wait resulting in error `40P01` (deadlock detected).
   - *Remediation:* In `auth_service.login()`, `user_repo.update_login_success` is now executed *before* `session_repo.revoke_session`. This establishes a consistent global lock hierarchy across all authentication flows: `users` -> `password_reset_tokens` -> `user_sessions`. A circular wait between `users` and `user_sessions` is mathematically impossible under Coffman's conditions.

### 1.3 Verification Matrix

| # | Acceptance Requirement | Implementation Detail | Test Verification | Status |
|---|---|---|---|---|
| 1 | Anti-enumeration generic response | `POST /auth/forgot-password` returns `"If that email exists, we sent a link."` for existing, non-existent, unverified, suspended, and banned emails | `test_01`, `test_02`, `test_03` | **PASS** |
| 2 | Balanced execution timing | Non-existent and active accounts execute symmetrical token generation/hashing without Argon2id imbalance | `test_32_forgot_password_timing_anti_enumeration` | **PASS** |
| 3 | Single-use hashed token with 30-min expiry | 32-byte URL-safe token (256 bits entropy); SHA-256 hex stored in `password_reset_tokens`; `expires_at = now + 30 min` | `test_01_forgot_password_success_creates_token_and_queues_email` | **PASS** |
| 4 | Transactional outbox email queueing | Queued atomically in `email_outbox` with subject, HTML/text templates, tags `["auth", "password_reset"]`, and outbox metadata | `test_01_forgot_password_success_creates_token_and_queues_email` | **PASS** |
| 5 | Invalidation of prior tokens on forgot-password | Requesting a new reset token invalidates any prior unexpired, unused reset tokens for that user | `test_06_forgot_password_invalidates_prior_unused_tokens` | **PASS** |
| 6 | Invalidation of all tokens on successful reset | Consuming a reset token invalidates ALL other outstanding reset tokens for that user | `test_29_successful_reset_invalidates_all_outstanding_reset_tokens` | **PASS** |
| 7 | Per-email rate limiting (forgot-password) | 3 requests/hour in Redis (fallback 2) per normalized email; returns HTTP 429 `RATE_LIMIT_EXCEEDED` | `test_04`, `test_04b` | **PASS** |
| 8 | Per-IP rate limiting (forgot-password) | 10 requests/hour in Redis (fallback 5) per client IP; returns HTTP 429 `RATE_LIMIT_EXCEEDED` | `test_05_forgot_password_rate_limiting_per_ip` | **PASS** |
| 9 | Successful password reset | `POST /auth/reset-password` updates Argon2id password hash, resets `failed_login_count=0`, clears `locked_until=None` | `test_07_reset_password_success` | **PASS** |
| 10 | Immediate global session revocation | Resetting password immediately revokes all user sessions in `user_sessions` with `revocation_reason="password_reset"` | `test_08_reset_password_global_session_revocation` | **PASS** |
| 11 | Old sessions rejected with 401 | Any API request using a pre-reset session token is rejected with HTTP 401 `SESSION_REVOKED` | `test_09_old_sessions_fail_after_reset` | **PASS** |
| 12 | Concurrent login race protection | Optimistic `expected_password_hash` check in `update_login_success` aborts login and leaves 0 sessions if password changed during verify | `test_30_concurrent_login_during_reset_aborts_without_issuing_session` | **PASS** |
| 13 | Pre-reset login revoked by reset | A session committed before reset executes is strictly revoked by reset | `test_31_login_before_reset_is_revoked_by_subsequent_reset` | **PASS** |
| 14 | Authentication with new password | Old password fails with HTTP 401; new password succeeds with HTTP 200 and issues a fresh session | `test_10_login_with_new_password_succeeds_old_password_fails` | **PASS** |
| 15 | Ambient session cookie clearance | If reset-password request carries an ambient session cookie, it is cleared via `Set-Cookie: max-age=0` in the response | `test_11_reset_password_clears_ambient_cookie` | **PASS** |
| 16 | Reused token rejection | Attempting to reuse a consumed token returns HTTP 400 with code `TOKEN_ALREADY_USED` | `test_12_reused_reset_token_rejected` | **PASS** |
| 17 | Expired token rejection | Attempting to use an expired token (>30 min) returns HTTP 400 with code `TOKEN_EXPIRED` | `test_13_expired_reset_token_rejected` | **PASS** |
| 18 | Invalid / malformed token rejection | Non-existent or malformed token (< 10 chars) returns HTTP 400 with code `TOKEN_INVALID` | `test_14_invalid_or_malformed_reset_token_rejected` | **PASS** |
| 19 | Password strength validation | Passwords < 10 characters or in common password blocklist rejected with HTTP 400 `WEAK_PASSWORD`; token remains unconsumed | `test_15_weak_password_rejected` | **PASS** |
| 20 | Disabled account defense | Suspended or banned accounts rejected with HTTP 403 `ACCOUNT_DISABLED`; password is not reset | `test_16_disabled_account_reset_rejected` | **PASS** |
| 21 | Navigation safety | GET /reset-password returns HTTP 405 Method Not Allowed; navigation or email scanners cannot consume action token | `test_17_get_navigation_does_not_consume_action` | **PASS** |
| 22 | Web client transport | Web client submits reset with Origin header and valid CSRF token; succeeds | `test_18_both_client_transport_web_with_csrf` | **PASS** |
| 23 | Native client transport | Native client submits reset with `X-Client-Type: native` (exempt from CSRF, cookie-free); succeeds | `test_19_both_client_transport_native_without_csrf` | **PASS** |
| 24 | Inactive auth guard | `auth_endpoints_enabled=False` by default returns HTTP 503 `SERVICE_UNAVAILABLE` on all auth routes | `test_20_auth_endpoints_disabled_by_default_returns_503` | **PASS** |
| 25 | Concurrency simulation (mock) | In-memory asyncio simulation proves conditional UPDATE prevents double consumption | `test_21_concurrent_reset_password_race_condition` | **PASS (Mock)** |
| 26 | Exact boundary expiration | Token expiring at or before now is rejected with HTTP 400 `TOKEN_EXPIRED` | `test_22_exact_boundary_expiration` | **PASS** |
| 27 | Transaction rollback atomicity | Database failure rolls back token consumption, password update, and session revocation atomically | `test_23_transaction_rollback_atomicity` | **PASS** |
| 28 | Cross-user token isolation | User A's reset token cannot reset User B's password | `test_24_cross_user_token_isolation` | **PASS** |
| 29 | ORM identity-map fresh read | `populate_existing=True` forces fresh read from database, bypassing stale identity-map state on failure re-query | `test_25_sqlalchemy_orm_identity_map_fresh_read` | **PASS (ORM)** |
| 30 | ORM losing-request classification | Losing request in concurrent race deterministically returns `TOKEN_ALREADY_USED` on real ORM state | `test_26_sqlalchemy_losing_request_returns_token_already_used` | **PASS (ORM)** |
| 31 | ORM atomic commit verification | Reset commits token, password, and session revocation atomically to database, verified via independent session | `test_27_sqlalchemy_atomic_commit` | **PASS (ORM)** |
| 32 | ORM atomic rollback verification | Failed transaction rolls back token, password, and session revocation in database with zero manual model resets | `test_28_sqlalchemy_atomic_rollback` | **PASS (ORM)** |
| 33 | Lock acquisition ordering | In login, `update_login_success` (users lock) executes strictly before `revoke_session` (user_sessions lock) | `test_33_login_lock_ordering_update_login_success_before_revoke_session` | **PASS (ORM)** |
| 34 | Ambient session rotation | Login with ambient web cookie rotates old session to revoked and creates new active session in DB | `test_34_login_session_rotation_genuine_sqlalchemy` | **PASS (ORM)** |
| 35 | Failed optimistic check safety | If optimistic password check fails during login, ambient session is NOT revoked and 0 new sessions are created | `test_35_failed_optimistic_login_aborts_before_revoking_ambient_session` | **PASS (ORM)** |
| 36 | Total lock ordering contract | Strict global hierarchy `users` -> `password_reset_tokens` -> `user_sessions` verified across login and reset | `test_36_session_rotation_deadlock_freedom_total_lock_ordering_contract` | **PASS (Code/Contract)** |

---

## 2. Architecture & Implementation Design

### 2.1 Database Model & Schema Integration

The `password_reset_tokens` table was provisioned in the initial foundation migration (`0001_foundation.py`) under strict PostgreSQL constraints:

```sql
CREATE TABLE password_reset_tokens (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    user_id UUID NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_password_reset_tokens PRIMARY KEY (id),
    CONSTRAINT ck_password_reset_tokens_token_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_password_reset_tokens_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
    CONSTRAINT uq_password_reset_tokens_token_hash UNIQUE (token_hash)
);
CREATE INDEX ix_password_reset_tokens_expires_at ON password_reset_tokens (expires_at);
CREATE INDEX ix_password_reset_tokens_user_id ON password_reset_tokens (user_id);
```

**Cryptographic Token & Data Storage Realities:**
1. **`password_reset_tokens.token_hash`:** Strictly stores 64-character lowercase SHA-256 hex digests. Plaintext action tokens are never written to this table.
2. **`email_outbox.html_content` & `text_content`:** Transactional emails contain the full action link (`{frontend_origin}/reset-password?token={raw_token}`) so the transactional background worker can dispatch the link to the recipient. Outbox `metadata` is strictly sanitized to `{"action": "password_reset", "user_id": str(user_id)}`.
3. **Plaintext User Identifiers:** User email addresses are stored in plaintext in `users.email` and `email_outbox.recipient` in PostgreSQL. Redis rate limiting keys use SHA-256 HMAC/hashed identifiers (`rl:password_reset:email:<hash>`), strictly excluding raw emails from Redis.

### 2.2 Concurrency Architecture: Total Lock Ordering & Deadlock Freedom

Under concurrent execution of `login` (with ambient session rotation) and `reset_password`, inconsistent lock acquisition orders could yield a circular wait:
- **Previous Inversion:**
  - `login`: acquired lock on `user_sessions` (via `revoke_session`) before `users` (via `update_login_success`).
  - `reset_password`: acquired lock on `users` (via `get_user_by_id FOR UPDATE`) before `user_sessions` (via `revoke_all_user_sessions`).
  - Concurrently, this produced a PostgreSQL deadlock (`40P01 DeadlockDetected`).
- **Corrected Total Lock Ordering:**
  - In `auth_service.login()`, `user_repo.update_login_success` is executed **before** `session_repo.revoke_session`.
  - Global acquisition hierarchy: `users` -> `password_reset_tokens` -> `user_sessions`.
  - Both `login` and `reset_password` serialize on the `users` row FIRST before touching `user_sessions`.
  - Circular wait between `users` and `user_sessions` is mathematically impossible under Coffman's conditions, preventing error `40P01`.

1. **In `apps/api/app/db/repositories/user_repo.py` (`update_login_success`):**
   ```python
   async def update_login_success(
       session: AsyncSession,
       user_id: UUID,
       login_at: datetime,
       expected_password_hash: str | None = None,
   ) -> bool:
       stmt = update(User).where(User.id == user_id)
       if expected_password_hash is not None:
           stmt = stmt.where(User.password_hash == expected_password_hash)
       stmt = stmt.values(failed_login_count=0, last_login_at=login_at)
       result = await session.execute(stmt)
       return int(getattr(result, "rowcount", 0) or 0) == 1
   ```
2. **In `apps/api/app/services/auth_service.py` (`login`):**
   - Executed **before** session rotation and session creation:
     ```python
     updated = await user_repo.update_login_success(
         session, user.id, now, expected_password_hash=user.password_hash
     )
     if updated is False:
         raise AppError(
             code="AUTHENTICATION_FAILED",
             message="Email or password is incorrect.",
             status_code=status.HTTP_401_UNAUTHORIZED,
         )
     # Session rotation executes only after user row lock is acquired:
     if client_type == "web" and existing_cookie_token:
         ...
         await session_repo.revoke_session(...)
     user_session = await session_repo.create_session(...)
     ```
3. **Database Concurrency Guarantee under PostgreSQL READ COMMITTED:**
   - Case A (Reset commits before Login updates user): The `UPDATE users ... WHERE password_hash = :expected_password_hash` finds 0 matching rows (`rowcount == 0`), returns `False`, and `login` raises HTTP 401 `AUTHENTICATION_FAILED`. The transaction rolls back, and neither session rotation nor new session creation in `user_sessions` occurs.
   - Case B (Login acquires row lock before Reset updates user): Login locks the user row, rotates the existing cookie, creates the replacement session, and commits. When Reset runs, its `revoke_all_user_sessions` executes after login has committed, strictly revoking all sessions including the replacement session.
   - Case C (Session rotation concurrency): Because `login` locks `users` before `user_sessions`, and `reset_password` locks `users` before `user_sessions`, lock acquisition follows an identical global order, eliminating circular waits.

### 2.3 Serialization & Invalidation of Reset Tokens

In `apps/api/app/services/auth_service.py` (`reset_password`):
1. **User Row Serialization:** `user = await user_repo.get_user_by_id(session, token_row.user_id, for_update=True)` acquires an exclusive row lock on `users`. All reset requests for the same user are strictly serialized.
2. **Complete Invalidation:**
   ```python
   await user_repo.update_user_password(session, user.id, new_password_hash)
   await password_reset_repo.invalidate_user_reset_tokens(session, user.id, now)
   await session_repo.revoke_all_user_sessions(session, user.id, now, reason="password_reset")
   ```
3. **Racing Resets with Different Tokens:**
   If Request 1 and Request 2 arrive with Token 1 and Token 2: Request 1 locks the user row, updates the password, and invalidates all reset tokens for that user (marking Token 2 used). When Request 2 unblocks, its call to `mark_token_used` on Token 2 finds `used_at IS NOT NULL`, returns `rowcount == 0`, and deterministically fails with HTTP 400 `TOKEN_ALREADY_USED` without overwriting the password.

### 2.4 Anti-Enumeration & Timing Equalization

In `apps/api/app/services/auth_service.py` (`forgot_password`):
- Removed `verify_dummy_password()` (which executed ~75ms Argon2id CPU work only for non-existent users).
- Both existing and non-existent/inactive accounts now perform identical token generation and SHA-256 hashing operations (`generate_action_token()` + `hash_action_token()`).
- Measured response latencies across all states are uniformly fast (~2-5ms locally), eliminating side-channel account enumeration.

---

## 3. Test Matrix & Verification Results

### 3.1 T-11 Focused Test Suite (`test_t11_password_reset.py`)

All 37 tests pass:
```
============================= test session starts ==============================
platform darwin -- Python 3.12.16, pytest-9.0.2, pluggy-1.7.0
rootdir: /Users/manish/Desktop/dormiostay/apps/api
configfile: pyproject.toml
plugins: anyio-4.16.0
collected 37 items

tests/unit/test_t11_password_reset.py::test_01_forgot_password_success_creates_token_and_queues_email PASSED [  2%]
tests/unit/test_t11_password_reset.py::test_02_forgot_password_anti_enumeration_nonexistent_email PASSED [  5%]
tests/unit/test_t11_password_reset.py::test_03_forgot_password_anti_enumeration_suspended_or_banned_email PASSED [  8%]
tests/unit/test_t11_password_reset.py::test_04_forgot_password_rate_limiting_per_email PASSED [ 10%]
tests/unit/test_t11_password_reset.py::test_04b_forgot_password_rate_limiting_fallback_stricter PASSED [ 13%]
tests/unit/test_t11_password_reset.py::test_05_forgot_password_rate_limiting_per_ip PASSED [ 16%]
tests/unit/test_t11_password_reset.py::test_06_forgot_password_invalidates_prior_unused_tokens PASSED [ 18%]
tests/unit/test_t11_password_reset.py::test_07_reset_password_success PASSED [ 21%]
tests/unit/test_t11_password_reset.py::test_08_reset_password_global_session_revocation PASSED [ 24%]
tests/unit/test_t11_password_reset.py::test_09_old_sessions_fail_after_reset PASSED [ 27%]
tests/unit/test_t11_password_reset.py::test_10_login_with_new_password_succeeds_old_password_fails PASSED [ 29%]
tests/unit/test_t11_password_reset.py::test_11_reset_password_clears_ambient_cookie PASSED [ 32%]
tests/unit/test_t11_password_reset.py::test_12_reused_reset_token_rejected PASSED [ 35%]
tests/unit/test_t11_password_reset.py::test_13_expired_reset_token_rejected PASSED [ 37%]
tests/unit/test_t11_password_reset.py::test_14_invalid_or_malformed_reset_token_rejected PASSED [ 40%]
tests/unit/test_t11_password_reset.py::test_15_weak_password_rejected PASSED [ 43%]
tests/unit/test_t11_password_reset.py::test_16_disabled_account_reset_rejected PASSED [ 45%]
tests/unit/test_t11_password_reset.py::test_17_get_navigation_does_not_consume_action PASSED [ 48%]
tests/unit/test_t11_password_reset.py::test_18_both_client_transport_web_with_csrf PASSED [ 51%]
tests/unit/test_t11_password_reset.py::test_19_both_client_transport_native_without_csrf PASSED [ 54%]
tests/unit/test_t11_password_reset.py::test_20_auth_endpoints_disabled_by_default_returns_503 PASSED [ 56%]
tests/unit/test_t11_password_reset.py::test_21_concurrent_reset_password_race_condition PASSED [ 59%]
tests/unit/test_t11_password_reset.py::test_22_exact_boundary_expiration PASSED [ 62%]
tests/unit/test_t11_password_reset.py::test_23_transaction_rollback_atomicity PASSED [ 64%]
tests/unit/test_t11_password_reset.py::test_24_cross_user_token_isolation PASSED [ 67%]
tests/unit/test_t11_password_reset.py::test_25_sqlalchemy_orm_identity_map_fresh_read PASSED [ 70%]
tests/unit/test_t11_password_reset.py::test_26_sqlalchemy_losing_request_returns_token_already_used PASSED [ 72%]
tests/unit/test_t11_password_reset.py::test_27_sqlalchemy_atomic_commit PASSED [ 75%]
tests/unit/test_t11_password_reset.py::test_28_sqlalchemy_atomic_rollback PASSED [ 78%]
tests/unit/test_t11_password_reset.py::test_29_successful_reset_invalidates_all_outstanding_reset_tokens PASSED [ 81%]
tests/unit/test_t11_password_reset.py::test_30_concurrent_login_during_reset_aborts_without_issuing_session PASSED [ 83%]
tests/unit/test_t11_password_reset.py::test_31_login_before_reset_is_revoked_by_subsequent_reset PASSED [ 86%]
tests/unit/test_t11_password_reset.py::test_32_forgot_password_timing_anti_enumeration PASSED [ 89%]
tests/unit/test_t11_password_reset.py::test_33_login_lock_ordering_update_login_success_before_revoke_session PASSED [ 91%]
tests/unit/test_t11_password_reset.py::test_34_login_session_rotation_genuine_sqlalchemy PASSED [ 94%]
tests/unit/test_t11_password_reset.py::test_35_failed_optimistic_login_aborts_before_revoking_ambient_session PASSED [ 97%]
tests/unit/test_t11_password_reset.py::test_36_session_rotation_deadlock_freedom_total_lock_ordering_contract PASSED [100%]

======================== 37 passed, 1 warning in 1.89s =========================
```

### 3.2 Regression Suites & Linters

| Suite / Check | Command | Result |
|---|---|---|
| Security Suite (T-06..T-11) | `pytest tests/unit/test_t06_auth.py ... test_t11_password_reset.py` | **195 passed** |
| Full API Unit Suite | `pytest tests/unit/` | **634 passed** (0 failures) |
| Python Linting | `ruff check app tests scripts alembic` | **All checks passed!** (0 errors) |
| Python Formatting | `ruff format --check app tests scripts alembic` | **62 files already formatted** |
| Python Type Checking | `mypy app tests alembic scripts` | **Success: no issues found in 62 source files** |
| Frontend Web Unit Tests | `npm test --workspace @dormiostay/web` | **84 passed** |
| Frontend Mobile Unit Tests | `npm run test --workspace @dormiostay/mobile` | **88 passed** |
| Frontend Web Linting | `npm run lint` | **0 warnings, 0 errors** |
| Frontend Mobile Linting | `npm run lint:mobile` | **0 warnings, 0 errors** |
| Frontend Web Typecheck | `npm run typecheck` | **0 errors** |
| Frontend Mobile Typecheck | `npm run typecheck:mobile` | **0 errors** |
| Prettier Style Check | `npm run format:check` | **All matched files use Prettier code style!** |
| Frontend Web Build | `npm run build` | **Built in 150ms** |
| Git Whitespace Check | `git diff --check` | **Clean (0 errors)** |

---

## 4. Security Boundaries, File Inventory & Release Gates

### 4.1 Exact 10-File Inventory

The implementation consists of exactly 10 authorized files (7 modified tracked files, 3 new untracked files):

**Modified Tracked Files (7):**
1. `apps/api/app/api/v1/auth.py` — API endpoints for forgot-password and reset-password, CSRF and rate limiting wiring.
2. `apps/api/app/core/csrf.py` — CSRF exemption for unauthenticated `/auth/reset-password` endpoint.
3. `apps/api/app/core/rate_limit.py` — Rate limiter rules (`RULE_PASSWORD_RESET_EMAIL`, `RULE_PASSWORD_RESET_IP`).
4. `apps/api/app/db/repositories/user_repo.py` — `get_user_by_id(..., for_update=True)` and optimistic `update_login_success`.
5. `apps/api/app/schemas/auth.py` — Pydantic schemas (`ForgotPasswordRequest`, `ResetPasswordRequest`, `ResetPasswordResponse`).
6. `apps/api/app/services/auth_service.py` — Core logic: login lock order, constant forgot-password overhead, reset-password transaction.
7. `apps/api/app/services/email_service.py` — `queue_password_reset_email` transactional outbox enqueueing.

**New Files (3):**
8. `apps/api/app/db/repositories/password_reset_repo.py` — Token creation, atomic conditional consumption, and bulk invalidation.
9. `apps/api/tests/unit/test_t11_password_reset.py` — 37 comprehensive unit, ORM, and concurrency regression tests.
10. `docs/T-11 Verification Report.md` — Authoritative verification report and audit evidence.

### 4.2 Security Boundaries & Production Readiness Limitations

1. **Uncommitted State Prior to Final Closure:** All modifications remain on branch `feature/t11-implementation`.
2. **Local Isolation:** Zero external network calls (no Neon, Render, Brevo, or Upstash API calls). Zero live emails sent (captured in transactional outbox).
3. **Safe Default:** Feature toggle `auth_endpoints_enabled = False` preserved across all configurations.
4. **PostgreSQL Concurrency Integration Limitation:** While total lock ordering ($users \prec password\_reset\_tokens \prec user\_sessions$) is proven by code analysis and verified via SQLite/SQLAlchemy ORM tests, true multi-connection PostgreSQL row lock concurrency testing under high throughput remains pending live database deployment.
5. **Production Readiness:** T-11 is complete locally but **NOT production-ready** until full staging deployment gates pass.
6. **Project Release Gates Status:**
   - **T-05 Astra Gate 4:** PENDING (authoritative staging database role provisioning).
   - **T-05 Gate 5 Render:** NOT STARTED.
   - **T-06 through T-10:** LOCALLY COMMITTED.
   - **T-11:** AWAITING FINAL LOCAL COMMIT.
   - **T-13 Admin MFA:** PENDING.
