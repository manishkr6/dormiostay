#!/usr/bin/env python3
"""DormioStay Background Email Worker CLI (T-09).

Dispatches queued transactional emails via Brevo with exponential backoff retries.
Can run as a one-shot batch job (e.g. Render Cron / K8s CronJob every minute)
or as a continuous background daemon with graceful shutdown.
"""

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine
from app.services.email_worker import process_outbox_batch

logger = logging.getLogger("email_worker")


class GracefulKiller:
    """Intercept SIGINT and SIGTERM for clean shutdown."""

    def __init__(self) -> None:
        self.kill_now = False
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, *args: Any) -> None:
        logger.info("Shutdown signal received; finishing in-flight batch and terminating...")
        self.kill_now = True


async def run_worker(
    settings: Settings,
    once: bool = False,
    batch_size: int = 20,
    poll_interval: float = 5.0,
) -> int:
    """Run the worker loop or one-shot execution."""
    killer = GracefulKiller()
    engine = create_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    logger.info(
        "Starting DormioStay Email Worker (env=%s, dev_mode=%s, batch_size=%d, once=%s)",
        settings.app_env,
        settings.is_email_dev_mode,
        batch_size,
        once,
    )

    total_processed = 0
    try:
        while not killer.kill_now:
            try:
                stats = await process_outbox_batch(
                    session_factory=session_factory,
                    settings=settings,
                    batch_size=batch_size,
                )
                if stats["processed"] > 0:
                    total_processed += stats["processed"]
                    logger.info(
                        "Batch: %d items (accepted=%d, retried=%d, failed=%d, auth_error=%d)",
                        stats["processed"],
                        stats["accepted"],
                        stats["retried"],
                        stats["failed"],
                        stats["auth_error"],
                    )
            except Exception as exc:
                logger.exception("Error processing email outbox batch: %s", exc)

            if once:
                break

            # Sleep briefly before polling for next batch
            for _ in range(int(poll_interval * 10)):
                if killer.kill_now:
                    break
                await asyncio.sleep(0.1)

    finally:
        await engine.dispose()
        logger.info("Email worker stopped. Total items processed: %d", total_processed)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DormioStay Email Outbox Worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single batch and exit immediately (default for Cron jobs)",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously in a loop polling the outbox",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Maximum emails to process per batch (defaults to settings.email_worker_batch_size)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between poll cycles when running continuously (default: 5.0)",
    )
    return parser.parse_args()


def main() -> None:
    configure_logging()
    args = parse_args()
    settings = get_settings()

    batch_size = args.batch_size or settings.email_worker_batch_size
    # Default to --once if neither --once nor --continuous is explicitly provided
    run_once = args.once or (not args.continuous)

    try:
        sys.exit(
            asyncio.run(
                run_worker(
                    settings=settings,
                    once=run_once,
                    batch_size=batch_size,
                    poll_interval=args.interval,
                )
            )
        )
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
