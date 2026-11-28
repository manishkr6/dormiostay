from typing import Any

import pytest

from app.db.render_readiness import RenderInventory


@pytest.mark.parametrize(
    "override",
    [
        {"role": "neondb_owner"},
        {"host": "unverified.invalid"},
        {"sslmode": "require"},
        {"worker_enabled": True},
        {"auth_endpoints_enabled": True},
        {"api_proxy_enabled": True},
        {"migration_credentials_present": True},
        {"DATABASE_URL": "redacted"},
    ],
)
def test_offline_render_boundary(override: dict[str, Any]) -> None:
    inventory = dict(
        app_env="staging",
        role="dormiostay_app",
        database="neondb",
        port=5432,
        sslmode="verify-full",
        host="ep-cold-smoke-bir1s74t-pooler.c-5.ap-southeast-1.aws.neon.tech",
        worker_enabled=False,
        auth_endpoints_enabled=False,
        api_proxy_enabled=False,
        migration_credentials_present=False,
    )
    RenderInventory.model_validate(inventory).check()
    with pytest.raises(ValueError):
        RenderInventory.model_validate(inventory | override).check()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "observed",
    [
        ("neondb", "other", "dormiostay_app", "on"),
        ("other", "dormiostay_app", "dormiostay_app", "on"),
        ("neondb", "dormiostay_app", "dormiostay_app", "off"),
    ],
)
async def test_required_server_observations(observed: tuple[str, ...]) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.db.render_readiness import verify_runtime_session

    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.one.return_value = observed
    with pytest.raises(RuntimeError, match="Unexpected Render"):
        await verify_runtime_session(session)
