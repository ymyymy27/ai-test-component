"""模板与规则的导入导出：**可逐字节往返的规范 JSON**。

为什么用规范 JSON 而不是 Markdown
--------------------------------

架构文档《01-项目与计划》把"规则 Markdown 导入导出"记为待实现能力，
但**没有定义 Markdown 方言**。在方言未定的前提下做 Markdown 导出会引入两个问题：

1. **无法逐字节往返**：`unknown_extension_fields`（来源里未识别的执行字段）与
   多行文本在 Markdown 里没有约定的转义规则，导出再导入会丢信息；
2. **等于另立一套格式合同**：格式一旦被外部文件引用，就变成事实契约，
   而它没有经过合同流程。

因此本模块导出的是**按生成 Schema 的规范 JSON**（键排序、紧凑分隔符、`ensure_ascii`）：
可逐字节往返、可核对摘要、未知字段原样保留。**Markdown 方言一旦定下来**，
可以在本模块的导出函数旁再加一个渲染器，规范 JSON 仍是往返与校验的基准。

文件形状
--------

| 对象 | 形状 | 说明 |
| --- | --- | --- |
| 模板 | `TemplatePack` 的生成 Schema 形态 | 与已安装资源同一形状，可直接落回资源目录 |
| 规则 | 规则字段 + `unknown_extension_fields` | 规则无生成 Schema，口径在本模块声明 |

导入的三条硬约束
----------------

1. **导入只得到草稿**：`import_rule_version()` 产出的永远是 `confirmed=False`、
   `enablement=DISABLED` 的草稿——导入**不是**发布，也不等于人工确认
   （需求 P1-FR04："模板只能生成草稿，不自动发布或执行"）；
2. **未知字段单独存放、不混进步骤**：`unknown_extension_fields` 与 `steps` 分开，
   避免"未识别字段被当成可执行步骤"（需求 P1-FR05）；
3. **读不懂就拒绝**：缺字段、类型不符、版本不匹配一律抛错，
   不填默认值、不静默降级。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from aitest.contracts.templates import TemplatePack
from aitest.domain.planning.rules import RuleDraft, RuleEnablement, RuleVersion
from aitest.domain.planning.templates import TemplateRef

#: 规则可携带格式的版本；与模板 `TemplatePack.schema_version` 同一做法。
RULE_PORTABLE_SCHEMA_VERSION = "aitest.rule-portable/1.0"

#: 规则可携带格式的字段集合；与 `rule_draft_to_payload()` 逐字一致。
RULE_PORTABLE_FIELDS: tuple[str, ...] = (
    "schema_version",
    "rule_id",
    "revision",
    "scope",
    "text",
    "source",
    "steps",
    "evidence_requirements",
    "known_extension_fields",
    "unknown_extension_fields",
)


# ------------------------------------------------------------------ 规范编码


def canonical_json(value: object) -> str:
    """规范 JSON：键排序、紧凑分隔符、`ensure_ascii`。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_digest(value: object) -> str:
    """规范 JSON 的 sha256 摘要。"""
    return "sha256:" + sha256(canonical_json(value).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 模板


def export_template(template_ref: TemplateRef) -> dict[str, Any]:
    """导出模板为**生成 Schema 的规范形态**。

    与已安装资源同一形状，因此可以直接落回 `<template_id>/<version>.json`。
    模板不存在时由 `load_template()` 抛错（不产出空模板）。
    """
    from aitest.application.planning.draft import load_template

    pack = load_template(template_ref)
    return pack.model_dump(mode="json")


def import_template(payload: object) -> TemplatePack:
    """读回模板并**校验**：形状必须能过 `TemplatePack` 的生成 Schema。"""
    if not isinstance(payload, Mapping):
        raise ValueError("a template payload must be an object")
    try:
        pack = TemplatePack.model_validate(dict(payload))
    except Exception as error:  # pydantic 的 ValidationError；这里只报"不合形状"
        raise ValueError(f"template payload does not match the schema: {error}") from error
    if pack.template_id != payload.get("template_id") or pack.version != payload.get(
        "version"
    ):
        raise ValueError("template identity does not round-trip")
    return pack


# ------------------------------------------------------------------ 规则


def rule_draft_to_payload(
    draft: RuleDraft, *, project_id: str | None = None
) -> dict[str, Any]:
    """规则草稿的可携带形状。

    `unknown_extension_fields` 单独成键：它与 `steps` 不混放，
    因此"未识别的执行字段"在往返后仍不会被当成步骤。

    `project_id` 是**可选**的：可携带格式本身不要求项目（规则可以跨实例搬），
    但**落盘记录**需要项目范围才可查询，因此保存时传入。
    """
    payload: dict[str, Any] = {
        "schema_version": RULE_PORTABLE_SCHEMA_VERSION,
        "rule_id": draft.rule_id,
        "revision": draft.revision,
        "scope": draft.scope,
        "text": draft.text,
        "source": draft.source,
        "steps": list(draft.steps),
        "evidence_requirements": list(draft.evidence_requirements),
        "known_extension_fields": list(draft.steps),
        "unknown_extension_fields": list(draft.unknown_extension_fields),
    }
    if project_id is not None:
        if not project_id.strip():
            raise ValueError("project_id must not be empty when given")
        payload["project_id"] = project_id
    return payload


def rule_draft_from_payload(payload: object) -> RuleDraft:
    """读回规则草稿，**恒为未确认、未启用的草稿**。

    导入不是发布：即便来源写着"已发布/已确认"，导入结果也是草稿。
    这一点通过"不接受 payload 里的 `confirmed` / `enablement`"实现——
    它们**不在** `RULE_PORTABLE_FIELDS` 里，给了也会被忽略。
    """
    if not isinstance(payload, Mapping):
        raise ValueError("a rule payload must be an object")
    version = payload.get("schema_version")
    if version != RULE_PORTABLE_SCHEMA_VERSION:
        raise ValueError(
            "unsupported rule payload version: "
            f"{version!r} (expected {RULE_PORTABLE_SCHEMA_VERSION!r})"
        )
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("revision must be an integer >= 1")
    return RuleDraft(
        rule_id=_required_text(payload, "rule_id"),
        revision=revision,
        scope=_required_text(payload, "scope"),
        text=_required_text(payload, "text"),
        source=_required_text(payload, "source"),
        steps=_text_list(payload, "steps"),
        evidence_requirements=_text_list(payload, "evidence_requirements"),
        # 导入结果永远是草稿：不读来源的确认与启用状态。
        enablement=RuleEnablement.DISABLED,
        confirmed=False,
        unknown_extension_fields=_text_list(payload, "unknown_extension_fields"),
    )


def export_rule_version(version: RuleVersion) -> dict[str, Any]:
    """导出**已发布**规则版本为可携带形状。

    导出的是内容而非"已发布"这一状态：导入方拿到的是草稿，
    "已发布"由各自实例在本地重新走一次发布动作产生。
    """
    return {
        "schema_version": RULE_PORTABLE_SCHEMA_VERSION,
        "rule_id": version.rule_id,
        "revision": version.revision,
        "scope": version.scope,
        "text": version.text,
        "source": version.source,
        "steps": list(version.steps),
        "evidence_requirements": list(version.evidence_requirements),
        "known_extension_fields": list(version.steps),
        "unknown_extension_fields": [],
        "published_confirmation_id": version.confirmation_id,
        "published_digest": version.digest,
    }


def import_rule_version(payload: object) -> RuleDraft:
    """把导出的规则版本读回为**草稿**（丢弃"已发布"状态）。

    `published_confirmation_id` / `published_digest` 只作追溯信息，
    **不**用来把导入结果标成已发布——否则等于"自报已确认"。
    """
    return rule_draft_from_payload(payload)


def export_rule_payloads(versions: Sequence[RuleVersion]) -> dict[str, Any]:
    """批量导出：一个文件可携带多条规则，按 `(rule_id, revision)` 排序。"""
    ordered = sorted(versions, key=lambda item: (item.rule_id, item.revision))
    return {
        "schema_version": RULE_PORTABLE_SCHEMA_VERSION,
        "rules": [export_rule_version(version) for version in ordered],
    }


def import_rule_payloads(payload: object) -> tuple[RuleDraft, ...]:
    """批量导入：返回**草稿**元组，顺序按 `(rule_id, revision)` 规范化。"""
    if not isinstance(payload, Mapping):
        raise ValueError("a rule bundle must be an object")
    if payload.get("schema_version") != RULE_PORTABLE_SCHEMA_VERSION:
        raise ValueError("unsupported rule bundle version")
    raw = payload.get("rules")
    if not isinstance(raw, (list, tuple)):
        raise ValueError("a rule bundle must carry a rules list")
    drafts = tuple(rule_draft_from_payload(item) for item in raw)
    return tuple(sorted(drafts, key=lambda item: (item.rule_id, item.revision)))


# ------------------------------------------------------------------ 读取辅助


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _text_list(payload: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name, [])
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{index}] must be a non-empty string")
        out.append(item)
    return tuple(out)


__all__ = [
    "RULE_PORTABLE_FIELDS",
    "RULE_PORTABLE_SCHEMA_VERSION",
    "canonical_digest",
    "canonical_json",
    "export_rule_payloads",
    "export_rule_version",
    "export_template",
    "import_rule_payloads",
    "import_rule_version",
    "import_template",
    "rule_draft_from_payload",
    "rule_draft_to_payload",
]
