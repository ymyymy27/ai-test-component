"""草稿生成：应用模板产出**草稿**，并做**定向失效**。

对应需求 P1-FR04（内置模板与检查内容生成）与架构文档第 3、9 节。
依赖方向：`application → domain`、`application → contracts`。

三条硬约束（B 包 AI 规则第 3.1、3.9 节）：

1. **模板只生成草稿**：本模块没有任何"发布"路径，也不构造 `Plan` 或 `RuleVersion`。
2. **人工修订不被生成覆盖**：`apply_template()` 只产出**新的草稿修订**，
   不读取也不改写既有草稿；重新生成得到修订号 +1 的新对象，旧对象原样保留。
3. **上下文缺失不编造**：缺口非空时**不生成草稿**，返回缺口。

模板读取是**只读资源访问**（`importlib.resources`），不是业务文件 I/O。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import Any

from aitest.application.project.context import ContextGap
from aitest.contracts.templates import TemplatePack
from aitest.domain.planning.templates import TemplateRef

#: 内置模板资源的目录名。
TEMPLATE_RESOURCE_PACKAGE = "aitest.resources"
TEMPLATE_RESOURCE_DIR = "templates"


class TemplateNotFoundError(LookupError):
    """请求的模板或版本不在已安装资源里。

    需求 P1-FR04 异常分支："无匹配模板时提示自建或导入"——
    因此这是一个**可向用户展示**的错误，不是内部故障。
    """


# ------------------------------------------------------------------ 模板加载


@dataclass(frozen=True, slots=True)
class TemplateSummary:
    """列表用的模板摘要；**不含正文**（列表读摘要，详情按引用读取）。"""

    template_ref: TemplateRef
    name: str
    implementation_status: str
    delivery_method: str
    applicability: str

    def __post_init__(self) -> None:
        for name in (
            "name",
            "implementation_status",
            "delivery_method",
            "applicability",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"template summary {name} must not be empty")


def load_template(template_ref: TemplateRef) -> TemplatePack:
    """按**准确版本**读取模板正文；不返回"最新版本"。

    当前发布布局是 `<template_id>/<version>.json` 单文件
    （架构文档第 3 节描述的 `manifest.json` + 子目录与仓库实际不一致，已登记为 D-01，以代码为准）。
    """
    directory = files(TEMPLATE_RESOURCE_PACKAGE).joinpath(
        TEMPLATE_RESOURCE_DIR, template_ref.template_id
    )
    source = directory.joinpath(f"{template_ref.version}.json")
    if not source.is_file():
        raise TemplateNotFoundError(
            "unknown template version: "
            f"{template_ref.template_id}@{template_ref.version}"
        )
    return TemplatePack.model_validate_json(source.read_text(encoding="utf-8"))


def list_templates() -> tuple[TemplateSummary, ...]:
    """列出安装后可用的模板版本，按 `(template_id, version)` 排序。

    对应需求 P1-FR04："随插件提供版本化的通用模板包"与 P1-AC17
    （同一模板处理两个项目，生成结构一致的草稿）。
    """
    root = files(TEMPLATE_RESOURCE_PACKAGE).joinpath(TEMPLATE_RESOURCE_DIR)
    summaries: list[TemplateSummary] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir():
            continue
        for source in sorted(directory.iterdir(), key=lambda item: item.name):
            if not source.name.endswith(".json"):
                continue
            pack = TemplatePack.model_validate_json(source.read_text(encoding="utf-8"))
            summaries.append(
                TemplateSummary(
                    template_ref=TemplateRef(
                        template_id=pack.template_id, version=pack.version
                    ),
                    name=pack.name,
                    implementation_status=pack.implementation_status.value,
                    delivery_method=pack.delivery_method,
                    applicability=pack.applicability,
                )
            )
    return tuple(
        sorted(
            summaries,
            key=lambda item: (item.template_ref.template_id, item.template_ref.version),
        )
    )


# ------------------------------------------------------------------ 草稿与失效


@dataclass(frozen=True, slots=True)
class RevisionContext:
    """生成一份草稿时**实际依赖**的修订集合。

    只记"生成时用到了什么"，不记时间：业务顺序按提交序号判断。
    某类来源不参与本次生成时用 `None` 表示**不适用**（不是"未知"）。
    """

    project_revision: int
    binding_revision: int
    template_revision: str
    environment_revision: int | None = None
    source_revision: int | None = None
    rules_revision: int | None = None

    def __post_init__(self) -> None:
        for name in ("project_revision", "binding_revision"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if not self.template_revision.strip():
            raise ValueError("template_revision must not be empty")
        for name in ("environment_revision", "source_revision", "rules_revision"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} must be >= 1 when applicable")


class DraftKind:
    """草稿类别（用常量而不是枚举：取值由调用方与报告侧共同约定）。"""

    CONTEXT_SUMMARY = "context_summary"
    CHECK_CONTENT = "check_content"
    ACCEPTANCE_ITEMS = "acceptance_items"
    CASE_SUGGESTION = "case_suggestion"
    DELIVERY_NOTE = "delivery_note"


@dataclass(frozen=True, slots=True)
class GeneratedContent:
    """一次生成的草稿。

    **记录实际依赖的修订集合**（`revision_context`），使"相关来源变化后定向过期"
    有据可依；`revision` 单调递增，因此重新生成得到**新对象**，旧草稿不被覆盖。
    """

    generated_content_id: str
    project_id: str
    draft_kind: str
    template_ref: TemplateRef
    revision: int
    revision_context: RevisionContext
    status: str = "draft"
    #: 草稿正文的摘要；正文另按引用落盘（`generated_content` 记录）。
    #: 只存元数据会让"模型确实产出了什么"没有可核对的字节。
    content_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("generated_content_id", "project_id", "draft_kind", "status"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.revision < 1:
            raise ValueError("generated content revision must be >= 1")
        if self.status != "draft":
            raise ValueError("generated content is always a draft and must not be published")
        if self.content_digest is not None and not self.content_digest.strip():
            raise ValueError("content_digest must be a real digest when present")


@dataclass(frozen=True, slots=True)
class DraftResult:
    """生成结果：要么得到草稿，要么得到阻塞缺口，**不会两者都有**。"""

    content: GeneratedContent | None
    gaps: tuple[ContextGap, ...] = ()

    def __post_init__(self) -> None:
        if self.content is None and not self.gaps:
            raise ValueError("a blocked generation requires at least one gap")
        if self.content is not None and self.gaps:
            raise ValueError("a generated draft must not carry blocking gaps")


def apply_template(
    *,
    template_ref: TemplateRef,
    project_id: str,
    revision_context: RevisionContext,
    draft_kind: str,
    gaps: tuple[ContextGap, ...] = (),
    content_revision: int = 1,
) -> DraftResult:
    """应用模板生成一份草稿。

    - 缺口非空 → **不生成草稿**，原样返回缺口（不编造检查内容）；
    - 生成成功 → 返回 `GeneratedContent`，`status` 恒为 `draft`。

    **不提供覆盖既有草稿的入口**：调用方再次生成时应传入递增的 `content_revision`，
    得到一个**新对象**；人工修订过的旧草稿仍按原修订保存（架构文档第 3 节）。
    """
    if gaps:
        return DraftResult(content=None, gaps=gaps)

    pack = load_template(template_ref)
    if pack.template_id != template_ref.template_id or pack.version != template_ref.version:
        raise ValueError("template resource does not match the requested reference")

    return DraftResult(
        content=GeneratedContent(
            generated_content_id=f"draft:{project_id}:{draft_kind}:{content_revision}",
            project_id=project_id,
            draft_kind=draft_kind,
            template_ref=template_ref,
            revision=content_revision,
            revision_context=revision_context,
        )
    )


def text_digest(text: str) -> str | None:
    """文本摘要；空文本返回 `None`（**不是**空串占位）。"""
    if not text.strip():
        return None
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


def template_draft_text(pack: TemplatePack) -> str:
    """模板草稿的**正文**：模板内容按冻结 Schema 的规范 JSON 形态。

    为什么用模板内容本身当正文：`apply_template()` 产出的是"应用了哪个模板的哪一个版本"
    的**草稿元数据**，模板的可核对内容就是它自己声明的检查项、必测项、关键链路与样例。
    把它按 `TemplatePack` 的生成 Schema 序输出（键排序、紧凑分隔符、`ensure_ascii`），
    正文就是**可复现、可核对**的字节，`content_digest` 也才有意义。

    这一步**不编造检查内容**：正文完全来自已安装的模板资源，模板里没有的东西不会出现。
    """
    canonical = pack.model_dump(mode="json")
    return json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def generated_content_payload(
    content: GeneratedContent,
    draft_text: str,
    *,
    credential_filter: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """草稿的落盘 payload：**正文与摘要一起落**。

    只存元数据会让"到底产出了什么"没有可核对的字节，报告与导出也就无法引用真实内容。
    模板草稿与模型草稿**共用这一份形状**，避免两条路径各写一套。

    `credential_filter` 是**模型路径专有**的落盘事实（检查项 B-03）：
    `draft_text` 是**已在落盘前过滤掉已知凭据**的正文，这里只登记"过滤了几个、
    按哪一版策略"，**不含任何凭据原值或摘要**。模板路径不传，键不出现。
    """
    payload: dict[str, Any] = {
        "project_id": content.project_id,
        "generated_content_id": content.generated_content_id,
        "draft_kind": content.draft_kind,
        "template_id": content.template_ref.template_id,
        "template_version": content.template_ref.version,
        "revision": content.revision,
        "status": content.status,
        "content_digest": content.content_digest,
        "draft_text": draft_text,
        "revision_context": {
            "project_revision": content.revision_context.project_revision,
            "binding_revision": content.revision_context.binding_revision,
            "template_revision": content.revision_context.template_revision,
            "environment_revision": content.revision_context.environment_revision,
            "source_revision": content.revision_context.source_revision,
            "rules_revision": content.revision_context.rules_revision,
        },
    }
    if credential_filter is not None:
        payload["credential_filter"] = dict(credential_filter)
    return payload


def draft_expiry(
    content: GeneratedContent, current: RevisionContext
) -> tuple[str, ...]:
    """列出使该草稿**定向过期**的来源名；**只列实际变化的那几项**。

    未变化的来源不出现在结果里，因此未变部分仍可复用
    （架构文档第 9 节："仅重新生成受影响草稿；未变部分可复用"）。
    不适用（`None`）的来源不参与比较：它本来就不影响这份草稿。
    """
    changed: list[str] = []
    if current.project_revision != content.revision_context.project_revision:
        changed.append("project_revision")
    if current.binding_revision != content.revision_context.binding_revision:
        changed.append("binding_revision")
    if current.template_revision != content.revision_context.template_revision:
        changed.append("template_revision")
    for name in ("environment_revision", "source_revision", "rules_revision"):
        recorded = getattr(content.revision_context, name)
        if recorded is not None and getattr(current, name) != recorded:
            changed.append(name)
    return tuple(changed)


# ------------------------------------------------------------------ 模板适用条件


@dataclass(frozen=True, slots=True)
class TemplateRequirements:
    """模板**要求**哪些前置能力。

    取自模板正文的 `applicability` 与必测条目：全是"要求"，
    因此字段命名统一为 `requires_*`，避免与项目实际能力混淆。
    """

    requires_entry_point: bool = True
    requires_http_target: bool = False
    requires_agent_model: bool = False
    requires_frontend: bool = False
    requires_database_verification: bool = False


@dataclass(frozen=True, slots=True)
class ProjectCapabilities:
    """项目**实际具备**哪些能力（由调用方从项目上下文与环境中判定）。"""

    has_entry_point: bool = False
    has_http_target: bool = False
    has_agent_model: bool = False
    has_frontend: bool = False
    has_database_verification: bool = False


@dataclass(frozen=True, slots=True)
class ApplicabilityAssessment:
    """适用性判定结果。

    `missing` 里每一项都是**模板要求而项目没有**的能力。三项语义严格区分：

    - **适用**：`applicable=True`、`missing` 为空；
    - **不适用**：有 `missing` 项 —— 该模板**不能**用于这个项目；
    - **尚不支持**：不在此判定范围内（指能力清单里未实现的技术栈），
      由调用方另行声明，**不得与"不适用"混用**。
    """

    applicable: bool
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.applicable and self.missing:
            raise ValueError("an applicable template must not report missing requirements")
        if not self.applicable and not self.missing:
            raise ValueError("a non-applicable template must name what is missing")


def assess_template(
    requirements: TemplateRequirements, capabilities: ProjectCapabilities
) -> ApplicabilityAssessment:
    """判定模板是否适用于该项目。

    架构文档与施工清单要求"不适用项给出原因"，
    因此这里返回**具体缺哪一项**，而不是只回一个布尔。
    """
    missing: list[str] = []
    if requirements.requires_entry_point and not capabilities.has_entry_point:
        missing.append("entry_point")
    if requirements.requires_http_target and not capabilities.has_http_target:
        missing.append("http_target")
    if requirements.requires_agent_model and not capabilities.has_agent_model:
        missing.append("agent_model")
    if requirements.requires_frontend and not capabilities.has_frontend:
        missing.append("frontend")
    if (
        requirements.requires_database_verification
        and not capabilities.has_database_verification
    ):
        missing.append("database_verification")
    return ApplicabilityAssessment(applicable=not missing, missing=tuple(missing))


__all__ = [
    "TEMPLATE_RESOURCE_DIR",
    "TEMPLATE_RESOURCE_PACKAGE",
    "ApplicabilityAssessment",
    "DraftKind",
    "DraftResult",
    "GeneratedContent",
    "ProjectCapabilities",
    "RevisionContext",
    "TemplateNotFoundError",
    "TemplateRequirements",
    "TemplateSummary",
    "apply_template",
    "assess_template",
    "draft_expiry",
    "list_templates",
    "load_template",
]
