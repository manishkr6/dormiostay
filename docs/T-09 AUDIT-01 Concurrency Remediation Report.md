# T-09 AUDIT-01 Concurrency Remediation Report — Email Worker Batch Lock-Release Fix

- **Date:** 2026-11-26 (Asia/Kolkata)
- **Ticket / Finding:** T-09 (Email Outbox and Brevo Worker) — Defect AUDIT-01
- **Branch:** `feature/t09-outbox-concurrency-fix`
- **Base Commit / Parent HEAD:** `0509dbc9e0cafb4e53b5e397bf5291677e39c704` (T-11 canonical commit)
- **Status:** **REMEDIATED & INDEPENDENTLY APPROVED — READY FOR FINAL PRE-COMMIT CLOSURE**

---

## 1. Executive Summary & Defect Overview

During the comprehensive T-05 through T-11 architectural and staging readiness audit, an independent review identified concurrency finding **AUDIT-01**:

### 1.1 The Original Defect & Root Cause
In `apps/api/app/services/email_worker.py`, `process_outbox_batch()` queried queued outbox records in a batch:
```python
# PREVIOUS VULNERABLE PATTERN:
async with session_factory() as session:
    stmt = (
        select(EmailOutbox)
        .where(EmailOutbox.status == "queued", EmailOutbox.send_after <= func.now())
        .order_by(EmailOutbox.send_after.asc())
        .limit(batch_size)
    )
    if bind and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    items = list((await session.execute(stmt)).scalars().all())

    for item in items:
        await process_single_outbox_item(session, item, settings, client)
```
Inside `process_single_outbox_item()`:
```python
await send_outbox_email(item, settings, client=client)
await session.commit()
```

### 1.2 Concurrency Mechanism & Duplicate Delivery Risk
1. **Premature Lock Release:** In PostgreSQL, row-level locks acquired via `SELECT ... FOR UPDATE` are bound to the duration of the current transaction. When `session.commit()` executed after the first email item was dispatched, PostgreSQL terminated the transaction and **immediately released all row locks** on items $1$ through $N-1$ in the batch.
2. **Stale Database State:** Items $1$ through $N-1$ remained in `status = 'queued'` and unlocked in PostgreSQL.
3. **Multi-Worker / Overlapping Cron Collision:** Any concurrent worker process (or overlapping Render cron execution scheduled at `* * * * *` when batches took $>60$s) executing `process_outbox_batch()` would query `status = 'queued' FOR UPDATE SKIP LOCKED` and claim those exact same unlocked rows.
4. **Duplicate Dispatch:** Both workers would then independently process items $1$ through $N-1$, issuing duplicate HTTP POST requests to Brevo and sending duplicate transactional emails to users.

---

## 2. Remediated Architecture & Transaction Boundaries

### 2.1 Discrete Per-Item Transaction Architecture
The batch processing logic in `apps/api/app/services/email_worker.py` was refactored from a multi-row transaction into **discrete per-item atomic transactions**:

```python
async def process_outbox_batch(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    batch_size: int = 20,
    client: httpx.AsyncClient | None = None,
) -> dict[str, int]:
    stats = {"processed": 0, "accepted": 0, "retried": 0, "failed": 0, "auth_error": 0}
    attempted_ids: set[UUID] = set()

    for _ in range(max(0, batch_size)):
        try:
            async with session_factory() as session:
                stmt = (
                    select(EmailOutbox)
                    .where(EmailOutbox.status == "queued", EmailOutbox.send_after <= func.now())
                    .order_by(EmailOutbox.send_after.asc())
                    .limit(1)
                )
                if attempted_ids:
                    stmt = stmt.where(EmailOutbox.id.not_in(attempted_ids))

                bind = getattr(session, "bind", None) or getattr(session, "get_bind", lambda: None)()
                if bind and hasattr(bind, "dialect") and bind.dialect.name == "postgresql":
                    stmt = stmt.with_for_update(skip_locked=True)

                result = await session.execute(stmt)
                item = result.scalars().first()

                if item is None:
                    break  # No more eligible queued items

                attempted_ids.add(item.id)

                outcome = await process_single_outbox_item(
                    session=session,
                    item=item,
                    settings=settings,
                    client=client,
                )
                stats["processed"] += 1
                if outcome in stats:
                    stats[outcome] += 1

                if outcome == "auth_error":
                    logger.error(
                        "Halting outbox batch processing due to auth error on item %s",
                        item.id,
                    )
                    break

        except Exception as batch_err:
            logger.exception("Error processing outbox item in batch: %s", batch_err)
            break

    return stats
```

### 2.2 Invariant Verification & Critical Safeguards

1. **Zero Multi-Row Lock Leakage:** Exactly ONE email record is selected with `LIMIT 1 FOR UPDATE SKIP LOCKED` per transaction. The row lock is held continuously through dispatch and is released only upon that item's `commit()`.
2. **Explicit SQLAlchemy Commit Behavior:** `process_single_outbox_item()` explicitly calls `await session.commit()` on every single normal outcome branch (accepted by provider, permanent 400 failure, 401 auth error, transient retry backoff, terminal attempt exhaustion, and unexpected error). The session context manager (`async with session_factory() as session:`) does not auto-commit; rather, it closes the session upon exit, ensuring that any unhandled exception or cancellation triggers an automatic rollback in PostgreSQL without leaving stale row locks or corrupt state.
3. **No In-Memory Batch Staleness:** The worker never holds a list of future batch items in memory. Each item is queried fresh from PostgreSQL, eliminating race windows where another worker or admin mutated the row.
4. **Tight Polling Loop Prevention:** `attempted_ids` tracks IDs processed in the current batch invocation. If an item remains in `status = 'queued'` (e.g. after an authentication failure or transient delay), `EmailOutbox.id.not_in(attempted_ids)` prevents the worker from re-selecting the same item during the same batch invocation.
5. **Fast Failure on Provider Auth Error:** If Brevo returns HTTP 401 (`BrevoAuthError`), the batch halts immediately after processing item 1. This prevents burning CPU and network cycles attempting 20 subsequent requests when API credentials are misconfigured.
6. **Bounded Processing:** The loop runs at most `batch_size` times (default 20), maintaining bounded execution time per cron run.
7. **Preserved Contracts:** All return signatures (`stats: dict[str, int]`) and retry/webhook transitions remain identical.

---

## 3. Crash Recovery & Delivery Guarantees (At-Least-Once Delivery)

### 3.1 Industry Reality & External Provider Boundaries
- **Provider Idempotency Limitation:** Brevo's API v3 (`POST /v3/smtp/email`) does not support an idempotency key header across distinct HTTP requests.
- **Worker Crash Window:** In `process_single_outbox_item()`, `send_outbox_email()` dispatches the email to Brevo, followed by `await session.commit()`. If the worker process experiences a hard crash (e.g. SIGKILL, OOM, power loss) after Brevo accepts the message but before PostgreSQL commits the status update:
  - The transaction rolls back.
  - The item remains in status `'queued'` in PostgreSQL.
  - Upon worker restart, the item will be claimed and dispatched again.
- **Guarantee Classification:** DormioStay's transactional email system provides **at-least-once delivery**. Physical exactly-once delivery across external network APIs without two-phase commit or provider idempotency keys is theoretically impossible.
- **Defect Remediation Scope:** AUDIT-01 eliminates **deterministic concurrent duplicate dispatching** during normal execution across multiple workers or overlapping cron runs.

---

## 4. Verification & Regression Test Suite

Ten focused regression tests were added to `apps/api/tests/unit/test_t09_email_worker.py`:

| Test Name | Verified Invariant | Status |
|:---|:---|:---:|
| `test_audit01_per_item_discrete_session_lifecycle` | Each item is queried, locked, and committed in its own discrete transaction ($N$ items $\rightarrow$ $N+1$ sessions, $N$ commits). | **PASS** |
| `test_audit01_no_stale_batch_in_memory_processing` | If an item is modified concurrently in the DB between iterations, the worker re-queries fresh state and avoids duplicate processing. | **PASS** |
| `test_audit01_multi_worker_skip_locked_simulation` | Concurrent workers claim distinct items without collisions or duplicate deliveries. | **PASS** |
| `test_audit01_batch_statistics_counters_mixed_outcomes` | Exact counters across accepted, failed (permanent 400), and retried (transient 429) items. | **PASS** |
| `test_audit01_no_eligible_items_clean_exit` | Clean exit with zero processing when no queued items exist. | **PASS** |
| `test_audit01_brevo_auth_error_halts_batch_without_spinning` | Provider 401 error halts batch immediately without spinning across remaining items. | **PASS** |
| `test_audit01_attempted_ids_deduplication_prevents_reselection` | `attempted_ids` prevents re-querying items that remain queued in the same batch. | **PASS** |
| `test_audit01_bounded_processing_respects_batch_size` | Worker respects `batch_size` limit (e.g. processes 2 out of 5 items). | **PASS** |
| `test_audit01_worker_exception_rolls_back_safely` | Database errors in loop log and break cleanly without crashing process. | **PASS** |
| `test_audit01_genuine_sqlalchemy_orm_per_item_transactions` | Genuine SQLAlchemy ORM execution over SQLite engine with discrete session lifecycle. | **PASS** |

### 4.1 Test Execution Results
```bash
$ pytest tests/unit/test_t09_email_worker.py -q
35 passed, 1 warning in 0.49s
```

All 35 T-09 tests pass 100%.

---

## 5. Quality Gate Evidence Across Workspaces

| Gate | Command | Result |
|:---|:---|:---:|
| **T-09 Focused Suite** | `pytest tests/unit/test_t09_email_worker.py` | **35 passed** (0.49s) |
| **Combined Security Suite** | `pytest tests/unit/test_t06* .. test_t11*` | **205 passed** (7.10s) |
| **Full API Unit Suite** | `pytest tests/unit/` | **644 passed** (34.12s) |
| **Ruff Linter** | `ruff check app tests scripts alembic` | **All checks passed!** (0 errors) |
| **Ruff Formatter** | `ruff format --check app tests scripts alembic` | **62 files cleanly formatted** |
| **Strict Mypy** | `mypy app tests alembic scripts` | **Success: 0 issues across 62 files** |
| **Frontend Web Vitest** | `npm test --workspace @dormiostay/web` | **84 passed** (1.38s) |
| **Frontend Mobile Jest** | `npm run test --workspace @dormiostay/mobile` | **88 passed** (3.36s) |
| **Frontend Linters & Types** | `npm run lint` & `npm run typecheck` | **0 errors, 0 warnings** |
| **Prettier** | `npm run format:check` | **Code style clean** |
| **Frontend Web Build** | `npm run build` | **Vite build clean (148ms)** |
| **Git Whitespace Check** | `git diff --check` | **0 errors** |

---

## 6. Release Gate & Deployment Limitations

1. **Uncommitted State:** All changes are uncommitted on `feature/t09-outbox-concurrency-fix` for independent review.
2. **Worker Cron State:** In `render.yaml`, the worker cron job remains intentionally commented out (`# - type: cron ... dormiostay-worker`).
3. **Production Readiness:** The worker is hardened for multi-worker concurrency locally, but live deployment remains deferred pending Gate 4 Astra approval and Gate 5 Render authorization.
4. **Feature Ticket T-12:** Remains ready to begin upon user authorization.
