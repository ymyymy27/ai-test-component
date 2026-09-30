# A-C 存储恢复接口约定评审意见

> **评审历史说明（2026-09-30）**：本文件中“未冻结端口签名放到一期之后”的建议已被项目负责人裁定覆盖。现行一期范围以同目录 `contract.md` v1.1、AB-001 第 10 节和总体架构为准；本文件仅保留当时评审过程。

版本：1.0  
日期：2026-09-29  
评审方：LU（A 包负责人）  
评审对象：`docs/接口对接/进行中/AC-001-存储与恢复/contract.md`（v1.0）
依据：`src/aitest/application/ports.py`、`src/aitest/infrastructure/file_store/`、`docs/文档-LU/A包对外接口与装配约定.md`

---

## 1 评审背景与目标

C 包已有本地验证实现（`FileSpoolStore`、`FileObjectStore`、`FileCheckpointStore` 等），存在与 A 包底座重复实现文件布局的风险。本次评审目标为检查同目录 `contract.md` 是否完整覆盖 C 包提出的全部对齐需求，确保 C 包后续只依赖 A 包对外端口，不直接操作底层文件布局，避免两套文件实现。

---

## 2 需求覆盖检查清单

### ① WorkspaceUnitOfWork、RecordRepository 的最终方法签名和提交语义

| 项 | 内容 |
| --- | --- |
| **需求原文** | WorkspaceUnitOfWork、RecordRepository 的最终方法签名和提交语义 |
| **文档覆盖情况** | §3.1 `WorkspaceUnitOfWork` Protocol 签名（open/stage_record/commit/rollback）；§3.2 `FileUnitOfWork` 最终签名（begin/open/stage_record/commit/rollback/recover）；§3.3 提交语义表格 + 锁释放保证（`try/finally` 兜底释放 OS 单写锁）；§3.4 `RecordRepository` 端口（read/query）+ `RecordQueryResult` status 取值（ok/maintenance_required/invalid_cursor）。 |
| **结论** | ✅ 已覆盖。端口签名、最终实现签名、提交语义、锁释放保证均完整。 |

### ② ExecutionFacts 快照单 commit 保存读取，snapshot_cursor、expected_revision、幂等处理

| 项 | 内容 |
| --- | --- |
| **需求原文** | ExecutionFacts 快照单 commit 保存读取，snapshot_cursor、expected_revision、幂等处理 |
| **文档覆盖情况** | §4.1 模型来源（`aitest.execution-facts/1.0`）；§4.2 单 commit 保存读取规则（每个 Run 一次 commit 原子发布，修订号单调递增）；§4.3 `snapshot_cursor` 字段表（snapshot_cursor/snapshot_revision/snapshot_commit_id）；§4.4 `expected_revision` 冲突（`RECORD_REVISION_CONFLICT`）+ intent 幂等（同 intent_id 同摘要不重复执行）。 |
| **结论** | ✅ 已覆盖。单 commit 发布、snapshot_cursor、expected_revision、幂等四要素齐全。 |

### ③ AttemptFact.output_cursors、ExitFact.timed_out、RedactionSummary、对象引用、缺口字段持久化

| 项 | 内容 |
| --- | --- |
| **需求原文** | AttemptFact.output_cursors、ExitFact.timed_out、RedactionSummary、对象引用、缺口字段持久化 |
| **文档覆盖情况** | §5.1 `AttemptFact.output_cursors` 完整 DTO（offset/last_block_index/last_committed_digest/durable）+ 恢复语义；§5.2 `ExitFact.timed_out` 完整 DTO + 与 `termination_reason` 联动规则（timeout 时 timed_out 必须 True）；§5.3 `RedactionSummary` 完整 DTO + `persist_redaction_summary()` 持久化；§5.4 对象引用 `StoredObjectRef` 字段 + `EvidenceFact` 关联约束；§5.5 缺口字段表格（gap_ids/unknown_reason_ids/primary_gap_ids/completeness）+ 随单 commit 发布约束。 |
| **结论** | ✅ 已覆盖。五个字段/对象的 DTO、语义和持久化规则均完整。 |

### ④ EvidenceObjectStore：项目隔离、内容寻址、摘要校验、读取 API

| 项 | 内容 |
| --- | --- |
| **需求原文** | EvidenceObjectStore：项目隔离、内容寻址、摘要校验、读取 API |
| **文档覆盖情况** | §6.1 端口定义（publish_bytes/read_bytes）；§6.2 项目隔离（`objects/<project_id>/<sha256_hex>`，安全路径组件校验）；§6.3 内容寻址（sha256，同摘要幂等，同摘要异内容冲突）；§6.4 摘要校验（size + digest 双重校验）；§6.5 读取 API（仅 `read_bytes(ref)`，禁止拼接路径）。 |
| **结论** | ✅ 已覆盖。项目隔离、内容寻址、摘要校验、读取 API 四要素齐全。 |

### ⑤ RecoveryCheckpoint：维护方、端口、扫描方式，C 替换 FileCheckpointStore

| 项 | 内容 |
| --- | --- |
| **需求原文** | RecoveryCheckpoint：维护方、端口、扫描方式，C 替换 FileCheckpointStore |
| **文档覆盖情况** | §7.1 维护方（A 包维护，C 替换本地 FileCheckpointStore）；§7.2 当前实现接口（persist/load/scan）；§7.3 对外端口（CheckpointPort，C 不得直接实例化 FileCheckpointStore）；§7.4 启动扫描方式（A 核心启动调用 scan()，只读不修改）；§9 C 替换 FileCheckpointStore 四条约束。 |
| **结论** | ✅ 已覆盖。维护方、端口、扫描方式、C 替换约束均明确。 |

### ⑥ Spool、Checkpoint、ExecutionFacts 发布在同一恢复协议、崩溃恢复顺序

| 项 | 内容 |
| --- | --- |
| **需求原文** | Spool、Checkpoint、ExecutionFacts 发布在同一恢复协议、崩溃恢复顺序 |
| **文档覆盖情况** | §8.1 三类发布统一恢复协议表格（Spool 块级原子、Checkpoint 单文件原子、ExecutionFacts 事务原子，各自恢复方式）；§8.2 崩溃恢复顺序 5 步（事务恢复→检查点扫描→Spool 抢救→快照续跑→重新提交）；§8.3 恢复约束（不重放外部调用、不盲目重提交 active 事务、不全表扫描补偿）。 |
| **结论** | ✅ 已覆盖。统一恢复协议、崩溃恢复顺序、恢复约束三部分完整。 |

### ⑦ 核心约束：C 只依赖 A 对外端口，不直接操作底层文件布局

| 项 | 内容 |
| --- | --- |
| **需求原文** | 核心约束：C 只依赖 A 对外端口，不直接操作底层文件布局 |
| **文档覆盖情况** | §1 模块概述明确禁止直接读写 records.json/indexes.json/commit.json/events.json/checkpoints/spool/objects；§2.1 事务原子性（C 不得在事务外调用 append/append_intent）；§2.3 索引缺失返回 maintenance_required；§9 C 不得直接引用 FileCheckpointStore；§8.3 恢复流程不得全表扫描补偿。 |
| **结论** | ✅ 已覆盖。约束散见于各章节，覆盖全面。 |

---

## 3 风险项

| 风险 | 说明 | 建议 |
| --- | --- | --- |
| 端口签名未冻结 | `CheckpointPort`、`SourceSnapshotPort`、`SourceControlPort`、`ModelProvider`、`ProjectionPort` 方法签名放在 A 包一期交付之后迭代，当前以 `FileCheckpointStore` 接口为参考 | C 包一期仅使用 A 已交付能力，待端口签名冻结后再对接 |
| RedactionSummary 持久化方法 | 文档提 `SpoolStore.persist_redaction_summary()`，需确认 A 包 SpoolStore 是否已提供 | C 包确认 A 包 SpoolStore 方法清单 |
| `recover_workspace(root)` 调用方 | §8.2 引用该函数，未明确由 A 核心启动调用还是 C 显式调用 | 双方确认调用边界 |
| aggregate_kind 取值 | ExecutionFacts 的 `aggregate_kind` 文档写"由 C 包约定"，未固化 | C 包确认实际取值后回写文档 |

---

## 4 待双方确认事项

1. **ExecutionFacts `aggregate_kind` 实际取值**：文档写"由 C 包约定（如 `execution_facts`）"，需 C 包确认实际使用的聚合类型名与记录 ID 规则。
2. **`SpoolStore.persist_redaction_summary()` 方法是否存在**：若 A 包 SpoolStore 未提供，需确认是扩展 SpoolStore 还是新增专用端口。
3. **`recover_workspace(root)` 调用方**：由 A 包核心启动时调用，还是 C 包显式调用。
4. **`CheckpointPort` 正式定义时机**：当前以 `FileCheckpointStore` 接口为参考，需确认 A 包何时在 `ports.py` 中正式定义 Protocol。
5. **C 包本地 `FileSpoolStore`/`FileObjectStore`/`FileCheckpointStore` 迁移计划**：确认替换为 A 包实现的时间节点与数据迁移方式。

---

## 5 评审结论

**文档已对齐 C 包提出的全部 7 项需求**，覆盖 WorkspaceUnitOfWork/RecordRepository 签名、ExecutionFacts 快照、AttemptFact/ExitFact/RedactionSummary/对象引用/缺口字段、EvidenceObjectStore、RecoveryCheckpoint、恢复协议、核心约束。

**结论：可进入 C 包审阅。** 确认上述 5 项待确认事项后，新建独立契约分支提交 PR。代码实现（未冻结端口签名）放在 A 包一期交付之后迭代，不属于 A 包一期底座开发范围。
