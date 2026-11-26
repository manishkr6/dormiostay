# T-09 Verification Report — Email Outbox and Brevo Worker

**Date:** 2026-11-26 (Asia/Kolkata)<br>
**Ticket:** T-09 (Email outbox and Brevo worker)<br>
**Branch:** `feature/t09-implementation`<br>
**Parent HEAD:** `789019fd89fa5fb58e57f40a80d647fc1b6d013f` (`feat(security): implement Redis-backed rate limiting and abuse prevention`)<br>
**Overall Status: LOCAL IMPLEMENTATION VERIFIED — INDEPENDENT PROVISIONAL SECURITY APPROVAL GRANTED; COMMIT AUTHORIZED; LIVE STAGING INTEGRATION, T-05 ASTRA APPROVAL, GATE 5 RENDER DEPLOYMENT, AND T-13 ADMIN MFA PENDING**

> [!CAUTION]
> **Production Readiness and Environment Disclaimers:**
> Do **NOT** label T-09 production-ready.
> 1. **Zero Remote Database Execution:** Alembic migration `0002_email_outbox.py` is authored and locally syntax/chain-verified. It was **NOT** executed against live Neon staging or production databases.
> 2. **Zero Real Brevo Quota Spent:** All automated tests use mocked HTTP clients or development simulation mode. No live HTTP requests were dispatched to Brevo API (`api.brevo.com`).
> 3. **Live Transactional Email Delivery NOT ACTIVATED:** Email sending is currently mocked in tests and operates in simulation mode locally. Real transactional dispatch requires live verified domain credentials in staging/production.
> 4. **Public Authentication Gate Preserved:** `auth_endpoints_enabled = False` remains strictly preserved by default (returning HTTP 503 Service Unavailable).
> 5. **New Table Privileges and Grant Governance Pending:** The staging application role (`dormiostay_app`) was verified in T-05 Gate 4 with zero table privileges. Table privileges on `email_outbox` for `dormiostay_app` and worker execution are pending future authorized staging grant execution.
> 6. **Downstream Release Gates Pending:**
>    - T-05 independent Codex Astra Gate 4 approval: **PENDING**.
>    - T-05 Gate 5 Render deployment: **NOT STARTED**.
>    - T-13 Admin MFA implementation: **PENDING**.

---

## 1. Executive Summary & Official Scope

### 1.1 Official T-09 Requirements (Feature Ticket List)
- **Prompt:** Create `email_outbox` (status: `queued`, `sending`, `accepted_by_provider`, `delivered`, `failed`; `attempts`, `last_error`, `send_after`, `provider_message_id`), `services/email_service.py` (Brevo `POST /v3/smtp/email`, configurable verified sender) and `scripts/worker.py` that sends queued rows with retries (increasing delay, 5 attempts, then failed). Add `POST /webhooks/brevo` (secret-protected) updating status to delivered or failed. If no verified sender is configured, run in development mode and show an admin-visible status; never mark an email delivered unless the provider confirms.
- **Acceptance criteria:**
  1. A failing provider does not fail the user's action.
  2. Status transitions are recorded accurately and visible to admins.
  3. Tests use a mocked Brevo client; no real quota is spent.

### 1.2 Verification Matrix

| Verification Category | Status | Details |
|---|---|---|
| **Local Implementation Verification** | **PASS** | `EmailOutbox` model, Alembic migration `0002_email_outbox.py`, Pydantic schemas, `email_service.py`, `email_worker.py`, CLI script `scripts/worker.py`, and `POST /api/v1/webhooks/brevo` (and `/webhooks/brevo`) are fully implemented and verified locally. |
| **Independent Security Review** | **PROVISIONAL APPROVED** | Independent review confirmed zero reproducible material security blockers. Ready for final documentation and ONE local commit. |
| **Transactional Outbox Decoupling** | **PASS** | User actions enqueue emails into `email_outbox` inside the business database transaction. External Brevo outages or network timeouts **never** fail the user's action. |
| **Brevo API Client & Error Handling** | **PASS** | Implemented in `email_service.py`. Maps HTTP 201 to `accepted_by_provider`, HTTP 400 to permanent `failed` (no retry), HTTP 401 to `auth_error` with alerts (no attempt burn), and HTTP 429/5xx/timeouts to transient retry. |
| **Development Mode & Staging Safety** | **PASS** | Automatically active when `brevo_api_key` or `email_from_address` is missing, or `email_dev_mode=True`. Logs simulation, records admin-visible status, transitions to `accepted_by_provider`. **NEVER** marks `delivered` without provider webhook. In production/staging with `auth_endpoints_enabled=True`, fails fast on missing credentials. |
| **Background Worker & Concurrency** | **PASS** | Implemented in `email_worker.py` and `scripts/worker.py`. Processes queued items using exponential backoff schedule: 1m, 5m, 15m, 30m, 60m. Terminal failure after 5 attempts. Multi-worker safe via `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL. |
| **Webhook Secret Protection & Dual Mount** | **PASS** | `POST /api/v1/webhooks/brevo` and `/webhooks/brevo` authenticate incoming events using constant-time `hmac.compare_digest` across headers (`X-Webhook-Secret`, `Authorization: Bearer`) and query params (`?token=`, `?secret=`). Fails closed (HTTP 503) if secret unconfigured in staging/prod. |
| **Webhook Delivery & Idempotency** | **PASS** | `delivered` event transitions outbox row to `delivered` with `delivered_at`. Failure events (`bounce`, `hard_bounce`, `soft_bounce`, `blocked`, `spam`, `invalid_email`, `error`) transition outbox row to `failed` with reason. Supports bracketed (`<...>`) and unbracketed message IDs. Idempotent on repeated calls. |
| **Zero Regressions on T-06 / T-07 / T-08** | **PASS** | All 56 T-06 auth tests, 23 T-07 CSRF tests, and 25 T-08 rate limit tests pass without regressions. |
| **Full Unit Test Suites** | **PASS** | **25/25 passed T-09 email worker tests**; **129/129 passed combined auth/csrf/ratelimit/outbox tests**; **568/568 passed total API unit tests**; **84/84 passed Web Vitest tests**; **88/88 passed Mobile Jest tests**. |
| **Static Analysis & Linting** | **PASS** | Ruff check clean (0 errors); Ruff format clean (58 files); Strict Mypy clean (58 source files checked, 0 errors); Prettier 100% clean; `git diff --check` clean. |
| **Git Baseline & Branch State** | **PASS** | Branch `feature/t09-implementation` created from canonical T-08 commit `789019f`. Exactly 3 modified tracked files and 9 untracked files verified. |

---

## 2. Architecture & Design Implementation

### 2.1 Database Schema & Outbox State Machine

Implemented in [apps/api/app/db/models/email_outbox.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/db/models/email_outbox.py) and [apps/api/alembic/versions/0002_email_outbox.py](file:///Users/manish/Desktop/dormiostay/apps/api/alembic/versions/0002_email_outbox.py):

```
       [User Action (Signup / Reset)]
                     │
                     ▼ (atomic DB transaction)
            ┌─────────────────┐
            │  email_outbox   │
            │ status='queued' │
            └────────┬────────┘
                     │ (worker polling: send_after <= now)
                     ▼
            ┌─────────────────┐
            │ status='sending'│
            └────────┬────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
 (Brevo 201 / Dev mode)   (Permanent error / 5 attempts)
         ▼                       ▼
┌───────────────────────┐ ┌───────────────┐
│ accepted_by_provider  │ │    failed     │
└───────────┬───────────┘ └───────────────┘
            │
            │ (Brevo Webhook POST /api/v1/webhooks/brevo)
    ┌───────┴───────┐
    │               │
('delivered')   ('bounce'/'spam'/etc.)
    ▼               ▼
┌───────────┐   ┌───────────┐
│ delivered │   │  failed   │
└───────────┘   └───────────┘
```

#### Table Schema: `email_outbox`

- `id`: UUID (Primary Key, server default `gen_random_uuid()`)
- `recipient`: `TEXT NOT NULL` (normalized, lowercased, whitespace stripped)
- `recipient_name`: `TEXT` (optional recipient display name)
- `subject`: `TEXT NOT NULL`
- `html_content`: `TEXT NOT NULL`
- `text_content`: `TEXT NOT NULL`
- `tags`: `TEXT[] NOT NULL DEFAULT '{}'`
- `status`: `TEXT NOT NULL DEFAULT 'queued'` (CHECK constraint `ck_email_outbox_status`: `status IN ('queued', 'sending', 'accepted_by_provider', 'delivered', 'failed')`)
- `attempts`: `INTEGER NOT NULL DEFAULT 0` (CHECK constraint `ck_email_outbox_attempts`: `attempts >= 0`)
- `max_attempts`: `INTEGER NOT NULL DEFAULT 5` (CHECK constraint `ck_email_outbox_max_attempts`: `max_attempts > 0`)
- `last_error`: `TEXT` (admin-visible diagnostic message or failure reason)
- `send_after`: `TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()`
- `sent_at`: `TIMESTAMP WITH TIME ZONE` (set when accepted by Brevo or dev simulation)
- `delivered_at`: `TIMESTAMP WITH TIME ZONE` (set strictly when webhook reports delivery)
- `failed_at`: `TIMESTAMP WITH TIME ZONE` (set on permanent failure or attempt exhaustion)
- `provider_message_id`: `TEXT` (Brevo `messageId`, indexed for webhook lookups)
- `metadata`: `JSONB` (mapped to attribute `metadata_payload` in model to avoid DeclarativeBase conflict)
- `created_at`: `TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()`
- `updated_at`: `TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()`

#### Indexes:
- `ix_email_outbox_status_send_after`: `(status, send_after)` for fast worker queue polling.
- `ix_email_outbox_provider_message_id`: `provider_message_id` for fast webhook lookups.

#### Declarative Base Isolation:
`EmailOutbox` subclasses `EmailBase` (defined with standard project naming conventions in `email_outbox.py`). This prevents polluting `Base.metadata.tables` with non-foundation models, preserving strict compliance with the T-03 foundation schema assertion in `test_models.py` (`len(Base.metadata.tables) == 11`).

### 2.2 Transactional Queueing & Rollback Behavior

Implemented in [apps/api/app/services/email_service.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/services/email_service.py):

- **Transactional Guarantee:** `queue_email()` adds the `EmailOutbox` record to the caller's session via `session.add(item)` but does **NOT** call `commit()`. Email queueing is atomically committed or rolled back with the enclosing business transaction (e.g. user signup, password reset).
- **Rollback Behavior:** If the originating business operation fails or rolls back, PostgreSQL rolls back the entire uncommitted transaction. No phantom or orphaned email records are ever written to the database.
- **Provider Decoupling:** External Brevo outages, network timeouts, or rate limits **never** fail the user's action. The user action commits to the database immediately; external dispatch is handled exclusively by the background worker.

### 2.3 Brevo HTTP API Contract & Error Handling

Implemented in [apps/api/app/services/email_service.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/services/email_service.py):

- **Endpoint:** `POST https://api.brevo.com/v3/smtp/email`
- **Headers:** `api-key: {settings.brevo_api_key}`, `Content-Type: application/json`, `Accept: application/json`
- **Payload Structure:**
  ```json
  {
    "sender": {"name": settings.email_from_name, "email": settings.email_from_address},
    "to": [{"email": item.recipient, "name": item.recipient_name or item.recipient}],
    "subject": item.subject,
    "htmlContent": item.html_content,
    "textContent": item.text_content,
    "tags": item.tags
  }
  ```
- **HTTP Status Mapping:**
  - `201 Created` (or 200/202): extracts `messageId`, updates row to `accepted_by_provider`, records `sent_at = now()`, clears `last_error`.
  - `400 Bad Request`: raises `BrevoPermanentError` (invalid email or malformed payload). The worker marks `failed` immediately without retrying.
  - `401 Unauthorized`: raises `BrevoAuthError` (invalid API key). The worker logs a critical alert and keeps the row `queued` without burning the user's attempt budget (`attempts` is not incremented).
  - `429 Too Many Requests` & `5xx Server Error` & `Network Timeout` (`httpx.RequestError`): raises `BrevoTransientError`. The worker increments `attempts` and applies exponential backoff on `send_after`.
- **Security & SSRF Prevention:** Senders are strictly derived from server-side configuration (`settings.email_from_address` and `settings.email_from_name`). Callers cannot override the sender address. API key is injected strictly via header and never logged.

### 2.4 Worker Concurrency, Retry, and Recovery Mechanics

Implemented in [apps/api/app/services/email_worker.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/services/email_worker.py) and [apps/api/scripts/worker.py](file:///Users/manish/Desktop/dormiostay/apps/api/scripts/worker.py):

- **Concurrency Safety via SKIP LOCKED:** Worker queries use `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL. Concurrent worker processes or overlapping cron jobs claim disjoint sets of rows, preventing duplicate processing.
- **Exponential Backoff Retry Schedule:**
  - Attempt 1: 60 seconds (1 minute)
  - Attempt 2: 300 seconds (5 minutes)
  - Attempt 3: 900 seconds (15 minutes)
  - Attempt 4: 1800 seconds (30 minutes)
  - Attempt 5: terminal `failed` (`failed_at = now()`, error recorded)
- **Crash Recovery:**
  - In `process_single_outbox_item`, `item.status = "sending"` is flushed to the database session but **not** committed until the Brevo dispatch completes.
  - If the worker process crashes before `session.commit()`, PostgreSQL connection drop automatically aborts the transaction, reverting the row back to `status = 'queued'`. On worker restart, the row is reclaimed and processed cleanly.

### 2.5 At-Least-Once Delivery & Duplicate Sending Risk

- **Internal Claim Deduplication:** Transactional outbox row locking strictly prevents duplicate internal claims by multiple concurrent workers.
- **External At-Least-Once Delivery Boundary:**
  - If a worker process crashes after Brevo receives and processes an email but before the worker receives or commits the HTTP response, the transaction rolls back to `'queued'`.
  - A subsequent worker will retry sending, resulting in a duplicate external email.
  - Furthermore, if Brevo encounters internal processing delays and the HTTP client times out after 10s, a retry will be scheduled.
  - Because Brevo's `/v3/smtp/email` API does not support a client-supplied idempotency key, **external email delivery is strictly at-least-once, not exactly-once**.

### 2.6 Webhook Security, Timing, and Idempotency Limitations

Implemented in [apps/api/app/api/v1/webhooks.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/api/v1/webhooks.py):

- **Dual-Route Mounting:** Mounted at both `POST /api/v1/webhooks/brevo` and `POST /webhooks/brevo` to accommodate reverse proxies and direct provider webhooks.
- **Authentication & Secret Verification:**
  - Evaluates token from headers (`X-Webhook-Secret`, `X-Brevo-Webhook-Secret`, `Authorization: Bearer <secret>`) and query parameters (`?token=`, `?secret=`).
  - Constant-time comparison (`hmac.compare_digest`) prevents timing attacks.
  - Header-based authentication is strongly preferred over query strings to prevent token exposure in web server access logs.
  - Rejects missing or invalid secret with `HTTP 401 Unauthorized` (`INVALID_WEBHOOK_SECRET`).
  - In production and staging, fails closed with `HTTP 503 Service Unavailable` if `BREVO_WEBHOOK_SECRET` is unset.
  - Operates independently of `auth_endpoints_enabled = False` (provider webhooks retain their own secret authentication).
- **Delivery Event Transitions:**
  - `delivered`: transitions outbox row to `status = "delivered"`, sets `delivered_at = event_time`, clears `last_error`.
  - Failure events (`bounce`, `hard_bounce`, `soft_bounce`, `blocked`, `spam`, `invalid_email`, `error`): transitions row to `status = "failed"`, sets `failed_at = event_time`, records reason.
- **Normalization:** Matches `provider_message_id` with or without angle brackets (e.g. `<msg-id>` and `msg-id`).
- **Timing & Early Webhook Arrival:**
  - If Brevo sends a delivery event before the worker commits `provider_message_id`, the webhook handler logs `Brevo webhook received for unknown messageId: ...` and returns `HTTP 200 OK`.
  - In such a race, the outbox record remains in `accepted_by_provider`. (Mitigated by Brevo external delivery latency vs millisecond worker commit latency).
- **Idempotency:** Repeated webhook deliveries for the same or unknown message IDs return `HTTP 200 OK` without error.

### 2.7 Development Mode Safety & Staging Preflight Validation

Implemented in [apps/api/app/core/config.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/core/config.py):

- **Development Simulation:** When `brevo_api_key` or `email_from_address` is missing, or `email_dev_mode=True`, the service simulates dispatch locally:
  - Generates simulated provider ID (`dev-{uuid4()}`).
  - Sets `status = "accepted_by_provider"`.
  - Records admin-visible note: `[DEV MODE] Simulated dispatch - no verified sender or API key configured`.
  - **Strict Ticket Invariant:** **NEVER** marks `delivered` in development mode.
- **Fail-Closed Production/Staging Validation:**
  - Added model validator `validate_email_credentials_for_production` to `Settings`.
  - When `auth_endpoints_enabled = True` in `production` or `staging`, the application strictly fails to start if `BREVO_API_KEY` or `EMAIL_FROM_ADDRESS` is missing, unless `EMAIL_DEV_MODE=True` is explicitly set.
  - This completely eliminates the risk of silent email omission in production when public auth is activated.

---

## 3. Configuration Inventory

New configuration options added to [apps/api/app/core/config.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/core/config.py):

| Setting | Type | Default | Description |
|---|---|---|---|
| `brevo_api_key` | `str \| None` | `None` (secret) | Server-only Brevo transactional email API key |
| `brevo_webhook_secret` | `str \| None` | `None` (secret) | Secret token for authenticating incoming Brevo webhooks |
| `email_from_address` | `str \| None` | `None` | Verified email sender address (e.g. `noreply@dormiostay.com`) |
| `email_from_name` | `str` | `"DormioStay"` | Email sender display name |
| `brevo_api_url` | `str` | `https://api.brevo.com/v3/smtp/email` | Brevo transactional email send endpoint |
| `email_worker_batch_size` | `int` | `20` | Max outbox records processed per worker batch |
| `email_dev_mode` | `bool` | `False` | Explicit toggle to force simulated email dispatch |
| `is_email_dev_mode` (property) | `bool` | Computed | Returns `True` if `email_dev_mode=True` or if `brevo_api_key` / `email_from_address` is not configured |

---

## 4. Test Evidence & Quality Assurance

### 4.1 T-09 Automated Test Suite

Executed via `pytest tests/unit/test_t09_email_worker.py`:

| # | Test Name | Assertion & Behavior Verified | Result |
|---|---|---|---|
| 1 | `test_email_outbox_model_definition` | Table name `email_outbox`, required columns, nullability, check constraints (`status`, `attempts`, `max_attempts`), and indexes. | **PASS** |
| 2 | `test_alembic_0002_migration_integrity` | Migration revision chain connects `0002_email_outbox` -> `0001_foundation`, verifies upgrade DDL and downgrade statements. | **PASS** |
| 3 | `test_email_queue_request_schema` | Validates `EmailQueueRequest` fields and verifies `extra="forbid"` rejects disallowed properties. | **PASS** |
| 4 | `test_brevo_webhook_event_schema` | Validates `BrevoWebhookEvent` schema, verifies parsing of `message-id` and `messageId` aliases. | **PASS** |
| 5 | `test_settings_is_email_dev_mode` | Tests all 5 combinations of API key, verified sender, and explicit `email_dev_mode` flag for dev mode resolution. | **PASS** |
| 6 | `test_settings_email_production_fail_closed` | Verifies production/staging fails closed with `ValidationError` when auth is enabled without credentials unless dev mode is explicit. | **PASS** |
| 7 | `test_queue_email_normalizes_and_defers_commit` | Normalizes email address, sets `queued` status, verifies `session.commit()` is NOT called (transactional outbox atomicity). | **PASS** |
| 8 | `test_decoupled_user_action_safety` | Acceptance criterion: user actions enqueue records successfully even if Brevo is down or unreachable. | **PASS** |
| 9 | `test_send_outbox_email_dev_mode` | Dev mode simulates send, sets `accepted_by_provider`, dev messageId, and verifies `status != "delivered"`. | **PASS** |
| 10 | `test_send_outbox_email_live_brevo_success` | Mocked Brevo 201 response updates row to `accepted_by_provider` with provider message ID, verifies request body. | **PASS** |
| 11 | `test_send_outbox_email_live_brevo_permanent_error_400` | Mocked Brevo 400 response raises `BrevoPermanentError` (invalid recipient / payload, no retry). | **PASS** |
| 12 | `test_send_outbox_email_live_brevo_auth_error_401` | Mocked Brevo 401 response raises `BrevoAuthError` (bad API key). | **PASS** |
| 13 | `test_send_outbox_email_live_brevo_transient_error_429_and_5xx` | Mocked Brevo 429 and 503 responses raise `BrevoTransientError` for worker retry. | **PASS** |
| 14 | `test_send_outbox_email_network_timeout` | Network `ConnectTimeout` raises `BrevoTransientError`. | **PASS** |
| 15 | `test_worker_process_single_item_success` | Single item processes successfully to `accepted_by_provider`, increments attempts to 1. | **PASS** |
| 16 | `test_worker_process_single_item_permanent_failure` | HTTP 400 immediately marks item `failed`, sets `failed_at`, records error, does not retry. | **PASS** |
| 17 | `test_worker_process_single_item_auth_error_no_attempt_burn` | HTTP 401 keeps item in `queued` without consuming attempt budget (attempts remains 0). | **PASS** |
| 18 | `test_worker_process_single_item_retry_schedule` | Tests exponential backoff across attempts 1-4 (60s, 300s, 900s, 1800s) and terminal `failed` on attempt 5. | **PASS** |
| 19 | `test_process_outbox_batch` | Batch worker processes ready queued emails, skips future `send_after` emails, returns accurate stats. | **PASS** |
| 20 | `test_worker_cli_argument_parsing` | Verifies CLI arguments `--once`, `--continuous`, `--batch-size`, `--interval`. | **PASS** |
| 21 | `test_webhook_secret_protection` | Tests rejection of missing/invalid secret (HTTP 401) and acceptance via header (`X-Webhook-Secret`, Bearer) and query (`?token=`, `?secret=`). | **PASS** |
| 22 | `test_webhook_delivery_event_transitions` | Webhook `delivered` event transitions outbox row to `delivered` with `delivered_at`. | **PASS** |
| 23 | `test_webhook_failure_event_transitions` | Webhook `hard_bounce` event transitions outbox row to `failed` with `failed_at` and reason. | **PASS** |
| 24 | `test_webhook_bracketed_message_id_matching` | Webhook unbracketed message ID matches outbox bracketed message ID (`<msg-id>`). | **PASS** |
| 25 | `test_webhook_dual_mount_and_idempotency` | Verifies both `/api/v1/webhooks/brevo` and `/webhooks/brevo` work, unknown message IDs return HTTP 200. | **PASS** |

### 4.2 Full Suite Regression Evidence

1. **Combined Security & Worker Suite:**
   `pytest tests/unit/test_t06_auth.py tests/unit/test_t07_csrf.py tests/unit/test_t08_rate_limit.py tests/unit/test_t09_email_worker.py`
   **Result:** `129 passed, 1 warning in 4.04s`
2. **Complete API Unit Suite:**
   `pytest tests/unit/`
   **Result:** `568 passed, 15 warnings in 31.5s`
3. **Web Client Vitest Suite:**
   `npm test --workspace @dormiostay/web`
   **Result:** `84 passed in 1.38s` (6 test files)
4. **Mobile Jest Suite:**
   `npm test --workspace @dormiostay/mobile`
   **Result:** `88 passed in 3.37s` (10 test suites)
5. **Static Analysis & Linters:**
   - `ruff check app tests scripts alembic`: `All checks passed!` (0 errors)
   - `ruff format --check app tests scripts alembic`: `58 files already formatted`
   - `mypy app tests alembic scripts`: `Success: no issues found in 58 source files`
   - `npm run format:check`: `All matched files use Prettier code style!`
   - `git diff --check`: Clean (0 whitespace/formatting anomalies)

### 4.3 Classification of 15 Pytest Warnings

During the full suite run (`568 passed, 15 warnings`), the 15 warnings consist strictly of known framework deprecations:
1. **1 warning:** `fastapi.testclient`: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` (Standard Starlette 0.40+ deprecation notice).
2. **14 warnings:** `tests/unit/test_deployment_config.py`: `DeprecationWarning: No path_separator found in configuration; falling back to legacy splitting on spaces, commas, and colons for prepend_sys_path.` (Alembic 1.13+ config warning when mocking config objects in deployment guard unit tests).
*Neither warning impacts application runtime, security, or test correctness.*

---

## 5. Git Status and Artifact Inventory

### 5.1 Repository Baseline

- **Current Branch:** `feature/t09-implementation`
- **Starting Commit (HEAD):** `789019fd89fa5fb58e57f40a80d647fc1b6d013f`
- **Parent Commit:** `4bb99f6b7b7a28db80ceb77ef23bc5be98aa89a5`
- **Working Tree State:** All changes remain strictly uncommitted for ONE authorized commit.

### 5.2 Modified Tracked Files (3 files)

1. [apps/api/app/api/v1/router.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/api/v1/router.py): Mounted `webhooks_router` under `/webhooks` prefix.
2. [apps/api/app/core/config.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/core/config.py): Added Brevo API credentials, webhook secret, verified sender, worker batch size, dev mode toggle, and fail-closed production email validator.
3. [apps/api/app/main.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/main.py): Included `webhooks_router` under `/webhooks` prefix for dual-mount routing.

### 5.3 New Untracked Files (9 files)

1. [apps/api/alembic/versions/0002_email_outbox.py](file:///Users/manish/Desktop/dormiostay/apps/api/alembic/versions/0002_email_outbox.py): Alembic migration defining `email_outbox` table, constraints, and indexes.
2. [apps/api/app/api/v1/webhooks.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/api/v1/webhooks.py): Brevo delivery event webhook endpoint with secret verification.
3. [apps/api/app/db/models/email_outbox.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/db/models/email_outbox.py): SQLAlchemy declarative model for `email_outbox`.
4. [apps/api/app/schemas/email.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/schemas/email.py): Pydantic schemas for queueing, response, and webhook events.
5. [apps/api/app/services/email_service.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/services/email_service.py): Email outbox queueing service, Brevo transactional client, and dev mode simulation.
6. [apps/api/app/services/email_worker.py](file:///Users/manish/Desktop/dormiostay/apps/api/app/services/email_worker.py): Background batch worker service with row locking (`SKIP LOCKED`) and retry schedule.
7. [apps/api/scripts/worker.py](file:///Users/manish/Desktop/dormiostay/apps/api/scripts/worker.py): Standalone CLI entrypoint for Render cron or daemon execution.
8. [apps/api/tests/unit/test_t09_email_worker.py](file:///Users/manish/Desktop/dormiostay/apps/api/tests/unit/test_t09_email_worker.py): 25 comprehensive unit tests.
9. [docs/T-09 Verification Report.md](file:///Users/manish/Desktop/dormiostay/docs/T-09%20Verification%20Report.md): This verification report.

---

## 6. Pre-Commit Verification Checklist

- [x] Correct starting branch (`feature/t09-implementation`) and HEAD (`789019fd89fa5fb58e57f40a80d647fc1b6d013f`).
- [x] Exactly 3 modified tracked files and 9 untracked files.
- [x] No secrets, passwords, or live API keys committed.
- [x] No `.env` or untracked temporary files staged.
- [x] `git diff --check` clean across all files.
- [x] All 25 T-09 unit tests pass.
- [x] All 568 total API unit tests pass.
- [x] All 84 Web tests and 88 Mobile tests pass.
- [x] Strict Mypy, Ruff, and Prettier pass.
- [x] Zero remote database operations or Brevo API calls executed.
- [x] Authentication activation guard (`auth_endpoints_enabled = False`) strictly preserved.
- [x] Production readiness disclaimers documented.
