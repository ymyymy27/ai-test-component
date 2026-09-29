# A包C包存储恢复公共接口约定

版本：1.0  
日期：2026-09-29  
状态：A/C 跨包存储与恢复接口对齐（草案）  
提供方：A包（本地核心底座）  
调用方：C包（执行编排）  
协议版本：`aitest.local/2.0`  
合同源码：`src/aitest/application/ports.py`、`src/aitest/contracts/execution_facts.py`、`src/aitest/infrastructure/file_store/`

## 范围声明

本文档为**接口契约设计**。相关代码实现（`CheckpointPort` 端口定义、`SourceSnapshotPort` / `SourceControlPort` / `ModelProvider` / `ProjectionPort` 方法签名、`stage_preparation` 等）放在 **A 包一期交付之后迭代**，不属于 A 包一期底座开发范围。

A 包一期底座已交付的能力（事务、记录、索引、对象存储、Spool、检查点、恢复）可直接使用；本文档中待定义的端口签名在后续迭代中由 A 包统一写入 `application/ports.py`。

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
