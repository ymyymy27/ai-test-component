"""存量运行之间的整用例复用引用记录（合同形状 + 严格校验）。

一期架构01《复用、回归与模型任务矩阵》（`docs/项目文档/一期/架构文档/01-项目与计划.md`）规定：

    ReuseReference 登记 target_run/case_id、source_run/step/attempt、来源计划与
    源码/环境/入口参数/规则/适配器修订、证据引用、validity_checked_at_commit
    及验证依据。它是**已有运行记录的引用关系**，不创建虚假 Attempt，不增加
    执行器或独立服务。

本模块只定义记录形状、规范字节与严格校验，**不授予复用资格**：R/V 仍由纯领域
在读取整套当前材料后派生（`domain/execution/reuse.py`、`domain/execution/cases.py`），
引用记录本身既不能产生 Attempt，也不能把未核实的条件变成通过。

四类修订摘要（计划/环境/入口参数/规则）与适配器摘要允许为 ``None``：该来源修订
**尚未观测或无法核实**时按未知显式登记，绝不补造摘要；记录里出现摘要只说明该项
被登记，不说明资格已通过。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 记录模式版本；形状变化必须递增并同步接口台账。
SCHEMA_VERSION: Literal["aitest.reuse-reference/1.0"] = "aitest.reuse-reference/1.0"

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_COMMIT_SEQUENCE = re.compile(r"[0-9]+")


class ReuseReference(BaseModel):
    """一条已保存运行到另一条运行/用例的复用引用（不可变、无资格语义）。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["aitest.reuse-reference/1.0"]
    target_run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    source_step_id: str = Field(min_length=1)
    source_attempt_id: str = Field(min_length=1)
    #: 整用例来源映射（Step→Attempt）：记录被引用用例每个必需步骤的实际来源 Attempt，
    #: 与 `WholeCaseReuseBasis.source_attempt_by_step` 同一形状；上面的 source_step_id/
    #: source_attempt_id 是其中的锚点，必须命中该映射。
    source_attempt_by_step: tuple[tuple[str, str], ...] = Field(min_length=1)
    #: 来源计划修订摘要（冻结计划正文的规范字节摘要）。
    #: ``None`` == 该来源修订**尚未核实**：登记为未知，绝不补造摘要。
    source_plan_revision_digest: str | None = None
    #: 来源源码内容身份（快照 content_identity，不是仓储修订号）；始终可得。
    source_content_identity: str = Field(min_length=1)
    #: 来源环境修订摘要（冻结环境记录的规范字节摘要）；未知为 ``None``。
    environment_revision_digest: str | None = None
    #: 入口参数摘要（已登记入口与参数、输入解析结果）；未知为 ``None``。
    entry_input_digest: str | None = None
    #: 规则与模板修订摘要；未知为 ``None``。
    rules_revision_digest: str | None = None
    #: 适配器版本摘要；没有适配器版本时显式为空。
    adapter_digest: str | None = None
    #: 引用到的证据身份（不可为空、不可重复）。
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    #: 资格核对发生在哪个已提交序号（正整数规范十进制）。
    validity_checked_at_commit: str
    #: 验证依据说明（不可为空、不可重复）；不放置凭据或正文。
    verification_basis: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        for name in (
            "target_run_id",
            "case_id",
            "source_run_id",
            "source_step_id",
            "source_attempt_id",
            "source_content_identity",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty text")
        for name in (
            "source_plan_revision_digest",
            "environment_revision_digest",
            "entry_input_digest",
            "rules_revision_digest",
        ):
            value = getattr(self, name)
            if value is not None and not _DIGEST.fullmatch(value):
                raise ValueError(f"{name} must be an exact sha256 digest or unknown")
        if self.adapter_digest is not None and not _DIGEST.fullmatch(self.adapter_digest):
            raise ValueError("adapter_digest must be an exact sha256 digest")
        commit = self.validity_checked_at_commit
        if (
            not _COMMIT_SEQUENCE.fullmatch(commit)
            or commit != str(int(commit))
            or int(commit) < 1
        ):
            raise ValueError(
                "validity_checked_at_commit must be a canonical positive commit sequence"
            )
        _require_unique_text(self.evidence_refs, "evidence references")
        _require_unique_text(self.verification_basis, "verification basis entries")
        _validate_step_map(self.source_attempt_by_step)
        if (self.source_step_id, self.source_attempt_id) not in self.source_attempt_by_step:
            raise ValueError("source anchor must be one exact entry of the step mapping")
        return self


def _validate_step_map(mapping: tuple[tuple[str, str], ...]) -> None:
    seen_steps: set[str] = set()
    seen_attempts: set[str] = set()
    for pair in mapping:
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError("source step mapping requires exact pairs")
        step_id, attempt_id = pair
        if (
            not isinstance(step_id, str)
            or not step_id.strip()
            or not isinstance(attempt_id, str)
            or not attempt_id.strip()
        ):
            raise ValueError("source step mapping requires nonempty text identities")
        if step_id in seen_steps or attempt_id in seen_attempts:
            raise ValueError("source Step and Attempt identities must be unique")
        seen_steps.add(step_id)
        seen_attempts.add(attempt_id)


def _require_unique_text(values: tuple[str, ...], name: str) -> None:
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{name} must be nonempty text")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must be unique")


def reuse_reference_payload(reference: ReuseReference) -> bytes:
    """规范 JSON 字节：键排序、无 NaN、UTF-8；用于落盘与摘要。"""
    return json.dumps(
        reference.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def reuse_reference_digest(reference: ReuseReference) -> str:
    return "sha256:" + hashlib.sha256(reuse_reference_payload(reference)).hexdigest()


def parse_reuse_reference(payload: Mapping[str, object]) -> ReuseReference:
    """从已保存载荷严格重建记录；多余字段、类型转换或非规范值一律拒绝。"""
    if not isinstance(payload, Mapping):
        raise ValueError("reuse reference payload must be a mapping")
    return ReuseReference.model_validate_json(
        json.dumps(dict(payload), ensure_ascii=False, allow_nan=False),
        strict=True,
    )


__all__ = [
    "SCHEMA_VERSION",
    "ReuseReference",
    "parse_reuse_reference",
    "reuse_reference_digest",
    "reuse_reference_payload",
]
