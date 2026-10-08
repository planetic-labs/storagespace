"""Ark login, revocable local sessions and storage permissions."""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

import httpx
import jwt
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

DATA = Path(os.getenv("APP_DATA", "/data"))
DB = DATA / "app.sqlite3"
COOKIE = "storage_space_session"
SESSION_DAYS = 7
CHECK_SECONDS = 600
ARK_JWKS_URL = os.getenv("ARK_JWKS_URL", "")
ARK_AUDIENCE = os.getenv("ARK_AUDIENCE", "")
ARK_ISSUER = os.getenv("ARK_ISSUER", "")
SECRET = os.getenv("SESSION_SECRET", "")
_jwk_client = jwt.PyJWKClient(ARK_JWKS_URL, cache_keys=True) if ARK_JWKS_URL else None
_refresh_locks = {}
personal_storage_ids: set[str] = set()


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
          ark_name TEXT NOT NULL UNIQUE, system INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, ark_sub TEXT NOT NULL UNIQUE,
          email TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
          ark_roles TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS personal_folder_settings(storage TEXT PRIMARY KEY,
          path TEXT NOT NULL, label TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS user_roles(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE, PRIMARY KEY(user_id,role_id));
        CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, PRIMARY KEY(group_id,user_id));
        CREATE TABLE IF NOT EXISTS grants(id INTEGER PRIMARY KEY, subject_type TEXT NOT NULL
          CHECK(subject_type IN ('role','group','user')), subject_id INTEGER NOT NULL,
          storage TEXT NOT NULL, path TEXT NOT NULL DEFAULT '', level INTEGER NOT NULL CHECK(level IN (1,2)),
          UNIQUE(subject_type,subject_id,storage,path));
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL
          REFERENCES users(id) ON DELETE CASCADE, refresh_token TEXT NOT NULL,
          checked_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_grants_storage ON grants(storage,path);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """)
        if "ark_roles" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
            db.execute("ALTER TABLE users ADD COLUMN ark_roles TEXT NOT NULL DEFAULT '[]'")
        if "first_name" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
            db.execute("ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''")
        if "last_name" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
            db.execute("ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''")
        for name, ark_name, system in (("Админ", "admin", 1), ("Воин", "warrior", 0), ("Ученик", "student", 0)):
            db.execute("INSERT OR IGNORE INTO roles(name,ark_name,system) VALUES(?,?,?)", (name, ark_name, system))


def _cipher():
    if len(SECRET) < 32:
        raise RuntimeError("SESSION_SECRET must contain at least 32 characters")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest()))


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_ark(token):
    if not _jwk_client or not ARK_AUDIENCE or not ARK_ISSUER:
        raise HTTPException(503, "Авторизация Ark не настроена")
    try:
        key = _jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=ARK_AUDIENCE,
                            issuer=ARK_ISSUER, options={"require": ["exp", "aud", "iss", "sub", "roles"]})
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        raise HTTPException(401, "Недействительный ответ Ark") from None
    if claims.get("status") != "active" or not isinstance(claims.get("roles"), list):
        raise HTTPException(403, "Учётная запись Ark неактивна")
    return claims


async def ark_post(route, payload):
    if not ARK_JWKS_URL:
        raise HTTPException(503, "Авторизация Ark не настроена")
    base = ARK_JWKS_URL.removesuffix("/.well-known/jwks.json")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(base + "/api/v1/auth/" + route, json=payload)
    except httpx.HTTPError:
        raise HTTPException(503, "Ark временно недоступен") from None
    if response.status_code != 200:
        raise HTTPException(401 if response.status_code in (400, 401, 403) else 503,
                            "Неверный код или учётная запись недоступна" if response.status_code < 500 else "Ark временно недоступен")
    return response.json()


def _sync_roles(db, user_id, ark_roles):
    db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
    for row in db.execute("SELECT id,ark_name FROM roles"):
        if row["ark_name"] in ark_roles:
            db.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (user_id, row["id"]))


async def profile_name(access_token):
    base = ARK_JWKS_URL.removesuffix("/.well-known/jwks.json")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(base + "/api/v1/users/me",
                                        headers={"Authorization": f"Bearer {access_token}"})
            response.raise_for_status()
            profile = response.json()
        if not isinstance(profile, dict):
            return None
        return (str(profile.get("first_name") or "").strip()[:255],
                str(profile.get("last_name") or "").strip()[:255])
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def needs_profile(ark_sub):
    with connection() as db:
        row = db.execute("SELECT first_name,last_name FROM users WHERE ark_sub=?", (str(ark_sub),)).fetchone()
    return row is None or not (row["first_name"] and row["last_name"])


def login(claims, email, refresh_token, name=None):
    now = int(time.time())
    with connection() as db:
        row = db.execute("SELECT * FROM users WHERE ark_sub=?", (str(claims["sub"]),)).fetchone()
        if row and not row["active"]:
            raise HTTPException(403, "Доступ к Storage Space отключён")
        if not row:
            db.execute("INSERT INTO users(ark_sub,email,created_at,ark_roles) VALUES(?,?,?,?)",
                       (str(claims["sub"]), email, now, json.dumps(claims["roles"])))
            row = db.execute("SELECT * FROM users WHERE ark_sub=?", (str(claims["sub"]),)).fetchone()
        else:
            db.execute("UPDATE users SET email=?,ark_roles=? WHERE id=?",
                       (email, json.dumps(claims["roles"]), row["id"]))
        if name:
            db.execute("UPDATE users SET first_name=CASE WHEN first_name='' THEN ? ELSE first_name END,"
                       "last_name=CASE WHEN last_name='' THEN ? ELSE last_name END WHERE id=?",
                       (name[0], name[1], row["id"]))
        _sync_roles(db, row["id"], claims["roles"])
        token = secrets.token_urlsafe(32)
        encrypted = _cipher().encrypt(refresh_token.encode()).decode()
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                   (_hash(token), row["id"], encrypted, now, now + SESSION_DAYS * 86400))
    return token


async def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Требуется вход")
    hashed = _hash(token)
    now = int(time.time())
    with connection() as db:
        row = db.execute("SELECT s.*,u.ark_sub,u.email,u.active FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (hashed,)).fetchone()
        if not row or not row["active"] or row["expires_at"] <= now:
            raise HTTPException(401, "Сессия завершена")
        user_id = row["user_id"]
        checked_at = row["checked_at"]
        encrypted_refresh = row["refresh_token"]
    if now - checked_at >= CHECK_SECONDS:
      lock = _refresh_locks.setdefault(hashed, asyncio.Lock())
      async with lock:
        with connection() as db:
            current = db.execute("SELECT checked_at,refresh_token FROM sessions WHERE token_hash=?", (hashed,)).fetchone()
        if not current:
            raise HTTPException(401, "Сессия завершена")
        if now - current["checked_at"] >= CHECK_SECONDS:
            await _refresh_session(hashed, user_id, row["ark_sub"], current["refresh_token"], now)
      _refresh_locks.pop(hashed, None)
    with connection() as db:
        user = db.execute("SELECT id,ark_sub,email,first_name,last_name,active,ark_roles FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not user["active"]:
            raise HTTPException(401, "Доступ отключён")
        _sync_roles(db, user_id, json.loads(user["ark_roles"]))
        roles = [dict(r) for r in db.execute("SELECT r.id,r.name,r.ark_name,r.system FROM roles r JOIN user_roles ur ON ur.role_id=r.id WHERE ur.user_id=?", (user_id,))]
    return {**dict(user), "roles": roles, "admin": any(r["ark_name"] == "admin" and r["system"] for r in roles), "session_hash": hashed}


async def _refresh_session(hashed, user_id, ark_sub, encrypted_refresh, now):
        try:
            refreshed = await ark_post("refresh", {"refresh_token": _cipher().decrypt(encrypted_refresh.encode()).decode()})
            claims = verify_ark(refreshed.get("access_token"))
            if str(claims["sub"]) != ark_sub:
                raise HTTPException(401, "Сессия завершена")
            new_refresh = refreshed.get("refresh_token") or _cipher().decrypt(encrypted_refresh.encode()).decode()
        except (HTTPException, Exception) as exc:
            # A failed Ark check never extends a session or preserves stale roles.
            if isinstance(exc, HTTPException) and exc.status_code == 503:
                raise
            with connection() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashed,))
            raise HTTPException(401, "Сессия завершена. Войдите снова") from None
        with connection() as db:
            db.execute("UPDATE users SET ark_roles=? WHERE id=?", (json.dumps(claims["roles"]), user_id))
            db.execute("UPDATE sessions SET checked_at=?,refresh_token=? WHERE token_hash=?",
                       (now, _cipher().encrypt(new_refresh.encode()).decode(), hashed))


def permission(user, storage, path):
    if storage in personal_storage_ids:
        return 2
    if user["admin"]:
        return 2
    path = path.strip("/")
    with connection() as db:
        rows = db.execute("""SELECT g.path,g.level FROM grants g WHERE g.storage=? AND (
          (g.subject_type='user' AND g.subject_id=?) OR
          (g.subject_type='role' AND g.subject_id IN (SELECT role_id FROM user_roles WHERE user_id=?)) OR
          (g.subject_type='group' AND g.subject_id IN (SELECT group_id FROM group_members WHERE user_id=?)))""",
          (storage, user["id"], user["id"], user["id"]))
        return max((r["level"] for r in rows if not r["path"] or path == r["path"] or path.startswith(r["path"] + "/")), default=0)


def can_traverse(user, storage, path):
    if permission(user, storage, path):
        return True
    with connection() as db:
        rows = db.execute("""SELECT g.path FROM grants g WHERE g.storage=? AND (
          (g.subject_type='user' AND g.subject_id=?) OR
          (g.subject_type='role' AND g.subject_id IN (SELECT role_id FROM user_roles WHERE user_id=?)) OR
          (g.subject_type='group' AND g.subject_id IN (SELECT group_id FROM group_members WHERE user_id=?)))""",
          (storage, user["id"], user["id"], user["id"]))
        prefix = path.strip("/") + "/" if path else ""
        return any(r["path"].startswith(prefix) for r in rows)


def require(user, storage, path, level=1, traverse=False):
    if permission(user, storage, path) >= level or (traverse and level == 1 and can_traverse(user, storage, path)):
        return
    raise HTTPException(403, "Нет доступа к папке или файлу")
