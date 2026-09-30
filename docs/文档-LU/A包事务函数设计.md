# A包事务函数设计

## 背景/目的
为 A 包文件存储提供基于 `request_id` 的非嵌套短事务边界，保证快照、提交、回滚和事务日志的一致性。

## 变更清单

- 在 `src/aitest/infrastructure/file_store/unit_of_work.py` 提供 `begin`、`commit`、`rollback` 三个事务函数。
- 事务状态按 `request_id` 隔离；同一工作空间同一时刻只允许一个活动事务，不支持嵌套。
- `begin` 建立原始数据快照和事务日志起点。
- `commit` 以临时文件、刷新和原子替换发布快照修改，并记录提交日志。
- `rollback` 丢弃快照修改，保留回滚事件日志；任何 IO 异常保留原始数据。
- 返回值统一携带 `request_id`、事务状态和数值返回码。

## 拟定接口

```python
begin(workspace: Path, request_id: str) -> TransactionResult
commit(workspace: Path, request_id: str, changes: Mapping[str, JSONValue]) -> TransactionResult
rollback(workspace: Path, request_id: str) -> TransactionResult
```

`TransactionResult.code == 0` 表示成功；失败时保留原始数据并返回非零错误码。事务日志为追加写入的 JSONL，记录 request_id、事务阶段、时间和结果，不写入业务凭据正文。

## 身份和状态守卫

- `request_id` 是传输事务身份；同一 request_id 重传必须返回同一事务结果，不得创建第二个事务。
- `intent_id` 仍由业务应用层管理，不由本地存储事务函数生成或替代。
- 活动事务只能由其 request_id 提交或回滚；未知、已提交和已回滚事务不得改变数据。
- 一个工作空间最多存在一个活动事务；第二次 begin 返回错误，不覆盖已有快照。

## 待确认的对外合同

<<<<<<< HEAD
当前 `docs/接口对接/A包对外接口文档.md` 尚未定义以下内容，代码实现前必须补齐：
=======
当前 `docs/接口对接/进行中/CORE-001-A包公共接口/contract.md` 尚未定义以下内容，代码实现前必须补齐：
>>>>>>> 9e1d645bd96f7bfc30e0d34f8dba47206ac87f33

1. `1001`、`1002`、`1003`、`1004` 分别对应哪一种错误（参数、事务状态、并发/嵌套、IO 或其他）。
2. `begin` 的快照来源和 `commit` 的修改载荷结构；是完整 JSON 状态、记录集合，还是由 `WorkspaceUnitOfWork` 暂存的记录/索引变更。
3. `commit` 是否必须接收 `intent_id`、`expected_revision`、workspace_id，以及返回事务日志编号或提交序号。

在上述合同确认前，不实现函数，避免让实现自行冻结一个与 B/C/D 调用方不兼容的错误码和载荷协议。

## 验证计划

- 正常 begin/commit 后重启读取已提交数据。
- begin 后 rollback，数据恢复原快照且回滚日志仍存在。
- 提交前后注入 IO 故障，验证原始数据和事务日志完整性。
- 重复 request_id、未知 request_id、嵌套 begin 和跨 request_id commit/rollback 均返回约定错误码。
