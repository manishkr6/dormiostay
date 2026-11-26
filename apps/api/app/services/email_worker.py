"""Background email queue worker and batch processing service (T-09)."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models.email_outbox import EmailOutbox
from app.services.email_service import (
    BrevoAuthError,
    BrevoPermanentError,
    BrevoTransientError,
    send_outbox_email,
)

logger = logging.getLogger(__name__)

# Increasing retry delays for transient failures: 1m, 5m, 15m, 30m, 60m
RETRY_DELAYS_SECONDS = [60, 300, 900, 1800, 3600]


async def process_single_outbox_item(
    session: AsyncSession,
    item: EmailOutbox,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Process a single email outbox item with state transitions and error handling.

    Transitions:
    - Initial: 'queued' -> 'sending'
    - Success / Dev mode: -> 'accepted_by_provider'
    - Permanent failure (400, bad payload): -> 'failed'
    - Auth failure (401, bad key): -> stays 'queued' without burning attempts (alerts logged)
    - Transient failure (429, 5xx, network timeout):
        * If attempts < max_attempts: -> 'queued' with increasing send_after delay
        * If attempts >= max_attempts: -> terminal 'failed'
    """
    now = datetime.now(UTC)
    item.attempts += 1
    item.status = "sending"
    await session.flush()

    try:
        await send_outbox_email(item, settings, client=client)
        # On success, send_outbox_email sets status to 'accepted_by_provider'
        await session.commit()
        return "accepted"

    except BrevoPermanentError as err:
        item.status = "failed"
        item.failed_at = now
        item.last_error = f"Permanent error: {err}"
        logger.warning(
            "Outbox item %s permanently failed on attempt %d/%d: %s",
            item.id,
            item.attempts,
            item.max_attempts,
            err,
        )
        await session.commit()
        return "failed"

    except BrevoAuthError as err:
        # Do not burn user attempt budget for infrastructure API key configuration errors
        item.status = "queued"
        item.attempts = max(0, item.attempts - 1)
        item.last_error = f"Provider authentication error: {err}"
        logger.error(
            "Brevo authentication error processing outbox item %s: %s",
            item.id,
            err,
        )
        await session.commit()
        return "auth_error"

    except BrevoTransientError as err:
        if item.attempts >= item.max_attempts:
            item.status = "failed"
            item.failed_at = now
            item.last_error = (
                f"Max attempts ({item.max_attempts}) reached. Last transient error: {err}"
            )
            logger.error(
                "Outbox item %s exhausted all %d attempts: %s",
                item.id,
                item.max_attempts,
                err,
            )
            await session.commit()
            return "failed"
        else:
            delay_idx = min(item.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)
            delay_seconds = RETRY_DELAYS_SECONDS[delay_idx]
            item.status = "queued"
            item.send_after = now + timedelta(seconds=delay_seconds)
            item.last_error = (
                f"Transient error (attempt {item.attempts}/{item.max_attempts}): {err}"
            )
            logger.warning(
                "Outbox item %s failed transiently (attempt %d/%d), retrying in %ds: %s",
                item.id,
                item.attempts,
                item.max_attempts,
                delay_seconds,
                err,
            )
            await session.commit()
            return "retried"

    except Exception as err:
        logger.exception("Unexpected exception dispatching outbox item %s: %s", item.id, err)
        if item.attempts >= item.max_attempts:
            item.status = "failed"
            item.failed_at = now
            item.last_error = f"Max attempts reached. Unexpected error: {err}"
            await session.commit()
            return "failed"
        else:
            item.status = "queued"
            item.send_after = now + timedelta(seconds=60)
            item.last_error = f"Unexpected error: {err}"
            await session.commit()
            return "retried"


async def process_outbox_batch(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    batch_size: int = 20,
    client: httpx.AsyncClient | None = None,
) -> dict[str, int]:
    """Fetch and process a batch of queued outbox rows.

    Uses `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL to support multi-worker concurrency
    without duplicate processing.
    """
    stats: dict[str, int] = {
        "processed": 0,
        "accepted": 0,
        "retried": 0,
        "failed": 0,
        "auth_error": 0,
    }

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        own_client = True

    try:
        async with session_factory() as session:
            # Construct query for pending queued emails whose send_after timestamp has passed
            stmt = (
                select(EmailOutbox)
                .where(
                    EmailOutbox.status == "queued",
                    EmailOutbox.send_after <= func.now(),
                )
                .order_by(EmailOutbox.send_after.asc())
                .limit(batch_size)
            )

            # Enable SKIP LOCKED for PostgreSQL concurrency; gracefully omit on SQLite in tests
            bind = getattr(session, "bind", None)
            if bind and bind.dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)

            result = await session.execute(stmt)
            items = list(result.scalars().all())

            for item in items:
                outcome = await process_single_outbox_item(
                    session=session,
                    item=item,
                    settings=settings,
                    client=client,
                )
                stats["processed"] += 1
                if outcome in stats:
                    stats[outcome] += 1
    finally:
        if own_client:
            await client.aclose()

    return stats
