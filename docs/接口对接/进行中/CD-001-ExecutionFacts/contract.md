---
contract_id: CD-001
title: ExecutionFacts
provider: C
consumer: D
contract_version: "1.4"
contract_status: reviewing
provider_implementation: partial
consumer_implementation: partial
verification_status: not_run
last_verified_commit: 8d9883c
blockers: []
next_owner: C/D
next_action: C 补准确冻结来源读取及完整复用材料，D 接入资格与保存选择；真实对拍另记
---

# C包-D包 ExecutionFacts 对接说明

版本：1.4
日期：2026-10-08
状态：1.0历史确认保留；1.1—1.4增量评审中，待完整复用接入和真实夹具对拍
提供方：C包（执行与证据）-赵  
调用方：D包（判定、报告与用户入口）  
保存方：A包（核心底座与存储）  
Schema版本：aitest.execution-facts/1.0  
合同源码：src/aitest/contracts/execution_facts.py  
生成Schema：src/aitest/contracts/schemas/ExecutionFacts.json

1.3 准确检查点来源：非空 Attempt 快照与不可变 `execution_checkpoint_refs@1`、当前指针在同一工作单元提交，快照仍为最后发布记录。引用表 Schema 为 `aitest.execution-checkpoint-refs/1.0`，身份由项目、运行、快照编号规范摘要生成，冻结 workspace、snapshot_cursor、snapshot_revision=1、snapshot_digest，以及完整 Attempt 身份到 `{record_id, revision, digest}` 的映射。revision 是实际 `stage_record` 返回的检查点仓储修订，不能由 Attempt 正文版本推算；digest 核对完整保存检查点正文。未改变的历史 Attempt 可保留上一快照准确检查点，当前事实变更须核对相应暂存或准确保存检查点投影。仓储修订必须为实际正整数，检查点项目/运行/步骤/Attempt及正文投影均须与快照一致。

历史复用消费只按表中准确修订读取，不回退最新检查点。缺表、表或引用错位、缺失正文、摘要或投影不符时阻塞复用；旧快照仍可按原 DTO 展示，但不能补猜可复用来源。空 Attempt 初始快照不创建空表，也不产生复用资格。新增内部伴随记录不改变 ExecutionFacts DTO、Schema及旧快照摘要；只证明检查点来源，R 资格、选择持久化、证据和独立核验仍另行核对。该记录随 A 原提交清单、索引与备份保存；不得写成恢复旁录或上传许可。FR/AC不变，reviewing/partial/not_run不变。

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

## 8 D侧确认项

1. [x] 确认采用 `aitest.execution-facts/1.0`。
2. [x] 确认 stdout／stderr 使用独立的 `output_cursors` 数组消费。
3. [x] 确认 timeout 表现为 `pending_verification + ExitFact.timed_out=true + termination_reason=timeout`。
4. [x] 确认非 UTF-8 证据只按 `object_digest` 和字节读取，不强制 UTF-8 解码。
5. [x] 确认 D 不读取 C 内部 spool 路径，只读取已提交的 `ExecutionFacts` 和对象引用。
6. [x] 确认后续合同变更重新走契约 PR。

## 9 D侧 evidence_level 使用确认

`EvidenceFact.evidence_level` 继续作为 C 发布的**逐条证据事实**保留：

- D 可以显示该值并按对象引用核对对应证据；
- D 不修改 C 写入的 `evidence_level`；
- D 不把该字段直接当作最终 Run 级 A／B／C／D 证据等级；
- Run 级证据等级仍由 D 根据同一提交中的 S／M、E／R／V／P／F／U／H、源码身份、关键链路、必需证据、必要核验、过期依据、未知与缺口等事实唯一派生；
- 遇到未知枚举值或无法核对的引用时按 unknown 处理，不默认为充分证据。

因此，C 的“透传事实”和 D 的“报告级派生等级”是不同层次，不构成重复所有权。

## 10 确认

确认：[x] C包 赵    日期：2026-09-27  
确认：[x] D包 郭    日期：2026-10-02  

双方确认后，本文件作为 C-D ExecutionFacts 唯一对接依据；消费方接入和真实夹具对拍由 `review-D.md` 跟踪。

## 11 准确冻结来源读取（1.1增量）

跨运行复用或修订核对使用准确 `snapshot_commit_id`、`snapshot_cursor`、原正文摘要及明确的project/run，读取不可变 `execution_facts@1`；不能拿最新current或run_revision代替仓储修订。原正文和记录信封、快照自身身份及Step/Attempt当前引用必须一致；整数、布尔等JSON类型严格校验，不以类型转换修饰已保存材料。缺失、损坏、另存修订或错误归属拒绝，没有历史扫描或回退到另一份成功快照。

current回读与冻结来源回读共用同一事实校验。可读历史仅证明该快照的原事实，不证明当前复用资格；来源/环境动态状态、准确用例/依据确认、必要核验/证据、依赖有效性和目标无新Attempt均须另核对。旧缺证明仍可按原合同展示历史，不能自动增加R/V，也不能为复用创建空Attempt。1.1不改变公开DTO/Schema字段或1.0已确认的业务词汇，完整持久选择和D消费继续实施。

## 12 整用例准确来源映射（1.2增量）

C内部读取一份准确源快照、原run@1、原prepared_run@1及原准备回执，按源Case的每个本地步骤位置核对完整冻结正文、准确StepRevisionRef和原稳定步骤身份，返回该源快照的Step→当前Attempt映射。源Run的物理step_id与其他Run不同，本地冻结step_id与case_step_index用于对照；不能把其他Run的物理ID当作本轮步骤或拼接多个快照。

缺原准备/信封/摘要、未知Case、遗漏/重复位置、正文修订混合、原步骤身份或必需标记被替换均拒绝。原准备及记录只按准确引用读取，不回填缺失证明。未执行源步骤的Attempt保持null；可读源映射不等于有效整用例复用、不增加R/V，也不制造本轮Attempt。实际动态环境、规则/输入、确认/核验/证据、源当前有效性、持久选择及新Attempt撤销仍由后续完整守卫核对。公开ExecutionFacts/Schema在此阶段不增加字段。


### 历史来源的实际输出与证据可读性（1.4，2026-10-08）

整 Case 来源读取除准确快照、检查点及步骤正文外，须经 A spool 端口按准确检查点读取选定源 Attempt 的输出块，证据经对象端口读取；已有准确发布的COMMAND_OUTPUT证据须核对其稳定块编号、归属、摘要/长度及实际对象，允许从永久对象读取，不依赖已合法回收的spool，逐项核对项目、运行、Step/Attempt 归属、实际字节长度与 sha256，不以记录或文件存在代替可读性。材料非空但缺相应读取端口、缺失/损坏字节或错误归属时拒绝来源读取；空的初始历史仍可展示且不产生 R。对象核对证明可读性，不证明脱敏摘要来源、断言/独立核验或当前动态环境有效。

纯聚合的 R 还须按 BC-001 0.45 消费完整资格依据；继承标记不能授予 R/V/H。实际环境/依赖核实、完整证据与脱敏来源闭包、持久选择及 D 默认接线仍开放，公开 ExecutionFacts/Schema 不变，原记录不迁移，reviewing/partial/not_run 和真实 AC 不变。
