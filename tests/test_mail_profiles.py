import asyncio


def profile(name, password):
    return dict(
        name=name,
        enabled=True,
        host="smtp.example.com",
        port=465,
        security="ssl",
        username=f"{name}@example.com",
        password=password,
        sender=f"{name}@example.com",
        recipients=[f"{name}-inbox@example.com"],
    )


def test_legacy_mail_is_migrated_without_losing_secret(app, authenticated):
    old = profile("legacy", "legacy-secret")
    old.pop("name")
    old["password"] = app.state.vault.encrypt(old["password"])
    app.state.database.set_setting("smtp", old)
    response = authenticated.get("/api/settings/mail/profiles")
    assert response.status_code == 200
    store = response.json()
    assert len(store["profiles"]) == 1
    saved = store["profiles"][0]
    assert store["active_id"] == saved["id"]
    assert saved["has_password"] and "password" not in saved
    assert "legacy-secret" not in response.text
    assert app.state.engine.smtp_config().password == "legacy-secret"


def test_switch_during_delivery_does_not_send_cancelled_tests(app, authenticated):
    first = authenticated.post("/api/settings/mail/profiles", json=profile("first", "secret"))
    assert first.status_code == 201
    second_id = authenticated.post("/api/settings/mail/profiles", json=profile("second", "secret")).json()[
        "id"
    ]
    authenticated.post("/api/settings/mail/test")
    authenticated.post("/api/settings/mail/test")
    sent = []

    def sender(config, *_):
        sent.append(config.sender)
        authenticated.post(f"/api/settings/mail/profiles/{second_id}/activate")

    app.state.engine.sender = sender
    asyncio.run(app.state.engine.dispatch_mail())
    assert sent == ["first@example.com"]
    assert [n["status"] for n in authenticated.get("/api/notifications").json()] == ["cancelled", "sent"]


def test_profile_switch_preserves_each_secret_and_changes_delivery(app, authenticated):
    first = authenticated.post("/api/settings/mail/profiles", json=profile("first", "first-secret"))
    assert first.status_code == 201
    first_id = first.json()["id"]
    second = authenticated.post("/api/settings/mail/profiles", json=profile("second", "second-secret"))
    assert second.status_code == 201
    second_id = second.json()["id"]
    assert app.state.engine.smtp_config().password == "first-secret"
    update = {**profile("second", ""), "host": "backup.example.com"}
    assert authenticated.put(f"/api/settings/mail/profiles/{second_id}", json=update).status_code == 200
    assert authenticated.post(f"/api/settings/mail/profiles/{second_id}/activate").status_code == 200
    sent = []
    app.state.engine.sender = lambda config, *_: sent.append(config)
    assert authenticated.post("/api/settings/mail/test").status_code == 202
    asyncio.run(app.state.engine.dispatch_mail())
    assert len(sent) == 1
    assert sent[0].password == "second-secret" and sent[0].host == "backup.example.com"
    assert sent[0].recipients == ["second-inbox@example.com"]
    assert authenticated.post(f"/api/settings/mail/profiles/{first_id}/activate").status_code == 200
    assert app.state.engine.smtp_config().password == "first-secret"
    assert "secret" not in authenticated.get("/api/settings/mail/profiles").text


def test_switch_cancels_queued_test_and_delete_active_disables_mail(app, authenticated):
    first = authenticated.post("/api/settings/mail/profiles", json=profile("first", "secret"))
    assert first.status_code == 201
    second_id = authenticated.post("/api/settings/mail/profiles", json=profile("second", "secret")).json()[
        "id"
    ]
    authenticated.post("/api/settings/mail/test")
    authenticated.post(f"/api/settings/mail/profiles/{second_id}/activate")
    assert authenticated.get("/api/notifications").json()[0]["status"] == "cancelled"
    assert authenticated.delete(f"/api/settings/mail/profiles/{second_id}").status_code == 200
    assert not app.state.engine.smtp_config().enabled
    assert not authenticated.get("/api/dashboard").json()["mail_enabled"]
    assert authenticated.get("/api/settings/mail/profiles").json()["active_id"] is None
    assert authenticated.post("/api/settings/mail/test").status_code == 400


def test_profiles_survive_reopen_and_legacy_put_updates_active(app, authenticated):
    first = authenticated.post("/api/settings/mail/profiles", json=profile("first", "secret"))
    assert first.status_code == 201
    ident = first.json()["id"]
    legacy = profile("changed", "")
    legacy.pop("name")
    authenticated.put("/api/settings/mail", json=legacy)
    from app.main import create_app

    reopened = create_app(app.state.database.directory, start_engine=False)
    assert reopened.state.engine.smtp_config().sender == "changed@example.com"
    assert reopened.state.engine.smtp_config().password == "secret"
    store = authenticated.get("/api/settings/mail/profiles").json()
    assert store["active_id"] == ident and len(store["profiles"]) == 1
    assert store["profiles"][0]["sender"] == "changed@example.com"


def test_invalid_and_missing_profile_leave_active_unchanged(app, authenticated):
    created = authenticated.post("/api/settings/mail/profiles", json=profile("first", "secret"))
    assert created.status_code == 201
    assert authenticated.post("/api/settings/mail/profiles/missing/activate").status_code == 404
    assert authenticated.delete("/api/settings/mail/profiles/missing").status_code == 404
    response = authenticated.post(
        "/api/settings/mail/profiles", json={**profile("bad", "secret"), "sender": "bad"}
    )
    assert response.status_code == 422 and "secret" not in response.text
    assert app.state.engine.smtp_config().sender == "first@example.com"
