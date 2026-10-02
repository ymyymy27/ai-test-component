# A包事务函数设计

## 背景/目的

为 A 包文件存储提供基于 `request_id` 的非嵌套短事务边界，保证快照、提交、回滚和事务日志的一致性。对外语义以 `docs/接口对接/进行中/CORE-001-A包公共接口/contract.md` v1.1（以下简称 CORE-001）为事务、存储与查询的唯一对接合同；方法签名与提交语义与 `docs/接口对接/进行中/AC-001-存储与恢复/contract.md`（以下简称 AC-001）第 2、3 节保持一致。

## 变更清单

- 在 `src/aitest/infrastructure/file_store/unit_of_work.py` 提供 `begin`、`stage_record`、`commit`、`rollback`、`recover`；`open` 为 `begin` 内部的事务打开步骤，同时满足 AC-001 §3.1 的 `WorkspaceUnitOfWork` 端口形状。
- `begin` 校验 `workspace_id`、获取 OS 单写锁，从当前已提交边界生成数据快照；快照修改在 `commit` 成功前对其他读取者不可见。
- 业务修改不经 `commit` 载荷传入，而由上层在活动事务内调用 `stage_record(*, aggregate_kind, record_id, expected_revision, payload)` 逐记录暂存；`expected_revision` 与当前修订不一致立即拒绝，不自动覆盖。
- `commit` 在同一原子发布边界内依次发布活动标记、事件、提交清单、索引和业务记录（业务记录最后发布，为事实来源），五者要么全部成功要么保持上一提交边界，并返回提交序号 `commit_sequence`。
- `rollback` 丢弃全部暂存修改并释放单写锁，原始数据和已提交索引保持不变，回滚事件日志保留。
- 返回值携带 `request_id` 和事务状态；错误按 CORE-001 §7 归一为结构化错误码（数值码 `0/1001–1004` 与现有符号码的映射见下）。
- 事务日志为追加写入的 JSONL，至少记录 `request_id`、事务阶段、工作空间身份、提交边界或快照引用、结果码和错误分类，不写入业务凭据正文。

## 拟定接口

最终签名与 AC-001 §3.2 一致，已在 `FileUnitOfWork` 落地：

```python
begin(request_id: str, project_id: str,
      workspace_id: str | None = None,
      intent_id: str | None = None) -> dict
open(project_id: str) -> None
stage_record(*, aggregate_kind: str, record_id: str,
             expected_revision: int | None,
             payload: Mapping[str, object]) -> int
commit(request_id: str | None = None,
       workspace_id: str | None = None) -> dict
rollback(request_id: str | None = None,
         workspace_id: str | None = None) -> dict
recover(workspace_id: str) -> dict
```

返回结构：

| 方法 | 返回 |
| --- | --- |
| `begin` | `{"request_id", "state": "active", "intent_id"?}` |
| `stage_record` | 新修订号（`int`） |
| `commit` | `{"request_id", "state": "committed", "created", "commit_sequence"}` |
| `rollback` | `{"request_id", "state": "rolled_back", "intent_id"?}` |
| `recover` | `{"workspace_id", "state": "not_started" \| "active"}` |

`commit` 使用 `try/finally` 兜底释放单写锁：无论提交正常返回还是抛出异常，OS 单写锁都会释放；提交失败后旧提交边界保持有效，调用方可凭原 `request_id` 调用 `rollback` 清理。`recover` 只报告事务状态，不重放未知副作用，也不盲目重新提交。

## 身份和状态守卫

- `request_id` 是传输事务身份；同一 request_id 重传必须返回同一事务结果，不得创建第二个事务；同请求异输入返回冲突错误（`REQUEST_CONFLICT`）。
- `intent_id` 由应用用例创建，在 `begin` 时绑定，A 包在同一提交边界持久化 intent 记录；不由本地存储事务函数生成，也不能用新 `request_id` 替代。同一 intent_id 同输入摘要重提不重复执行，返回已有的 `commit_sequence`；明确重跑必须创建新意图。
- 事务状态为 `not_started / active / committed / rolled_back / failed`；活动事务只能由持有它的 request_id 提交或回滚，终态不能再次读写、提交或回滚。
- 一个工作空间最多存在一个活动事务；第二次 begin 返回冲突错误，不覆盖已有快照；未结束事务不得通过新 request_id 绕过单写边界。
- 修订守卫下沉到每条 `stage_record`：`expected_revision` 与当前修订不一致返回 `RECORD_REVISION_CONFLICT`；`commit` 不再接收修订参数。
- 网络、模型、编辑器和被测业务调用必须在事务外完成，不得持有 A 包写事务；事务只在本机工作空间内生效，不引入独立事务服务、消息队列或分布式提交协议。

## 对外合同确认结果

原三项待确认内容已由 CORE-001 v1.1（§4、§5、§7、§8）与 AC-001（§2.3、§3、§4.4）明确，结论如下。

### 1. 错误码 1001–1004 的含义（CORE-001 §7）

| 数值码 | 语义 | 现有符号码（`contracts/errors.py`） |
| --- | --- | --- |
| `0` | 成功，事务结果已达到声明的持久化边界 | `OK` |
| `1001` | `request_id` 不存在或不属于当前活动事务；调用方不得继续提交或回滚该请求 | `TRANSACTION_NOT_FOUND` |
| `1002` | 事务状态非法；已提交或已回滚事务不得重复操作 | `INVALID_TRANSACTION_STATE` |
| `1003` | 事务重复开启或检测到活动事务冲突；应先结束已有事务再开启新事务 | `TRANSACTION_CONFLICT` / `WORKSPACE_IN_USE` |
| `1004` | 文件系统读写、刷新、替换或持久化校验失败；必须保留原始数据，不能返回已提交，也不能产生脏数据 | `STORAGE_NOT_WRITABLE` / `ATOMIC_PUBLISH_FAILED` / `INTEGRITY_CHECK_FAILED` |

另有记录级修订冲突 `RECORD_REVISION_CONFLICT`，在 `stage_record` 阶段返回，不归并到事务码。错误响应必须保留原始 `request_id`，并包含结构化错误码、脱敏消息和下一步建议；异常堆栈、凭据正文、原始敏感输出和未过滤日志不得写入响应、事务日志或永久业务材料。

### 2. begin 快照来源与 commit 修改载荷结构

- 快照由 A 包在 `begin` 时按当前已提交边界生成，调用方不传快照来源；提交前快照修改对其他读取者不可见，提交失败时不能把部分修改标记为已提交。
- 既不采用“完整 JSON 状态”载荷，也不采用“记录集合”入参：修改在活动事务内经 `stage_record` 逐条暂存，即原待确认项的第三种情形——由 `WorkspaceUnitOfWork` 暂存的记录/索引变更；`commit(request_id)` 不接收业务载荷。
- 提交在同一原子边界发布 intent 记录、业务记录、提交清单、查询索引和事件；任一材料无法可靠发布时保留上一有效提交边界并返回 `1004`，索引与日志不分阶段对外可见。

### 3. commit 的身份参数与返回

- `commit` 不接收 `intent_id`：业务意图在 `begin(intent_id=...)` 时绑定并随提交持久化。`intent_id`、`project_id`、`expected_revision` 分别属于业务写命令与逐记录暂存的职责，不堆叠到 commit 签名上。
- `workspace_id` 在 `begin/commit/rollback` 均为可选校验参数，与工作空间身份不匹配时拒绝写入。
- 成功返回 `state="committed"`、本轮创建记录数 `created` 和提交序号 `commit_sequence`；`commit_sequence` 单调递增，是恢复和执行事实快照游标（AC-001 §4.3 `snapshot_commit_id`）引用的提交身份，替代另行返回事务日志编号的需要。

## 遗留问题/下一步

- CORE-001 当前状态仍为 `reviewing`，其 §12 的 B/C/D 确认栏尚未签字；在各方对字段、错误码和日志的确认回写前，数值码与字段不视为最终冻结。
- 数值码 `1001–1004` 尚未在代码中落地：当前响应层使用符号 `ErrorCode` 归一，`FileUnitOfWork` 内部仍抛 `RuntimeError`/`ValueError`；下一步在适配层补充数值码映射，不改变上述方法签名。
- Windows 目标文件系统上的重启、掉电、权限和空间故障证据仍需按 CORE-001 §9 真实执行并记录；文档一致或模拟器、单元测试通过不替代该证据。

## 验证计划

- 正常 begin → stage_record → commit 后重启读取已提交数据，`commit_sequence` 单调递增且与提交清单、快照游标互相吻合。
- begin 后 rollback，数据恢复原快照、单写锁已释放，且回滚事件日志仍可读取。
- 在暂存写入、清单发布、当前指针替换前后注入中断或 IO 故障，验证旧提交边界有效、锁释放且无部分可见提交。
- 未知或不持有活动事务的 request_id 提交/回滚返回 `1001`；终态重复操作返回 `1002`；活动事务冲突或嵌套 begin 返回 `1003`；持久化失败返回 `1004` 且不伪造成功。
- `expected_revision` 冲突在 `stage_record` 阶段即被拒绝（`RECORD_REVISION_CONFLICT`）；同 intent_id 同输入摘要重提不产生第二条业务记录，返回原 `commit_sequence`。
- 既有自动化覆盖：`tests/acceptance/p1/test_a_package_independent_acceptance.py`（真实文件单写锁、原子发布故障注入、对象不可变性/摘要校验与分页协议）和 `tests/recovery/test_atomic_and_lock.py`（原子提交与锁的故障注入）。
