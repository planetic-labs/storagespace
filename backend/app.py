"""Single-user prototype API. Keep behind the Caddy access gate."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from storage import MountedStorage, Storage

DATA = Path(os.getenv("APP_DATA", "/data"))
DB = DATA / "app.sqlite3"
CONFIG = json.loads(os.getenv("STORAGES_JSON", '[{"id":"team","name":"Командное","path":"/data/team","demo":true},{"id":"archive","name":"Архив","path":"/data/archive","demo":true},{"id":"media","name":"Медиа","path":"/data/media","demo":true}]'))
STORAGES: dict[str, Storage] = {
    x["id"]: MountedStorage(x["id"], x["name"], x["path"], x.get("mode", "local" if x.get("demo") else "sshfs"))
    for x in CONFIG
}
app = FastAPI(title="Storage Space prototype")


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


@app.on_event("startup")
def startup():
    with database() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS favorites(storage TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(storage,path));
        CREATE TABLE IF NOT EXISTS trash(id TEXT PRIMARY KEY, storage TEXT NOT NULL, original_path TEXT NOT NULL,
          trashed_path TEXT NOT NULL, deleted_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL, operation TEXT NOT NULL,
          storage TEXT NOT NULL, path TEXT NOT NULL, target TEXT);
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, storage TEXT NOT NULL, path TEXT NOT NULL,
          size INTEGER NOT NULL, created_at TEXT NOT NULL);
        """)
    samples = {
        "team": ("Документы", "Проекты"),
        "archive": ("2025", "Договоры"),
        "media": ("Видеозаписи", "Аудио"),
    }
    for config in CONFIG:
        if not config.get("demo"):
            continue
        root = Path(config["path"])
        root.mkdir(parents=True, exist_ok=True)
        if not any(root.iterdir()):
            for folder in samples.get(config["id"], ()): (root / folder).mkdir()
            (root / "Прочитайте меня.txt").write_text("Демонстрационное хранилище Storage Space.\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def adapter(storage: str) -> Storage:
    selected = STORAGES.get(storage)
    if not selected:
        raise HTTPException(404, "Хранилище не найдено")
    return selected


def audit(operation: str, storage: str, path: str, target: str | None = None):
    with database() as db:
        db.execute("INSERT INTO audit(at,operation,storage,path,target) VALUES(?,?,?,?,?)", (now(), operation, storage, path, target))


class PathBody(BaseModel):
    storage: str
    path: str


class MoveBody(PathBody):
    target: str


class UploadBody(PathBody):
    size: int


class CompleteUploadBody(BaseModel):
    filename: str | None = None


@app.get("/api/storages")
def storages():
    return [storage.status() for storage in STORAGES.values()]


@app.get("/api/files")
def files(storage: str, path: str = "", search: str = ""):
    entries = adapter(storage).list_dir(path, search)
    with database() as db:
        favorites = {(r["storage"], r["path"]) for r in db.execute("SELECT storage,path FROM favorites")}
    for entry in entries:
        entry["favorite"] = (storage, entry["path"]) in favorites
    return entries


@app.get("/api/download")
def download(storage: str, path: str):
    return adapter(storage).serve(path, inline=False)


@app.get("/api/preview")
def preview(storage: str, path: str):
    return adapter(storage).serve(path, inline=True)


@app.post("/api/folders")
def mkdir(body: PathBody):
    result = adapter(body.storage).mkdir(body.path)
    audit("mkdir", body.storage, body.path)
    return result


@app.post("/api/uploads")
def start_upload(body: UploadBody):
    if body.size < 0 or body.size > 1024**4:
        raise HTTPException(400, "Некорректный размер файла")
    upload_id = uuid.uuid4().hex
    selected = adapter(body.storage)
    selected.create_upload(upload_id, body.path)
    with database() as db:
        db.execute("INSERT INTO uploads VALUES(?,?,?,?,?)",
                   (upload_id, body.storage, body.path, body.size, now()))
    return {"id": upload_id, "offset": 0, "size": body.size}


def upload_row(upload_id: str):
    with database() as db:
        row = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Загрузка не найдена")
    return row


@app.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str):
    row = upload_row(upload_id)
    return {"id": upload_id, "storage": row["storage"], "path": row["path"],
            "size": row["size"], "offset": adapter(row["storage"]).upload_offset(upload_id)}


@app.patch("/api/uploads/{upload_id}")
async def upload_part(upload_id: str, request: Request):
    row = upload_row(upload_id)
    try:
        offset = int(request.headers.get("X-Upload-Offset", ""))
    except ValueError:
        raise HTTPException(400, "Требуется X-Upload-Offset")
    if offset < 0:
        raise HTTPException(400, "Некорректное смещение")
    new_offset = await adapter(row["storage"]).append_upload(upload_id, offset, row["size"], request.stream())
    return {"offset": new_offset}


@app.post("/api/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, body: CompleteUploadBody | None = None):
    row = upload_row(upload_id)
    path = row["path"]
    if body and body.filename is not None:
        if not body.filename or "/" in body.filename or "\\" in body.filename:
            raise HTTPException(400, "Некорректное имя файла")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        path = "/".join(x for x in (parent, body.filename) if x)
    result = adapter(row["storage"]).finish_upload(upload_id, path, row["size"])
    with database() as db:
        db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
    audit("upload", row["storage"], path)
    return result


@app.delete("/api/uploads/{upload_id}")
def cancel_upload(upload_id: str):
    row = upload_row(upload_id)
    adapter(row["storage"]).abort_upload(upload_id)
    with database() as db:
        db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
    return {"ok": True}


@app.post("/api/move")
def move(body: MoveBody):
    result = adapter(body.storage).move(body.path, body.target)
    with database() as db:
        db.execute("UPDATE favorites SET path=? WHERE storage=? AND path=?", (body.target, body.storage, body.path))
    audit("move", body.storage, body.path, body.target)
    return result


@app.post("/api/favorite")
def favorite(body: PathBody):
    adapter(body.storage).info(body.path)
    with database() as db:
        exists = db.execute("SELECT 1 FROM favorites WHERE storage=? AND path=?", (body.storage, body.path)).fetchone()
        if exists:
            db.execute("DELETE FROM favorites WHERE storage=? AND path=?", (body.storage, body.path))
        else:
            db.execute("INSERT INTO favorites VALUES(?,?)", (body.storage, body.path))
    return {"favorite": not bool(exists)}


@app.get("/api/favorites")
def favorites():
    with database() as db:
        rows = list(db.execute("SELECT storage,path FROM favorites ORDER BY path"))
    result = []
    for row in rows:
        try:
            result.append({**adapter(row["storage"]).info(row["path"]), "favorite": True})
        except HTTPException:
            pass
    return result


@app.post("/api/trash")
def trash(body: PathBody):
    trash_id = uuid.uuid4().hex
    adapter(body.storage).trash(body.path, trash_id)
    with database() as db:
        db.execute("INSERT INTO trash VALUES(?,?,?,?,?)", (trash_id, body.storage, body.path, trash_id, now()))
        db.execute("DELETE FROM favorites WHERE storage=? AND (path=? OR path LIKE ?)", (body.storage, body.path, body.path + "/%"))
    audit("trash", body.storage, body.path)
    return {"id": trash_id}


@app.get("/api/trash")
def trash_list():
    with database() as db:
        return [dict(row) for row in db.execute("SELECT id,storage,original_path,deleted_at FROM trash ORDER BY deleted_at DESC")]


def trash_row(trash_id: str):
    with database() as db:
        row = db.execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Не найдено в корзине")
    return row


@app.post("/api/trash/{trash_id}/restore")
def restore(trash_id: str):
    row = trash_row(trash_id)
    adapter(row["storage"]).restore(trash_id, row["original_path"])
    with database() as db:
        db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    audit("restore", row["storage"], row["original_path"])
    return {"ok": True}


@app.delete("/api/trash/{trash_id}")
def delete_forever(trash_id: str):
    row = trash_row(trash_id)
    adapter(row["storage"]).purge(trash_id)
    with database() as db:
        db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    audit("delete_forever", row["storage"], row["original_path"])
    return {"ok": True}
