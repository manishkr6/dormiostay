from fastapi.testclient import TestClient

from app.main import app


def test_empty_api_starts() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "DormioStay API"
    assert response.json()["paths"] == {}
