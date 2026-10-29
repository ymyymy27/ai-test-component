"""受控备份入口（架构 04 §6/§9/§10、P1-FR16）。

报告点名的缺口是「backup/migrate 虽有底层组件，工作空间级受控写身份与产品入口
尚未完成」。底层 `FileBackupStore` 已经实现冻结闭包、清单最后写、逐文件摘要核对、
敏感材料拒绝与双向闭包校验；本模块只补**受控入口**与**如实结论**。

合同依据（架构 04）：

- §6/§9：「备份冻结一个已提交历史根及其可达的全部永久记录、历史世代/清单与对象闭包、
  版本/epoch/事件边界和摘要，**清单最后完成，校验后才可恢复**」；「备份位置由用户配置在
  独立于活动目录的存储上」；「不自动轮换删除」；
- §10：「发现应在闭包中的记录/对象缺失，标为**有缺口备份**，保留材料但**不得作为迁移前
  完整备份验收通过**」；
- §10 活动材料：普通已提交状态备份不复制正在变化的 spool/workdir/transactions，
  **记录活动 Attempt、材料位置和未纳入原因，不宣称完整运行备份**；
- §9.3：诊断记录包含备份时间、清单校验结果与缺失内容。

四道边界：

1. **受控写身份**：备份会把工作空间闭包写到工作空间之外，属工作空间级副作用，
   必须有**受控交互会话**（`interactive=True`）才能执行；Agent 中继会话一律拒绝；
2. **不在活动目录内**：目标必须是绝对路径、不得位于活动工作空间内、不得是链接；
3. **不静默降级**：必需永久文件缺失或闭包校验不通过时，如实返回 `complete=false`
   与 `known_missing`／`gaps`，绝不在响应里把它说成可用的完整备份；
4. **不发明状态**：本入口只做**在线备份**（§10「在线备份可覆盖其 root_commit 之前
   全部已提交历史」）；需要维护模式的完整停机/迁移备份要求"停止接纳新写入"，本入口
   没有维护模式状态可依赖，因此明确拒绝该类型而不是假装做到了。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Final, Protocol, cast

from aitest.application.ports import BackupAuditPort, BackupStorePort

BACKUP_KIND: Final = "online"
BACKUP_SCHEMA: Final = "aitest.backup/1.0"
#: 备份清单登记的冻结上下文键（架构 04 §6/§10）。
CLOSURE_KEYS: Final[tuple[str, ...]] = (
    "root_commit",
    "workspace_id",
    "generation",
    "writer_epoch",
    "event_boundary",
)
#: 备份闭包的最低必需永久文件：缺一即"有缺口备份"（不是完整备份）。
REQUIRED_PERMANENT_FILES: Final[tuple[str, ...]] = (
    "workspace.json",
    "records.json",
    "current.json",
    "commit.json",
)
_MAX_KNOWN_MISSING: Final = 64


class BackupBlocked(ValueError):
    """备份被拒绝；沿用已登记错误码集合。"""

    code = "BACKUP_REFUSED"


class _Actors(Protocol):
    def current(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class BackupOutcome:
    destination: str
    kind: str
    root_commit: str
    scheme: str
    verified: bool
    complete: bool
    restorable: bool
    file_count: int
    total_bytes: int
    manifest_digest: str
    excluded_active: tuple[str, ...]
    known_missing: tuple[str, ...]
    gaps: tuple[str, ...]
    #: 备份清单登记的冻结上下文（§6/§10）：工作空间身份、generation、epoch 与事件边界。
    workspace_id: str | None = None
    generation: int | None = None
    writer_epoch: int | None = None
    event_boundary: tuple[tuple[str, object], ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "destination": self.destination,
            "kind": self.kind,
            "schema": BACKUP_SCHEMA,
            # 备份身份即**清单摘要**（内容寻址）：`inspect`／`restore` 按它定位，
            # 不需要新记录类别，也不可能出现"同名不同内容"的歧义。
            "backup_id": self.manifest_digest,
            "root_commit": self.root_commit,
            "scheme": self.scheme,
            "verified": self.verified,
            "complete": self.complete,
            "restorable": self.restorable,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "manifest_digest": self.manifest_digest,
            "workspace_id": self.workspace_id,
            "generation": self.generation,
            "writer_epoch": self.writer_epoch,
            "event_boundary": dict(self.event_boundary),
            "excluded_active": list(self.excluded_active),
            "known_missing": list(self.known_missing),
            "gaps": list(self.gaps),
        }


class SavedBackupEntry:
    """受控备份入口：`create`（默认）／`inspect`／`restore` 三个模式。

    - `create`：受控会话 + 工作空间外绝对目标 + 先建后校验 + 登记 `backup_id`
      （清单摘要即身份）+ 如实结论；
    - `inspect`：读回登记事实并可要求现场复核（只读，任何受控会话可用）；
    - `restore`：从登记的 `backup_id` 恢复到**新的空目标**（受控会话）；
      恢复后立即核实原空间活动、按需隔离双接管，并登记副本内待核实的活动材料；
      索引缺失时才要求由启动恢复流程重建。
    """

    def __init__(
        self,
        *,
        root: Path,
        actors: _Actors,
        store: BackupStorePort,
        maintenance: BackupAuditPort,
    ) -> None:
        if not isinstance(root, Path):
            raise BackupBlocked("backup requires a workspace root")
        if store is None or maintenance is None:
            raise BackupBlocked("backup requires the backup-store and audit ports")
        self.root = root.resolve()
        self.actors = actors
        self.store = store
        self.maintenance = maintenance

    # ------------------------------------------------------------ 入口

    def apply(self, command: Any) -> Mapping[str, object]:
        if getattr(command, "action", None) != "backup":
            raise BackupBlocked("backup entry only serves the backup action")
        parameters = getattr(command, "parameters", None)
        mode = self._mode(parameters)
        if mode == "inspect":
            return self._inspect(parameters)
        if mode == "restore":
            return self._restore(parameters)
        return self._create(parameters)

    def _mode(self, parameters: object) -> str:
        if not isinstance(parameters, Mapping):
            raise BackupBlocked("backup requires explicit parameters")
        mode = parameters.get("mode", "create")
        if mode not in {"create", "inspect", "restore"}:
            raise BackupBlocked(
                f"backup mode must be one of ['create', 'inspect', 'restore'], got {mode!r}"
            )
        return str(mode)

    def _create(self, parameters: object) -> Mapping[str, object]:
        self._controlled_identity()
        self._require_no_maintenance_kind(parameters)
        destination = self._destination(parameters)
        outcome = self._run(destination)
        # §9.3：诊断记录包含备份时间、校验结果与缺失内容（维护审计，不是业务记录）。
        # §6/§10：同时登记冻结上下文（root_commit、workspace/generation/epoch、
        # 事件有效边界），使恢复与去重都能按同一份事实核对。
        self.maintenance.record_backup(
            destination=outcome.destination,
            kind=outcome.kind,
            verified=outcome.verified,
            complete=outcome.complete,
            known_missing=outcome.known_missing,
            file_count=outcome.file_count,
            total_bytes=outcome.total_bytes,
            manifest_digest=outcome.manifest_digest,
            root_commit=outcome.root_commit,
            workspace_id=outcome.workspace_id,
            generation=outcome.generation,
            writer_epoch=outcome.writer_epoch,
            event_boundary=dict(outcome.event_boundary),
        )
        return outcome.payload()

    def _inspect(self, parameters: object) -> Mapping[str, object]:
        """读回备份登记；`verify=true` 时对每条登记现场复核闭包。"""

        assert isinstance(parameters, Mapping)
        unknown = set(parameters) - {"mode", "backup_id", "verify"}
        if unknown:
            raise BackupBlocked(f"backup inspect received unknown parameters: {sorted(unknown)}")
        wanted = parameters.get("backup_id")
        if wanted is not None and (not isinstance(wanted, str) or not wanted.strip()):
            raise BackupBlocked("backup inspect backup_id must be non-empty text")
        live = parameters.get("verify", False)
        if type(live) is not bool:
            raise BackupBlocked("backup inspect verify must be a boolean")
        records: list[dict[str, object]] = []
        for fact in self.maintenance.backup_facts():
            destination = fact.get("destination")
            if not isinstance(destination, str) or not destination.strip():
                continue
            entry: dict[str, object] = {
                "backup_id": str(fact.get("manifest_digest") or ""),
                "destination": destination,
                "kind": str(fact.get("kind") or ""),
                "verified_at_creation": fact.get("verified") is True,
                "complete": fact.get("complete") is True,
                "file_count": fact.get("file_count"),
                "total_bytes": fact.get("total_bytes"),
                "known_missing": [
                    str(item)
                    for item in cast(list[object], fact.get("known_missing") or [])
                ],
                "exists": Path(destination).is_dir(),
            }
            if wanted is not None and entry["backup_id"] != wanted:
                continue
            if live and entry["exists"] is True:
                try:
                    verification = self.store.verify(Path(destination))
                except (OSError, ValueError, RuntimeError) as error:
                    entry["verified_now"] = False
                    entry["verification_error"] = str(error)
                else:
                    entry["verified_now"] = bool(verification.get("ok"))
                    entry["verification_errors"] = [
                        str(item)
                        for item in cast(list[object], verification.get("errors", ()))
                    ]
            records.append(entry)
        if wanted is not None and not records:
            raise BackupBlocked(f"no registered backup matches backup_id {wanted!r}")
        return {"mode": "inspect", "backups": records, "count": len(records)}

    def _restore(self, parameters: object) -> Mapping[str, object]:
        """按登记的 `backup_id` 恢复到新的空目标；不覆盖、不删除任何材料。"""

        self._controlled_identity()
        assert isinstance(parameters, Mapping)
        unknown = set(parameters) - {"mode", "backup_id", "target"}
        if unknown:
            raise BackupBlocked(f"backup restore received unknown parameters: {sorted(unknown)}")
        backup_id = parameters.get("backup_id")
        if not isinstance(backup_id, str) or not backup_id.strip():
            raise BackupBlocked("backup restore requires an explicit backup_id")
        target = self._destination({"destination": parameters.get("target")})
        source = self._registered_backup(backup_id)
        try:
            if not self.store.verify(source)["ok"]:
                raise BackupBlocked("registered backup no longer verifies; refusing to restore")
            report = self.store.restore(backup=source, target=target)
        except BackupBlocked:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            raise BackupBlocked(str(error)) from error
        payload = _plain(report)
        if payload.get("state") == "rejected":
            raise BackupBlocked("restore target or manifest paths are out of bounds")
        source_activity = payload.get("source_activity")
        isolated = payload.get("isolated") is True
        pending = list(cast(list[object], payload.get("pending_verification") or ()))
        index_rebuild_required = payload.get("index_rebuild_required") is True
        next_steps: list[str] = []
        if index_rebuild_required:
            next_steps.append(
                "open the restored copy with a core so startup recovery rebuilds the "
                "missing index before any business read"
            )
        gaps: list[str] = []
        if index_rebuild_required:
            gaps.append("index_rebuild_pending")
        return {
            "mode": "restore",
            "backup_id": backup_id,
            "source": str(source),
            "target": str(target),
            "state": payload.get("state"),
            "files_restored": payload.get("files_restored"),
            "bytes_restored": payload.get("bytes_restored"),
            "verified": payload.get("verified") is True,
            # §9/§10：活动核实与双接管隔离已在本动作内完成，如实登记结果。
            "source_activity": source_activity if isinstance(source_activity, str) else None,
            "isolated_read_only": isolated,
            "pending_verification": [str(item) for item in pending],
            "index_rebuild_required": index_rebuild_required,
            "next_steps": next_steps,
            "gaps": gaps,
        }

    def _registered_backup(self, backup_id: str) -> Path:
        for fact in self.maintenance.backup_facts():
            if str(fact.get("manifest_digest") or "") != backup_id:
                continue
            destination = fact.get("destination")
            if not isinstance(destination, str) or not destination.strip():
                break
            path = Path(destination)
            if not path.is_dir():
                raise BackupBlocked("registered backup location is unavailable")
            return path
        raise BackupBlocked(f"no registered backup matches backup_id {backup_id!r}")

    # ------------------------------------------------------------ 守卫

    def _controlled_identity(self) -> None:
        actor = self.actors.current()
        if actor is None or not getattr(actor, "interactive", False):
            raise BackupBlocked(
                "backup writes outside the workspace and requires a controlled "
                "interactive session"
            )

    @staticmethod
    def _require_no_maintenance_kind(parameters: object) -> None:
        if not isinstance(parameters, Mapping):
            raise BackupBlocked("backup requires explicit parameters")
        unknown = set(parameters) - {"mode", "destination", "kind"}
        if unknown:
            raise BackupBlocked(f"backup received unknown parameters: {sorted(unknown)}")
        kind = parameters.get("kind", BACKUP_KIND)
        if kind != BACKUP_KIND:
            raise BackupBlocked(
                "maintenance-mode (full stop) backup requires a maintenance mode that "
                "stops new writes; only the online backup is available here"
            )

    def _destination(self, parameters: object) -> Path:
        if not isinstance(parameters, Mapping):
            raise BackupBlocked("backup requires explicit parameters")
        raw = parameters.get("destination")
        if not isinstance(raw, str) or not raw.strip():
            raise BackupBlocked("backup requires an explicit destination path")
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise BackupBlocked("backup destination must be an absolute path")
        if candidate.exists() and candidate.is_symlink():
            raise BackupBlocked("backup destination must not be a link")
        resolved = candidate.resolve()
        if resolved == self.root or resolved.is_relative_to(self.root):
            raise BackupBlocked("backup destination must be outside the active workspace")
        parent = resolved.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if parent.is_symlink():
            raise BackupBlocked("backup destination path contains a link")
        return resolved

    # ------------------------------------------------------------ 执行

    def _run(self, destination: Path) -> BackupOutcome:
        known_missing = self._known_missing()
        try:
            created = self.store.create(destination)
        except (OSError, ValueError, RuntimeError) as error:
            # 端口以 RuntimeError 派生类型报告拒绝原因（含"目标非空/含链接/敏感材料"）。
            raise BackupBlocked(str(error)) from error
        try:
            verification = self.store.verify(created)
        except (OSError, ValueError, RuntimeError) as error:
            raise BackupBlocked(str(error)) from error
        verified = bool(verification.get("ok"))
        errors = tuple(str(item) for item in cast(list[object], verification.get("errors", ())))
        manifest = self._manifest(created)
        file_count = len(manifest)
        total_bytes = self._total_bytes(created, manifest)
        excluded_active = self._excluded_active(created)
        root_commit = self._root_commit()
        closure = self._closure(created)
        workspace_id = closure.get("workspace_id")
        generation = closure.get("generation")
        writer_epoch = closure.get("writer_epoch")
        boundary = closure.get("event_boundary")
        event_boundary = (
            tuple(sorted((str(name), value) for name, value in boundary.items()))
            if isinstance(boundary, dict)
            else ()
        )
        gaps: list[str] = []
        if errors:
            gaps.append("closure_verification_failed")
        if known_missing:
            gaps.append("required_permanent_material_missing")
        if excluded_active:
            gaps.append("active_material_excluded")
        complete = verified and not known_missing and not errors
        return BackupOutcome(
            destination=str(created),
            kind=BACKUP_KIND,
            root_commit=root_commit,
            scheme=BACKUP_SCHEMA,
            verified=verified,
            complete=complete,
            # "校验后才可恢复"：闭包不通过一律不得标可恢复。
            restorable=verified and not errors,
            file_count=file_count,
            total_bytes=total_bytes,
            manifest_digest=_manifest_digest(manifest),
            excluded_active=excluded_active,
            known_missing=known_missing,
            gaps=tuple(gaps),
            workspace_id=workspace_id if isinstance(workspace_id, str) else None,
            generation=generation if isinstance(generation, int) else None,
            writer_epoch=writer_epoch if isinstance(writer_epoch, int) else None,
            event_boundary=event_boundary,
        )

    def _known_missing(self) -> tuple[str, ...]:
        missing = [
            name for name in REQUIRED_PERMANENT_FILES if not (self.root / name).is_file()
        ]
        return tuple(sorted(missing))[:_MAX_KNOWN_MISSING]

    @staticmethod
    def _closure(destination: Path) -> dict[str, object]:
        """读回备份清单登记的冻结上下文；缺失/损坏如实返回空对象。"""

        try:
            payload = json.loads((destination / "backup.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            "root_commit": payload.get("root_commit"),
            "workspace_id": payload.get("workspace_id"),
            "generation": payload.get("generation"),
            "writer_epoch": payload.get("writer_epoch"),
            "event_boundary": payload.get("event_boundary"),
        }

    @staticmethod
    def _manifest(destination: Path) -> dict[str, str]:
        try:
            payload = json.loads((destination / "backup.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BackupBlocked(f"backup manifest cannot be read: {error}") from error
        files = payload.get("files")
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != BACKUP_SCHEMA
            or not isinstance(files, dict)
            or any(
                not isinstance(name, str) or not isinstance(digest, str)
                for name, digest in files.items()
            )
        ):
            raise BackupBlocked("backup manifest shape cannot be verified")
        return dict(files)

    @staticmethod
    def _excluded_active(destination: Path) -> tuple[str, ...]:
        try:
            payload = json.loads((destination / "backup.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ()
        excluded = payload.get("excluded_active")
        if not isinstance(excluded, list):
            return ()
        return tuple(str(item) for item in excluded)

    @staticmethod
    def _total_bytes(destination: Path, manifest: Mapping[str, str]) -> int:
        total = 0
        for name in manifest:
            path = destination / name
            try:
                total += os.path.getsize(path)
            except OSError as error:
                raise BackupBlocked(f"backup material is unreadable: {name}") from error
        return total

    def _root_commit(self) -> str:
        """历史根：取已提交根/提交记录的标识；读不到就明确"未知"，不编造。"""

        for name in ("current.json", "commit.json"):
            payload = self._read_json(self.root / name)
            if not isinstance(payload, dict):
                continue
            for key in ("commit_id", "root_commit", "current_commit_id"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return "unknown"

    @staticmethod
    def _read_json(path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None


def _plain(value: object) -> dict[str, object]:
    """把 `RestoreReport` 等结果 dataclass 变成可序列化映射。"""

    if is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise BackupBlocked("backup result shape cannot be serialized")
    return {key: _jsonable(item) for key, item in payload.items()}


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _manifest_digest(manifest: Mapping[str, str]) -> str:
    """清单摘要：按规范字节对逐文件摘要取摘要（清单自身最后写入）。"""

    canonical = json.dumps(
        dict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


__all__ = [
    "BACKUP_KIND",
    "BACKUP_SCHEMA",
    "REQUIRED_PERMANENT_FILES",
    "BackupBlocked",
    "BackupOutcome",
    "SavedBackupEntry",
]
