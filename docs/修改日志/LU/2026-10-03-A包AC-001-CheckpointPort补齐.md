# A包AC-001 CheckpointPort补齐与对齐核对（对应 review-A.md 评审意见）

修改日期：2026-10-03。分支：`A包-LU-defect-fix-r2`（未 commit、未推送，等待人工审核后新建独立契约分支提交 PR）。
评审依据：[review-A.md](file:///c:/Users/Lenovo/Desktop/zhiyin/ai-test-component/docs/接口对接/进行中/AC-001-存储与恢复/review-A.md) §3 风险项第 1 条与 §4 待确认事项第 4 条（CheckpointPort Protocol 写入 ports.py 属一期交付范围）；评审文档仅作需求依据，未在其中写代码。

## 1. 评审意见逐项响应

| review-A.md 条目 | A 包响应 | 状态 |
| --- | --- | --- |
| 风险项1：CheckpointPort 未写入 ports.py | 已在 [ports.py](file:///c:/Users/Lenovo/Desktop/zhiyin/ai-test-component/src/aitest/application/ports.py#L337-L362) 补齐 Protocol（persist/load/scan），签名严格对齐 [FileCheckpointStore](file:///c:/Users/Lenovo/Desktop/zhiyin/ai-test-component/src/aitest/infrastructure/file_store/checkpoints.py#L33-L68) | 已补齐 |
| 待确认2：persist_redaction_summary 是否存在 | 已存在：[SpoolStore.persist_redaction_summary](file:///c:/Users/Lenovo/Desktop/zhiyin/ai-test-component/src/aitest/application/ports.py#L328-L333)（Protocol）+ spool.py 实现 + command.py 实际调用。**不新增任何专用端口**，C 直接复用 SpoolStore | 已确认，零改动 |
| 待确认3：recover_workspace 调用方 | A 核心启动时调用：[bootstrap.py](file:///c:/Users/Lenovo/Desktop/zhiyin/ai-test-component/src/aitest/bootstrap.py#L261-L267) `RecoveryOrchestrator(root).run()` 在取排他写锁后、服务装配前执行，处理活动事务；recovery blocked 时核心拒绝启动。检查点只读扫描经 CheckpointPort.scan() 提供，C 消费 RecoveryRecord 做 Attempt 恢复 | 已确认边界 |
| 待确认4：CheckpointPort 定义时机 | 本轮已落地（一期范围内），C 侧签名确认即生效 | 已完成 |
| 核心约束：C 禁止直接读写底层文件 | A 侧 grep 全量确认：`FileCheckpointStore` 在 src/aitest 下**零实例化**（仅定义文件与端口 docstring 引用）；records.json/indexes/commit/events/checkpoints/spool/objects 均不暴露给 C | 已满足 |

## 2. 对齐规则核对报告

依据纪要：ExecutionFacts `aggregate_kind=execution_facts`；`record_id=run_id`；每次快照发布递增 `snapshot_revision`；`snapshot_commit_id` 取 A 返回 `commit_sequence`。

| 规则项 | 纪要约定 | A 侧现状 | 结论 |
| --- | --- | --- | --- |
| aggregate_kind | `execution_facts` | `stage_record(aggregate_kind=...)` 由调用方传入，A 端口不约束取值 | 同构兼容，C 按纪要传值即可，无需适配 |
| record_id | `run_id` | 同上，`record_id` 由调用方提供 | 同构兼容 |
| snapshot_revision | 每次发布递增 | `expected_revision`（新建 None / 否则当前修订）+ 冲突报 `RECORD_REVISION_CONFLICT` + 提交后单调 +1 | 语义一致（纪要注明"与 record_revision 对齐"） |
| snapshot_commit_id | 取 A 返回的 `commit_sequence` | `commit()` 返回 `commit_sequence`；`commit_seq()/next_commit_seq()` 可读 | 完全一致 |
| 记录版本 | `aitest.recovery-checkpoint/1.0` | checkpoints.py `_SCHEMA_VERSION` 固定，不符拒绝读取 | 一致 |

**结论：纪要 ID/修订规则与 A 现有 stage_record 完全同构，无冲突、无需适配。**

## 3. 代码改动

### ① ports.py 新增端口代码

```python
class CheckpointPort(Protocol):
    """Persist, load and scan recovery checkpoints for Attempt resumption.

    对齐 AC-001 §7.3：C 包通过本端口调用，不得直接实例化
    :class:`FileCheckpointStore`。记录版本固定 ``aitest.recovery-checkpoint/1.0``。

    ID/修订规则与 :meth:`WorkspaceUnitOfWork.stage_record` 同构：
    ``aggregate_kind`` 固定 ``execution_facts``，``record_id`` 取 ``run_id``，
    每次快照发布递增 ``snapshot_revision``（对应 ``expected_revision``），
    ``snapshot_commit_id`` 取 A 返回的 ``commit_sequence``。
    """

    def persist(self, record: RecoveryRecord) -> Path: ...
    def load(self, attempt_id: str) -> RecoveryRecord: ...
    def scan(self) -> tuple[RecoveryRecord, ...]: ...
```

### ② 单元测试源码

新增 [tests/unit/test_a_checkpoint_port.py](file:///c:/Users/Lenovo/Desktop/zhiyin/ai-test-component/tests/unit/test_a_checkpoint_port.py)，4 用例覆盖三个接口：

1. `test_port_signatures_match_file_checkpoint_store`：persist 返回 Path 且落盘、load 按 attempt_id 取回同身份记录、scan 返回 tuple——签名与 FileCheckpointStore 严格对齐。
2. `test_checkpoint_record_version_is_pinned`：落盘 payload 固定 `aitest.recovery-checkpoint/1.0`。
3. `test_scan_is_read_only`：scan() 只读——空目录不创建、已有文件不被修改（对应 §7.4 启动扫描只读约束）。
4. `test_caller_cannot_instantiate_store_directly_via_port`：CheckpointPort 为 Protocol 且直接实例化抛 TypeError（落实 §7.3 "C 不得直接实例化 FileCheckpointStore"）。

### ③ 修改日志（统一格式：缺陷编号｜修改文件｜改动简述）

| 缺陷编号 | 修改文件 | 改动简述 |
| --- | --- | --- |
| AC-001-P1 | src/aitest/application/ports.py | 新增 CheckpointPort Protocol（persist/load/scan），导入 RecoveryRecord；docstring 固化 ID/修订对齐规则与版本钉住 |
| AC-001-P1 | tests/unit/test_a_checkpoint_port.py | 新增 4 个端口合同测试：签名对齐、版本钉住、scan 只读、Protocol 不可实例化 |

## 4. 范围边界

- 仅 A 包改动：ports.py（application 层端口）+ 新增测试；B/C 包零改动；application 层无 infrastructure 导入，架构边界测试通过。
- 未冻结端口签名（SourceSnapshotPort 之外的迭代项）不在本轮范围，仅处理已稳定的 CheckpointPort。
- 装配接线（bootstrap 将 FileCheckpointStore 注入 CheckpointPort 消费方）与 C 侧移除本地 FileSpoolStore/FileObjectStore/FileCheckpointStore 的迁移，均走后续单独 PR。

## 5. 校验结果

| 校验 | 结果 |
| --- | --- |
| ruff check src tests | All checks passed!（exit 0） |
| mypy src | Success: no issues found in 127 source files（exit 0） |
| pytest tests 全量 | exit 0（含新增 4 用例全过） |

## 6. 未验证边界

- C 侧迁移（移除本地 store 直接引用）属 C 包单独 PR，未验证。
- C 消费 `CheckpointPort.scan()` 返回 RecoveryRecord 做 Attempt 续跑的端到端链路，待 C 侧接入后对拍。
