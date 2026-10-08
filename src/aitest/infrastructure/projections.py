"""冻结策略的安全字节投影、真实摘要及排除原因。

投影器对**显式选定**的材料逐项处理：

- ``source_snippet`` 类别受全局源码片段开关约束，关闭时整类排除（不允许
  通过其他字段夹带未授权源码）；
- 疑似结构化材料（``{``/``[`` 开头）先经严格 JSON 解码，再在**解码后的键与
  值**上过滤已知凭据；转义（``\\uXXXX``）不能隐藏已登记凭据。无法严格解析
  （重复键、NaN/溢出数值、孤立代理字符、破损结构、深度耗尽）或过滤造成键名
  碰撞时整项排除并登记缺口；
- 纯文本命中已知凭据标记的整行丢弃；处理后为空则整项排除，绝不伪造完整；
- 含非法控制字符（疑似二进制/非文本）或超过单项上限的材料排除；上限按
  **过滤与重序列化之后的最终 UTF-8 字节**复核，替换膨胀不能突破预算；
- 每项及整批摘要均为真实字节 SHA256，不使用长度占位。未命中的合法结构化
  材料保留原字节与摘要，不因解码重排而产生新投影。
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
from aitest.contracts.redaction import (
    redact_structure,
    scrub_secret_text,
)
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.infrastructure.security import (
    KnownSecretRegistry,
    UnsafeMaterialError,
    guard_json_text,
    scrub_text,
)

#: 投影策略版本；策略变化必须递增。
#: 4：严格解码 + 解码后键值过滤（覆盖 JSON 转义编码变体），
#: 无法严格解析/键名碰撞登记缺口，并按过滤后最终字节复核单项上限。
POLICY_REVISION = 4
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


def _utf8_size(text: str) -> int | None:
    """最终 UTF-8 字节数；无法编码（如孤立代理字符）返回 ``None``。"""
    try:
        return len(text.encode("utf-8"))
    except UnicodeError:
        return None


def _gap(excluded: list[tuple[MaterialKind, str]], kind: MaterialKind, path: str) -> None:
    """登记材料缺口一次，重复原因不产生重复条目。"""
    if (kind, path) not in excluded:
        excluded.append((kind, path))


def _safe_text(text: str) -> str:
    """丢弃命中凭据标记的整行，保留其余行。"""
    return "\n".join(
        line for line in text.splitlines() if line.strip() and not _looks_like_credential(line)
    )


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class SafeMaterialProjector:
    """ProjectionPort 的生产实现：逐项安全检查 + 真实字节摘要。"""

    def __init__(self, *, registry: KnownSecretRegistry | None = None) -> None:
        self._registry = registry

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
                _gap(excluded, kind, field_path)
                continue
            size = _utf8_size(text)
            if size is None or size > ITEM_LIMIT_BYTES or not _is_printable_text(text):
                _gap(excluded, kind, field_path)
                continue
            try:
                structured = guard_json_text(text, self._registry)
            except UnsafeMaterialError:
                # 疑似结构化但无法严格解析/安全过滤：整项登记缺口，不投影原文。
                _gap(excluded, kind, field_path)
                continue
            if structured is not None:
                # 嵌套结构：保留安全投影，但命中即登记缺口，
                # 不允许以 complete 名义夹带被排除材料。
                safe, changed = structured
                if changed:
                    _gap(excluded, kind, field_path)
            else:
                if any(_looks_like_credential(line) for line in text.splitlines()):
                    _gap(excluded, kind, field_path)
                safe, changed = scrub_text(_safe_text(text), self._registry)
                if changed:
                    _gap(excluded, kind, field_path)
            final_size = _utf8_size(safe)
            if final_size is None or final_size > ITEM_LIMIT_BYTES:
                # 替换可能膨胀：最终字节超预算时排除，不投影超限材料。
                _gap(excluded, kind, field_path)
                continue
            if not safe.strip():
                _gap(excluded, kind, field_path)
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
    "redact_structure",
    "scrub_secret_text",
    "sha256_text",
]
