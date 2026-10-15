---
contract_id: AC-001
title: ExecutionFacts 存储与恢复
provider: A
consumer: C
contract_version: "1.9"
contract_status: reviewing
provider_implementation: partial
consumer_implementation: partial
verification_status: not_run
last_verified_commit: null
blockers:
  - "A-05/A-07：已知检查点重启闸门已补，未知活动/完整迁移与副本启动恢复仍欠"
  - "C-03/C-05：默认核验/外部导入准确闭包与来源事实保存仍欠"
  - "真实业务保存→换核心→恢复及物理掉电未验收"
next_owner: A/C
next_action: 已知持久检查点重启写闸门已补；独立备份位置/冻结地址已补；继续完整来源/核验闭包、未知活动与实际 generation 迁移/恢复。
---

## 2026-10-15 全目录复核

迁移独立备份位置、周期内冻结地址、旧记录兼容与已知重启写闸门已补；未知活动、完整 generation 恢复及物理掉电仍未验证。 本批合同/消费方检查与未验证范围见[证据](../../../validation/p1-interface-review-20261015/README.md)。历史章节只证明对应旧版本，签署与整体对拍状态不代填。


## 当前续修与验证范围（76ab7d2 起点）

已知持久检查点重启写闸门已补；独立备份位置/冻结地址已补；继续完整来源/核验闭包、未知活动与实际 generation 迁移/恢复。 [本次准确实现/证据](../../../validation/p1-completion-repair-20261013/README.md)。生成 ReadViewQuery 来自 Python 源码；旧协议字段/历史记录可读，旧核心按能力显式降级。reviewing/partial/not_run 和历史签署/last_verified_commit 保持，局部测试不等于真实对拍。

## 上一批实现修复（2026-10-13）

现行版本：1.9；修复起点 fix@7b931af，准确代码指纹与原输出见[本轮修复](../../../validation/p1-boundary-fixes-20261013/README.md)。维护漏写已修；继续跨进程维护、完整来源/核验闭包、未知活动与实际备份/恢复对拍。

本轮依一期功能第3/12/15节及架构01/03/04/05修实现，没有另定 FR/AC 或修改项目规范；历史签署/last_verified_commit 保留，reviewing/partial/not_run 不变。新增报告内部伴随执行快照/阻塞引用可读旧记录，旧无来源指针的材料明确缺口，不落回最新运行；公开 Command/Response/生成 Schema 不改。select/inspect 使用既有执行 SnapshotContentRef 的 snapshot_commit_id/snapshot_cursor/digest，修复错误摘要字段读取，不混业务源码快照。

原 A-20、C-18、D-11—14 的代码关闭仅依本轮回归；C-17 无可信动态资格时继续拒绝 R。局部/类型夹具不等于真实 Trae、业务数据/供应方故障、物理掉电或完整合同对拍。下方原审查内容仅保留历史问题来源。


## 修复前源码复核（7b931af 的原记录）

现行版本：1.7；源码 `fix@a51c2b4`。本版只同步实现、阻塞与交付边界，不改公开协议/Schema、字段签名、保存数据或 FR/AC，不需迁移。历史双方确认、last_verified_commit=null 保留；reviewing / partial / not_run 不因局部回归改成对拍完成。

**已实现及原缺陷修复**：备份三模式与迁移五模式已有受控默认入口；query 保留 commit/event 配对，对象分块读取已有底层实现。C-15/C-16 原准备/核验阻断已修。维护状态跨进程、备份位置配置、副本索引重建/活动待核实和真实恢复继续验证；A-20 已用实际 LocalAPI 闸门复现。

**当前阻塞与下一动作**：A 覆盖全部注册写动作的维护限制并完善活动恢复；A/C 核对备份/迁移、检查点/证据/意图闭包及来源事实准确保存；补真实重启恢复。 具体反例、组件替身范围与原输出见[本轮验证](../../../validation/p1-fix-review-20261013/README.md)和[逐 FR/AC 对照](../../../一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)，当前状态只查[项目现状](../../../现状.md)。

本轮未完成真实 Trae、实际业务对象/供应方故障、物理掉电或全部合同场景提供/消费方对拍，未新增双方签署。以下历史复核段中的旧缺项/数量只对应原日期；仍有效的正式字段和版本增量继续按正文消费。


## 历史源码复核记录（2026-10-12）

本次全源码完成性复核（文档起点 fix@708439b）更正前轮默认接线结论：真实保存材料经未替换默认解析器无法 prepare_execution（C-15），兼容字段替身下语法错/部分导入失败仍 verified（C-16）。组件已注入不等于可信默认链已贯通。[原脚本/实际输出及替身边界](../../../validation/p1-source-completion-20261012/README.md)与[逐 FR/AC 对照](../../../一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)分别说明；本次仅修实现状态/阻塞记录，字段签名/Schema/已存数据/历史确认保持，无数据迁移，不补签或标对拍完成。

本版 1.6 只同步实现状态、阻塞项及历史内容的生效边界；**不改变端口签名、字段/枚举、协议版本、生成 Schema/夹具或已保存记录语义**，无需数据迁移。取证 `develop@845e697`，整改/证据文档基线 `90baba0`。此记录为自动化源码复核，**不替代提供方/消费方确认**；reviewing/partial 与原确认历史保留。

**已实现**：ExecutionCommitCoordinator、准确检查点引用、命令永久输出/脱敏材料、持久外部导入附件和独立查询材料保存已有实现。基础存储/恢复并非空端口。

**仍缺／优先动作**：默认 business_verification_resolver/port 为 None，verify_pending 注册不等于业务核验可执行。真实损坏备份、符号链接权限、未知停止/物理掉电仍待完整实测。

**验证范围**：本轮局部合同/Schema/夹具和文件往返按[接口复核证据](../../../validation/p1-interface-review-20261012/README.md)登记。合同全部场景的统一提供/消费方对拍未完成，因此 verification_status 保持 not_run，不能因部分回归自动标为对接完成。last_verified_commit 保持 null，没有为未完成的统一对拍代填提交号。真实 Trae/供应方/掉电与 AC 证据另验。

字段和业务语义仍以对应主责分册与本合同正文为准；以下带日期的版本增量、草案、确认和实施记录保留其当时基线，其中“未注入／无签名／待接线”和旧动作数量不再作为今天的状态。当前关闭条件查四包检查和[项目现状](../../../现状.md)。


# A包C包存储恢复公共接口约定

历史正文版本：1.5（2026-10-12 状态复核；历史修订与确认按各自日期）
日期：2026-10-12（历史记录保留原条目日期）
状态：A/C 跨包存储与恢复接口对齐（草案）  
提供方：A包（本地核心底座）  
调用方：C包（执行编排）  
协议版本：`aitest.local/2.0`  
合同源码：`src/aitest/application/ports.py`、`src/aitest/contracts/execution_facts.py`、`src/aitest/infrastructure/file_store/`

1.4 按CORE-001 1.39补退出准入：执行句柄/输出侧录只是检查线索，退出前还须回读权威execution_checkpoint的准确项目/Attempt/当前修订与保存的真实退出事实。缺句柄但已有启动意图同样保留待核实，不能因无活动标记或假终态侧录允许退出。检查失败保留核心与材料；只有已核实执行终止且必要材料保存后方可退出。该维护核对不执行新业务、不自动补查/重试、不改运行或业务结论。

1.3 按CORE-001 1.38增加外部结果保存。`external_import`不可变记录包含准确项目/工作空间、外部身份、安全文档完整对象引用及安全附件引用、来源缺口；`execution_intent`回执绑定原意图、文档摘要和准确导入@1。同来源/内容返回原记录，改变任何决定性安全材料（层级/源码/运行尝试/实际输出/自述/Mock/附件）冲突，提交失败不占位。回读须实际校验原文及全部附件项目/大小/摘要与规范JSON，未知来源不升级为内部采集或业务验证；普通导入既不启动业务也不改ExecutionFacts。附件无可核对安全材料时阻塞，不落未过滤字节。通过现有对象/记录端口及UOW实现，不另建文件写入服务或旁路索引。

1.2 按CORE-001 1.37补独立业务查询材料的保存边界。业务查询端口属于`application/ports.py`；采集端口返回独立查询实际JSON和事实，适配器不直接写业务记录。应用用例冻结预期与实际JSON，凭据过滤在对象发布前完成，所有摘要描述安全字节。`execution_intent`中的不可变核验登记绑定项目/工作空间/Run/Step/Attempt/原快照与请求摘要；`verification`记录保存完整对象引用、证据/核验事实、准确结果快照及幂等回执。对象、引用与快照同一权威提交可核对，回读重算同一领域比较规则，不把外部matched声明或技术hash核验升级为业务通过。已登记而结果未可靠保存不自动重查，历史结果不覆盖新Attempt。旧瞬时`verify`继续作为组件事实接口，不提供持久材料或默认验收证明；新增`capture`和可信解析端口仅用于上述保存链。

## 范围声明

本文档为**接口契约设计**。根据项目负责人 2026-09-30 裁定，`SourceSnapshotPort` / `SourceControlPort` / `ModelProvider` / `ProjectionPort` 的一期所需方法签名，以及准备意图的同键幂等、冲突与同次提交语义，均属于**一期范围**，不得再统一推迟到一期之后。`CheckpointPort` 是否需要独立 Protocol 仍按 A/C 的实际调用面确认，不以空接口制造承诺。

A 包一期底座已交付的能力（事务、记录、索引、对象存储、Spool、检查点、恢复）可直接使用；待定义的当期端口签名由 A 包写入唯一 `application/ports.py`，业务包确认语义并完成合同测试。

## 1 模块概述

本文档对齐 A 包与 C 包之间的存储与恢复公共接口。C 包只依赖 A 包对外端口（`application/ports.py` 中定义的 Protocol），禁止直接读写工作空间底层文件布局。

A 包负责：工作空间身份、单写事务、不可变记录、有限索引查询、内容寻址对象存储、Spool 流持久化、恢复检查点、恢复扫描。

C 包负责：执行编排、调用 A 包端口发布材料、提交事务、读取恢复检查点续跑。C 包不得直接读写 `records.json`、`indexes.json`、`commit.json`、`events.json`、`checkpoints/`、`spool/`、`objects/` 等底层文件。

## 2 公共约定

### 2.1 事务原子性

A 包所有写入必须通过 `WorkspaceUnitOfWork` 事务边界提交。`commit()` 在同一方法内依次发布：活动标记 → 事件 → 提交清单 → 索引 → **业务记录（最后，事实来源）**。任一投影写入失败时旧提交边界保持有效，活动标记供恢复识别。

C 包不得在事务外直接调用 `RecordRepository.append` 或 `append_intent`。

### 2.2 追加只读

业务记录、历史修订、证据对象、Spool 块、检查点只追加保存。A 包不提供永久业务数据删除或覆盖接口。

### 2.3 事务错误码分类

事务相关错误按以下分类返回，C 包应据此决定重试、回滚或上报维护：

- `expected_revision` 与当前修订不一致 → `RECORD_REVISION_CONFLICT`，不自动覆盖。
- 同一工作空间已有活动写事务 → `WORKSPACE_IN_USE` / `TRANSACTION_CONFLICT`。
- 非活动事务上的提交/回滚 → `INVALID_TRANSACTION_STATE`。

### 2.4 索引缺失处理

查询接口在索引缺失、损坏或版本不兼容时返回 `status="maintenance_required"` 结构化状态，**不抛出原始异常，不隐式全表扫描**。C 包收到该状态必须保留维护缺口，不得降级为全表读取。

## 3 WorkspaceUnitOfWork 与 RecordRepository

### 3.1 WorkspaceUnitOfWork 端口（ports.py）

```python
class WorkspaceUnitOfWork(Protocol):
    def open(self, project_id: str) -> None: ...
    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: dict[str, object],
    ) -> object: ...
    def commit(self) -> object: ...
    def rollback(self) -> None: ...
```

### 3.2 FileUnitOfWork 最终签名

```python
class FileUnitOfWork:
    def begin(self, request_id: str, project_id: str,
              workspace_id: str | None = None,
              intent_id: str | None = None) -> dict
    def open(self, project_id: str) -> None
    def stage_record(self, *, aggregate_kind: str, record_id: str,
                     expected_revision: int | None,
                     payload: Mapping[str, object]) -> int
    def commit(self, request_id: str | None = None,
               workspace_id: str | None = None) -> dict
    def rollback(self, request_id: str | None = None,
                 workspace_id: str | None = None) -> dict
    def recover(self, workspace_id: str) -> dict
```

### 3.3 提交语义

| 方法 | 语义 |
| --- | --- |
| `begin` | 校验 `workspace_id`，获取 OS 单写锁，设置 `request_id`/`intent_id`，返回 `{"request_id","state":"active","intent_id"}` |
| `stage_record` | 校验 `expected_revision` 与当前修订一致，追加暂存，返回新修订号 |
| `commit` | 调用 `commit_transaction()` 原子发布 intent/业务记录/提交清单/索引/事件，`try/finally` 兜底释放锁，返回 `{"request_id","state":"committed","created","commit_sequence"}` |
| `rollback` | 丢弃暂存，释放锁，返回 `{"request_id","state":"rolled_back","intent_id"}` |
| `recover` | 返回工作空间事务状态，不重放未知副作用 |

**锁释放保证**：`commit()` 使用 `try/finally` 调用 `_release()`，无论 `commit_transaction()` 正常返回还是异常抛出，OS 单写锁都会释放。提交失败后事务状态保留，C 包可调用 `rollback(original_request_id)` 清理。

### 3.4 RecordRepository 端口

```python
class RecordRepository(Protocol):
    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> object: ...
    def query(self, query: object) -> object: ...
```

`query()` 返回 `RecordQueryResult(status, items, next_cursor)`，`status` 取值：`ok` / `maintenance_required` / `invalid_cursor`。

## 4 ExecutionFacts 快照

### 4.1 模型来源

`contracts/execution_facts.py` 定义跨包冻结 schema，`schema_version` 固定为 `aitest.execution-facts/1.0`。C 包使用同一模型构建快照，A 包通过事务持久化。

### 4.2 单 commit 保存读取规则

- 每个 Run 对应一个 `ExecutionFacts` 快照，通过**一次** `WorkspaceUnitOfWork.commit()` 原子发布。
- 快照作为一条业务记录（`aggregate_kind` 由 C 包约定，如 `execution_facts`）写入 `records.json`，修订号单调递增。
- 读取时通过 `RecordRepository.read(aggregate_kind, record_id, revision)` 按修订读取，或通过 `query()` 按项目/类型分页查询。

### 4.3 snapshot_cursor

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `snapshot_cursor` | `int` (≥0) | 快照内事实集合的逻辑游标，用于增量同步；C 包在每次发布时递增 |
| `snapshot_revision` | `int` (≥1) | 快照修订号，与 `record_revision` 对齐 |
| `snapshot_commit_id` | `str` | 本次发布的提交身份，关联 `commit.json` 中 `commit_sequence` |

### 4.4 expected_revision 与幂等

- `stage_record` 必须传入 `expected_revision`，与 A 包当前修订不一致时抛 `RECORD_REVISION_CONFLICT`。
- 同一 `intent_id` 重复提交同一输入摘要时，`commit_transaction` 不重复执行，返回已存在的 `commit_sequence`。
- C 包重跑必须创建新 `intent_id`，不得复用旧意图冒充新执行。

## 5 AttemptFact / ExitFact / RedactionSummary / 对象引用 / 缺口字段

### 5.1 AttemptFact.output_cursors

```python
class OutputCursorFact(ContractModel):
    attempt_id: str
    stream_name: str
    offset: int          # ≥0
    last_block_index: int  # ≥0
    last_committed_digest: str
    durable: bool = False
```

`output_cursors` 记录每个流已持久化到 Spool 的字节偏移和最后块摘要。`durable=True` 表示对应块已 fsync 落盘。C 包崩溃恢复时从 `last_block_index` 和 `last_committed_digest` 继续采集，不得回退已提交偏移。

### 5.2 ExitFact.timed_out

```python
class ExitFactDTO(ContractModel):
    attempt_id: str
    startup_token: str
    process_start_identity: str
    real_exit_code: int | None = None
    last_block_index_by_stream: dict[str, int]
    saved_bytes_by_stream: dict[str, int]
    capture_completeness: CaptureCompletenessFact
    termination_reason: ProcessTerminationReasonFact
    timed_out: bool = False
    published_at: datetime | None = None
```

`timed_out` 表示该 Attempt 是否因超时终止。`termination_reason` 取值：`natural_exit` / `timeout` / `confirmed_stop` / `executor_lost` / `capture_failure` / `unknown`。当 `termination_reason == "timeout"` 时 `timed_out` 必须为 `True`。

### 5.3 RedactionSummary

```python
class RedactionSummaryFact(ContractModel):
    policy_version: str
    applied_rule_categories: tuple[str, ...] = ()
    filtered_streams: tuple[str, ...] = ()
    filtered_ranges: tuple[str, ...] = ()
    replacement_count: int = 0    # ≥0
    completeness: str = "unknown"
    gap_reasons: tuple[str, ...] = ()
    created_at: datetime | None = None
```

脱敏摘要由 C 包在输出过滤时生成，通过 `SpoolStore.persist_redaction_summary()` 持久化，返回 `summary_id`。AttemptFact 和 OutputBlockFact 通过 `redaction_summary` / `redaction_summary_id` 引用。

### 5.4 对象引用

证据对象通过 `StoredObjectRef`（`project_id`、`digest`、`size`、`media_type`、`relative_path`）引用。`EvidenceFact.object_digest` 和 `object_size` 必须与对象存储中的实际摘要和大小一致。C 包发布证据前必须先调用 `EvidenceObjectStore.publish_bytes()` 获取引用。

### 5.5 缺口字段持久化

| 字段 | 位置 | 语义 |
| --- | --- | --- |
| `gap_ids` | RunFact / StepFact / AttemptFact / EvidenceFact / VerificationFact 等 | 关联 `EvidenceGapFact.gap_id`，记录证据缺口 |
| `unknown_reason_ids` | AttemptFact | 关联 `UnknownReasonFact.unknown_reason_id`，记录未知原因 |
| `primary_gap_ids` | RunFact | Run 级主要缺口 |
| `completeness` | ExecutionFacts | `complete` / `partial` / `unknown` |

缺口事实（`EvidenceGapFact`、`UnknownReasonFact`）作为 `ExecutionFacts` 快照的一部分随单 commit 发布，不得独立于快照写入。

## 6 EvidenceObjectStore

### 6.1 端口定义

```python
class EvidenceObjectStore(Protocol):
    def publish_bytes(
        self,
        project_id: str,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObjectRef: ...
    def read_bytes(self, ref: StoredObjectRef) -> bytes: ...
```

### 6.2 项目隔离

对象按 `objects/<project_id>/<sha256_hex>` 路径存储，`project_id` 必须为安全路径组件（不含 `\/:*?"<>|`）。不同项目的对象物理隔离，C 包不得跨项目读取。

### 6.3 内容寻址

- 摘要算法：`sha256`，digest 格式 `"sha256:<hex>"`。
- 同摘要同内容重复发布幂等，不报错；同摘要不同内容抛 `ValueError("content-addressed object conflicts")`。
- 写入使用 `tempfile` + `os.fsync` + `os.replace` 原子发布，崩溃不会留下半成品。

### 6.4 摘要校验

`read_bytes(ref)` 读取后校验：
1. `len(content) == ref.size`，否则抛 `object size mismatch`
2. `"sha256:" + sha256(content).hexdigest() == ref.digest`，否则抛 `object digest mismatch`

C 包不得绕过摘要校验直接读文件。

### 6.5 读取 API

C 包只能通过 `read_bytes(ref: StoredObjectRef)` 读取，`ref` 来自 `publish_bytes` 返回值或已提交记录中的引用。不得直接拼接文件路径读取 `objects/` 目录。

## 7 RecoveryCheckpoint

### 7.1 维护方

RecoveryCheckpoint 由 **A 包维护**，C 包通过 A 包端口读写，C 包原有本地 `FileCheckpointStore` 将被替换为 A 包提供的统一实现。

### 7.2 当前实现接口

```python
class FileCheckpointStore:
    def persist(self, record: RecoveryRecord) -> Path
    def load(self, attempt_id: str) -> RecoveryRecord
    def scan(self) -> tuple[RecoveryRecord, ...]
```

- `persist`：每个 Attempt 持久化一条检查点记录到 `checkpoints/<attempt_id>.json`，使用 `atomic.write_json` 原子写入。
- `load`：按 `attempt_id` 读取检查点。
- `scan`：启动时扫描 `checkpoints/` 目录下所有 `*.json`，返回全部未完成的 `RecoveryRecord` 列表。

### 7.3 对外端口

C 包应通过 A 包定义的端口（如 `CheckpointPort`）调用，不得直接实例化 `FileCheckpointStore`。端口方法至少包含 `persist` / `load` / `scan`，签名与上述一致。

### 7.4 启动扫描方式

A 包在核心启动时调用 `scan()` 扫描检查点目录，返回的 `RecoveryRecord` 列表供 C 包决定哪些 Attempt 需要续跑。扫描只读，不修改任何文件。

## 8 恢复协议与崩溃恢复顺序

### 8.1 三类发布是否同一恢复协议

Spool 对象发布、Checkpoint 提交、ExecutionFacts 发布**不属于同一个事务**，但遵循统一的恢复协议：

| 发布物 | 持久化方式 | 原子性 | 恢复方式 |
| --- | --- | --- | --- |
| Spool 流块 | `SpoolStore` 流式写入 + fsync，manifest 原子更新 | 块级原子 | `salvage_streams()` 抢救未密封尾部 |
| Checkpoint | `FileCheckpointStore.persist()` 原子写入 | 单文件原子 | `scan()` 扫描未完成记录 |
| ExecutionFacts | `WorkspaceUnitOfWork.commit()` 五者原子发布 | 事务原子 | 按 `snapshot_cursor` 增量续跑 |

### 8.2 崩溃后的恢复顺序

A 包核心启动后，C 包按以下顺序恢复：

1. **事务恢复**：调用 `recover_workspace(root)` 检查 `transactions/active.json`。若存在活动标记，说明上次 `commit` 中断；旧提交边界有效，陈旧投影（events/indexes/commit）由 A 包清理。
2. **检查点扫描**：调用 `FileCheckpointStore.scan()` 获取所有未完成 Attempt 的 `RecoveryRecord`。
3. **Spool 抢救**：对每个未完成 Attempt 调用 `SpoolStore.salvage_streams(attempt_id)`，将流文件中未密封的尾部字节封装为 `complete=False` 的块，更新 manifest。
4. **快照续跑**：C 包根据 `ExecutionFacts` 的 `snapshot_cursor` 和各 Attempt 的 `output_cursors` 决定从何处继续采集，已提交偏移不得回退。
5. **重新提交**：续跑产生的新事实通过新的 `commit()` 发布，创建新的 `commit_sequence`，不覆盖旧修订。

### 8.3 恢复约束

- 恢复只读取和抢救，不自动重放外部执行或网络调用。
- `active.json` 标记的中断事务不得盲目重提交；由 C 包根据检查点决定是否重新发起。
- 索引缺失时查询返回 `maintenance_required`，恢复流程不得全表扫描补偿。

## 9 C包替换 FileCheckpointStore 约束

1. C 包不得直接引用 `aitest.infrastructure.file_store.checkpoints.FileCheckpointStore`。
2. C 包通过 A 包端口（由 `bootstrap` 装配注入）获取检查点存储能力。
3. 端口签名以 `ports.py` 中定义为准，A 包保证实现与端口一致。
4. 若 C 包已有本地检查点实现，迁移时数据格式以 `aitest.recovery-checkpoint/1.0` schema 为准。

## 10 与A包对外接口与装配约定的关系

本文档是 `A包对外接口与装配约定.md` 在存储与恢复领域的专项对齐，不替代主约定。冲突时以 `A包对外接口与装配约定.md` 为准。


## 2026-10-13 后续补证（76ab7d2 起点）

进程内维护窗口之外，普通写入还核对原迁移步骤检查点与成功注册：未登记的 prepared、rollback_pending 或不可核实检查点跨核心重启后继续阻塞新业务写入，诊断/受控迁移入口保留。检查不重放迁移、不把进程重启当作收尾成功。此项只是已知持久检查点的恢复闸门；未知活动核实、独立备份位置、新 generation 迁移和物理掉电仍需单独证明。


2026-10-14 隔离续检修复（本次 fix 发布）：准入源观察随内部 resolved-execution-action/1.1 保存原始对象元数据，旧 1.0 字节形状保留；原授权核对后与启动快照/evidence_ref 同事务发布。没有原始对象或过滤改变材料时不得升为已核实；普通 DTO ExecutionFacts 1.0 不新增字段。缓存、ZIP 父子冲突等证据与原子/重启未完成检查见[候选续检](../../../validation/p1-unproven-audit-20261014/README.md)。保持 reviewing/partial/not_run，不写双方实测或修改旧签署。


2026-10-14 隔离候选备份续修：备份闭包只豁免根清单，嵌套同名文件仍核对；严格 UTF-8 JSON/1.0 格式、有限清单读取、路径别名与重复字段拒绝，结构化永久材料不允许重复字段隐藏转义已知凭据。安全整文件32项通过，备份/恢复/迁移既有文件与新候选全量仍待串行结果；不宣称完整历史、独立位置/世代迁移或物理掉电通过。字段、版本、reviewing/partial/not_run、签署与last_verified_commit保持；见[本批备份反例](../../../validation/p1-unproven-audit-20261014/README.md#继续修复的备份反例)。


2026-10-14 准入/实际运行来源发布边界：准入原始证据保存不等于后续业务进程加载范围已证明；当前快照保留 unverified/actual_execution_load_source_unverified，原授权准入观察不追改，mismatch/blocked/unknown 不降级。64项轻量选择通过，真实事务和实际加载仍待；公开DTO字段、接口版本/签署/not_run保持，见[准确反例](../../../validation/p1-unproven-audit-20261014/README.md#准入与实际运行来源不得混同)。


2026-10-14 零字节永久引用回收守卫续修：候选识别和删除前复核均核对空字节摘要，空事件暂存不得绕过永久引用。两份完整受改小型文件 37 项通过，新增六个节点；5403 节点仅收集，旧 14ea0ca/5397 全量仍在执行。此为既有不可回收永久材料条件的实现修复，公开字段/版本、reviewing/partial/not_run、签署和 last_verified_commit 不变；完整历史闭包/迁移/真实恢复仍开放，见[本批证据](../../../validation/p1-empty-reference-20261014/README.md)。


2026-10-14 迁移备份身份续修：内部周期冻结既有 manifest_facts 材料摘要，续跑拒绝副本改写/消失后重采样、旧活动周期无摘要和已有前态无周期；无本轮归属副本保留，新一轮创建自己的前态。最后一步回滚保存但周期收尾中断时，依据全部 rolled_back 为当前新一轮备份，不沿用旧前态。备份异常统一 MigrationError，13个新节点及两个完整小型文件47项通过，5416仅收集；公开DTO/动作/Schema/合同版本、reviewing/partial/not_run及签署保持。旧5397全量不证明本批，真实恢复/完整历史/独立位置仍欠，见[本批证据](../../../validation/p1-migration-backup-identity-20261014/README.md)。


本批最终范围更正：受控apply/resume已补准确检查点计划/步骤/已知状态及周期一致性，非字符串状态标准拒绝。最新两个完整受改文件58项（24新增）通过，5427仅收集；前47/5416为阶段范围，保留原回执。旧5397全量18:26因验证ledger误受os.fsync故障注入而exit3，1112/2非全量通过。新记录器实际故障注入38项通过，新全量另见[新闭环](../../../validation/p1-migration-closure-20261014/README.md)，不改版本/签署/not_run。


2026-10-15独立位置实施续修：可信装配migration_backup_root、宿主环境AITEST_MIGRATION_BACKUP_ROOT及worker选项接入同一配置；外部工作空间目录隔离，活动周期冻结原backup_path，配置更改/取消仍核对原副本，旧周期固定位置不追改。三完整小型文件82通过、独立原生CLI1通过且外置备份现场校验通过，5451仅收集；旧5427已31.5%外部中断。未配置兼容模式仍回执本地位置，不假称独立存储满足。公开Command/DTO/端口/Schema/动作及版本、签署/not_run不变，宿主设置/完整历史/真实业务与恢复未闭合，见[本批范围](../../../validation/p1-migration-location-20261014/README.md)。
