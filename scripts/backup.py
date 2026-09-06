"""Consistent SQLite backup. Include secret.key for a restorable SMTP configuration."""

import argparse
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--data", default=os.getenv("DATA_DIR", "data"))
parser.add_argument("--output", required=True, help="A new directory; existing backups are never overwritten")
args = parser.parse_args()
source = Path(args.data)
target = Path(args.output)
if not (source / "radar.db").is_file() or not (source / "secret.key").is_file():
    raise SystemExit("Missing radar.db or secret.key in source directory")
target.mkdir(parents=True, exist_ok=False)
os.chmod(target, 0o700)
with sqlite3.connect(source / "radar.db") as src, sqlite3.connect(target / "radar.db") as dest:
    src.backup(dest)
shutil.copyfile(source / "secret.key", target / "secret.key")
os.chmod(target / "secret.key", 0o600)
(target / "backup-info.txt").write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
print(f"Backup saved to {target.resolve()}")
