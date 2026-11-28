"""Opt-in, loopback-only PostgreSQL fixtures for T-12 acceptance tests."""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.test_target import validate_test_url

DATABASE = "dormiostay_t03_test_gate3b"


@pytest.fixture
def target() -> str:
    if os.environ.get("T12_GATE3B_TEST") != "1":
        pytest.skip("Requires the dedicated loopback Gate 3b fixture")
    url = os.environ["DATABASE_URL_DIRECT"]
    if os.environ.get("TEST_DATABASE_NAME") != DATABASE:
        pytest.fail("Dedicated Gate 3b disposable fixture required")
    validate_test_url(url, DATABASE)
    return url


@pytest.fixture
async def engine(target: str) -> AsyncIterator[AsyncEngine]:
    db = create_async_engine(validate_test_url(target, DATABASE), poolclass=pool.NullPool)
    try:
        yield db
    finally:
        await db.dispose()
