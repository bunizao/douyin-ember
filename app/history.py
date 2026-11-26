from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AlreadyRunningError(RuntimeError):
    pass


class History:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries = self._load()

    def run_date(self, timezone: str) -> str:
        try:
            return datetime.now(ZoneInfo(timezone)).date().isoformat()
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"未知时区: {timezone}，请安装 tzdata 或修正配置") from exc

    def key(self, task_id: str, run_date: str, target: str, message_id: str) -> str:
        return f"{task_id}:{run_date}:{target}:{message_id}"

    def contains(self, key: str) -> bool:
        return key in self.entries

    def reserve(self, key: str) -> None:
        self.entries[key] = {"status": "unknown", "started_at": datetime.now().astimezone().isoformat()}
        self._save()

    def mark_success(self, key: str) -> None:
        self.entries[key] = {"status": "success", "finished_at": datetime.now().astimezone().isoformat()}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self.entries, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        if os.name != "nt":
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or any(
                not isinstance(entry, dict) or entry.get("status") not in {"unknown", "success"}
                for entry in value.values()
            ):
                raise ValueError(f"发送历史损坏，为避免重复发送，任务已停止: {self.path}")
            return value
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"发送历史损坏，为避免重复发送，任务已停止: {self.path}") from exc


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b" ")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise AlreadyRunningError(f"A task already holds {path}") from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AlreadyRunningError(f"A task already holds {path}") from exc
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        # Keep the inode stable so another process cannot acquire a replacement file.
        os.close(descriptor)
