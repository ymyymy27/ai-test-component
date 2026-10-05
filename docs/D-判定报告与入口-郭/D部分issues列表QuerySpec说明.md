# D部分 issues.list QuerySpec 说明

版本：0.1（草案）  
日期：2026-10-02  
提出方：D 包（判定、报告与用户入口）- 郭  
接收方：A 包（本地核心底座）  
状态：待 A 确认并编码物理索引

## 1. 目的

本文冻结 D 的 `issues.list` 业务查询语义，供 A 编译索引和执行物理查询。D 不另写全表查询，面板也不对当前加载页做局部筛选。

查询实现源码：

- `src/aitest/domain/review/issue_queries.py`
- `src/aitest/application/review/issue_queries.py`

## 2. QuerySpec 输入

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `project_id` | string | 必填，固定项目 |
| `scope` | `open` / `all` | 默认 `open` |
| `facet` | 枚举 | 主筛选维度 |
| `facet_value` | string / null | 主维度值；`NONE` 时必须为空 |
| `severity` | `P0/P1/P2/P3/unknown` / null | 可选叠加，未知是显式值 |
| `page_size` | integer | 1—200 |
| `cursor` | `IssueListCursor` | 翻页时必填 |

主筛选维度：

- `none`
- `module`
- `layer`
- `review_state`
- `workflow_state`
- `disposition`
- `blocking`

## 3. 固定 14 种掩码

| 编号 | 掩码 |
| --- | --- |
| 1 | `none` |
| 2 | `severity` |
| 3 | `module` |
| 4 | `module+severity` |
| 5 | `layer` |
| 6 | `layer+severity` |
| 7 | `review_state` |
| 8 | `review_state+severity` |
| 9 | `workflow_state` |
| 10 | `workflow_state+severity` |
| 11 | `disposition` |
| 12 | `disposition+severity` |
| 13 | `blocking` |
| 14 | `blocking+severity` |

每个掩码都支持 `OPEN` 和 `ALL`，共 28 种索引入口形态。

## 4. 枚举与未知值

### review_state

- `unreviewed`
- `reviewed`
- `unknown`

### blocking

- `blocking`
- `non_blocking`
- `unknown`

### layer

- `L1`
- `L2`
- `L3`
- `unknown`

`workflow_state`、`disposition` 和 `severity` 的 `unknown` 是显式筛选值，不能与“不筛选”混用。

## 5. OPEN 与 ALL

`ALL` 返回同一 commit 下每个问题当前修订的完整投影。

`OPEN` 包含：

- `active`
- `deferred`
- duplicate 解析到 active/deferred 主问题的问题
- duplicate 关联悬空或循环、无法解析主问题的异常投影

`OPEN` 不包含：

- `fixed`
- `non_defect`
- duplicate 解析到 fixed/non_defect 主问题的问题

无法解析的 duplicate 必须保留在 OPEN 并显示 `gap_reason`，不得静默隐藏。

## 6. 投影字段

`IssueListProjection` 固定包含：

- `project_id`
- `issue_id`
- `content_revision`
- `module_ids`
- `layers`
- `severity`
- `workflow_state`
- `review_state`
- `disposition`
- `blocking`
- `canonical_issue_id`
- `updated_sequence`
- `gap_reason`

索引只保存最小投影和记录引用，不复制证据正文。

## 7. 排序和游标

稳定排序：

```text
updated_sequence ASC, issue_id ASC
```

游标固定：

- `query_spec_version`
- `condition_fingerprint`
- `commit_id`
- `last_updated_sequence`
- `last_issue_id`

不同条件或不同 commit 复用游标必须返回 `QUERY_CURSOR_MISMATCH`。不得静默切换查询根。

`updated_sequence` 取最后一次改变列表投影的已提交序号，不使用客户端时间。

## 8. 错误合同

| 场景 | 错误码 |
| --- | --- |
| 非允许的筛选组合或非法枚举 | `QUERY_UNSUPPORTED_FILTER` |
| 游标跨条件、跨版本或跨 commit | `QUERY_CURSOR_MISMATCH` |
| 对应索引缺失或损坏 | `INDEX_REBUILD_REQUIRED` |

A 负责将后两种错误按统一命令/查询错误结构返回。

## 9. 索引更新责任

每次以下事实发生变化时，A 必须更新受影响列表投影：

- 问题修订。
- 有效人工复核。
- 处置或处置撤销。
- 归并关联或主问题变化。
- 阻塞事实。
- 重开和级别变化。

模块未知使用独立 `unknown` 分支。一个问题的多个模块键必须保证单模块查询不重复返回同一 `issue_id`。

## 10. D 与 A 边界

D 负责：

- 业务过滤语义。
- 14 种掩码。
- OPEN/ALL。
- 重复主问题解析语义。
- DTO 投影。

A 负责：

- 物理索引 Schema。
- 同 commit 原子发布。
- 游标验证。
- 不重复分页。
- 索引缺失、损坏和维护错误。
- 不隐式全扫历史。

## 11. 验证

当前已由 D 侧测试锁定：

- 14 种掩码完整枚举。
- OPEN/ALL 及重复主问题解析。
- 多模块问题不重复。
- severity 独立和叠加筛选。
- unknown 与“不筛选”区分。
- 同 commit 稳定分页无漏重。
- 跨 commit、跨条件游标拒绝。
- 非法组合拒绝。
- 未知 blocking 必须显示缺口。