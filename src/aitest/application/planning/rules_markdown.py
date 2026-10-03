"""规则的可携带 **Markdown** 形态：人类可读、可往返、不引入新依赖。

对应需求 P1-FR05（"人类步骤、AI 规则、证据和输出规范；**Markdown 导入导出**"）
与检查文档 B-04（"合同的规则 Markdown 导入导出尚未实现"）。

## 为什么需要本模块

`portable.py` 已经给出**规范 JSON** 的可携带形态（键排序、逐字节往返、可核对摘要），
但需求要的导入导出格式是 **Markdown**：那是给人看、给人改的格式。
两者不是替代关系——规范 JSON 仍是**往返与校验的基准**，本模块在它之上加一层
人类可读的渲染/解析。

## 方言（本模块定义，是唯一权威）

```markdown
# 规则：<rule_id> @<revision>

- 规范版本：aitest.rule-portable/1.0
- 适用范围：<scope>
- 来源：<source>

## 规则正文

（自由文本，可多行、可包含列表与代码块）

## 步骤

- 第一步
- 第二步

## 证据要求

- 原始响应

## 未识别字段

```json
[]
```
```

### 解析规则（逐条确定，避免歧义）

1. **标题**：文首第一个一级标题，形如 `# 规则：<rule_id> @<revision>`。
   `revision` 必须是 `>= 1` 的整数。
2. **元数据**：标题之后、第一个二级标题之前的行，形如 `- 键：值`。
   四个键固定：`规范版本`、`适用范围`、`来源`（`来源` 可为空串）。
3. **段落**：二级标题名与上面完全一致时切换段落；**未知的二级标题按错误拒绝**
   （宁可拒绝也不猜，避免把"没读懂"当成"没有"）。
4. **正文**：`## 规则正文` 下的自由文本，去掉首尾空行；内部空行保留。
   **不做硬换行**：正文里的换行就是内容本身的一部分。
5. **列表**：`## 步骤` 与 `## 证据要求` 下的 `- ` 条目；
   **空列表**用单独一行 `_（无）_` 表示——它不会被误读成一个条目。
6. **未识别字段**：`## 未识别字段` 下**恰好一个** ```json 围栏块，内容为 JSON 数组。
   非数组或非法 JSON 一律拒绝。
7. **导入恒为草稿**：与规范 JSON 路径同一口径——不接受 `confirmed` / `enablement`，
   它们不在可携带字段集合里。**导入不是发布。**

### 有意的取舍

- **不保留原始字节**：本格式是"给人编辑"的，往返保证**字段语义**往返
  （`payload -> markdown -> payload` 相等），不保证文本逐字节相同。
  需要逐字节核对时用规范 JSON（`portable.canonical_digest`）。
- **不用 YAML**：`pyproject.toml` 没有 YAML 依赖，而"为了一个导出格式新增依赖"
  属第③级决定。用固定四行的元数据 + JSON 围栏块即可无损承载全部字段。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from aitest.application.planning.portable import (
    RULE_PORTABLE_FIELDS,
    RULE_PORTABLE_SCHEMA_VERSION,
    canonical_json,
    rule_draft_from_payload,
)
from aitest.domain.planning.rules import RuleDraft

#: 文首标题前缀；导入时逐字匹配。
_TITLE_PREFIX = "# 规则："

#: 固定段落名。**顺序即渲染顺序**；导入时按名匹配，不依赖顺序。
_SECTION_BODY = "规则正文"
_SECTION_STEPS = "步骤"
_SECTION_EVIDENCE = "证据要求"
_SECTION_UNKNOWN = "未识别字段"

_SECTIONS: tuple[str, ...] = (
    _SECTION_BODY,
    _SECTION_STEPS,
    _SECTION_EVIDENCE,
    _SECTION_UNKNOWN,
)

#: 元数据标签；导入时逐字匹配。
_LABEL_SCHEMA = "规范版本"
_LABEL_SCOPE = "适用范围"
_LABEL_SOURCE = "来源"

_METADATA_LABELS: tuple[str, ...] = (_LABEL_SCHEMA, _LABEL_SCOPE, _LABEL_SOURCE)

#: 空列表的显式标记；避免"没有条目"与"有一个空条目"两种解释。
_EMPTY_LIST_MARKER = "_（无）_"

_JSON_FENCE = "```json"
_FENCE = "```"


# ------------------------------------------------------------------ 导出


def rule_markdown_from_payload(payload: Mapping[str, Any]) -> str:
    """把**规则可携带 payload** 渲染成 Markdown（方言见模块 docstring）。

    只接受可携带字段集合内的键；多余的键按错误拒绝，避免"渲染时悄悄丢字段"。
    """
    _require_portable_payload(payload)
    rule_id = payload["rule_id"]
    revision = payload["revision"]
    scope = payload["scope"]
    source = payload["source"]
    body = payload["text"]
    steps = payload["steps"]
    evidence = payload["evidence_requirements"]
    unknown = payload["unknown_extension_fields"]
    assert isinstance(rule_id, str)
    assert isinstance(revision, int)

    lines: list[str] = [
        f"{_TITLE_PREFIX}{rule_id} @{revision}",
        "",
        f"- {_LABEL_SCHEMA}：{RULE_PORTABLE_SCHEMA_VERSION}",
        f"- {_LABEL_SCOPE}：{scope}",
        f"- {_LABEL_SOURCE}：{source}",
        "",
    ]
    lines += _render_section(_SECTION_BODY, _render_body(str(body)))
    lines += _render_list_section(_SECTION_STEPS, _as_text_list(steps, "steps"))
    lines += _render_list_section(
        _SECTION_EVIDENCE, _as_text_list(evidence, "evidence_requirements")
    )
    lines += _render_unknown_section(_as_text_list(unknown, "unknown_extension_fields"))
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_section(name: str, content: list[str]) -> list[str]:
    return [f"## {name}", "", *content, ""]


def _render_body(text: str) -> list[str]:
    """正文原样输出（去尾空行），**不做硬换行**。"""
    if not text.strip():
        return [_EMPTY_LIST_MARKER]
    return text.rstrip("\n").split("\n")


def _render_list_section(name: str, items: tuple[str, ...]) -> list[str]:
    if not items:
        return [f"## {name}", "", _EMPTY_LIST_MARKER, ""]
    return [f"## {name}", "", *[f"- {item}" for item in items], ""]


def _render_unknown_section(items: tuple[str, ...]) -> list[str]:
    rendered = json.dumps(list(items), ensure_ascii=False, sort_keys=True)
    return [f"## {_SECTION_UNKNOWN}", "", _JSON_FENCE, rendered, _FENCE, ""]


def _require_portable_payload(payload: Mapping[str, Any]) -> None:
    missing = [key for key in RULE_PORTABLE_FIELDS if key not in payload]
    if missing:
        raise ValueError(f"payload is missing portable fields: {missing}")
    extra = sorted(set(payload) - set(RULE_PORTABLE_FIELDS) - {"project_id"})
    if extra:
        raise ValueError(f"payload carries non-portable fields: {extra}")


def _as_text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} entries must be non-empty strings")
        items.append(item)
    return tuple(items)


# ------------------------------------------------------------------ 导入


def rule_markdown_to_payload(markdown: str) -> dict[str, Any]:
    """把 Markdown 解析回**规则可携带 payload**。

    解析口径见模块 docstring 第 2 节。任何"读不懂"的情况都**报错**，
    不做默认值填充——"没读到"不等于"没有"。
    """
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("markdown must be a non-empty string")

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # 1) 标题
    title_index = _first_non_blank(lines)
    if title_index is None or not lines[title_index].startswith(_TITLE_PREFIX):
        raise ValueError(f"markdown must start with a level-1 title '{_TITLE_PREFIX}...'")
    rule_id, revision = _parse_title(lines[title_index])

    # 2) 元数据 + 3) 段落
    sections: dict[str, list[str]] = {}
    metadata: dict[str, str] = {}
    current: str | None = None
    for raw in lines[title_index + 1 :]:
        # **刻意不 `rstrip()` 内容行**：行尾空格属于正文内容，格式不该改内容。
        line = raw
        if line.startswith("## "):
            name = line[3:].strip()
            if name not in _SECTIONS:
                raise ValueError(f"unknown section heading: {name!r}")
            if name in sections:
                raise ValueError(f"duplicate section heading: {name!r}")
            sections[name] = []
            current = name
            continue
        if current is None:
            if not line.strip():
                continue
            key_value = _parse_metadata_line(line)
            if key_value is None:
                raise ValueError(
                    f"expected a metadata line '- 标签：值' before the first section: {line!r}"
                )
            key, value = key_value
            if key in metadata:
                raise ValueError(f"duplicate metadata label: {key!r}")
            metadata[key] = value
            continue
        sections[current].append(line)

    missing_labels = [label for label in _METADATA_LABELS if label not in metadata]
    if missing_labels:
        raise ValueError(f"markdown is missing metadata labels: {missing_labels}")
    missing_sections = [name for name in _SECTIONS if name not in sections]
    if missing_sections:
        raise ValueError(f"markdown is missing sections: {missing_sections}")
    if metadata[_LABEL_SCHEMA] != RULE_PORTABLE_SCHEMA_VERSION:
        raise ValueError(
            "unsupported portable schema version: "
            f"{metadata[_LABEL_SCHEMA]!r} (expected {RULE_PORTABLE_SCHEMA_VERSION!r})"
        )

    body = _parse_body(sections[_SECTION_BODY])
    steps = _parse_list(sections[_SECTION_STEPS], _SECTION_STEPS)
    evidence = _parse_list(sections[_SECTION_EVIDENCE], _SECTION_EVIDENCE)
    unknown = _parse_unknown(sections[_SECTION_UNKNOWN])

    return {
        "schema_version": RULE_PORTABLE_SCHEMA_VERSION,
        "rule_id": rule_id,
        "revision": revision,
        "scope": metadata[_LABEL_SCOPE],
        "text": body,
        "source": metadata[_LABEL_SOURCE],
        "steps": list(steps),
        "evidence_requirements": list(evidence),
        "known_extension_fields": list(steps),
        "unknown_extension_fields": list(unknown),
    }


def rule_draft_from_markdown(markdown: str) -> RuleDraft:
    """解析并读回**草稿**（复用规范 JSON 路径的 `rule_draft_from_payload()`）。

    与规范 JSON 导入同一口径：**导入恒为未确认、未启用的草稿**。
    """
    return rule_draft_from_payload(rule_markdown_to_payload(markdown))


def _first_non_blank(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if line.strip():
            return index
    return None


def _parse_title(line: str) -> tuple[str, int]:
    rest = line[len(_TITLE_PREFIX) :].strip()
    if " @" not in rest:
        raise ValueError(f"title must look like '{_TITLE_PREFIX}<rule_id> @<revision>'")
    rule_id, _, raw_revision = rest.rpartition(" @")
    rule_id = rule_id.strip()
    raw_revision = raw_revision.strip()
    if not rule_id:
        raise ValueError("title must carry a non-empty rule id")
    try:
        revision = int(raw_revision)
    except ValueError as error:
        raise ValueError(f"title revision must be an integer: {raw_revision!r}") from error
    if revision < 1:
        raise ValueError("title revision must be >= 1")
    return rule_id, revision


def _parse_metadata_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None
    label, separator, value = stripped[2:].partition("：")
    if not separator:
        return None
    label = label.strip()
    if label not in _METADATA_LABELS:
        return None
    return label, value.strip()


def _parse_body(lines: list[str]) -> str:
    text = "\n".join(lines).strip("\n")
    if text.strip() == _EMPTY_LIST_MARKER:
        return ""
    return text


def _parse_list(lines: list[str], name: str) -> tuple[str, ...]:
    meaningful = [line for line in lines if line.strip()]
    if not meaningful:
        return ()
    if len(meaningful) == 1 and meaningful[0].strip() == _EMPTY_LIST_MARKER:
        return ()
    items: list[str] = []
    for line in meaningful:
        stripped = line.strip()
        if not stripped.startswith("- "):
            raise ValueError(f"section {name!r} may only contain '- ' items: {line!r}")
        item = stripped[2:].strip()
        if not item:
            raise ValueError(f"section {name!r} carries an empty item")
        items.append(item)
    return tuple(items)


def _parse_unknown(lines: list[str]) -> tuple[str, ...]:
    """`## 未识别字段` 下恰好一个 ```json 围栏块，内容为字符串数组。"""
    fence_open = None
    fence_close = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == _JSON_FENCE and fence_open is None:
            fence_open = index
            continue
        if stripped == _FENCE and fence_open is not None:
            fence_close = index
            break
    if fence_open is None or fence_close is None:
        raise ValueError(
            f"section {_SECTION_UNKNOWN!r} must contain exactly one '{_JSON_FENCE}' block"
        )
    stray = [
        line.strip()
        for index, line in enumerate(lines)
        if line.strip() and not (fence_open <= index <= fence_close)
    ]
    if stray:
        raise ValueError(f"section {_SECTION_UNKNOWN!r} carries text outside the json block")
    raw = "\n".join(lines[fence_open + 1 : fence_close]).strip()
    try:
        decoded = json.loads(raw) if raw else []
    except json.JSONDecodeError as error:
        raise ValueError(
            f"section {_SECTION_UNKNOWN!r} is not valid JSON: {error}"
        ) from error
    if not isinstance(decoded, list):
        raise ValueError(f"section {_SECTION_UNKNOWN!r} must be a JSON array")
    return _as_text_list(decoded, "unknown_extension_fields")


#: 规范 JSON 与 Markdown 两种形态共用同一份字段集合与摘要口径。
__all__ = [
    "RULE_PORTABLE_SCHEMA_VERSION",
    "canonical_json",
    "rule_draft_from_markdown",
    "rule_markdown_from_payload",
    "rule_markdown_to_payload",
]
