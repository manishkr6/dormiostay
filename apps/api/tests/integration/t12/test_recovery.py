import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.recovery import foundation_fingerprint

pytestmark = pytest.mark.anyio


async def test_rehearsal_fingerprint(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await conn.execute(text("SET LOCAL row_security=off"))
        await conn.execute(text("SET LOCAL timezone='UTC'"))
        before = await foundation_fingerprint(conn)
        await conn.execute(text("INSERT INTO amenities(name) VALUES ('disposable rehearsal')"))
        assert await foundation_fingerprint(conn) != before
        await conn.rollback()
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        await conn.execute(text("SET LOCAL row_security=off"))
        await conn.execute(text("SET LOCAL timezone='UTC'"))
        assert await foundation_fingerprint(conn) == before
