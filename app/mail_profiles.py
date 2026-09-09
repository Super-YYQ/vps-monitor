"""Named SMTP profiles; keep the effective SMTP setting compatible with the worker."""

import json
import uuid
from contextlib import contextmanager

from fastapi import HTTPException

from app.models import MailSettings


class MailProfiles:
    def __init__(self, database, vault):
        self.database, self.vault = database, vault

    @contextmanager
    def edit(self):
        # The profiles and the worker's effective configuration change atomically.
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM settings WHERE key='smtp_profiles'").fetchone()
            if row:
                store = json.loads(row[0])
            else:
                old = db.execute("SELECT value FROM settings WHERE key='smtp'").fetchone()
                store = {"active_id": "legacy" if old else None, "profiles": []}
                if old:
                    store["profiles"].append({"id": "legacy", "name": "默认邮箱", **json.loads(old[0])})
            previous = store["active_id"]
            yield store
            active = next((p for p in store["profiles"] if p["id"] == store["active_id"]), None)
            effective = {k: v for k, v in active.items() if k not in {"id", "name"}} if active else {}
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('smtp', ?)",
                (json.dumps(effective or MailSettings().model_dump()),),
            )
            db.execute("INSERT OR REPLACE INTO settings VALUES ('smtp_profiles', ?)", (json.dumps(store),))
            if previous != store["active_id"]:
                db.execute("""UPDATE notifications SET status='cancelled',error='邮件配置已切换，请重新测试'
                              WHERE event_id IS NULL AND status='pending'""")

    def list(self):
        with self.edit() as store:
            return {
                "active_id": store["active_id"],
                "profiles": [
                    {
                        **{k: v for k, v in p.items() if k != "password"},
                        "has_password": bool(p.get("password")),
                    }
                    for p in store["profiles"]
                ],
            }

    @staticmethod
    def find(store, ident):
        item = next((p for p in store["profiles"] if p["id"] == ident), None)
        if item is None:
            raise HTTPException(404, "邮件配置不存在")
        return item

    def save(self, config, ident=None, *, current=False):
        with self.edit() as store:
            if current:
                ident = store["active_id"]
            old = self.find(store, ident) if ident else {}
            if not old and len(store["profiles"]) >= 20:
                raise HTTPException(400, "最多保存 20 组邮件配置")
            value = config.model_dump()
            value["id"] = ident or str(uuid.uuid4())
            value.setdefault("name", old.get("name", "默认邮箱"))
            value["password"] = (
                self.vault.encrypt(config.password) if config.password else old.get("password", "")
            )
            if old:
                store["profiles"][store["profiles"].index(old)] = value
            else:
                store["profiles"].append(value)
            if store["active_id"] is None:
                store["active_id"] = value["id"]
            return value["id"]

    def activate(self, ident):
        with self.edit() as store:
            self.find(store, ident)
            store["active_id"] = ident

    def delete(self, ident):
        with self.edit() as store:
            store["profiles"].remove(self.find(store, ident))
            if store["active_id"] == ident:
                store["active_id"] = None
