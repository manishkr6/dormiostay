"""Explicit disposable-target validation, shared by tests and guarded migrations."""

import re

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.session import async_url


def validate_test_url(value: str, approved: str | None) -> URL:
    url = async_url(value)
    if not approved or not re.fullmatch(r"dormiostay_t03_test_[a-z0-9_]+", approved):
        raise ValueError("An explicitly approved disposable database name is required")
    if url.database != approved or url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Test target must be the approved local disposable database")
    return url


async def verify_test_connection(connection: AsyncConnection, approved: str) -> None:
    actual = await connection.scalar(text("SELECT current_database()"))
    if actual != approved:
        raise RuntimeError("Effective database target differs from approved test database")
