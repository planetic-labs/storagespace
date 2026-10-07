"""Storage adapter boundary. API and metadata code use only this contract."""
from __future__ import annotations

import os
import shutil
import stat
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Protocol

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response


class Storage(Protocol):
    id: str
    name: str
    def status(self) -> dict: ...
    def list_dir(self, path: str, search: str = "") -> list[dict]: ...
    def info(self, path: str) -> dict: ...
    def exists(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def serve(self, path: str, inline: bool) -> Response: ...
    def upload(self, path: str, source: BinaryIO) -> dict: ...
    def mkdir(self, path: str) -> dict: ...
    def move(self, source: str, target: str) -> dict: ...
    def trash(self, path: str, trash_id: str) -> None: ...
    def trash_exists(self, trash_id: str) -> bool: ...
    def restore(self, trash_id: str, path: str) -> None: ...
    def purge(self, trash_id: str) -> None: ...
    def create_upload(self, upload_id: str, path: str) -> None: ...
    def upload_offset(self, upload_id: str) -> int: ...
    async def append_upload(self, upload_id: str, offset: int, size: int, chunks: AsyncIterator[bytes]) -> int: ...
    def finish_upload(self, upload_id: str, path: str, size: int) -> dict: ...
    def abort_upload(self, upload_id: str) -> None: ...


class MountedStorage:
    """Adapter for a local directory or a host-mounted SSHFS directory.

    A future SFTPStorage implements Storage without changing API endpoints.
    The host must guard mount points against writes to an unmounted directory.
    """

    def __init__(self, storage_id: str, name: str, root: str, mode: str = "sshfs"):
        if mode not in ("local", "sshfs"):
            raise ValueError(f"Unsupported storage mode: {mode}")
        if not Path(root).is_absolute():
            raise ValueError(f"Storage path must be absolute: {root}")
        self.id, self.name, self.root, self.mode = storage_id, name, Path(root), mode
        self._upload_locks: dict[str, asyncio.Lock] = {}

    def _online(self) -> bool:
        return self.root.is_dir() and (self.mode == "local" or os.path.ismount(self.root))

    def _require_online(self) -> None:
        if not self._online():
            raise HTTPException(503, "Хранилище недоступно")

    @staticmethod
    def _parts(path: str, *, allow_root: bool = False) -> list[str]:
        if path == "" and allow_root:
            return []
        if not path or path.startswith("/") or "\\" in path or "\x00" in path:
            raise HTTPException(400, "Некорректный путь")
        parts = path.split("/")
        if any(x in ("", ".", "..") for x in parts) or parts[0] in (".storage-space-trash", ".storage-space-uploads"):
            raise HTTPException(400, "Некорректный путь")
        if any(len(part.encode("utf-8")) > 255 for part in parts):
            raise HTTPException(400, "Имя файла или папки слишком длинное")
        return parts

    def _path(self, path: str, *, allow_root: bool = False) -> Path:
        self._require_online()
        current = self.root
        for part in self._parts(path, allow_root=allow_root):
            current = current / part
            if current.is_symlink():
                raise HTTPException(400, "Символьные ссылки не поддерживаются")
        return current

    def _info(self, path: Path) -> dict:
        info = path.stat()
        return {"storage": self.id, "path": path.relative_to(self.root).as_posix(),
                "name": path.name, "directory": stat.S_ISDIR(info.st_mode),
                "size": info.st_size,
                "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()}

    def status(self) -> dict:
        online = self._online()
        free = total = None
        if online:
            usage = shutil.disk_usage(self.root)
            free, total = usage.free, usage.total
        return {"id": self.id, "name": self.name, "online": online, "free": free, "total": total}

    def list_dir(self, path: str, search: str = "") -> list[dict]:
        directory = self._path(path, allow_root=True)
        if not directory.is_dir():
            raise HTTPException(404, "Папка не найдена")
        entries = [self._info(p) for p in directory.iterdir()
                   if p.name not in (".storage-space-trash", ".storage-space-uploads") and not p.is_symlink()
                   and search.casefold() in p.name.casefold()]
        return sorted(entries, key=lambda x: (not x["directory"], x["name"].casefold()))

    def info(self, path: str) -> dict:
        file = self._path(path)
        if not file.exists():
            raise HTTPException(404, "Файл или папка не найдены")
        return self._info(file)

    def exists(self, path: str) -> bool:
        return self._path(path).exists()

    def is_dir(self, path: str) -> bool:
        return self._path(path).is_dir()

    def serve(self, path: str, inline: bool) -> Response:
        file = self._path(path)
        if not file.is_file():
            raise HTTPException(404, "Файл не найден")
        return FileResponse(file, filename=file.name,
                            content_disposition_type="inline" if inline else "attachment")

    def upload(self, path: str, source: BinaryIO) -> dict:
        target = self._path(path)
        if not target.parent.is_dir():
            raise HTTPException(404, "Папка не найдена")
        try:
            with target.open("xb") as out:
                shutil.copyfileobj(source, out)
        except FileExistsError:
            raise HTTPException(409, "Такое имя уже существует")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return self._info(target)

    def mkdir(self, path: str) -> dict:
        target = self._path(path)
        if not target.parent.is_dir():
            raise HTTPException(404, "Родительская папка не найдена")
        try:
            target.mkdir()
        except FileExistsError:
            raise HTTPException(409, "Такое имя уже существует")
        return self._info(target)

    def move(self, source: str, target: str) -> dict:
        src, dst = self._path(source), self._path(target)
        if not src.exists() or not dst.parent.is_dir():
            raise HTTPException(404, "Файл или папка не найдены")
        if dst.exists():
            raise HTTPException(409, "Такое имя уже существует")
        if src.is_dir() and dst.is_relative_to(src):
            raise HTTPException(400, "Нельзя переместить папку в саму себя")
        src.rename(dst)
        return self._info(dst)

    def _trash_path(self, trash_id: str) -> Path:
        self._require_online()
        if len(trash_id) != 32 or any(c not in "0123456789abcdef" for c in trash_id):
            raise HTTPException(400, "Некорректный идентификатор")
        return self.root / ".storage-space-trash" / trash_id

    def trash(self, path: str, trash_id: str) -> None:
        source = self._path(path)
        if not source.exists():
            raise HTTPException(404, "Файл или папка не найдены")
        target = self._trash_path(trash_id)
        target.parent.mkdir(exist_ok=True)
        source.rename(target)

    def trash_exists(self, trash_id: str) -> bool:
        return self._trash_path(trash_id).exists()

    def restore(self, trash_id: str, path: str) -> None:
        source, target = self._trash_path(trash_id), self._path(path)
        if target.exists():
            raise HTTPException(409, "Исходное имя уже занято")
        if not target.parent.is_dir() or not source.exists():
            raise HTTPException(409, "Исходная папка или файл в корзине недоступны")
        source.rename(target)

    def purge(self, trash_id: str) -> None:
        target = self._trash_path(trash_id)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)

    def _upload_path(self, upload_id: str) -> Path:
        self._require_online()
        if len(upload_id) != 32 or any(c not in "0123456789abcdef" for c in upload_id):
            raise HTTPException(400, "Некорректный идентификатор")
        return self.root / ".storage-space-uploads" / (upload_id + ".part")

    def create_upload(self, upload_id: str, path: str) -> None:
        target = self._path(path)
        if not target.parent.is_dir():
            raise HTTPException(404, "Папка не найдена")
        if target.exists():
            raise HTTPException(409, "Такое имя уже существует")
        temp = self._upload_path(upload_id)
        temp.parent.mkdir(exist_ok=True)
        with temp.open("xb"):
            pass

    def upload_offset(self, upload_id: str) -> int:
        temp = self._upload_path(upload_id)
        if not temp.exists():
            raise HTTPException(404, "Незавершённая загрузка не найдена")
        return temp.stat().st_size

    async def append_upload(self, upload_id: str, offset: int, size: int, chunks: AsyncIterator[bytes]) -> int:
        lock = self._upload_locks.setdefault(upload_id, asyncio.Lock())
        async with lock:
            temp = self._upload_path(upload_id)
            if not temp.exists():
                raise HTTPException(404, "Незавершённая загрузка не найдена")
            current = temp.stat().st_size
            if current != offset:
                raise HTTPException(409, {"message": "Смещение изменилось", "offset": current})
            written = 0
            with temp.open("r+b") as out:
                out.seek(current)
                async for chunk in chunks:
                    if written + len(chunk) > 8 * 1024 * 1024 or current + written + len(chunk) > size:
                        raise HTTPException(413, "Слишком большая часть файла")
                    await asyncio.to_thread(out.write, chunk)
                    written += len(chunk)
                await asyncio.to_thread(out.flush)
                await asyncio.to_thread(os.fsync, out.fileno())
            return current + written

    def finish_upload(self, upload_id: str, path: str, size: int) -> dict:
        temp, target = self._upload_path(upload_id), self._path(path)
        if not temp.exists() or temp.stat().st_size != size:
            raise HTTPException(409, "Файл загружен не полностью")
        if target.exists():
            raise HTTPException(409, "Такое имя уже существует")
        temp.rename(target)
        return self._info(target)

    def abort_upload(self, upload_id: str) -> None:
        self._upload_path(upload_id).unlink(missing_ok=True)
