# B-A 端口与保存需求评审意见

版本：1.0  
日期：2026-09-29  
评审方：LU（A 包负责人）  
评审对象：`docs/接口对接/B-A-端口与保存需求.md`（v0.3）  
依据：`src/aitest/application/ports.py`、`src/aitest/infrastructure/file_store/`、`docs/文档-LU/A包对外接口与装配约定.md`

---

## 1 已满足项（A 包现有能力）

以下 B 包提出的需求，A 包一期底座已实现，B 包可直接使用：

| B 的诉求 | A 包现有能力 | 位置 |
| --- | --- | --- |
| 短事务边界、同一事务提交记录/引用/索引 | `FileUnitOfWork.commit()` → `commit_transaction()` 五者原子发布 | `infrastructure/file_store/records.py` |
| 单写锁，外部调用不持长锁 | `portalocker.LOCK_EX \| LOCK_NB`，事务外不持有 | `infrastructure/file_store/locking.py` |
| 按准确修订读取，不回退最新 | `RecordRepository.read(aggregate_kind, record_id, revision)` | `infrastructure/file_store/records.py` |
| 列表读摘要、详情按引用读取 | `RecordRepository.query()` 走索引返回摘要 + `read()` 按修订读详情 | `infrastructure/file_store/records.py`、`index.py` |
| 索引缺失返回 `maintenance_required`，不隐式全表扫描 | `IndexQueryResult(status="maintenance_required")`，查询只读 `indexes.json` | `infrastructure/file_store/index.py` |
| `expected_revision` 冲突不自动覆盖 | `stage_record` 校验 `expected_revision`，不一致抛 `ValueError` | `infrastructure/file_store/unit_of_work.py` |
| 业务记录、对象、Spool、检查点只追加 | 各存储组件均只追加，不提供删除/覆盖 | `infrastructure/file_store/` |
| `Clock.now()` | `Clock` 端口已定义 `now()` + `monotonic()` | `application/ports.py` |
| 内容寻址对象存储 + 摘要校验 | `EvidenceObjectStore.publish_bytes` / `read_bytes`，sha256 校验 | `infrastructure/file_store/objects.py` |
| 意图幂等 | `append_intent` 按 `intent_id` 去重，同摘要返回已存修订 | `infrastructure/file_store/records.py` |
| 通用记录读写（不依赖 B 领域类型） | `append(kind, record_id, expected_revision, payload)` 通用签名 | `infrastructure/file_store/records.py` |

---

## 2 需要 A 包确认项

以下事项由 A 包评估确认，不涉及组长裁定：

### 2.1 `commit_sequence` 类型

| 项 | 内容 |
| --- | --- |
| B 草案 | `commit_seq: str` |
| A 现状 | `commit_sequence: int` |
| **A 侧意见** | 提交序号本质为整数计数器，建议维持 `int`。B 包若需字符串形式，在应用层 `str(commit_sequence)` 转换即可，无需端口层变更。 |

### 2.2 `expected_revision` 冲突返回差异提示

| 项 | 内容 |
| --- | --- |
| B 诉求 | 冲突时返回"当前修订与差异提示" |
| A 现状 | 抛 `ValueError("revision conflict: expected X, current Y")`，`ErrorDTO` 无差异字段 |
| **A 侧意见** | 当前 message 已携带当前修订号，可满足基本冲突提示。若 B 包需要结构化差异字段，需扩展 `ErrorDTO` 增加 `details` 或复用 `next_step`。建议 B 包确认是否必须结构化，若必须则列入后续迭代。 |

### 2.3 `stage_preparation` 专用方法

| 项 | 内容 |
| --- | --- |
| B 诉求 | `register_preparation` / `find_preparation` / `find_preparation_by_intent`，同键异摘要返回冲突不覆盖 |
| A 现状 | 通用 `append_intent(intent_id, kind, record_id, expected_revision, payload)` 支持意图幂等 |
| **A 侧意见** | preparation 是 intent 的业务形态。建议在后续迭代中于 `WorkspaceUnitOfWork` 内新增 `stage_preparation` 方法，与 `stage_record` 同事务提交，复用 `append_intent` 的幂等机制。一期不实现。 |

### 2.4 各类 Port 签名写入 `ports.py`

| 端口 | A 现状 | B 草案 | **A 侧意见** |
| --- | --- | --- | --- |
| `SourceSnapshotPort` | 空 Protocol stub | `pin` / `materialize` / `read_pinned` / `detect_changes` | 采用 B 草案方法签名，由 A 写入 `ports.py`（A 独占端口文件） |
| `SourceControlPort` | 空 Protocol stub | 未提供完整签名 | A 补充定义，覆盖本地 Git 元信息、工作区变更；远端变更/CI 状态待组长裁定是否一期 |
| `ModelProvider` | 空 Protocol stub | 未提供完整签名 | A 补充定义 |
| `ProjectionPort` | 空 Protocol stub | 未提供 | 建议明确为 A 定义（与 `SecretPort` 同属安全投影域），A 补充方法签名 |

> 以上端口方法签名均在一期之后迭代实现，不阻塞一期交付。`ports.py` 保持单文件，由 A 统一写入。

---

## 3 需要组长裁定事项

以下事项超出 A 包单方决策范围，需组长裁定：

### 3.1 `SourceSnapshot` 归属（B 第 5.1 节）

- **冲突**：领域对象物理位置在 C 的 `domain/execution/sources.py`，但建立时机、`purpose`、排除规则、内容身份计算属 B 的 FR01 职责。
- **B 主张**：定义层（规则）归 B，位置层（类文件）留 C，C 按差集补字段。
- **A 侧建议**：支持 B 主张的分层方案，避免两套实现。需组长确认并同步修正架构文档歧义表述。

### 3.2 端口定义归属与提交方式（B 第 5.2 节）

- **冲突**：`SourceSnapshotPort` / `SourceControlPort` / `ModelProvider` 按原文属 B 的端口，但 `ports.py` 所有者为 A，形成定义权与提交权分离。
- **B 主张**：由 B 按"只追加本包段落"约定写入 `ports.py`，A 事后复核。
- **A 侧建议**：维持 A 独占 `ports.py` 的现状，端口方法签名由 A 写入。若组长裁定允许 B 追加，需约定签名确认流程且不得重排 A 已有段落。
- **附带**：`Clock` 在两份分册重复列出，无实际冲突，仅文档冗余；`ProjectionPort` 两册均未列，建议明确归 A。

### 3.3 GitHub 只读一期范围（B 第 5.3 节）

- **B 需求**：4 类信息（本地 Git 元信息、工作区变更、远端变更、远端 CI 状态）。
- **A 侧建议**：第 1、2 类（本地 Git 元信息、工作区变更）纳入一期；第 3、4 类（远端变更、CI 状态）列为可选能力，是否一期实现需组长裁定。
- **硬性约束确认**：plain 形态不调用 Git、绿色状态不替代业务通过、凭据经 `SecretPort` 不进配置/日志/面板/导出 — A 侧无异议。

---

## 4 结论

**A 包一期底座可按期交付。**

B 包提出的核心保存与端口需求中，事务原子性、单写锁、按修订读取、索引结构化状态、追加只读、内容寻址对象存储、意图幂等、通用记录读写等**底座能力均已满足**，B 包可直接使用。

**B-A 新增端口与方法签名（`stage_preparation`、`SourceSnapshotPort` / `SourceControlPort` / `ModelProvider` / `ProjectionPort` 方法签名、`RevisionRef.digest` 增强、`expected_revision` 结构化差异提示）属于一期之后迭代内容，不在本次一期范围。**

待组长裁定 SourceSnapshot 归属、端口提交方式、GitHub 远端信息一期范围三项后，A 包在后续迭代中按裁定结果落地端口签名。
