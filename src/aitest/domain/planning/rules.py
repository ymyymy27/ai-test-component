"""规则草稿与不可变发布版本；发布编排属应用用例。

架构文档《01-项目与计划》第 3 节：`RuleDraft` 发布 `RuleVersion` 不可变。
需求 P1-FR05：正文说明、结构化规则与发布状态**分开保存**；导入只创建草稿，
不执行其中命令；往返保留编号、来源与未识别扩展字段，**未知执行字段不得静默启用**。

本模块只使用标准库，零 I/O。发布这一**动作**（`publish_rules`）属人工动作，
由应用用例在人工确认后执行；本模块只提供领域对象与守卫，**不提供自动发布路径**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RuleEnablement(StrEnum):
    """规则启用状态。草稿未确认不得启用。"""

    ENABLED = "enabled"
    DISABLED = "disabled"


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_items(values: tuple[str, ...], name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must not contain empty values")


def _require_unique(values: tuple[str, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


@dataclass(frozen=True, slots=True)
class RuleDraft:
    """规则草稿。不提供任何发布方法：发布是独立的人工动作。"""

    rule_id: str
    revision: int
    scope: str
    text: str
    source: str
    steps: tuple[str, ...] = field(default_factory=tuple)
    evidence_requirements: tuple[str, ...] = field(default_factory=tuple)
    enablement: RuleEnablement = RuleEnablement.DISABLED
    confirmed: bool = False
    unknown_extension_fields: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "rule_id")
        _require_text(self.scope, "scope")
        _require_text(self.text, "text")
        _require_text(self.source, "source")
        if self.revision < 1:
            raise ValueError("rule revision must be >= 1")
        _require_items(self.steps, "steps")
        _require_items(self.evidence_requirements, "evidence_requirements")
        _require_items(self.unknown_extension_fields, "unknown_extension_fields")
        _require_unique(self.unknown_extension_fields, "unknown_extension_fields")
        # 未确认发布的草稿不得生效。
        if self.enablement is RuleEnablement.ENABLED and not self.confirmed:
            raise ValueError("an unconfirmed rule draft must not be enabled")
        # 未识别的执行字段不得与已识别步骤混在一起。
        overlap = set(self.unknown_extension_fields) & set(self.steps)
        if overlap:
            raise ValueError(
                f"unknown extension fields must not be executable steps: {sorted(overlap)}"
            )

    def executable_steps(self) -> tuple[str, ...]:
        """只返回已识别的步骤。

        `unknown_extension_fields` 仅用于往返保留来源与未识别字段，
        **不得被执行**（需求 P1-FR05）。本方法存在的意义是让"未知字段不得静默启用"
        有一个可断言的落点。
        """
        return self.steps


@dataclass(frozen=True, slots=True)
class RuleVersion:
    """已发布的不可变规则版本。

    不含任何修改路径；新测试绑定新修订，历史运行仍引用原版。
    """

    rule_id: str
    revision: int
    scope: str
    text: str
    steps: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    source: str
    confirmation_id: str
    digest: str
    # 正文 revision 可来自导入；仓储修订由提交分配，冻结引用使用后者。
    record_revision: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "rule_id")
        _require_text(self.scope, "scope")
        _require_text(self.text, "text")
        _require_text(self.source, "source")
        _require_text(self.confirmation_id, "confirmation_id")
        _require_text(self.digest, "digest")
        if self.revision < 1:
            raise ValueError("rule revision must be >= 1")
        if self.record_revision is not None and self.record_revision < 1:
            raise ValueError("rule record revision must be >= 1")
        _require_items(self.steps, "steps")
        _require_items(self.evidence_requirements, "evidence_requirements")


@dataclass(frozen=True, slots=True)
class RuleRevisionRef:
    """按**准确修订**引用规则；不使用"最新"语义。

    与 `contracts/prepared_run.py` 的 `RuleVersionRef` 形状一致
    （`rule_id` / `revision` / `digest`），由
    `tests/contracts/test_project_vocabulary.py` 锁定两者字段名一致。
    """

    rule_id: str
    revision: int
    digest: str

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "rule_id")
        _require_text(self.digest, "digest")
        if self.revision < 1:
            raise ValueError("rule revision must be >= 1")

    @classmethod
    def of(cls, version: RuleVersion) -> RuleRevisionRef:
        return cls(
            rule_id=version.rule_id,
            revision=version.record_revision or version.revision,
            digest=version.digest,
        )


def validate_draft_publication(draft: RuleDraft, confirmation_id: str) -> None:
    """发布门禁：草稿必须已确认，且发布必须绑定一条确认记录。

    不满足则抛 `ValueError`。**本函数只校验，不落盘、不生成版本对象**：
    构造 `RuleVersion` 由应用用例在人工确认后完成。
    """
    if not draft.confirmed:
        raise ValueError("an unconfirmed rule draft must not be published")
    _require_text(confirmation_id, "confirmation_id")
