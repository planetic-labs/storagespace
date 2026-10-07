"""Stable file IDs and optional, serialized SHA-256 calculation."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.getenv("APP_DATA", "/data")) / "app.sqlite3"
HASH_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-sha256")


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS file_metadata(
            id TEXT PRIMARY KEY, storage TEXT NOT NULL, path TEXT NOT NULL,
            parent TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','trashed','missing')),
            size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            sha256 TEXT, hash_status TEXT NOT NULL DEFAULT 'not_computed',
            last_seen TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_file_metadata_active_path
            ON file_metadata(storage,path) WHERE status='active';
        CREATE INDEX IF NOT EXISTS idx_file_metadata_parent
            ON file_metadata(storage,parent,status);
    """)
    db.execute("UPDATE file_metadata SET hash_status='not_computed' WHERE hash_status IN ('queued','running')")


def observe(db: sqlite3.Connection, entry: dict) -> str | None:
    if entry["directory"]:
        return None
    storage, path = entry["storage"], entry["path"]
    size, mtime_ns = entry["size"], entry["mtime_ns"]
    seen = datetime.now(timezone.utc).isoformat()
    row = db.execute("SELECT id,size,mtime_ns FROM file_metadata WHERE storage=? AND path=? AND status='active'",
                     (storage, path)).fetchone()
    if row:
        changed = row["size"] != size or row["mtime_ns"] != mtime_ns
        db.execute("""UPDATE file_metadata SET size=?,mtime_ns=?,last_seen=?,
                      sha256=CASE WHEN ? THEN NULL ELSE sha256 END,
                      hash_status=CASE WHEN ? THEN 'not_computed' ELSE hash_status END WHERE id=?""",
                   (size, mtime_ns, seen, changed, changed, row["id"]))
        return row["id"]
    file_id = str(uuid.uuid7())
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    db.execute("""INSERT INTO file_metadata(id,storage,path,parent,status,size,mtime_ns,last_seen)
                  VALUES(?,?,?,?, 'active',?,?,?)""",
               (file_id, storage, path, parent, size, mtime_ns, seen))
    return file_id


def sync_directory(db: sqlite3.Connection, storage: str, parent: str, entries: list[dict]) -> None:
    seen = set()
    for entry in entries:
        file_id = observe(db, entry)
        if file_id:
            entry["id"] = file_id
            seen.add(entry["path"])
    for row in list(db.execute("SELECT id,path FROM file_metadata WHERE storage=? AND parent=? AND status='active'",
                               (storage, parent))):
        if row["path"] not in seen:
            db.execute("UPDATE file_metadata SET status='missing',sha256=NULL,hash_status='not_computed' WHERE id=?",
                       (row["id"],))


def move_path(db: sqlite3.Connection, storage: str, source: str, target: str) -> None:
    rows = list(db.execute("""SELECT id,path FROM file_metadata WHERE storage=? AND status='active'
                              AND (path=? OR substr(path,1,length(?)+1)=? || '/')""",
                           (storage, source, source, source)))
    for row in rows:
        path = target + row["path"][len(source):]
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        db.execute("""UPDATE file_metadata SET path=?,parent=?,
                      hash_status=CASE WHEN hash_status IN ('queued','running') THEN 'not_computed' ELSE hash_status END
                      WHERE id=?""", (path, parent, row["id"]))


def set_status(db: sqlite3.Connection, storage: str, path: str, status: str) -> None:
    if status not in ("active", "trashed", "missing"):
        raise ValueError("Invalid file metadata status")
    db.execute("""UPDATE file_metadata SET status=?,
                  sha256=CASE WHEN ?='missing' THEN NULL ELSE sha256 END,
                  hash_status=CASE WHEN hash_status IN ('queued','running') OR ?='missing'
                    THEN 'not_computed' ELSE hash_status END
                  WHERE storage=? AND (path=? OR substr(path,1,length(?)+1)=? || '/')
                    AND status IN ('active','trashed')""",
               (status, status, status, storage, path, path, path))


def get(db: sqlite3.Connection, file_id: str) -> dict | None:
    row = db.execute("SELECT * FROM file_metadata WHERE id=?", (file_id,)).fetchone()
    return dict(row) if row else None


def queue_hash(db: sqlite3.Connection, record: dict) -> bool:
    if record["hash_status"] in ("queued", "running", "ready"):
        return False
    pending = db.execute("SELECT COUNT(*) FROM file_metadata WHERE hash_status IN ('queued','running')").fetchone()[0]
    if pending >= 10:
        raise RuntimeError("Очередь расчёта хешей заполнена")
    db.execute("UPDATE file_metadata SET hash_status='queued' WHERE id=?", (record["id"],))
    return True


def start_hash(record: dict, storage) -> None:
    HASH_WORKER.submit(_calculate_hash, record["id"], record["path"], storage)


def _calculate_hash(file_id: str, path: str, storage) -> None:
    try:
        with sqlite3.connect(DB, timeout=30) as db:
            db.execute("UPDATE file_metadata SET hash_status='running' WHERE id=? AND path=? AND hash_status='queued'",
                       (file_id, path))
        before = storage.info(path)
        digest = hashlib.sha256()
        with storage.open_for_hash(path) as source:
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        after = storage.info(path)
        valid = before["size"] == after["size"] and before["mtime_ns"] == after["mtime_ns"]
        with sqlite3.connect(DB, timeout=30) as db:
            db.execute("""UPDATE file_metadata SET sha256=?,hash_status=? WHERE id=? AND path=?
                          AND status='active' AND size=? AND mtime_ns=? AND hash_status='running'""",
                       (digest.hexdigest() if valid else None, "ready" if valid else "not_computed",
                        file_id, path, before["size"], before["mtime_ns"]))
    except Exception:
        with sqlite3.connect(DB, timeout=30) as db:
            db.execute("""UPDATE file_metadata SET hash_status='error' WHERE id=? AND path=?
                          AND hash_status IN ('queued','running')""", (file_id, path))
