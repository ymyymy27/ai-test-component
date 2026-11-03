# D部分与C执行证据对接说明

版本：0.1（草案）  
日期：2026-09-28  
提出方：D包（判定、报告与用户入口）- 郭  
提供方：C包（执行与证据）- 赵  
状态：待 C 与双方确认；与现有 `docs/接口对接/C包-赵-D包/C包-D包 ExecutionFacts 对接说明.md` 保持同一语义

## 1. 目的

D 根据 C 发布的单次 Run 一致快照 `ExecutionFacts` 计算业务结论、证据等级、问题和报告。C 提供执行事实，不提供最终业务结论；D 不重算执行事实，也不读取 C 尚未发布到快照内部的 spool 文件。

## 2. 所有权边界

C 负责：

- Run、Step、Attempt 的实际执行生命周期。
- 当前 Attempt、尝试序号和重试事实。
- stdout/stderr 输出块、独立游标、ExitFact、超时和采集完整性。
- EvidenceRef、Verification、TraceNode、Mock 声明和来源核对。
- 依赖失效事实、未知原因和证据缺口。

D 负责：

- E/R/V/P/F/U/H 聚合、业务结论、证据等级和报告口径。
- 问题创建、归并、复核、处置、回归和关闭。
- 报告修订、用户展示和本地导出。

A 负责：

- 按一个 commit 保存 `ExecutionFacts` 和对象引用。
- 保证 D 读取的 Run、Step、Attempt、Evidence、Unknown、Gap 来自同一快照。

## 3. D 需要的稳定快照

D 至少需要以下顶层字段：

- `schema_version`
- `facts_id`
- `project_id`
- `run_id`
- `snapshot_commit_id`
- `snapshot_cursor`
- `snapshot_revision`
- `committed_at`
- `run_revision`
- `plan_revision`
- `run`
- `steps`
- `attempts`
- `current_attempt_by_step`
- `evidence_refs`
- `source_check_results`
- `source_verifications`
- `verifications`
- `mock_declarations`
- `dependency_invalidations`
- `unknowns`
- `gaps`
- `coverage`
- `completeness`

D 必须读取整个一致快照，不得把不同 commit 的字段拼接。任何列表分页或详情读取都必须绑定同一 `run_id`、`snapshot_commit_id` 和 `snapshot_cursor`。

## 4. D 需要的状态语义

RunControlState：

`not_started`、`running`、`pause_requested`、`paused`、`cancel_requested`、`cancelling`、`recovering`、`pending_verification`、`completed`、`cancelled`、`execution_error`。

StepState：

`pending`、`ready`、`running`、`blocked`、`pending_verification`、`completed`、`cancelled`、`invalidated`、`execution_error`。

AttemptState：

`intent_recorded`、`starting`、`running`、`stop_requested`、`collecting`、`completed`、`cancelled`、`pending_verification`、`execution_error`、`invalidated`、`unknown`。

D 的展示和判定规则：

- `completed` 只表示事实采集完成，不表示业务通过。
- `blocked` 不是业务断言失败。
- `execution_error` 不是业务断言失败。
- `timeout` 通过 `ExitFact.timed_out=true` 和 `termination_reason=timeout` 表达。
- 超时 Attempt 保持 `pending_verification`，D 不自动重放未知副作用。
- D 遇到未知枚举值不得静默当作通过，必须按未知或未确认处理。

## 5. D 需要的字段类别

### 5.1 Step 与 Attempt

- Step 所属 run、准确 step revision、ordinal、case、level、必测标记、状态和当前 Attempt。
- Attempt 的 attempt revision、序号、重试次数、当前标记、状态、步骤修订引用、来源摘要、副作用类别、适配器种类/版本。
- `current_attempt_by_step` 用于确定当前事实，旧 Try 不覆盖当前 Try。

### 5.2 输出与退出

- stdout/stderr 的 block ID、offset、length、digest、complete 和 capture source。
- 每个流的独立 cursor、durable 和最后提交摘要。
- ExitFact 的启动身份、真实退出码、各流最后块、保存字节、采集完整性、终止原因和超时标志。
- 非 UTF-8 内容按 bytes 保存并通过媒体类型和对象摘要读取，不能因解码失败丢失证据。

### 5.3 证据、来源与核验

- 证据类型、来源、摘要、媒体类型和可读取的对象引用。
- 证据完整性、脱敏状态、投影状态和缺口。
- 来源核验的 expected/actual 身份、状态、原因和证据引用。
- 独立核验的观察结果、是否独立、关联证据和核验时间。
- Mock 声明的来源、验证状态、影响范围及证据。

### 5.4 未知、缺口和依赖失效

- 未知原因的主体类型、主体 ID、原因码、安全说明、是否需要核验和建议动作。
- 缺口类型、影响对象、原因码、安全说明、严重性、是否需要用户动作。
- 依赖失效影响的 Step/Attempt、上游尝试、原因、来源修订、传递性和证据。
- `error_ref` 的解析方式：D 需要结构化错误引用或可查询详情，不能只拿到一个无法展示的字符串。

## 6. D 对消费方式的要求

- 采用 `aitest.execution-facts/1.0`，变更按兼容规则走合同 PR。
- 采用 `output_cursors` 数组，stdout 和 stderr 不共用 offset。
- 只读取已提交对象引用，不读取 C 的内部 spool 路径。
- 阅读对象时重新校验摘要；摘要不一致按损坏处理。
- D 不把执行完成、真实退出码 0、接口响应成功或日志文案直接映射为业务通过。
- D 不把 blocked、execution_error 或 timeout 自动映射为业务断言失败。
- D 的未知、缺口、状态和错误展示必须保留原始枚举，并在报告中保留原因。

## 7. D 输出的结果

C-D 接口本身是 C 产生、D 读取的单向事实接口。D 在其业务存储中另行产生：

- 唯一业务结论和证据等级。
- 问题、复核、处置、回归和关闭事实。
- 报告修订和导出产物。

D 不把这些结果写回 `ExecutionFacts`，避免 C 的事实合同混入 D 的业务结论。

## 8. 联调与验收清单

- [ ] 双方确认 Schema 版本 `aitest.execution-facts/1.0`。
- [ ] 成功、失败、未知、quick、timeout、多流和非 UTF-8 夹具全部通过解析。
- [ ] D 能从当前 Attempt 正确处理旧尝试和依赖失效。
- [ ] D 能区分采集完成、业务通过、有效验证和未完成。
- [ ] D 能展示未知原因、严重缺口和下一步动作。
- [ ] D 能按一个 commit 读取一致快照并正确处理对象摘要不一致。
- [ ] 双方确认未知枚举和新增枚举的兼容行为。

## 9. 待 C 回答

1. `error_ref` 对应的结构化错误模型、错误码目录和读取入口是什么？
2. `reason_code`、`gap.kind` 等字符串枚举是否有版本化清单和未知值策略？
3. 事件续读时，D 如何从 `snapshot_cursor` 获取后续已提交的 ExecutionFacts 修订？
4. `source_check_results`、`source_verifications` 和 `verifications` 的最终展示字段是否已冻结？
5. `coverage` 是执行事实摘要还是 C 侧统计投影？D 只将其作为输入，不将其当业务结论。
6. 计划修订与运行中变更如何反映到同一 Run 的 `plan_revision` 和 Step revision？

## 10. 确认记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-28 | 0.1 | D 侧初稿；待 C 确认 | D 包郭 |