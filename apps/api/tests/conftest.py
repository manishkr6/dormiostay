"""Safe test defaults; production settings remain fail-fast."""

import os

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ["DATABASE_URL"] = "not-used-by-unit-tests"
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:5173")


@pytest.fixture(autouse=True)
def isolate_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_database(_: str) -> None:
        pass

    monkeypatch.setattr("app.main.check_runtime_database", no_database)
