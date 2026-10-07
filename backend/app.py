"""Storage Space API."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
from pydantic import BaseModel
from storage import MountedStorage, Storage
import authz
import admin
import metadata

DATA = Path(os.getenv("APP_DATA", "/data"))
DB = DATA / "app.sqlite3"
CONFIG = json.loads(os.getenv("STORAGES_JSON", '[{"id":"team","name":"Командное","path":"/data/team","demo":true},{"id":"archive","name":"Архив","path":"/data/archive","demo":true},{"id":"media","name":"Медиа","path":"/data/media","demo":true}]'))
STORAGES: dict[str, Storage] = {
    x["id"]: MountedStorage(x["id"], x["name"], x["path"], x.get("mode", "local" if x.get("demo") else "sshfs"))
    for x in CONFIG
}
app = FastAPI(title="Storage Space prototype")
admin.storage_ids = STORAGES
app.include_router(admin.router)


@app.exception_handler(OSError)
async def storage_io_error(request: Request, error: OSError):
    return JSONResponse({"detail": "Ошибка чтения хранилища. Проверьте подключение и повторите попытку."}, status_code=503)


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
    authz.initialize()
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
        if "directory" not in {row["name"] for row in db.execute("PRAGMA table_info(trash)")}:
            db.execute("ALTER TABLE trash ADD COLUMN directory INTEGER")
        if "user_id" not in {row["name"] for row in db.execute("PRAGMA table_info(uploads)")}:
            db.execute("ALTER TABLE uploads ADD COLUMN user_id INTEGER")
        audit_columns = {row["name"] for row in db.execute("PRAGMA table_info(audit)")}
        if "user_id" not in audit_columns:
            db.execute("ALTER TABLE audit ADD COLUMN user_id INTEGER")
        if "user_email" not in audit_columns:
            db.execute("ALTER TABLE audit ADD COLUMN user_email TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(id DESC)")
        metadata.initialize(db)
        db.execute("""CREATE TABLE IF NOT EXISTS user_favorites(user_id INTEGER NOT NULL,
                   storage TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(user_id,storage,path))""")
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


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in ("/api/auth/identify", "/api/auth/login"):
        try:
            request.state.user = await authz.current_user(request)
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    return await call_next(request)


def actor(request: Request):
    return request.state.user


def check(request: Request, storage: str, path: str, level=1, traverse=False):
    adapter(storage)
    authz.require(actor(request), storage, path, level, traverse)


class IdentifyBody(BaseModel):
    email: str


class LoginBody(IdentifyBody):
    code: str


@app.post("/api/auth/identify")
async def identify(body: IdentifyBody):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Укажите email")
    return await authz.ark_post("identify", {"email": email})


@app.post("/api/auth/login")
async def login(body: LoginBody, response: Response):
    result = await authz.ark_post("verify-code", {"email": body.email.strip().lower(), "code": body.code})
    claims = authz.verify_ark(result.get("access_token"))
    if not result.get("refresh_token"):
        raise HTTPException(503, "Ark не выдал сессию")
    token = authz.login(claims, body.email.strip().lower(), result["refresh_token"])
    response.set_cookie(authz.COOKIE, token, max_age=authz.SESSION_DAYS * 86400,
                        httponly=True, secure=True, samesite="lax", path="/")
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with database() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (actor(request)["session_hash"],))
    response.delete_cookie(authz.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    user = actor(request)
    return {"id": user["id"], "email": user["email"], "admin": user["admin"], "roles": user["roles"]}


def audit(request: Request, operation: str, storage: str, path: str, target: str | None = None):
    user = actor(request)
    with database() as db:
        db.execute("INSERT INTO audit(at,operation,storage,path,target,user_id,user_email) VALUES(?,?,?,?,?,?,?)",
                   (now(), operation, storage, path, target, user["id"], user["email"]))


@app.get("/healthz")
def healthz():
    with database() as db:
        db.execute("SELECT 1").fetchone()
    return {"ok": True}


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
def storages(request: Request, fresh: bool = False):
    return [storage.status(force=fresh) for storage in STORAGES.values()
            if authz.can_traverse(actor(request), storage.id, "")]


@app.get("/api/files")
def files(request: Request, storage: str, path: str = "", search: str = ""):
    check(request, storage, path, traverse=True)
    entries = adapter(storage).list_dir(path)
    with database() as db:
        metadata.sync_directory(db, storage, path, entries)
        favorites = {(r["storage"], r["path"]) for r in db.execute("SELECT storage,path FROM user_favorites WHERE user_id=?", (actor(request)["id"],))}
    entries = [entry for entry in entries if search.casefold() in entry["name"].casefold()
               and authz.can_traverse(actor(request), storage, entry["path"])]
    for entry in entries:
        entry["favorite"] = (storage, entry["path"]) in favorites
    return entries


def file_record(request: Request, file_id: str) -> dict:
    with database() as db:
        record = metadata.get(db, file_id)
    if not record or record["status"] != "active":
        raise HTTPException(404, "Файл не найден")
    check(request, record["storage"], record["path"])
    try:
        entry = adapter(record["storage"]).info(record["path"])
    except HTTPException as error:
        if error.status_code == 404:
            with database() as db:
                metadata.set_status(db, record["storage"], record["path"], "missing")
        raise
    if entry["directory"]:
        with database() as db:
            metadata.set_status(db, record["storage"], record["path"], "missing")
        raise HTTPException(404, "Файл не найден")
    with database() as db:
        metadata.observe(db, entry)
        return metadata.get(db, file_id)


@app.get("/api/files/{file_id}")
def file_metadata(file_id: str, request: Request):
    return file_record(request, file_id)


@app.get("/api/files/{file_id}/content")
def file_content(file_id: str, request: Request, download: bool = False):
    record = file_record(request, file_id)
    return adapter(record["storage"]).serve(record["path"], inline=not download)


@app.post("/api/files/{file_id}/sha256")
def calculate_sha256(file_id: str, request: Request):
    record = file_record(request, file_id)
    try:
        with database() as db:
            queued = metadata.queue_hash(db, record)
    except RuntimeError as error:
        raise HTTPException(429, str(error)) from None
    if queued:
        metadata.start_hash(record, adapter(record["storage"]))
    with database() as db:
        return metadata.get(db, file_id)


@app.get("/api/download")
def download(request: Request, storage: str, path: str):
    check(request, storage, path)
    return adapter(storage).serve(path, inline=False)


@app.get("/api/preview")
def preview(request: Request, storage: str, path: str):
    check(request, storage, path)
    return adapter(storage).serve(path, inline=True)


@app.post("/api/folders")
def mkdir(body: PathBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    result = adapter(body.storage).mkdir(body.path)
    audit(request, "mkdir", body.storage, body.path)
    return result


@app.post("/api/uploads")
def start_upload(body: UploadBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    if body.size < 0 or body.size > 1024**4:
        raise HTTPException(400, "Некорректный размер файла")
    upload_id = uuid.uuid4().hex
    selected = adapter(body.storage)
    selected.create_upload(upload_id, body.path)
    with database() as db:
        db.execute("INSERT INTO uploads(id,storage,path,size,created_at,user_id) VALUES(?,?,?,?,?,?)",
                   (upload_id, body.storage, body.path, body.size, now(), actor(request)["id"]))
    return {"id": upload_id, "offset": 0, "size": body.size}


def upload_row(upload_id: str, request: Request):
    with database() as db:
        row = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Загрузка не найдена")
    if row["user_id"] != actor(request)["id"] and not (row["user_id"] is None and actor(request)["admin"]):
        raise HTTPException(404, "Загрузка не найдена")
    check(request, row["storage"], row["path"].rsplit("/", 1)[0] if "/" in row["path"] else "", 2)
    return row


@app.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str, request: Request):
    row = upload_row(upload_id, request)
    return {"id": upload_id, "storage": row["storage"], "path": row["path"],
            "size": row["size"], "offset": adapter(row["storage"]).upload_offset(upload_id)}


@app.patch("/api/uploads/{upload_id}")
async def upload_part(upload_id: str, request: Request):
    row = upload_row(upload_id, request)
    try:
        offset = int(request.headers.get("X-Upload-Offset", ""))
    except ValueError:
        raise HTTPException(400, "Требуется X-Upload-Offset")
    if offset < 0:
        raise HTTPException(400, "Некорректное смещение")
    try:
        new_offset = await adapter(row["storage"]).append_upload(upload_id, offset, row["size"], request.stream())
    except ClientDisconnect:
        raise HTTPException(499, "Клиент прервал передачу") from None
    return {"offset": new_offset}


@app.post("/api/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, request: Request, body: CompleteUploadBody | None = None):
    row = upload_row(upload_id, request)
    path = row["path"]
    if body and body.filename is not None:
        if not body.filename or "/" in body.filename or "\\" in body.filename:
            raise HTTPException(400, "Некорректное имя файла")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        path = "/".join(x for x in (parent, body.filename) if x)
    result = adapter(row["storage"]).finish_upload(upload_id, path, row["size"])
    with database() as db:
        db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
        metadata.observe(db, result)
    audit(request, "upload", row["storage"], path)
    return result


@app.delete("/api/uploads/{upload_id}")
def cancel_upload(upload_id: str, request: Request):
    row = upload_row(upload_id, request)
    adapter(row["storage"]).abort_upload(upload_id)
    with database() as db:
        db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
    audit(request, "upload_cancel", row["storage"], row["path"])
    return {"ok": True}


@app.post("/api/move")
def move(body: MoveBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    check(request, body.storage, body.target.rsplit("/", 1)[0] if "/" in body.target else "", 2)
    source_info = adapter(body.storage).info(body.path)
    directory = source_info["directory"]
    if not directory:
        with database() as db:
            metadata.observe(db, source_info)
    result = adapter(body.storage).move(body.path, body.target)
    with database() as db:
        metadata.move_path(db, body.storage, body.path, body.target)
        db.execute("""UPDATE user_favorites
                      SET path=? || substr(path, length(?) + 1)
                      WHERE storage=? AND (path=? OR substr(path, 1, length(?) + 1)=? || '/')""",
                   (body.target, body.path, body.storage, body.path, body.path, body.path))
        if directory:
            db.execute("""UPDATE grants SET path=? || substr(path, length(?) + 1)
                          WHERE storage=? AND (path=? OR substr(path, 1, length(?) + 1)=? || '/')""",
                       (body.target, body.path, body.storage, body.path, body.path, body.path))
    audit(request, "move", body.storage, body.path, body.target)
    return result


@app.post("/api/favorite")
def favorite(body: PathBody, request: Request):
    check(request, body.storage, body.path)
    adapter(body.storage).info(body.path)
    with database() as db:
        exists = db.execute("SELECT 1 FROM user_favorites WHERE user_id=? AND storage=? AND path=?", (actor(request)["id"], body.storage, body.path)).fetchone()
        if exists:
            db.execute("DELETE FROM user_favorites WHERE user_id=? AND storage=? AND path=?", (actor(request)["id"], body.storage, body.path))
        else:
            db.execute("INSERT INTO user_favorites VALUES(?,?,?)", (actor(request)["id"], body.storage, body.path))
    return {"favorite": not bool(exists)}


@app.get("/api/favorites")
def favorites(request: Request):
    with database() as db:
        rows = list(db.execute("SELECT storage,path FROM user_favorites WHERE user_id=? ORDER BY path", (actor(request)["id"],)))
    result = []
    for row in rows:
        if not authz.permission(actor(request), row["storage"], row["path"]):
            continue
        try:
            entry = adapter(row["storage"]).info(row["path"])
            with database() as db:
                entry["id"] = metadata.observe(db, entry)
            result.append({**entry, "favorite": True})
        except HTTPException:
            pass
    return result


@app.post("/api/trash")
def trash(body: PathBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    trash_id = uuid.uuid4().hex
    selected = adapter(body.storage)
    directory = selected.is_dir(body.path)
    if not directory:
        with database() as db:
            metadata.observe(db, selected.info(body.path))
    if directory:
        with database() as db:
            grant = db.execute("""SELECT 1 FROM grants WHERE storage=? AND
                (path=? OR substr(path, 1, length(?) + 1)=? || '/') LIMIT 1""",
                (body.storage, body.path, body.path, body.path)).fetchone()
        if grant:
            raise HTTPException(409, "Сначала уберите права доступа к этой папке и вложенным папкам")
    selected.trash(body.path, trash_id)
    with database() as db:
        metadata.set_status(db, body.storage, body.path, "trashed")
        db.execute("INSERT INTO trash(id,storage,original_path,trashed_path,deleted_at,directory) VALUES(?,?,?,?,?,?)",
                   (trash_id, body.storage, body.path, trash_id, now(), int(directory)))
        db.execute("DELETE FROM user_favorites WHERE storage=? AND (path=? OR path LIKE ?)", (body.storage, body.path, body.path + "/%"))
    audit(request, "trash", body.storage, body.path)
    return {"id": trash_id}


@app.get("/api/trash")
def trash_list(request: Request):
    with database() as db:
        rows = [dict(row) for row in db.execute("SELECT id,storage,original_path,deleted_at,directory FROM trash ORDER BY deleted_at DESC")]
    rows = [row for row in rows if authz.permission(actor(request), row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "")]
    for row in rows:
        if row["directory"] is None:
            try:
                row["directory"] = int(adapter(row["storage"]).trash_is_dir(row["id"]))
            except HTTPException:
                row["directory"] = 0
        row["directory"] = bool(row["directory"])
    return rows


@app.get("/api/trash/{trash_id}/files")
def trash_files(trash_id: str, request: Request, path: str = ""):
    row = trash_row(trash_id)
    check(request, row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "")
    entries = adapter(row["storage"]).list_trashed(trash_id, path)
    return [{**entry, "path": row["original_path"] + "/" + entry["path"],
             "trash_path": entry["path"], "trash_id": trash_id} for entry in entries]


def trash_row(trash_id: str):
    with database() as db:
        row = db.execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Не найдено в корзине")
    return row


@app.post("/api/trash/{trash_id}/restore")
def restore(trash_id: str, request: Request):
    row = trash_row(trash_id)
    check(request, row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "", 2)
    adapter(row["storage"]).restore(trash_id, row["original_path"])
    with database() as db:
        metadata.set_status(db, row["storage"], row["original_path"], "active")
        db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    audit(request, "restore", row["storage"], row["original_path"])
    return {"ok": True}


@app.delete("/api/trash/{trash_id}")
def delete_forever(trash_id: str, request: Request):
    row = trash_row(trash_id)
    check(request, row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "", 2)
    adapter(row["storage"]).purge(trash_id)
    with database() as db:
        metadata.set_status(db, row["storage"], row["original_path"], "missing")
        db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    audit(request, "delete_forever", row["storage"], row["original_path"])
    return {"ok": True}
