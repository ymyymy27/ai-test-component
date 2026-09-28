# 薄底座与 prepare_run 编排设计说明（覆盖 Sprint 2／5／6）

版本：0.1
日期：2026-09-28
分支：`feat/package-b-substrate`
状态：**设计定稿，代码同批提交**
依据：一期架构文档《01-项目与计划》第 7 节「记录字段与模块归属」、第 8 节「发布、准备与启动的调用次序」、第 11 节「准备请求与业务身份合同」；一期功能文档第 5、9 节；一期需求 P1-FR07、P1-AC17、P1-AC19
关联：`10-准备意图与幂等规则设计说明.md`、`09-待解决问题清单.md`（B-Q02）、`docs/接口对接/B-A-端口与保存需求.md` 第 8 节

---

## 1 为什么做这件事

### 1.1 问题：三个 Sprint 卡在同一处

B 包剩余工作的依赖关系核对如下：

| Sprint | 剩余内容 | 前置条件 |
| --- | --- | --- |
| 2 | 项目/绑定/环境用例、模板草稿、计划发布、`prepare_run` | `WorkspaceUnitOfWork` + `RecordRepository` |
| 5 | 模型请求编排、迟到响应落盘、脱敏投影、GitHub 只读 | `ProjectionPort` + `ModelProvider` + `SecretPort` + **`WorkspaceUnitOfWork`** |
| 6 | 交接一：用**真实 `PreparedRun`** 替换 C 的夹具 | **Sprint 2 的产物** |
| 6 | 交接二：配合 C→D 的 `ExecutionFacts` 交接 | C 与 D |

**Sprint 6 的交接一要真实 `PreparedRun`，而真实 `PreparedRun` 是 Sprint 2 的产物；
Sprint 5 的编排要保存记录，用的是 Sprint 2 卡住的同一个工作单元。**
即：Sprint 2 → 5 编排 → 6 交接一是一条串联链，**前置条件是同一处**。

### 1.2 现状：那一处没有签名

实测 `src/aitest/application/ports.py`：

| 端口 | 所有者 | 现状 |
| --- | --- | --- |
| `Clock` | A | ✅ 有签名 |
| `WorkspaceUnitOfWork` / `RecordRepository` | A | ❌ 只有一行文档字符串 |
| `ProjectionPort` / `ModelProvider` / `SecretPort` / `SourceSnapshotPort` | A | ❌ 同上 |
| `ExecutionPort` / `SpoolStore` / `EvidenceObjectStore` | C 用 | ✅ C 已补签名 |

A 至今未在 `develop` 上留下任何工作包，对 `B-A-端口与保存需求.md`（含第 8 节签名草案）未回应。

### 1.3 采取的办法：本地薄底座 + 内存实现 + 转接头

**不碰 A 的任何目录、不修改 `application/ports.py`**，在自己的目录里定一份**窄接口**，
用内存实现让编排真实跑起来；将来 A 给签名后，只写一层薄转接头。

这与 C 包的做法同源（C 用 25 行的 `tests/support/fake_unit_of_work.py` 验证应用闭环，
并在文档里如实标注"A 包正式 UoW 接入未完成"），但 B 的做法多一层：
**把接口显式定出来**，因为 B 需要的操作比 C 多，不能只靠一个测试替身。

---

## 2 范围与边界

### 2.1 本次做

| 产物 | 位置 |
| --- | --- |
| 薄底座协议（`CommittedRecord` / `RecordQuery` / `CommitBatch` / `UnitOfWork` / `RecordReader`） | `src/aitest/application/planning/substrate.py`（新建） |
| `prepare_run` 真实用例（编排，零 I/O） | `src/aitest/application/planning/prepare_run.py`（新建） |
| 内存实现（含固定时钟） | `tests/support/memory_substrate.py`（新建） |
| A 端口转接头骨架 | `src/aitest/application/planning/substrate_adapter.py`（新建） |
| 全链路演示（建项目 → 发布计划 → prepare → `PreparedRun`） | `tests/unit/test_prepare_run_flow.py`（新建） |

### 2.2 本次不做

| 不做项 | 原因 |
| --- | --- |
| 真实文件存储（`infrastructure/file_store/`） | **A 的目录**，B 不碰；已按用户决定"先不接，继续等" |
| 修改 `application/ports.py` | A 唯一所有；B 只在 `B-A` 文档提需求 |
| `SourceSnapshotPort` 的真实读取 | 归属未裁定（B-Q01 / C-Q08） |
| 模型请求编排、脱敏投影、GitHub 只读 | 依赖 `ProjectionPort` / `ModelProvider` / `SecretPort`；本次只做底座，接它们属下一步 |
| 项目/绑定/环境用例、模板草稿生成 | 同样依赖底座；本次先把底座与 `prepare_run` 打通，其余用例在后续分支按同一路径做 |

### 2.3 本次交付的意义与限度

**意义**：`prepare_run` 会真正跑起来 —— 幂等判定、冲突拒绝、来源变化阻塞、`PreparedRun` 构造
全部可执行、可测试、可演示；A 一交端口，只需实现转接头。

**限度（不得据此声称验收通过）**：内存实现**不是**真实存储。
"写入前后崩溃恢复""单写锁""不可变历史与备份闭包""索引分页"这些存储侧验收
（实施方案 A 包第 37 行）**仍然未验证**，必须等真实文件后端。

---

## 3 薄底座协议

### 3.1 设计原则

1. **窄**：只定义 B 的用例真正需要的最小操作，不模拟 A 的完整存储合同。
2. **按准确修订读**：所有读操作必须显式给修订号，**没有"读最新"的方法**。
3. **业务顺序按提交序号**：`commit_seq` 字符串，不用系统时间。
4. **不重复实现脱敏与投影**：那是 `ProjectionPort` 的事。

### 3.2 值对象

```python
AggregateKind = Literal[
    "project", "binding", "module", "dependency_set", "task", "delivery",
    "acceptance_item", "environment", "source_snapshot", "template_ref",
    "generated_content", "rule_draft", "rule_version", "case", "case_link",
    "plan", "acceptance_scope", "preparation_record", "model_outbound_policy",
]

@dataclass(frozen=True, slots=True)
class CommittedRecord:
    """已提交的不可变记录。"""

    aggregate_kind: AggregateKind
    record_id: str
    revision: int
    payload: Mapping[str, object]

@dataclass(frozen=True, slots=True)
class RecordQuery:
    """有限查询：项目范围 + 可选聚合类别 + 可选精确标识。"""

    project_id: str
    aggregate_kind: AggregateKind | None = None
    record_id: str | None = None
    limit: int = 50

@dataclass(frozen=True, slots=True)
class RecordPage:
    items: tuple[CommittedRecord, ...]
    next_cursor: str | None = None

@dataclass(frozen=True, slots=True)
class StagedRevision:
    aggregate_kind: AggregateKind
    record_id: str
    revision: int

@dataclass(frozen=True, slots=True)
class CommitResult:
    commit_seq: str
    created: tuple[StagedRevision, ...]
```

### 3.3 协议（两个，不是一个）

```python
class UnitOfWork(Protocol):
    """短事务：暂存记录与准备意图，一次提交。"""

    def open(self, project_id: str) -> None: ...
    def commit_seq(self) -> str: ...
    def stage_record(self, *, aggregate_kind, record_id, expected_revision, payload) -> StagedRevision: ...
    def stage_preparation(self, *, record: PreparationRecord, payload: Mapping[str, object]) -> StagedRevision: ...
    def commit(self) -> CommitResult: ...
    def rollback(self) -> None: ...

class RecordReader(Protocol):
    """只读查询；按准确修订读取，没有"读最新"。"""

    def read(self, *, aggregate_kind, record_id, revision: int) -> CommittedRecord: ...
    def query(self, query: RecordQuery) -> RecordPage: ...
    def find_preparation(self, *, project_id, client_id, prepare_request_id) -> PreparationRecord | None: ...
    def find_preparation_by_intent(self, *, intent_id: str) -> PreparationRecord | None: ...
```

**读写分成两个协议**（对应 `B-A` 第 8 节第 6.3 问）：用例读多写少，
分开后测试可以只给一个只读替身；A 若坚持单端口，转接头里合成一个即可。

### 3.4 不变量（由实现与测试共同保证）

| # | 不变量 | 依据 |
| --- | --- | --- |
| 1 | 未 `open` 就 `stage_*` / `commit` → 抛 `ValueError` | 短事务边界必须显式 |
| 2 | `expected_revision is None` 表示**新建**；已有记录时传 `None` → 抛 `StaleRevisionError` | "不自动覆盖用户编辑" |
| 3 | `expected_revision` 与当前修订不符 → 抛 `StaleRevisionError`，**携带当前修订** | 架构文档第 8 节末 |
| 4 | 同一次提交内**同一 `(aggregate_kind, record_id)` 出现两次** → 抛 `ValueError` | 防"半次提交" |
| 5 | `stage_preparation` 在同一事务内按三元组检查：摘要相同返回原修订、摘要不同抛 `PreparationConflictError` | 架构文档第 11 节；`B-A` 第 8.6 节建议 |
| 6 | `commit` 后 `commit_seq()` 前进；提交后**对后续读取可见** | "提交后可见性" |
| 7 | `rollback` 后本次暂存**全部丢弃**，不产生任何可见修订 | 事务语义 |
| 8 | `read` 不带修订参数即无法调用（签名强制） | 实施方案第 3 节 |
| 9 | 记录**不可变**：同一 `(kind, id, revision)` 不允许被改写 | 永久保留 |

---

## 4 `prepare_run` 用例

### 4.1 输入与输出

```python
@dataclass(frozen=True, slots=True)
class PreparationInputs:
    """prepare_run 的全部业务输入；不含任何传输层参数。"""

    project_id: str
    workspace_id: str
    binding_id: str
    binding_revision: int
    binding_form: BindingFormFact
    client_id: str
    prepare_request_id: str
    input_revisions: InputRevisions
    snapshot: SnapshotRef
    selected_paths: tuple[str, ...]
    environment: EnvironmentRefFact
    execution_source: ExecutionSourceBinding
    plan_revision: PlanRevisionRef
    acceptance_scope_revision: int
    rule_versions: tuple[RuleVersionRef, ...]
    template_versions: tuple[TemplateVersionRef, ...]
    case_revisions: tuple[CaseRevisionRef, ...]
    frozen_cases: tuple[FrozenCase, ...]
    assertion_bases: tuple[AssertionBasisEntry, ...]
    authorization_requirements: tuple[AuthorizationRequirement, ...] = ()
    model_outbound_policy_revision: int | None = None
    source_snippets_enabled: bool = False
    exclusion_rules: tuple[str, ...] = ()
    refetch_dependencies: tuple[str, ...] = ()
    git_base_commit: str | None = None
    git_diff_digest: str | None = None
    plain_manifest_digest: str | None = None
    run_tier: RunTierFact = RunTierFact.QUICK
    initial_driver: RunDriverFact = RunDriverFact.PLANNED
    template_required_case_ids: tuple[str, ...] = ()
    frozen_required_case_ids: tuple[str, ...] = ()
    selected_case_ids: tuple[str, ...] = ()
    skipped_scope: tuple[SkippedScopeEntry, ...] = ()
    applicability_exclusions: tuple[ExclusionEntry, ...] = ()
    context_gaps: tuple[GapEntry, ...] = ()
```

```python
def prepare_run(
    inputs: PreparationInputs,
    *,
    unit_of_work: UnitOfWork,
    reader: RecordReader,
    clock: Clock,
) -> PreparedRun: ...
```

**为什么返回 `PreparedRun` 而不是 `PreparationLookup`**：调用方（D 的入口、CLI、面板）要的是
可直接展示的准备结果；`PreparationLookup` 是内部判定，留在 `preparation.py`。

### 4.2 步骤（严格对应架构文档第 8、11 节）

| # | 步骤 | 失败时的行为 | 依据 |
| --- | --- | --- | --- |
| 1 | **项目上下文缺口检查**：`context_gaps` 非空 → 构造 `status=blocked` 的 `PreparedRun`，把缺口写成 `blocking_reasons`，**不抛异常** | 返回阻塞态 | P1-AC17：「上下文缺失时列缺口并阻塞，**不编造依赖与结论**」 |
| 2 | `payload_hash` 计算：**只覆盖请求侧业务意图**（`selected_paths`、档位、驱动、计划修订、用例/规则/模板修订、快照内容身份），**不含实际观察到的来源修订**，也不含传输参数 | — | `10-...设计说明.md` 第 3.4 节；见下方第 4.4 节的语义说明 |
| 3 | `reader.find_preparation(三元组)` → `decide_preparation()` | — | 架构文档第 11 节 |
| 4a | 判定为 `CONFLICTED` → 抛 `PreparationConflictError`（携带原 `intent_id`、原摘要、原提交序号） | 拒绝并提示 | 「同键异输入摘要返回冲突，不覆盖」 |
| 4b | 判定为 `NEEDS_REPREPARE` → 构造 `status=blocked` 的 `PreparedRun`，`invalidation_rules` 列出变化的来源 | 返回阻塞态，**不把新字节写入旧意图** | 「同请求期间源码已变则返回原意图及'依据需重新准备'」 |
| 4c | 判定为 `REUSED` → 返回 `existing`（调用方传入的已存 `PreparedRun`），**不新建** | — | 「同请求同输入幂等返回」 |
| 4d | 判定为 `NEW` → 生成 `intent_id`，`stage_preparation()` 登记，构造 `PreparedRun` | — | 「`intent_id` 与准备记录同一次提交」 |
| 5 | `commit()`，用返回的 `commit_seq` 填 `created_at_commit` | 提交失败则整个用例失败，不返回半成品 | 「业务顺序按提交序号判断」 |

**`intent_id` 从哪来**：`prepare_request_id` 是客户端生成的**传输/请求身份**，
`intent_id` 是**应用生成**的业务身份（架构文档第 11 节表格）。
本次用 `intent_id = f"intent:{prepare_request_id}"`，但**不是把请求号当意图号**——
它是应用侧派生并随记录一起提交的独立值，`PreparationRecord` 仍拒绝两者相等。
（该派生规则若 A 有不同约定，改转接头即可。）

### 4.3 四条硬性约束的落点

| 约束 | 落点 |
| --- | --- |
| **不把新字节塞进旧意图** | `NEEDS_REPREPARE` 分支**只返回阻塞态，不调用 `stage_preparation`** |
| **冲突不覆盖** | `CONFLICTED` 分支**抛出**，不写任何记录 |
| **幂等** | `REUSED` 分支直接返回 `existing`，`commit_seq` 不前进 |
| **阻塞不抛异常** | 缺口与来源变化都返回 `status=blocked` 的 `PreparedRun`（合同允许 `status=blocked` 且要求 `blocking_reasons` 非空），由 D 展示 |

### 4.4 实现时发现并修正的一处语义分歧

**分歧**：来源修订（`InputRevisions` 的八项）算不算"输入摘要"的一部分？

- 若算 → 来源一变摘要就变 → `decide_preparation()` 会判成 **`conflicted`（同键异输入）**；
- 但架构文档第 11 节要求这种情况报 **"依据需重新准备"**（`needs_reprepare`）。

**两者互斥**，必须选一个。**取值原则**：架构文档第 11 节把三句话分开写——
"输入摘要不同返回冲突"讲的是**请求内容**不同；"同请求期间源码已变则返回原依据及'依据需重新准备'"
讲的是**来源发生漂移**。因此：

| 概念 | 含义 | 处理 |
| --- | --- | --- |
| `payload_hash` | **请求要什么**（业务意图） | 进摘要；不同 → 冲突 |
| `input_revisions` | **实际观察到什么**（来源漂移） | **不进摘要**；不同 → 需重新准备 |

**已同步修改** `application/planning/preparation.py` 的 `PAYLOAD_FIELDS`（去掉八项修订）
并更新对应测试：新增"来源修订不在摘要字段内"与"来源修订变化不改变摘要"两条反例测试。
`10-准备意图与幂等规则设计说明.md` 第 3.4 节的口径以本条为准。

---

## 5 转接头骨架

```python
class PortsUnitOfWork:
    """把 A 的 WorkspaceUnitOfWork / RecordRepository 适配成本地薄底座。

    本类是在 A 的端口签名落地**之前**写的骨架：方法体为 `NotImplementedError`，
    但三个"翻译"职责已经写明，届时只需填方法体。
    """
```

三处翻译职责（写进 docstring，避免将来重新推演）：

1. **修订语义**：本地 `expected_revision: int | None` ↔ A 的 `expected_revision`；
   若 A 用别的"新建"表示法（如 `0`），在 `_to_expected()` 里转。
2. **错误类型**：本地 `StaleRevisionError` / `PreparationConflictError` ↔ A 的端口错误类；
   若 A 回传"当前修订 + 差异提示"的元组而不是异常，在适配层包成异常。
3. **payload 形状**：本地 `Mapping[str, object]` ↔ A 的记录 payload；
   若 A 直接回领域对象而非 payload，在适配层做转换（这也正是 `B-A` 第 8.3 节第 2 问的两种选择）。

---

## 6 验证设计

| 文件 | 覆盖 |
| --- | --- |
| `tests/support/memory_substrate.py`（新增） | 内存实现 + `FixedClock`；**仅测试支撑，不注册为可用能力** |
| `tests/unit/test_substrate_contract.py`（新增，22 项） | 第 3.4 节九条不变量逐条：未开事务即暂存/提交/回滚、新建与更新、陈旧修订携带当前修订、`expected_revision=None` 覆盖已有记录报冲突、同事务重复暂存、准备记录同键同摘要复用/异摘要冲突且不覆盖、提交序号 advance、提交后可见、空提交拒绝、回滚丢弃（含准备记录）、按修订读、历史上只追加、查询按项目范围与分页 |
| `tests/unit/test_prepare_run.py`（新增，21 项） | 四态分支逐条：新建、幂等复用（含"重放不再提交"）、冲突抛出（含"冲突不写任何东西"）、来源变化阻塞（含"不把新字节写入旧意图"与"保留原意图"）；**缺口阻塞不抛异常且不登记准备记录**；`intent_id` 不等于请求号；`created_at_commit` 取自提交序号、`created_at` 取自时钟；摘要只含请求侧输入（含"来源修订不改变摘要"与"请求侧变化改变摘要"两条反例）；quick 档派生 `partial`；身份字段校验 |
| `tests/unit/test_prepare_run_flow.py`（新增，2 项） | **全链路**：建项目（三个修订）→ 建绑定 → 发布计划（走真实领域门禁 `validate_plan_publication`）→ `prepare_run` → 得到合同合法的 `PreparedRun`；重放幂等且不再提交；记录按准确修订读取、查询按项目范围分页 |

**通过条件**

```powershell
uv run ruff check .
uv run mypy
uv run pytest -q
uv run python scripts/generate_schemas.py
git diff --exit-code -- src/aitest/contracts/schemas
```

本次**不改 `contracts/`**，Schema 应无变化。

---

## 7 交付说明（四类）

| 类别 | 内容 |
| --- | --- |
| **已实现** | 薄底座协议与九条不变量；`prepare_run` 编排；内存实现；转接头骨架；全链路演示 |
| **已通过静态·单元·合同检查** | `ruff`、`mypy --strict`（107 源文件）、`pytest`、Schema 一致性 |
| **已通过真实环境验收** | **无**。内存实现不是真实存储，无端到端运行结果 |
| **未验证** | 真实文件存储的全部存储侧验收（崩溃恢复、单写锁、不可变历史与备份闭包、索引分页）；`SourceSnapshotPort` 实读；模型出站编排与脱敏投影；GitHub 只读；P1-AC17／AC19／AC30／AC32 端到端验收 |

### 7.1 实现中发现的已知缺口（登记，不在本次修）

| 缺口 | 说明 | 归属 |
| --- | --- | --- |
| **`binding_revision` 未与底座核对** | `prepare_run` 目前**直接采用调用方传入的 `binding_revision`**，没有读回实际修订做一致性核对。架构文档第 11 节要求"提交时校验项目、绑定、计划、规则、模板、环境修订……是否仍有效"，这一步需要 A 的记录仓储提供读入口 | 等 A 的 `RecordRepository`（B-Q02） |
| 计划/规则/模板/环境的修订核对 | 同上，目前各 `*_revision` 也来自调用方 | 同上 |
| `PreparedRun` 自身的落盘 | 本次只产出对象，不保存 | Sprint 6 交接前需定 |

以上三项**都不影响本次已实现的规则正确性**，但**不能据此声称"准备功能完整"**。

---

## 8 跨包影响

| 对象 | 影响 | 处理 |
| --- | --- | --- |
| A | **不修改其任何文件**；本协议是 B 侧临时底座，A 的签名落地后由转接头对接 | `B-A` 文档第 8 节已提需求；本次在文档中说明"B 侧已按该草案自行验证" |
| C | 无：`prepare_run` 只产出 `PreparedRun`，消费者是 C 与 D | Sprint 6 交接一时用得上；本次不改 C 的目录 |
| D | 面板可读取 `PreparedRun`；`status=blocked` + `blocking_reasons` 是缺口与来源变化的展示来源 | 本次未改 `B-D` 文档 |
| 全组 | 新建 4 个模块与 3 个测试文件，**不改任何既有代码文件** | PR 说明 |

---

## 9 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-28 | 0.1 | 初稿：问题与收敛分析、薄底座协议与九条不变量、`prepare_run` 五步编排与四条硬约束落点、转接头三处翻译职责、三层验证设计 |
