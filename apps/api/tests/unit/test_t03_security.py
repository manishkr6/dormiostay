import json
import logging
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.logging import JsonFormatter
from app.core.sentry import sanitize_event
from app.db.session import async_url
from app.db.test_target import validate_test_url, verify_test_connection
from app.main import create_app

NAME = "dormiostay_t03_test_guard"


@pytest.mark.parametrize(
    "query",
    [
        "database=dormiostay",
        "dbname=dormiostay",
        "host=other",
        "port=5432",
        "user=admin",
        "password=secret",
        "dsn=postgresql://other",
        "server_settings=x",
        "ssl=disable&ssl=require",
        "sslmode=require&ssl=require",
        "%64atabase=dormiostay",
    ],
)
def test_reject_target_overrides(query: str) -> None:
    with pytest.raises(ValueError):
        validate_test_url(f"postgresql://test@127.0.0.1/{NAME}?{query}", NAME)


@pytest.mark.parametrize(
    "database,host,approval",
    [
        ("dormiostay", "127.0.0.1", NAME),
        ("postgres", "127.0.0.1", NAME),
        (NAME, "example.com", NAME),
        (NAME, "127.0.0.1", None),
        (NAME, "127.0.0.1", "dormiostay_t03_test_other"),
    ],
)
def test_reject_nonapproved_targets(database: str, host: str, approval: str | None) -> None:
    with pytest.raises(ValueError):
        validate_test_url(f"postgresql://test@{host}/{database}", approval)


def test_effective_database_must_match() -> None:
    import asyncio

    connection = AsyncMock()
    connection.scalar.return_value = "dormiostay"
    with pytest.raises(RuntimeError):
        asyncio.run(verify_test_connection(connection, NAME))


def test_tls_option_is_preserved() -> None:
    url = async_url("postgresql://test@localhost/db?sslmode=require")
    assert url.query == {"ssl": "require"}


def test_sensitive_exception_and_record_are_not_logged() -> None:
    secret = "synthetic-sensitive-value"
    try:
        raise IntegrityError(secret, {"password": secret}, Exception(secret))
    except IntegrityError:
        record = logging.LogRecord(secret, logging.ERROR, secret, 1, secret, (), sys.exc_info())
    record.request_id = "12345678-1234-1234-1234-123456789012"
    output = JsonFormatter().format(record)
    assert secret not in output
    assert json.loads(output)["code"] == "INTERNAL_SERVER_ERROR"
    assert json.loads(output)["request_id"] == "12345678-1234-1234-1234-123456789012"


def test_sentry_discards_sensitive_event_fields() -> None:
    secret = "synthetic-sensitive-value"
    event = sanitize_event(
        {
            "message": secret,
            "extra": {"sql": secret},
            "request": {"data": secret, "headers": {"Authorization": secret}},
            "exception": {"values": [{"value": secret}]},
            "tags": {"request_id": "12345678-1234-1234-1234-123456789012", "password": secret},
        },
        {},
    )
    assert secret not in json.dumps(event)
    assert event["tags"]["code"] == "INTERNAL_SERVER_ERROR"


def test_startup_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unsafe(_: str) -> None:
        raise RuntimeError("synthetic-sensitive-value")

    monkeypatch.setattr("app.main.check_runtime_database", unsafe)
    with pytest.raises(RuntimeError, match="^DATABASE_STARTUP_CHECK_FAILED$"):
        with TestClient(create_app()):
            pass
