"""Runtime 命令模块：backup_cleanup。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from aitest.commands import (
    Payload,
    _project,
    _require,
    _text,
)
from aitest.config import known_secret_values
from aitest.core.evidence import (
    data_prefix,
    evidence_bytes,
)
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import load_project_config
from aitest.runtime._common import _as_bool, facade_attr
from aitest.security import redact, redact_bytes
from aitest.storage.workspace import Workspace, iso_now


def _invalid_backup(message: str) -> AiTestError:
    return AiTestError("BACKUP_INVALID", message, "换一个备份或重新备份")



_MAX_BACKUP_FILES = 100_000



_MAX_BACKUP_UNCOMPRESSED = 2 * 1024**3



def _check_zip_member(name: str) -> str:
    import posixpath

    raw = name.replace("\\", "/")
    normalized = posixpath.normpath(raw)
    parts = normalized.split("/")
    if normalized != raw:
        raise _invalid_backup(f"备份路径未规范化：{name}")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if (
        not name
        or normalized.startswith("/")
        or raw.startswith("/")
        or ".." in parts
        or ":" in normalized
        or any(
            part.endswith((".", " ")) or part.split(".")[0].upper() in reserved
            for part in parts
        )
    ):
        raise _invalid_backup(f"备份包含非法路径：{name}")
    return normalized



def _read_zip_limited(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with archive.open(name) as stream:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise _invalid_backup("备份 manifest 体积超过上限")
            chunks.append(chunk)
        return b"".join(chunks)



def _zip_manifest(archive: zipfile.ZipFile) -> tuple[list[str], dict[str, str]]:
    try:
        raw = _read_zip_limited(archive, "manifest.json", 16 * 1024 * 1024)
    except KeyError as exc:
        raise _invalid_backup("备份缺少 manifest.json") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_backup("备份 manifest 不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise _invalid_backup("备份 manifest 不是对象")
    if data.get("schema_version") != 1:
        raise AiTestError(
            "SCHEMA_UNSUPPORTED",
            "备份格式版本不受支持：" + repr(data.get("schema_version")),
            "用当前版本重新备份；旧备份需先转换",
        )
    files = data.get("files")
    hashes = data.get("sha256")
    count = data.get("file_count")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise _invalid_backup("备份 manifest 缺少文件清单")
    if not isinstance(hashes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in hashes.items()
    ):
        raise _invalid_backup("备份 manifest 缺少 sha256 摘要")
    if not isinstance(count, int) or isinstance(count, bool):
        raise _invalid_backup("备份 manifest 缺少文件总数")
    if count != len(files) or set(files) != set(hashes):
        raise _invalid_backup("备份 manifest 文件总数或摘要与实际文件不一致")
    return files, hashes



def _evidence_refs(value: object) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_refs" and isinstance(item, list):
                refs.extend(entry for entry in item if isinstance(entry, str))
            elif key == "evidence_index" and isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict) and isinstance(entry.get("ref"), str):
                        refs.append(entry["ref"])
            elif isinstance(item, (dict, list)):
                refs.extend(_evidence_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_evidence_refs(item))
    return refs



def _is_local_ref(ref: str) -> bool:
    """A reference that should resolve inside the restored workspace."""
    text = ref.strip()
    if not text or "://" in text:
        return False
    normalized = text.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return not (":" in parts[0] or Path(text).is_absolute())



def _record_base(restored: Path, path: Path, data: dict[str, Any]) -> Path:
    """Resolve relative evidence references for one restored business record."""
    if path.name == "run.json":
        return path.parent
    run_id = data.get("run_id")
    if isinstance(run_id, str) and run_id:
        parts = path.relative_to(restored).parts
        if len(parts) >= 2 and parts[0] == "projects":
            candidate = restored / "projects" / parts[1] / "runs" / run_id
            if candidate.is_dir():
                return candidate
    return path.parent



def _is_evidence_ref(ref: str) -> bool:
    """Only workspace evidence belongs in missing-evidence checks.

    Mock judgement also carries ``evidence_refs`` pointing at project source
    files; those are not copied into the workspace and must not be reported as
    missing evidence.
    """
    normalized = ref.strip().replace("\\", "/")
    return normalized.startswith("evidence/") or normalized.startswith("generated/")



def _missing_evidence(restored: Path) -> list[str]:
    missing: list[str] = []

    def check(base: Path, refs: object) -> None:
        for ref in _evidence_refs({"evidence_refs": refs}):
            if not _is_local_ref(ref) or not _is_evidence_ref(ref) or ref in missing:
                continue
            if not (base / ref).is_file():
                missing.append(ref)

    for path in sorted(restored.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        parts = path.relative_to(restored).parts
        if path.parent.name == "issues" and len(parts) >= 3 and parts[0] == "projects":
            project_id = parts[1]
            occurrences = data.get("occurrences")
            for occurrence in occurrences if isinstance(occurrences, list) else []:
                if not isinstance(occurrence, dict):
                    continue
                run_id = occurrence.get("run_id")
                if not isinstance(run_id, str) or not run_id:
                    continue
                check(
                    restored / "projects" / project_id / "runs" / run_id,
                    occurrence.get("evidence_refs"),
                )
            continue
        base = _record_base(restored, path, data)
        for ref in _evidence_refs(data):
            if not _is_local_ref(ref) or not _is_evidence_ref(ref) or ref in missing:
                continue
            if not (base / ref).is_file():
                missing.append(ref)
    return sorted(missing)



def _workspace_evidence_bytes(ws: Workspace) -> int:
    total = 0
    for project in ws.list_projects():
        runs = ws.project_dir(project.project_id) / "runs"
        if not runs.is_dir():
            continue
        for run_dir in runs.iterdir():
            if run_dir.is_dir():
                total += evidence_bytes(run_dir)
    return total



def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()



_LOCK_FILENAMES = frozenset({".workspace.lock", ".project.lock", ".repair.lock"})


def cmd_backup_create(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    ws_root = ws.root.resolve()
    try:
        if target.is_relative_to(ws_root):
            raise invalid_path(
                f"备份输出路径不能位于工作空间目录内：{target}",
                "请选择工作空间目录之外的备份文件路径",
            )
    except ValueError:
        pass
    if target.exists():
        raise invalid_request(f"备份文件已存在：{target}", "换一个文件名，不覆盖已有备份")
    include_evidence = _as_bool(payload.get("include_evidence"), False)
    target.parent.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    temp_target = target.parent / f".{target.name}.{secrets.token_hex(4)}.tmp"
    with tempfile.TemporaryDirectory(prefix=f".{target.name}.stage-") as stage_dir:
        stage_root = Path(stage_dir).resolve()
        # 仅在扫描清单和复制快照瞬间持有工作空间独占锁，压缩与脱敏全部移出锁外执行。
        with ws.lock(timeout=60.0):
            configs = [
                load_project_config(ws, project.project_id) for project in ws.list_projects()
            ]
            secret_list = known_secret_values(configs)
            for path in ws.root.rglob("*"):
                if not path.is_file() or path in (target, temp_target) or path.is_symlink():
                    continue
                resolved_path = path.resolve()
                if resolved_path.is_relative_to(stage_root):
                    continue
                relative = path.relative_to(ws.root)
                if (
                    path.name in _LOCK_FILENAMES
                    or relative.parts[0] in {"requests", "backups"}
                    or (
                        relative.parts[0] == ".cache"
                        and len(relative.parts) > 1
                        and relative.parts[1] == "plan"
                    )
                    or str(relative).replace("\\", "/") == "manifest.json"
                ):
                    continue
                if not include_evidence and (
                    "evidence" in relative.parts or "generated" in relative.parts
                ):
                    continue
                files.append(relative)
                staged = stage_root / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, staged)
            estimated_evidence = _workspace_evidence_bytes(ws)

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "created_at": iso_now(),
            "includes_evidence": include_evidence,
            "file_count": len(files),
            "total_files": len(files),
            "files": sorted(str(item).replace("\\", "/") for item in files),
            "sha256": {},
        }
        try:
            with zipfile.ZipFile(temp_target, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative in sorted(files):
                    key = str(relative).replace("\\", "/")
                    staged_file = stage_root / relative
                    # 超过 2 MiB 或纯二进制文件分块流式写入 zip，防止大证据一次性读入内存。
                    fsize = staged_file.stat().st_size
                    if fsize > 2 * 1024 * 1024:
                        manifest["sha256"][key] = _sha256_file(staged_file)
                        archive.write(staged_file, arcname=key)
                    else:
                        data = redact_bytes(staged_file.read_bytes(), secret_list)
                        manifest["sha256"][key] = hashlib.sha256(data).hexdigest()
                        archive.writestr(key, data)
                archive.writestr(
                    "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
                )
            os.replace(temp_target, target)
        except OSError as exc:
            temp_target.unlink(missing_ok=True)
            raise AiTestError(
                "BACKUP_INVALID", f"备份写入失败：{exc}", "检查目标目录权限与磁盘空间"
            ) from exc
    digest = _sha256_file(target)
    return {
        "path": str(target),
        "sha256": digest,
        "bytes": target.stat().st_size,
        "includes_evidence": include_evidence,
        "files": len(files),
        "file_count": len(files),
        "estimated_evidence_bytes": estimated_evidence,
        "estimated_evidence_mb": round(estimated_evidence / (1024 * 1024), 1),
    }



def cmd_backup_restore(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del ws, confirm
    source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target = Path(_text({"target": _require(payload, "target")}, "target")).expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"备份不存在：{source}")
    if target.exists():
        if not target.is_dir():
            raise invalid_path(f"目标不是目录：{target}", "换一个空目录")
        if any(target.iterdir()):
            raise invalid_path(f"目标目录非空：{target}", "换一个空目录")
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise _invalid_backup(f"备份无法打开：{exc}") from exc
    with archive:
        members: dict[str, zipfile.ZipInfo] = {}
        seen_lower: dict[str, str] = {}
        declared_total = 0
        for member in archive.infolist():
            normalized = _check_zip_member(member.filename)
            if member.is_dir():
                continue
            lower = normalized.lower()
            if normalized in members or lower in seen_lower:
                raise _invalid_backup(f"备份包含重复条目：{member.filename}")
            seen_lower[lower] = normalized
            if member.external_attr >> 16 & 0o170000 == 0o120000:
                raise _invalid_backup(f"备份包含符号链接：{member.filename}")
            declared_total += member.file_size
            if len(members) + 1 > facade_attr("_MAX_BACKUP_FILES", _MAX_BACKUP_FILES):
                raise _invalid_backup("备份条目过多，拒绝恢复")
            if declared_total > facade_attr("_MAX_BACKUP_UNCOMPRESSED", _MAX_BACKUP_UNCOMPRESSED):
                raise _invalid_backup("备份解压后体积超过上限，拒绝恢复")
            members[normalized] = member
        files, hashes = _zip_manifest(archive)
        try:
            manifest = json.loads(
                _read_zip_limited(archive, "manifest.json", 16 * 1024 * 1024).decode("utf-8")
            )
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid_backup("备份 manifest 无法读取") from exc
        evidence_included = bool(manifest.get("includes_evidence"))
        if set(members) - {"manifest.json"} != set(files):
            raise _invalid_backup("备份 manifest 与 zip 内容不一致")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.parent / f".{target.name}.restore-{secrets.token_hex(4)}"
        actual_total = 0
        try:
            temp_target.mkdir(parents=True)
            for name in files:
                _check_zip_member(name)
                dest = (temp_target / name).resolve()
                if not dest.is_relative_to(temp_target.resolve()):
                    raise _invalid_backup(f"备份路径越界：{name}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                hasher = hashlib.sha256()
                with archive.open(members[name]) as src, dest.open("wb") as out:
                    while chunk := src.read(65536):
                        actual_total += len(chunk)
                        max_bytes = facade_attr(
                            "_MAX_BACKUP_UNCOMPRESSED", _MAX_BACKUP_UNCOMPRESSED
                        )
                        if actual_total > max_bytes:
                            raise _invalid_backup("备份实际解压体积超过上限，拒绝恢复")
                        hasher.update(chunk)
                        out.write(chunk)
                if hasher.hexdigest() != hashes[name]:
                    raise _invalid_backup(f"备份文件摘要不匹配：{name}")
            if target.exists():
                target.rmdir()
            os.replace(temp_target, target)
        except AiTestError:
            shutil.rmtree(temp_target, ignore_errors=True)
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            shutil.rmtree(temp_target, ignore_errors=True)
            raise _invalid_backup(f"恢复失败：{exc}") from exc
    missing = _missing_evidence(target)
    if missing and evidence_included:
        notes = f"证据未包含：{len(missing)} 个引用在恢复目录中不存在"
    elif missing:
        notes = f"证据未包含（备份创建时选择不包含）：{len(missing)} 个证据引用在恢复后不可用"
    else:
        notes = ""
    return {
        "target": str(target),
        "files": len(files),
        "verified": True,
        "evidence_included": evidence_included,
        "missing_evidence": missing,
        "notes": notes,
    }



def _trim_output(text: str | None, limit: int = 4000) -> str:
    if not text:
        return ""
    return text[-limit:]



def _action_log(ws: Workspace, entry: dict[str, Any]) -> str:
    month = datetime.now().astimezone().strftime("%Y-%m")
    path = ws.root / "actions" / f"{month}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with ws.lock(timeout=30.0), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return str(path.relative_to(ws.root)).replace("\\", "/")



def cmd_data_cleanup(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    project = _project(ws, payload)
    config = load_project_config(ws, project.project_id)
    settings = config.get("data")
    dry_run = _as_bool(payload.get("dry_run"), True)
    if not isinstance(settings, dict):
        raise invalid_request(
            "未配置数据清理命令",
            '在 project.config.json 增加 "data": {"cleanup_dry_run": "...", "cleanup": "..."}',
        )
    key = "cleanup_dry_run" if dry_run else "cleanup"
    command_value = settings.get(key)
    if not isinstance(command_value, str) or not command_value.strip():
        raise invalid_request(
            f"未配置 data.{key}",
            f'在 project.config.json 的 data 段补充 "{key}" 命令',
        )
    if not dry_run and not confirm:
        raise AiTestError("CONFIRMATION_REQUIRED", "清理数据需要确认", "加 --confirm 或先 dry-run")
    run_id = _text(payload, "run_id")
    needs_run_id = "{run_id}" in command_value or "{data_prefix}" in command_value
    if needs_run_id and not run_id and not dry_run:
        raise invalid_request("执行清理需要提供 run_id")
    if run_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
        raise invalid_request("run_id 只允许字母、数字、- 和 _，且不能以点开头")
    if not dry_run and not needs_run_id:
        raise invalid_request(
            "清理命令必须包含 {run_id} 或 {data_prefix} 占位符",
            "只允许按本次运行的标记或 ID 清理，不做全表或按时间删除",
        )
    command = command_value.replace("{run_id}", run_id).replace(
        "{data_prefix}", data_prefix(run_id) if run_id else ""
    )
    secret_list = known_secret_values([config])
    started = {
        "at": iso_now(),
        "run_id": run_id,
        "command": redact(command, secret_list),
        "dry_run": dry_run,
        "phase": "started",
        "exit_code": None,
    }
    _action_log(ws, started)
    from aitest.runners.command_runner import run_command
    from aitest.worker import _run_env

    cleanup_env = _run_env(Path(project.root), None, run_id or "cleanup")
    result = run_command(
        command,
        cwd=Path(project.root),
        timeout_seconds=600,
        env=cleanup_env,
    )
    stdout = redact(
        _trim_output(result.stdout.decode("utf-8", errors="replace")), secret_list
    )
    stderr = redact(
        _trim_output(result.stderr.decode("utf-8", errors="replace")), secret_list
    )
    exit_code = result.exit_code
    if result.status == "blocked":
        stderr = redact((stderr + "\n" + result.error).strip(), secret_list)
    entry = {
        "at": iso_now(),
        "run_id": run_id,
        "command": redact(command, secret_list),
        "dry_run": dry_run,
        "phase": "finished",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }
    log_path = _action_log(ws, entry)
    if result.status != "passed":
        raise AiTestError(
            "CLEANUP_FAILED",
            f"清理命令未通过：{result.error or f'退出码 {exit_code}'}",
            f"检查 {log_path} 中的输出",
        )
    return {
        "dry_run": dry_run,
        "executed": not dry_run,
        "run_id": run_id,
        "command": redact(command, secret_list),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "log_path": log_path,
    }



