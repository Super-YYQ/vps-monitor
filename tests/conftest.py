import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "test-password-at-least-12")
    monkeypatch.delenv("APP_ORIGIN", raising=False)
    monkeypatch.delenv("SECURE_COOKIES", raising=False)
    return create_app(tmp_path, start_engine=False)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def authenticated(client):
    response = client.post("/api/login", json={"password": "test-password-at-least-12"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return client


@pytest.fixture
def config():
    return {
        "provider": "Example",
        "name": "LAX Basic",
        "url": "https://example.com/products",
        "selector": "tr",
        "product_match": "LAX Basic",
        "expected_text": "LAX Basic",
        "in_stock": ["Buy now"],
        "out_of_stock": ["Sold Out"],
        "enabled": True,
    }
