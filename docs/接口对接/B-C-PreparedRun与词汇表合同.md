# B-C 跨包合同确认：PreparedRun 与运行词汇表

版本：0.3
日期：2026-09-25
提出方：B 包（feix-a，项目与计划）
接收方：C 包（执行与证据）
状态：**待 C 确认**（B 侧 4.1 节与第 10、13 节已完成）
依据：一期架构文档《01-项目与计划》第 4、11、12 节；《02-执行与证据》；组长《一期工程四部分拆分与低对接实施方案》第 3 节跨包合同表；需求文档 P1-FR07、P1-AC19/AC20/AC31
对照对象：`origin/feat/package-c-execution`，commit `d60781d`，文件 `src/aitest/contracts/execution_facts.py`

---

## 1 目的

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

> 本目录的处理规则：只追加，不覆盖（见 `索引.md`）。

| 日期 | 版本 | 变更 | B 包 | C 包 |
| --- | --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿，提出 C-01—C-08 | 已提出 | 待确认 |
| 2026-09-25 | 0.2 | 4.1 节词汇表改名与 `ConclusionCeiling` 已完成；新增第 10 节 B 侧对象边界 | 已完成 | 待确认 |
| 2026-09-25 | 0.3 | 新增 C-09 与第 11 节；新增第 13 节 B→C 交接定义与待办状态 | 已完成 | 待确认 |

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
| `SourceSnapshot` 归属 | 架构文档第 7 节归 `domain/execution/sources.py`，第 1、2 节与 P1-FR01 把职责归 B；待裁定 | 涉及源码内容身份的字段以哪一侧为准尚未定 |

以上三项均**不要求 C 现在动手**，登记在此以便交接时核对。

### 13.4 确认

依据接口对接目录的处理规则，本文件确认后即为 B 与 C 之间的合同依据。

```text
对接：B 包（项目与计划） ↔ C 包（执行与证据）
文件：docs/接口对接/B-C-PreparedRun与词汇表合同.md
版本：0.3

确认：[ ] B 包 feix-a    日期：
确认：[ ] C 包           日期：

未决项：C-01、C-02/C-03、C-06、C-08、C-09，以及第 7 节的接收形式一项
```
