import asyncio
import hmac
import json
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db import Database
from app.detectors import discover_whmcs, inspect_monitor
from app.engine import Engine
from app.fetcher import fetch_page
from app.mail_profiles import MailProfiles
from app.mirror import mirror_result
from app.models import MailProfile, MailSettings, Monitor, public_url
from app.security import InstanceLock, Vault, password_hash, password_matches, token_hash

ROOT = Path(__file__).parent


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=500)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=500)
    new_password: str = Field(min_length=12, max_length=500)


class Discover(BaseModel):
    url: str = Field(max_length=2048)


class ImportData(BaseModel):
    monitors: list[Monitor] = Field(max_length=100)


def create_app(data_dir=None, start_engine=True):
    directory = Path(data_dir or os.getenv("DATA_DIR", "data"))
    database = Database(directory)
    vault = Vault(directory)
    mail_profiles = MailProfiles(database, vault)
    engine = Engine(database, vault)
    attempts = defaultdict(deque)
    sensitive_calls = defaultdict(deque)
    secure_cookie = os.getenv("SECURE_COOKIES", "false").lower() == "true"

    @asynccontextmanager
    async def lifespan(app):
        lock = InstanceLock(directory)
        task = None
        try:
            if not database.setting("admin_hash"):
                password = os.getenv("ADMIN_PASSWORD") or secrets.token_urlsafe(24)
                if len(password) < 12 or password == "replace-with-a-unique-password-at-least-12-characters":
                    raise RuntimeError("ADMIN_PASSWORD 至少需要 12 个字符，且不能使用示例占位密码")
                database.set_setting("admin_hash", password_hash(password))
                if not os.getenv("ADMIN_PASSWORD"):
                    path = directory / "initial-password.txt"
                    path.write_text(password, encoding="utf-8")
                    os.chmod(path, 0o600)
            if start_engine:
                task = asyncio.create_task(engine.run())
            yield
        finally:
            engine.alive = False
            if task:
                await task
            lock.close()

    app = FastAPI(
        title="VPS Radar", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.database = database
    app.state.engine = engine
    app.state.vault = vault

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Omit `input` from validation errors, particularly passwords/SMTP secrets.
        return JSONResponse(
            {
                "detail": "; ".join(
                    f"{'.'.join(str(i) for i in e['loc'][1:])}: {e['msg']}" for e in exc.errors()
                )
            },
            status_code=422,
        )

    def rate_limit(bucket, key, count, period):
        now = time.time()
        if len(bucket) > 2000:
            for old_key in list(bucket):
                if not bucket[old_key] or bucket[old_key][-1] < now - period:
                    del bucket[old_key]
            if len(bucket) > 2000:
                raise HTTPException(429, "请求过多，请稍后重试")
        queue = bucket[key]
        while queue and queue[0] < now - period:
            queue.popleft()
        if len(queue) >= count:
            raise HTTPException(429, "请求过于频繁，请稍后重试")
        queue.append(now)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }
        try:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                expected_origin = os.getenv("APP_ORIGIN", "").rstrip("/") or str(request.base_url).rstrip("/")
                origin = request.headers.get("origin")
                if origin and origin != expected_origin:
                    raise HTTPException(403, "请求来源不匹配")
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 128 * 1024:
                        raise HTTPException(413, "请求内容过大，单次最多 128 KB")
                request._body = bytes(body)
            path = request.url.path
            if path.startswith("/api/") and path != "/api/login":
                token = request.cookies.get("radar_session", "")
                with database.connect() as db:
                    session = db.execute(
                        "SELECT * FROM sessions WHERE token=? AND expires>?", (token_hash(token), time.time())
                    ).fetchone()
                if not session:
                    raise HTTPException(401, "请先登录")
                request.state.session = dict(session)
                if request.method not in {"GET", "HEAD"}:
                    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
                        raise HTTPException(403, "会话校验失败，请刷新后重试")
                    rate_limit(sensitive_calls, session["token"], 120, 60)
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        response.headers.update(headers)
        return response

    @app.get("/healthz")
    async def health():
        healthy = not start_engine or engine.alive and time.time() - engine.heartbeat < 30
        return JSONResponse({"status": "ok" if healthy else "degraded"}, status_code=200 if healthy else 503)

    @app.post("/api/login")
    async def login(payload: Login, request: Request):
        ip = request.client.host if request.client else "unknown"
        rate_limit(attempts, ip, 10, 900)
        valid = await asyncio.to_thread(password_matches, payload.password, database.setting("admin_hash"))
        if not valid:
            raise HTTPException(401, "密码不正确")
        attempts.pop(ip, None)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with database.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (token_hash(token), csrf, time.time() + 86400))
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            "radar_session",
            token,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            max_age=86400,
            path="/",
        )
        return response

    @app.get("/api/session")
    async def session(request: Request):
        return {"csrf": request.state.session["csrf"]}

    @app.post("/api/logout")
    async def logout(request: Request):
        with database.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (request.state.session["token"],))
        response = JSONResponse({"ok": True})
        response.delete_cookie("radar_session")
        return response

    @app.post("/api/password")
    async def change_password(payload: PasswordChange):
        if not await asyncio.to_thread(
            password_matches, payload.current_password, database.setting("admin_hash")
        ):
            raise HTTPException(400, "当前密码不正确")
        database.set_setting("admin_hash", await asyncio.to_thread(password_hash, payload.new_password))
        with database.connect() as db:
            db.execute("DELETE FROM sessions")
        initial = directory / "initial-password.txt"
        initial.unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/dashboard")
    async def dashboard():
        now = time.time()
        rows = database.monitors()
        with database.connect() as db:
            stats = dict(
                db.execute(
                    """SELECT COUNT(*) AS checks, SUM(status IN ('in_stock','out_of_stock')) AS success
                                      FROM checks WHERE created>?""",
                    (now - 86400,),
                ).fetchone()
            )
            events = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")]
            pending = db.execute("SELECT COUNT(*) FROM notifications WHERE status='pending'").fetchone()[0]
            buckets = [
                dict(row)
                for row in db.execute(
                    """SELECT CAST(created/3600 AS INTEGER)*3600 AS hour,
                         COUNT(*) AS total, SUM(status IN ('in_stock','out_of_stock')) AS success
                         FROM checks WHERE created>? GROUP BY hour ORDER BY hour""",
                    (now - 86400,),
                )
            ]
        return {
            "monitors": rows,
            "stats": stats,
            "events": events,
            "buckets": buckets,
            "pending_mail": pending,
            "mail_enabled": database.setting("smtp", {}).get("enabled", False),
            "scheduler": {
                "running": engine.alive,
                "heartbeat": engine.heartbeat,
                "active_checks": len(engine.running),
            },
            "now": now,
        }

    @app.get("/api/catalog")
    async def catalog():
        return json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))

    def insert_monitors(configs):
        ids = []
        with database.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM monitors").fetchone()[0]
            if total + len(configs) > 500:
                raise HTTPException(400, "单实例最多 500 个监控")
            existing = {
                (r["provider"].casefold(), r["name"].casefold(), r["url"])
                for (raw,) in db.execute("SELECT config FROM monitors")
                for r in [json.loads(raw)]
            }
            for config in configs:
                key = (config.provider.casefold(), config.name.casefold(), config.url)
                if key in existing:
                    continue
                ident = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO monitors(id,config,created) VALUES (?,?,?)",
                    (ident, config.model_dump_json(), time.time()),
                )
                ids.append(ident)
                existing.add(key)
        return ids

    @app.get("/api/settings/mirror")
    async def get_mirror():
        return database.setting("mirror", {"enabled": True})

    @app.put("/api/settings/mirror")
    async def save_mirror(payload: dict):
        enabled = bool(payload.get("enabled", True))
        database.set_setting("mirror", {"enabled": enabled})
        return {"ok": True, "enabled": enabled}

    @app.post("/api/monitors", status_code=201)
    async def add_monitor(config: Monitor):
        return {"ids": insert_monitors([config])}

    @app.put("/api/monitors/{ident}")
    async def update_monitor(ident: str, config: Monitor):
        with database.connect() as db:
            row = db.execute("SELECT * FROM monitors WHERE id=?", (ident,)).fetchone()
            if not row:
                raise HTTPException(404, "监控不存在")
            old = json.loads(row["config"])
            detection_keys = {
                "url",
                "adapter",
                "selector",
                "product_match",
                "expected_text",
                "json_path",
                "in_stock",
                "out_of_stock",
                "confirmations",
            }
            reset = any(old[k] != config.model_dump()[k] for k in detection_keys)
            reset = reset or old["enabled"] != config.enabled
            db.execute(
                "UPDATE monitors SET config=?,version=version+1,next_check=0 WHERE id=?",
                (config.model_dump_json(), ident),
            )
            if reset:
                db.execute(
                    """UPDATE monitors SET status='unknown',stable='unknown',candidate='unknown',streak=0,
                             failures=0,last_check=NULL,last_change=NULL,latency=NULL,reason='配置已更新，等待检测' WHERE id=?""",
                    (ident,),
                )
                db.execute(
                    """UPDATE notifications SET status='cancelled',error='检测配置已变化'
                              WHERE status='pending' AND event_id IN (SELECT id FROM events WHERE monitor_id=?)""",
                    (ident,),
                )
        return {"ok": True}

    @app.delete("/api/monitors/{ident}")
    async def delete_monitor(ident: str):
        with database.connect() as db:
            cursor = db.execute("DELETE FROM monitors WHERE id=?", (ident,))
            if not cursor.rowcount:
                raise HTTPException(404, "监控不存在")
        return {"ok": True}

    @app.post("/api/monitors/{ident}/check", status_code=202)
    async def check_now(ident: str):
        with database.connect() as db:
            row = db.execute("SELECT * FROM monitors WHERE id=?", (ident,)).fetchone()
            if not row:
                raise HTTPException(404, "监控不存在")
            if not json.loads(row["config"])["enabled"]:
                raise HTTPException(400, "请先启用监控；也可在编辑窗口试运行规则")
            if row["last_check"] and time.time() - row["last_check"] < 10:
                raise HTTPException(429, "刚刚完成检查，请 10 秒后再试")
            db.execute("UPDATE monitors SET next_check=0 WHERE id=?", (ident,))
        return {"ok": True, "message": "已加入检查队列，同站点保持至少 5 秒间隔"}

    @app.get("/api/monitors/{ident}/history")
    async def history(ident: str):
        with database.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM checks WHERE monitor_id=? ORDER BY id DESC LIMIT 100", (ident,)
                )
            ]

    @app.post("/api/probe")
    async def probe(config: Monitor, request: Request):
        rate_limit(sensitive_calls, "probe:" + request.state.session["token"], 6, 60)
        if not config.url:
            raise HTTPException(400, "请填写检测地址")
        mirror = mirror_result if database.setting("mirror", {"enabled": True}).get("enabled") else None
        try:
            return vars(await asyncio.to_thread(inspect_monitor, config, fetch_page, mirror))
        except ValueError as exc:
            return {"status": "unknown", "reason": str(exc), "latency": 0}
        except Exception:
            return {"status": "error", "reason": "请求超时或网络不可达", "latency": 0}

    @app.post("/api/discover")
    async def discover(payload: Discover, request: Request):
        rate_limit(sensitive_calls, "discover:" + request.state.session["token"], 4, 60)
        try:
            if not payload.url:
                raise ValueError("请填写产品目录地址")
            public_url(payload.url)
            page = await asyncio.to_thread(fetch_page, payload.url)
            return {"products": discover_whmcs(page)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:
            raise HTTPException(400, "目录获取失败，站点可能限制访问") from None

    @app.get("/api/export")
    async def export():
        return {"version": 1, "monitors": [row["config"] for row in database.monitors()]}

    @app.post("/api/import")
    async def import_monitors(payload: ImportData):
        # Config transfer should not start network traffic/notifications before review.
        configs = [item.model_copy(update={"enabled": False}) for item in payload.monitors]
        return {"ids": insert_monitors(configs), "message": "导入的监控默认暂停，请核对后启用"}

    @app.get("/api/settings/mail")
    async def get_mail():
        config = database.setting("smtp", MailSettings().model_dump())
        config["has_password"] = bool(config.pop("password", ""))
        return config

    @app.put("/api/settings/mail")
    async def save_mail(config: MailSettings):
        mail_profiles.save(config, current=True)
        return {"ok": True}

    @app.get("/api/settings/mail/profiles")
    async def list_mail_profiles():
        return mail_profiles.list()

    @app.post("/api/settings/mail/profiles", status_code=201)
    async def add_mail_profile(config: MailProfile):
        return {"id": mail_profiles.save(config)}

    @app.put("/api/settings/mail/profiles/{ident}")
    async def update_mail_profile(ident: str, config: MailProfile):
        mail_profiles.save(config, ident)
        return {"ok": True}

    @app.post("/api/settings/mail/profiles/{ident}/activate")
    async def activate_mail_profile(ident: str):
        mail_profiles.activate(ident)
        return {"ok": True}

    @app.delete("/api/settings/mail/profiles/{ident}")
    async def delete_mail_profile(ident: str):
        mail_profiles.delete(ident)
        return {"ok": True}

    @app.post("/api/settings/mail/test", status_code=202)
    async def test_mail(request: Request):
        rate_limit(sensitive_calls, "mailtest:" + request.state.session["token"], 3, 300)
        if not database.setting("smtp", {}).get("enabled"):
            raise HTTPException(400, "请先保存并启用邮件配置")
        with database.connect() as db:
            ident = db.execute(
                "INSERT INTO notifications(subject,body,created) VALUES (?,?,?)",
                (
                    "[VPS Radar] 邮件通知测试",
                    "这是一封 VPS Radar 测试邮件。您的邮件通知已成功送达。",
                    time.time(),
                ),
            ).lastrowid
        return {"id": ident, "message": "测试邮件已排队，可在通知记录中查看投递结果"}

    @app.get("/api/notifications")
    async def notifications():
        with database.connect() as db:
            return [
                dict(row)
                for row in db.execute("""SELECT id,subject,status,attempts,error,created,sent
                                                       FROM notifications ORDER BY id DESC LIMIT 100""")
            ]

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app
