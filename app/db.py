import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "radar.db"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS monitors (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'unknown', stable TEXT NOT NULL DEFAULT 'unknown',
                    candidate TEXT NOT NULL DEFAULT 'unknown', streak INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, last_check REAL, next_check REAL NOT NULL DEFAULT 0,
                    last_change REAL, reason TEXT NOT NULL DEFAULT '尚未检测', latency INTEGER,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY, monitor_id TEXT NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
                    status TEXT NOT NULL, reason TEXT NOT NULL, latency INTEGER, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS checks_monitor_time ON checks(monitor_id, created DESC);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, monitor_id TEXT REFERENCES monitors(id) ON DELETE SET NULL,
                    name TEXT NOT NULL, previous TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY, event_id INTEGER UNIQUE REFERENCES events(id) ON DELETE CASCADE,
                    subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, sent REAL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))

    def monitors(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM monitors ORDER BY created")]

    @staticmethod
    def decode(row):
        value = dict(row)
        value["config"] = json.loads(value["config"])
        return value
