# T-05 Verification Report — Render Deployment & Environments

**Date:** 2026-11-22 (Asia/Kolkata)
**Ticket:** T-05 (Render Deployment and Environments)
**Base Commit:** `2483a01abb25ded26841a94da13cee6701d3b0c2` (`style(web): update DormioStay branding assets`)
**Overall Status: APPROVED BY CODEX ASTRA FOR CONTROLLED STAGING HANDOFF (LOCAL ONLY — LIVE DEPLOYMENT PENDING)**

> [!IMPORTANT]
> **Deployment Authorization Notice:**
> In strict compliance with project safety guidelines, all infrastructure configurations, build commands, routing rules, fail-closed migration guards, startup probes, environment matrices, and automated validation tests have been verified locally. **No live cloud infrastructure (Render services, Neon databases, or EAS builds) has been provisioned, no live migrations have been run, and no Git commits or pushes have been performed.** Live cloud provisioning remains **PENDING explicit user approval**.

---

## 1. Codex Astra Review Blockers Resolution Log

Codex Astra completed focused re-reviews and confirmed that all security blockers have been resolved. On 2026-11-22, Codex Astra formally issued **APPROVED FOR CONTROLLED STAGING HANDOFF**, validating the local implementation while live staging deployment remains pending.

| Blocker | Finding | Resolution Status | Verified Implementation |
|---|---|---|---|
| **BLOCKER 1: Replaceable staging approval** | `migration_guard.py` read approved staging identity from ordinary runtime environment variables. A caller could replace approval values alongside the database URL without independent Neon verification. | **FIXED** | Adopted safest security policy: **ALL remote database migrations are disabled** until independently verified staging infrastructure is available. Caller-controlled `APPROVED_STAGING_*` environment variables cannot authorize remote execution. Remote targets fail closed immediately with `ValueError` and `SystemExit("MIGRATION_TARGET_REJECTED")` before any SQL or DDL can be produced. |
| **BLOCKER 2: Alembic downgrade bypass** | `MIGRATION_ACTION=upgrade` could override actual CLI downgrade intent. Programmatic `command.downgrade()` was classified as an upgrade. Destructive `DROP TABLE` SQL was generated in offline mode. `process_revision_directives` was not a reliable execution-time downgrade guard. | **FIXED** | Implemented fail-safe action detection across CLI arguments, programmatic `EnvironmentContext.context_opts` (`fn=downgrade`, `destination_rev`), and configuration. Detected downgrades can never be overridden by `MIGRATION_ACTION=upgrade`. Upfront target rejection in `env.py` and `migration_guard.py` blocks execution before `context.configure()` or `context.run_migrations()`, guaranteeing zero DDL/SQL output on rejection. |
| **BLOCKER 3: Command-level regression tests** | Existing tests mostly passed action strings into helper functions; they failed to detect actual Alembic CLI and programmatic bypasses. Previously misreported as 13 tests (verified baseline was 11 existing command-level tests). | **FIXED** | Verified the exact baseline of **11 existing command-level tests** and added new focused tests for DNS resolution and canonical loopback handling. All tests directly exercise `alembic.command.upgrade()`, `alembic.command.downgrade()`, and `CommandLine().main()` entry points. Output buffers (`sys.stdout`/`sys.stderr`) are asserted to contain zero DDL on rejection. |
| **BLOCKER 4: Localhost DNS resolution bypass** | An approved disposable database URL using `localhost:5433` could be redirected to a remote IP (`203.0.113.17`) via a mocked DNS resolver, causing Alembic to attempt a socket connection to that remote address resulting in `MIGRATION_FAILED` rather than target rejection. | **FIXED** | In `apps/api/app/db/test_target.py` and `apps/api/alembic/env.py`, all accepted `localhost` targets are canonicalized to a literal loopback IP (`127.0.0.1`) before connection. Both online and offline Alembic executions consume this validated canonical `target`. When DNS resolves `localhost` to a remote IP, no remote socket connection is attempted and the migration connection uses literal `127.0.0.1`. Direct remote IP targets and connection override parameters are rejected. |

---

## 2. Executive Summary

| Verification Category | Status | Details |
|---|---|---|
| **Local Implementation Verification** | **PASS** | All infrastructure blueprints, build scripts, migration guards, route configs, startup probes, and test suites are 100% verified locally. |
| **Independent Security Review** | **PASS** | Codex Astra approved for controlled staging handoff (local implementation only). |
| **Live Staging Deployment** | **PENDING** | Actual creation of Render services, Neon staging branch, and EAS cloud builds is deferred pending explicit user approval. |
| **Active Web API Proxy** | **BLOCKED** | Disabled in `render.yaml` pending live staging API provisioning and destination verification. |
| **All Remote Alembic Migrations** | **DISABLED** | **All remote migrations are disabled, including upgrades, downgrades and forward fixes, until a separately reviewed enabling change is explicitly authorized.** |
| **Local Disposable Test Migrations**| **PERMITTED** | Allowed for integration testing under existing T-03 safeguards (`TEST_DATABASE_NAME=dormiostay_t03_test_*` on canonical loopback). |
| **Neon Staging DB Provisioning** | **PENDING** | Not provisioned; deferred until user approval. |
| **Independent Neon Verification** | **PENDING** | Requires a separately reviewed, controlled mechanism (not caller-controlled env vars). |
| **Web Test & Build Suite** | **PASS** | 6 test suites, 82 passed tests (0 failures); ESLint 0 warnings; TypeScript strict 0 errors; Vite production build succeeded (`dist/`). |
| **Backend Test & Quality Suite** | **PASS** | **73 passed, 4 skipped** (disposable database tests); Ruff check passed; Ruff format check 100% clean; Mypy strict 0 errors across 28 files. |
| **Mobile Test & Quality Suite** | **PASS** | 10 test suites, 88 passed tests (0 failures); `config:mobile` passed across all 3 variants; ESLint 0 warnings; TypeScript strict 0 errors. |
| **Formatting & Whitespace Audit** | **PASS** | Prettier 100% clean across workspace; `git diff --check` returned 0 whitespace errors. |

---

## 3. Test Command Results & Evidence

### 3.1 Web Test Suite (Vitest)
```text
✓ config/render-routes.test.ts (10 tests)
✓ src/components/seo/PageHead.test.tsx (1 test)
✓ src/components/ui/startup-probe.test.tsx (4 tests)
✓ src/lib/api-client.test.tsx (32 tests)
✓ src/components/ui/ui.test.tsx (19 tests)
✓ src/app/App.test.tsx (16 tests)

Test Files: 6 passed, 6 total
Tests:      82 passed, 82 total
Time:       1.47s
```

### 3.2 Web Production Build (Vite)
```text
tsc --noEmit && vite build
✓ 2269 modules transformed.
dist/index.html                   0.89 kB │ gzip:   0.44 kB
dist/assets/index-Bf_4dLqG.css   25.91 kB │ gzip:   6.14 kB
dist/assets/index-CMCZs3Jf.js   417.46 kB │ gzip: 131.38 kB
✓ built in 141ms
```

### 3.3 Backend Test Suite (Pytest)
```text
tests/integration/test_database.py ssss (4 skipped: requires disposable DB)
tests/unit/test_bootstrap.py . (1 test)
tests/unit/test_deployment_config.py ......................................... (41 tests)
tests/unit/test_models.py . (1 test)
tests/unit/test_sentry_security.py . (1 test)
tests/unit/test_t02.py ........ (8 tests)
tests/unit/test_t03_security.py ..................... (21 tests)

Total: 73 passed, 4 skipped in 0.41s
```

### 3.4 Backend Static Analysis & Formatting
- **Ruff Lint:** `uv run --no-sync ruff check .` -> `All checks passed!`
- **Ruff Format:** `uv run --no-sync ruff format --check .` -> `28 files already formatted`
- **Mypy Strict:** `uv run --no-sync mypy` -> `Success: no issues found in 28 source files`

### 3.5 Mobile Test Suite (Jest)
```text
PASS src/__tests__/components.test.tsx (19 tests)
PASS src/__tests__/api-client.test.ts (10 tests)
PASS src/__tests__/telemetry.test.ts (10 tests)
PASS src/__tests__/showcase.test.tsx (8 tests)
PASS src/__tests__/with-ios-scene-delegate.test.ts (3 tests)
PASS src/__tests__/session-store.test.ts (7 tests)
PASS src/__tests__/form-validation.test.ts (5 tests)
PASS src/__tests__/tokens.test.ts (4 tests)
PASS src/__tests__/linking.test.ts (10 tests)
PASS src/__tests__/config.test.ts (12 tests)

Test Suites: 10 passed, 10 total
Tests:       88 passed, 88 total
Time:        1.028 s
```

### 3.6 Mobile Configuration Matrix Check (`check-config.cjs`)
```text
✓ Expo config verified for variant: development (app.dormiostay.mobile.development)
✓ Expo config verified for variant: preview (app.dormiostay.mobile.preview)
✓ Expo config verified for variant: production (app.dormiostay.mobile)
✓ EAS build profiles and environment isolation verified.
All Expo configuration checks passed.
```

### 3.7 Workspace Lint, Types & Prettier
- **Web Lint:** `npm run lint` -> ESLint passed with 0 warnings, 0 errors.
- **Web Types:** `npm run typecheck` -> TypeScript strict passed with 0 errors.
- **Mobile Lint:** `npm run lint:mobile` -> ESLint passed with 0 warnings, 0 errors.
- **Mobile Types:** `npm run typecheck:mobile` -> TypeScript strict passed with 0 errors.
- **Prettier Format Check:** `npm run format:check` -> `All matched files use Prettier code style!`
- **Git Whitespace Check:** `git diff --check` -> 0 errors.

---

## 4. Exact Safe-to-Commit File Manifest

### 4.1 New Files (8 files)
1. `render.yaml` — Root Render Blueprint specification (`plan: free`, `branch: main`, `APP_ENV: staging`, SPA fallback, unverified proxy disabled).
2. `apps/api/requirements.txt` — Pinned production dependencies for Python 3.12 on Render.
3. `apps/api/app/db/migration_guard.py` — Authoritative identity resolver, action detector, and fail-closed migration guard.
4. `apps/api/tests/unit/test_deployment_config.py` — Blueprint, requirements, probes, and migration guard CLI/programmatic regression tests (41 tests).
5. `apps/web/src/components/ui/startup-probe.tsx` — Web startup readiness probe component.
6. `apps/web/src/components/ui/startup-probe.test.tsx` — Vitest tests for startup probe and waking banner interaction.
7. `docs/deployment-and-environments.md` — 12-section deployment, environments, and migration safety guide.
8. `docs/T-05 Verification Report.md` — This verification report.

### 4.2 Modified Files (12 files)
9. `README.md` — Updated Render deployment documentation reference.
10. `apps/api/alembic/env.py` — Integrated fail-closed `migration_guard` with canonical URL consumption and downgrade rejection.
11. `apps/api/app/api/v1/router.py` — Registered `health_router` under `/api/v1`.
12. `apps/api/app/db/test_target.py` — Loopback host canonicalization to literal `127.0.0.1` and `::1`, rejecting multi-host and Unix socket overrides.
13. `apps/mobile/app.config.ts` — Deferred active `associatedDomains` and `intentFilters`.
14. `apps/mobile/eas.json` — Explicit build profiles with environment-isolated API bases.
15. `apps/mobile/scripts/check-config.cjs` — Updated assertions matching deferred linking declarations.
16. `apps/mobile/src/__tests__/config.test.ts` — Profile isolation and SecureStore partition tests.
17. `apps/mobile/src/__tests__/linking.test.ts` — Allowlisted HTTPS link handling and route security tests.
18. `apps/mobile/src/lib/linking.ts` — Allowlisted HTTPS domain handling, parameter stripping, and route validation.
19. `apps/web/config/render-routes.test.ts` — Blueprint assertions (SPA fallback active, unverified proxy rewrite disabled).
20. `apps/web/src/app/providers.tsx` — Mounted `<StartupProbe />` in top-level `Providers`.

**Total Scope:** Exactly **20 files** (8 new, 12 modified). Zero unrelated files modified.

---

## 5. Security & Verification Policy Summary

1. **Proxy Destination Safety:**
   - Active proxy forwarding in `render.yaml` is disabled until the staging API is provisioned and its assigned URL is verified.
   - Guessing hostnames is prevented; cookies and Authorization headers are protected from accidental leakage.
2. **Neon Migration Identity & Operation Safety:**
   - **All remote migrations are disabled, including upgrades, downgrades and forward fixes, until a separately reviewed enabling change is explicitly authorized.**
   - Remote staging upgrades, remote staging downgrades, remote production upgrades, remote production downgrades, and remote offline SQL generation are strictly rejected.
   - Self-declared `APPROVED_STAGING_*` variables cannot authorize remote execution.
   - `MIGRATION_ACTION=upgrade` cannot bypass downgrade protection; programmatic `command.downgrade()` and CLI downgrade arguments are accurately detected.
   - Rejection occurs before any SQL or DDL is produced.
   - Local disposable test databases (`TEST_DATABASE_NAME=dormiostay_t03_test_*`) remain fully operational for automated test runs.
3. **Localhost Resolution & Transport Safety:**
   - Accepted `localhost` targets are canonicalized to literal loopback IP (`127.0.0.1`) before connection.
   - Both online and offline Alembic executions consume this validated canonical `target`.
   - Mocked DNS mapping `localhost` to a remote IP cannot redirect connections; no remote socket connection is attempted.
   - Safe TLS configuration via `sslmode=require` (or `ssl=require`) is explicitly preserved and supported under T-03 rules. Target parameter overrides (e.g. `host=`, `port=`, `database=`, `dsn=`, `service=`, `server_settings=`) and conflicting/duplicate TLS parameters (e.g. `ssl=disable&ssl=require`, `sslmode=require&ssl=require`) are strictly rejected.
4. **Current Infrastructure Status:**
   - Local disposable test migrations: **PERMITTED**.
   - All remote Alembic migrations: **DISABLED**.
   - Neon staging database provisioning: **PENDING**.
   - Independent Neon identity verification: **PENDING**.
   - Future staging migration activation: **REQUIRES a separately reviewed, controlled mechanism**.
   - Render API destination verification and proxy activation: **PENDING**.
   - Live staging deployment: **PENDING**.
   - Final Astra approval for local staging handoff: **APPROVED** (Live staging deployment remains PENDING).

---

## 6. Final Status

**Status: APPROVED BY CODEX ASTRA FOR CONTROLLED STAGING HANDOFF**
Codex Astra has formally approved the local implementation for controlled staging handoff. The localhost security blocker and documentation inaccuracies are resolved with regression unit tests, strict typechecking, clean formatting, and reconciled documentation. All live cloud actions (Render service provisioning, Neon staging branch creation, remote database migrations, and proxy activation) remain strictly **PENDING explicit user authorization**.
