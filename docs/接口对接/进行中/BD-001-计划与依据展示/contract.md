---
contract_id: BD-001
title: 计划、范围与断言依据展示
provider: B
consumer: D
contract_version: "0.2"
contract_status: reviewing
provider_implementation: partial
consumer_implementation: not_started
verification_status: not_run
last_verified_commit: null
blockers: []
next_owner: D
next_action: D 回复第 5 节五项展示/查询/运行中修订问题，以及第 8 节五项动作与状态新增的确认与排期
---

# B-D 跨包需求：计划、范围与断言依据的展示口径

版本：0.2
日期：2026-09-25
提出方：B 包（项目与计划）
接收方：D 包（判定、报告与用户入口）
状态：**待 D 确认**（2.2 节的枚举已由 B 实现，见第 6 节确认记录）
依据：一期架构文档《01-项目与计划》第 3、4 节；《03-报告与缺陷》；功能文档第 2、3 节；需求 P1-FR06/07/14/17、P1-AC17/AC20/AC31/AC32

---

## 1 目的

D 包负责面板呈现与报告，需要展示"测什么、怎么测、这次能不能判通过"。这些值**全部由 B 冻结或派生**，
D 只读取、不重算。本文档明确：哪些值 B 提供、哪些值 D 可以算、哪些值 D **不得**自行推导。

核心背景（实施方案第 6 节）：最大的对接风险之一是"把 B 的计划修订当成 C 的实时可变输入"。
版本管理上，**B 提供的是按准确修订冻结的快照，不是"当前最新"**。

---

## 2 B 提供给 D 的只读数据

### 2.1 计划与范围（FR06/FR07）

| 数据 | 说明 | 修订语义 |
| --- | --- | --- |
| `Plan` | 计划编号、修订、冻结的规则版本与用例修订、必测项集合、档位、驱动、结论上限 | 不可变；变化生成新修订 |
| `AcceptanceScope` | 目标、包含/排除范围及原因、依赖闭包、适用性、模板修订 | 不可变修订；运行冻结 `scope_revision` |
| `Case` / `CaseLink` | 用例字段 + 关联验收项/模块/环境/关键链路 | 发布后不可变 |
| `RunPlanRevision` | 运行中修订：序号、生效步骤边界、变更内容、原因、操作人 | **由 C 在执行侧记录**，B 提供守卫规则 |

**三条必须向用户如实展示的口径（需求 P1-FR06/07）：**

1. `M`（冻结必测）与 `S`（本轮所选）**分列展示**；未选、跳过、未执行项**逐条列出并说明原因**，
   **一律记为"未执行"，不能当作"不适用"**。
2. 按需自测允许用户选子集；**未选与未执行项即使全部勾满也不判完整通过**。
3. `T`（模板适用必测下限）低于下限时报告必须显示 **"必测覆盖率警告"**，并在**完整报告**的证据等级上体现。

### 2.2 档位、驱动与结论上限（FR07）

| 值 | 取值 | 唯一来源 |
| --- | --- | --- |
| `RunTier` | `quick` / `on_demand` / `full` | B 冻结在运行上（**已实现**，`domain/planning/plans.py`） |
| `RunDriver` | `planned` / `stepwise` | B 冻结；运行中只允许 `planned → stepwise`（**已实现**） |
| `ConclusionCeiling` | `partial` / `passable` | **由档位派生，B 计算**（**已实现**：`conclusion_ceiling_for()`，并由合同在解析时校验档位一致） |

**硬性口径：**

> **结论上限只由档位决定，不由覆盖多少或执行方式决定。**
> `quick` 与 `on_demand` 即使把冻结范围全部选上并跑完，仍只判所选范围的结果，**不能判完整通过**；
> 需要完整通过结论时必须新建一次 `full` 运行。
> —— 需求 P1-FR07

因此 D 的界面**不得**根据"覆盖率很高"把结论上限提升为 `passable`。
判定入口只接受 `ConclusionCeiling`，不接受"覆盖了多少"作为放宽依据（架构文档第 4 节）。

> ⚠️ **已知上游问题**：C 包三份 `ExecutionFacts` 夹具中 `conclusion_ceiling` 均写作 `"full"`，
> 该值不属于 `partial` 或 `passable`。B 已在 `docs/接口对接/已完成/BC-001-PreparedRun/contract.md`
> 的 C-01 提出修正。修正前，D 不得按 `"full"` 实现任何分支逻辑。

### 2.3 断言依据三态（FR06）

每条用例的断言依据状态分三态，**三者语义完全不同，不可合并**：

| 状态 | 含义 | 对展示的要求 |
| --- | --- | --- |
| `missing` | 缺失 | **禁止进入 full 必测**；面板应阻止发布/运行并给出补齐入口 |
| `present_unconfirmed` | 已填写未确认 | 可以执行；保留在分母内；显示为**"断言未核实"**；**不计入已验证 V**；完整报告下调证据等级 |
| `confirmed` | 已确认 | **仍须实际证据/独立核验有效**才计入 V |

**必须由 B 派生、D 只读的字段：**

- `Case.assertion_basis_state` —— **该 Case 修订保存时的状态**（冻结值，不回写）
- `effective_assertion_basis_state` —— 基于**准确 Case/依据修订 + 有效 `ConfirmationRecord`** 派生的**当前有效状态**

依据功能文档第 4 节：

> 后补依据确认按准确 Case/依据修订及 `ConfirmationRecord` 派生 `effective_assertion_basis_state`，
> **不修改冻结 Case/Plan 或旧报告**。依据变化后旧确认失效。

**D 侧实现约束：**

1. D **不得**自行由"文本是否为空"推断依据状态；
2. 依据文本变化后，旧确认**失效**，`effective_assertion_basis_state` 必须回落；
3. **补充确认不能把 `verified_count` 加一** —— 功能文档第 3 节：
   "确认依据后必须重新评价已存在的实际核验材料，不能直接给 `verified_count` 加一。"

### 2.4 独立核验方式

发布用例时**必须登记该用例的独立核验方式**；缺失时该用例**不得计入已验证**（架构文档第 3 节）。
D 展示"未验证"时，应能区分"缺独立核验方式"与"核验未完成"。

---

## 3 B 的领域规则与 D 的领域规则的边界

避免重复实现（`AGENTS.md` 第 3 节：判定、覆盖、证据等级只在领域规则中计算一次）：

| 计算项 | 所有者 | 位置 |
| --- | --- | --- |
| 档位 → 结论上限 | **B** | `domain/planning/` |
| `T ⊆ M`、`M ⊆ S`、full 跳过项为 0 | **B** | `domain/planning/` |
| 不得删必测 / 弱化断言 / 变更适用性的拒绝 | **B** | `domain/planning/` |
| 驱动单向收窄（`planned → stepwise`） | **B** | `domain/planning/` |
| `effective_assertion_basis_state` 派生 | **B** | `domain/planning/` |
| 用例聚合 `E/R/V/P/F/U`、`H`、统计分母 | **D** | `domain/review/` |
| 证据等级 A/B/C/D | **D** | `domain/review/` |
| 问题归并、复核、关闭守卫 | **D** | `domain/review/` |
| 报告冻结与历史修订 | **D** | `domain/review/` |
| 连接重试策略 | A | `connectivity` |
| 源码身份固定（预期） | **B** | prepare 冻结 |
| 源码实际执行来源核对 | C | start 解析 |

**口径说明**：`ConclusionCeiling` 与证据等级是两个独立维度，不可互相推导。
`quick`/`on_demand` 的等级为 `null`（不打分），但仍有结论上限 `partial`。

---

## 4 与 P1-AC17 / AC20 / AC31 / AC32 相关的展示要求

B 牵头的这四个 AC 需要 D 的界面配合，列在这里便于对齐：

| AC | B 侧保证 | D 侧需要呈现 |
| --- | --- | --- |
| **AC17** | 同一模板处理两个项目生成**结构一致**的草稿并标注来源；上下文缺失时**列缺口并阻塞**；未确认草稿不进入执行 | 草稿来源标记、缺口清单与阻塞原因、草稿不可执行的明确入口 |
| **AC20** | 运行中修订只影响未执行步骤；正在执行的拒绝修改；反向驱动切换被拒；依据失效时暂停 | **基础计划修订与运行中修订序列都可查**；标注"依据已过期"并保留历史原始结论；当前完整通过不得使用失效依据 |
| **AC31** | `publish_plan` 属人工动作，`agent_relay` 不能代为确认 | 每个副作用动作**单独**的确认记录；输入/目标/凭据范围变化后原授权失效的提示；不接受参数自报角色 |
| **AC32** | `ModelOutboundPolicy` 按项目修订；`source_snippets` 默认关闭 | 首次出站确认入口；改接收地址/材料类别/源码片段开关后**重新确认**；关闭 AI 后已有人工/规则/报告仍可用 |

---

## 5 待 D 确认问题清单

1. `PreparedRun` 需要以什么形式提供给面板——完整快照、还是摘要 + 按引用读取详情？
2. `effective_assertion_basis_state` 由 B 派生后，D 是通过查询获取，还是随用例记录一起返回？
3. D 是否需要"模板覆盖 `count(M∩T)/count(T)`"的计算结果，还是仅需要 `M`、`T` 两个集合自行展示？
4. 报告中的"必测覆盖率警告"由 B 提供布尔标志 + 缺口清单，还是 D 自行比较？
5. 运行中修订序列（`RunPlanRevision`）由 C 持久化，D 的展示是否也需要 B 提供守卫规则说明？

---

## 6 确认记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿 | B 包（待 D 回复） |
| 2026-09-25 | 0.2 | 2.2 节标注三个枚举已实现；新增第 7 节 B 侧环境与交付对象的展示口径 | B 包 |
| 2026-10-03 | 0.3 | 新增第 8 节：B 侧动作与状态的新增（发布第二次起必须声明 `expected_revision`、出站 `OUTBOUND_UNRESOLVED`、两个规则 Markdown 动作与受控确认动作 `confirm_assertion_basis`，**动作表 15 → 18**）并列出 5 条待 D 确认项。对应检查文档 2026-10-03 版 B-11／B-10／B-04 第三块与 B-01。**不改既有字段、不要求 D 改 Schema** | B 包（待 D 确认与排期） |

---

## 7 B 侧对象边界（2026-09-25 新增，D 展示时须知）

以下三项来自 B 包 Sprint 1 的领域层实现。**B 侧已完成**，供 D 明确"什么可展示、什么不可由界面推断"。

### 7.1 环境：面板只能展示声明，不得把声明当已核实事实

B 侧把环境拆成两个对象：

| 对象 | 内容 | 何时产生 | D 的展示要求 |
| --- | --- | --- | --- |
| `EnvironmentRef` | 声明与策略：隔离方式、解释器**要求**、依赖声明来源、数据隔离/复位、超时、网络目标、按用途 `SecretRef` | 用户登记环境时 | 可展示为"配置" |
| `ResolvedEnvironment` | 已解析**事实**：`interpreter_identity`、`dependency_set_digest` | prepare 时刻 | 可展示为"已核实来源" |

**`EnvironmentRef` 不含解释器实际身份与依赖集合摘要**，因此面板不得在只有声明时显示"环境已核实"。
`isolation_mode` 三态（`venv` / `none` / `unmanaged`）：**`none` 是用户显式选择的合法状态，不得渲染为"未配置"或"异常"。**

### 7.2 交付说明：自述与验证必须分列

`Delivery` 已拆出 `self_report`（`SelfReport`：`completed` / `incomplete`）与 `verified_in_scope` / `unverified_scope`。

**展示要求（需求 P1-FR02）：**

1. 开发自述完成与测试验证完成**分列两处**，不得合并为一个"完成"字段；
2. `verified_in_scope` 为空而 `self_report.completed` 非空时，必须显示为**待验证**，不得显示为已完成；
3. AI 草稿不作为完成证据。

### 7.3 项目身份与依赖

- `LocalProject.project_id` 是 `local_project_id` 的权威别名，取值相同；DTO 中只有一个项目编号。
- 依赖边由 `Dependency` 记录承载（使用者 → 提供者），唯一权威来源；允许循环，拒绝自环。
- 推导得出的依赖边（`DependencyOrigin.INFERRED`）带 `source`，展示时必须标注**"推导结果，可能不完整"**（需求 P1-FR03）。
- `BindingForm`（`git` / `plain`）：**`plain` 项目不显示任何仓库字段**，且不因不是 Git 仓库而降低检查范围。

### 7.4 模板状态与必测清单（2026-09-27 新增）

六个内置模板已在 Sprint 3 升级定稿。面板展示模板时须知：

| 项 | 值 | 展示要求 |
| --- | --- | --- |
| `implementation_status` | `draft` / `released` / `deprecated` | 取值由原先固定的 `scaffold` 改为三态。**`released` 只表示模板内容定稿**，不表示项目草稿已确认，也不表示可以执行 |
| `required_item_ids` | 模板适用必测下限（`T`） | 与计划冻结的 `M` 分列展示；`T ⊄ M` 时显示**必测覆盖率警告** |
| `critical_paths` | 关键链路：`real_dependencies`、`required_verification` | 展示"哪些依赖必须真实、哪次核验不可省"；为空时必须同时展示 `no_critical_path_reason` |
| `assertion_basis` | 固定写法"待用户补齐并确认，模板不是断言依据" | **不得展示为已填写的断言依据**；它是待用户补齐项，不是模板提供的结论 |

**已知的取值变化**：升级前六份模板为 `scaffold`；升级后为 `released`。若面板此前按 `scaffold` 判断"模板未定稿"，需同步调整。

---

## 8 B 侧动作与状态的新增（2026-10-03）

对应 `docs/一期工程检查-B包.md`（2026-10-03 版）**B-11**（发布修订核对）、
**B-10**（出站意图去重）、**B-04 第三块**（规则 Markdown）。
B 侧已随 PR #70 合并（`develop a7ad9fc`）。**本节不改 `BD-001` 既有字段，也不要求 D 改 Schema。**

### 8.1 发布类动作的行为变化：第二次及以后必须声明修订

`publish_rules` / `publish_plan` 现在**核对调用方声明的 `expected_revision`**
（`AB-001` 第 8.12 节的原文："**第二次及以后的发布必须声明"我看到的是 `@N`"**"）。

| 场景 | `Command.expected_revision` | 结果 |
| --- | --- | --- |
| 首次发布 | `0`（表示"新建"） | 正常发布 |
| 后续发布 | **必须**声明"我看到的是 `@N`" | 与当前修订一致则发布；不一致报 **`B_REVISION_CONFLICT`** |

**与 P1-AC31 的衔接**：AC31 已要求"输入/目标/凭据范围变化后**原授权失效的提示**"。
因此发起发布的入口需携带用户所见的修订（首次为 `0`，其后为 `@N`）；
收到 `B_REVISION_CONFLICT` 时的展示口径属第 8.4 节待确认项，
**B 侧不再替调用方接受最新基线**（这是本次行为变化的实质）。

同类约束也适用于 `save_case` / `save_acceptance_scope`：**正文修订必须等于这次分配的仓储修订**，
调用方**不能跳号或复用旧修订号**（`AB-001` 第 8.14 节），不符同样报 `B_REVISION_CONFLICT`。

### 8.2 出站新增状态 `OUTBOUND_UNRESOLVED`

模型出站现在区分"**同一业务请求号已有出站意图、但没有已提交的结果**"这一情形，
返回状态 **`OUTBOUND_UNRESOLVED`**，**不盲目重发**（对应 P1-AC32 与 `AB-001` 第 8.13 节）。

**与 P1-AC32 的衔接**：AC32 已要求"首次出站确认入口；改接收地址/材料类别/源码片段开关后**重新确认**"。
该状态对应的展示要求是"**本次出站结果未知，需核对原始出站事实**"；
重新生成需由用户明确选择新的业务请求号。展示口径属第 8.4 节待确认项。

### 8.3 新增两个规则 Markdown 动作与一个受控确认动作

| 动作 | 参数 | 结果 |
| --- | --- | --- |
| `export_rules_markdown` | `rule_versions` | `documents`：每项 `{rule_id, revision, markdown}` |
| `import_rules_markdown` | `markdown`（单份或列表） | `imported`：与 `import_rules` 同形（**恒为未确认未启用草稿**） |
| `confirm_assertion_basis` | `case_id`／`case_revision`／`basis_text_digest` | 登记一条依据确认（`confirmation_id` 与 `confirmed_at_commit` 由系统派生，见 `AB-001` 第 8.17 节） |

**与 D-06 的衔接**（"CLI/MCP/面板/Trae 没有同核心业务动作"）：
**动作清单由 15 增至 18**（两个 Markdown 动作与一个受控确认动作），
D 侧实现用户动作与导航时可直接接入这三个动作。
**Markdown 方言由 B 定义**（`application/planning/rules_markdown.py` 的模块 docstring 为唯一权威），
无需在 D 侧另行实现渲染。

### 8.4 待 D 确认

| # | 待确认事项 | 性质 |
| --- | --- | --- |
| 1 | 第 8.1—8.3 节三项变化是否与 D 侧的用户动作与角色边界设计一致（对应 `D-06`） | 合同闭环 |
| 2 | `B_REVISION_CONFLICT` 的展示口径（提示重读 vs. 其他）；是否需要 B 提供当前修订与差异提示 | 展示口径 |
| 3 | 两个 Markdown 动作在面板/CLI/MCP 三处的暴露范围 | 接入范围 |
| 4 | 上述三项是否影响 D 侧**已有的静态面板测试**（`D-08` 涉及的阶段卡与导航断言） | 回归影响 |
| 5 | 接入排期：是否与 `D-09`（`reports.py` 空 full 范围）和 `D-02`（`ExecutionFacts→DecisionFacts` 适配）并入同一批 | 排期 |

D 侧已于 2026-10-03 回复五项，确认动作表为 **18 项**；发布冲突、出站未知、Markdown 动作和人工确认的展示/入口边界见 [`review-D.md`](review-D.md)。该回复不表示 D 业务桥已经接入或 P1-AC 已验收。

**以上五项属合同闭环事项，不构成 B 侧阻塞**，按 D 现有工作节奏回复即可。
**B 侧不改 D 的目录，也不代 D 决定展示口径**；本节仅登记动作与状态变化并列出待确认项。
