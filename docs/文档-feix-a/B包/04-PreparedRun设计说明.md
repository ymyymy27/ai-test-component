# PreparedRun 设计说明

版本：0.1
日期：2026-09-25
分支：`feat/package-b-project-planning`
状态：待评审
依据：一期架构文档《01-项目与计划》第 3、4、7、11、12 节；《02-执行与证据》；《04-存储与恢复》；一期需求文档 P1-FR01、FR06、FR07；《一期工程四部分拆分与低对接实施方案》第 3 节
关联：`docs/接口对接/B-C-PreparedRun与词汇表合同.md`、`docs/文档-feix-a/B包/03-待冻结枚举与跨包字段确认表.md`

---

## 1 目的

B 包对外的唯一业务交付是 `PreparedRun`。本文档确定：

1. `PreparedRun` 的字段构成与语义；
2. 运行词汇表（档位、驱动、结论上限）的定义与派生关系；
3. 与 C、D 的字段交接口径；
4. 实现时不可违反的不变量。

本文档是 `src/aitest/contracts/prepared_run.py` 与三份夹具的设计依据。

## 2 要解决的问题

架构文档第 4 节：

> `prepare_run` 解析真实源码和环境、检查已发布计划与 full 适用下限，产生 `intent_id` 和 canonical `payload_hash`。

需要冻结在准备时刻的原因（实施方案第 3 节）：

> 防止执行时重新读取可变草稿、计划确认被误当逐项动作授权。

即：从"用户点准备"到"实际启动执行"之间存在时间差，期间源码、计划、规则、环境都可能被修改。
若 C 在启动时读取"当前最新"的对象，就会出现"用新源码执行旧授权"。
`PreparedRun` 是准备时刻的一张不可变快照，C 只读取它，不做二次推导。

## 3 设计原则

| 原则 | 含义 |
| --- | --- |
| 不可变 | 一旦生成不再修改；输入变化生成新的 `PreparedRun` |
| 按准确修订引用 | 所有来源以"编号 + 修订 + 摘要"引用，不引用"最新" |
| 来源冻结 | 源码、环境、执行入口的实际身份在准备时冻结 |
| 词汇统一 | 档位、驱动、结论上限的定义由 B 唯一提供，C/D 只读取 |
| 缺口如实 | 缺失、未确认、阻塞均显式登记，不用默认值补出结论 |

## 4 运行词汇表

依据架构文档第 4 节。三个枚举的定义归 B，`domain/planning/plans.py` 为唯一语义来源。

### 4.1 档位 `RunTier`

| 值 | 含义 | 范围 |
| --- | --- | --- |
| `quick` | 快速检查 | 适用 L1 + 本次改动涉及模块的关键 L2 + 适用独立核验；无需勾选 |
| `on_demand` | 按需自测 | 用户自选检查集；三档中唯一允许选择子集的档位 |
| `full` | 完整验证 | 冻结范围内全部适用必测项，跳过项必须为 0；用户只能增补 |

### 4.2 驱动 `RunDriver`

| 值 | 含义 |
| --- | --- |
| `planned` | 计划驱动（默认）：确认一次计划后连续执行 |
| `stepwise` | 逐步驱动：每步确认后执行 |

运行中只允许 `planned → stepwise`（收窄授权）；反向必须拒绝并提示新建运行。

### 4.3 结论上限 `ConclusionCeiling`

| 值 | 含义 |
| --- | --- |
| `partial` | 只代表所选范围，不判完整通过 |
| `passable` | 证据与 P0／P1 条件满足时可判通过 |

**派生关系（唯一口径）：**

| `RunTier` | `ConclusionCeiling` | 证据等级 |
| --- | --- | --- |
| `quick` | `partial` | 不打分 |
| `on_demand` | `partial` | 不打分 |
| `full` | `passable` | A / B / C / D（由 D 派生） |

架构文档第 4 节：

> **结论上限只由档位派生**：`on_demand` 即使把冻结范围全部选上也仍为 `partial`，
> 判定入口只接受 `ConclusionCeiling`，不接受"覆盖了多少"作为放宽依据。

因此 `conclusion_ceiling` 不由输入传入，而由 `run_tier` 计算。合同层对此设校验。

### 4.4 词汇表与 C 的关系

C 的 `ExecutionFacts` 已声明 `RunTierFact`（值一致）与 `driver: str`、`conclusion_ceiling: str`。
B 侧合同声明 `RunTierFact`、`RunDriverFact`、`ConclusionCeilingFact`，
并以测试锁定两侧值集合一致（见第 8 节）。

> 已发现的上游缺陷：C 的三份夹具中 `conclusion_ceiling` 均写作 `"full"`。
> 详见 `docs/接口对接/B-C-PreparedRun与词汇表合同.md` C-01。

## 5 字段设计

### 5.1 身份与幂等

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | `Literal["aitest.prepared-run/1.0"]` | 合同版本 |
| `prepared_run_id` | `str` | 本快照编号 |
| `project_id` | `str` | 等于 `local_project_id`，二者为同一值 |
| `workspace_id` | `str` | 工作空间标识 |
| `client_id` | `str` | 客户端持久身份；重传不变 |
| `prepare_request_id` | `str` | 一次明确的准备请求；重传及取回丢失响应继续使用 |
| `intent_id` | `str` | 应用创建的业务意图，与准备记录同一次提交 |
| `payload_hash` | `str` | 规范化输入摘要 |
| `created_at` | `datetime` | 记录时间 |
| `created_at_commit` | `str` | 提交序号；业务顺序以此判断，不用系统时间 |
| `status` | `PreparedRunStatus` | `prepared` / `blocked` |

架构文档第 11 节要求：同 `(project_id, client_id, prepare_request_id)` 但输入摘要不同时返回冲突；
跨入口恢复使用 `intent_id`，不以 `request_id` 代替业务身份。

**关于"依据需重新准备"**：该结论不是快照的状态。

`PreparedRun` 不可变，因此不会从"可用"变成"需重新准备"。
架构文档第 11 节：

> 同请求期间源码已变则返回原意图及"依据需重新准备"，不能把新字节塞进旧意图。

实现上，快照携带 `invalidation_rules`，由应用用例在收到启动或查询请求时，
将当前来源修订与快照冻结的修订比较，得出该结论。快照本身保持 `prepared`。
这样既满足不可变性，也保留了"旧意图不可执行、需重新准备"的语义。

### 5.2 项目绑定

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `binding_id` / `binding_revision` | `str` / `int` | 绑定及其修订 |
| `binding_form` | `BindingForm` | `git` / `plain` |

**`plain` 形态完全省略 Git 字段**（需求 P1-FR01：不显示为空值、不显示"未知"）。
合同层强制：`plain` 时 `git_base_commit`、`git_diff_digest` 必须为空；
`git` 时二者必须非空且有 `plain_manifest_digest` 为空。

### 5.3 来源（源码内容身份）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `snapshot` | `SnapshotRef` | 快照编号、用途（`analysis` / `prepare`）、内容身份 |
| `git_base_commit` / `git_diff_digest` | `str \| None` | 仅 `git` |
| `plain_manifest_digest` | `str \| None` | 仅 `plain` |
| `selected_paths` | `tuple[str, ...]` | 选定范围 |
| `exclusion_rules` | `tuple[str, ...]` | 排除规则 |
| `refetch_dependencies` | `tuple[str, ...]` | LFS、子模块等复取依赖 |

内容身份必须来自实际字节摘要；元数据（mtime）仅作为变化提示。

### 5.4 环境与执行来源

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `environment` | `EnvironmentRefFact` | 环境编号与修订、隔离方式、解释器身份、依赖集合摘要 |
| `execution_source` | `ExecutionSourceBinding` | 见下 |

`ExecutionSourceBinding`（架构文档第 12 节）：

| 字段 | 说明 |
| --- | --- |
| `registered_entry` | 已登记入口 |
| `entry_arguments` | 入口参数 |
| `cwd_mapping` | 工作目录映射 |
| `allowed_env_keys` | 允许的环境变量键 |
| `secret_refs` | 凭据引用（仅引用，不含正文） |
| `test_config_ref` | 测试配置引用 |
| `adapter_versions` | 适配器版本 |
| `resolved_input_digest` | 上述来源约束的摘要 |

**与 C 的 `source_binding_digest` 区分**：后者由 C 在 `start` 时按"期望 → 实际路径"映射计算。
两者不可合并。

### 5.5 计划与修订引用

| 字段 | 类型 |
| --- | --- |
| `plan_revision` | `PlanRevisionRef`（`revision_id` / `revision_no ≥ 1` / `digest`） |
| `acceptance_scope_revision` | `int` |
| `rule_versions` | `tuple[RuleVersionRef, ...]` |
| `template_versions` | `tuple[TemplateVersionRef, ...]` |
| `case_revisions` | `tuple[CaseRevisionRef, ...]` |

结构对齐 C 的 `PlanRevisionRefFact`，避免 C 二次构造。

### 5.6 档位、范围与门禁

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_tier` | `RunTier` | 档位 |
| `initial_driver` | `RunDriver` | 初始驱动 |
| `conclusion_ceiling` | `ConclusionCeiling` | 由档位派生并校验 |
| `template_required_case_ids` | `tuple[str, ...]` | `T`：模板适用必测下限 |
| `frozen_required_case_ids` | `tuple[str, ...]` | `M`：冻结必测范围 |
| `selected_case_ids` | `tuple[str, ...]` | `S`：本轮所选（含补充用例） |
| `skipped_scope` | `tuple[SkippedScopeEntry, ...]` | 未选与未执行项及原因 |
| `applicability_exclusions` | `tuple[ExclusionEntry, ...]` | 不适用项及理由 |

**不变量：** `T ⊆ M`；`full` 要求 `M ⊆ S` 且 `skipped_scope` 为空；
不适用项必须给出理由且不计入分母。

### 5.7 断言依据

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `assertion_bases` | `tuple[AssertionBasisEntry, ...]` | 每条用例的依据修订、三态、确认引用 |
| `frozen_cases` | `tuple[FrozenCase, ...]` | 冻结的用例与步骤 |

`AssertionBasisEntry`：`case_id`、`basis_revision`、`basis_text_digest`、
`assertion_basis_state`（`missing` / `present_unconfirmed` / `confirmed`）、`confirmation_refs`。

**不变量：** 处于 `missing` 的用例不得出现在 `frozen_required_case_ids` 中。

`FrozenCase` 携带：`case_id`、`revision`、`layer`、`required`、
`independent_verification`（发布必填）、`mock_scope`、`importance`、`steps`、`case_links`。

### 5.8 授权前置、模型出站与失效规则

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `authorization_requirements` | `tuple[AuthorizationRequirement, ...]` | 声明需要授权的动作；**授权本身由 C 执行并持久化** |
| `model_outbound_policy_revision` | `int \| None` | 模型出站策略修订 |
| `source_snippets_enabled` | `bool` | 默认 `false` |
| `invalidation_rules` | `tuple[InvalidationRule, ...]` | 哪些来源变化会使本快照失效 |
| `blocking_reasons` | `tuple[BlockingReason, ...]` | `status = blocked` 时必须非空 |
| `gaps` | `tuple[GapEntry, ...]` | 缺口登记 |

## 6 失效规则

| 变化 | 结果 |
| --- | --- |
| 源码内容身份变化 | 派生结论"依据需重新准备"；返回原意图，不写入新字节 |
| 绑定修订变化 | 同上 |
| 计划、规则、模板、验收范围修订变化 | 同上 |
| 环境修订变化 | 同上 |
| 非空 `blocking_reasons` | 快照 `status = blocked` |

派生结论由应用用例按 `invalidation_rules` 计算；快照自身状态不变。

架构文档第 11 节：

> 同请求期间源码已变则返回原意图及"依据需重新准备"，不能把新字节塞进旧意图。

## 7 与下游的交接

### 7.1 B → C（`RunFact` 字段映射）

| C 的字段 | B 的来源 |
| --- | --- |
| `tier` | `run_tier` |
| `driver` | `initial_driver` |
| `conclusion_ceiling` | `conclusion_ceiling` |
| `plan_revision` | `plan_revision` |
| `rules_revision` | `rule_versions` |
| `environment_ref`、`environment_isolated` | `environment` |
| `required_scope` | `frozen_required_case_ids`（`M`） |
| `selected_scope` | `selected_case_ids`（`S`） |

### 7.2 B → D

D 只读展示，不重算。`conclusion_ceiling`、`effective_assertion_basis_state` 由 B 提供。

### 7.3 B → A

保存经 `WorkspaceUnitOfWork` 与 `RecordRepository`，按准确修订读取。
具体端口签名见 `docs/接口对接/B-A-端口与保存需求.md`。

## 8 验证设计

| 层次 | 内容 |
| --- | --- |
| 合同测试 | 三份夹具（成功、失败、未知）可被解析 |
| 词汇表锁定 | `domain/planning/plans.py` 与 `contracts/prepared_run.py` 的枚举值集合一致 |
| 派生校验 | `conclusion_ceiling` 与 `run_tier` 不匹配时解析失败 |
| 形态校验 | `plain` 夹具不含任何 Git 字段；`git` 夹具必须含基准提交与差异摘要 |
| 门禁校验 | `T ⊄ M`、`full` 下 `M ⊄ S`、`full` 下存在跳过项、`missing` 依据进入 `M` 均解析失败 |
| Schema 同步 | 重生成 Schema 后 `git diff --exit-code` 为空 |

## 9 交付物

| 产物 | 路径 |
| --- | --- |
| 领域词汇表 | `src/aitest/domain/planning/plans.py` |
| 跨包合同 | `src/aitest/contracts/prepared_run.py` |
| 生成 Schema | `src/aitest/contracts/schemas/PreparedRun.json`（由脚本生成） |
| 三份夹具 | `tests/contracts/fixtures/prepared_run/{success,failure,unknown}.json` |
| 合同测试 | `tests/contracts/test_prepared_run.py` |
| 词汇表锁定测试 | `tests/contracts/test_run_vocabulary.py` |

## 10 待确认项

| 编号 | 事项 | 关联 |
| --- | --- | --- |
| C-01 | C 侧夹具 `conclusion_ceiling` 取值应为 `passable` | `B-C` 对接文档 |
| C-02 / C-03 | C 侧 `driver`、`conclusion_ceiling` 建议改为枚举 | 同上 |
| C-06 | `RunFact` 是否增加非空 `intent_id` | 同上 |
| — | `domain/planning/plans.py` 中 `RunMode` → `RunTier`、`Driver` → `RunDriver` 的重命名 | 本文档第 4 节 |

## 11 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-25 | 0.1 | 初稿 |
