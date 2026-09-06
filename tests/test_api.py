import json

import pytest


def test_authentication_and_csrf(client, config):
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    login = client.post("/api/login", json={"password": "test-password-at-least-12"})
    assert "HttpOnly" in login.headers["set-cookie"] and "SameSite=strict" in login.headers["set-cookie"]
    assert client.post("/api/monitors", json=config).status_code == 403
    client.headers["X-CSRF-Token"] = login.json()["csrf"]
    assert (
        client.post("/api/monitors", json=config, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.post("/api/monitors", json=config).status_code == 201


def test_crud_export_import_and_duplicate_skip(authenticated, config):
    ident = authenticated.post("/api/monitors", json=config).json()["ids"][0]
    assert authenticated.post("/api/monitors", json=config).json()["ids"] == []
    config["name"] = "LAX Core"
    assert authenticated.put(f"/api/monitors/{ident}", json=config).status_code == 200
    exported = authenticated.get("/api/export").json()
    assert len(exported["monitors"]) == 1 and "password" not in json.dumps(exported)
    exported["monitors"][0]["name"] = "Imported"
    response = authenticated.post("/api/import", json=exported)
    assert len(response.json()["ids"]) == 1
    rows = authenticated.get("/api/dashboard").json()["monitors"]
    assert len(rows) == 2 and rows[1]["config"]["enabled"] is False
    assert authenticated.delete(f"/api/monitors/{ident}").status_code == 200
    assert authenticated.delete(f"/api/monitors/{ident}").status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"url": "file:///etc/passwd"},
        {"url": "http://name:pass@example.com"},
        {"url": "http://example.com:22"},
        {"selector": "["},
        {"selector": ""},
        {"interval": 1},
        {"confirmations": 0},
        {"in_stock": [""]},
        {"adapter": "json", "json_path": ""},
        {"adapter": "whmcs", "expected_text": ""},
        {"name": ""},
    ],
)
def test_rejects_invalid_configuration(authenticated, config, change):
    assert authenticated.post("/api/monitors", json={**config, **change}).status_code == 422


def test_import_validation_is_atomic(authenticated, config):
    response = authenticated.post("/api/import", json={"monitors": [config, {**config, "interval": 0}]})
    assert response.status_code == 422
    assert authenticated.get("/api/dashboard").json()["monitors"] == []


def test_smtp_secret_encrypted_omitted_and_preserved(app, authenticated):
    payload = dict(
        enabled=True,
        host="smtp.example.com",
        port=587,
        security="starttls",
        username="user",
        password="top-secret-app-code",
        sender="radar@example.com",
        recipients=["user@example.com"],
    )
    assert authenticated.put("/api/settings/mail", json=payload).status_code == 200
    raw = app.state.database.setting("smtp")
    assert raw["password"] != "top-secret-app-code"
    assert app.state.vault.decrypt(raw["password"]) == "top-secret-app-code"
    response = authenticated.get("/api/settings/mail")
    assert (
        response.json()["has_password"]
        and "top-secret" not in response.text
        and "password" not in response.json()
    )
    payload["password"] = ""
    authenticated.put("/api/settings/mail", json=payload)
    assert app.state.vault.decrypt(app.state.database.setting("smtp")["password"]) == "top-secret-app-code"


def test_validation_errors_never_echo_secret(authenticated):
    payload = dict(enabled=True, host="smtp.example.com", sender="bad", password="top-secret")
    response = authenticated.put("/api/settings/mail", json=payload)
    assert response.status_code == 422 and "top-secret" not in response.text


def test_password_change_revokes_all_sessions(authenticated):
    response = authenticated.post(
        "/api/password",
        json={"current_password": "test-password-at-least-12", "new_password": "new-password-at-least-12"},
    )
    assert response.status_code == 200
    assert authenticated.get("/api/session").status_code == 401
    assert authenticated.post("/api/login", json={"password": "new-password-at-least-12"}).status_code == 200


def test_login_throttle(client):
    for _ in range(10):
        assert client.post("/api/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/login", json={"password": "bad"}).status_code == 429


def test_public_assets_and_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200 and "VPS Radar" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}


def test_catalog_models_are_valid_and_paused(authenticated):
    from app.models import Monitor

    catalog = authenticated.get("/api/catalog").json()
    assert len(catalog["providers"]) == 7
    for preset in catalog["presets"]:
        config = Monitor(**preset["config"])
        assert not config.enabled


def test_manual_check_paused_and_missing(authenticated, config):
    ident = authenticated.post("/api/monitors", json={**config, "enabled": False}).json()["ids"][0]
    assert authenticated.post(f"/api/monitors/{ident}/check").status_code == 400
    assert authenticated.post("/api/monitors/missing/check").status_code == 404


def test_large_request_body_is_rejected(authenticated):
    response = authenticated.post("/api/import", content="x" * 140000)
    assert response.status_code == 413


def test_example_password_cannot_bootstrap_server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("ADMIN_PASSWORD", "replace-with-a-unique-password-at-least-12-characters")
    with pytest.raises(RuntimeError):
        with TestClient(create_app(tmp_path, start_engine=False)):
            pass
