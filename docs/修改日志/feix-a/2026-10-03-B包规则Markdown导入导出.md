# B-04 第三块：规则的 Markdown 导入导出

日期：2026-10-03
包：B（项目与计划）
对应检查文档：`docs/一期工程检查-B包.md`（2026-10-03 版）第 3.1 节 **B-04** 末句
分支：（本文件随该分支提交）

---

## 1 范围

B-04 共三块，本文件只覆盖**第③块**：

| 块 | 内容 | 本轮 |
| --- | --- | --- |
| ① | 实际变更来源与正式 `SourceSnapshot` 转换装配 | **未做** |
| ② | `drift` 覆盖扩展到 Case/规则/范围/模板/快照 | **未做** |
| ③ | **合同的规则 Markdown 导入导出** | **已完成** |

第 35 节当时用规范 JSON 而非 Markdown，理由是"**方言未定**"（见第 35.2 节）；
本轮把方言定下来并实现。

## 2 为什么必须先把方言定下来

需求 P1-FR05 与功能文档都要求"导入／导出 Markdown"，但**没有规定方言**。
在方言未定的前提下做 Markdown 导出会引入两个问题（第 35.2 节原文）：

1. **无法往返**：未识别字段与多行文本在 Markdown 里没有约定的转义规则；
2. **等于另立一套格式合同**：格式一旦被外部文件引用就变成事实契约。

本轮的处理是**把方言写下来**（`rules_markdown.py` 模块 docstring 是唯一权威），
再实现编解码。

## 3 方言与理由

```markdown
# 规则：<rule_id> @<revision>

- 规范版本：aitest.rule-portable/1.0
- 适用范围：<scope>
- 来源：<source>

## 规则正文

（自由文本）

## 步骤

- 第一步

## 证据要求

_（无）_

## 未识别字段

```json
["timeout_ms"]
```
```

| 决定 | 理由 |
| --- | --- |
| 标题承载 `rule_id` + `revision` | 导入方唯一能核对的稳定身份；也是人阅读的第一眼信息 |
| 元数据用固定三行 `- 标签：值` | **不引入 YAML 依赖**——`pyproject.toml` 没有 YAML，为一个导出格式新增依赖属第③级决定 |
| 正文 / 步骤 / 证据要求 / 未识别字段各成一段 | 结构化字段不能与自由正文混淆（第 35.3 节"未知字段不混进步骤"同一原则） |
| 列表统一 `- ` 前缀 | Markdown 里列表就是列表；不用数字，避免"编号被当成内容" |
| 空列表写 `_（无）_` | 否则"没有条目"与"有一个空条目"无法区分 |
| 未识别字段放 ```json 围栏 | 它们是机器字段、不是给人读的内容；JSON 保证转义正确且不会被当步骤 |
| 不用 YAML front matter | 同上不新增依赖；且 front matter 在预览里常被隐藏，不利人工核对 |

## 4 解析口径（严格，不猜默认值）

1. 文首第一个一级标题必须是 `# 规则：<rule_id> @<revision>`，`revision >= 1` 的整数；
2. 三个元数据标签必须齐全，**多出的标签不被识别 → 报错**
   （不静默忽略：防的正是"来源里塞 `confirmed` / `enablement`"）；
3. 段落名**未知即报错、重复即报错**——宁可拒绝，也不把"没读懂"当成"没有"；
4. 正文**不做硬换行、不去行尾空格**：格式不该改内容；
5. 列表段落只接受 `- ` 条目；空项与非 `- ` 行报错；
6. `未识别字段` 段落内**恰好一个** ```json 围栏块、内容为字符串数组，围栏外不得有杂散文本；
7. **导入恒为草稿**：与规范 JSON 路径同一口径（复用 `rule_draft_from_payload()`）。

## 5 改动文件

| 位置 | 内容 |
| --- | --- |
| `src/aitest/application/planning/rules_markdown.py`（新增） | 方言定义与编解码：`rule_markdown_from_payload()`、`rule_markdown_to_payload()`、`rule_draft_from_markdown()` |
| `src/aitest/application/usecase_registry.py` | 新增出口动作 `export_rules_markdown`、`import_rules_markdown`；动作表 17 → **19** |
| `tests/unit/test_rules_markdown.py`（新增） | **23 项** |
| `tests/contracts/test_rule_import_export_entrypoint.py` | 新增 **5 项** |

### 一条接线决定（如实登记）

`export_rules_markdown` **只把可携带字段**交给渲染器。
`export_rule_version()` 另带 `published_confirmation_id` / `published_digest`
这类**本地追溯**字段，它们不属于可携带格式——Markdown 是给人编辑的，
不能携带"某个实例已发布"的宣称（与 `import_rule_version()` 丢弃发布状态的既有口径一致）；
同时"渲染器只接受可携带字段"这一严格性被保留（多余键仍会被拒）。

## 6 验证命令与实测结果

环境：Windows 11 x64 / CPython 3.13.3 / uv 0.12.19；命令一律
`py -3.13 -m uv run --frozen ...`（与 CI 同一套），
`$env:UV_CACHE_DIR='E:\project\project1\.uvcache-tmp'`。

| 检查 | 命令 | 实测结果 |
| --- | --- | --- |
| 静态检查 | `ruff check .` | **All checks passed!** |
| 类型检查 | `mypy` | **Success: no issues found in 137 source files** |
| Markdown 编解码单测 | `pytest tests/unit/test_rules_markdown.py` | **23 passed** |
| 出口合同测试 | `pytest tests/contracts/test_rule_import_export_entrypoint.py` | **16 passed**（新增 5 项） |
| 全量（本执行环境） | `pytest` | **1246 项：7 failed、214 errors**——失败/错误数与改动前**一致**（同一批环境限制：`tmp_path` 目录枚举、命名管道、子进程捕获），**无新增失败** |

## 7 未做到（如实登记）

1. **B-04 的第①、②块未做**：`SourceSnapshot`/实际变更来源装配、`drift` 覆盖扩展；
2. **没有真实环境验收**：`tests/acceptance/p1/status.json` **未改**，
   B 牵头 8 项 AC 的 `evidence.path` 仍为空；
3. **Markdown 不承载本地发布追溯**：见第 5 节的接线决定（刻意取舍）；
4. **不保证文本逐字节往返**：本格式面向人工编辑，保证的是**字段语义**往返；
   需要逐字节核对时用规范 JSON（`portable.canonical_digest()`）。
