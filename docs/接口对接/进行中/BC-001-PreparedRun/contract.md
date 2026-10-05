---
contract_id: BC-001
title: PreparedRun 与运行词汇表
provider: B
consumer: C
contract_version: "0.17"
contract_status: reviewing
provider_implementation: partial
consumer_implementation: partial
verification_status: not_run
last_verified_commit: null
blockers: []
next_owner: C
next_action: 接通权威准备与 C 默认启动/运行中修订链，完成真实环境验收；DEC-007/008/009 已由负责人裁定。
---

# B-C 跨包合同确认：PreparedRun 与运行词汇表

版本：0.17（2026-10-05 初始步骤内容准确仓储读取，验证记录另行登记）
日期：2026-09-26
提出方：B 包（项目与计划）
接收方：C 包（执行与证据）
状态：**旧版签署与交接历史保留；本次新增合同已由负责人裁定，默认消费与真实环境验收仍在进行**
依据：一期架构文档《01-项目与计划》第 4、11、12 节；《02-执行与证据》；组长《一期工程四部分拆分与低对接实施方案》第 3 节跨包合同表；需求文档 P1-FR07、P1-AC19/AC20/AC31
对照对象：`origin/feat/package-c-execution`，commit `d60781d`，文件 `src/aitest/contracts/execution_facts.py`；B 侧本轮分支 `feature/contract-b-c-isolation-mode`

---

## 1 目的

本次增量约束：默认 `prepare_run` 不信任请求携带的“无缺口”、内容摘要或人工确认标签。登记新准备意图前，按冻结仓储修订核对项目归属、计划/用例/规则的完整正文摘要、模板资源摘要、源码清单内容身份、计划引用与验收范围、冻结步骤与用例正文、确认记录与准确依据修订。锁内提交前重复核对，失败返回 `blocked` 与 `basis_unverified`，不消耗新准备意图。请求摘要冲突仍优先于来源变化；人工补确认独立追加，不能改写冻结计划或用例，不能推导执行或核验通过。环境/执行来源的实际运行核对仍由 C 在启动时完成，材料可核不代表真实环境验收。

默认准备成功时，`prepared_run@1` 完整合同 DTO 与 `preparation_record` 同次提交，`created_at_commit` 为该批真实提交边界。同准备身份重传从准确仓储修订回读原 DTO，不能用当前请求字段重新拼接冻结快照；缺少原 DTO 的旧意图显式要求重新准备。实际源码核对在短事务外执行，事务内复核保存的准确引用。新增受控 `confirm_basis` 只接受准确用例/依据修订及正文摘要，确认标识按项目和持久意图派生，单条不可变确认与该意图一次提交；重传回原确认，同键换输入冲突。新增协议动作按能力表协商，代理入口不得自签确认。

C 包已先行完成 `ExecutionFacts` v1.0 与三份夹具。其中若干字段的**语义所有权属于 B 包**（档位、驱动、结论上限、必测范围），
C 包按文档推导时出现了一处**取值错误**和两处**类型未闭合**。本文档提出最小改动方案，避免 D 包做报告时面对多套口径。

C 包的字段分层与取值总体符合架构约定：`evidence_level: EvidenceLevelFact | None` 的可空设计正确
（仅完整验证派生等级），`tier` 的枚举值与 B 包一致。本文档仅处理剩余不一致。

---

## 2 C 侧现状（逐字引用，未转述）

```python
# 第 21 行
class RunTierFact(StrEnum):
    FULL = "full"
    QUICK = "quick"
    ON_DEMAND = "on_demand"

# 第 197 行
class PlanRevisionRefFact(ContractModel):
    revision_id: str
    revision_no: int = Field(ge=1)
    digest: str

# 第 277 行
class RunFact(ContractModel):
    run_id: str
    run_revision: int = Field(ge=0)
    origin_workspace_id: str
    tier: RunTierFact
    driver: str
    conclusion_ceiling: str
    plan_revision: PlanRevisionRefFact
    environment_ref: str
    environment_isolated: bool
    rules_revision: str
    control_state: RunControlStateFact
    evidence_level: EvidenceLevelFact | None = None
    ...
    required_scope: tuple[str, ...] = Field(default_factory=tuple)
    selected_scope: tuple[str, ...] = Field(default_factory=tuple)
    source_binding_digest: str | None = None

# 第 336 行（AttemptFact 内）
    intent_id: str | None = None
```

三份夹具（`tests/contracts/fixtures/execution_facts/{success,failure,unknown}.json`）第 19—21 行**逐字相同**：

```json
"tier": "full",
"driver": "planned",
"conclusion_ceiling": "full",
```

> **本节记录的是评审当时的现象（修订前）。** 该冲突已由 C-01 处理：现在这三份夹具的
> `conclusion_ceiling` 均为 `passable`（`quick.json` 为 `partial`），字段类型也已由裸 `str` 改为
> `ConclusionCeilingFact`。逐字记录保留在此，供追溯改动原因，**不代表现状**。

---

## 3 冲突与缺口清单

| 编号 | 字段 | C 侧现状 | 规范要求 | 影响 | 建议动作 | 归属 |
| --- | --- | --- | --- | --- | --- | --- |
| **C-01** | `conclusion_ceiling` | 夹具取值为 `"full"` | 只能取 `partial` / `passable` | **取值不符合规范。** `conclusion_ceiling` 是结论上限，由档位派生：quick/on_demand → `partial`，full → `passable`。写为 `"full"` 会使 D 无法判断本次运行能否判定完整通过；又因该字段为裸 `str`，校验不报错，缺陷会静默传播 | 夹具改为 `"passable"`；字段改为枚举 | C 改夹具，B 供枚举 |
| **C-02** | `driver` | `str`，夹具 `"planned"` | `planned` / `stepwise` | 值目前正确，但类型未闭合，未来可写入任意字符串；D 依赖该字段判断"是否逐步驱动" | 改为 `RunDriverFact(StrEnum)` | C 改，B 供枚举 |
| **C-03** | `conclusion_ceiling` | `str` | `partial` / `passable` | 同 C-02 | 改为 `ConclusionCeilingFact(StrEnum)` | C 改，B 供枚举 |
| **C-04** | `tier` 枚举归属 | C 自定义 `RunTierFact` | 架构 01 第 4 节：档位由 B 定义与冻结 | 值一致（`full/quick/on_demand`），无功能影响；但"同一枚举不得另起定义"（`AGENTS.md` 第 1.2 条） | 保留双声明，**加漂移锁定测试**（见 4.4） | 共同 |
| **C-05** | `required_scope` / `selected_scope` | `tuple[str, ...]`，语义未标注 | M = 冻结必测范围；S = 本轮所选范围 | 字段可承载，但未标明对应关系；`T`（模板下限）不在 ExecutionFacts 中，属 B 内部校验输入 | 在文档与代码注释中标注 `required_scope = M`、`selected_scope = S` | 共同 |
| **C-06** | `intent_id` 位置 | 仅存在于 `AttemptFact`，且 `str \| None = None` | 架构 01 第 11 节：`intent_id` 是 prepare→start→run 的业务身份 | Run 级无 `intent_id`，**无法从一次运行追溯到产生它的准备意图**，违反实施方案第 3 节"B→C 来源与计划修订匹配"的交接门槛 | 建议 `RunFact` 增加 `intent_id: str`（非空） | C 决定，B 确认 |
| **C-07** | `plan_revision` 重复 | 顶层 `ExecutionFacts` 与 `RunFact` 各有一份 | 同一快照内应一致 | 两份可能不一致且无守卫 | 增加一致性校验，或明确注释"必须逐字节相同" | C 改 |
| **C-08** | 档位覆盖 | 三份夹具 `tier` 全为 `"full"` | 需求 P1-FR07：quick/on_demand **不打分** | "非完整验证不派生证据等级"这条合同行为**没有被任何夹具覆盖** | 建议增加第 4 份夹具 `quick.json`（`tier: "quick"`、`conclusion_ceiling: "partial"`、`evidence_level: null`） | C 决定，B 供快速检查的期望值 |
| **C-09** | `RunTierFact` 声明重复 | `execution_facts.py` 第 21 行声明 `RunTierFact`（`FULL`/`QUICK`/`ON_DEMAND`） | 实施方案第 2 节：档位属 B 包"项目/源码/环境上下文、模板规则、计划、**档位**和准备门禁"；实施方案第 3 节：跨包字段变化"只由合同所有者修改 Schema/夹具并做兼容检查" | 值集合当前一致，**无功能影响**；但两处独立定义会长期漂移。注意：`driver` 与 `conclusion_ceiling` 在 C 侧为裸 `str`，B 侧已有对应枚举（`RunDriverFact`/`ConclusionCeilingFact`），**不存在重复** | 运行时词汇表三枚举收敛为单一声明点，C 侧删除本地 `RunTierFact` 并改为 import（见第 11 节） | C 决定，B 提供声明点 |

> **C-01 为本文档唯一的语义缺陷**，其余为类型闭合与一致性建议。

---

## 4 建议方案

### 4.1 词汇表的规范定义（B 侧，`domain/planning/plans.py`）

> **状态：已完成**（提交 `df0f557`）。以下为现行代码，C 可直接按值对齐。

B 包已将原 `RunMode` / `Driver` 改名为架构文档使用的名称，并补齐 `ConclusionCeiling`：

```python
class RunTier(StrEnum):            # 原 RunMode
    QUICK = "quick"
    ON_DEMAND = "on_demand"
    FULL = "full"

class RunDriver(StrEnum):          # 原 Driver
    PLANNED = "planned"
    STEPWISE = "stepwise"

class ConclusionCeiling(StrEnum):
    PARTIAL = "partial"            # quick / on_demand
    PASSABLE = "passable"          # full
```

**派生关系由 B 的领域规则唯一计算，C 仅读取结果：**

| `RunTier` | `ConclusionCeiling` | 证据等级 |
| --- | --- | --- |
| `quick` | `partial` | 不打分（`evidence_level = None`） |
| `on_demand` | `partial` | 不打分（`evidence_level = None`） |
| `full` | `passable` | A / B / C / D |

对应需求 P1-FR07 原文："**结论上限只由档位决定，不由覆盖多少或执行方式决定**"。

### 4.2 C 侧最小改动

```python
class RunDriverFact(StrEnum):
    PLANNED = "planned"
    STEPWISE = "stepwise"

class ConclusionCeilingFact(StrEnum):
    PARTIAL = "partial"
    PASSABLE = "passable"

class RunFact(ContractModel):
    tier: RunTierFact
    driver: RunDriverFact                      # 原 str
    conclusion_ceiling: ConclusionCeilingFact  # 原 str
    ...
```

夹具同步：`"conclusion_ceiling": "full"` → `"conclusion_ceiling": "passable"`（三份文件各一处）。

> 改动向后兼容：`"planned"` 与 `"partial"`、`"passable"` 均为合法字符串值，仅 `"full"` 会被新枚举拒绝，
> 即为预期效果。

### 4.3 不采用单一定义的原因

`contracts/` 与 `domain/` 是两个不同的层：C 包在《01-C包一期实施计划》中已写明"**合同层不直接绑定领域对象**"，
`tests/architecture/test_boundaries.py` 也禁止 `domain/` 反向依赖 `contracts/`。

因此**不强行合并成一处**，而是各自在所属层声明（`domain/planning/plans.py` 与 `contracts/`），
用下面的测试锁死值集合一致。这比"大家记得同步"可靠，且不需要现在就做分层决策。

### 4.4 漂移锁定测试（建议放在 `tests/contracts/`）

```python
def test_run_vocabulary_matches_between_domain_and_contract() -> None:
    """跨包运行词汇表必须逐值一致；任一侧改动而另一侧未跟进时立即失败。"""
    from aitest.contracts.execution_facts import ConclusionCeilingFact, RunDriverFact, RunTierFact
    from aitest.domain.planning.plans import ConclusionCeiling, RunDriver, RunTier

    assert {t.value for t in RunTier} == {t.value for t in RunTierFact}
    assert {d.value for d in RunDriver} == {d.value for d in RunDriverFact}
    assert {c.value for c in ConclusionCeiling} == {c.value for c in ConclusionCeilingFact}


def test_conclusion_ceiling_is_derived_only_from_tier() -> None:
    from aitest.domain.planning.plans import ConclusionCeiling, RunTier, conclusion_ceiling_for

    assert conclusion_ceiling_for(RunTier.QUICK) is ConclusionCeiling.PARTIAL
    assert conclusion_ceiling_for(RunTier.ON_DEMAND) is ConclusionCeiling.PARTIAL
    assert conclusion_ceiling_for(RunTier.FULL) is ConclusionCeiling.PASSABLE
```

---

## 5 PreparedRun → RunFact 字段映射（C 不必再猜）

C 的 `RunFact` 中凡是"应由 B 冻结"的字段，都是按文档推导的。下表消除歧义：
**左列由 B 的 `PreparedRun` 提供，C 直接读取，不做二次推导。**

| `RunFact` 字段 | 来源 | 说明 |
| --- | --- | --- |
| `tier` | `PreparedRun.run_tier` | B 冻结 |
| `driver` | `PreparedRun.initial_driver` | B 冻结；运行中的切换由 C 记入 `DriverChange`，不覆盖初始值 |
| `conclusion_ceiling` | `PreparedRun.conclusion_ceiling` | B 计算，**C 不得自行由覆盖度推导** |
| `plan_revision` | `PreparedRun.plan_revision` | 结构须与 `PlanRevisionRefFact` 一致：`revision_id` / `revision_no ≥ 1` / `digest` |
| `rules_revision` | `PreparedRun` 冻结的规则修订标识 | B 发布 `RuleVersion` 时产出 |
| `environment_ref`、`environment_isolated` | `ResolvedEnvironment.environment_id/revision`、`isolation_mode` | 默认 venv；**显式不隔离是合法值，不等于缺配置**（需求 P1-FR07） |
| `required_scope` | `PreparedRun.frozen_required_case_ids`（= M） | 冻结必测 |
| `selected_scope` | `PreparedRun.selected_case_ids`（= S） | 本轮所选，含补充用例 |
| `source_binding_digest` | **C 在 start 时计算** | 见第 6 节 |
| `evidence_level` | D 派生 | 仅 `full` 有值；C 只透传 |

**`PreparedRun` 尚未定稿**，上述字段名为草案，确认后以本目录下一版为准。
冻结前 C 可继续使用夹具，无需阻塞等待。

---

## 6 两个 digest 的区分（易混点）

架构文档第 12 节把执行来源核对拆成两个不同的摘要，归属不同、时机不同：

| 摘要 | 生成者 | 时机 | 含义 |
| --- | --- | --- | --- |
| `resolved_input_digest` | **B**（prepare） | 准备运行时 | 冻结解释器身份、模块范围、工作目录映射、已登记入口与参数、允许的环境变量键、测试配置引用、依赖集合与适配器版本 |
| `source_binding_digest` | **C**（start） | 启动时 | 在固定 workdir 中解析出的"期望 → 实际路径"映射摘要 |

**两者不可合并、不可互相替代。** 前者证明计划使用的来源，后者证明实际加载的来源。
C 的 `RunFact.source_binding_digest: str | None` 归属正确。

---

## 7 待 C 确认问题清单

请逐条回复，便于记录：

1. **C-01**：`conclusion_ceiling` 夹具取值 `"full"` 是否确认为笔误？是否同意改为 `"passable"`？
2. **C-02 / C-03**：是否同意将 `driver`、`conclusion_ceiling` 由 `str` 改为枚举？
3. **C-04**：是否接受"各自声明 + 漂移锁定测试"的方案，而不是合并为单一定义？
4. **C-06**：`RunFact` 是否增加非空的 `intent_id`？若保持只在 `AttemptFact`，请说明如何从运行追溯到准备意图。
5. **C-07**：顶层与 `RunFact` 内的 `plan_revision` 是否增加一致性校验？
6. **C-08**：是否愿意增加第 4 份夹具覆盖"quick 档不打分"？
7. **接收形式**：`PreparedRun` 定稿后，C 希望以何种形式接收——Python `Protocol`、Pydantic 合同，还是 JSON 夹具？
8. **C-09**：运行时词汇表三枚举（`RunTierFact` / `RunDriverFact` / `ConclusionCeilingFact`）是否同意收敛为单一声明点？若同意，选第 11.2 节的方案甲还是方案乙？若不同意，请说明理由。

### 7.1 C 确认结论（2026-09-26）

1. **C-01**：确认夹具中的 full 为笔误，同意改为 passable。
2. **C-02 / C-03**：同意 driver 和 conclusion_ceiling 使用 B 合同枚举。
3. **C-04 / C-09**：不采用 contracts 层各自声明，选择第 11.2 节方案甲；删除 C 的 RunTierFact，RunTierFact、RunDriverFact、ConclusionCeilingFact 统一从 PreparedRun 合同导入。
4. **C-05**：required_scope 对应 PreparedRun.frozen_required_case_ids，即 M；selected_scope 对应 PreparedRun.selected_case_ids，即 S。
5. **C-06**：同意 RunFact 增加非空 intent_id，来源为 PreparedRun.intent_id。
6. **C-07**：顶层 plan_revision 与 RunFact.plan_revision 均保留，并增加模型一致性校验。
7. **C-08**：同意增加 quick.json，tier 为 quick、conclusion_ceiling 为 partial、evidence_level 为 null。
8. **接收形式**：使用 Pydantic 合同、生成 JSON Schema，以及 success、failure、unknown、quick 四类夹具。
9. **补充映射**：RunFact.driver 来源为 PreparedRun.initial_driver；运行中的驱动切换不得覆盖冻结初始值。

### 7.2 C 对 C-10 的结论（2026-09-26）

C-10：同意 environment_isolated 从 bool 改为三态，不允许用 bool 折叠 venv、none 和 unmanaged。

要求：

- B 合同所有者增加隔离方式合同枚举，建议名称为 EnvironmentIsolationModeFact，取值固定为 venv、none、unmanaged。
- B 的 EnvironmentRefFact.isolation_mode 使用该枚举，不再使用裸 str。
- B 完成合同变更并合入 develop 后，C 将 RunFact.environment_isolated: bool 改为 environment_isolation_mode: EnvironmentIsolationModeFact。
- C 同步更新 success、failure、unknown、quick 四份夹具、ExecutionFacts JSON Schema 和合同测试。
- none 表示用户显式选择不隔离，是合法事实，不得当作缺配置或自动降级证据等级。

当前状态：C 已确认三态语义；B 合同枚举尚未提供，因此本契约 PR 暂不修改 C-10 字段实现。

### 7.3 B 对 C-10 的响应（2026-09-26）

B 同意 C 的三态结论，并已完成合同侧改动，与 C 的要求逐条对应：

| C 的要求 | B 的改动 | 位置 |
| --- | --- | --- |
| 增加隔离方式合同枚举，取值 `venv` / `none` / `unmanaged` | 新增 `EnvironmentIsolationModeFact` | `src/aitest/contracts/prepared_run.py` |
| `EnvironmentRefFact.isolation_mode` 改用该枚举，不再用裸 `str` | 字段类型由 `str` 改为 `EnvironmentIsolationModeFact` | 同上 |
| 值集合不得与领域层漂移 | `tests/contracts/test_project_vocabulary.py` 锁定合同枚举与 `aitest.domain.project.context.IsolationMode` 一致 | `tests/contracts/` |
| 生成的 Schema 需同步 | 已重新生成 `contracts/schemas/PreparedRun.json`，`isolation_mode` 现为枚举，非法取值在校验层被拒绝 | `src/aitest/contracts/schemas/` |

**验收证据**：合法值 `venv`、`none`、`unmanaged` 均通过；非法值 `bogus`、`True`、空串、`VENV`（大小写不符）均被 `ValidationError` 拒绝。
改动前该字段为裸 `str`，非法取值不会报错，与 C-01 的静默传播属同一类风险。

**B 侧遗留说明**：`PreparedRun` 的三份既有夹具取值为 `venv` / `venv` / `none`，改动后在枚举范围内，无需修改。

C 收到本节后即可按第 7.2 节执行：把 `RunFact.environment_isolated: bool` 改为
`environment_isolation_mode: EnvironmentIsolationModeFact`，并同步四份夹具、Schema 与合同测试。

### 7.4 C 对 C-10 的实施记录（2026-09-26）

C 已按第 7.2、7.3 节完成本地实现：

- RunFact.environment_isolated: bool 已替换为 environment_isolation_mode: EnvironmentIsolationModeFact。
- environment_isolation_mode 直接从 prepared_run.py 导入，不在 C 侧重复声明枚举。
- success.json 使用 venv。
- failure.json 使用 venv。
- unknown.json 使用 none，保留"显式不隔离是合法事实"的合同行为。
- quick.json 使用 unmanaged，覆盖第三个合法取值。
- ExecutionFacts JSON Schema 已重新生成。
- 合同测试已覆盖三态值，并继续覆盖 quick 的 conclusion_ceiling=partial、evidence_level=null。
- 本地验证通过后，进入 C 侧契约 PR Review。

---

## 8 兼容性影响

| 影响面 | 说明 |
| --- | --- |
| C 侧现有三份夹具 | 各改 1 处（`conclusion_ceiling`） |
| `tests/contracts/test_execution_facts.py` | 若断言了具体取值需同步；当前测试只做加载验证，预计无需改动 |
| 已生成 Schema `contracts/schemas/ExecutionFacts.json` | 需重新生成（`python scripts/generate_schemas.py`），并确认 CI 的 `git diff --exit-code` 通过 |
| D 包 | **受益方**：`ConclusionCeiling` 成为闭合枚举后，报告聚合不再需要猜测取值 |
| B 包 | 词汇表改名与 `ConclusionCeiling` 已完成（提交 `df0f557`）；`PreparedRun` 其余字段见第 13 节缺口表 |

---

## 10 B 侧对象边界（2026-09-25 新增，C 读取时须知）

以下三项来自 B 包 Sprint 1 的领域层实现，影响 C 读取 `PreparedRun` 时的字段理解。**B 侧已完成，无需 C 改动代码**，但 C 若按对象名直连 B 的领域层，需按本节对齐。

### 10.1 环境：声明与解析事实分离

B 侧把环境拆成两个对象，**C 只应接触后者**：

| 对象 | 层 | 内容 | 何时产生 |
| --- | --- | --- | --- |
| `EnvironmentRef` | `domain/project/context.py` | 声明与策略：隔离方式、解释器**要求**、依赖声明来源、数据来源/隔离/复位、超时、网络目标、按用途 `SecretRef` | 用户登记环境时 |
| `ResolvedEnvironment` | 同上 | 已解析**事实**：`interpreter_identity`、`dependency_set_digest`、`isolation_mode` | prepare 时刻由应用用例解析 |

`ResolvedEnvironment` 与 C 侧 `RunFact.environment_ref` / `environment_isolated` 的取值来源对应；
`contracts.EnvironmentRefFact` 的三个必填字段（`interpreter_identity`、`dependency_set_digest`、`isolation_mode`）来自 `ResolvedEnvironment`，**不来自 `EnvironmentRef`**。

`isolation_mode` 为三态：`venv`（默认）/ `none`（显式不隔离）/ `unmanaged`。**`none` 是合法事实，不得当作缺配置或据此自动降级证据等级。**

### 10.2 交付说明：自述与验证事实分离

`Delivery` 的构造参数已调整：`completed` / `incomplete` 由顶层字段移入 `self_report`（`SelfReport` 对象）。
原名称保留为只读属性，**读取方无需改动**；构造方需改为 `self_report=SelfReport(completed=..., incomplete=...)`。

原因：需求 P1-FR02 要求"开发自述完成与测试验证完成分别展示"，原结构无法区分两者。
`verified_in_scope` 只能来自执行事实，自述非空不会使其非空。

### 10.3 项目对象改名

`domain/project/context.py` 中的 `Project` 已由 `LocalProject` 取代（`Project` 无生产代码引用，同时保留会形成两套项目身份）。
`LocalProject.project_id` 是 `local_project_id` 的权威别名，取值相同。

### 10.4 依赖关系记录

依赖边由 `Dependency` 记录承载（使用者 → 提供者），是依赖关系的**唯一权威来源**；
`Module` 上不再保留可写的依赖字段。允许循环，拒绝自环；推导得出的边必须带 `source`，并须在报告中标注"推导结果，可能不完整"。

---

## 11 关于 `RunTierFact` 声明重复的处理建议（2026-09-25 新增）

### 11.1 依据

实施方案第 3 节「只冻结四类跨包合同」：

> 每包改自己的内部字段无需会签；**跨包字段变化只由合同所有者修改 Schema/夹具并做兼容检查**。

`PreparedRun` 属于四类跨包合同之一，所有者是 B；运行档位是其字段。同时实施方案第 2 节把"**档位**"列入 B 包的独占责任。

因此 C-09 的归属是明确的：**运行时词汇表的声明点在 B 侧，不在 C 侧。**

### 11.2 声明点建议

`aitest.contracts` 是同一个包，`prepared_run.py` 与 `execution_facts.py` 位于同一目录。为避免跨包字段出现两个定义，列出两种方案，C 可择一或提出第三种：

| 方案 | 做法 | 代价 | 对 C 的改动 |
| --- | --- | --- | --- |
| **甲（最小）** | `execution_facts.py` 删除本地 `RunTierFact`，改为 `from aitest.contracts.prepared_run import RunTierFact` | 合同层内多一条 import | 删 4 行、加 1 行 |
| **乙（独立声明点）** | 新建 `contracts/run_vocabulary.py` 作为三个枚举的唯一声明点，`prepared_run.py` 与 `execution_facts.py` 均从该模块 import | 需同步调整 B 侧已冻结的 `prepared_run.py` 与 `tests/contracts/test_run_vocabulary.py` | 删 4 行、加 1 行；B 侧同步 |

**B 侧当前状态**：`RunTierFact` / `RunDriverFact` / `ConclusionCeilingFact` 已声明于
`src/aitest/contracts/prepared_run.py`，并由 `tests/contracts/test_run_vocabulary.py` 锁定与
`domain/planning/plans.py` 的值集合一致。**B 侧不修改 C 的文件**；若选方案乙，由 B 与 C 各自改本包文件。

### 11.3 与 `domain/planning/` 那份声明的区别

`domain/planning/plans.py` 中的 `RunTier` / `RunDriver` / `ConclusionCeiling` 是**另一层**的声明，不是重复：

- `tests/architecture/test_boundaries.py` 禁止 `domain/` import `contracts/`，故两层必须各自声明；
- 两层由 `tests/contracts/test_run_vocabulary.py` 锁定值集合一致。

**这一层结构保持不变**，本节的收敛建议只针对 `contracts/` 层的两份重复。

### 11.4 风险等级

**不阻断合并。** 两份声明位于不同文件，git 三方合并不会产生冲突，两处都会保留。
风险是长期维护隐患：任一侧新增取值而另一侧未跟进时，不会立即失败，只会静默分歧。

---

## 12 确认记录

> 本目录的处理规则见 `docs/接口对接/README.md`；已确认内容不得静默覆盖，变更必须记录版本、影响和迁移方式。

| 日期 | 版本 | 变更 | B 包 | C 包 |
| --- | --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿，提出 C-01—C-08 | 已提出 | 待确认 |
| 2026-09-25 | 0.2 | 4.1 节词汇表改名与 `ConclusionCeiling` 已完成；新增第 10 节 B 侧对象边界 | 已完成 | 待确认 |
| 2026-09-25 | 0.3 | 新增 C-09 与第 11 节；新增第 13 节 B→C 交接定义与待办状态 | 已完成 | 待确认 |
| 2026-09-26 | 0.4 | C 确认 C-01—C-09，选择方案甲；确认 RunFact.intent_id、quick 夹具、scope 说明和 plan_revision 校验 | 已同意方案甲 | 已确认 |
| 2026-09-26 | 0.5 | C 确认 C-10 使用三态隔离方式；等待 B 补充 EnvironmentIsolationModeFact 后由 C 修改 RunFact 与夹具 | 待补充枚举 | C 已确认语义 |
| 2026-09-26 | 0.6 | B 补充 `EnvironmentIsolationModeFact`、改用枚举、加漂移锁定测试并重生成 Schema；见第 7.3 节 | 已完成 | 待 C 同步 RunFact |
| 2026-09-26 | 0.7 | C 将 RunFact 改为三态隔离方式，四份夹具覆盖 venv/none/unmanaged，重生成 Schema 并补合同测试 | 已完成 | 已完成 |
| 2026-09-29 | 0.8 | C 确认 SourceSnapshot 分工与 PreparedRun 功能夹具四条场景、plain Git 键省略和 snapshot_revision 取值 | 待组长确认字段口径 | 已确认 |
| 2026-09-30 | 0.9 | 项目负责人确认 SourceSnapshot 采用“规则归 B、类位置留 C、端口适配归 A”；实现缺口转入 AB-001，不改变 PreparedRun 冻结合同 | 已裁定 | 已确认兼容边界 |
| 2026-10-03 | 0.10 | 新增第 16 节：运行中修订的落盘与消费（B-05）。B 侧领域门禁与决策结果已交付，落盘、步骤边界应用、失效清单与 runner 消费归 C | 已提出 | **待确认** |
| 2026-10-03 | 0.11 | 新增第 17 节：`running` 快照与“半程运行”草案（B 侧提交、**待 C 确认**）。含实测缺口清单（三个状态枚举的未覆盖取值）、中间态十条字段语义、三份半程夹具设计与六条证据口径。**不改 `ExecutionFacts` 任何字段** | 草案已提交 | **待确认** |

---

## 13 B→C 交接定义（2026-09-25 新增）

### 13.1 B 侧交付物

B 对外的唯一业务交付是 `PreparedRun`。C 只读取它，不做二次推导（架构文档《01-项目与计划》第 4 节）：

> 档位与结论上限的对应**由领域判定器计算，界面与报告只读取结果**。

| 交付物 | 位置 | 状态 |
| --- | --- | --- |
| `PreparedRun` 合同 | `src/aitest/contracts/prepared_run.py` | **已冻结**（Sprint 0） |
| 生成 Schema | `src/aitest/contracts/schemas/PreparedRun.json` | **已生成** |
| 成功夹具 | `tests/contracts/fixtures/prepared_run/success.json` | **已提交** |
| 失败夹具 | `tests/contracts/fixtures/prepared_run/failure.json` | **已提交** |
| 未知夹具 | `tests/contracts/fixtures/prepared_run/unknown.json` | **已提交** |
| 领域词汇表 | `src/aitest/domain/planning/plans.py` | **已冻结** |
| 项目上下文领域对象 | `src/aitest/domain/project/context.py` | **已提交**（见第 10 节） |

**C 无需自行定义以下内容**，可直接引用 B 侧的声明：

- 档位、驱动、结论上限的枚举与取值（C-09）
- `PreparedRun` 的字段名与结构
- 三份夹具所固定的准备态样例

### 13.2 B 侧已在 Sprint 1 落地的对象边界

C 读取 `PreparedRun` 时会遇到下列对象，其边界在第 10 节已逐项说明，此处只作索引：

| 对象 | 要点 |
| --- | --- |
| `EnvironmentRef` / `ResolvedEnvironment` | 声明与解析事实分离；`PreparedRun` 携带的是**解析结果**；`isolation_mode` 为三态（`venv` / `none` / `unmanaged`），不是布尔 |
| `LocalProject` | 取代原 `Project`；`project_id` 是 `local_project_id` 的权威别名 |
| `Delivery` | `completed` / `incomplete` 移入 `self_report`；`verified_in_scope` 只能来自执行事实 |

### 13.3 B 侧尚未交付、C 需知悉的缺口

| 缺口 | 原因 | 对 C 的影响 |
| --- | --- | --- |
| 产生 `PreparedRun` 的应用用例 | 属 Sprint 2，依赖 A 的 `WorkspaceUnitOfWork` / `RecordRepository` 端口；A 的 `application/ports.py` 目前仍为文档字符串占位 | 暂时只能读合同与夹具，无法取得真实准备结果 |
| `PreparedRun` 的功能夹具 | 三份夹具为 Sprint 0 的合同级样例，未覆盖 Sprint 1 新增的项目上下文对象 | 与 C 的用例可能不完全对应 |
| `SourceSnapshot` 字段实现 | 项目负责人已裁定规则归 B、唯一类位置保留在 `domain/execution/sources.py`、A 实现端口；现有模型仍缺完整字段 | C 等 B 冻结字段并评审执行兼容，不另建模型 |

以上三项均**不要求 C 现在动手**，登记在此以便交接时核对。

### 13.4 确认

依据接口对接目录的处理规则，本文件确认后即为 B 与 C 之间的合同依据。

```text
对接：B 包（项目与计划） ↔ C 包（执行与证据）
文件：docs/接口对接/进行中/BC-001-PreparedRun/contract.md
版本：0.8

确认：[x] B 包（项目与计划）    日期：2026-09-26
确认：[x] C 包 赵        日期：2026-09-26

未决项：无。C-10 的 EnvironmentIsolationModeFact 已由 B 补充并由 C 同步到 RunFact 和四份夹具；等待 C 侧契约 PR 的 CI 与合并。
```

---

## 14 C 对 SourceSnapshot 与 PreparedRun 功能夹具的确认（2026-09-29）

### 14.1 SourceSnapshot 归属

C 同意 C-Q08 / B-Q01 按以下边界处理：

- SourceSnapshot 类定义暂留在 domain/execution/sources.py，不拆文件、不复制第二套模型。
- 快照建立时机、purpose（analysis / prepare）、排除规则、内容身份算法、Git/plain 身份统一、有效性及是否需重新准备由 B 唯一决定。
- C 只消费 B 冻结的快照事实，并核对实际物化执行来源；不自行定义快照规则。
- 缺失字段由 B 提供最终字段名、类型和口径，C 评审执行兼容后在唯一模型中补齐，包括 git 基准提交、plain 文件清单摘要、工作目录范围、排除规则、差异摘要、内容引用、创建时间、复取依赖与可复取范围；A 同步端口、存储和物化适配。
- Git/plain 形态保持互斥；plain 省略 Git 键，不写 null、空串或未知。
- ExecutionSourceVerification 继续只记录实际执行来源核对事实。

### 14.2 PreparedRun 功能夹具第 7 节确认

1. 四个场景对当前 C start 路径足够：git/plain 覆盖可执行路径，blocked 覆盖禁止启动，needs_reprepare 覆盖旧意图不得复用。quick/on_demand 已由 B-C 合同和 C 侧合同测试覆盖；不需要阻断当前交接。
2. plain.json 省略 Git 键符合 C 的解析预期。C 按键不存在处理，不补造仓库、分支、提交或未知值。
3. needs_reprepare 使用 source_kind=snapshot_revision 对来源快照变化够用。C 对任何非空 invalidation_rules 都 fail-closed，未知新取值也不会静默放行。

### 14.3 C 确认

C 对 SourceSnapshot 分工无异议；该分工已由袁（项目负责人）于 2026-09-30 裁定。对四项功能夹具无阻塞意见；plain 省略 Git 键和 snapshot_revision 取值可按现状关闭。字段补齐后续走新的契约变更，不改变本合同已冻结的 PreparedRun 词汇和夹具结论。

---

## 15 B 侧功能夹具的**取值**变更（2026-10-01）

### 15.1 变的是什么

四项功能夹具（`tests/contracts/fixtures/prepared_run_functional/{git,plain,blocked,needs_reprepare}.json`）
经 `tests/support/prepared_run_factory.py` **重新生成**（不是手改），逐字节差异只有 3 个键 × 4 份文件：

| 键 | 变更前 | 变更后 | 原因 |
| --- | --- | --- | --- |
| `intent_id` | `intent:prepare-request-1` | `intent-<40 位摘要>` | 意图标识改为由 `(project_id, client_id, prepare_request_id)` 派生，**带项目与客户端命名空间** |
| `prepared_run_id` | `prepared:prepare-request-1` | `prepared-<40 位摘要>` | 同上 |
| `payload_hash` | 旧摘要 | 新摘要 | 摘要口径修正：改为只装**请求意图与人工选择**，不再纳入观察到的来源/依据修订（见 15.2） |

重新生成脚本与逐字节守卫测试：`tests/contracts/test_prepared_run_functional_fixtures.py::test_fixtures_can_be_regenerated_byte_for_byte`。

### 15.2 为什么必须变

1. **命名空间**：存储层的修订计数按 `record_id` **全局**计，不含项目维度。原标识只由
   `prepare_request_id` 构成，两个项目用同一个请求号会互相覆盖。
2. **摘要语义**：原摘要纳入了 `snapshot.content_identity`（**观察结果**）却漏掉了
   `selected_case_ids` 等**人工选择**。后果有两个方向：改了本轮选定用例却得到同一个摘要
   （被误判成幂等复用）；源码一变却先撞"同键异输入冲突"，而正确结论是"依据需重新准备"。

### 15.3 兼容性判断与影响范围（**B 侧实测 + 结论，待 C 确认**）

**字段层面**：字段名、类型、语义均未变——`intent_id` / `prepared_run_id` / `payload_hash`
仍是 `min_length=1` 的不透明字符串，`PreparedRun.schema_version` 仍为 `aitest.prepared-run/1.0`。
按 `docs/接口对接/README.md` 第 8 节，只有"删除、改名、改变枚举或错误语义"才提升主版本，
**本次不提升**。

**消费方影响（B 于 2026-10-01 在 `origin/develop` 上实测）**：

| 实测项 | 结果 |
| --- | --- |
| 谁引用这四项功能夹具 | 全仓**只有一处**：`tests/contracts/test_prepared_run_functional_fixtures.py`（B 自己的合同测试）。**C 侧没有副本** |
| C 的实现如何使用 `intent_id` | 当**不透明字符串**透传（`application/execution/facts.py` 拷进事实；`domain/execution/runs.py` 只声明类型），**无格式假设** |
| C 的实现如何使用 `payload_hash` | **未使用** |
| 全仓（含 `docs/`）是否残留旧取值字面量 | **无** |

**因此：C 侧不需要改动代码、测试或夹具副本。**

**唯一需要 C 留意的语义点**：旧 `intent_id` 在 B 侧**不再产生**；按旧值做恢复查询会查不到。
旧标识没有项目/客户端命名空间，继续产生它会保留跨项目串记录的风险。

### 15.4 C 侧动作

1. 按 15.3 的实测结论，C 侧无需改动；除非 C 本地有未提交的夹具副本或写死的取值断言；
2. 确认后回写本节一行，B 侧据此关闭。

**在本节被确认前，`last_verified_commit` 置空**：先前验证所对应的提交不再生成当前夹具字节。
字段契约本身仍然有效。

## 16 运行中修订的落盘与消费（2026-10-03 追加）

对应 `docs/一期工程检查-B包.md`（2026-10-03 版）**B-05**：
"RuntimeRevision 未持久写入实际运行序列，C runner 未消费接受/失效清单"。

### 16.1 B 侧已交付什么（本次不需 C 改字段）

B 侧的**领域门禁与决策结果**已实现并有回归测试
（`domain/planning/runtime_revision.py`、`application/planning/run_mode.py`）：

| B 侧的产物 | 内容 |
| --- | --- |
| `RunRuntimeFacts` | 由 C 的 `ExecutionFacts` 翻译来的一致事实视图（运行/步骤/尝试/游标/必测集合） |
| `RuntimeRevisionRequest` | 一次修订请求：**冻结计划完整身份**（`base_plan_revision_id` + `base_plan_revision_no` + 可选 `base_plan_revision_digest`）、观察到的快照游标、逐用例的新修订与目标步骤 |
| `RuntimeRevisionDecision` | 决策结果：`accepted`、`revision_no`、`effective_driver`、`snapshot_commit_id`/`snapshot_cursor`，以及交接清单 `affected_step_ids`、`preserved_step_ids`、`invalidated_basis_step_ids`、`rejudge_case_ids`、`confirmation_required_case_ids`、`pause_required`、`new_run_required` |
| 拒绝原因 | `RuntimeRevisionRefusalCode` 的结构化枚举（未发布计划、修订不符、陈旧游标、运行不活跃、驱动扩张、必测移除、适用性弱化、断言弱化、独立核验移除、步骤正在执行/已记录事实、无可改步骤等） |

**B 侧不加新字段、不改 `PreparedRun` 或 `ExecutionFacts` 的 Schema**：
本节的落盘与消费属 C 的记录序列与 runner 行为。

### 16.2 C 侧需要的动作（请 C 确认）

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | **保存运行中修订的序列** | 按 `RuntimeRevisionDecision` 落 `RunPlanRevision`（或等价记录）与 `StepRevisionRef`，使"第几次修订、依据哪个冻结计划、作用到哪些步骤"可按准确修订读回 |
| 2 | **应用接受清单** | runner 在**步骤边界**应用：`affected_step_ids` 用新修订、`preserved_step_ids` 绑原修订；`pause_required` 时在边界暂停而不是中途打断 |
| 3 | **应用失效清单** | 按 `invalidated_basis_step_ids` 失效受影响依据（含 C 自己的传递失效），不得把旧尝试回退成当前通过 |
| 4 | **给"运行中/正在执行"快照夹具** | 现行交付夹具的 `run.control_state` 只有 `completed`／`pending_verification`，且没有任何一步是 `running`；这两类取值在合同里合法但**没有样本**，B 侧只能自行派生，属"夹具覆盖缺口" |
| 5 | **真实半程运行证据** | 半程修订、驱动收窄（`planned → stepwise`）、必测不弱化的真实流程验证 |

### 16.3 依赖的既有约定（不变）

- 修订序列号由**已记录条数**派生，不接受调用者自报（B 侧已按此实现）；
- 消费前先核对同一事实快照的准确身份：顶层与Run内run_id/run_revision相等，runtime_revision_refs逐项及顺序相等、引用非空且不重复。条数相等不能证明序列相同；不自动排序、合并或选择一处为准。
- Step/Attempt均属于同一Run，步骤和尝试ID各自唯一，尝试指向本快照已知步骤；current_attempt_by_step的键集合准确等于全部步骤ID（包括值为null的未开始步骤），逐步值与Step.current_attempt_id一致。被选中的Attempt必须属于该步骤且is_current=true；历史Attempt可以保留但不能宣称当前。孤立/跨运行/跨步骤或矛盾当前身份整体拒绝，不能把历史完成状态当作当前状态。
- 此增量落实既有准确事实语义，不新增PreparedRun/ExecutionFacts字段、状态或业务结论；历史材料仍可读取，矛盾材料不作为新的运行修订依据。核对通过也不证明事实来自权威仓储；默认修订仍须读取准确权威活动/依据、保存序列并由C实际消费。
- 决策按**同一 commit 的一致快照**作出（`observed_snapshot_cursor` 与 C 的事实不符即 `stale_snapshot`）；
- 驱动只允许收窄，扩张需新运行；
- 历史事实保留，失效的是**依据**而不是删记录。

### 16.4 C 侧动作

1. 按 16.2 逐条确认范围与归属，并回写本节；
2. 若第 1／2／3 条需要新的记录类别或字段，**走新的契约 PR**，不在本节散改；
3. 第 4 条（运行中/正在执行夹具）确认后由 C 补，B 侧据此替换自行派生的部分。

---

## 17 `running` 快照与"半程运行"草案（2026-10-03，B 侧提交，**待 C 确认**）

对应第 16.2 节第 4、5 条。C 于 2026-10-03 要求"**B 先出 `running` 草案，C 再在接口文档与 C 侧夹具里补齐
`running` / 半程运行样本，避免两边各造一套**"，并请 B "**把 `running` 的字段语义和'半程运行'的证据口径一并固定**"。

**本节性质**：B 侧**草案**，是待 C 确认的提案，**不是已冻结口径**；C 确认并回写后方可作为夹具依据。
本节**不改 `ExecutionFacts` 任何字段**，只固定"什么样的中间态记录算合法、算完整"。

### 17.1 好消息：中间态在合同里**早已合法**，缺的只是样本

`RunControlStateFact` / `StepStateFact` / `AttemptStateFact` 三个枚举的取值**完整覆盖**中间态，
不需要新增枚举值、不需要改合同字段。缺口在**夹具覆盖**——实测交付夹具
（`tests/contracts/fixtures/execution_facts/`：`success.json`／`quick.json`／`failure.json`／`unknown.json`）
只出现了少数取值，**没有任何一步是 `running`**。

**实测缺口清单**（2026-10-03 逐文件统计，非估计）：

| 枚举 | 合同取值数 | 已有样本 | **缺样本** |
| --- | --- | --- | --- |
| `RunControlStateFact` | **11** | `completed`、`pending_verification` | `not_started`、**`running`**、`pause_requested`、`paused`、`cancel_requested`、`cancelling`、`recovering`、`cancelled`、`execution_error` |
| `StepStateFact` | **9** | `blocked`、`completed`、`execution_error`、`pending_verification` | `pending`、`ready`、**`running`**、`cancelled`、`invalidated` |
| `AttemptStateFact` | **11** | `completed`、`execution_error`、`pending_verification` | `intent_recorded`、`starting`、**`running`**、`stop_requested`、`collecting`、`cancelled`、`invalidated`、`unknown` |

**B 侧需要的**（不是全部都要，见第 17.2／17.3 节）：`running` 与 `pause_requested`／`paused`
的**运行级**样本、`running`／`pending`／`completed` 的**步骤级**样本、
`running`／`collecting` 的**尝试级**样本。其余取值可在后续批次补。

### 17.2 中间态快照的字段语义（草案）

下列口径用于判定"一份 `running` 快照是否自洽"。**全部基于既有字段**，不新增字段。

| # | 口径 | 说明与理由 |
| --- | --- | --- |
| 1 | **恰好一个步骤处于 `running`** | 一期串行执行（架构"一个用户数据工作空间由一个核心进程持排他写锁"）。若出现多个 `running` 步骤，该快照**不自洽**，C 侧夹具不得产生 |
| 2 | `RUNNING` 步骤**必须有当前尝试**，且 `current_attempt_by_step[step_id]` 非空 | 否则"正在执行"没有可核对的事实（`current_attempt_by_step` 已是合同字段） |
| 3 | 该尝试的 `AttemptStateFact` ∈ {`starting`, `running`, `collecting`, `stop_requested`} | `intent_recorded` 表示**尚未开始**（可出现在"已登记意图但未启动"的快照，此时步骤应为 `ready`） |
| 4 | **`waiting_*` 不改写历史事实** | 快照必须**保留**已终止尝试与其证据引用；`pending_verification` 的步骤**保留** `current_attempt_by_step`（因为要核验） |
| 5 | **`control_state=running` 时不得出现已结算的结论** | `run.evidence_level` **不得**为 `full_link`（唯一"完整链路"档）；取 `None` 或 `unknown`／`insufficient` 均可，由 C 按当时能否判定选择。`conclusion_ceiling` 仍按档位冻结（`quick`／`on_demand` → `partial`，`full` → `passable`），**不因"跑了一半"放宽或收紧** |
| 6 | **`pause_requested` ≠ `paused`** | `pause_requested` 表示"已请求、**尚未生效**"；执行中的步骤仍为 `running`。`paused` 表示**已在步骤边界停下**，此时**不应有** `running` 步骤（`RUNNING` 步骤应转为 `pending`/`ready`）。二者混用会让"步骤边界暂停"无法验证 |
| 7 | **`invalidated` 与 `running` 可共存，但互斥于同一步骤** | 受影响步骤为 `invalidated` 且带 `invalidated_by`；正在执行的步骤不得被标记 `invalidated`（架构：正在执行的拒绝修改） |
| 8 | **`completeness` 取 `partial` 或 `unknown`，不得取 `complete`** | 中间态运行未结束，事实不完整。既有 `unknown.json` 夹具即以 `completeness=unknown` 表达"判不了"（本项与既有做法一致） |
| 9 | **`runtime_revision_refs` 为运行中修订** | 与第 16.2 节第 1 条对应：运行中修订的序号须可按准确修订读回 |
| 10 | **`snapshot_cursor` 表达一致视角** | 决策按同一 commit 的一致快照作出（第 16.3 节），中间态快照的 `snapshot_cursor` 必须与其中包含的步骤/尝试修订自洽 |

**B 侧不需要**（明确排除，避免范围膨胀）：`recovering`、`cancelling`、`cancel_requested`、`cancelled`
的中间样本——它们属恢复与取消路径，与本轮"运行中修订"验证无关。

### 17.3 三份"半程运行"夹具的设计（草案）

**共同前提**：`run.tier = full`、`required_scope` ⊇ 已选范围、必测项未被删除或弱化
（否则运行中修订本就应被拒绝，见 `RuntimeRevisionRefusalCode`）。

| # | 夹具 | 关键取值 | 用来验证什么 |
| --- | --- | --- | --- |
| **R1** | **正常执行中** | `run.control_state=running`；步骤：1 个 `completed`（带历史尝试与证据引用）、1 个 `running`（有当前尝试）、其余 `pending` 或 `ready`；`current_attempt_by_step` 齐全；`completeness=partial`；`run.evidence_level=None` | 设计 §16.2 第 1、2 条的基础样本：修订**只作用于未执行步骤**，已完成的步骤修订与依据不变 || **R2** | **暂停请求 → 已在边界暂停** | 同一逻辑运行的两份快照：①`pause_requested`（执行中的步骤仍 `running`）；②`paused`（**无** `running` 步骤，下一步为 `pending`／`ready`） | §16.2 第 2 条的"在**步骤边界**暂停而不是中途打断"；这是第 17.2 节第 6 条的直接检验 |
| **R3** | **依据失效 + 修订生效** | `paused` 或 `running`；1 个步骤 `invalidated` 且带 `invalidated_by`；另一部分步骤为 `pending`（未执行）；`runtime_revision_refs` 非空；`run.evidence_level=None` | §16.2 第 3 条：失效的是**依据**而不是删记录；失效步骤**不得**回退成"当前通过" |

**每份夹具的最低自洽要求**：能通过 `ExecutionFacts` 的既有校验器；`current_attempt_by_step` 与
`steps[].current_attempt_id`、`attempts[].state` 三者一致；保留已终止尝试的历史。

### 17.4 "半程运行"的证据口径（草案）

**用来证明**：运行中修订**只影响未执行步骤**、**正在执行的拒绝修改**、**反向驱动切换被拒**、
**依据失效时暂停**（对应 P1-AC20）。**不能用来**结算业务结论或证据等级。

| # | 口径 | 说明 |
| --- | --- | --- |
| 1 | 证据由**同一运行的连续快照**构成：至少"修订前"与"修订后"两份，`snapshot_commit_id` 与 `snapshot_cursor` 表达先后 | 单份快照证明不了"只影响未执行步骤" |
| 2 | 必须**同时**呈现：被修订步骤的 `step_revision_ref`、已完成的步骤**保持原修订**、`invalidated_by` 指向具体失效依据 | 缺任一项即视为证据不完整 |
| 3 | **`runtime_revision_refs` 的序号须可按准确修订读回**（第 16.2 节第 1 条） | 否则"第几次修订"不可核对 |
| 4 | **不得**使用 `run.evidence_level` 或 `conclusion_ceiling` 作为"运行通过"的证据 | 中间态没有业务结论（第 17.2 节第 5 条） |
| 5 | **"暂停"必须体现为步骤边界**（无 `running` 步骤），不得只写 `paused` 而仍留执行中的步骤 | 与第 17.2 节第 6 条一致 |
| 6 | 缺少任何一项时的正确呈现是**如实登记缺口**，不是补默认值 | `FactCompleteness`／`EvidenceGapFact` 已能承载 |

### 17.5 待 C 确认

| # | 事项 |
| --- | --- |
| 1 | 第 17.1 节的**缺口清单**与 B 侧实际需要的样本集合是否一致（是否有 B 未列、但 C 认为必需的中间态） |
| 2 | 第 17.2 节十条口径是否与 C 侧既有实现一致；**特别是第 6 条 `pause_requested` 与 `paused` 的区分**是否与 C 的 runner 实际行为相符 |
| 3 | 第 17.3 节 R1／R2／R3 三份夹具的取值是否可实现（尤其 R2 是否确实产生**两份**快照） |
| 4 | 坐标：夹具放在 `tests/contracts/fixtures/execution_facts/`（与既有四份同目录）是否符合 C 侧约定 |
| 5 | 第 17.2 节第 5 条（中间态 `evidence_level` 为 `None`）是否与 C 既有构造一致 |

**B 侧在 C 确认前不据此改动任何夹具、也不新增字段。** C 确认后本节转为冻结口径，
B 侧据此替换自行派生的部分（第 16.4 节第 3 条）。

**在本节被确认前**，B 侧对 B-05 只登记"领域门禁已完成、落盘与消费待 C"，
**不声称 B-05 已闭合**。

## 18 2026-10-03 负责人裁定与冻结引用

主责裁定：[DEC-007](../../归档/裁定/DEC-007-规则与计划记录的修订配对.md)、[DEC-008](../../归档/裁定/DEC-008-验收范围标识是否冻结进PreparedRun.md)、[DEC-009](../../归档/裁定/DEC-009-源码快照的修订语义.md)。本节更新旧稿中“不新增字段”和“修订号可代替内容身份”的表述。

1. 规则与计划正文 `revision` 是内容版本；发布结果 `record_revision` 是仓储读取修订。`rule_versions[].revision`、`plan_revision.revision_no` 冻结仓储修订；读取不可把正文版本当作仓储修订。Case 与 Scope 继续保持原来的正文/仓储配对不变量。
2. PreparedRun 新增 `scope_id` 与 `project_revision`。新准备必须冻结范围标识；历史模型可读时缺字段保留缺口，范围缺标识要求重新准备。范围按 `scope_id + acceptance_scope_revision` 核对，并校验项目归属。
3. SnapshotRef 新增兼容字段 `record_revision`（旧记录默认 1，用于准确读取）。`InputRevisions.snapshot_revision` 保留整数仓储修订。源码变化单独比较 `observed_snapshot_content_identity`，该观察值随准备记录保存和重建，不进入 payload_hash；内容不同返回原意图及 `needs_reprepare`，不覆盖旧准备。
4. `observed_scope_id` 随准备意图保存；标识变化或旧标识缺失均要求重新准备。不能根据同修订号猜范围。冻住材料可读，不代表当前源码或业务实际加载来源已证实。
5. 兼容性：Schema 保持 1.0，新增读取字段为可选；新准备要求 scope_id，旧准备需重新准备是明确迁移行为。Schema 与功能夹具由生成脚本产生，历史签署记录保留。

实现入口：`contracts/prepared_run.py`、`planning/preparation.py`、`prepare_run.py`、`publish.py`、`plan_builder.py`、`drift.py`。验证详见 ABC 修复证据清单；不据此登记真实 AC 通过。


## 19 运行中修订的准确仓储引用

沿用第18节及DEC-007，不新增字段或改变PreparedRun/ExecutionFacts Schema。RuntimeRevisionRequest.base_plan_revision_no、C事实plan_revision.revision_no与已读取Plan.record_revision须核对同一准确仓储修订；Plan.revision仍是正文版本，不能在请求比较中替代仓储修订。正文7/仓储2时准确@2请求可以进入其余原门禁，@7请求须拒绝；正文2/仓储7同理。计划ID与已提供摘要仍单独核对，拒绝不能留下部分生效步骤。

旧无record_revision的纯领域对象保持既有配对版本兼容调用；不能据该兼容调用宣称真实仓储读取或默认执行已核实。应用用例须按准确仓储引用读取并附读取修订，新的冻结引用不从正文版本推定。此修复只修领域比较条件，不新增运行中修订落盘、权威活动读取、默认C消费或真实验收。合同仍reviewing/partial，历史双方签署记录保留。

## 20 权威运行修订评估入口（2026-10-05）

沿用第16、18、19节，一期功能第10.6/15节与架构01第4节。新增应用组件 `SavedRuntimeRevisionAssessment`，只返回绑定当前快照的评估结果，不产生修订、执行授权或通过结论；原纯值 `request_runtime_revision` 保留，不能独立作为产品权威入口。

1. C 提供 `RuntimeExecutionReader.read_runtime_revision_facts(project_id, run_id)`（协议声明归 `application/ports.py`）。从已保存的准确当前引用读取事实，并逐项读取评估消费的当前及历史 Attempt 的权威检查点，核对完整投影；缺失、跨项目、状态/依据/消费关系/输出不同均拒绝。检查点的 expected_plan_revision_ref 不在公开投影中，须单独与 Run 冻结引用逐项核对；缺失或不同均拒绝，不能按投影相等推定一致。读取后再核对当前快照未变化，不把仅自洽的调用方 DTO 当作活动来源。
2. B 从该事实冻结的 `plan_revision` 按准确仓储修订读取计划正文，先核对归属与正文摘要。传入的 Plan 是待核对视图，必须附准确 `record_revision`，正文版本、完整计划内容及 Scope 正文与仓储材料一致。Case 从计划准确引用读取，逐项核对 ID、正文/仓储修订配对、规范内容摘要和断言文本摘要。不能用“同 ID/修订”的替代正文绕过独立核验或必测关联门禁，也不以最新记录替换冻结旧记录。
3. 已冻结 M、S、档位与保存计划核对。确认只接受引用 ID，按 `case_link@1` 回读受控保存记录；核对项目、准确用例/依据、正文摘要、持久意图派生 ID 与输入摘要。请求写 `confirmed` 或自报确认对象不替代仓储事实；旧确认仅按原纯规则匹配新依据，不会因引用存在就有效。
4. 尚无可读运行中修订序列记录的旧快照，若 `runtime_revision_refs` 非空，显式阻塞；不按字符串条数自签下一修订。材料读取后再次核对当前 C 事实完整相等，变化拒绝。评估结果仍可能在返回后过期，后续保存必须在同一短事务内重读并重新评估，不能凭预览结果直接执行。
5. 兼容：不改 PreparedRun/ExecutionFacts Schema、生成夹具、公开动作或已签字段；不代替第17节待确认半程夹具、C 实际句柄检查、持久 RunPlanRevision/StepRevisionRef、暂停/依赖失效、默认产品入口和真实 AC。合同保持 reviewing/partial/not_run，组件验证另行记录。
6. 当前事实的后续发布不能改写初始冻结 PlanRevisionRef、环境引用/隔离方式、规则引用及按档位派生的结论上限；与既有 workspace/intent/tier/M 一起逐项核对。运行中修订追加序列、保留初始计划引用；换环境、规则或不兼容依据须新建运行。拒绝发生在当前指针/快照暂存前；`allow_current_change` 仅支持原子认领新 Attempt，不能绕过冻结依据守卫。
7. 普通状态/证据发布与新Attempt认领也不能增删冻结步骤或改写其case_id、ordinal、required_for_case、level、dependency_step_ids、registered_entry_ref、assertion_refs、evidence_requirement_ids和StepRevisionRef；这些属于冻结执行内容。运行修订需新的受控保存/应用入口核对可读权威序列，不能用普通发布或allow_current_change布尔值替代。当前仅保持状态、当前Attempt、失效及缺口等执行进度字段的合法更新路径；不把此守卫算作运行修订应用已经完成。
8. 普通发布与新Attempt认领同时保留已保存的driver、selected_scope、source_binding_digest及准确runtime_revision_refs序列，包括未核实源码的空值。驱动收窄须有受控DriverChange记录，运行修订须有可读序列和步骤边界应用，源码从待核实升级须有准确保存的来源核验；不能直接修改事实字段替代这些动作。Run的M/S与coverage的M/S分别保留原集合，序列顶层与Run内逐项同序一致、引用非空且唯一。进度、缺口和当前执行计数仍可合法改变；本条守卫不表示上述受控持久动作已完成。

### 20.1 已产生尝试的内容保留与活动优先级

沿用一期功能第4节、架构01第4节和AC20，不新增公开DTO或状态。运行修订的“尚未执行”不能只由Step状态标签证明：

- 当前或历史Attempt仍在`intent_recorded / starting / running / stop_requested / collecting`时，所属步骤在修订评估中视为正在执行，拒绝修改；改为非当前不能证明外部执行已停止。
- 当前或历史Attempt已完成、取消、待核实、执行错误、失效或结果未知时，已有事实和原执行内容继续保留；即使Step投影滞后为pending、ready、blocked或invalidated，也不能重新列入可修改步骤。Unknown不证明未执行，失效不抹除曾经执行。
- 仅Step为既有可修改状态且没有任何Attempt事实时，才按尚未执行处理。未执行的同用例步骤仍可修改；依据变化时已有事实的步骤进入preserved/invalidated_basis清单并要求暂停，不把历史结果改成新执行。
- 当前Attempt的StepRevisionRef必须与当前Step逐字段相等（ID、仓储修订、摘要、继承及基础引用），不能只核对所属步骤ID。历史Attempt保留原引用，不要求它跟随当前步骤内容变化。

应用翻译完整消费同快照中的当前/历史尝试，领域只计算一次修订进度。该增量仍是评估守卫；不代替真实停止核实、持久RunPlanRevision、runner步骤边界应用或真实半程验收。旧公开材料保持可读，无法证明依据的请求明确拒绝。合同状态继续reviewing/partial/not_run，不替代双方确认。


## 21 可读步骤内容与运行修订基础

沿用一期架构01第4/7节、架构02的StepRevisionRef及第16节，不新增公开Schema或业务结论。C通过A公共记录仓储保存不可变`step_revision`，与初始Run/Step、登记意图、当前指针和快照同一短事务发布。

- 每份内容绑定项目、origin_workspace_id、Run、Step、准确PreparedRun@1、冻结PlanRevisionRef和CaseRevisionRef；保存完整Case正文、用例内步骤索引及冻结步骤的objective/expected/layer。内容摘要覆盖整个规范正文，不只覆盖步骤标题。本文定义内部存储记录，业务字段语义仍引用现行分册。
- StepRevisionRef.revision_no为该内容记录的准确仓储读取修订，初始内容@1；Case正文版本/仓储引用另外保留，不能把Case版本当作首次step_revision的仓储修订。步骤内容身份包含Run，两个新运行即使消费同一准备也不共用带Run归属的内容记录。
- 读取须逐项核对记录类别/ID/仓储修订、项目/工作空间/Run/Step及整个正文摘要，并核对完整Case摘要和冻结步骤索引/正文配对。当前/历史Step引用各读自身材料；不按latest替代准确引用，旧记录缺材料时不能据摘要猜正文或冒充可执行。
- 重传原登记意图返回准确初始结果，不能重复创建内容。暂存或权威发布失败不发布部分内容/Run/指针；失联先核对原意图，不重新执行被测业务。
- 此阶段补真实保存→换核心→准确回读与错误材料/同准备多Run/提交故障验证；内容可读不等于已具备实际执行入口、运行修订保存/应用、环境验证或半程AC。后续受控修订在本基础上核对最新内容、原子保存序列及C/D消费，不开放普通快照绕过字段守卫。

合同保持reviewing/partial/not_run；项目文档只读，旧无独立内容的组件材料仍可读取，但后续受控执行/修订明确阻塞或重新准备，不伪造新执行记录。
