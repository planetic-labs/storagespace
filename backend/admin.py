"""Administration API for local role mappings and file permissions."""
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import authz

router = APIRouter(prefix="/api/admin")
storage_ids = {}


def administrator(request):
    if not request.state.user["admin"]:
        raise HTTPException(403, "Только для администратора")


def conflict(error):
    if isinstance(error, sqlite3.IntegrityError):
        raise HTTPException(409, "Такое имя или идентификатор уже существует") from None
    raise error


class RoleBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    ark_name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class MemberBody(BaseModel):
    user_id: int


class UserBody(BaseModel):
    active: bool


class GrantBody(BaseModel):
    subject_type: str
    subject_id: int
    storage: str
    path: str = ""
    level: int


class PersonalFolderBody(BaseModel):
    storage: str
    path: str
    label: str = Field(min_length=1, max_length=80)


@router.get("/overview")
def overview(request: Request):
    administrator(request)
    with authz.connection() as db:
        roles = [dict(r) for r in db.execute("SELECT * FROM roles ORDER BY id")]
        users = [dict(r) for r in db.execute("SELECT * FROM users ORDER BY email")]
        groups = [dict(r) for r in db.execute("SELECT * FROM groups ORDER BY name")]
        members = [dict(r) for r in db.execute("SELECT * FROM group_members")]
        grants = [dict(r) for r in db.execute("SELECT * FROM grants ORDER BY storage,path")]
        user_roles = [dict(r) for r in db.execute("SELECT * FROM user_roles")]
        personal_folders = [dict(r) for r in db.execute("SELECT storage,path,label FROM personal_folder_settings")]
    return {"roles": roles, "users": users, "groups": groups, "members": members,
            "grants": grants, "user_roles": user_roles, "personal_folders": personal_folders,
            "storages": [{"id": key, "name": value.name} for key, value in storage_ids.items()]}


@router.put("/personal-folders")
def save_personal_folder(body: PersonalFolderBody, request: Request):
    administrator(request)
    selected = storage_ids.get(body.storage)
    if not selected:
        raise HTTPException(404, "Хранилище не найдено")
    path, label = body.path.strip("/"), body.label.strip()
    if not path or not label or "\\" in path or any(part in ("", ".", "..") or part.startswith(".") for part in path.split("/")):
        raise HTTPException(400, "Некорректная папка или название")
    if not selected.is_dir(path):
        raise HTTPException(404, "Папка не найдена")
    with authz.connection() as db:
        db.execute("INSERT INTO personal_folder_settings(storage,path,label) VALUES(?,?,?) "
                   "ON CONFLICT(storage) DO UPDATE SET path=excluded.path,label=excluded.label",
                   (body.storage, path, label))
    return {"ok": True}


@router.delete("/personal-folders/{storage}")
def delete_personal_folder(storage: str, request: Request):
    administrator(request)
    with authz.connection() as db:
        db.execute("DELETE FROM personal_folder_settings WHERE storage=?", (storage,))
    return {"ok": True}


@router.get("/audit")
def audit_log(request: Request, before: int | None = None, limit: int = 50):
    administrator(request)
    if limit < 1 or limit > 100 or (before is not None and before < 1):
        raise HTTPException(400, "Некорректная страница журнала")
    with authz.connection() as db:
        rows = [dict(row) for row in db.execute(
            """SELECT id,at,operation,storage,path,target,user_id,user_email
               FROM audit WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?""",
            (before, before, limit + 1))]
    return {"items": rows[:limit], "next_before": rows[limit - 1]["id"] if len(rows) > limit else None}


@router.post("/roles")
def create_role(body: RoleBody, request: Request):
    administrator(request)
    try:
        with authz.connection() as db:
            cursor = db.execute("INSERT INTO roles(name,ark_name) VALUES(?,?)", (body.name.strip(), body.ark_name))
            return {"id": cursor.lastrowid}
    except sqlite3.IntegrityError as error:
        conflict(error)


@router.put("/roles/{role_id}")
def edit_role(role_id: int, body: RoleBody, request: Request):
    administrator(request)
    try:
        with authz.connection() as db:
            role = db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
            if not role:
                raise HTTPException(404, "Роль не найдена")
            if role["system"] and body.ark_name != "admin":
                raise HTTPException(400, "Идентификатор admin нельзя изменить")
            db.execute("UPDATE roles SET name=?,ark_name=? WHERE id=?", (body.name.strip(), body.ark_name, role_id))
    except sqlite3.IntegrityError as error:
        conflict(error)
    return {"ok": True}


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, request: Request):
    administrator(request)
    with authz.connection() as db:
        role = db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        if not role:
            raise HTTPException(404, "Роль не найдена")
        if role["system"]:
            raise HTTPException(400, "Системную роль нельзя удалить")
        db.execute("DELETE FROM grants WHERE subject_type='role' AND subject_id=?", (role_id,))
        db.execute("DELETE FROM roles WHERE id=?", (role_id,))
    return {"ok": True}


@router.post("/groups")
def create_group(body: GroupBody, request: Request):
    administrator(request)
    try:
        with authz.connection() as db:
            cursor = db.execute("INSERT INTO groups(name) VALUES(?)", (body.name.strip(),))
            return {"id": cursor.lastrowid}
    except sqlite3.IntegrityError as error:
        conflict(error)


@router.put("/groups/{group_id}")
def edit_group(group_id: int, body: GroupBody, request: Request):
    administrator(request)
    try:
        with authz.connection() as db:
            if not db.execute("UPDATE groups SET name=? WHERE id=?", (body.name.strip(), group_id)).rowcount:
                raise HTTPException(404, "Группа не найдена")
    except sqlite3.IntegrityError as error:
        conflict(error)
    return {"ok": True}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, request: Request):
    administrator(request)
    with authz.connection() as db:
        db.execute("DELETE FROM grants WHERE subject_type='group' AND subject_id=?", (group_id,))
        db.execute("DELETE FROM groups WHERE id=?", (group_id,))
    return {"ok": True}


@router.post("/groups/{group_id}/members")
def add_member(group_id: int, body: MemberBody, request: Request):
    administrator(request)
    try:
        with authz.connection() as db:
            db.execute("INSERT OR IGNORE INTO group_members(group_id,user_id) VALUES(?,?)", (group_id, body.user_id))
    except sqlite3.IntegrityError:
        raise HTTPException(404, "Пользователь или группа не найдены") from None
    return {"ok": True}


@router.delete("/groups/{group_id}/members/{user_id}")
def remove_member(group_id: int, user_id: int, request: Request):
    administrator(request)
    with authz.connection() as db:
        db.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))
    return {"ok": True}


@router.put("/users/{user_id}")
def edit_user(user_id: int, body: UserBody, request: Request):
    administrator(request)
    if user_id == request.state.user["id"] and not body.active:
        raise HTTPException(400, "Нельзя отключить собственный доступ")
    with authz.connection() as db:
        if not db.execute("UPDATE users SET active=? WHERE id=?", (int(body.active), user_id)).rowcount:
            raise HTTPException(404, "Пользователь не найден")
        if not body.active:
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return {"ok": True}


@router.post("/users/{user_id}/revoke")
def revoke_user(user_id: int, request: Request):
    administrator(request)
    with authz.connection() as db:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return {"ok": True}


@router.post("/grants")
def save_grant(body: GrantBody, request: Request):
    administrator(request)
    if body.subject_type not in ("role", "group", "user") or body.level not in (1, 2):
        raise HTTPException(400, "Некорректные права")
    if body.storage not in storage_ids:
        raise HTTPException(404, "Хранилище не найдено")
    path = body.path.strip("/")
    if path and ("\\" in path or any(part in ("", ".", "..") or part.startswith(".") for part in path.split("/"))):
        raise HTTPException(400, "Некорректный путь")
    if path and not storage_ids[body.storage].is_dir(path):
        raise HTTPException(404, "Папка не найдена")
    with authz.connection() as db:
        table = {"role": "roles", "group": "groups", "user": "users"}[body.subject_type]
        if not db.execute(f"SELECT 1 FROM {table} WHERE id=?", (body.subject_id,)).fetchone():
            raise HTTPException(404, "Роль, группа или пользователь не найдены")
        db.execute("""INSERT INTO grants(subject_type,subject_id,storage,path,level) VALUES(?,?,?,?,?)
                      ON CONFLICT(subject_type,subject_id,storage,path) DO UPDATE SET level=excluded.level""",
                   (body.subject_type, body.subject_id, body.storage, path, body.level))
    return {"ok": True}


@router.delete("/grants/{grant_id}")
def delete_grant(grant_id: int, request: Request):
    administrator(request)
    with authz.connection() as db:
        db.execute("DELETE FROM grants WHERE id=?", (grant_id,))
    return {"ok": True}
