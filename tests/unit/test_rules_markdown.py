"""规则 Markdown 导入导出（检查文档 B-04 的"规则 Markdown"一项）。

依据：需求 P1-FR05（"人类步骤、AI 规则、证据和输出规范；**Markdown 导入导出**"）；
功能文档第 4 节；`docs/文档-feix-a/B包/02-B包施工检查项清单.md` 第 39 节。

方言由 `rules_markdown.py` 的模块 docstring 定义，本文件逐条锁定它：

1. **往返**：`payload -> markdown -> payload` 字段语义相等（含空列表、多行正文、
   含 JSON 围栏块的正文、未知字段）；
2. **可读**：渲染结果里逐字出现标题、四个元数据标签、四个段落名；
3. **严格解析**：未知段落名、重复段落、缺标签、错版本、非法 JSON、
   围栏外的杂散文本、空条目——全部**报错**，不猜默认值；
4. **导入恒为草稿**：与规范 JSON 路径同一口径，来源写 `confirmed` / `enablement` 也无效。
"""

from __future__ import annotations

import pytest

from aitest.application.planning.portable import rule_draft_to_payload
from aitest.application.planning.rules_markdown import (
    RULE_PORTABLE_SCHEMA_VERSION,
    rule_draft_from_markdown,
    rule_markdown_from_payload,
    rule_markdown_to_payload,
)
from aitest.domain.planning.rules import RuleDraft, RuleEnablement


def _draft(**overrides: object) -> RuleDraft:
    values: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 3,
        "scope": "http workflows",
        "text": "第一行\n\n第二段",
        "source": "manual",
        "steps": ("调用接口", "读回响应"),
        "evidence_requirements": ("原始响应",),
        "unknown_extension_fields": (),
        "enablement": RuleEnablement.ENABLED,
        "confirmed": True,
    }
    values.update(overrides)
    return RuleDraft(**values)  # type: ignore[arg-type]


def _payload(**overrides: object) -> dict:
    return rule_draft_to_payload(_draft(**overrides))


# ------------------------------------------------------------------ 往返


def test_a_rule_payload_round_trips_through_markdown() -> None:
    payload = _payload()
    rendered = rule_markdown_from_payload(payload)
    assert rule_markdown_to_payload(rendered) == payload


def test_empty_lists_round_trip() -> None:
    """空列表必须可往返：没有条目与"有一个空条目"不能混为一谈。"""
    payload = _payload(steps=(), evidence_requirements=())
    rendered = rule_markdown_from_payload(payload)
    assert "（无）" in rendered
    back = rule_markdown_to_payload(rendered)
    assert back["steps"] == []
    assert back["evidence_requirements"] == []
    assert back == payload


def test_multi_line_text_round_trips_without_hard_wrapping() -> None:
    """正文里的换行是内容本身：不折行、不合并空行、**不改动行内容**。"""
    text = "第一行\n\n第三行\n- 看起来像列表\n# 看起来像标题"
    payload = _payload(text=text)
    back = rule_markdown_to_payload(rule_markdown_from_payload(payload))
    assert back["text"] == text


def test_trailing_spaces_in_the_body_are_preserved() -> None:
    """格式不该改内容：行尾空格属于正文，不能在这里被吃掉。"""
    text = "第一行   \n第二行"
    payload = _payload(text=text)
    back = rule_markdown_to_payload(rule_markdown_from_payload(payload))
    assert back["text"] == text


def test_text_containing_a_json_fence_round_trips() -> None:
    """正文里出现 ```json 围栏块也不能打乱解析（解析按段落名定位）。"""
    text = "示例：\n```json\n{\"a\": 1}\n```\n结束"
    payload = _payload(text=text)
    back = rule_markdown_to_payload(rule_markdown_from_payload(payload))
    assert back["text"] == text


def test_unknown_extension_fields_round_trip() -> None:
    payload = _payload(unknown_extension_fields=("timeout_ms", "retry_policy"))
    rendered = rule_markdown_from_payload(payload)
    back = rule_markdown_to_payload(rendered)
    assert back["unknown_extension_fields"] == ["timeout_ms", "retry_policy"]
    assert back == payload


def test_the_rendered_markdown_is_readable() -> None:
    """渲染结果里必须逐字出现标题、四个标签与四个段落名（这是"给人看"的最低要求）。"""
    rendered = rule_markdown_from_payload(_payload())
    for token in (
        "# 规则：rule-1 @3",
        "- 规范版本：",
        "- 适用范围：http workflows",
        "- 来源：manual",
        "## 规则正文",
        "## 步骤",
        "## 证据要求",
        "## 未识别字段",
    ):
        assert token in rendered, token


def test_revision_and_identity_come_from_the_title() -> None:
    """身份与修订取自标题——这是导入方唯一能核对的稳定身份。"""
    payload = _payload(rule_id="rule-42", revision=7)
    parsed = rule_markdown_to_payload(rule_markdown_from_payload(payload))
    assert parsed["rule_id"] == "rule-42"
    assert parsed["revision"] == 7


# ------------------------------------------------------------------ 严格解析


def test_an_unknown_section_heading_is_refused() -> None:
    """未知段落名报错：宁可拒绝，也不把"没读懂"当成"没有"。"""
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("## 步骤", "## 步骤清单")
    with pytest.raises(ValueError, match="unknown section heading"):
        rule_markdown_to_payload(broken)


def test_a_duplicate_section_heading_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("## 证据要求", "## 步骤")
    with pytest.raises(ValueError, match="duplicate section heading"):
        rule_markdown_to_payload(broken)


def test_a_missing_metadata_label_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("- 来源：manual\n", "")
    with pytest.raises(ValueError, match="missing metadata labels"):
        rule_markdown_to_payload(broken)


def test_an_unsupported_schema_version_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace(RULE_PORTABLE_SCHEMA_VERSION, "aitest.rule-portable/0.9")
    with pytest.raises(ValueError, match="unsupported portable schema version"):
        rule_markdown_to_payload(broken)


def test_a_missing_title_is_refused() -> None:
    with pytest.raises(ValueError, match="level-1 title"):
        rule_markdown_to_payload("- 规范版本：x\n")


def test_a_non_integer_revision_in_the_title_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("# 规则：rule-1 @3", "# 规则：rule-1 @latest")
    with pytest.raises(ValueError, match="revision must be an integer"):
        rule_markdown_to_payload(broken)


def test_an_invalid_json_block_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace('["timeout_ms"]', "not-json")
    broken = broken.replace("```json\n[]", "```json\nnot-json")
    with pytest.raises(ValueError, match="not valid JSON|must be a JSON array"):
        rule_markdown_to_payload(broken)


def test_a_non_array_json_block_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("```json\n[]", '```json\n{"a": 1}')
    with pytest.raises(ValueError, match="must be a JSON array"):
        rule_markdown_to_payload(broken)


def test_stray_text_in_the_unknown_section_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("## 未识别字段\n", "## 未识别字段\n\n随手写的一行\n")
    with pytest.raises(ValueError, match="outside the json block"):
        rule_markdown_to_payload(broken)


def test_an_empty_step_item_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("- 调用接口", "- ")
    with pytest.raises(ValueError, match="empty item|only contain"):
        rule_markdown_to_payload(broken)


def test_non_list_content_in_a_list_section_is_refused() -> None:
    rendered = rule_markdown_from_payload(_payload())
    broken = rendered.replace("- 调用接口", "调用接口")
    with pytest.raises(ValueError, match="only contain"):
        rule_markdown_to_payload(broken)


# ------------------------------------------------------------------ 导入恒为草稿


def test_import_is_always_an_unconfirmed_disabled_draft() -> None:
    """与规范 JSON 导入同一口径：导入不是发布。"""
    payload = _payload(enablement=RuleEnablement.ENABLED)
    imported = rule_draft_from_markdown(rule_markdown_from_payload(payload))
    assert imported.confirmed is False
    assert imported.enablement is RuleEnablement.DISABLED


def test_source_flags_in_the_markdown_are_ignored() -> None:
    """来源里硬塞 `confirmed` / `enablement` 也不生效——它们不在可携带字段里。"""
    rendered = rule_markdown_from_payload(_payload())
    injected = rendered.replace(
        "- 来源：manual\n", "- 来源：manual\n- confirmed：true\n- enablement：enabled\n"
    )
    # 多出来的标签**不被识别**，因此既不会被当真，也不会被静默丢弃——
    # 解析器把它当成"第一个段落之前的非法行"直接拒绝。
    with pytest.raises(ValueError, match="expected a metadata line"):
        rule_markdown_to_payload(injected)


def test_export_refuses_a_payload_with_extra_fields() -> None:
    """渲染时不允许悄悄丢字段：多余键按错误拒绝。"""
    payload = _payload()
    payload["confirmed"] = True
    with pytest.raises(ValueError, match="non-portable fields"):
        rule_markdown_from_payload(payload)


def test_export_refuses_a_payload_missing_fields() -> None:
    payload = _payload()
    del payload["scope"]
    with pytest.raises(ValueError, match="missing portable fields"):
        rule_markdown_from_payload(payload)
