# B-A 端口与保存需求评审意见
 
> **评审历史说明（2026-09-30）**：本文件中"相关端口推迟到一期之后"的建议已被项目负责人裁定覆盖。现行范围、维护方式和下一动作以同目录 `contract.md` 第 10 节及总体架构为准；本文件仅保留当时评审过程。本次评审基于 `contract.md` v0.5（含项目负责人裁定）重新核对 A 包现行实现。


> **评审历史说明（2026-09-30）**：本文件中"相关端口推迟到一期之后"的建议已被项目负责人裁定覆盖。现行范围、维护方式和下一动作以同目录 `contract.md` 第 10 节及总体架构为准；本文件仅保留当时评审过程。本次评审基于 `contract.md` v0.5（含项目负责人裁定）重新核对 A 包现行实现。

版本：1.1  
日期：2026-09-30  
评审方：LU（A 包负责人）  
评审对象：`docs/接口对接/进行中/AB-001-端口与保存/contract.md`（v0.5）  
依据：`src/aitest/application/ports.py`、`src/aitest/infrastructure/file_store/`、`docs/文档-LU/A包对外接口与装配约定.md`

---

## 1 已满足项（A 包现有能力）

以下 B 包提出的需求，A 包一期底座已实现，B 包可直接使用：

| B 的诉求 | A 包现有能力 | 代码位置 |
| --- | --- | --- |
| 短事务边界、同一事务提交记录/引用/索引与幂等结果 | `FileUnitOfWork.commit()` → `commit_transaction()` 原子发布记录、索引、清单 | `infrastructure/file_store/unit_of_work.py`、`records.py` |
| 单写锁，外部调用不持长锁 | `portalocker.LOCK_EX \| LOCK_NB`，事务外不持有 | `infrastructure/file_store/locking.py` |
| 按准确修订读取，不默默回退最新 | `RecordRepository.read(aggregate_kind, record_id, revision)` 接受显式修订 | `application/ports.py`、`infrastructure/file_store/records.py` |
| 列表读摘要、详情按引用读取 | `RecordRepository.query()` 走索引返回摘要，`read()` 按修订读详情 | `infrastructure/file_store/records.py`、`index.py` |
| 索引缺失返回 `maintenance_required`，不隐式全表扫描 | `IndexQueryResult(status="maintenance_required")`，查询只读 `indexes.json` | `infrastructure/file_store/index.py` |
| `expected_revision` 冲突不自动覆盖用户编辑 | `stage_record` 校验 `expected_revision`，不一致抛 `RECORD_REVISION_CONFLICT` | `infrastructure/file_store/unit_of_work.py` |
| 业务记录、对象、Spool、检查点只追加 | 各存储组件均只追加，不提供删除/覆盖 | `infrastructure/file_store/` |
| `Clock.now()` / `monotonic()` | `Clock` 端口已定义，业务顺序以提交序号判断 | `application/ports.py` |
| 内容寻址对象存储 + 摘要校验 | `EvidenceObjectStore.publish_bytes` / `read_bytes`，sha256 校验 | `application/ports.py`、`infrastructure/file_store/objects.py` |
| 意图幂等（同 `intent_id` 同摘要不重复） | `append_intent` 按 `intent_id` 去重，同摘要返回已存修订 | `infrastructure/file_store/records.py` |
| 提交序号（业务顺序依据） | `commit()` 返回 `commit_sequence: int` | `infrastructure/file_store/unit_of_work.py` |
| `SourceSnapshotPort`（`pin` / `materialize` / `read_pinned` / `detect_changes`） | `ports.py` 已定义 Protocol，签名与 B 草案一致 | `application/ports.py` |
| `SourceControlPort`（`is_available` / `is_repository` / `describe` / `changes` / `upstream_counts`） | `ports.py` 已定义 Protocol，覆盖本地 Git 元信息、工作区变更、远端计数 | `application/ports.py` |
| `ModelProvider.call()` | `ports.py` 已定义 | `application/ports.py` |
| `ProjectionPort.project()` | `ports.py` 已定义 | `application/ports.py` |
| `SecretPort.resolve()` / `has_secret()` | `ports.py` 已定义，按用途解析引用 | `application/ports.py` |
| `SpoolStore.persist_redaction_summary()` | 已实现，返回 `summary_id` | `application/ports.py`、`infrastructure/file_store/spool.py` |

---

## 2 需要 A 确认项

以下为 B 包提出的新增需求，A 包逐条评估现状与处理意见。每条评估维度：一期能否实现、建议方案、是否延后迭代。

### 2.1 `RecordRepository` 按记录类型拆分 `save_*` / `read_*` 方法

| 项 | 内容 |
| --- | --- |
| B 诉求 | 为每个记录类型提供独立方法（`save_project` / `read_project` / `save_binding` / `read_binding` / `save_module` … 共约 18 组） |
| A 现状 | `RecordRepository` 提供通用 `read(aggregate_kind, record_id, revision)` 与 `query(spec)`；写入走 `WorkspaceUnitOfWork.stage_record(aggregate_kind, record_id, expected_revision, payload)`，不依赖 B 领域类型 |
| 一期能否实现 | 否 |
| 建议方案 | B 使用通用 `stage_record` / `read` + `aggregate_kind` 区分记录类型。端口层不引入 per-type 方法，避免端口依赖 B 领域具体类。B 在自身 `application/serialization.py` 中完成 payload 与领域对象的往返转换 |
| 是否延后迭代 | 是。一期不实现 per-type 方法；若后续确有强需求，在一期之后统一评估 |

### 2.2 `stage_preparation` / `register_preparation` 专用方法

| 项 | 内容 |
| --- | --- |
| B 诉求 | `stage_preparation` 在事务内按 `(project_id, client_id, prepare_request_id)` 登记 `PreparationRecord` 及 `intent_id`；同键异摘要返回冲突不覆盖；与准备记录同一次提交 |
| A 现状 | 通用 `stage_record(aggregate_kind="preparation_record", ...)` + `intent_id` 幂等机制（`append_intent` 按 `intent_id` 去重）可覆盖同键幂等语义 |
| 一期能否实现 | 语义属一期（裁定 §10.2：`stage_preparation` 所表达的同键幂等/冲突和同次提交语义属于一期），但允许复用通用工作单元原语，不强制保留同名专用方法 |
| 建议方案 | 一期用 `stage_record(aggregate_kind="preparation_record")` + `intent_id` 实现 preparation 幂等与冲突语义：同 `(project_id, client_id, prepare_request_id)` 摘要相同返回原记录修订，摘要不同抛 `INTENT_CONFLICT`。B 的 `decide_preparation()` 负责单线程判定与提示内容。`stage_preparation` 同名专用方法不新增 |
| 是否延后迭代 | 专用方法延后；通用语义一期覆盖 |

### 2.3 `commit_seq` 类型（B 草案 `str` vs A 现状 `int`）

| 项 | 内容 |
| --- | --- |
| B 诉求 | `CommitResult.commit_seq: str` |
| A 现状 | `commit()` 返回 `commit_sequence: int` |
| 一期能否实现 | 是（维持 `int`） |
| 建议方案 | 提交序号本质为单调递增整数计数器，维持 `int`。B 包若需字符串形式（如拼接标识），在应用层 `str(commit_sequence)` 转换即可，无需端口层变更 |
| 是否延后迭代 | 否，一期按 `int` 交付 |

### 2.4 `expected_revision` 冲突结构化差异提示

| 项 | 内容 |
| --- | --- |
| B 诉求 | 冲突时返回"当前修订与差异提示"（结构化字段） |
| A 现状 | 抛 `RECORD_REVISION_CONFLICT`，`ErrorDTO.message` 携带当前修订号，无结构化差异字段 |
| 一期能否实现 | 否 |
| 建议方案 | 当前 `message` 已携带当前修订号，可满足基本冲突提示。若 B 包需要结构化差异（如字段级 diff），需扩展 `ErrorDTO` 增加 `details` 字段或复用 `next_step`。建议 B 包确认是否必须结构化，若必须则列入一期之后迭代 |
| 是否延后迭代 | 是 |

### 2.5 `WorkspaceUnitOfWork` 实例获取方式

| 项 | 内容 |
| --- | --- |
| B 诉求 | 需要一个入口拿到 `WorkspaceUnitOfWork` 实例 |
| A 现状 | `bootstrap.py` 注入实现；B 通过 `create_api(handlers=...)` 注册用例，应用用例在执行时接收已注入的 UoW |
| 一期能否实现 | 是 |
| 建议方案 | B 通过 A 的 `create_api` 注册自身用例处理器，在应用用例函数签名中接收 `WorkspaceUnitOfWork`（由 A 在调用时注入）。B 不自行实例化 UoW |
| 是否延后迭代 | 否 |

### 2.6 提交后可见性读取入口

| 项 | 内容 |
| --- | --- |
| B 诉求 | `commit()` 成功后如何读取刚提交的修订 |
| A 现状 | `commit()` 返回本次提交创建的修订引用（含 `aggregate_kind` / `record_id` / `revision`）；`RecordRepository.read(aggregate_kind, record_id, revision)` 按修订读取 |
| 一期能否实现 | 是 |
| 建议方案 | B 从 `commit()` 返回值中提取修订引用，再调用 `RecordRepository.read()` 按显式修订读取。读写分离，便于测试与回放 |
| 是否延后迭代 | 否 |

### 2.7 `RevisionRef` / `CommitResult` / `Page[T]` 公共类型

| 项 | 内容 |
| --- | --- |
| B 诉求 | 在 `ports.py` 中定义 `RevisionRef`、`CommitResult`、`Page[T]` 公共类型 |
| A 现状 | `ports.py` 未定义这些类型；`commit()` 返回 `object`，`query()` 返回 `object` |
| 一期能否实现 | 部分（`RevisionRef` 可一期，`CommitResult` / `Page[T]` 延后） |
| 建议方案 | 一期 `commit()` 返回 `dict` 含 `commit_sequence: int` 与 `created: Mapping[tuple[aggregate_kind, record_id], RevisionRef]`；`RevisionRef` 可用 `dict` 或简单 dataclass 承载 `aggregate_kind` / `record_id` / `revision` / `digest`。`CommitResult` 正式类型化与 `Page[T]` 泛型延后一期之后迭代 |
| 是否延后迭代 | `CommitResult` / `Page[T]` 延后；`RevisionRef` 一期以 `dict` 形式提供 |

### 2.8 `SourceSnapshotPort` 返回类型与字段边界

| 项 | 内容 |
| --- | --- |
| B 诉求 | 返回 `Mapping[str, object]` 回避 `SourceSnapshot` 类型归属问题（裁定 §10.1：领域对象在 C，建立规则归 B） |
| A 现状 | `ports.py` 已定义 `SourceSnapshotPort` 四个方法均返回 `Mapping[str, object]` |
| 一期能否实现 | 是 |
| 建议方案 | 维持 `Mapping[str, object]` 返回，字段名以《一期架构 01：项目与计划》冻结语义和唯一 `SourceSnapshot` 模型为准。A 实现物化与持久化适配，B 提供字段与判据 |
| 是否延后迭代 | 否 |

---

## 3 冲突项

> 预留章节。当前 `contract.md` v0.5 第 10 节项目负责人裁定已覆盖此前全部待裁定事项（`SourceSnapshot` 归属、端口定义与提交方式、`Clock`/`ProjectionPort` 归属、Git/GitHub 一期边界），暂无新增冲突。
>
> 后续若出现分歧，按以下模板记录并提交组长裁定：
>
> | 项 | 内容 |
> | --- | --- |
> | 冲突描述 | … |
> | B 主张 | … |
> | A 侧建议 | … |

---

## 4 结论

### 4.1 一期交付与 B 核心需求满足情况

**A 包一期底座可按期交付。**

B 包提出的核心保存与端口需求中，事务原子性、单写锁、按修订读取、索引结构化状态、追加只读、内容寻址对象存储、意图幂等、通用记录读写、`Clock`、`SourceSnapshotPort` / `SourceControlPort` / `ModelProvider` / `ProjectionPort` / `SecretPort` 端口定义、`SpoolStore.persist_redaction_summary` 等**底座能力均已满足**，B 包可直接使用。

B 包可基于 A 包通用 `WorkspaceUnitOfWork.stage_record` + `RecordRepository.read/query` + `aggregate_kind` 区分记录类型，完成项目、绑定、模块、任务、计划、用例、规则等全部记录的保存与读取；preparation 幂等语义通过 `intent_id` + 通用 `stage_record` 一期覆盖。

**B 核心需求逐项满足情况：**

| B 核心需求（contract.md 对应章节） | A 包满足方式 | 满足状态 |
| --- | --- | --- |
| §2 记录清单（12 种记录类型保存） | 通用 `stage_record(aggregate_kind, record_id, expected_revision, payload)` + `aggregate_kind` 区分类型 | ✅ 一期满足 |
| §3.1 `RecordRepository` 按准确修订读取、列表读摘要 | `read(aggregate_kind, record_id, revision)` + `query(spec)` 走索引 | ✅ 一期满足 |
| §3.2 准备意图登记（FR07：同键幂等、异摘要冲突、同次提交） | `intent_id` 幂等 + `stage_record(aggregate_kind="preparation_record")` 事务内提交 | ✅ 一期满足（语义覆盖，专用方法延后） |
| §3.3 `WorkspaceUnitOfWork` 短事务边界、同事务原子提交 | `FileUnitOfWork.commit()` 原子发布记录/索引/清单 | ✅ 一期满足 |
| §3.4 `Clock.now()`，业务顺序按提交序号 | `Clock.now()` + `commit()` 返回 `commit_sequence: int` | ✅ 一期满足 |
| §3.5 `SourceSnapshotPort` | `ports.py` 已定义 `pin` / `materialize` / `read_pinned` / `detect_changes` | ✅ 一期满足 |
| §3.5 `SourceControlPort`（本地 Git 元信息、工作区变更） | `ports.py` 已定义 `is_available` / `is_repository` / `describe` / `changes` / `upstream_counts` | ✅ 一期满足 |
| §3.5 `ModelProvider` | `ports.py` 已定义 `call()` | ✅ 一期满足 |
| §3.5 `SecretPort`（按用途解析引用） | `ports.py` 已定义 `resolve()` / `has_secret()` | ✅ 一期满足 |
| §3.5 `ProjectionPort`（安全投影） | `ports.py` 已定义 `project()` | ✅ 一期满足 |
| §5.3 GitHub 只读（远端变更、CI 状态） | `SourceControlPort.upstream_counts()` 已定义；远端 HTTPS 获取待适配器实现 | ⚠️ 一期可选（裁定 §10.3：远端为可选能力，不阻塞本地闭环） |

### 4.2 一期范围与延后迭代区分

**纳入一期（本次实现）：**

- 通用 `WorkspaceUnitOfWork`（`open` / `stage_record` / `commit` / `rollback`）与原子提交
- 通用 `RecordRepository.read` / `query`（按 `aggregate_kind` + 显式修订读取）
- `Clock`、`EvidenceObjectStore`、`SourceSnapshotPort`、`SourceControlPort`、`ModelProvider`、`ProjectionPort`、`SecretPort` 端口 Protocol 定义与适配器实现
- `SpoolStore.persist_redaction_summary`
- preparation 幂等/冲突语义（复用通用 `stage_record` + `intent_id`，不新增同名专用方法）
- `commit()` 返回 `commit_sequence: int` 与修订引用（`dict` 形式）

**延后至一期之后迭代（本次不实现）：**

- `RecordRepository` 按记录类型拆分的 `save_*` / `read_*` 方法（共约 18 组）
- `stage_preparation` / `register_preparation` 同名专用方法
- `expected_revision` 冲突的结构化差异提示（`ErrorDTO.details` 字段扩展）
- `CommitResult` 正式类型化与 `Page[T]` 泛型公共类型
- `RevisionRef` 正式 dataclass 化（一期以 `dict` 承载）

以上延后项不阻塞 B 包一期核心功能，B 包可通过现有通用接口完成等价语义；待一期交付后由 A 包按迭代节奏评估落地。

**第 2 节确认项与一期范围对照：**

| 第 2 节确认项 | 一期范围归类 | 说明 |
| --- | --- | --- |
| 2.1 `RecordRepository` per-type `save_*` / `read_*` | 延后 | 一期用通用 `stage_record` / `read` + `aggregate_kind` 替代 |
| 2.2 `stage_preparation` 专用方法 | 延后（专用方法）；语义一期覆盖 | 一期复用 `stage_record` + `intent_id` 实现幂等/冲突语义 |
| 2.3 `commit_seq` 类型 | 纳入一期 | 维持 `int`，B 应用层 `str()` 转换 |
| 2.4 `expected_revision` 结构化差异提示 | 延后 | 一期 `ErrorDTO.message` 已携带当前修订号 |
| 2.5 `WorkspaceUnitOfWork` 实例获取 | 纳入一期 | `bootstrap.py` 注入，B 通过 `create_api` 注册用例接收 |
| 2.6 提交后可见性读取入口 | 纳入一期 | `commit()` 返回修订引用 + `RecordRepository.read()` 按修订读取 |
| 2.7 `RevisionRef` / `CommitResult` / `Page[T]` | 部分一期 | `RevisionRef` 一期以 `dict` 承载；`CommitResult` / `Page[T]` 延后 |
| 2.8 `SourceSnapshotPort` 返回类型 | 纳入一期 | 维持 `Mapping[str, object]`，字段以架构冻结语义为准 |
