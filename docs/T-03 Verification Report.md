# T-03 verification report

Final verification: 2026-11-17. **PASS for the five requested local security fixes.**
Scope: T-03. No commit, push or T-04 work.

## Restricted application role

Created dormiostay_app as a dedicated LOGIN with NOSUPERUSER, NOBYPASSRLS,
NOCREATEDB, NOCREATEROLE and NOREPLICATION. Read-only verification returned false
for every privileged flag, zero memberships and zero public-table ownerships.

The ignored apps/api/.env has mode 0600. DATABASE_URL uses dormiostay_app;
DATABASE_URL_DIRECT uses dormiostay_local_admin. Credentials were generated
locally, were not printed, and were not added to source or Git.

Startup, readiness and request sessions verify the actual connected role:
elevated flags, memberships, database/table ownership and public-schema CREATE
permission cause rejection. Actual application startup with the restricted
local credentials succeeded; /healthz and /readyz both returned HTTP 200.
The bootstrap administrator is retained for administration and migration use.

The grant script rejects elevated memberships/ownership, grants ordinary table
operations and only SELECT/INSERT on audit_logs. It ran successfully against
the approved disposable database using the actual application role.
Tests with that login prove INSERT works, guest SELECT sees no audit rows,
trusted admin context can SELECT, and UPDATE/DELETE/TRUNCATE privileges and
operations are denied. Tests also prove the bootstrap role fails runtime checks.

Development public tables remain absent (zero tables): the existing development
schema was not migrated. Application-role table privileges were verified in the
disposable database; applying a future development migration/grants remains a
separately authorized action.

## Database target safeguards

Both tests and Alembic require TEST_DATABASE_NAME to exactly identify an approved
local database named dormiostay_t03_test_<suffix>. They reject development/system
database names, remote hosts, missing approval and mismatched names.

Database URLs allow only scalar TLS options (ssl or sslmode, not both).
Database/host/user/password/DSN overrides, encoded overrides and repeated options
are rejected before connecting. The connected current_database() must match
the approved name before DDL. The test fixture additionally rejects nonempty
public schemas before upgrade and never falls back to DATABASE_URL.

Alembic uses only DATABASE_URL_DIRECT. During this T-03 workflow it deliberately
refuses development/production targets, including downgrade. Deployment
migration authorization is not provided by this test-only guard.

Regression tests cover override attempts, privileged/system database targets,
remote hosts, missing/mismatched approval and effective-target mismatch.
They use synthetic URLs and mocks, not a real development target.

## Migration and model consistency

The eleven foundation tables, UUID identities, timezone-aware timestamps,
relationships, constraints and indexes remain present. Model metadata now includes
ix_locations_aliases_trgm with the same expression and operator class as the migration.

On dormiostay_t03_test_20261117:
- alembic upgrade head: passed.
- alembic check: passed, “No new upgrade operations detected.”
- alembic downgrade base: passed.
- Case-insensitive email uniqueness and coordinate rejection: passed.
- Single-connection pool identity reset after commit and rollback: passed.

After teardown the disposable database retains only alembic_version among public
tables and the shared pg_trgm extension. No database was deleted or reset.
Only disposable test tables were created/dropped by migration verification.

## Sensitive logging and error handling

The JSON formatter emits only timestamp, level, a fixed logger/event label,
sanitized codes and validated request IDs. Arbitrary messages, SQL, parameters,
driver details, exception tracebacks and locals are omitted. Uvicorn, SQLAlchemy
and asyncpg loggers route through this formatter.

Unexpected-error handling logs a fixed message and sends a fixed Sentry message,
rather than forwarding the original exception. Sentry disables automatic
integrations, request-body capture and local variables; before_send reconstructs
events from an allowlist, dropping request data, exceptions, breadcrumbs and
arbitrary context. Database URLs are excluded from Settings repr and validation
errors hide input values. SQLAlchemy runtime and migration engines hide parameters.
Migration/startup failures use fixed messages without underlying exception details.

Regression tests inject synthetic sensitive values into exception details,
SQL parameters, log metadata and Sentry fields and prove those values are absent.
A separate test verifies that SDK initialization installs the privacy controls.
No external Sentry event was sent; transport delivery is not claimed.
This conservative logging intentionally reduces detailed debugging information.

## Git hygiene

.gitignore excludes plain SQL dumps and backup/dump directories, while explicitly
allowing maintained SQL in apps/api/scripts and apps/api/alembic.
Environment files, virtualenvs, node_modules, caches, logs and generated files
remain excluded. All required Python source and test files remain eligible for Git.

The initial migration's trailing whitespace was removed.
git diff --check and a separate untracked-file whitespace scan passed.
Changed/untracked source scans found no local credential values or private-key/
provider-token candidates. .env.example contains only blank assignments.
The two local credential files remain ignored. No files were staged.

## Actual final checks

| Check | Result |
| --- | --- |
| Ruff | Passed |
| Ruff formatting | Passed, 26 files |
| mypy | Passed, 26 source files |
| Full pytest with approved disposable/admin and application URLs | 36 passed, no skips |
| Existing API behavior and new security regressions | Passed |
| Restricted-login startup and readiness | Passed |
| Alembic upgrade/check/downgrade | Passed |
| Real application-role audit permissions | Passed |
| Frontend ESLint and TypeScript | Passed |
| Vitest | 1 passed |
| Prettier and production build | Passed |
| PostgreSQL/Redis Docker health | Both healthy on 5433/6380 |
| Credential and whitespace scans | No matches/issues found |

One existing Starlette/httpx deprecation warning remains. Hosted GitHub Actions
for these changes has not been run. Docker containers, networks, volumes and
the development schema were preserved. Provisioning the authorized new login
and connection/schema-usage grants was the only development database change.

## Remaining scope limitations

Full account/listing RLS and authentication are later-ticket work. The context
helper accepts trusted backend identity only. Tests cover connection reuse with
commit/rollback; exhaustive HTTP cancellation/concurrency coverage is not claimed.
updated_at uses SQLAlchemy onupdate rather than a database-wide update trigger.
The optional profile budget maximum can still be negative if its minimum is NULL,
and PlatformSetting.value remains typed as an object; these pre-existing T-03
model observations are separate from the five security fixes and remain follow-up
items before those fields are exposed through later feature APIs.

## Exact files safe to commit

- .env.example
- .gitignore
- README.md
- apps/api/alembic.ini
- apps/api/alembic/env.py
- apps/api/alembic/script.py.mako
- apps/api/alembic/versions/0001_foundation.py
- apps/api/app/api/v1/health.py
- apps/api/app/core/config.py
- apps/api/app/core/errors.py
- apps/api/app/core/logging.py
- apps/api/app/core/sentry.py
- apps/api/app/db/__init__.py
- apps/api/app/db/base.py
- apps/api/app/db/models/__init__.py
- apps/api/app/db/session.py
- apps/api/app/db/test_target.py
- apps/api/app/main.py
- apps/api/pyproject.toml
- apps/api/scripts/grant_application_role.sql
- apps/api/tests/conftest.py
- apps/api/tests/integration/test_database.py
- apps/api/tests/unit/test_models.py
- apps/api/tests/unit/test_sentry_security.py
- apps/api/tests/unit/test_t03_security.py
- docs/T-03 Verification Report.md

Do not commit .env, apps/api/.env, generated artifacts or temporary verification
scripts. No commit or push was performed.
