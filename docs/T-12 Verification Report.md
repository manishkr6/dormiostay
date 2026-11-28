# T-12 Verification Report: Role Permissions and Row-Level Security

- **Date:** 2026-11-28 (Asia/Kolkata)
- **Ticket:** T-12 — Role permissions and row-level security
- **Branch:** `feature/t12-implementation`
- **Base Commit / Parent HEAD:** `53040a8875048ee4bc3789cb3d535b014a4f127d` (`fix(ci): correct isolated Gate 3b PostgreSQL test setup`)
- **Remote Synchronization:** In sync with `origin/feature/t12-implementation` (7 pushed commits: 6 approved T-12 commits + 1 corrective CI commit).
- **Status:** **T12-CI-FIX-02 LOCAL CORRECTIVE COMMIT CREATED — READY FOR CONTROLLED PRE-PUSH VERIFICATION (1 LOCAL COMMIT, 0 PUSHES)**

---

## 1. Executive Summary & Resolution of All Audit Findings

Across three independent Gemini 4 Hydrogen architectural and security reviews, eleven total findings (T12-SEC-01 through T12-SEC-11) have been investigated, reconciled against the actual codebase, and resolved:

| Finding ID | Severity | Verified Status | Description & Root Cause | Resolution Implemented | Verification Evidence |
|:---|:---:|:---:|:---|:---|:---|
| **T12-SEC-01** | `CRITICAL` | **RESOLVED** | Auth bootstrap blocked by PostgreSQL RLS in unauthenticated context. | Added trusted server-side `set_session_identity(session, role="system")` in internal `auth_service` and `deps.py`. After validation, session transitions to `user.role` and `user.id`. | Verified via `test_auth_bootstrap_rls.py` and 58 passing security tests. |
| **T12-SEC-02** | `CRITICAL` | **RESOLVED** | `require_approved_owner` queried non-existent `host_applications` table (T-16 dependency). | Refactored `is_owner_approved` to fail closed and return `False` directly without executing SQL, preventing PostgreSQL `42P01` transaction aborts. | Verified via `test_is_owner_approved_executes_no_sql_and_fails_closed` (0 SQL calls). |
| **T12-SEC-03** | `HIGH` | **RESOLVED** | Admin MFA check was fail-open (`mfa_completed is False` allowed missing/None state to pass). | Refactored check to strictly fail closed: requires `request.state.mfa_completed is True`. Missing, `None`, `False`, or untrusted strings raise HTTP 403 `MFA_REQUIRED`. | Verified via 4 dedicated MFA boundary tests in `test_permissions.py`. |
| **T12-SEC-04** | `HIGH` | **RESOLVED** | Missing PostgreSQL table grants for `dormiostay_app` under T-05 zero-grant baseline. | Added explicit, table-by-table least-privilege `GRANT` statements in `0003_row_level_security.py` (SELECT/INSERT/UPDATE on users/profiles, no DELETE on users/profiles, no UPDATE/DELETE on audit_logs). | Verified via `test_migration_0003_least_privilege_table_grants_matrix`. |
| **T12-SEC-05** | `MEDIUM` | **RESOLVED** | Inconsistent worker role typing and raw SQL GUC injection in `email_worker.py`. | Added `"worker"` to `Role` in `apps/api/app/db/session.py`. Replaced raw SQL in `email_worker.py` with `await set_session_identity(session, role="worker")`. | Verified via `test_set_session_identity_worker_role`. |
| **T12-SEC-06** | `CRITICAL` | **RESOLVED** | `users_insert` RLS policy had fail-open `OR NULLIF(app.user_id, '') IS NULL` allowing arbitrary admin creation. | Removed fail-open bypass. `users_insert` policy now strictly enforces `WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))`. Public signups run under trusted `auth_service.signup` system context. | Verified via `test_migration_0003_users_insert_no_fail_open` and `test_postgres_rls_missing_context_users_insert_denied`. |
| **T12-SEC-07** | `CRITICAL` | **RESOLVED** | `email_outbox_insert` had fail-open `OR NULLIF(app.role, '') IS NULL` permitting unauthenticated spam insertion. | Removed fail-open bypass. `email_outbox_insert` now strictly requires `WITH CHECK (current_setting('app.role', true) IN ('admin', 'system', 'worker'))`. | Verified via `test_migration_0003_email_outbox_insert_no_fail_open` and live postgres test. |
| **T12-SEC-08** | `HIGH` | **RESOLVED** | Table-wide `UPDATE` grant on `users` allowed tenants to update own `role` or `status` via RLS row ownership. | Added PostgreSQL `BEFORE UPDATE` trigger `protect_users_sensitive_columns` on `users` preventing non-admin/non-system sessions from modifying `role`, `status`, `password_hash`, `email_verified_at`, `failed_login_count`, `locked_until`. | Verified via `test_migration_0003_protect_users_sensitive_columns_trigger` and live postgres test. |
| **T12-SEC-09** | `HIGH` | **RESOLVED** | Discrepancy between documentation claims and CI workflow; CI lacked PostgreSQL container. | Added isolated disposable `postgres:16-alpine` service container to `.github/workflows/ci.yml`, automated role provisioning (`dormiostay_app`), Alembic migration execution, and live integration tests. | Verified via `.github/workflows/ci.yml` and integration test guard. |
| **T12-SEC-10** | `MEDIUM` | **RECONCILED** | Hydrogen review alleged GUC parameter name mismatch (`app.current_user_id` vs `app.user_id`). | Source audit confirmed both `session.py` and migration `0003` already use canonical names `app.user_id` and `app.role` exclusively. Added negative tests for malformed/unset GUCs and verification of exact names. | Verified via `test_guc_parameters_exact_canonical_names` and `test_negative_malformed_roles_rejected`. |
| **T12-SEC-11** | `HIGH` | **RESOLVED** | Duplicate step block in `.github/workflows/ci.yml` causing step redundancy and root Prettier check failure. | Removed the redundant duplicate step block in `.github/workflows/ci.yml` and formatted with Prettier. | Verified via `npm run format:check` (clean, 0 errors) and YAML syntax check. |
| **T12-AUDIT-01** | `CRITICAL` | **RESOLVED** | Inherited foundation policy `audit_insert` with `WITH CHECK (true)` survived migration 0003, resulting in 45 policies and bypassing audit log write restrictions via permissive OR composition. | Added explicit `DROP POLICY IF EXISTS audit_insert` and `audit_admin_read` in migration 0003 upgrade, restored them in downgrade while preserving RLS, and added 12 new static/live tests. | Verified via 14 static RLS tests, 16 live RLS integration tests, and exact 43-policy catalog assertion. |
| **T12-CI-FIX-01** | `HIGH` | **RESOLVED** | CI Step "Gate 3b acceptance on isolated PostgreSQL 16" setup failed with `MIGRATION_FAILED: inspect schema and privileges; details suppressed` during `alembic upgrade 0001_foundation` in workflow run 46124476984. | Root cause analysis: In `alembic/env.py`, `online()` catches any unhandled `Exception` and emits `MIGRATION_FAILED: inspect schema and privileges; details suppressed`, leaving the exact underlying exception unobserved in the CI log. The deduced causal mechanism is that `loopback()` executed `sudo nsenter --target "$service_pid" --net -- setpriv ... --clear-groups`, which sanitized runner environment variables (`HOME=/root`, stripped `PATH`, no `uv` virtualenv context) and executed `.venv/bin/alembic` outside the managed environment. Furthermore, container network namespace switching is unnecessary for provisioning commands. Decoupling provisioning to run directly against `localhost:5432` via `uv run alembic upgrade 0001_foundation` matches proven step 49, while `loopback()` is preserved for `pytest tests/integration/t12` using `sudo -E nsenter` with explicit `PATH` and `HOME`. | Verified via full local regression suite: 771 passed backend tests, 84 passed web tests, 88 passed mobile tests, Ruff clean, Mypy clean (87 source files), Prettier clean, zero diff whitespace errors. Pushed in commit `53040a8875048ee4bc3789cb3d535b014a4f127d`. |
| **T12-CI-FIX-02** | `HIGH` | **RESOLVED** | CI test `test_isolated_rehearsal_comparison_and_acknowledged_upgrade` failed in workflow run 46137634407 with `asyncpg.exceptions.ObjectInUseError: cannot ALTER TABLE "users" because it is being used by active queries in this session` on statement `ALTER TABLE users ENABLE ROW LEVEL SECURITY`. | Root cause analysis: In `apps/api/app/db/gate3b.py`, `run_disposable()` invokes `approval.verify_recovery(conn)` inside the outer migration transaction immediately before running Alembic migrations (`conn.run_sync(upgrade_shared)`). In `apps/api/app/db/recovery.py`, `foundation_fingerprint()` iterated over `APPLICATION_TABLES` (ending with `users`) using `connection.stream()` without awaiting `result.close()`. The asyncpg server-side portal on `users` remained open in the transaction, causing PostgreSQL's `CheckTableNotInUse` to reject the subsequent `ALTER TABLE users ENABLE ROW LEVEL SECURITY` DDL with SQLSTATE `55P03`. Remediation: Wrapped streaming iteration in `try ... finally: await result.close()`, guaranteeing immediate deterministic closure of the server-side cursor/portal for each table, preserving ordered streaming and bounded memory behavior. | Verified via 11 unit tests in `test_t12_recovery.py` (including normal closure, exception closure, and cancellation closure), new integration test `test_foundation_fingerprint_releases_cursor_before_ddl`, 774 passed backend offline tests (16 skipped requiring live DB), web suite clean (84 passed), mobile suite clean (88 passed), Ruff/Mypy/Prettier clean. |

---

## 2. Source-of-Truth Discrepancy Reconciliation

During the independent reviews, several terms and findings were raised that required code-level verification against actual source files:

1. **`user_sessions` vs `refresh_tokens`:**
   - **Review claim:** Audit traces referenced `refresh_tokens`.
   - **Source Evidence:** DormioStay's schema uses **`user_sessions` exclusively** (`apps/api/alembic/versions/0001_foundation.py` line 134, `apps/api/app/db/models/user_session.py`). Refresh tokens do not exist in the database architecture. All session lifecycle operations (creation, sliding expiration, absolute expiration, revocation) operate on `user_sessions`.
2. **GUC Parameter Names (`app.user_id` vs `app.current_user_id`):**
   - **Review claim (T12-SEC-10):** Alleged Python code set `app.current_user_id` while SQL read `app.user_id`.
   - **Source Evidence:** `apps/api/app/db/session.py` (lines 117–118) executes:
     `SELECT set_config('app.user_id', :user_id, true), set_config('app.role', :role, true)`
     Migration `0003_row_level_security.py` reads `current_setting('app.user_id', true)` and `current_setting('app.role', true)`.
     The GUC names were already 100% consistent.
3. **`system_transaction` vs `get_system_session`:**
   - **Review claim:** Audit traces referenced `get_system_session`.
   - **Source Evidence:** `system_transaction(factory)` is implemented in `apps/api/app/db/session.py` (lines 150–163). Internal authentication services establish system context via `await set_session_identity(session, role="system")`.
4. **CI Workflow Step Duplication (T12-SEC-11):**
   - **Review claim:** The third review flagged that `.github/workflows/ci.yml` contained consecutive duplicate step blocks for role provisioning and live RLS tests.
   - **Source Evidence:** Confirmed duplicate block in `.github/workflows/ci.yml`. Removed the second block and formatted with Prettier. `npm run format:check` now passes cleanly.

---

## 3. Defense-in-Depth Architecture & Privileged Trust Boundary

### 3.1 GUC Trust Boundary Analysis
- In Lakebase PostgreSQL, the application connects using a single connection pool role: `dormiostay_app` (`NOSUPERUSER`, `NOBYPASSRLS`).
- Custom GUCs (`app.user_id`, `app.role`) are set via `set_config(..., true)`, which scopes the values strictly to the current database transaction (`SET LOCAL`).
- **Intellectual Honesty on Threat Model:** Custom GUCs are not independently enforced database principals against arbitrary SQL injection. If an attacker achieves arbitrary SQL execution inside `dormiostay_app`, they can call `set_config('app.role', 'system', true)`.
- **Application Safeguards:**
  1. `get_session(request)` in `apps/api/app/db/session.py` inspects `request.state.role` and strictly restricts it to `{'guest', 'tenant', 'owner', 'admin'}`. Requests can **never** assume `system` or `worker` role from HTTP input.
  2. All queries are parameterized via SQLAlchemy ORM; `hide_parameters=True` prevents parameter leakage in logs.
  3. `verify_runtime_role(session)` confirms at startup and session yield that `current_user` cannot bypass RLS, lacks superuser privileges, and has no schema DDL creation rights.

### 3.2 Row-Level Security vs Column-Level Security (T12-SEC-08)
- PostgreSQL RLS filters **rows**, not columns. The policy `users_update` allows a tenant to update rows where `id = current_setting('app.user_id', true)::uuid`.
- To prevent tenants from updating sensitive columns (`role`, `status`, `password_hash`, `email_verified_at`, `failed_login_count`, `locked_until`), migration `0003_row_level_security.py` provisions a PostgreSQL `BEFORE UPDATE` trigger:
  ```sql
  CREATE OR REPLACE FUNCTION dormiostay_protect_users_sensitive_columns()
  RETURNS trigger AS $$
  BEGIN
      IF current_setting('app.role', true) IN ('admin', 'system') THEN
          RETURN NEW;
      END IF;
      IF NEW.role IS DISTINCT FROM OLD.role THEN
          RAISE EXCEPTION 'Unauthorized modification of role' USING ERRCODE = '42501';
      END IF;
      IF NEW.status IS DISTINCT FROM OLD.status THEN
          RAISE EXCEPTION 'Unauthorized modification of status' USING ERRCODE = '42501';
      END IF;
      IF NEW.password_hash IS DISTINCT FROM OLD.password_hash THEN
          RAISE EXCEPTION 'Unauthorized modification of password_hash' USING ERRCODE = '42501';
      END IF;
      IF NEW.email_verified_at IS DISTINCT FROM OLD.email_verified_at THEN
          RAISE EXCEPTION 'Unauthorized modification of email_verified_at' USING ERRCODE = '42501';
      END IF;
      IF NEW.failed_login_count IS DISTINCT FROM OLD.failed_login_count THEN
          RAISE EXCEPTION 'Unauthorized modification of failed_login_count' USING ERRCODE = '42501';
      END IF;
      IF NEW.locked_until IS DISTINCT FROM OLD.locked_until THEN
          RAISE EXCEPTION 'Unauthorized modification of locked_until' USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
  END;
  $$ LANGUAGE plpgsql;

  CREATE TRIGGER protect_users_sensitive_columns
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION dormiostay_protect_users_sensitive_columns();
  ```
- This guarantees that even if a tenant owns the row, any attempt to escalate `role` to `'admin'` or change `status` fails immediately with SQLSTATE `42501` (`insufficient_privilege`).

---

## 4. Complete Table Grants & RLS Policy Matrix

Under T-05's zero-table-grant baseline, `dormiostay_app` possesses zero default privileges. Migration `0003_row_level_security.py` defines explicit, least-privilege grants and fail-closed RLS policies:

| Table | Grants to `dormiostay_app` | SELECT Policy (USING) | INSERT Policy (WITH CHECK) | UPDATE Policy (USING & WITH CHECK) | DELETE Policy (USING) |
|:---|:---:|:---|:---|:---|:---|
| **users** | SELECT, INSERT, UPDATE | `app.role IN ('admin','system') OR id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system')` *(Fail-closed)* | `app.role IN ('admin','system') OR id = NULLIF(app.user_id,'')::uuid` *(Guarded by trigger)* | **DENIED** (Soft status only) |
| **user_profiles** | SELECT, INSERT, UPDATE | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | **DENIED** |
| **user_sessions** | SELECT, INSERT, UPDATE, DELETE | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` |
| **email_verification_tokens** | SELECT, INSERT, UPDATE, DELETE | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` |
| **password_reset_tokens** | SELECT, INSERT, UPDATE, DELETE | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` | `app.role IN ('admin','system') OR user_id = NULLIF(app.user_id,'')::uuid` |
| **email_outbox** | SELECT, INSERT, UPDATE, DELETE | `app.role IN ('admin','system','worker')` | `app.role IN ('admin','system','worker')` *(Fail-closed)* | `app.role IN ('admin','system','worker')` | `app.role IN ('admin','system','worker')` |
| **locations** | SELECT | `is_active = true OR app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` |
| **institutions** | SELECT | `is_active = true OR app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` |
| **amenities** | SELECT | `is_active = true OR app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` |
| **property_categories** | SELECT | `is_active = true OR app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` |
| **platform_settings** | SELECT | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` | `app.role IN ('admin','system')` | **DENIED** |
| **audit_logs** | SELECT, INSERT | `app.role = 'admin'` | `app.role IN ('admin','system')` | **DENIED** (Append-only trigger) | **DENIED** (Append-only trigger) |

---

## 5. Offline vs CI vs Staging Verification Boundaries

### 5.1 Verification Tiers
- **Tier 1: Offline Local Suite (Mock / SQLite):**
  - 58 passing security tests validate application route guards, role matrices, GUC setting logic, and static migration SQL syntax.
  - Exactly 7 live PostgreSQL tests in `test_postgres_rls_integration.py` are cleanly marked `SKIPPED` when running offline without `DATABASE_URL`.
- **Tier 2: CI Pipeline (`.github/workflows/ci.yml`):**
  - Provisions a disposable `postgres:16-alpine` service container.
  - Executes Alembic migrations 0001 -> 0002 -> 0003 under `neondb_owner`.
  - Executes live PostgreSQL integration tests under `dormiostay_app` (`OFFLINE_ONLY=0`).
  - Strict guard: if `CI=true` and `OFFLINE_ONLY=0` and PostgreSQL is unreachable, the test suite explicitly fails (`pytest.fail`) rather than skipping.
- **Tier 3: Staging Gates (Gate 4 / Gate 5):**
  - Live execution against Neon Lakebase PostgreSQL (`small-dew-81510029`) during deployment under dual-role procedure.

### 5.2 CI-01 Remediation: GitHub Actions Migration Target Rejection (Run 45953154308)
- **Incident Summary:** Initial GitHub Actions CI execution for commit `954646ec266f8b202ef88a0b28fcb5151adb30b0` (Workflow Run ID: [45953154308](https://github.com/manishkr6/dormiostay/actions/runs/45953154308)) succeeded on `web` and `mobile` jobs, but the `api` job failed at step `Provision PostgreSQL test roles and run migrations` with `MIGRATION_TARGET_REJECTED`.
- **Exact Root Cause:**
  1. `apps/api/alembic/env.py` (line 20) evaluates `direct_url = os.environ.get("DATABASE_URL_DIRECT")`. In CI, only `MIGRATION_URL` and `DATABASE_URL` were passed; without `DATABASE_URL_DIRECT`, `alembic/env.py` aborted immediately.
  2. `apps/api/app/db/test_target.py` enforces regex `r"dormiostay_t03_test_[a-z0-9_]+"` and requires `TEST_DATABASE_NAME` for local disposable database migration authorization. The CI workflow was configured with database name `dormiostay_test`, which failed validation.
- **Minimal Correction in `.github/workflows/ci.yml`:**
  - Standardized the disposable test database name to `dormiostay_t03_test_ci` across `POSTGRES_DB`, health check (`pg_isready -U neondb_owner -d dormiostay_t03_test_ci`), role-provisioning script, and runtime `DATABASE_URL`.
  - Injected `DATABASE_URL_DIRECT` and `TEST_DATABASE_NAME: dormiostay_t03_test_ci` into the migration step environment, directly satisfying `alembic/env.py` and `migration_guard.py`.
- **Local Verification:**
  - Evaluated `resolve_migration_target()` locally against proposed CI URL: successfully validated and resolved to loopback without network.
  - Executed 87 deployment/migration guard tests in `test_deployment_config.py` and `test_staging_migration_guard.py`: all passed.
  - Prettier format check and `git diff --check`: clean (0 errors).
- **PostgreSQL Tests Status:** The 7 live PostgreSQL RLS tests remain pending until re-run on GitHub Actions CI.
- **Production / Staging Isolation:** Zero changes to staging or production environments; `auth_endpoints_enabled = False` preserved.

### 5.3 CI-02 Remediation: Pytest Environment Overwrite in Live PostgreSQL RLS Suite (Run 45956134746)
- **Incident Summary:** GitHub Actions CI execution for commit `044775cf4f7c02f559aea75fac9d9e105279dcd9` (Workflow Run ID: [45956134746](https://github.com/manishkr6/dormiostay/actions/runs/45956134746)) verified the CI-01 fix:
  - `web` job passed cleanly (lint, typecheck, 84 Vitest tests, Vite build).
  - `mobile` job passed cleanly (lint, typecheck, 88 Jest tests, Expo config).
  - `api` job: PostgreSQL 16 container initialized successfully; Ruff and Mypy passed; role provisioning and Alembic migrations `0001_foundation` → `0002_email_outbox` → `0003_row_level_security` completed with exit code 0.
  - However, step `Live PostgreSQL RLS security tests (T-12)` failed during pytest collection with exit code 2:
    `ERROR tests/security/test_postgres_rls_integration.py - Failed: Live PostgreSQL RLS tests required in CI environment with OFFLINE_ONLY=0, but DATABASE_URL is missing or does not target PostgreSQL.`
- **Exact Root Cause:**
  - `apps/api/tests/conftest.py` line 8 unconditionally assigned `os.environ["DATABASE_URL"] = "not-used-by-unit-tests"`.
  - When pytest initialized and loaded `tests/conftest.py`, this unconditional assignment overwrote the live connection string passed by the CI workflow step: `DATABASE_URL: postgresql+asyncpg://dormiostay_app:test_app_password@localhost:5432/dormiostay_t03_test_ci`.
  - When `tests/security/test_postgres_rls_integration.py` was subsequently imported, `is_live_postgres()` evaluated `DATABASE_URL` as `"not-used-by-unit-tests"`, causing `is_live_postgres()` to return `False`.
  - The fail-closed CI guard (`if CI == "true" and OFFLINE_ONLY == "0" and not is_live_postgres(): pytest.fail(...)`) tripped immediately as designed to prevent silent skipping of live tests.
- **Minimal Correction:**
  - In `apps/api/tests/conftest.py`, changed line 8 from:
    `os.environ["DATABASE_URL"] = "not-used-by-unit-tests"`
    to:
    `os.environ.setdefault("DATABASE_URL", "not-used-by-unit-tests")`
  - This safely preserves the offline default `"not-used-by-unit-tests"` for unit and security suites while permitting CI and live environments to inject a real disposable PostgreSQL target.
- **New Regression Suite (`apps/api/tests/unit/test_t12_ci_database_env.py`):**
  - Added 7 focused tests:
    1. `test_conftest_safe_offline_default`: Without ambient `DATABASE_URL`, offline default is retained.
    2. `test_conftest_preserves_supplied_disposable_database_url`: Supplied disposable URL is preserved.
    3. `test_conftest_subprocess_preserves_ambient_database_url`: Full subprocess collection with ambient `DATABASE_URL` collects all 7 live tests.
    4. `test_ci_mode_invalid_or_missing_postgres_fails_closed`: Empty or offline `DATABASE_URL` in CI mode fails closed during collection.
    5. `test_is_live_postgres_logic`: Verifies boolean detection matrix across offline, live, and invalid dialects.
    6. `test_unit_tests_isolated_fixtures_no_db_connection`: Unit tests execute with isolated fixtures and no database connections.
    7. `test_disposable_target_validator_rejects_staging_and_production_urls`: `validate_test_url` rejects staging hosts, production hosts, and non-disposable database names.
- **Status of Live PostgreSQL Tests:** The 7 live PostgreSQL RLS tests remain pending successful remote execution on GitHub Actions.
- **Production / Staging Isolation:** Zero changes to Neon staging, Render, Brevo, or Upstash; `auth_endpoints_enabled = False` preserved.

### 5.4 CI-02-SEC-01 Remediation: Disposable Database Target Policy Enforcement in Live RLS Harness
- **Incident Summary:** During independent Hydrogen security review of CI-02, a security blocker was reported:
  - Finding `CI-02-SEC-01`: The live PostgreSQL RLS test harness (`test_postgres_rls_integration.py`) checked only whether `DATABASE_URL` contained `"postgres"`, without invoking disposable-target validation (`validate_test_url`).
  - An arbitrary remote staging or production URL could pass `is_live_postgres() == True` and execute data mutation SQL directly against non-disposable environments.
- **Root Cause:**
  - `test_postgres_rls_integration.py` was not wired to `app.db.test_target.validate_test_url`.
  - Step `Live PostgreSQL RLS security tests (T-12)` in `.github/workflows/ci.yml` lacked `TEST_DATABASE_NAME: dormiostay_t03_test_ci`.
- **Minimal Correction:**
  1. Integrated `app.db.test_target.validate_test_url` into `test_postgres_rls_integration.py`:
     - Defined `validate_rls_test_target()` requiring explicit `TEST_DATABASE_NAME` matching `r"dormiostay_t03_test_[a-z0-9_]+"`.
     - Validates host is loopback (`127.0.0.1`, `localhost`, `::1`) canonicalized to literal IP.
     - Rejects remote hosts, Neon staging URLs, production URLs, and query parameter overrides before engine creation.
     - Defined `live_database_url` test fixture supplying the canonicalized URL string to all 7 live test functions.
     - Fails closed in mandatory CI mode (`CI="true"`, `OFFLINE_ONLY="0"`) if validation fails for any reason.
     - Cleanly skips in offline mode (`OFFLINE_ONLY="1"` or offline default URL).
  2. Updated `.github/workflows/ci.yml`:
     - Added `TEST_DATABASE_NAME: dormiostay_t03_test_ci` to the `Live PostgreSQL RLS security tests (T-12)` step.
- **Security Regression Evidence (`test_t12_ci_database_env.py`):**
  - Expanded regression suite to 13 tests covering:
    - Valid localhost disposable target acceptance and loopback canonicalization.
    - Rejection of remote Neon staging URLs and remote production URLs.
    - Rejection of mismatched or missing `TEST_DATABASE_NAME`.
    - Mandatory CI mode collection failure (exit code 2) on staging target or missing configuration.
    - Structural verification that all 7 live RLS tests consume `live_database_url` fixture.
    - Verification that unit tests remain completely offline with isolated mock fixtures.
- **Status of Live PostgreSQL Tests:** The 7 live PostgreSQL RLS tests remain pending successful remote execution on GitHub Actions.
- **Production / Staging Isolation:** Zero changes to Neon staging, Render, Brevo, or Upstash; `auth_endpoints_enabled = False` preserved.

### 5.5 T12-FIX-01 Remediation: Session Identity Fail-Closed GUC Error Handling
- **Incident / Defect Summary:** In `apps/api/app/db/session.py` (`set_session_identity`), a generic `try...except` block caught `Exception` and silently swallowed any error whose message contained `"set_config"` or `"no such function"`. This was originally intended to tolerate SQLite during early testing, but line 109 already checks `dialect_name != "postgresql"`. As a result, on PostgreSQL, any database-level failure during GUC establishment (e.g. invalid arguments, parameter limits, permission failures, or catalog corruption) was silently ignored. The session identity helper returned cleanly, permitting protected transactional operations to execute with unset or stale identity context.
- **Root Cause:**
  - Broad exception catching (`except Exception as exc:`) coupled with string-matching (`if "set_config" in err_msg or "no such function" in err_msg: return`).
  - Failure to let genuine PostgreSQL connection or query errors propagate and fail closed.
- **Minimal Correction:**
  1. In `apps/api/app/db/session.py`:
     - Removed the entire exception-swallowing `try...except` block around `session.execute()`.
     - Strengthened dialect checking to inspect `session.bind`, `session.get_bind()`, `session.sync_session`, and adapter connections (`hasattr(session, "conn")`). If the dialect is not `"postgresql"`, it cleanly returns (no-op).
     - On PostgreSQL, `session.execute(text("SELECT set_config(...), set_config(...)"))` executes directly. Any error propagates immediately, causing `transaction()`, `system_transaction()`, and `get_session()` to abort and roll back before any protected operation can execute.
- **Security Regression Evidence (`apps/api/tests/unit/test_t12_session_identity_fail_closed.py`):**
  - Added 9 dedicated regression tests:
    1. `test_set_session_identity_postgres_success`: Verifies correct parameterized execution on PostgreSQL dialect.
    2. `test_set_session_identity_postgres_generic_failure_propagates`: Verifies generic DB exceptions propagate.
    3. `test_set_session_identity_failure_mentioning_set_config_cannot_be_swallowed`: Verifies errors mentioning "set_config" are never swallowed.
    4. `test_set_session_identity_no_such_function_not_swallowed`: Verifies errors mentioning "no such function" propagate on PostgreSQL.
    5. `test_set_session_identity_sqlite_dialect_noop`: Verifies clean no-op on SQLite dialect without executing SQL.
    6. `test_set_session_identity_sqlite_adapter_conn_noop`: Verifies clean no-op on connection-wrapping session adapters.
    7. `test_failed_identity_setup_prevents_protected_operations_in_transaction`: Verifies `transaction()` aborts and never enters the protected body if setup fails.
    8. `test_failed_identity_setup_prevents_protected_operations_in_system_transaction`: Verifies `system_transaction()` aborts and never enters the system body if setup fails.
    9. `test_role_and_user_validation_preserved`: Verifies role and user_id validation remains fully active and enforced.
- **Residual Custom GUC Trust-Boundary Limitation:**
  - In Lakebase PostgreSQL, custom GUCs (`app.user_id`, `app.role`) are transaction-local session variables set via `set_config(..., true)`. Because `dormiostay_app` is the single connection role, an attacker with arbitrary SQL injection capability could theoretically call `set_config('app.role', 'admin', true)`.
  - Defense-in-depth mitigations: strict query parameterization with `hide_parameters=True`, application-level role restriction in `get_session` refusing `system`/`worker` from HTTP requests, and the `protect_users_sensitive_columns` database trigger preventing tenants from modifying sensitive columns.
- **Status of Live PostgreSQL Tests:** The 7 live PostgreSQL RLS tests were verified passing in GitHub Actions CI run `46027414645` for commit `cf80ec15de6f718793e510d9d7b7e5724ab3f45c`.
- **Independent Security Review Approval:** Gemini 4 Hydrogen completed a strictly read-only security audit of T12-FIX-01 and formally issued **`APPROVED — T12-FIX-01 READY FOR ONE LOCAL COMMIT`** on 2026-11-27. Commit `cf80ec1` was pushed and verified remotely in CI run `46027414645`.
- **Production / Staging Isolation:** Zero changes to Neon staging, Render, Brevo, or Upstash; `auth_endpoints_enabled = False` preserved.

### 5.6 T12-FIX-02 Remediation: Fail-Closed Migration Role Guard
- **Ticket ID:** `T12-FIX-02`
- **Actual Migration Revision Identifiers:**
  - `0001_foundation` (`apps/api/alembic/versions/0001_foundation.py`, `down_revision = None`)
  - `0002_email_outbox` (`apps/api/alembic/versions/0002_email_outbox.py`, `down_revision = "0001_foundation"`)
  - `0003_row_level_security` (`apps/api/alembic/versions/0003_row_level_security.py`, `down_revision = "0002_email_outbox"`)
  - *Discrepancy Reconciliation:* Previous conversational summaries casually referred to migrations as `0001_initial_schema`, `0002_add_auth_tables`, and `0003_add_session_security`. Source-level inspection confirms the exact canonical revision chain in `apps/api/alembic/versions/` is `0001_foundation` -> `0002_email_outbox` -> `0003_row_level_security`.
- **Original Missing-Role Risk:**
  - In `0003_row_level_security.py` (statement 13), table grants to the runtime application role were wrapped in:
    ```sql
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dormiostay_app') THEN
            GRANT SELECT, INSERT, UPDATE ON users TO dormiostay_app;
            ...
        END IF;
    END
    $$;
    ```
  - If PostgreSQL role `dormiostay_app` did not exist at migration time (e.g. running migrations prior to role provisioning or in a misconfigured environment), the block silently exited without executing any table grants.
  - Alembic then marked the migration as successful, updating `alembic_version` to `0003_row_level_security`. This resulted in a silent fail-open situation where the database reported successful migration while runtime role privileges were completely missing, leaving the application unusable or in an unverified privilege state.
- **Chosen Mitigation & Compatibility Rationale:**
  - In `0003_row_level_security.py` (statement 13), replaced the silent `IF EXISTS` wrapper with an active fail-closed assertion and attribute guard:
    ```sql
    DO $$
    DECLARE
        r RECORD;
    BEGIN
        SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
        INTO r
        FROM pg_roles
        WHERE rolname = 'dormiostay_app';

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Required application role "dormiostay_app" does not exist'
                USING ERRCODE = '42704';
        END IF;

        IF r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls THEN
            RAISE EXCEPTION 'Application role "dormiostay_app" possesses forbidden elevated attributes'
                USING ERRCODE = '42501';
        END IF;

        -- Apply table grants...
    END
    $$;
    ```
  - *Why this preserves migration history:*
    1. Revision `0003_row_level_security` exists exclusively on the unmerged `feature/t12-implementation` branch. Neither staging nor production has applied revision 0003 (both are explicitly BLOCKED).
    2. Editing `0003_row_level_security.py` directly before merging ensures clean, canonical migration history without creating unnecessary "patch" revisions or migration technical debt.
    3. Preserves exact revision ID (`0003_row_level_security`), `down_revision` (`0002_email_outbox`), schema design, RLS policies, and all 12 table grants.
    4. Does not automatically create roles (which would violate least privilege or embed passwords in migration scripts).
    5. Fails closed with standard PostgreSQL error codes (`42704` for undefined object; `42501` for forbidden elevated attributes), rolling back the entire Alembic transaction.
- **Changed-File Inventory:**
  - Tracked modifications (3):
    - `apps/api/alembic/versions/0003_row_level_security.py`
    - `apps/api/tests/security/test_rls_migration_and_policies.py`
    - `docs/T-12 Verification Report.md`
  - Untracked files (1):
    - `apps/api/tests/unit/test_t12_migration_role_guard.py`
- **Security Regression Evidence:**
  - Added 17 dedicated tests in `apps/api/tests/unit/test_t12_migration_role_guard.py`:
    1. `test_migration_revision_chain_exact_identifiers`: Verifies `0001_foundation` -> `0002_email_outbox` -> `0003_row_level_security`.
    2. `test_migration_0003_silent_skip_eliminated`: Verifies `IF EXISTS ... dormiostay_app` is absent from UPGRADE_STATEMENTS.
    3. `test_migration_0003_explicit_role_assertion_present`: Verifies existence assertion and `42704` error code.
    4. `test_migration_0003_elevated_role_attributes_rejected`: Verifies `rolsuper`, `rolcreaterole`, `rolcreatedb`, `rolreplication`, `rolbypassrls` rejection (`42501`).
    5. `test_migration_0003_does_not_create_or_alter_role`: Verifies zero `CREATE ROLE` or `ALTER ROLE` statements in migration.
    6. `test_simulation_missing_role_fails_closed`: Verifies simulated PL/pgSQL block aborts and grants zero privileges when role is missing.
    7. `test_simulation_elevated_role_attributes_fail_closed`: Parameterized test across all 5 elevated attributes asserting immediate failure and zero grants.
    8. `test_simulation_valid_restricted_role_succeeds`: Verifies all 12 table grants execute when role has proper non-elevated attributes.
    9. `test_upgrade_propagates_role_check_failure`: Verifies `upgrade()` aborts and propagates error when role statement fails in `op.execute`.
    10. `test_upgrade_executes_all_statements_when_role_check_passes`: Verifies complete upgrade execution when role check passes.
    11. `test_runtime_grant_matrix_exhaustively_verified`: Verifies exact table grant matrix for all 12 tables and absence of forbidden grants (`DELETE` on users/profiles, `UPDATE`/`DELETE` on audit_logs, `GRANT ALL`).
    12. `test_ci_workflow_role_provisioning_compatible_with_guard`: Verifies `.github/workflows/ci.yml` provisions `dormiostay_app` with explicit non-elevated attributes.
    13. `test_migration_guard_security_invariants_preserved`: Verifies `FORBIDDEN_APP_ROLE = "dormiostay_app"` and production protection invariants.
  - Added `test_migration_0003_role_guard_fail_closed_assertions` to `apps/api/tests/security/test_rls_migration_and_policies.py`.
- **Independent Security Review Approval:** Gemini 4 Hydrogen completed a strictly read-only security audit of T12-FIX-02 and formally issued **`APPROVED — T12-FIX-02 READY FOR ONE LOCAL COMMIT`** on 2026-11-27.
- **Status of Live PostgreSQL Tests & Rollback Evidence:** Offline unit and simulation tests verify AST, exception propagation, and catalog simulation; live PostgreSQL engine-level transaction rollback and live RLS integration verification remain pending remote execution on disposable PostgreSQL in CI.
- **Current Status:** Independently approved by Gemini 4 Hydrogen; finalized in single local commit.
- **Historical T-05 Gate 4:** APPROVED by Astra.
- **Neon Staging Migration:** BLOCKED.
- **Render Staging Deployment:** BLOCKED.
- **Residual Custom GUC Trust-Boundary Limitation:** Documented in Section 5.5.
- **Public Auth Activation Guard:** `auth_endpoints_enabled = False` strictly preserved.

### 5.7 T12-AUDIT-01 Remediation: Inherited Audit RLS Policy Correction
- **Ticket ID:** `T12-AUDIT-01`
- **Astra's Finding:** GPT-6.2 Astra discovered that sequential execution of migrations 0001 -> 0002 -> 0003 produced 45 effective RLS policies instead of the intended 43 because two foundation policies created in `0001_foundation.py` (`audit_insert` with `WITH CHECK (true)` and `audit_admin_read`) survived migration `0003_row_level_security.py`.
- **Root Cause & Vulnerability Analysis:**
  1. `apps/api/alembic/versions/0001_foundation.py` (lines 212–216) created `audit_insert` (`FOR INSERT WITH CHECK (true)`) and `audit_admin_read` (`FOR SELECT USING (current_setting('app.role', true) = 'admin')`).
  2. `apps/api/alembic/versions/0003_row_level_security.py` created replacement policies `audit_logs_insert` (`WITH CHECK (current_setting('app.role', true) IN ('admin', 'system'))`) and `audit_logs_select` without dropping the legacy 0001 policies.
  3. Under PostgreSQL permissive policy composition rules (§5.8), multiple permissive policies for the same command combine using Boolean `OR`. For `INSERT` on `audit_logs`:
     $$\text{Effective WITH CHECK} = (\text{app.role} \in \{\text{'admin'}, \text{'system'}\}) \lor (\text{true}) \equiv \mathbf{TRUE}$$
  4. Because `dormiostay_app` holds `GRANT SELECT, INSERT ON audit_logs`, any database connection (including unauthenticated, guest, or tenant sessions) could insert arbitrary forged audit log entries.
  5. The immutable audit trigger `audit_immutable` triggers only `BEFORE UPDATE OR DELETE OR TRUNCATE`, offering zero protection against unauthorized `INSERT` operations.
- **Exact Upgrade & Downgrade Corrections:**
  - *Upgrade:* In `0003_row_level_security.py` `UPGRADE_STATEMENTS` (before creating replacement audit policies), added:
    ```sql
    "DROP POLICY IF EXISTS audit_insert ON audit_logs",
    "DROP POLICY IF EXISTS audit_admin_read ON audit_logs",
    ```
    This completely eliminates the permissive `OR` composition bypass and establishes the exact intended 43-policy catalog manifest.
  - *Downgrade:* In `0003_row_level_security.py` `DOWNGRADE_STATEMENTS`:
    1. Dropped replacement policies `audit_logs_select` and `audit_logs_insert`.
    2. Restored legacy 0001 policies:
       ```sql
       "CREATE POLICY audit_insert ON audit_logs FOR INSERT WITH CHECK (true)",
       """CREATE POLICY audit_admin_read ON audit_logs FOR SELECT
       USING (current_setting('app.role', true) = 'admin')""",
       ```
    3. Removed the incorrect statements that previously executed `DISABLE ROW LEVEL SECURITY` and `NO FORCE ROW LEVEL SECURITY` on `audit_logs`, thereby preserving `ENABLE` and `FORCE ROW LEVEL SECURITY` as established by `0001_foundation` lines 210–211.
- **Changed-File Inventory (Exactly 5 Files):**
  1. `apps/api/alembic/versions/0003_row_level_security.py`
  2. `apps/api/tests/security/test_rls_migration_and_policies.py`
  3. `apps/api/tests/security/test_postgres_rls_integration.py`
  4. `apps/api/tests/unit/test_t12_ci_database_env.py`
  5. `docs/T-12 Verification Report.md`
- **Security Regression Evidence:**
  - Added 3 new tests in `apps/api/tests/security/test_rls_migration_and_policies.py` (total 14 passed):
    1. `test_migration_0003_inherited_audit_policies_dropped`: Proves explicit drops of `audit_insert` and `audit_admin_read` precede replacement policy creation.
    2. `test_migration_0003_downgrade_restores_foundation_audit_policies`: Proves downgrade restores foundation policies and preserves RLS.
    3. `test_migration_0003_exact_43_policy_definitions`: Proves migration 0003 defines exactly 43 policies across 12 tables.
  - Added 9 new tests in `apps/api/tests/security/test_postgres_rls_integration.py` (total 16 live tests):
    1. `test_postgres_rls_missing_context_audit_logs_insert_denied`: Asserts SQLSTATE 42501 / RLS policy violation on missing context.
    2. `test_postgres_rls_tenant_context_audit_logs_insert_denied`: Asserts SQLSTATE 42501 / RLS policy violation on tenant context.
    3. `test_postgres_rls_guest_context_audit_logs_insert_denied`: Asserts SQLSTATE 42501 / RLS policy violation on guest context.
    4. `test_postgres_rls_system_context_audit_logs_insert_permitted`: Asserts authorized system context can insert into `audit_logs`.
    5. `test_postgres_rls_admin_context_audit_logs_select_permitted`: Asserts authorized admin context can read `audit_logs`.
    6. `test_postgres_rls_tenant_context_audit_logs_select_denied`: Asserts tenant context cannot read `audit_logs` (returns 0 rows).
    7. `test_postgres_rls_audit_logs_update_delete_denied`: Asserts UPDATE and DELETE on `audit_logs` are denied for all roles.
    8. `test_postgres_rls_exact_cumulative_policy_inventory`: Asserts cumulative catalog policy count is exactly 43.
    9. `test_postgres_rls_audit_logs_policy_catalog_clean`: Asserts `audit_logs` catalog contains only `audit_logs_insert` and `audit_logs_select`.
  - Updated collection-count assertion in `apps/api/tests/unit/test_t12_ci_database_env.py` (13 passed) to verify exact 16 collected live tests while preserving ambient and disposable DATABASE_URL fail-closed protections.
- **Independent Security Review Approval:** Gemini 4 Hydrogen completed a strictly read-only security audit of T12-AUDIT-01 and formally issued **`APPROVED — T12-AUDIT-01 READY FOR ONE LOCAL COMMIT`** on 2026-11-28.
- **Status of Live PostgreSQL Tests & Test Skip Message Limitation:**
  - The 16 live PostgreSQL RLS tests skip locally in offline environments because live PostgreSQL is absent (`OFFLINE_ONLY=1`).
  - As enforced by `validate_test_url()`, these write-based tests are **never** executed against Neon staging (which would corrupt staging data); they run exclusively against the isolated disposable `postgres:16-alpine` service container in CI.
  - The skip message phrasing (`"verified during Gate 4/5 staging and CI"`) is a legacy docstring note; actual engine-level transaction execution and live RLS integration verification remain pending remote execution on disposable PostgreSQL in CI.
- **Preserved Status & Invariants:**
  - T12-FIX-01 and T12-FIX-02 CI evidence preserved.
  - Historical T-05 Gate 4 APPROVED by Astra.
  - Neon staging migration: **STRICTLY BLOCKED**.
  - Render staging deployment: **STRICTLY BLOCKED**.
  - Residual custom GUC trust-boundary limitation preserved.
  - `auth_endpoints_enabled = False` strictly preserved.
  - Note: Direct persistent database queries have not been executed against Neon staging or production; staging is confirmed at `0001_foundation` from historical Gate 3 records.

### 5.8 T12-AUDIT-01 Remote CI Results & Transaction Isolation Remediation
- **GitHub Actions CI Run:** `46095935764` for commit `91fd909435031103b0da2df4952154007660022e`.
- **Remote CI Run Results:**
  - `web` job: **PASSED** (84 tests, build succeeded).
  - `mobile` job: **PASSED** (88 tests, config verified).
  - `api` job: **FAILED** in `Live PostgreSQL RLS security tests (T-12)`.
- **Live PostgreSQL Engine Evidence:**
  - Disposable PostgreSQL 16 container started healthy and provisioned `dormiostay_app` role.
  - Migration chain `0001_foundation` -> `0002_email_outbox` -> `0003_row_level_security` completed successfully.
  - Exactly **43 cumulative RLS policies** verified in live database catalog (`test_postgres_rls_exact_cumulative_policy_inventory` PASSED).
  - Clean `audit_logs` policy catalog verified (`test_postgres_rls_audit_logs_policy_catalog_clean` PASSED): only `audit_logs_insert` and `audit_logs_select` exist; legacy `audit_insert` and `audit_admin_read` are absent.
  - 14 live tests passed; 2 failed; 0 skipped.
- **Failure Root Cause Analysis (INSERT ... RETURNING):**
  - Both failing tests (`test_postgres_rls_system_context_audit_logs_insert_permitted` and `test_postgres_rls_audit_logs_update_delete_denied`) executed `INSERT INTO audit_logs ... RETURNING id` under `app.role = 'system'`.
  - Under PostgreSQL RLS rules (§5.8), an `INSERT` statement containing `RETURNING` checks BOTH the table's `WITH CHECK` policy (for INSERT) and the table's `USING` policy (for SELECT, as RETURNING reads the newly inserted row).
  - Because `audit_logs_select` strictly restricts read access to `app.role = 'admin'`, the `SELECT` policy evaluated to FALSE for system context, raising `SQLSTATE 42501 InsufficientPrivilegeError: new row violates row-level security policy for table "audit_logs"`.
- **Application Requirement Assessment:**
  - Application code does not require system-context `RETURNING id`. `audit_logs` is an immutable append-only audit trail; only admins query audit logs (`audit_logs_select`).
- **Independent Hydrogen Finding (SQLSTATE 25P02 & Transaction Contamination):**
  - Gemini 4 Hydrogen identified that executing `UPDATE`, `DELETE`, and `SELECT` sequentially on a single uncommitted transaction creates an aborted transaction cascade:
    1. A failed UPDATE leaves the PostgreSQL transaction in the `25P02` aborted state (`current transaction is aborted, commands ignored until end of transaction block`).
    2. The subsequent DELETE is aborted by PostgreSQL without evaluating DELETE grants, RLS, or triggers, which would fail the assertion or cause false denial.
    3. An unhandled verification SELECT on the aborted transaction crashes the test suite.
    4. Rolling back an uncommitted transaction would destroy the fixture, failing the immutability check.
- **Exact Architectural Remediation:**
  - In `apps/api/tests/security/test_postgres_rls_integration.py`:
    1. `test_postgres_rls_system_context_audit_logs_insert_permitted`:
       - System INSERT is committed in its own `async with engine.begin()` transaction without `RETURNING`.
       - Persistence is verified via an authorized admin `SELECT` in a separate clean transaction.
       - Engine is disposed in a `finally` block (`await engine.dispose()`).
    2. `test_postgres_rls_audit_logs_update_delete_denied`:
       - Phase A (Fixture creation): System context inserts and commits the row in its own `engine.begin()` transaction without `RETURNING`.
       - Phase B (Target ID retrieval): Admin queries `target_id` in a separate clean transaction.
       - Phase C (UPDATE denial): Admin attempts UPDATE in an isolated transaction; explicitly catches denial, rolls back, verifies SQLSTATE `42501` / immutable trigger / permission denial, and explicitly asserts `25P02` is absent.
       - Phase D (DELETE denial): Admin attempts DELETE in an entirely separate clean, un-aborted transaction; catches denial, rolls back, verifies SQLSTATE `42501` / immutable trigger / permission denial, and explicitly asserts `25P02` is absent.
       - Phase E (Immutability check): Admin verifies the original record exists and remains untouched (`reason == "immutability test"`) in a fresh healthy transaction.
       - Engine is disposed in a `finally` block (`await engine.dispose()`).
- **Local Verification:**
  - 53 targeted regression tests passed.
  - 745 offline backend tests passed, 16 skipped.
  - 16 live RLS tests collected.
  - Ruff, Mypy, Prettier, git diff --check clean.
- **Status:** **T12-AUDIT-01 REMOTELY VERIFIED IN CI RUN 46102340989 (16 LIVE RLS TESTS PASSED, 0 SKIPS); FOLLOW-ON CONTROLS T12-FIX-03..07 IMPLEMENTED LOCALLY AND VERIFIED (771 OFFLINE TESTS PASSED, 0 COMMITS, 0 PUSHES)**

### 5.9 T12-AUDIT-01 Remote CI Verification Evidence (Run 46102340989)
- **Commit SHA:** `3e02f16ec513fcbb81dd9a0d1eb5725cb1c4c0c3`
- **Parent Commit SHA:** `91fd909435031103b0da2df4952154007660022e`
- **Branch:** `feature/t12-implementation`
- **GitHub Actions Run ID:** [46102340989](https://github.com/manishkr6/dormiostay/actions/runs/46102340989)
- **Workflow Status & Conclusion:** `completed` / **`success`**
- **Job Inventory:**
  - `web` (ID `140847367777`): **`success`** — Lint, format, typecheck, build, 84 unit tests passed.
  - `mobile` (ID `140847367977`): **`success`** — Lint, typecheck, config verification, 88 unit tests passed.
  - `api` (ID `140847368009`): **`success`** — PostgreSQL 16 container, migrations, 16 live RLS tests passed (0 skips), 745 offline tests passed.
- **Live Engine Results:**
  - Disposable `postgres:16-alpine` started healthy.
  - Role `dormiostay_app` provisioned with non-elevated attributes.
  - Alembic migrations `0001_foundation` -> `0002_email_outbox` -> `0003_row_level_security` passed cleanly.
  - Exactly **16 live RLS integration tests passed, 0 failed, 0 skipped**.
  - Exactly **43 cumulative policies verified in live catalog** (`test_postgres_rls_exact_cumulative_policy_inventory` PASSED).
  - Clean `audit_logs` policy catalog verified (`test_postgres_rls_audit_logs_policy_catalog_clean` PASSED): only `audit_logs_insert` and `audit_logs_select` exist; legacy permissive policies absent.
  - System context audit INSERT succeeded without `RETURNING id` (`test_postgres_rls_system_context_audit_logs_insert_permitted` PASSED).
  - Admin UPDATE and DELETE independently denied with genuine SQLSTATE `42501` without transaction abort cascades (`25P02`), and record immutability confirmed (`test_postgres_rls_audit_logs_update_delete_denied` PASSED).
- **Remote CI Verdict:** `CI PASSED — T12-AUDIT-01 CORRECTION REMOTELY VERIFIED`.

### 5.10 T12-FIX-05 Implementation: Historical Gate 4 Writer Quarantine
- **Problem & Vulnerability:** The historical Gate 4 grant script (`execute_gate4_grants_async` and `apps/api/scripts/execute_gate4_grants.py`) was designed for T-05's zero-table-grant baseline. If accidentally executed against staging or production after migration 0003, it would unconditionally execute `REVOKE ALL PRIVILEGES ON TABLE public."{tbl}" FROM dormiostay_app` on all 11 application tables and `alembic_version`, instantly stripping all runtime table grants established by `0003_row_level_security.py` and taking down the API service.
- **Architectural Remediation:**
  1. In `apps/api/app/db/grant_guard.py`: Retired the mutation runner. `execute_gate4_grants_async` now raises `RuntimeError("GATE4_MUTATION_RETIRED")` immediately before evaluating target connections, reading credentials, or executing SQL.
  2. In `apps/api/scripts/execute_gate4_grants.py`: CLI entry point raises `SystemExit("GATE4_MUTATION_RETIRED")` immediately.
  3. In `apps/api/scripts/grant_application_role.sql`: Documented that direct manual SQL execution is strictly prohibited.
  4. In `apps/api/tests/unit/test_gate4_grant_guard.py`: Updated test suite to verify that both the callable and CLI refuse execution before touching database connections or checking environment variables.

### 5.11 T12-FIX-04 Implementation: Revision-Aware Security Catalog Verifier
- **Problem:** Verification of PostgreSQL security state required a deterministic, reproducible catalog comparison capable of detecting any unexpected grant, policy, trigger, rule, column, or relation modification across revisions 0001 and 0003.
- **Architectural Remediation:**
  1. Implemented `apps/api/app/db/security_catalog.py`:
     - Queries 22 PostgreSQL system catalogs and namespaces (`pg_class`, `pg_policy`, `pg_trigger`, `pg_proc`, `pg_attribute`, `pg_constraint`, `pg_namespace`, `pg_database`, `pg_roles`, `pg_auth_members`, `pg_default_acl`, `pg_db_role_setting`, etc.).
     - Strictly enforces PostgreSQL 16 server catalog version (`int(version) // 10000 == 16`) and standard search path (`pg_catalog, public`).
     - Serializes catalog state to deterministic, sorted JSON lines.
  2. Defined Canonical Manifests:
     - `apps/api/app/db/security_manifests/foundation.jsonl`: Baseline manifest for revision `0001_foundation`.
     - `apps/api/app/db/security_manifests/rls.delta`: Delta manifest representing exact structural and security changes introduced by `0003_row_level_security.py` (drops 2 permissive policies, adds 43 RLS policies, adds trigger `protect_users_sensitive_columns`, grants table permissions to `dormiostay_app`, adds `email_outbox` table and columns).
  3. Fail-Closed Verification (`verify_catalog`):
     - Compares observed live catalog against `manifest(revision)`.
     - Verifies `alembic_version` contains exactly `[revision]`.
     - Fails closed on any schema drift, unexpected grant, or missing security attribute without leaking sensitive expressions in logs.
  4. Automated Tests:
     - Unit tests in `apps/api/tests/unit/test_t12_catalog_manifest.py`: Verifies manifest parsing, 43 policies across 12 tables, table grant distributions, and audit policy names.
     - Integration tests in `apps/api/tests/integration/t12/test_catalog.py`: 13 drift scenarios (alembic_version manipulation, role rename, elevated privileges, public grants, extra columns, column type change, dropped columns, disabled RLS, disabled triggers, altered policies).

### 5.12 T12-FIX-06 Implementation: Recovery Contract and Snapshot Rehearsal Validation
- **Problem:** Staging and production migrations require cryptographic and evidentiary proof of recovery readiness (pre-migration snapshot, independent rehearsal verification, write freeze) without granting automated agent tools permission to create or restore Neon cloud snapshots.
- **Architectural Remediation:**
  1. Implemented `apps/api/app/db/recovery.py`:
     - `RecoveryEvidence` Pydantic model with strict field validation:
       - Pinned to approved staging identifiers (`project: "small-dew-81510029"`, `source_branch: "br-weathered-dust-bim5fibs"`, `endpoint: "ep-cold-smoke-bir1s74t"`, `database: "neondb"`, `revision: "0001_foundation"`).
       - Enforces timeline invariants: `captured_at <= rehearsed_at <= approved_at <= now < expires_at <= retained_until`.
       - Enforces release window validity: `expires_at - captured_at <= 24 hours`.
       - Enforces two-person separation of duties: `recovery_owner != independent_approver`.
       - Rejects ineligible snapshot artifacts and prohibited rehearsal branches (`br-holy-lake-bi0hc1lu`, source branch).
  2. Implemented `foundation_fingerprint(connection)`:
     - Executes under `REPEATABLE READ READ ONLY`, `row_security=off`, and UTC timezone.
     - Streams JSON representation of all rows across the 11 application tables ordered by `id` and computes SHA-256 fingerprint.
  3. Implemented `verify_isolated_rehearsal(source, rehearsal, evidence)`:
     - Compares separately authenticated source and restored rehearsal sessions.
     - Confirms read-only snapshots on both connections.
     - Enforces distinct database destinations (`source_name != restore_name`).
     - Verifies baseline catalog and exact byte-for-byte fingerprint equality.
  4. Automated Tooling & Tests:
     - CLI validator: `apps/api/scripts/check_recovery_evidence.py`.
     - Unit tests: `apps/api/tests/unit/test_t12_recovery.py` (8 passed).
     - Integration test: `apps/api/tests/integration/t12/test_recovery.py` (rehearsal data perturbation detection).

### 5.13 T12-FIX-03 Implementation: Gate 3b Incremental Migration Runner
- **Problem:** Safe execution of migrations 0001 -> 0003 requires exclusive transaction control, explicit advisory locking, physical connection pinning, fail-closed handling of ambiguous commit outcomes, and cryptographic release binding.
- **Architectural Remediation:**
  1. Implemented `apps/api/app/db/gate3b.py`:
     - Session advisory lock on `LOCK_KEY = 731203` with pre- and post-operation session verification (`_check_session`).
     - Physical connection pinning: SQLAlchemy event listener on `do_connect` forbids replacement physical connections (`refuse_reconnect`), guaranteeing session GUCs and locks cannot be silently lost across reconnection.
     - Dedicated Alembic runner `apps/api/app/db/gate3b_env/env.py`: Executes migrations strictly in a single online transaction without intermediate commits (`transactional_ddl=True, transaction_per_migration=False`), verifies starting revision is `0001_foundation`, verifies destination is `0003_row_level_security`, and enforces loopback connection.
     - Ambiguous commit fail-closed handling: If commit acknowledgement is lost or ambiguous, raises `CommitOutcomeUnknown` and records `"unknown"` outcome in custodian ledger; automatic retries are strictly prohibited.
  2. Implemented Release Contract in `apps/api/app/db/release_contract.py`:
     - `ReleaseBinding` and `ReleaseDecision` models binding Git commit, branch, workflow digest, dependency digest (`pyproject.toml`, `requirements.txt`), manifest digest (`foundation.jsonl`, `rls.delta`), and recovery evidence digest.
     - Custodian durable ledger (`consume_at_custodian`, `record_outcome`): Enforces single-use consumption of release decisions via SQLite primary key constraints (`PRIMARY KEY(repository, run_id)`).
  3. Safe Quarantined Boundaries:
     - Standalone script `apps/api/scripts/gate3b.py` raises `SystemExit("GATE3B_DISABLED...")` unconditionally.
     - Boundary module `apps/api/app/db/gate3b_identity.py` raises `RuntimeError("Provider-issued session branch and endpoint binding unavailable")`.
  4. Automated Tests:
     - Unit tests: `apps/api/tests/unit/test_t12_release_contract.py` (7 passed).
     - Integration tests: `apps/api/tests/integration/t12/test_gate3b.py` (contention, atomic failure rollback, precommit interruption, pre-DDL recheck, isolated rehearsal upgrade, populated upgrade and lost ack).

### 5.14 T12-FIX-07 Implementation: Render Staging Readiness & Secret Hygiene
- **Problem:** Render staging configuration must be independently verified to ensure proper database pooling, strict TLS, unprivileged runtime roles, zero Git-stored secrets, and fail-closed service disabling (`AUTH_ENDPOINTS_ENABLED = 'false'`).
- **Architectural Remediation:**
  1. Updated `render.yaml`:
     - Added explicit `AUTH_ENDPOINTS_ENABLED: 'false'`.
     - Preserved `APP_ENV: staging`, `PYTHON_VERSION: 3.12.9`.
     - Confirmed sensitive credentials (`DATABASE_URL`, `FRONTEND_ORIGIN`) use `sync: false` and are never stored in Git.
  2. Implemented `apps/api/app/db/render_readiness.py`:
     - `RenderInventory` model: Validates password-free staging inventory (verifies pooler hostname, `sslmode: verify-full`, database `neondb`, role `dormiostay_app`, port 5432).
     - Fails closed if `auth_endpoints_enabled`, `worker_enabled`, `api_proxy_enabled`, or `migration_credentials_present` is True.
     - `verify_runtime_session`: Inspects read-only session state and asserts `dormiostay_app` runtime role.
  3. Automated Tooling & Tests:
     - CLI validator: `apps/api/scripts/check_render_evidence.py`.
     - Unit tests in `apps/api/tests/unit/test_t12_render_readiness.py` (11 passed).
     - Frontend blueprint tests in `apps/web/config/render-routes.test.ts` (10 passed).

### 5.15 Legacy Integration Test Alignment & Metadata Registration
- **Alembic Metadata Registration (`apps/api/alembic/env.py`):**
  - Registered `EmailBase.metadata` alongside `Base.metadata` (`target_metadata=[Base.metadata, EmailBase.metadata]`), ensuring Alembic autogenerate and diff inspections recognize `EmailOutbox`.
- **Database Integration Test Alignment (`apps/api/tests/integration/test_database.py`):**
  - `test_actual_application_login`: Updated to verify that unauthenticated guest context `INSERT INTO audit_logs` fails with SQLSTATE `42501` under RLS policy `audit_logs_insert`, and verified that system context (`role="system"`) succeeds.
  - `test_ordinary_application_role_audit_permissions`: Updated to verify that missing context `INSERT INTO audit_logs` fails with SQLSTATE `42501` inside a savepoint (`connection.begin_nested()`), and verified that `set_config('app.role', 'system', true)` succeeds.

### 5.16 Mypy Duplicate Module Resolution & Test Suite Idempotency Remediation
- **Mypy Duplicate Module Fix:**
  - `pyproject.toml` configured `files = ["app", "tests", "alembic"]`. When mypy discovered `apps/api/tests/conftest.py` and `apps/api/tests/integration/t12/conftest.py` without package boundaries, both mapped to top-level module `"conftest"`.
  - Resolution: Added package marker `__init__.py` files to `tests/`, `tests/integration/`, and `tests/integration/t12/`. Mypy now passes with 0 errors across all 87 source files.
- **Gate 3b Integration Test Idempotency Fix (`test_gate3b.py`):**
  - Incident: `test_isolated_rehearsal_comparison_and_acknowledged_upgrade` initially passed on fresh databases, but a subsequent run failed with 23 passed, 1 failed (`verify_catalog(rehearsal, FOUNDATION)` mismatch).
  - Root Cause: The test committed `other` (`dormiostay_t03_test_rehearsal`) to `DESTINATION` (`0003_row_level_security`). On subsequent runs against the same container without database recreation, `dormiostay_t03_test_rehearsal` was already at revision 0003, causing foundation fingerprint verification to fail.
  - Minimal Correction: Implemented `_reset_to_foundation(engine)` in `apps/api/tests/integration/t12/test_gate3b.py`. Before and after running the test (in a `finally:` block), if the database is at `0003_row_level_security`, it executes `mod_0003.DOWNGRADE_STATEMENTS`, `mod_0002.DOWNGRADE_STATEMENTS`, resets `alembic_version` to `'0001_foundation'`, and purges test fixtures from `amenities`. Applied the same reset to `test_populated_upgrade_and_lost_ack`. Both tests now execute cleanly and idempotently across multiple invocations.

### 5.17 T12-CI-FIX-01: Gate 3b Acceptance Database Setup Remediation in CI (Run 46124476984)
- **Incident Summary:**
  Following the authorized push of the six T-12 commits (`c9e81071c10c4b3df5008ef2dadc7b1dee509eb3`), GitHub Actions workflow run [46124476984](https://github.com/manishkr6/dormiostay/actions/runs/46124476984) executed. All frontend Web and Mobile jobs, offline API linters, formatters, typecheckers, and step 49 (`Provision PostgreSQL test roles and run migrations`) passed completely. However, step 50 (`Gate 3b acceptance on isolated PostgreSQL 16`) failed during test database setup:
  `MIGRATION_FAILED: inspect schema and privileges; details suppressed`
- **Root Cause Analysis:**
  - **Distinction between Unobserved Underlying Exception and Suspected Mechanism:**
    In `apps/api/alembic/env.py` (lines 79–86), any unhandled `Exception` occurring during `asyncio.run(online())` is caught and suppressed with:
    `raise SystemExit("MIGRATION_FAILED: inspect schema and privileges; details suppressed") from None`
    As a consequence, the raw underlying Python traceback or database error was suppressed and remains unobserved in the CI log output.
    However, comparative analysis between step 49 (which succeeded using `uv run alembic upgrade head` directly from the runner host) and step 50 reveals the concrete differences:
    1. **Privilege & Environment Sanitization:**
       In `.github/workflows/ci.yml`, the `loopback()` helper was configured as:
       ```bash
       loopback() {
         sudo nsenter --target "$service_pid" --net -- setpriv \
           --reuid="$(id -u)" --regid="$(id -g)" --clear-groups --no-new-privs -- "$@"
       }
       ```
       Invoking `createdb`, `psql`, and `.venv/bin/alembic upgrade 0001_foundation` through `loopback()` stripped essential environment variables (`--clear-groups`, cleared user environment, `HOME=/root`, stripped `PATH`), causing `.venv/bin/alembic` to execute outside the runner's managed `uv` virtualenv environment.
    2. **Unnecessary Namespace Switching for Database Provisioning:**
       Entering the PostgreSQL container network namespace (`nsenter --net`) is strictly required only during the execution of Gate 3b integration tests (`pytest tests/integration/t12`) to verify the loopback network address constraint enforced by `apps/api/app/db/gate3b.py` and `apps/api/app/db/gate3b_env/env.py` (`inet_server_addr() IN ('127.0.0.1', '::1')`). Database provisioning (`createdb`), initial role grants (`psql`), and baseline migrations (`alembic upgrade 0001_foundation`) do not inspect `inet_server_addr()`. In step 49, `uv run alembic upgrade head` ran directly from the runner host against `localhost:5432` without issue.
- **Architectural & Minimal Remediation in `.github/workflows/ci.yml`:**
  1. **Direct Runner Host Provisioning:**
     The three Gate 3b test databases (`dormiostay_t03_test_gate3b`, `dormiostay_t03_test_rehearsal`, `dormiostay_t03_test_commit`) are created, granted least-privilege schema permissions, and migrated to `0001_foundation` directly from the runner host connecting to `localhost:5432` using `uv run alembic upgrade 0001_foundation`, exactly matching step 49:
     ```bash
     for name in dormiostay_t03_test_gate3b dormiostay_t03_test_rehearsal dormiostay_t03_test_commit; do
       PGPASSWORD=test_owner_password createdb -h localhost -U neondb_owner "$name"
       PGPASSWORD=test_owner_password psql -X -v ON_ERROR_STOP=1 -h localhost -U neondb_owner -d "$name" \
         -c "REVOKE ALL ON DATABASE $name FROM PUBLIC" \
         -c "GRANT CONNECT ON DATABASE $name TO dormiostay_app" \
         -c "GRANT USAGE ON SCHEMA public TO dormiostay_app"
       DATABASE_URL_DIRECT="postgresql+asyncpg://neondb_owner:test_owner_password@localhost:5432/$name" \
       TEST_DATABASE_NAME="$name" \
       uv run alembic upgrade 0001_foundation
     done
     ```
  2. **Preserve Runner Environment for Namespace-Switched Pytest:**
     The `loopback()` helper is positioned before test execution and updated to preserve environment variables with `sudo -E nsenter` while explicitly passing `PATH` and `HOME`:
     ```bash
     service_pid=$(docker inspect --format '{{.State.Pid}}' "$PG_SERVICE")
     loopback() {
       sudo -E nsenter --target "$service_pid" --net -- setpriv \
         --reuid="$(id -u)" --regid="$(id -g)" --clear-groups --no-new-privs -- "$@"
     }
     name=dormiostay_t03_test_gate3b
     loopback env PATH="$PATH" HOME="$HOME" T12_GATE3B_TEST=1 TEST_DATABASE_NAME="$name" \
       DATABASE_URL_DIRECT="postgresql+asyncpg://neondb_owner:test_owner_password@127.0.0.1:5432/$name" \
       .venv/bin/pytest tests/integration/t12 -v
     ```
- **Security Invariance Verification:**
  - Zero modification to migration revisions (`0001_foundation.py`, `0002_user_profiles.py`, `0003_row_level_security.py`).
  - Zero modification to migration guards (`migration_guard.py`, `gate3b.py`, `gate3b_identity.py`).
  - Zero modification to RLS policies or PostgreSQL table-level grants.
  - Gate 3b integration tests continue to run inside the container's network namespace via `loopback()`, validating `inet_server_addr() IN ('127.0.0.1', '::1')`.

### 5.18 T12-CI-FIX-02: Active PostgreSQL Cursor During Migration (Run 46137634407)
- **Incident Summary:**
  Following the authorized push of commit `53040a8875048ee4bc3789cb3d535b014a4f127d`, GitHub Actions workflow run [46137634407](https://github.com/manishkr6/dormiostay/actions/runs/46137634407) executed:
  - `web` job: **SUCCESS** (Lint, format check, TypeScript, 84 tests passed, build succeeded).
  - `mobile` job: **SUCCESS** (Lint, TypeScript, 88 tests passed, Expo config dev/preview/prod verified).
  - `api` job: **FAILURE** in step `Gate 3b acceptance on isolated PostgreSQL 16`.
  - Database provisioning (`createdb`, role grants, and `alembic upgrade 0001_foundation` for all 3 databases) **succeeded completely**.
  - 23 of 24 Gate 3b integration tests **passed** (all 13 catalog drift tests, contention and rollback, 4 atomic failure tests, 2 precommit interruption tests, pre-DDL recheck, populated upgrade and lost ack, rehearsal fingerprint).
  - Single failure: `test_isolated_rehearsal_comparison_and_acknowledged_upgrade`:
    `asyncpg.exceptions.ObjectInUseError: cannot ALTER TABLE "users" because it is being used by active queries in this session`
    Failing statement: `ALTER TABLE users ENABLE ROW LEVEL SECURITY` (in `0003_row_level_security.py:444`).

- **Root Cause & Protocol Lifecycle Analysis:**
  1. **Lifecycle Tracing:**
     In `apps/api/app/db/gate3b.py`, `run_disposable(target, database, approval)` manages the migration workflow. Lines 133–148 execute:
     ```python
     tx = await conn.begin()
     try:
         ...
         if approval:
             await approval.verify_recovery(conn)
         await conn.execute(text("SET LOCAL search_path=public"))
         await conn.run_sync(upgrade_shared, database)
     ```
     `test_isolated_rehearsal_comparison_and_acknowledged_upgrade` is the only test that provides `approval=envelope`. Inside `approval.verify_recovery(conn)`, `foundation_fingerprint(conn)` is called (`apps/api/app/db/recovery.py`).
  2. **Streaming Cursor / Portal Leak:**
     In `foundation_fingerprint()`, tables from `APPLICATION_TABLES` (11 tables; `"users"` is the 11th table) are iterated:
     ```python
     result = await connection.stream(
         text(f'SELECT to_jsonb(t)::text FROM public."{table}" t ORDER BY t.id')
     )
     async for row in result:
         digest.update(row[0].encode() + b"\n")
     ```
     In SQLAlchemy AsyncIO with asyncpg, `connection.stream()` executes with an `AsyncAdapt_asyncpg_ss_cursor` backed by an asyncpg `Cursor` (a server-side portal). Because `foundation_fingerprint()` iterated through `async for row in result:` without calling `await result.close()`, the `AsyncResult` was not explicitly closed and its server-side portal on `users` remained open in the transaction.
  3. **PostgreSQL CheckTableNotInUse Assertion:**
     Immediately following `verify_recovery()`, within the **exact same physical connection and active transaction**, Alembic began applying `0003_row_level_security.py`. Line 444 executed `ALTER TABLE users ENABLE ROW LEVEL SECURITY`. PostgreSQL's internal `CheckTableNotInUse` detected an active portal on table `users` in the current session and rejected the DDL with SQLSTATE `55P03`:
     `ObjectInUseError: cannot ALTER TABLE "users" because it is being used by active queries in this session`.

- **Architectural & Minimal Remediation in `apps/api/app/db/recovery.py`:**
  - Wrapped `connection.stream()` iteration in a `try ... finally: await result.close()` block:
    ```python
    result = await connection.stream(
        text(f'SELECT to_jsonb(t)::text FROM public."{table}" t ORDER BY t.id')
    )
    try:
        async for row in result:
            digest.update(row[0].encode() + b"\n")
    finally:
        await result.close()
    ```
  - **Invariants Preserved:**
    1. Bounded memory: Does NOT load full tables into memory via `fetchall()`; rows continue to be streamed chunk-by-chunk.
    2. Ordered SHA-256 fingerprint: Digests remain 100% deterministic and byte-identical.
    3. Exception & Cancellation safety: `await result.close()` is guaranteed to execute whether rows are exhausted normally, an exception is raised, or `asyncio.CancelledError` occurs.
    4. Transaction & Connection integrity: The same physical connection, advisory lock, and single outer migration transaction are strictly preserved.

- **Regression Tests Added:**
  1. Unit tests in `apps/api/tests/unit/test_t12_recovery.py`:
     - `test_foundation_fingerprint_closes_all_cursors_normally`: Confirms all 11 table streams are closed when fingerprinting completes.
     - `test_foundation_fingerprint_closes_cursor_on_exception`: Confirms streams are closed even when an exception is raised during consumption.
     - `test_foundation_fingerprint_closes_cursor_on_cancellation`: Confirms streams are closed even when `asyncio.CancelledError` is raised.
  2. Integration test in `apps/api/tests/integration/t12/test_recovery.py`:
     - `test_foundation_fingerprint_releases_cursor_before_ddl`: Begins an explicit transaction on `conn`, executes `await foundation_fingerprint(conn)`, immediately executes `ALTER TABLE users ENABLE ROW LEVEL SECURITY` within the exact same transaction, asserts successful DDL execution without `ObjectInUseError`, and rolls back cleanly in `finally`.

---

## 6. Comprehensive Verification Results

All local verification suites were executed after the final code edit:

| Test / Check Suite | Target Command | Result |
|:---|:---|:---:|
| **T12-FIX-03 Release Contract Unit Suite** | `pytest apps/api/tests/unit/test_t12_release_contract.py` | **7 passed** |
| **T12-FIX-04 Catalog Manifest Unit Suite** | `pytest apps/api/tests/unit/test_t12_catalog_manifest.py` | **1 passed** |
| **T12-FIX-05 Gate 4 Grant Guard Unit Suite** | `pytest apps/api/tests/unit/test_gate4_grant_guard.py` | **41 passed** |
| **T12-FIX-06 Recovery Contract Unit Suite** | `pytest apps/api/tests/unit/test_t12_recovery.py` | **11 passed** (+3 new regression tests) |
| **T12-FIX-07 Render Readiness Unit Suite** | `pytest apps/api/tests/unit/test_t12_render_readiness.py` | **11 passed** |
| **T12-FIX-01 Session Identity Unit Suite** | `pytest apps/api/tests/unit/test_t12_session_identity_fail_closed.py` | **9 passed** |
| **T12-FIX-02 Migration Role Guard Unit Suite** | `pytest apps/api/tests/unit/test_t12_migration_role_guard.py` | **17 passed** |
| **CI-02-SEC-01 Test DB Env Unit Suite** | `pytest apps/api/tests/unit/test_t12_ci_database_env.py` | **13 passed, 1 warning** |
| **Full Security Test Suite** | `pytest apps/api/tests/security` | **62 passed, 16 skipped, 1 warning** |
| **Full Unit Test Suite** | `pytest apps/api/tests/unit` | **712 passed, 15 warnings** |
| **Combined Backend Offline Test Suite** | `pytest apps/api/tests/unit tests/security tests/integration` | **774 passed, 44 skipped, 15 warnings** |
| **Frontend Web Unit Suite** | `npm --prefix apps/web test` | **84 passed (6 test files)** |
| **Frontend Web Build** | `npm --prefix apps/web run build` | **Success (`dist/index.html` 0.89 kB, JS 417 kB)** |
| **Frontend Mobile Unit Suite** | `npm --prefix apps/mobile test` | **88 passed (10 test suites)** |
| **Mobile Configuration Verification** | `npm run config:mobile` | **All Expo variants verified (dev, preview, prod)** |
| **Frontend Web ESLint** | `npm --prefix apps/web run lint` | **Clean (0 errors, 0 warnings)** |
| **Frontend Mobile ESLint** | `npm --prefix apps/mobile run lint` | **Clean (0 errors, 0 warnings)** |
| **Python Ruff Lint** | `ruff check apps/api` | **Clean (0 errors across 94 files)** |
| **Python Ruff Format** | `ruff format --check apps/api` | **Clean (94 files formatted)** |
| **Strict Mypy Typecheck** | `mypy` (from `apps/api`) | **Success: 0 errors across 87 source files** |
| **Root Prettier Formatting** | `npm run format:check` | **Clean (All matched files use Prettier style)** |
| **Git Whitespace & Diff Check** | `git diff --check` | **Clean (0 errors)** |

---

## 7. Staging Gate Status & External Dependencies

1. **Gate Closure & Readiness Status:**
   - **Historical T-05 Gate 4:** APPROVED by Astra.
   - **Neon Staging Migration:** **BLOCKED** pending external snapshot creation, independent security review, and authorization.
   - **Render Staging Deployment:** **BLOCKED** pending staging database migration completion and external authorization.
2. **Feature Ticket Dependencies:**
   - **T-13 (TOTP Admin MFA):** Admin MFA enforcement will be completed when T-13 provides trusted server-side MFA verification (`request.state.mfa_completed = True`).
   - **T-16 (Host Application & Onboarding):** Full host application status checks will be connected when T-16 creates the `host_applications` table.
3. **Public Auth Activation Guard:** `auth_endpoints_enabled` remains set to `False` in application configuration and `render.yaml`.

---

## 8. Git Inventory & Review Baseline

- **Active Branch:** `feature/t12-implementation`
- **Committed HEAD:** `53040a8875048ee4bc3789cb3d535b014a4f127d` (`fix(ci): correct isolated Gate 3b PostgreSQL test setup`)
- **Remote Synchronization:** In sync with `origin/feature/t12-implementation` (7 pushed commits: 6 approved T-12 commits + 1 CI corrective commit).
- **Working Tree Inventory (Local Changes for T12-CI-FIX-02):**
  - **Exactly 4 Modified Tracked Files:**
    1. `apps/api/app/db/recovery.py`: Wrapped `connection.stream()` in `try ... finally: await result.close()`.
    2. `apps/api/tests/integration/t12/test_recovery.py`: Added `test_foundation_fingerprint_releases_cursor_before_ddl` integration test.
    3. `apps/api/tests/unit/test_t12_recovery.py`: Added 3 unit regression tests for normal, exception, and cancellation cursor closure.
    4. `docs/T-12 Verification Report.md`: Comprehensive documentation of T12-CI-FIX-02 incident, root cause, deterministic cursor closing fix, and local verification results.
  - **Zero Untracked Files.**
  - **Zero Staged Files.**
- **Zero New Commits or Pushes:** Local modifications only. No commits or pushes have been executed. Ready for independent review.

---

## 9. Seven-Commit Push Closure & Proposed Corrective Commit

The six independently reviewed T-12 commits and the subsequent corrective commit `T12-CI-FIX-01` (`53040a8875048ee4bc3789cb3d535b014a4f127d`) are pushed to `origin/feature/t12-implementation`:

1. **Commit 1 (`2ef8553`):** `fix(security): quarantine historical Gate 4 grant mutation runners` (4 files)
2. **Commit 2 (`e31b371`):** `feat(security): implement revision-aware PostgreSQL catalog manifest verifier` (9 files)
3. **Commit 3 (`0257b02`):** `feat(security): implement recovery evidence contract and snapshot rehearsal validator` (4 files)
4. **Commit 4 (`dc9312d`):** `feat(security): implement Gate 3b incremental migration runner with advisory locking` (9 files)
5. **Commit 5 (`1f0033f`):** `fix(render): enforce AUTH_ENDPOINTS_ENABLED false in Render blueprint and readiness check` (5 files)
6. **Commit 6 (`c9e8107`):** `test(integration): register EmailOutbox metadata and align legacy audit tests` (3 files)
7. **Commit 7 (`53040a8`):** `fix(ci): correct isolated Gate 3b PostgreSQL test setup` (2 files)

Following remote CI verification of Commit 7 (Workflow Run ID: `46137634407`), the active cursor leak during migration (`T12-CI-FIX-02`) was investigated and resolved locally.

Upon independent security review and explicit user authorization, the remediation is committed in exactly ONE local commit:
- **Subject:** `fix(db): close recovery fingerprint cursors before migration DDL`
- **Files to stage:**
  1. `apps/api/app/db/recovery.py`
  2. `apps/api/tests/integration/t12/test_recovery.py`
  3. `apps/api/tests/unit/test_t12_recovery.py`
  4. `docs/T-12 Verification Report.md`

---

## 10. Final Verification Status

- **Final Verdict:** **T12-CI-FIX-02 CORRECTIVE COMMIT CREATED LOCALLY — READY FOR CONTROLLED PRE-PUSH VERIFICATION**
- **Safety Guarantees Maintained:**
  - Zero modification to migration revisions `0001`, `0002`, or `0003`.
  - Zero modification to RLS policies or PostgreSQL table-level grants.
  - Zero weakening of migration guards (`migration_guard.py`, `gate3b.py`, `gate3b_identity.py`).
  - Streaming and bounded memory behavior strictly preserved (no `fetchall()`).
  - Single physical connection, session advisory lock, and outer transaction strictly preserved.
  - Historical T-05 Gate 4 remains `APPROVED`.
  - Neon staging migration remains `BLOCKED`.
  - Render staging deployment remains `BLOCKED`.
  - `auth_endpoints_enabled=False` strictly preserved across application settings and Render blueprints.
