"""T-02 acceptance tests."""

import pytest
from fastapi import APIRouter, Query
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, ValidationError

from app.api.v1 import health
from app.core.config import Settings
from app.main import create_app


def test_unknown_route_uses_safe_error_shape_and_request_id() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.headers["x-request-id"]
    assert response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "We couldn't find that.",
            "fields": {},
            "request_id": response.headers["x-request-id"],
        }
    }
    assert "Traceback" not in response.text


def test_request_id_is_preserved_when_valid() -> None:
    request_id = "d9428888-122b-11e1-b85c-61cd3cbb3210"
    with TestClient(create_app()) as client:
        response = client.get("/healthz", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id


def test_missing_required_configuration_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as error:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="development",
            frontend_origin=AnyHttpUrl("http://localhost:5173"),
        )

    assert "database_url" in str(error.value)


def test_healthz_does_not_call_database(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_database(_: Settings) -> None:
        raise AssertionError("healthz must not call the database")

    monkeypatch.setattr(health, "check_database", fail_database)
    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_ready_when_database_check_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def database_is_ready(_: Settings) -> None:
        return None

    monkeypatch.setattr(health, "check_database", database_is_ready)
    with TestClient(create_app()) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_returns_503_when_database_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def database_is_down(_: Settings) -> None:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(health, "check_database", database_is_down)
    with TestClient(create_app()) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_validation_error_uses_standard_field_shape() -> None:
    router = APIRouter()

    @router.get("/validation-test")
    async def validation_test(limit: int = Query(..., ge=1)) -> dict[str, int]:
        return {"limit": limit}

    application = create_app()
    application.include_router(router)
    with TestClient(application) as client:
        response = client.get("/validation-test", params={"limit": "zero"})

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "Please check the highlighted fields."
    assert body["fields"]["query.limit"]
    assert body["request_id"] == response.headers["x-request-id"]


def test_unexpected_error_is_safe_and_has_request_id() -> None:
    router = APIRouter()

    @router.get("/unexpected-test")
    async def unexpected_test() -> None:
        raise RuntimeError("private database detail")

    application = create_app()
    application.include_router(router)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/unexpected-test")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert response.json()["error"]["message"] == "Something went wrong on our side."
    assert "private database detail" not in response.text
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
