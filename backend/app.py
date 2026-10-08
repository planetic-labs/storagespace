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
from storage import MountedStorage, PersonalStorage, Storage
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
PERSONAL = {x["id"]: x["personal_parent"] for x in CONFIG if x.get("personal_parent") is not None}
app = FastAPI(title="Storage Space prototype")
admin.storage_ids = {key: value for key, value in STORAGES.items() if key not in PERSONAL}
authz.personal_storage_ids = set(PERSONAL)
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
        if "user_id" not in {row["name"] for row in db.execute("PRAGMA table_info(trash)")}:
            db.execute("ALTER TABLE trash ADD COLUMN user_id INTEGER")
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


def adapter(storage: str, request: Request) -> Storage:
    selected = STORAGES.get(storage)
    if not selected:
        raise HTTPException(404, "Хранилище не найдено")
    if storage in PERSONAL:
        cached = getattr(request.state, "personal_adapters", None)
        if cached is None:
            cached = request.state.personal_adapters = {}
        if storage not in cached:
            cached[storage] = PersonalStorage(selected, storage, selected.name, PERSONAL[storage], actor(request)["id"])
        return cached[storage]
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


def data_path(storage: str, path: str, request: Request) -> str:
    if storage in PERSONAL:
        return f"user-{actor(request)['id']}" + ("/" + path if path else "")
    return path


def virtual_path(storage: str, path: str, request: Request) -> str:
    if storage not in PERSONAL:
        return path
    prefix = f"user-{actor(request)['id']}/"
    if not path.startswith(prefix):
        raise HTTPException(404, "Файл не найден")
    return path[len(prefix):]


def data_entry(entry: dict, request: Request) -> dict:
    return {**entry, "path": data_path(entry["storage"], entry["path"], request)}


def user_folder_name(user) -> str:
    return " ".join(filter(None, (user["last_name"], user["first_name"])))


RUSSIAN_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
RUSSIAN_ORDER = {letter: index for index, letter in enumerate(RUSSIAN_ALPHABET)}


def user_folder_sort_key(name: str):
    return tuple((1, RUSSIAN_ORDER[letter]) if letter in RUSSIAN_ORDER else (0, ord(letter))
                 for letter in name.casefold())


def public_record(record: dict, request: Request) -> dict:
    storage = record["storage"]
    if storage not in PERSONAL:
        return record
    root = data_path(storage, "", request)
    parent = record["parent"]
    return {**record, "path": virtual_path(storage, record["path"], request),
            "parent": "" if parent == root else virtual_path(storage, parent, request)}


def check(request: Request, storage: str, path: str, level=1, traverse=False):
    adapter(storage, request)
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
    name = await authz.profile_name(result["access_token"]) if authz.needs_profile(claims["sub"]) else None
    token = authz.login(claims, body.email.strip().lower(), result["refresh_token"], name)
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
    return {"id": user["id"], "email": user["email"], "first_name": user["first_name"],
            "last_name": user["last_name"], "admin": user["admin"], "roles": user["roles"]}


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


class EnsureFoldersBody(BaseModel):
    storage: str
    paths: list[str]


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
    entries = adapter(storage, request).list_dir(path, search)
    with database() as db:
        known = {row["path"]: row["id"] for row in db.execute(
            "SELECT path,id FROM file_metadata WHERE storage=? AND parent=? AND status='active'",
            (storage, data_path(storage, path, request)))}
        favorites = {(r["storage"], r["path"]) for r in db.execute("SELECT storage,path FROM user_favorites WHERE user_id=?", (actor(request)["id"],))}
        setting = db.execute("SELECT path,label FROM personal_folder_settings WHERE storage=?", (storage,)).fetchone()
        names = {}
        if setting and path == setting["path"]:
            names = {f"user-{row['id']}": user_folder_name(row)
                     for row in db.execute("SELECT id,first_name,last_name FROM users")}
    entries = [entry for entry in entries if authz.can_traverse(actor(request), storage, entry["path"])]
    for entry in entries:
        if entry["directory"] and setting:
            if entry["path"] == setting["path"]:
                entry["display_name"] = setting["label"]
            elif path == setting["path"] and names.get(entry["name"]):
                entry["display_name"] = names[entry["name"]]
        if not entry["directory"]:
            entry["id"] = known.get(data_path(storage, entry["path"], request))
        entry["favorite"] = (storage, entry["path"]) in favorites
    if setting and path == setting["path"]:
        entries.sort(key=lambda entry: (not entry["directory"],
                    user_folder_sort_key(entry.get("display_name") or entry["name"]),
                    entry["name"]))
    return entries


@app.get("/api/personal-folders")
def personal_folder_labels(request: Request, storage: str, path: str = ""):
    check(request, storage, path, traverse=True)
    with database() as db:
        setting = db.execute("SELECT path,label FROM personal_folder_settings WHERE storage=?", (storage,)).fetchone()
        if not setting:
            return {}
        labels = {setting["path"]: setting["label"]}
        prefix = setting["path"] + "/user-"
        if path.startswith(prefix):
            user_id = path[len(prefix):].split("/", 1)[0]
            if user_id.isdecimal():
                row = db.execute("SELECT first_name,last_name FROM users WHERE id=?", (int(user_id),)).fetchone()
                if row:
                    name = user_folder_name(row)
                    if name:
                        labels[prefix + user_id] = name
        return labels


@app.post("/api/files/resolve")
def resolve_file(body: PathBody, request: Request):
    check(request, body.storage, body.path)
    entry = adapter(body.storage, request).info(body.path)
    if entry["directory"]:
        raise HTTPException(400, "Выберите файл")
    with database() as db:
        file_id = metadata.observe(db, data_entry(entry, request))
        record = metadata.get(db, file_id)
        return public_record(record, request)


def file_record(request: Request, file_id: str) -> dict:
    with database() as db:
        record = metadata.get(db, file_id)
    if not record or record["status"] != "active":
        raise HTTPException(404, "Файл не найден")
    path = virtual_path(record["storage"], record["path"], request)
    check(request, record["storage"], path)
    try:
        entry = adapter(record["storage"], request).info(path)
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
        metadata.observe(db, data_entry(entry, request))
        return metadata.get(db, file_id)


@app.get("/api/files/{file_id}")
def file_metadata(file_id: str, request: Request):
    record = file_record(request, file_id)
    return public_record(record, request)


@app.get("/api/files/{file_id}/content")
def file_content(file_id: str, request: Request, download: bool = False):
    record = file_record(request, file_id)
    return adapter(record["storage"], request).serve(virtual_path(record["storage"], record["path"], request), inline=not download)


@app.post("/api/files/{file_id}/sha256")
def calculate_sha256(file_id: str, request: Request):
    record = file_record(request, file_id)
    try:
        with database() as db:
            queued = metadata.queue_hash(db, record)
    except RuntimeError as error:
        raise HTTPException(429, str(error)) from None
    if queued:
        metadata.start_hash(record, adapter(record["storage"], request),
                            virtual_path(record["storage"], record["path"], request))
    with database() as db:
        updated = metadata.get(db, file_id)
        return public_record(updated, request)


@app.get("/api/download")
def download(request: Request, storage: str, path: str):
    check(request, storage, path)
    entry = adapter(storage, request).info(path)
    if not entry["directory"]:
        with database() as db:
            metadata.observe(db, data_entry(entry, request))
    return adapter(storage, request).serve(path, inline=False)


@app.get("/api/preview")
def preview(request: Request, storage: str, path: str):
    check(request, storage, path)
    entry = adapter(storage, request).info(path)
    if not entry["directory"]:
        with database() as db:
            metadata.observe(db, data_entry(entry, request))
    return adapter(storage, request).serve(path, inline=True)


@app.post("/api/folders")
def mkdir(body: PathBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    result = adapter(body.storage, request).mkdir(body.path)
    audit(request, "mkdir", body.storage, body.path)
    return result


@app.post("/api/folders/ensure")
def ensure_folders(body: EnsureFoldersBody, request: Request):
    if len(body.paths) > 1000:
        raise HTTPException(400, "Слишком много папок в одной части запроса")
    selected = adapter(body.storage, request)
    created = 0
    for path in sorted(set(body.paths), key=lambda value: (value.count("/"), value)):
        if not path:
            raise HTTPException(400, "Некорректный путь")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        check(request, body.storage, parent, 2)
        try:
            selected.mkdir(path)
        except HTTPException as error:
            if error.status_code != 409 or not selected.is_dir(path):
                raise
        else:
            audit(request, "mkdir", body.storage, path)
            created += 1
    return {"created": created}


@app.post("/api/uploads")
def start_upload(body: UploadBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    if body.size < 0 or body.size > 1024**4:
        raise HTTPException(400, "Некорректный размер файла")
    upload_id = uuid.uuid4().hex
    selected = adapter(body.storage, request)
    if selected.exists(body.path):
        raise HTTPException(409, "Такое имя уже существует")
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
            "size": row["size"], "offset": adapter(row["storage"], request).upload_offset(upload_id)}


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
        new_offset = await adapter(row["storage"], request).append_upload(upload_id, offset, row["size"], request.stream())
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
    result = adapter(row["storage"], request).finish_upload(upload_id, path, row["size"])
    with database() as db:
        db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
        metadata.observe(db, data_entry(result, request))
    audit(request, "upload", row["storage"], path)
    return result


@app.delete("/api/uploads/{upload_id}")
def cancel_upload(upload_id: str, request: Request):
    row = upload_row(upload_id, request)
    adapter(row["storage"], request).abort_upload(upload_id)
    with database() as db:
        db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
    audit(request, "upload_cancel", row["storage"], row["path"])
    return {"ok": True}


@app.post("/api/move")
def move(body: MoveBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    check(request, body.storage, body.target.rsplit("/", 1)[0] if "/" in body.target else "", 2)
    source_info = adapter(body.storage, request).info(body.path)
    directory = source_info["directory"]
    if not directory:
        with database() as db:
            metadata.observe(db, data_entry(source_info, request))
    result = adapter(body.storage, request).move(body.path, body.target)
    with database() as db:
        metadata.move_path(db, body.storage, data_path(body.storage, body.path, request),
                           data_path(body.storage, body.target, request))
        db.execute("""UPDATE user_favorites
                      SET path=? || substr(path, length(?) + 1)
                      WHERE storage=? AND (path=? OR substr(path, 1, length(?) + 1)=? || '/')
                      AND (?=0 OR user_id=?)""",
                   (body.target, body.path, body.storage, body.path, body.path, body.path,
                    int(body.storage in PERSONAL), actor(request)["id"]))
        if directory:
            db.execute("""UPDATE grants SET path=? || substr(path, length(?) + 1)
                          WHERE storage=? AND (path=? OR substr(path, 1, length(?) + 1)=? || '/')""",
                       (body.target, body.path, body.storage, body.path, body.path, body.path))
            db.execute("""UPDATE personal_folder_settings
                          SET path=? || substr(path, length(?) + 1)
                          WHERE storage=? AND (path=? OR substr(path, 1, length(?) + 1)=? || '/')""",
                       (body.target, body.path, body.storage, body.path, body.path, body.path))
    audit(request, "move", body.storage, body.path, body.target)
    return result


@app.post("/api/copy")
def copy(body: MoveBody, request: Request):
    check(request, body.storage, body.path)
    check(request, body.storage, body.target.rsplit("/", 1)[0] if "/" in body.target else "", 2)
    result = adapter(body.storage, request).copy(body.path, body.target)
    if not result["directory"]:
        with database() as db:
            metadata.observe(db, data_entry(result, request))
    audit(request, "copy", body.storage, body.path, body.target)
    return result


@app.post("/api/favorite")
def favorite(body: PathBody, request: Request):
    check(request, body.storage, body.path)
    entry = adapter(body.storage, request).info(body.path)
    with database() as db:
        metadata.observe(db, data_entry(entry, request))
        exists = db.execute("SELECT 1 FROM user_favorites WHERE user_id=? AND storage=? AND path=?", (actor(request)["id"], body.storage, body.path)).fetchone()
        if exists:
            db.execute("DELETE FROM user_favorites WHERE user_id=? AND storage=? AND path=?", (actor(request)["id"], body.storage, body.path))
        else:
            db.execute("INSERT INTO user_favorites VALUES(?,?,?)", (actor(request)["id"], body.storage, body.path))
    return {"favorite": not bool(exists)}


@app.get("/api/favorites")
def favorites(request: Request):
    with database() as db:
        personal_ids = tuple(PERSONAL)
        personal_clause = "f.storage IN (" + ",".join("?" for _ in personal_ids) + ")" if personal_ids else "0"
        rows = list(db.execute(f"""SELECT f.storage,f.path,m.id FROM user_favorites f
                  LEFT JOIN file_metadata m ON m.storage=f.storage AND m.path=
                    CASE WHEN {personal_clause} THEN 'user-' || f.user_id || '/' || f.path ELSE f.path END
                    AND m.status='active'
                  WHERE f.user_id=? ORDER BY f.path""", (*personal_ids, actor(request)["id"])))
        settings = {r["storage"]: r for r in db.execute("SELECT storage,path,label FROM personal_folder_settings")}
        users = {r["id"]: user_folder_name(r)
                 for r in db.execute("SELECT id,first_name,last_name FROM users")}
    result = []
    for row in rows:
        if not authz.permission(actor(request), row["storage"], row["path"]):
            continue
        try:
            entry = adapter(row["storage"], request).info(row["path"])
            entry["id"] = row["id"]
            setting = settings.get(row["storage"])
            if entry["directory"] and setting:
                if entry["path"] == setting["path"]:
                    entry["display_name"] = setting["label"]
                elif entry["path"].startswith(setting["path"] + "/user-"):
                    suffix = entry["path"][len(setting["path"]) + 6:]
                    if suffix.isdecimal() and users.get(int(suffix)):
                        entry["display_name"] = users[int(suffix)]
            result.append({**entry, "favorite": True})
        except HTTPException:
            pass
    return result


@app.post("/api/trash")
def trash(body: PathBody, request: Request):
    check(request, body.storage, body.path.rsplit("/", 1)[0] if "/" in body.path else "", 2)
    trash_id = uuid.uuid4().hex
    selected = adapter(body.storage, request)
    directory = selected.is_dir(body.path)
    if not directory:
        with database() as db:
            metadata.observe(db, data_entry(selected.info(body.path), request))
    if directory:
        with database() as db:
            grant = db.execute("""SELECT 1 FROM grants WHERE storage=? AND
                (path=? OR substr(path, 1, length(?) + 1)=? || '/') LIMIT 1""",
                (body.storage, body.path, body.path, body.path)).fetchone()
        if grant:
            raise HTTPException(409, "Сначала уберите права доступа к этой папке и вложенным папкам")
    selected.trash(body.path, trash_id)
    with database() as db:
        metadata.set_status(db, body.storage, data_path(body.storage, body.path, request), "trashed")
        db.execute("INSERT INTO trash(id,storage,original_path,trashed_path,deleted_at,directory,user_id) VALUES(?,?,?,?,?,?,?)",
                   (trash_id, body.storage, body.path, trash_id, now(), int(directory), actor(request)["id"]))
        db.execute("DELETE FROM user_favorites WHERE storage=? AND (path=? OR path LIKE ?) AND (?=0 OR user_id=?)",
                   (body.storage, body.path, body.path + "/%", int(body.storage in PERSONAL), actor(request)["id"]))
    audit(request, "trash", body.storage, body.path)
    return {"id": trash_id}


@app.get("/api/trash")
def trash_list(request: Request):
    with database() as db:
        rows = [dict(row) for row in db.execute("SELECT id,storage,original_path,deleted_at,directory,user_id FROM trash ORDER BY deleted_at DESC")]
    rows = [row for row in rows if (row["storage"] not in PERSONAL or row["user_id"] == actor(request)["id"])
            and authz.permission(actor(request), row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "")]
    for row in rows:
        if row["directory"] is None:
            try:
                row["directory"] = int(adapter(row["storage"], request).trash_is_dir(row["id"]))
            except HTTPException:
                row["directory"] = 0
        row["directory"] = bool(row["directory"])
    return rows


@app.get("/api/trash/{trash_id}/files")
def trash_files(trash_id: str, request: Request, path: str = ""):
    row = trash_row(trash_id, request)
    check(request, row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "")
    entries = adapter(row["storage"], request).list_trashed(trash_id, path)
    return [{**entry, "path": row["original_path"] + "/" + entry["path"],
             "trash_path": entry["path"], "trash_id": trash_id} for entry in entries]


def trash_row(trash_id: str, request: Request):
    with database() as db:
        row = db.execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if not row or (row["storage"] in PERSONAL and row["user_id"] != actor(request)["id"]):
        raise HTTPException(404, "Не найдено в корзине")
    return row


@app.post("/api/trash/{trash_id}/restore")
def restore(trash_id: str, request: Request):
    row = trash_row(trash_id, request)
    check(request, row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "", 2)
    adapter(row["storage"], request).restore(trash_id, row["original_path"])
    with database() as db:
        metadata.set_status(db, row["storage"], data_path(row["storage"], row["original_path"], request), "active")
        db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    audit(request, "restore", row["storage"], row["original_path"])
    return {"ok": True}


@app.delete("/api/trash/{trash_id}")
def delete_forever(trash_id: str, request: Request):
    row = trash_row(trash_id, request)
    check(request, row["storage"], row["original_path"].rsplit("/",1)[0] if "/" in row["original_path"] else "", 2)
    adapter(row["storage"], request).purge(trash_id)
    with database() as db:
        metadata.set_status(db, row["storage"], data_path(row["storage"], row["original_path"], request), "missing")
        db.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    audit(request, "delete_forever", row["storage"], row["original_path"])
    return {"ok": True}
