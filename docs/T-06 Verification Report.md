# T-06 Verification Report — Authentication Backend Foundation

**Date:** 2026-11-25 (Asia/Kolkata)  
**Ticket:** T-06 (Authentication Backend Implementation)  
**Branch:** `feature/t06-authentication`  
**Parent HEAD:** `b5ceae221d8800d8b22937ef187274946fcfda7a`  
**Overall Status: LOCAL IMPLEMENTATION VERIFIED — PROVISIONAL SECURITY APPROVAL; POSTGRESQL INTEGRATION AND DEPENDENT SECURITY TICKETS PENDING**

> [!CAUTION]
> **Production Readiness Notice:**
> Do NOT label T-06 production-ready.
> T-06 establishes the core backend authentication contracts, Argon2id hashing, session token management, and repository layers verified in offline unit test environments. The public authentication endpoints are guarded by `auth_endpoints_enabled = False` (returning HTTP 503 Service Unavailable). Live deployment requires:
> 1. Completion and verification of T-07 (CSRF double-submit protection and Origin verification).
> 2. Completion and verification of T-08 (Redis / Upstash rate limiting across login and signup endpoints).
> 3. Execution of live PostgreSQL integration tests verifying `asyncpg` structured diagnostics, functional unique index `uq_users_email_ci`, and regex constraints.
> 4. Verification of Gate 5 Render deployment after independent Codex Astra approval for T-05 is granted.

---

## 1. Executive Summary

| Verification Category | Status | Details |
|---|---|---|
| **Local Implementation Verification** | **PASS** | Dual-transport authentication (Web cookies / Native mobile Bearer tokens), Argon2id password hashing, SHA-256 session-token persistence, sliding idle/absolute session lifecycle, and transaction-local RLS contexts are fully implemented and verified locally. |
| **Independent Security Review** | **PROVISIONAL APPROVED** | Independent review conducted by Gemini 4 Hydrogen issued `PROVISIONAL APPROVED — T-06 READY FOR FINAL DOCUMENTATION AND ONE LOCAL COMMIT` following resolution of all three reported blockers. |
| **IntegrityError Constraint Diagnostics** | **RESOLVED** | PostgreSQL structured diagnostics (`sqlstate`, `constraint_name`) are checked exclusively when available; generic substring matching removed; narrow string fallback restricted to SQLite testing. |
| **Security Transaction Isolation** | **RESOLVED** | Mandatory `session_factory` established; fail-closed guard active; security event updates (`update_login_failure`, `revoke_session`) execute inside dedicated `sec_session.begin()` contexts; outer request session is never prematurely committed. |
| **Genuine Repository Execution** | **RESOLVED** | Toy inline SQL replaced with `AsyncSqliteSessionAdapter` executing real production repository functions (`session_repo.update_session_activity`, `session_repo.revoke_all_user_sessions`, `session_repo.revoke_session`, `user_repo.update_login_failure`). |
| **Unit Test Suite** | **PASS** | **56 passed T-06 tests**; **495 passed total API unit tests** (0 failures). |
| **Static Analysis & Linting** | **PASS** | Ruff lint 100% clean; Ruff format 100% clean (46 files formatted); strict Mypy clean across 43 source files. |
| **PostgreSQL Integration Testing** | **NOT EXECUTED** | Offline local verification only. Live PostgreSQL (`asyncpg`) concurrency, functional unique indexing, and regex check constraint execution remain unverified in this offline suite. |
| **Activation Guard** | **DISABLED BY DEFAULT** | `auth_endpoints_enabled` defaults to `False`; endpoints return HTTP 503 until T-07 and T-08 are active. |
| **T-05 Baseline Protection** | **PROTECTED** | T-05 Gate 4 sequence-aware live verification passed; Astra sign-off remains PENDING; Gate 5 Render deployment NOT STARTED; zero T-05 files modified. |

---

## 2. Implementation Inventory & API Contracts

### 2.1 API Endpoints & Contracts

All endpoints are mounted under `/api/v1/auth` and protected by the `require_auth_endpoints_enabled` dependency:

1. **`POST /api/v1/auth/signup`**
   - **Role Restriction:** Public registration strictly limited to `tenant` and `owner` roles; `admin` registration rejected with HTTP 400 (`FORBIDDEN_ROLE`).
   - **Password Complexity:** Validated against minimum length ($\ge 10$ characters), a case-insensitive common password blacklist, and rejection of single repeated characters (e.g., `aaaaaaaaaa`). No mandatory combination of uppercase, lowercase, digits, or special characters is required; fails with HTTP 422 (`WEAK_PASSWORD`).
   - **Case-Insensitive Uniqueness:** Enforces uniqueness using `lower(email)`; duplicate registration fails with HTTP 409 (`EMAIL_ALREADY_EXISTS`).
   - **Atomic Persistence:** User and user profile records created inside `session.begin_nested()` savepoint. If institution ID is invalid, foreign key violation maps to HTTP 400 (`INVALID_INSTITUTION`).
   - **Response:** HTTP 201 Created returning `UserResponse` (omitting credentials, hashes, and internal tokens).

2. **`POST /api/v1/auth/login`**
   - **Case-Insensitive Lookup:** Finds user by `lower(email)`.
   - **Timing Attack Mitigation:** Constant-time dummy Argon2id verification (`verify_dummy_password()`) runs when email is not found.
   - **Generic Credential Failure:** Returns HTTP 401 (`AUTHENTICATION_FAILED`) with message `"Email or password is incorrect."` for both nonexistent users and bad passwords.
   - **Security Write Isolation:** Failed password attempts increment `failed_login_count` in an independent session (`sec_session.begin()`), ensuring persistence even when outer transaction aborts on 401.
   - **Status Checks:** Inactive, suspended, or banned users are rejected with HTTP 403 (`ACCOUNT_DISABLED`).
   - **Client-Type Segregation:**
     - **Web Mode (`client_type="web"`):** Issues `HttpOnly`, `SameSite=Lax`, `Secure` session cookie (`dormiostay_session`); JSON response contains `user` metadata only with **no raw token in body**.
     - **Native Mobile Mode (`client_type="native"`):** Issues raw token once in JSON body (`AuthResponseNative(token=raw_token, user=...)`) for Expo SecureStore; **no session cookie is set**; headers enforce `Cache-Control: no-store, no-cache`. Requires HTTPS outside development environment.
   - **Transport Disambiguation:** Conflicting `client_type` in body versus `X-Client-Type` header rejected with HTTP 400 (`AMBIGUOUS_TRANSPORT`). Ambient session cookies on native requests rejected with HTTP 400 (`AMBIGUOUS_CREDENTIALS`).
   - **Session Rotation:** Re-login on Web revokes any previously presented ambient session cookie with `revocation_reason="session_rotation"`.
   - **Admin Boundary:** Admin login rejected with HTTP 403 (`ADMIN_PORTAL_REQUIRED` / `ADMIN_NATIVE_LOGIN_DISALLOWED`) pending dedicated portal and T-13 MFA.

3. **`POST /api/v1/auth/logout`**
   - **Immediate Revocation:** Sets `revoked_at=now()` and `revocation_reason="logout"` on the active session.
   - **Cookie Deletion:** Clears session cookie on Web clients with `max_age=0`.

4. **`GET /api/v1/auth/me`**
   - **Transport Support:** Accepts either Web session cookie or `Authorization: Bearer <token>` header. Ambiguous requests supplying both are rejected with HTTP 400 (`AMBIGUOUS_CREDENTIALS`).
   - **Expiry Enforcement:**
     - A session expires when the current UTC time reaches or exceeds its applicable stored expiry timestamp (`now >= absolute_expires_at` or `now >= expires_at`).
     - **Tenant / Owner Limits:** Idle timeout of 30 minutes; absolute session lifetime of 14 days.
     - **Admin Limits:** Idle timeout of 15 minutes; absolute session lifetime of 8 hours.
     - Expired sessions are marked revoked via an independent security transaction (`sec_session.begin()` with reason `"absolute_expiry"` or `"idle_expiry"`) and return HTTP 401 (`SESSION_EXPIRED`).
   - **Sliding Idle Window:** Valid requests slide `expires_at` forward by idle delta (capped at `absolute_expires_at`).
   - **Transaction-Local Identity:** Sets transaction-local PostgreSQL session GUCs via `set_config('app.user_id', :user_id, true)` and `set_config('app.role', :role, true)`.
   - **Response:** Returns account details and user profile without exposing sensitive hashes or credentials.

### 2.2 Security Architecture & Cryptographic Primitives

- **Password Hashing:** Implemented via `pwdlib` using Argon2id with OWASP-recommended parameters: memory cost $m=65536$ KiB (64 MiB), time cost $t=3$ iterations, parallelism $p=4$ threads (`$argon2id$m=65536,t=3,p=4`).
- **Session Tokens:** 32-byte cryptographic random tokens generated via `secrets.token_urlsafe(32)`.
- **Token Storage:** Only SHA-256 hashes of session tokens are stored in `user_sessions.token_hash` (`CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'")`). Raw tokens are never logged or stored.
- **Activation Guard:** Configured via `Settings.auth_endpoints_enabled: bool = False`. Dependency `require_auth_endpoints_enabled` raises HTTP 503 (`SERVICE_UNAVAILABLE`) when disabled. Health endpoints remain unaffected.

---

## 3. Independent Security Review & Blocker Remediation

### 3.1 Initial Blocker Resolutions

Gemini 4 Hydrogen identified five material architectural findings in initial review:
1. **Premature Commit in Transaction Lifecycle:** Outer transaction `.commit()` inside `session.begin()` broke SQLAlchemy context lifecycles. Resolved by removing explicit commits and relying on context manager scopes.
2. **Outer Session Rollback Dropping Security Writes:** Failed login attempts and session revocations were lost on 401 rollbacks. Resolved by introducing `session_factory` for independent `sec_session` transactions.
3. **Transport Ambiguity & Insecure Native Transport:** Added mutual exclusivity validation between cookies and Authorization headers, disambiguated `X-Client-Type` headers, and enforced HTTPS for native mobile.
4. **Concurrency & Foreign Key IntegrityError Classification:** Handled concurrent signup races and invalid institution foreign key references gracefully.
5. **Session Rotation & Revocation Race Handling:** Implemented atomic session rotation and handled concurrent logout races in sliding window updates.

### 3.2 Final Three-Blocker Remediation

Gemini 4 Hydrogen's focused second review identified three reproducible technical blockers, which were remediated and verified:

```
+---------------------------------------------------------------------------------------------------+
| BLOCKER 1: PostgreSQL IntegrityError Misclassification                                            |
| File: apps/api/app/services/auth_service.py                                                       |
| Issue: Substring checks ("users", "email") on 23505 misclassified unrelated unique constraints.   |
| Fix: Exclusive structured check on (sqlstate == '23505' and constraint == 'uq_users_email_ci')    |
|      and (sqlstate == '23503' and constraint == 'fk_user_profiles_institution_id_institutions').  |
|      All other structured constraint errors propagate. String fallback runs only when both None.  |
| Status: RESOLVED & VERIFIED                                                                       |
+---------------------------------------------------------------------------------------------------+
                                                  |
+---------------------------------------------------------------------------------------------------+
| BLOCKER 2: Mandatory Independent Security Transaction Factory                                     |
| Files: apps/api/app/services/auth_service.py, app/api/deps.py, app/api/v1/auth.py                 |
| Issue: Optional session_factory=None defaulted to outer session, dropping security writes.        |
| Fix: Removed default (= None); added fail-closed guard; eliminated outer session fallback;         |
|      injected Depends(get_session_factory) into all production callers.                           |
| Status: RESOLVED & VERIFIED                                                                       |
+---------------------------------------------------------------------------------------------------+
                                                  |
+---------------------------------------------------------------------------------------------------+
| BLOCKER 3: Production Repository Execution in Test Suite                                          |
| File: apps/api/tests/unit/test_t06_auth.py                                                        |
| Issue: Toy inline SQL on custom tables substituted for production repository calls.              |
| Fix: Implemented AsyncSqliteSessionAdapter executing actual repository coroutines                 |
|      (session_repo.update_session_activity, revoke_all_user_sessions, revoke_session,             |
|      user_repo.update_login_failure). Proved test failure if revoked_at IS NULL is omitted.        |
| Status: RESOLVED & VERIFIED                                                                       |
+---------------------------------------------------------------------------------------------------+
```

### 3.3 Independent Review Verdict

On 2026-11-25, Gemini 4 Hydrogen issued:
> **PROVISIONAL APPROVED — T-06 READY FOR FINAL DOCUMENTATION AND ONE LOCAL COMMIT**

---

## 4. Verification Evidence & Test Results

### 4.1 Automated Test Execution

All tests were executed locally against the unmodified working tree using Python 3.12:

```text
============================= test session starts ==============================
platform darwin -- Python 3.12.16, pytest-9.0.2, pluggy-1.7.0
rootdir: /Users/manish/Desktop/dormiostay/apps/api
configfile: pyproject.toml
plugins: anyio-4.16.0

apps/api/tests/unit/test_t06_auth.py: 56 passed in 1.54s
apps/api/tests/unit/ (Full API Suite): 495 passed in 28.50s
====================== 495 passed, 15 warnings in 28.50s =======================
```

### 4.2 Test Breakdown

- **Core T-06 Acceptance Tests (22 tests):** Covers Tenant/Owner signup, Admin signup rejection, case-insensitive duplicate email rejection, wrong password, unknown email, account suspension/ban, Argon2id hashing, SHA-256 token hashing, Web cookie issuance, Native bearer issuance, Native Admin rejection, mixed credentials rejection, invalid bearer rejection, idle session expiry, absolute session expiry, logout revocation, all-session revocation, session rotation, safe `/auth/me` profile response, transaction rollback, and activation guard.
- **Blocker 1 IntegrityError Tests (6 tests):** Covers structured duplicate email (409), structured invalid institution (400), structured unrelated unique constraint (500), structured unrelated foreign key (500), direct `auth_service.signup` `IntegrityError` propagation, and fallback generic string rejection (500).
- **Blocker 2 Transaction Isolation Tests (4 tests):** Covers fail-closed login without session factory, fail-closed validate session without session factory, security transaction commit and session write isolation on failed login, and security transaction commit and session write isolation on expired session.
- **Blocker 3 Genuine Repository Tests (4 tests):** Covers `session_repo.update_session_activity` (verifying unrevoked vs revoked rows), `session_repo.revoke_all_user_sessions` (verifying count and row preservation), `user_repo.update_login_failure` (verifying counter increment), and `session_repo.revoke_session` (verifying revocation reason).
- **Additional Security & Regression Tests (20 tests):** Covers RLS GUC execution on `db_session`, transport disambiguation, insecure transport rejection, admin portal required, concurrent logout race, sliding idle window updates, savepoint nested rollback, and PostgreSQL dialect query compilation.

### 4.3 Static Analysis & Formatting Audit

- **Ruff Check:** `./apps/api/.venv/bin/ruff check apps/api` $\rightarrow$ `All checks passed!` (0 lint errors).
- **Ruff Format:** `./apps/api/.venv/bin/ruff format --check apps/api` $\rightarrow$ `46 files already formatted` (0 formatting discrepancies).
- **Mypy Strict Check:** `(cd apps/api && .venv/bin/mypy app tests alembic)` $\rightarrow$ `Success: no issues found in 43 source files` (0 type errors).
- **Git Diff Whitespace Check:** `git diff --check` $\rightarrow$ Clean (0 whitespace or merge marker issues).

---

## 5. Offline Verification Boundaries & PostgreSQL Integration Limitations

The following items are explicitly recorded as **NOT EXECUTED** in the local verification scope:

1. **No Live PostgreSQL Execution:** The unit tests execute against in-memory SQLite and mock sessions. Real PostgreSQL execution via `asyncpg` was not performed.
2. **PostgreSQL Functional Unique Index Unverified:** Uniqueness enforcement on `lower(email)` via PostgreSQL functional index `uq_users_email_ci ON users (lower(email))` was verified via SQL dialect compilation and mock error simulation, not against a live PostgreSQL catalog.
3. **PostgreSQL Regex Check Constraints Unverified:** Check constraints such as `token_hash ~ '^[0-9a-f]{64}$'` use PostgreSQL POSIX regular expressions, which are unsupported in SQLite DDL and remain to be tested on PostgreSQL.
4. **PostgreSQL Connection Pool Concurrency:** Opening `sec_session` while outer `db_session` is active temporarily consumes two connections from the pool during failed login and expired session requests. Concurrency under constrained connection pools remains to be verified under load.
5. **PostgreSQL Runtime Role Verification:** Verification that `sec_session` operations comply with the least-privilege `dormiostay_app` role without encountering permission boundaries is pending live Gate 5 staging integration.

---

## 6. Remaining Ticket Dependencies

T-06 is a foundation ticket and does not complete all authentication security controls:

- **T-07 (CSRF & Origin Verification):** Double-submit cookie verification and strict `Origin` header checking for state-changing Web requests are **NOT IMPLEMENTED**.
- **T-08 (Rate Limiting):** Distributed Redis / Upstash rate limiting on `/login` and `/signup` endpoints is **NOT IMPLEMENTED**.
- **T-13 (Admin MFA & Dedicated Portal):** Multi-factor authentication (TOTP/WebAuthn) for administrative accounts is **NOT IMPLEMENTED**; Admin login via public endpoints is strictly blocked.
- **Native Mobile SecureStore Integration:** Frontend SecureStore integration on Expo Native Mobile is **NOT COMPLETED**.
- **Activation Status:** Authentication endpoints remain guarded by `auth_endpoints_enabled = False` in deployment.

---

## 7. T-05 Protected Infrastructure Status

The T-05 DevSecOps baseline remains strictly preserved and unmodified:

- **Gate 1:** Approved by Codex Astra.
- **Gate 2 (Role Provisioning):** Completed & verified on staging branch `br-weathered-dust-bim5fibs`.
- **Gate 3 (Foundation Migration):** Completed & verified (`0001_foundation`).
- **Gate 4 (Sequence-Aware Table Grants):** Sequence-aware live baseline **VERIFIED COMPLIANT** with zero effective application-table privileges on 12 governed tables. Provisional technical pass issued by Gemini 4 Hydrogen. Independent Codex Astra review remains **PENDING**.
- **Gate 5 (Render Deployment):** **NOT STARTED**.
- **Pre-Gate-3 Recovery Snapshot:** `snap-wandering-flower-bi9ojapb` remains active and preserved.
- **Cloud State:** Zero cloud mutations, zero migrations, zero GRANT/REVOKE commands executed.
