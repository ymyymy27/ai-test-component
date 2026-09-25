# B-A 跨包需求：B 包所需端口与保存语义

版本：0.1
日期：2026-09-24
提出方：B 包（feix-a，项目与计划）
接收方：A 包（本地核心底座）
状态：**待 A 确认**
依据：一期架构文档《01-项目与计划》第 7、8、11 节；《04-存储与恢复》第 2、13 节；组长实施方案第 3 节

---

## 1 目的与前提

架构文档《01-项目与计划》第 7 节末明确：

> 依赖方向：`application → domain`，外部能力一律经 `application/ports.py` 的端口。本册不直接读写业务文件，
> 记录落盘统一走《04-存储与恢复》的工作单元。

因此 B 包**不直接读写任何业务文件**，全部落盘经 A 的工作单元与端口。本文档列出 B 所需的最小端口面，
请 A 统一加入 `application/ports.py`。

**B 包不会自行修改 `application/ports.py`**（该文件的所有者是 A），仅在本文档提出签名需求。

---

## 2 B 需要保存的记录清单

依据架构文档《01-项目与计划》第 7 节"记录字段与模块归属"表：

| 记录 | 主责模块 | B 的用途 |
| --- | --- | --- |
| `LocalProject` / `LocalProjectBinding` | `domain/project/context.py` | FR01：项目身份与目录绑定（git / plain 两形态） |
| `Module` / `Dependency` | `domain/project/context.py` | FR01：模块与依赖边 |
| `Task` / `AcceptanceItem` / `Delivery` | `domain/project/context.py` | FR02：任务与标准交付说明 |
| `EnvironmentRef` | `domain/project/context.py` | FR01：环境引用 |
| `SourceSnapshot` | `domain/execution/sources.py`（**归属存疑，见第 5 节**） | FR01：源码内容身份 |
| `TemplatePack` / `CriticalPath` | `domain/planning/templates.py` | FR04：模板与关键链路 |
| `GeneratedContent` | `application/ai_assistance.py` 编排后经工作单元保存 | FR04：草稿与来源修订 |
| `RuleDraft` / `RuleVersion` | `domain/planning/rules.py` | FR05：规则版本 |
| `Case` / `CaseLink` | `domain/planning/plans.py` | FR06：用例与关联 |
| `Plan` / `AcceptanceScope` | `domain/planning/plans.py` | FR06/07：冻结计划与验收范围 |
| `PreparationRecord` | B 的应用用例（经工作单元登记） | FR07：准备意图与幂等 |
| `PreparedRun` | B 产生，C 读取 | FR07：B 的唯一对外业务交付 |

---

## 3 端口签名需求

以下为**语义需求**，具体签名形式由 A 决定。全部方法都要求支持 `expected_revision` 与幂等结果。

### 3.1 `RecordRepository`

```text
save_project(project, expected_revision) -> RevisionRef
read_project(project_id, revision) -> LocalProject        # 按准确修订读取，不返回“最新”
list_projects(project_id) -> Page[ProjectSummary]         # 列表读摘要，详情按引用读取

save_binding(binding, expected_revision) -> RevisionRef
read_binding(binding_id, revision) -> LocalProjectBinding

save_module / read_module / list_modules
save_dependency_set / read_dependency_set

save_task / read_task
save_delivery / read_delivery
save_acceptance_item / read_acceptance_item

save_environment(environment, expected_revision) -> RevisionRef
read_environment(environment_id, revision) -> EnvironmentRef

save_template_ref / read_template_ref
save_generated_content / read_generated_content
save_rule_draft / publish_rule_version -> RevisionRef
read_rule_version(rule_id, revision) -> RuleVersion

save_case(case, expected_revision) -> RevisionRef
read_case(case_id, revision) -> Case                     # 必须能按准确修订读取
save_case_link / read_case_links
save_plan(plan, expected_revision) -> RevisionRef
read_plan(plan_id, revision) -> Plan
save_acceptance_scope(scope, expected_revision) -> RevisionRef
read_acceptance_scope(scope_id, revision) -> AcceptanceScope
```

**关键要求：**

1. **按准确修订读取。** 实施方案第 3 节明确禁止 B 向 C 传"当前最新计划"。
   所有 `read_*` 必须接受显式修订参数，且不得默默回退到最新值。
2. **`expected_revision` 冲突处理。** 架构文档第 8 节：
   "跨用例必须比对 `expected_revision`；来源过期或并发编辑冲突返回**当前修订和差异提示**，
   **不自动覆盖用户编辑**。"
3. **列表读摘要，详情按引用读取。** （`AGENTS.md` 第 4 节 / 存储第 13 节有限 `QuerySpec`）

### 3.2 准备意图登记（FR07 核心）

```text
register_preparation(project_id, client_id, prepare_request_id, payload_hash, intent_id,
                     source_revisions) -> PreparationRecord
find_preparation(project_id, client_id, prepare_request_id) -> PreparationRecord | None
find_preparation_by_intent(intent_id) -> PreparationRecord | None
```

**语义要求（架构文档第 11 节）：**

- 在工作单元内按 `(project_id, client_id, prepare_request_id)` 登记 `PreparationRecord` 及 `intent_id`；
- **同一键但输入摘要不同 → 返回冲突**，不得覆盖；
- **并发完成同一准备请求时只能发布一条 `PreparationRecord`**，其余调用方取得已发布结果；
- `intent_id` 必须与准备记录**同一次提交**；
- 跨入口恢复通过 `intent_id` 或准备查询取得已有意图，**不得用新入口的 `request_id` 替代业务身份**。

### 3.3 `WorkspaceUnitOfWork`

B 的应用用例需要：

- 明确的**短事务边界**（提交点由用例决定）；
- 在**同一事务内**提交记录、引用、索引与幂等结果；
- **提交后可见性**（提交成功即对后续读取可见）；
- 事务外的外部调用（源码读取与摘要计算、模型调用）——这点由 B 保证，但需要 A 的事务不隐含持有长锁。

架构文档第 11 节：

> 准备需执行的源码读取与摘要计算在**短事务外**完成；提交时校验项目、绑定、计划、规则、模板、环境修订及
> 准备请求是否仍有效。

### 3.4 `Clock`

B 的领域对象不使用系统时间。架构文档第 9 节：`Clock` 提供记录和控制所需时间。
B 需要 `now()`；业务顺序以**提交序号**判断，不按可调整的系统时间选最新（功能文档第 5 节）。

### 3.5 `SourceSnapshotPort`、`SourceControlPort`、`SecretPort`、`ModelProvider`、`ProjectionPort`

B 包是这些端口的主要使用者之一：

| 端口 | B 的用途 | 关键约束 |
| --- | --- | --- |
| `SourceSnapshotPort` | 建立 analysis / prepare 用途快照，按实际字节计算内容摘要 | 元数据（mtime）只是变化提示，**不证明内容相同** |
| `SourceControlPort` | 本地 Git 元信息/差异；可选 GitHub 只读 | **plain 形态不注册、不调用**；GitHub 不可用不阻塞本地 |
| `SecretPort` | 按用途解析引用（模型 / 被测 HTTP / 核验数据库 / GitHub 分别授权） | 只返回引用解析结果，不向视图返回正文；不能只允许模型密钥 |
| `ModelProvider` | 策略校验后的脱敏投影草案请求 | 模型**只输出草稿**；迟到响应标过期 |
| `ProjectionPort` | 生成安全投影 | 源码片段默认关闭；无法安全投影则排除并显示分析缺口 |

---

## 4 关于 `application/ports.py` 的协作方式（重要）

**现状**：C 包已在 `origin/feat/package-c-execution`（commit `d60781d`）中修改了 `application/ports.py`，
新增 `SpoolStore` 协议并为 `ExecutionPort` 补充了 4 个方法签名。改动本身符合依赖方向
（`application` 依赖 `domain` 是允许的）。

**建议的协作约定：**

1. **`application/ports.py` 保持单文件，不要拆成包。** 原因：
   `tests/architecture/test_boundaries.py` 有一条硬编码断言——

   ```python
   if relative.parts[0] == "infrastructure" and name.startswith("aitest.application"):
       assert name in {"aitest.application.ports", "aitest.application.errors"}
   ```

   拆成 `application/ports/` 会让该断言失败，属于跨包破坏性改动。

2. **各包只在自己的段落追加，不重排、不整理他人已有的类。** git 对非相邻 hunk 的合并是可靠的，
   重排会导致所有人的 PR 冲突。

3. **A 是唯一所有者与合并仲裁人。** B/C/D 需要新端口时先在本目录提需求文档，由 A 统一加入。

4. **合并顺序**：若 B 的 PR 与 C 的 PR 都动了该文件，后合并方负责解冲突并重跑
   `uv run pytest tests/architecture -q`。

---

## 5 需要 A 裁定的一处归属冲突：`SourceSnapshot`

**冲突事实：**

| 来源 | 原文 |
| --- | --- |
| 架构文档《01-项目与计划》第 7 节记录表 | `SourceSnapshot` 的主责模块是 `domain/execution/sources.py` |
| 架构文档《01-项目与计划》第 1、2 节 | 源码快照的建立、内容身份、排除规则、复取依赖是 **FR01（B 包）** 的职责 |
| 需求文档 P1-FR01 | "**源码快照**：Git 保存基准提交、未提交新增/修改/删除内容及排除规则……" |
| 仓库现状 | C 包已修改 `domain/execution/sources.py`（117 行新增） |

**即：领域对象的物理位置在 C 的目录，但"何时建立快照、快照用途、内容身份规则"是 B 的职责。**

**B 的建议方案（不移动文件，避免破坏 C 的代码与架构测试）：**

- 领域对象 `SourceSnapshot` 的**物理定义保留在 `domain/execution/sources.py`**；
- **建立时机、`purpose` 取值（`analysis` / `prepare`）、排除规则、内容身份计算规则由 B 的应用用例决定**；
- C 只**读取**已冻结的快照，不自行建立 prepare 用途快照。

请 A（或组长）确认该分工，并同步修正架构文档中可能引起歧义的表述。

---

## 6 待 A 确认问题清单

1. 第 3 节列出的端口方法是否照单加入，还是希望 B 先提交一版签名草案？
2. `register_preparation` 的"同键异摘要返回冲突"是在端口层实现，还是由 B 的应用用例在事务内检查？
3. `SourceSnapshot` 的归属（第 5 节）请裁定。
4. `PreparedRun` 是否需要注册为查询结果类型（供 D 展示）？若需要，请给出查询入口建议。
5. 工作单元是否提供"提交后可见性"的读取入口，还是 B 需要单独的查询端口？

---

## 7 确认记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿 | B 包 feix-a（待 A 回复） |
