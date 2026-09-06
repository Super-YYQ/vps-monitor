import asyncio
import smtplib

import pytest

from app.detectors import Result
from app.engine import record_result


def add(client, config):
    return client.post("/api/monitors", json=config).json()["ids"][0]


def apply(app, ident, status, version=1, now=1000):
    record_result(app.state.database, ident, version, Result(status, "Test evidence", 15), now=now)


def mail_settings(app):
    app.state.database.set_setting(
        "smtp",
        dict(
            enabled=True,
            host="smtp.example.com",
            port=587,
            security="starttls",
            username="user",
            password="",
            sender="from@example.com",
            recipients=["to@example.com"],
        ),
    )


def counts(app):
    with app.state.database.connect() as db:
        return tuple(
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ["events", "notifications"]
        )


def test_debounce_and_exactly_one_outbox_per_restock(app, authenticated, config):
    ident = add(authenticated, config)
    mail_settings(app)
    apply(app, ident, "out_of_stock")
    assert counts(app) == (0, 0)
    apply(app, ident, "out_of_stock")
    apply(app, ident, "in_stock")
    assert counts(app) == (1, 0)
    apply(app, ident, "in_stock")
    for _ in range(6):
        apply(app, ident, "in_stock")
    assert counts(app) == (2, 1)


def test_error_resets_confirmation_but_preserves_stable_stock(app, authenticated, config):
    ident = add(authenticated, config)
    mail_settings(app)
    for state in ["out_of_stock", "out_of_stock", "in_stock", "error", "in_stock"]:
        apply(app, ident, state)
    assert counts(app) == (1, 0)
    apply(app, ident, "in_stock")
    assert counts(app) == (2, 1)
    for state in ["error", "unknown", "in_stock", "in_stock"]:
        apply(app, ident, state)
    assert counts(app) == (2, 1)


@pytest.mark.parametrize("notify_initial, expected", [(False, 0), (True, 1)])
def test_first_observation_notification_is_opt_in(app, authenticated, config, notify_initial, expected):
    config["notify_initial"] = notify_initial
    ident = add(authenticated, config)
    mail_settings(app)
    apply(app, ident, "in_stock")
    apply(app, ident, "in_stock")
    assert counts(app) == (1, expected)


def test_stale_inflight_fetch_cannot_overwrite_edited_rule(app, authenticated, config):
    ident = add(authenticated, config)
    config["url"] = "https://example.org/other"
    assert authenticated.put(f"/api/monitors/{ident}", json=config).status_code == 200
    apply(app, ident, "in_stock", version=1)
    row = app.state.database.monitors()[0]
    assert row["last_check"] is None and row["stable"] == "unknown"


def test_disabled_monitor_ignores_result(app, authenticated, config):
    config["enabled"] = False
    ident = add(authenticated, config)
    apply(app, ident, "in_stock")
    assert app.state.database.monitors()[0]["last_check"] is None


def test_second_restock_after_soldout_has_new_event(app, authenticated, config):
    mail_settings(app)
    ident = add(authenticated, config)
    for status in ["out_of_stock"] * 2 + ["in_stock"] * 2 + ["out_of_stock"] * 2 + ["in_stock"] * 2:
        apply(app, ident, status)
    assert counts(app) == (4, 2)


def test_mail_retry_is_durable_and_secret_free(app, authenticated):
    mail_settings(app)
    ident = authenticated.post("/api/settings/mail/test").json()["id"]

    def fail(*args):
        raise smtplib.SMTPAuthenticationError(535, b"secret-password")

    app.state.engine.sender = fail
    asyncio.run(app.state.engine.dispatch_mail())
    with app.state.database.connect() as db:
        row = db.execute("SELECT * FROM notifications WHERE id=?", (ident,)).fetchone()
        assert row["attempts"] == 1 and row["status"] == "pending" and "secret-password" not in row["error"]
        assert row["next_try"] > row["created"]
        db.execute("UPDATE notifications SET next_try=0")
    sent = []
    app.state.engine.sender = lambda *args: sent.append(args)
    asyncio.run(app.state.engine.dispatch_mail())
    asyncio.run(app.state.engine.dispatch_mail())
    assert len(sent) == 1
    with app.state.database.connect() as db:
        assert db.execute("SELECT status FROM notifications").fetchone()[0] == "sent"


def test_retry_limit_and_expired_stock_notice(app, authenticated, config):
    mail_settings(app)

    def fail(*args):
        raise OSError("network")

    app.state.engine.sender = fail
    authenticated.post("/api/settings/mail/test")
    for _ in range(5):
        with app.state.database.connect() as db:
            db.execute("UPDATE notifications SET next_try=0")
        asyncio.run(app.state.engine.dispatch_mail())
    assert authenticated.get("/api/notifications").json()[0]["status"] == "failed"
    ident = add(authenticated, {**config, "notify_initial": True})
    apply(app, ident, "in_stock", now=1)
    apply(app, ident, "in_stock", now=2)
    asyncio.run(app.state.engine.dispatch_mail())
    assert authenticated.get("/api/notifications").json()[0]["status"] == "cancelled"


def test_persisted_baseline_survives_new_engine(app, authenticated, config):
    from app.db import Database

    mail_settings(app)
    ident = add(authenticated, config)
    for status in ["out_of_stock"] * 2 + ["in_stock"] * 2:
        apply(app, ident, status)
    reopened = Database(app.state.database.directory)
    record_result(reopened, ident, 1, Result("in_stock", "still available"), now=2000)
    assert counts(app) == (2, 1)


def test_backoff_and_recovery(app, authenticated, config):
    ident = add(authenticated, config)
    for i in range(12):
        apply(app, ident, "error", now=1000)
    row = app.state.database.monitors()[0]
    assert 3600 <= row["next_check"] - 1000 <= 3610
    apply(app, ident, "out_of_stock", now=2000)
    row = app.state.database.monitors()[0]
    assert row["failures"] == 0 and 120 <= row["next_check"] - 2000 <= 130


def test_retention_prunes_check_history(app, authenticated, config):
    ident = add(authenticated, config)
    apply(app, ident, "out_of_stock", now=1)
    app.state.engine.cleanup(100 * 86400)
    assert authenticated.get(f"/api/monitors/{ident}/history").json() == []


def test_scheduler_runs_full_restock_to_delivery(app, authenticated, config):
    import time

    from app.fetcher import Page

    ident = add(authenticated, {**config, "confirmations": 1})
    mail_settings(app)
    engine = app.state.engine
    available = False
    delivered = []

    def fake_fetch(url):
        text = "<tr><td>LAX Basic " + ("Buy now" if available else "Sold Out") + "</td></tr>"
        return Page(200, text, url, 4)

    engine.fetch = fake_fetch
    engine.sender = lambda *args: delivered.append(args)

    async def scenario():
        nonlocal available
        task = asyncio.create_task(engine.run())
        try:
            deadline = time.monotonic() + 8
            while app.state.database.monitors()[0]["stable"] != "out_of_stock":
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            available = True
            with app.state.database.connect() as db:
                db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
            engine.host_due.clear()
            while not delivered:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            assert engine.heartbeat > 0
            assert counts(app) == (2, 1)
            assert app.state.database.monitors()[0]["stable"] == "in_stock"
        finally:
            engine.alive = False
            await task

    asyncio.run(scenario())
    assert "LAX Basic" in delivered[0][1] and "https://example.com/products" in delivered[0][2]
