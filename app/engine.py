import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.detectors import Result, inspect_monitor
from app.fetcher import fetch_page
from app.mailer import mail_error, send_mail
from app.models import MailSettings, Monitor

logger = logging.getLogger(__name__)
KNOWN = {"in_stock", "out_of_stock"}


def record_result(database, monitor_id, version, result: Result, now=None):
    now = time.time() if now is None else now
    with database.connect() as db:
        row = db.execute("SELECT * FROM monitors WHERE id=?", (monitor_id,)).fetchone()
        if not row or row["version"] != version:
            return  # Discard a fetch that completed after this monitor was edited/deleted.
        config = Monitor.model_validate_json(row["config"])
        if not config.enabled:
            return
        known = result.status in KNOWN
        streak = row["streak"] + 1 if known and row["candidate"] == result.status else int(known)
        failures = 0 if known else row["failures"] + 1
        delay = min(config.interval * (2 ** min(failures, 5)), 3600) if failures else config.interval
        delay += random.uniform(0, min(10, delay * 0.1))
        stable = row["stable"]
        changed = known and streak >= config.confirmations and stable != result.status
        last_change = now if changed else row["last_change"]
        if changed:
            event_id = db.execute(
                "INSERT INTO events(monitor_id,name,previous,status,created) VALUES (?,?,?,?,?)",
                (monitor_id, f"{config.provider} · {config.name}", stable, result.status, now),
            ).lastrowid
            smtp_row = db.execute("SELECT value FROM settings WHERE key='smtp'").fetchone()
            smtp_enabled = smtp_row and json.loads(smtp_row[0]).get("enabled")
            should_notify = config.notify and smtp_enabled and result.status == "in_stock"
            should_notify = should_notify and (stable != "unknown" or config.notify_initial)
            if should_notify:
                timestamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                body = (
                    f"{config.provider} / {config.name} 已检测到有货。\n\n"
                    f"配置：{config.specs or '见商家页面'}\n价格参考：{config.price_note or '见商家页面'}\n"
                    f"检测时间：{timestamp}\n连续确认：{config.confirmations} 次\n"
                    f"证据：{result.reason}\n购买：{config.purchase_url or config.url}\n"
                    f"检测页面：{config.url}\n\n库存及最终价格以商家结账页为准。此工具不会自动下单。"
                )
                db.execute(
                    "INSERT INTO notifications(event_id,subject,body,created) VALUES (?,?,?,?)",
                    (event_id, f"[VPS Radar 补货] {config.provider} {config.name}", body, now),
                )
            stable = result.status
        db.execute(
            """UPDATE monitors SET status=?,stable=?,candidate=?,streak=?,failures=?,
                      last_check=?,next_check=?,last_change=?,reason=?,latency=? WHERE id=?""",
            (
                result.status,
                stable,
                result.status,
                streak,
                failures,
                now,
                now + delay,
                last_change,
                result.reason[:500],
                result.latency,
                monitor_id,
            ),
        )
        db.execute(
            "INSERT INTO checks(monitor_id,status,reason,latency,created) VALUES (?,?,?,?,?)",
            (monitor_id, result.status, result.reason[:500], result.latency, now),
        )


class Engine:
    def __init__(self, database, vault, fetch=fetch_page, sender=send_mail):
        self.db, self.vault, self.fetch, self.sender = database, vault, fetch, sender
        self.running = set()
        self.tasks = set()
        self.host_due = {}
        self.host_running = set()
        self.alive = False
        self.heartbeat = 0
        self.last_cleanup = 0

    def smtp_config(self):
        raw = self.db.setting("smtp", {})
        raw["password"] = self.vault.decrypt(raw.get("password", ""))
        return MailSettings.model_validate(raw)

    async def check(self, row):
        try:
            config = Monitor.model_validate(row["config"])
            try:
                result = await asyncio.to_thread(inspect_monitor, config, self.fetch)
            except ValueError as exc:
                result = Result("unknown", str(exc)[:300])
            except Exception:
                result = Result("error", "网络请求失败或超时；稍后自动重试")
            record_result(self.db, row["id"], row["version"], result)
        except Exception:
            logger.exception("Failed to record monitor result")
        finally:
            self.running.discard(row["id"])
            host = urlsplit(row["config"]["url"]).hostname
            self.host_running.discard(host)
            self.host_due[host] = time.time() + 5

    async def dispatch_mail(self):
        config = self.smtp_config()
        if not config.enabled:
            return
        now = time.time()
        with self.db.connect() as db:
            rows = db.execute(
                """SELECT n.*,e.monitor_id FROM notifications n LEFT JOIN events e ON e.id=n.event_id
                               WHERE n.status='pending' AND n.next_try<=? ORDER BY n.id LIMIT 5""",
                (now,),
            ).fetchall()
        for row in rows:
            if row["event_id"]:
                with self.db.connect() as db:
                    monitor = db.execute("SELECT * FROM monitors WHERE id=?", (row["monitor_id"],)).fetchone()
                    stale = now - row["created"] > 3600 or not monitor
                    if monitor:
                        cfg = json.loads(monitor["config"])
                        stale = (
                            stale
                            or monitor["stable"] != "in_stock"
                            or not cfg["enabled"]
                            or not cfg["notify"]
                        )
                    if stale:
                        db.execute(
                            "UPDATE notifications SET status='cancelled',error=? WHERE id=?",
                            ("补货通知已过期、库存已变化或监控已停用", row["id"]),
                        )
                        continue
            try:
                await asyncio.to_thread(self.sender, config, row["subject"], row["body"])
                with self.db.connect() as db:
                    db.execute(
                        "UPDATE notifications SET status='sent',sent=?,attempts=attempts+1,error='' WHERE id=?",
                        (time.time(), row["id"]),
                    )
            except Exception as exc:
                attempts = row["attempts"] + 1
                with self.db.connect() as db:
                    db.execute(
                        "UPDATE notifications SET status=?,attempts=?,next_try=?,error=? WHERE id=?",
                        (
                            "failed" if attempts >= 5 else "pending",
                            attempts,
                            time.time() + min(30 * 2**attempts, 1800),
                            mail_error(exc),
                            row["id"],
                        ),
                    )

    def cleanup(self, now):
        if now - self.last_cleanup < 3600:
            return
        with self.db.connect() as db:
            db.execute("DELETE FROM checks WHERE created<?", (now - 7 * 86400,))
            db.execute("DELETE FROM events WHERE created<?", (now - 90 * 86400,))
            db.execute("DELETE FROM notifications WHERE created<?", (now - 90 * 86400,))
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            # Bounds are useful on small disks even with hundreds of monitors.
            db.execute(
                "DELETE FROM checks WHERE id NOT IN (SELECT id FROM checks ORDER BY id DESC LIMIT 200000)"
            )
        self.last_cleanup = now

    async def run(self):
        self.alive = True
        mail_task = None
        try:
            while self.alive:
                now = time.time()
                self.heartbeat = now
                try:
                    for row in self.db.monitors():
                        cfg = row["config"]
                        host = urlsplit(cfg["url"]).hostname
                        if (
                            not cfg["enabled"]
                            or row["id"] in self.running
                            or row["next_check"] > now
                            or self.host_due.get(host, 0) > now
                            or host in self.host_running
                            or len(self.running) >= 4
                        ):
                            continue
                        self.host_due[host] = now + 5
                        self.host_running.add(host)
                        self.running.add(row["id"])
                        task = asyncio.create_task(self.check(row))
                        self.tasks.add(task)
                        task.add_done_callback(self.tasks.discard)
                    if mail_task is None or mail_task.done():
                        if mail_task is not None and mail_task.exception():
                            logger.error("Notification worker failed; will retry")
                        mail_task = asyncio.create_task(self.dispatch_mail())
                    self.cleanup(now)
                except Exception:
                    logger.exception("Scheduler tick failed; will retry")
                await asyncio.sleep(1)
        finally:
            self.alive = False
            # Let bounded network requests finish before releasing the data-directory lock.
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            if mail_task:
                await asyncio.gather(mail_task, return_exceptions=True)
