# ExecutionPort与Runner骨架说明

版本：0.1  
日期：2026-09-25  
状态：代码骨架与合同测试已落地，完整执行流程尚未实现  
分支：feat/package-c-execution

## 1. ExecutionPort

ExecutionPort只负责一个实际执行句柄，不写业务记录，不计算断言。

- start(request) -> ExecutionHandle
- inspect(handle) -> ExecutionInspectionResult
- collect(handle, cursor) -> ExecutionCollectionResult
- request_stop(handle) -> StopRequestResult

### ExecutionInspectionResult

- handle_id
- state：running、exited、stopped、lost、unknown
- process_reachable
- identity_matches
- stop_confirmed
- observed_at
- unknown_reason

规则：

- 进程不可达不等于未执行。
- 身份不匹配时不能认领为原Attempt。
- 停止未确认时不能返回已取消终态。
- unknown必须携带原因，不能静默降级。

### ExecutionCollectionResult

- attempt_id
- output_blocks
- output_cursor_ref
- exit_fact_ref
- structured_result_ref
- capture_completeness
- error_ref
- complete

规则：

- 只引用已经校验归属和摘要的输出块。
- 游标不能代替已持久化内容。
- 退出码和采集完整性分开。
- 结构化结果只保存事实引用，不在端口层决定业务通过。

### StopRequestResult

- handle_id
- stop_confirmed
- observed_state
- unknown_reason

规则：

- stop_confirmed为true只表示停止已确认。
- 未确认时保持cancelling、stop_requested或pending_verification。
- 网络断开、窗口关闭和控制超时不作为停止证明。

## 2. SerialRunner骨架

当前SerialRunner提供：

- plan_dispatch(steps)：计算ready、blocked、waiting、terminal集合。
- start_attempt(attempt, request)：调用ExecutionPort.start并绑定真实句柄。
- inspect_attempt(attempt)：调用ExecutionPort.inspect。
- collect_attempt(attempt, cursor)：调用ExecutionPort.collect。
- request_stop(attempt)：调用ExecutionPort.request_stop。

调度规则：

- 上游Completed只使依赖满足。
- 上游Blocked、Cancelled、Invalidated或ExecutionError阻塞其下游。
- 上游PendingVerification只让下游Waiting，不直接判失败。
- 没有依赖的Pending或Ready步骤进入Ready。
- 运行中步骤进入Waiting。
- 重复step_id直接报错。
- Attempt和ExecutionRequest的attempt_id、run_id、step_id必须一致。
- 没有真实句柄时禁止inspect、collect或stop。

## 3. 当前未实现

- 真实执行循环、轮询间隔和取消收敛。
- Attempt状态持久化与提交。
- spool创建、块封口、核心认领和恢复。
- 源码身份检查。
- 七类适配器实现。
- 并行组、暂停边界和编辑器生命周期。
- 权限、幂等、授权和业务依赖失效的事务提交。

## 4. 验证

- pytest完整测试通过：46 passed。
- ruff check通过。
- ruff format --check通过。
- mypy strict通过。
