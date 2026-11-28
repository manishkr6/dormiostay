import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.security_catalog import FOUNDATION, verify_catalog

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "change",
    [
        "DELETE FROM public.alembic_version",
        "UPDATE public.alembic_version SET version_num='wrong'",
        "INSERT INTO public.alembic_version VALUES ('extra')",
        "ALTER ROLE dormiostay_app RENAME TO missing_role",
        "ALTER ROLE dormiostay_app CREATEROLE",
        "GRANT pg_read_all_data TO dormiostay_app",
        "GRANT SELECT(action) ON audit_logs TO PUBLIC",
        "ALTER TABLE users ADD COLUMN unexpected text",
        "ALTER TABLE audit_logs ALTER COLUMN reason TYPE varchar",
        "ALTER TABLE audit_logs DROP COLUMN reason",
        "ALTER TABLE audit_logs NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE audit_logs DISABLE TRIGGER audit_immutable",
        "ALTER POLICY audit_admin_read ON audit_logs USING (false)",
    ],
)
async def test_foundation_drift(engine: AsyncEngine, change: str) -> None:
    async with engine.connect() as conn:
        await verify_catalog(conn, FOUNDATION)
        await conn.execute(text(change))
        with pytest.raises((RuntimeError, DBAPIError)):
            await verify_catalog(conn, FOUNDATION)
        await conn.rollback()
        await verify_catalog(conn, FOUNDATION)
