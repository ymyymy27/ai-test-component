"""生成并核对摘要或证据包，不自行登记成功。

架构 03 §5／§8 的硬要求（本文件是它们的实现）：

- 导出器**只写事务临时位置**，完整校验后才发布；未登记成功不得对用户报成功；
- 包内路径必须是**规范相对路径**：拒绝绝对路径、盘符、路径穿越、重复覆盖与符号链接引用；
- 摘要按**实际导出字节**计算，与本地原始对象摘要区分；
- 失败不暴露为成功包；跨目录写入不假定与工作空间同一原子事务，
  因此"已发布文件"与"已登记导出记录"是两个分别核对的事实。

登记（`report_export` 记录）由应用用例经工作单元完成，本适配器不写业务记录。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from aitest.contracts.responses import Response

#: 允许的包内相对路径：只用 `/` 分隔、无空段、无 `.`/`..`、无盘符、无控制字符。
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_TEMP_ROOT = "tmp"
_ARTIFACT_ROOT = "artifacts/reports"
_SUFFIXES = {"markdown_summary": ".md", "evidence_bundle": ".zip"}


class ArtifactError(ValueError):
    """导出制品错误；`code` 复用已登记错误码，不新造协议词汇。"""

    code = "INVALID_REQUEST"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(message)


def _require_segment(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_SEGMENT.match(value):
        raise ArtifactError(f"{name} is not a safe canonical path segment")
    if value in {".", ".."}:
        raise ArtifactError(f"{name} must not be a relative path traversal")
    return value


def safe_artifact_name(*, report_id: str, kind: str, digest: str) -> str:
    """规范相对文件名：`<report_id>/<kind>-<digest前缀><后缀>`（全部为安全段）。"""

    if kind not in _SUFFIXES:
        raise ArtifactError(f"unknown artifact kind: {kind}")
    report = _require_segment(report_id, "report_id")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ArtifactError("artifact digest must be a sha256 hex digest")
    return f"{report}/{kind}-{digest[7:23]}{_SUFFIXES[kind]}"


class FileReportArtifactPort:
    """`ReportArtifactPort` 的文件实现：临时写入 → 摘要核对 → 原子发布。"""

    def __init__(self, root: Path, *, instance_id: str = "local-core") -> None:
        self._root = Path(root)
        self._instance_id = instance_id

    # ------------------------------------------------------------ 端口方法

    def write_artifact(
        self,
        *,
        request_id: str,
        workspace_id: str,
        report_id: str,
        kind: str,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> Response:
        if type(content) is not bytes or not content:
            raise ArtifactError("artifact content must be nonempty bytes")
        _require_segment(request_id, "request_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ArtifactError("workspace_id must not be empty")
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        relative = safe_artifact_name(report_id=report_id, kind=kind, digest=digest)

        temporary = self._root / _TEMP_ROOT / "report-artifacts" / request_id
        staged = temporary / relative
        published = self._root / _ARTIFACT_ROOT / relative
        if staged == published or self._escapes(self._root, staged) or self._escapes(
            self._root, published
        ):
            raise ArtifactError("artifact target escapes the workspace")
        # 已发布且摘要一致 ⇒ 幂等复用同一产物（崩溃恢复的"幂等登记"路径）；
        # 摘要不一致说明同名产物被篡改或路径冲突，必须拒绝而不是覆盖。
        if published.exists() or published.is_symlink():
            if published.is_symlink() or not published.is_file():
                raise ArtifactError("artifact target is not a plain file")
            existing = published.read_bytes()
            if "sha256:" + hashlib.sha256(existing).hexdigest() != digest:
                raise ArtifactError(
                    "published artifact with the same name differs",
                    code="INTEGRITY_CHECK_FAILED",
                )
            return self._published_response(
                request_id=request_id,
                workspace_id=workspace_id,
                report_id=report_id,
                kind=kind,
                relative=relative,
                digest=digest,
                size=len(content),
                media_type=media_type,
                status="reused",
            )
        for parent in (staged.parent, published.parent):
            self._ensure_directory(parent)

        staged.write_bytes(content)
        written = staged.read_bytes()
        if written != content or hashlib.sha256(written).hexdigest() != digest[7:]:
            raise ArtifactError(
                "staged artifact bytes cannot be verified",
                code="INTEGRITY_CHECK_FAILED",
            )
        if published.parent.exists() and published.parent.is_symlink():
            raise ArtifactError("artifact directory must not be a symbolic link")
        os.replace(staged, published)
        shutil.rmtree(temporary, ignore_errors=True)

        return self._published_response(
            request_id=request_id,
            workspace_id=workspace_id,
            report_id=report_id,
            kind=kind,
            relative=relative,
            digest=digest,
            size=len(content),
            media_type=media_type,
            status="published",
        )

    def _published_response(
        self,
        *,
        request_id: str,
        workspace_id: str,
        report_id: str,
        kind: str,
        relative: str,
        digest: str,
        size: int,
        media_type: str,
        status: str,
    ) -> Response:
        return Response(
            request_id=request_id,
            instance_id=self._instance_id,
            workspace_id=workspace_id,
            result=cast(
                dict[str, JsonValue],
                {
                    "status": status,
                    "report_id": report_id,
                    "kind": kind,
                    "artifact_ref": f"{_ARTIFACT_ROOT}/{relative}",
                    "artifact_digest": digest,
                    "size_bytes": size,
                    "media_type": media_type,
                },
            ),
        )

    def verify_artifact(
        self,
        *,
        request_id: str,
        workspace_id: str,
        report_id: str,
        expected_sha256: str,
    ) -> Response:
        _require_segment(request_id, "request_id")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256 or ""):
            raise ArtifactError("expected_sha256 must be a sha256 hex digest")
        matches: list[str] = []
        directory = self._root / _ARTIFACT_ROOT / _require_segment(report_id, "report_id")
        if directory.is_dir() and not directory.is_symlink():
            for candidate in sorted(directory.iterdir()):
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                content = candidate.read_bytes()
                if "sha256:" + hashlib.sha256(content).hexdigest() == expected_sha256:
                    matches.append(
                        f"{_ARTIFACT_ROOT}/{report_id}/{candidate.name}"
                    )
        status = "verified" if len(matches) == 1 else (
            "missing" if not matches else "ambiguous"
        )
        return Response(
            request_id=request_id,
            instance_id=self._instance_id,
            workspace_id=workspace_id,
            result=cast(
                dict[str, JsonValue],
                {
                    "status": status,
                    "report_id": report_id,
                    "artifact_ref": matches[0] if len(matches) == 1 else None,
                    "artifact_digest": expected_sha256 if matches else None,
                    "matches": list(matches),
                },
            ),
        )

    def artifact_path(self, artifact_ref: str) -> Path:
        """把已登记的相对引用解析为绝对路径；越界或非规范引用一律拒绝。"""

        if not isinstance(artifact_ref, str) or not artifact_ref.startswith(
            f"{_ARTIFACT_ROOT}/"
        ):
            raise ArtifactError("artifact_ref must be published under artifacts/reports")
        relative = artifact_ref[len(f"{_ARTIFACT_ROOT}/") :]
        parts = relative.split("/")
        if len(parts) < 2 or any(not _SAFE_SEGMENT.match(part) for part in parts):
            raise ArtifactError("artifact_ref is not a canonical relative path")
        path = self._root / _ARTIFACT_ROOT / relative
        if self._escapes(self._root, path):
            raise ArtifactError("artifact_ref escapes the workspace")
        if path.is_symlink() or not path.is_file():
            raise ArtifactError("published artifact is missing or is a symbolic link")
        return path

    def read_artifact(self, artifact_ref: str, *, expected_sha256: str) -> bytes:
        path = self.artifact_path(artifact_ref)
        content = path.read_bytes()
        if "sha256:" + hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ArtifactError("published artifact digest differs", code="INTEGRITY_CHECK_FAILED")
        return content

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _escapes(root: Path, candidate: Path) -> bool:
        try:
            resolved_root = root.resolve()
        except OSError as error:  # pragma: no cover - 本机异常
            raise ArtifactError(f"workspace root cannot be resolved: {error}") from error
        # 逐段解析已存在的父目录，拒绝符号链接穿越。
        for parent in [candidate, *candidate.parents]:
            if parent == candidate and not parent.exists():
                continue
            if parent.is_symlink():
                return True
            if parent == resolved_root:
                break
        target = candidate if candidate.exists() else candidate.parent
        try:
            resolved = target.resolve()
        except OSError as error:  # pragma: no cover - 本机异常
            raise ArtifactError(f"artifact target cannot be resolved: {error}") from error
        return resolved_root != resolved and resolved_root not in resolved.parents

    @staticmethod
    def _ensure_directory(directory: Path) -> None:
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise ArtifactError("artifact directory is not a plain directory")
        directory.mkdir(parents=True, exist_ok=True)


__all__ = ["ArtifactError", "FileReportArtifactPort", "safe_artifact_name"]
