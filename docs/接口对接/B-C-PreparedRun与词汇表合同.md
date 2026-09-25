# B-C 跨包合同确认：PreparedRun 与运行词汇表

版本：0.1
日期：2026-09-24
提出方：B 包（feix-a，项目与计划）
接收方：C 包（执行与证据）
状态：**待 C 确认**
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

> **C-01 为本文档唯一的语义缺陷**，其余为类型闭合与一致性建议。

---

## 4 建议方案

### 4.1 词汇表的规范定义（B 侧，`domain/planning/plans.py`）

B 包将把现有 `RunMode` / `Driver` 改名为架构文档使用的名称，并补齐 `ConclusionCeiling`：

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
| `environment_ref`、`environment_isolated` | `PreparedRun.environment_id/revision`、`isolation_mode` | 默认 venv；**显式不隔离是合法值，不等于缺配置**（需求 P1-FR07） |
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

---

## 8 兼容性影响

| 影响面 | 说明 |
| --- | --- |
| 现有三份夹具 | 各改 1 处（`conclusion_ceiling`） |
| `tests/contracts/test_execution_facts.py` | 若断言了具体取值需同步；当前测试只做加载验证，预计无需改动 |
| 已生成 Schema `contracts/schemas/ExecutionFacts.json` | 需重新生成（`python scripts/generate_schemas.py`），并确认 CI 的 `git diff --exit-code` 通过 |
| D 包 | **受益方**：`ConclusionCeiling` 成为闭合枚举后，报告聚合不再需要猜测取值 |
| B 包 | 需将 `RunMode`/`Driver` 改名并在 `PreparedRun` 中携带 `plan_revision`/`rules_revision`/`environment_ref`/M/S |

---

## 9 确认记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿，提出 C-01—C-08 | B 包 feix-a（待 C 回复） |
