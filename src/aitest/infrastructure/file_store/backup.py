"""Closure backup and verification; no business material deletion."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


class BackupError(RuntimeError):
    """备份不可用、目标非空或恢复后核对失败。"""


@dataclass(frozen=True, slots=True)
class RestoreReport:
    """安全恢复结果；恢复后按清单重新核对。"""

    backup: Path
    target: Path
    files_restored: int
    bytes_restored: int
    verified: bool
    state: str  # restored | empty


class FileBackupStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def create(self, destination: Path) -> Path:
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        for name in (
            "workspace.json",
            "records.json",
            "indexes.json",
            "current.json",
            "commit.json",
            "events.json",
            "transactions.json",
        ):
            source = self.root / name
            if source.exists():
                shutil.copy2(source, destination / name)
        for name in (
            "objects",
            "spool",
            "checkpoints",
            "transactions",
            "events",
            "manifests",
        ):
            source = self.root / name
            if source.exists():
                shutil.copytree(source, destination / name, dirs_exist_ok=True)
        manifest = {
            str(p.relative_to(destination)): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in destination.rglob("*")
            if p.is_file()
        }
        (destination / "backup.json").write_text(
            json.dumps(
                {"schema": "aitest.backup/1.0", "files": manifest}, indent=2
            ),
            encoding="utf-8",
        )
        return destination

    def verify(self, backup: Path) -> dict[str, object]:
        backup = backup.resolve()
        raw = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
        errors: list[str] = []
        for name, digest in raw["files"].items():
            path = backup / name
            if (
                not path.exists()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                errors.append(name)
        return {"ok": not errors, "errors": errors}

    def restore(self, *, backup: Path, target: Path) -> RestoreReport:
        """把已校验备份恢复到空目标，恢复后按清单重新核对。

        从不覆盖非空目录；调用方须显式提供新的恢复目标。
        """
        backup = backup.resolve()
        target = target.resolve()
        verification = self.verify(backup)
        if not verification["ok"]:
            raise BackupError(f"备份校验失败: {verification['errors']}")
        if target.exists() and any(target.iterdir()):
            raise BackupError("恢复目标非空，拒绝覆盖")
        raw = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
        manifest: dict[str, str] = dict(raw["files"])
        target.mkdir(parents=True, exist_ok=True)
        files_restored = 0
        bytes_restored = 0
        for name, _digest in manifest.items():
            source = backup / name
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            files_restored += 1
            bytes_restored += destination.stat().st_size
        post_errors: list[str] = []
        for name, digest in manifest.items():
            path = target / name
            if (
                not path.exists()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                post_errors.append(name)
        if post_errors:
            raise BackupError(f"恢复后核对失败: {post_errors}")
        state = "restored" if files_restored else "empty"
        return RestoreReport(
            backup=backup,
            target=target,
            files_restored=files_restored,
            bytes_restored=bytes_restored,
            verified=True,
            state=state,
        )
