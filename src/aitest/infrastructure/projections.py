"""冻结策略的安全字节投影、真实摘要及排除原因。

投影器对**显式选定**的材料逐项处理：

- ``source_snippet`` 类别受全局源码片段开关约束，关闭时整类排除（不允许
  通过其他字段夹带未授权源码）；
- 命中已知凭据标记的整行丢弃；处理后为空则整项排除，绝不伪造完整；
- 含非法控制字符（疑似二进制/非文本）或超过单项上限的材料排除；
- 每项及整批摘要均为真实字节 SHA256，不使用长度占位。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from aitest.application.ports import (
    ProjectedMaterial,
    Projection,
    ProjectionStatus,
)
from aitest.domain.planning.model_outbound import MaterialKind

#: 投影策略版本；策略变化必须递增。
POLICY_REVISION = 1
#: 单项投影字节上限，防止误投超大材料（超出即排除并显示缺口）。
ITEM_LIMIT_BYTES = 256 * 1024

_CREDENTIAL_MARKERS = (
    "api_key=",
    "apikey=",
    "authorization:",
    "bearer ",
    "password=",
    "secret=",
    "token=",
)

#: 允许出现在投影文本中的控制字符：制表、换行、回车。
_ALLOWED_CONTROLS = frozenset({"\t", "\n", "\r"})


def _looks_like_credential(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _CREDENTIAL_MARKERS)


def _is_printable_text(text: str) -> bool:
    return all(ord(char) >= 32 or char in _ALLOWED_CONTROLS for char in text)


def _safe_text(text: str) -> str:
    """丢弃命中凭据标记的整行，保留其余行。"""
    return "\n".join(
        line
        for line in text.splitlines()
        if line.strip() and not _looks_like_credential(line)
    )


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class SafeMaterialProjector:
    """ProjectionPort 的生产实现：逐项安全检查 + 真实字节摘要。"""

    def project(
        self,
        *,
        material: Mapping[MaterialKind, str],
        source_snippets_enabled: bool,
    ) -> Projection:
        projected: list[ProjectedMaterial] = []
        excluded: list[tuple[MaterialKind, str]] = []

        for kind, text in material.items():
            field_path = f"material.{kind.value}"
            if kind is MaterialKind.SOURCE_SNIPPET and not source_snippets_enabled:
                excluded.append((kind, field_path))
                continue
            if len(text.encode("utf-8")) > ITEM_LIMIT_BYTES or not _is_printable_text(text):
                excluded.append((kind, field_path))
                continue
            safe = _safe_text(text)
            if not safe.strip():
                excluded.append((kind, field_path))
                continue
            projected.append(
                ProjectedMaterial(
                    material_kind=kind,
                    field_path=field_path,
                    projected_text=safe,
                    digest=sha256_text(safe),
                )
            )

        if not projected:
            return Projection(
                status=ProjectionStatus.PARTIAL,
                excluded=tuple(excluded)
                or ((MaterialKind.PROJECT_CONTEXT, "material.project_context"),),
            )
        return Projection(
            status=ProjectionStatus.PARTIAL if excluded else ProjectionStatus.COMPLETE,
            projected=tuple(projected),
            excluded=tuple(excluded),
            projection_digest=self._overall_digest(projected),
            policy_revision=POLICY_REVISION,
        )

    @staticmethod
    def _overall_digest(projected: list[ProjectedMaterial]) -> str:
        canonical = [
            {
                "kind": item.material_kind.value,
                "path": item.field_path,
                "digest": item.digest,
            }
            for item in sorted(projected, key=lambda item: item.field_path)
        ]
        encoded = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ITEM_LIMIT_BYTES",
    "POLICY_REVISION",
    "SafeMaterialProjector",
    "sha256_text",
]
