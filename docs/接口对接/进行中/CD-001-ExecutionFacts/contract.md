---
contract_id: CD-001
title: ExecutionFacts
provider: C
consumer: D
contract_version: "1.0"
contract_status: reviewing
provider_implementation: done
consumer_implementation: not_started
verification_status: fixture_passed
last_verified_commit: 8d9883c
blockers: []
next_owner: D
next_action: 确认 Schema、多流游标、超时和非 UTF-8 处理并完成消费方接入
---

# C包-D包 ExecutionFacts 对接说明

版本：1.0  
日期：2026-09-27  
状态：草案，待D包确认  
提供方：C包（执行与证据）-赵  
调用方：D包（判定、报告与用户入口）  
保存方：A包（核心底座与存储）  
Schema版本：aitest.execution-facts/1.0  
合同源码：src/aitest/contracts/execution_facts.py  
生成Schema：src/aitest/contracts/schemas/ExecutionFacts.json

## 1 目的

ExecutionFacts是C包对D包交付的单个Run一致快照。C只提供执行事实、证据、来源核对、未知原因和缺口，不计算最终业务结论。

D只读取已提交快照，不重算执行事实，不根据退出码、响应成功或日志文案直接推断业务通过。

## 2 所有权边界

C负责：

- Run、Step、Attempt实际执行事实。
- 当前Attempt和尝试序号。
- stdout/stderr独立游标与输出块引用。
- ExitFact、超时和采集完整性。
- EvidenceRef、Verification、TraceNode、Mock和来源核对。
- 依赖失效事实和未知原因。

D负责：

- E/R/V/P/F/U/H聚合与业务通过、失败、未完成。
- 证据等级、报告口径和用户展示。
- 是否可以复用、是否需要新增执行。

A负责：

- 按一个commit保存和读取一致快照。
- 对象摘要和引用的实际保存。

## 3 顶层一致快照

核心定位字段：

- schema_version
- facts_id
- project_id
- run_id
- snapshot_commit_id
- snapshot_cursor
- snapshot_revision
- committed_at
- run_revision
- plan_revision
- run
- steps
- attempts
- current_attempt_by_step
- evidence_refs
- source_check_results
- source_verifications
- verifications
- mock_declarations
- dependency_invalidations
- unknowns
- gaps
- coverage
- completeness

D必须按同一commit和snapshot_cursor读取整个快照，不允许混合多个提交。

## 4 关键状态

RunControlState：

- not_started
- running
- pause_requested
- paused
- cancel_requested
- cancelling
- recovering
- pending_verification
- completed
- cancelled
- execution_error

StepState：

- pending
- ready
- running
- blocked
- pending_verification
- completed
- cancelled
- invalidated
- execution_error

AttemptState：

- intent_recorded
- starting
- running
- stop_requested
- collecting
- completed
- cancelled
- pending_verification
- execution_error
- invalidated
- unknown

ProcessTerminationReason：

- natural_exit
- timeout
- confirmed_stop
- executor_lost
- capture_failure
- unknown

状态规则：

- completed只表示执行事实收集完成，不表示业务通过。
- blocked不是业务断言失败。
- execution_error不是业务断言失败。
- timeout通过ExitFact.timed_out和termination_reason=timeout表达。
- 超时Attempt保持pending_verification，不自动重放未知副作用。
- 同一Attempt的stdout/stderr必须有独立游标。

## 5 夹具清单

目录：docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures/

- success.json：正常完成、单stdout流。
- failure.json：上游execution_error、下游blocked。
- unknown.json：进程结果未知和证据缺口。
- quick.json：quick档，conclusion_ceiling=partial，evidence_level=null。
- timeout.json：命令超时、ExitFact.timed_out=true、pending_verification。
- multistream.json：stdout和stderr分别具有块和独立游标。
- non_utf8.json：非UTF-8原始字节场景。
- non_utf8.stdout.bin：stdout原始字节侧车。
- non_utf8.stderr.bin：stderr原始字节侧车。

所有JSON夹具均通过ExecutionFacts Pydantic模型校验。

## 6 多流游标

AttemptFact.output_cursors为数组，每个元素对应一个OutputStreamName。

- 不同流不能共用一个offset。
- read_block必须按offset和length读取，并重新校验摘要。
- D展示阻塞进度时按流读取，不把最后一个流覆盖其他流。

## 7 兼容规则

- 新增可选字段：保持1.x兼容，必须补夹具和说明。
- 删除字段、改名或改变语义：必须提升主版本并双方确认。
- 枚举新增值：合同所有者和调用方必须先讨论兼容行为。
- D遇到未知枚举值：不能静默当作通过，必须按未知处理。
- C不能把D的最终结论写入ExecutionFacts。
- D不能直接读取未发布的spool文件或以文件名推断证据。
- A保存快照时必须保留未知原因、缺口和引用摘要。
- 非UTF-8内容按字节保存，media_type可为application/octet-stream。

## 8 待D确认

1. 确认采用aitest.execution-facts/1.0。
2. 确认多流游标以output_cursors数组消费。
3. 确认timeout表现为pending_verification + ExitFact.timed_out + termination_reason=timeout。
4. 确认非UTF-8证据只按object_digest和字节读取，不强制UTF-8解码。
5. 确认D不读取C内部spool路径，只读取已提交的ExecutionFacts和对象引用。
6. 确认后续合同变更重新走契约PR。

## 9 确认

确认：[ ] C包 赵    日期：  
确认：[ ] D包       日期：  

合并到develop并双方勾选后，本文件作为C-D ExecutionFacts唯一对接依据。
