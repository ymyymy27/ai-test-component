# B→C 交接一：真实 `PreparedRun` 功能夹具

版本：1.2
日期：2026-09-29
提出方：B 包（项目与计划）
接收方：C 包（赵）
状态：**已完成；四个场景、Plain 键省略和失效规则均已由 C 确认**
依据：组长实施方案第 4 节「只做两次业务交接」第 3 条；`docs/接口对接/已完成/BC-001-PreparedRun/contract.md` 第 13.3、14 节

---

## 1 这次交接要解决什么

实施方案要求：

> **只做两次业务交接**：先以 **B 的真实 `PreparedRun` 替换 C 的夹具**，核对准备/启动间来源变化。

B-C 合同第 13.3 节把这件事登记为缺口：

| 缺口 | 原因 | 对 C 的影响 |
| --- | --- | --- |
| `PreparedRun` 的功能夹具 | 三份夹具为 Sprint 0 的**合同级样例**，未覆盖 Sprint 1 新增的项目上下文对象 | 与 C 的用例可能不完全对应 |

**现在补齐了。** 新增的夹具由**真实编排**产出，不是手写 JSON。

---

## 2 两套夹具的区别（重要）

| | 合同级夹具（Sprint 0，已有） | **功能夹具（本次新增）** |
| --- | --- | --- |
| 路径 | `tests/contracts/fixtures/prepared_run/` | **`tests/contracts/fixtures/prepared_run_functional/`** |
| 文件 | `success.json`、`failure.json`、`unknown.json` | **`git.json`、`plain.json`、`blocked.json`、`needs_reprepare.json`** |
| 来源 | **手写** JSON | 由 `tests/support/prepared_run_factory.py` **真实编排**产出 |
| 覆盖 | 合同字段形状 | 合同字段 **+ Sprint 1 的项目/绑定/模块/环境/依赖图** |
| 漂移风险 | 手改才变，可能与代码脱节 | 有「重新生成并逐字节比对」的测试锁定 |

**两套都保留**：合同级夹具仍是合同解析的基线；功能夹具是对拍用的真实样例。

---

## 3 四个场景

| 文件 | 形态 | `status` | 覆盖的关键点 |
| --- | --- | --- | --- |
| `git.json` | `git` 绑定，full 档 | `prepared` | 完整上下文；含基准提交与差异摘要；含一条 `present_unconfirmed` 与一条 `confirmed` 断言依据 |
| `plain.json` | `plain` 绑定，full 档 | `prepared` | **不出现仓库/分支/提交字段**（键不存在，不是 `null`） |
| `blocked.json` | 上下文缺口 | `blocked` | `blocking_reasons` 说明缺什么；**不抛异常** |
| `needs_reprepare.json` | 已有准备记录 + 来源变化 | `blocked` | `invalidation_rules` 列出**变化的来源**（`snapshot_revision`）；**不把新字节塞进旧意图** |

### 3.1 四个场景共同的真实值

这些值都是**算出来的**，不是编的：

- `payload_hash`：由业务输入规范摘要得出（**不含传输层参数**，也**不含实际观察到的来源修订**）；
- `created_at_commit`：链路上的**提交序号**（项目 1、绑定 2、计划 3、准备记录 4）；
- `created_at`：固定时钟 `2026-09-28T12:00:00Z`（夹具必须可复现，不使用系统时间）；
- 各来源修订：项目 1、绑定 2、环境 1、计划 1、范围 1、规则 1、模板 1、来源 1（`needs_reprepare` 为 9）。

### 3.2 覆盖的 Sprint 1 对象清单

`git.json` 里可核对的真实内容：

| 对象 | 夹具中的体现 |
| --- | --- |
| 项目身份 | `project_id` = `project-ticket`、`workspace_id` = `ws-ticket` |
| 绑定 | `binding_id` = `binding-ticket`、`binding_revision` = 2、`binding_form` = `git` |
| 环境（**解析事实**，不是声明） | `environment.isolation_mode` = `venv`、`interpreter_identity` = `cpython-3.13.3-windows-amd64`、`dependency_set_digest` |
| 执行来源绑定 | `execution_source.registered_entry`、`resolved_input_digest`、`secret_refs` = `("MODEL_API_KEY",)` |
| 源码来源 | `snapshot.content_identity`、`selected_paths`、`exclusion_rules`、`refetch_dependencies` = `("git-lfs:assets/*",)` |
| 计划与范围 | `plan_revision`（`plan-ticket` / 修订 1 / 摘要）、`acceptance_scope_revision` = 1 |
| 规则与模板 | `rule_versions`（`rule-ticket`）、`template_versions`（`ticket-workflow@1.0.0`） |
| 冻结用例 | 2 条，含步骤、`independent_verification`、`importance` |
| **用例关联** | `links.module_ids` = `("module-store", "module-ticket")`、`environment_ids` = `("env-local",)`、`critical_path_ids` = `("path-ticket-write-read",)` |
| 断言依据三态 | `case-change-status` 为 `confirmed`（**带确认引用**）；`case-create-ticket` 为 `present_unconfirmed` |

---

## 4 怎么用（给 C）

夹具是标准的 `PreparedRun` 合同 JSON，直接用合同模型解析即可：

```python
from pathlib import Path
from aitest.contracts.prepared_run import PreparedRun

prepared = PreparedRun.model_validate_json(
    Path("tests/contracts/fixtures/prepared_run_functional/git.json").read_text("utf-8")
)
```

**对拍建议**（按实施方案第 4 节"核对准备/启动间来源变化"）：

1. 用 `git.json` 与 `plain.json` 喂给 C 的 start 路径，核对 C 侧读到的
   `tier` / `driver` / `conclusion_ceiling` / `required_scope`(=M) / `selected_scope`(=S) /
   `intent_id` / `plan_revision` 与夹具一致；
2. 核对 `plain.json` 的 `binding_form=plain` 时，C 侧不出现任何 Git 字段；
3. 用 `needs_reprepare.json` 核对 C 侧对"来源已变"的处理：**不得**按残留意图启动；
4. 用 `blocked.json` 核对阻塞态在 C 侧不被当成"可执行"。

---

## 5 夹具的产生方式（重要：不要手改）

夹具由 `tests/support/prepared_run_factory.py` 的 `build_scenario()` 产出，
链路就是产品链路：

```
项目上下文（项目/绑定/模块/依赖图/环境）
  → 领域发布门禁 validate_plan_publication()
  → prepare_run()
  → PreparedRun
```

有一条测试 `test_fixtures_can_be_regenerated_byte_for_byte` **逐字节比对**磁盘文件与重新生成的结果。
**若该测试失败，说明 B 侧行为变了** —— 应由 B 重新生成夹具并在 PR 里说明**为什么变**，
而不是手改 JSON。

### 5.1 `plain.json` 的一个特别处理

`plain.json` **省略**了 `git_base_commit` / `git_diff_digest` 两个键（不是写成 `null`）。
依据需求 P1-AC25：

> 界面与报告中**不出现**仓库、分支、提交、远端任何内容，**也不显示为空值或"未知"**。

因此夹具按**产品 payload 的规范**输出，而不是直接 `model_dump()` 的结果
（后者会写成 `null`，正是被禁止的形态）。合同对这两个字段有默认值，解析不受影响。

---

## 6 仍未覆盖的（如实说明）

| 项 | 说明 |
| --- | --- |
| `start_run` 之后的事实 | 本文档只覆盖 **prepare** 一侧；运行/尝试/证据属 C 的 `ExecutionFacts` |
| 真实文件存储 | 本次夹具经**内存底座**产出；真实存储的崩溃恢复与写锁验收仍未做 |
| `SourceSnapshot` 的实际建立 | 分工已裁定但字段、端口和适配未实现（B-Q01 / C-Q08），夹具中的 `snapshot.content_identity` 是构造值 |
| Git 形态来源身份 | 同上，待按裁定实现 |

---

## 7 需要 C 回应的

1. 这四个场景**够不够**覆盖你侧的用例？缺哪个场景请点名，B 补。
2. `plain.json` 省略 Git 键这个处理，是否符合你侧的解析预期？
3. `needs_reprepare` 场景的 `invalidation_rules.source_kind` 取值（如 `snapshot_revision`）
   对你侧够不够用？需要哪些取值请列出来。

**回应方式**：直接在本文件下方追加"确认记录"，或改动 `docs/接口对接/已完成/BC-001-PreparedRun/contract.md`。
按对接流程，改动走 PR。

### 7.1 三条问题的当前状态（2026-09-28 复核）

| # | 问题 | 状态 |
| --- | --- | --- |
| 1 | 四个场景够不够覆盖 C 侧用例 | **已确认**——Git/Plain 覆盖可执行路径，Blocked 覆盖禁止启动，Needs Reprepare 覆盖旧意图不得复用 |
| 2 | `plain.json` 省略 Git 键是否符合 C 侧解析预期 | **已由合同覆盖，按现状关闭**——同目录 `contract.md` 的 C-01—C-10 已双方确认并合并；`plain` 形态省略 Git 键是合同约定行为，实测 `plain.json` 不含任何 Git 键 |
| 3 | `invalidation_rules.source_kind` 取值是否够用 | **已由合同覆盖，按现状关闭**——合同已确认，且该字段在 `contracts/prepared_run.py` 中为无约束字符串（`Field(min_length=1)`），取值不匹配不会造成静默错误；实测 `needs_reprepare.json` 使用 `snapshot_revision` |

**复核依据**：同目录 `contract.md` 第 7.1—7.4 节的 C 逐条确认结论，以及
`tests/contracts/fixtures/prepared_run_functional/` 下四份夹具的实际取值。
三条问题均已关闭，确认原文见主合同第 14.2—14.3 节。

---

## 8 确认记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-28 | 1.0 | 初稿：四个场景的功能夹具、产生方式、对拍建议、待 C 回应的三项 | B 包（待 C 回复） |
| 2026-09-28 | 1.1 | 复核第 7 节三条问题的状态：第 2、3 条已由 C-01—C-10 合同覆盖并关闭，只剩第 1 条待 C 确认（新增 7.1 节） | B 包 |
| 2026-09-29 | 1.2 | C 确认四个场景足够覆盖当前 start 路径，Plain 省略 Git 键和 `snapshot_revision` 取值均可按现状关闭 | B、C 包 |
