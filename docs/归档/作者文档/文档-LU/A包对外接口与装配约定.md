# A包对外接口与装配约定

版本：1.0  
日期：2026-09-29  
状态：A包一期底座接口说明  
提供方：A包（本地核心底座）  
调用方：B/C/D包  
协议版本：`aitest.local/2.0`  
合同源码：`src/aitest/contracts/`、`src/aitest/application/ports.py`

## 1 模块概述

A包为B/C/D包提供本地工作空间身份、单写事务、不可变记录、有限索引查询、错误归一化和唯一装配入口。A包只保存已核实的业务材料和引用，只负责底座能力与用例路由装配，不生成测试计划、不执行被测业务、不判定业务通过/失败、不编写页面。

B/C/D包通过注册表提供自己的用例处理器，并通过A包事务和查询接口读写已提交事实；不得直接修改工作空间文件、清单、索引或事务日志。

## 2 公共约定

### 2.1 协议与身份

所有命令和响应使用 `aitest.local/2.0`。协议版本不匹配时拒绝请求，不使用默认值继续写入。

`request_id` 是一次传输请求的稳定身份，用于重试、响应匹配和输入指纹去重。同一会话中，相同 `request_id` 必须携带相同输入；输入不同返回 `REQUEST_CONFLICT`，不会创建第二个结果。

`intent_id` 是上层应用创建的持久业务意图，绑定项目、动作和输入摘要。它必须独立于 `request_id`，用于跨入口幂等和业务记录关联。明确重跑必须由上层创建新的 `intent_id`；A包不生成业务意图，也不把 `request_id` 当作 `intent_id`。

### 2.2 错误码

| 错误码 | 含义 | 调用方处理 |
| --- | --- | --- |
| `0` / 成功响应 | 请求达到接口声明的边界 | 读取结构化结果 |
| `REQUEST_CONFLICT` | 同一 `request_id` 的输入指纹不同 | 使用新的请求身份，不重放旧意图 |
| `AWAITING_USER_CONFIRMATION` | Agent Relay 试图执行人工确认动作 | 交由受控人工入口确认 |
| `WORKSPACE_IN_USE` | 工作空间已有活动写锁 | 等待原核心释放或读取状态 |
| `TRANSACTION_NOT_FOUND` | 请求不属于活动事务 | 先读取恢复状态，不盲目提交 |
| `INVALID_TRANSACTION_STATE` | 终态或非法状态上的操作 | 按返回状态处理，不重复执行 |
| `TRANSACTION_CONFLICT` | 嵌套事务或其他请求占用写边界 | 结束已有事务后重试 |
| `RECORD_REVISION_CONFLICT` | `expected_revision` 过期 | 读取准确修订后由上层决定重试 |
| `INTENT_CONFLICT` | 同一 `intent_id` 输入摘要不同 | 创建新的业务意图 |
| `INDEX_REBUILD_REQUIRED` / `maintenance_required` | 索引缺失、损坏或版本不兼容 | 发起显式维护，不允许全表扫描 |
| `invalid_cursor` | 游标格式或位置非法 | 使用上一次返回的游标或从首游标开始 |
| `CAPABILITY_UNAVAILABLE` | 动作未注册或能力未装配 | 查询能力后再决定入口 |
| `STORAGE_NOT_WRITABLE` | 文件系统无法可靠写入 | 保留旧提交边界并报告存储故障 |
| `INTEGRITY_CHECK_FAILED` | 身份、摘要、清单或对象校验失败 | 进入恢复/抢救流程 |
| `CONNECTIVITY_FAILED` | 连接策略耗尽重试仍失败 | 保留本地能力，不推断业务结论 |

错误消息必须脱敏，不包含凭据正文、原始堆栈或未过滤外部输出。所有响应统一使用 `contracts.errors.ErrorDTO`，错误码可为已登记枚举值或协议扩展字符串，但必须保持 `schema_version`、`message`、`retryable` 和 `next_step` 字段结构一致。

### 2.3 状态

事务状态：`not_started`、`active`、`committed`、`rolled_back`、`failed`。只有 `active` 可以提交或回滚；`failed` 不自动重放未知副作用。

查询状态：`ok`、`maintenance_required`、`invalid_cursor`。`maintenance_required` 不是空结果，调用方必须保留维护缺口；查询不得隐式读取记录文件或对象文件进行全表补偿。

### 2.4 永久材料

业务记录、历史修订、证据对象、报告、导出、诊断、提交日志和清单只追加保存。A包不提供永久业务数据删除、覆盖或按时间自动清理接口。

## 3 接口总览

| 接口 | 类型 | 用途 |
| --- | --- | --- |
| `begin` | 写事务 | 建立单写短事务和快照边界 |
| `commit` | 写事务 | 原子发布暂存材料、清单和索引 |
| `rollback` | 写事务 | 丢弃暂存材料并保留回滚事实 |
| `query` | 只读 | 使用维护索引执行有限 `QuerySpec` 查询 |
| `create_api` | 装配 | 创建本地协议 API 实例 |
| `register_use_cases` | 装配 | 注册B/C/D用例表，不允许覆盖已有动作 |
| `doctor` | 只读 | 返回协议、工作空间和装配状态 |

## 4 `begin`

### 4.1 签名

```python
begin(
    request_id: str,
    workspace_id: str,
    project_id: str,
    intent_id: str | None = None,
) -> Response
```

### 4.2 入参

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `request_id` | 是 | 本次传输请求身份 |
| `workspace_id` | 是 | 必须匹配当前核心实例身份 |
| `project_id` | 是 | 事务所属项目 |
| `intent_id` | 否 | 上层业务意图；不由A包生成 |

### 4.3 出参与状态

成功返回 `state=active`、`request_id`、工作空间身份和快照/事务引用。重复相同请求返回原响应；同一工作空间已有活动事务返回 `TRANSACTION_CONFLICT` 或 `WORKSPACE_IN_USE`。

### 4.4 调用约束

一个工作空间同时只能有一个活动写事务；不支持嵌套。外部执行、网络和模型调用不得持有事务锁。`begin` 成功后必须调用 `commit` 或 `rollback`。

## 5 `commit`

### 5.1 签名

```python
commit(request_id: str, workspace_id: str) -> Response
```

### 5.2 入参

`request_id` 必须是当前活动事务的请求身份；`workspace_id` 必须匹配实例。记录暂存由事务端口完成，API不接收业务结论。

### 5.3 出参与状态

成功返回 `state=committed`、提交序号或创建记录引用。提交只在记录、对象、清单和索引达到声明的持久化边界后可见。IO、刷新或替换失败返回 `STORAGE_NOT_WRITABLE`/`INTEGRITY_CHECK_FAILED`，旧提交边界保持有效。

## 6 `rollback`

### 6.1 签名

```python
rollback(request_id: str, workspace_id: str) -> Response
```

### 6.2 出参与约束

成功返回 `state=rolled_back`。暂存记录、索引和对象引用对后续查询不可见，但回滚事件日志保留。未知请求、终态事务或其他请求的事务不得被回滚。

## 7 `query`

### 7.1 签名

```python
query(
    request_id: str,
    workspace_id: str,
    spec: QuerySpec,
) -> QueryResponse
```

### 7.2 `QuerySpec`

`QuerySpec` 至少包含 `project_id`，可选 `aggregate_kind`、`record_id`、`revision`，固定排序字段、方向、`limit` 和 `cursor`。`limit` 为有界值，当前上限为 500。

### 7.3 出参与状态

成功返回 `status=ok`、索引摘要列表和 `next_cursor`。索引缺失、损坏或版本不兼容返回 `status=maintenance_required`（协议错误码可映射为 `INDEX_REBUILD_REQUIRED`）；无效游标返回 `status=invalid_cursor`。

### 7.4 调用约束

查询只读维护索引，不修改记录，不删除材料，不读取记录文件进行全表补偿。索引重建是显式维护动作，不由普通查询自动触发。列表读取摘要，详情必须按准确记录引用和 revision 读取。

## 8 `create_api`

### 8.1 签名

```python
create_api(
    workspace_root: Path | None = None,
    handlers: Mapping[str, Handler] | None = None,
) -> LocalAPI
```

### 8.2 行为

带 `workspace_root` 时，A包校验或创建工作空间身份，装配文件事务端口和已注册用例快照，并在首个事务开始时取得工作空间单写锁。同一工作空间在同一进程内返回同一核心实例的 API。未提供工作空间时只创建未就绪的协议 API，不宣称具备持久化能力。

`handlers` 仅用于兼容单次本地 API 创建；正式B/C/D接入应使用 `register_use_cases`，以保持统一装配边界。

## 9 `register_use_cases`

### 9.1 签名

```python
register_use_cases(
    package: Literal["B", "C", "D"],
    handlers: Mapping[str, Handler],
) -> None
```

### 9.2 调用约束

注册动作必须映射到可调用处理器。动作名在全局注册表中唯一；重复注册、覆盖和删除均拒绝。首个工作空间核心实例创建后注册窗口关闭，后续注册返回错误；已创建 API 不接受后续用例变更，避免实例之间的路由不一致。B/C/D只能提供自己的用例实现，不能访问或替换A包工作空间、事务端口、索引和错误归一化逻辑。

## 10 `doctor` 状态接口

### 10.1 请求

通过 `Command(request_id=<id>, action="doctor")` 调用，入口会话必须由受信宿主创建。

### 10.2 出参

```json
{
  "status": "READY | NOT_READY",
  "protocol": "aitest.local/2.0",
  "supported_actions": ["doctor"],
  "phase": 1
}
```

`READY` 只表示工作空间和A包装配入口已建立，不表示业务计划、执行、判定或平台上传已完成。未装配工作空间时返回 `NOT_READY`；能力缺失以具体动作的 `CAPABILITY_UNAVAILABLE` 表示。

## 11 统一调用时序

1. B/C/D通过 `register_use_cases` 注册自身动作。
2. A包通过 `create_api(workspace_root)` 创建唯一核心实例。
3. 调用方发送 `begin`，取得活动事务。
4. 外部执行和网络调用在事务外完成。
5. 已核实材料通过存储端口暂存。
6. 成功时调用 `commit`；失败时调用 `rollback`。
7. 使用 `query` 读取已提交摘要；索引维护状态必须显式处理。
8. 使用 `doctor` 读取底座状态，不将其解释为业务结果。

## 12 A包边界与未实现能力

- A包不生成计划、不执行业务代码、不判定业务结论、不关闭问题、不编写B/C/D页面。
- A包不提供永久业务材料删除、覆盖、隐式清理或按时间淘汰接口。
- A包不提供分布式事务、HTTP服务、消息队列或独立发送Worker。
- `maintenance_required` 不代表查询为空，也不允许通过全表扫描绕过索引维护。
- 内存假存储只用于并行开发，真实 Windows 单写锁、掉电恢复、权限拒绝和替换故障需独立验收。

