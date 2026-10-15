---
contract_id: CD-001
title: ExecutionFacts
provider: C
consumer: D
contract_version: "1.28"
contract_status: frozen
provider_implementation: done
consumer_implementation: done
verification_status: fixture_passed
last_verified_commit: 12007d56951d5453ed8fcaa6b18e54901e31f524
blockers: []
next_owner: D/C/B
next_action: 现行接口已关闭；后续字段变更重新评审，真实产品验收继续按项目现状跟踪。
---

## 当前逐项处理结论（2026-10-16）

当前状态只读文件顶部元数据。下面带旧日期的实施记录保留原基线；其中旧状态数字和人员待答文字不构成当前待办。

按用户明确授权代理完成六包间技术确认，并在用户指定的 fix 集成分支冻结；不补写他人历史签名。提供/消费的现行接口实现记 done，验证保持 fixture_passed。关闭依据与逐包异常分支见[关闭核对](../../../validation/p1-runtime-source-20261015/interface-closure.md)，不是一期产品全部完成或真实环境验收通过。

- 已实现：ExecutionFacts 当前 Attempt/来源事实消费、独立核验、报告/交付/ZIP 与重启回读已接；半程样例由 C 生产装配器生成并经 D 判定消费。
- 产品实施/验收跟踪（未关闭）：可信动态复用、所有实际载体、真实业务问题关闭及恢复验收仍有缺口。
- 237 项跨包关闭补测、14 项双侧半程夹具通过；完整样例执行、核验、报告、提测、导出和重启证据另列，不累计成全量。
- 当前准确范围见[交付说明](delivery-D.md)；旧版本记录保留追溯，合同后续变更重新评审。


## 半程交付夹具实施决定（2026-10-16）

按用户授权落实 BC 第 17 节：提供 R1 running、R2 pause_requested/paused 连续快照、R3 修订失效样例，共四个 JSON。使用生产 ExecutionFactsAssembler 生成，交付与消费副本一致；D 侧读取后不得将中间态判成完整通过。保留已完成 Attempt/证据和准确当前引用，R2 只在步骤边界成为 paused，R3 保留历史并携带失效依据与修订引用。该决定结束“待 C 选择方案”，实现与检查结果另行登记，不冒充真实半程用户验收。


## 实际进程来源采集增量（2026-10-15）

按一期架构01第12节实施 `python-script/1`。新默认动作冻结可选 `ExecutionRequest.source_capture`（策略、受控载体代码摘要、固定 Python 文件清单 `relative_path/content_digest`）；与原动作一起进入授权摘要，载体变化须重新准备。普通文件入口由登记解释器使用隔离载体执行，业务脚本及参数保持冻结值，`sys.path` 首项为脚本目录，不读取用户 PYTHONPATH 或用户 site。旧无此字段的动作保持原指纹、原执行方式和未核实边界。

`ExecutionCollectionResult.runtime_source` 与检查点的同名事实可选追加，绑定 Attempt、启动句柄出生身份和实际解释器进程出生身份。Windows venv 启动器与解释器可为两个 PID：解释器执行前必须由核心核对其属于原独占 Job；只按 PID 相等或进程自述不得通过。来源元数据经继承匿名管道有界收集，过滤后才能保存，不监听端口、不从 stdout 声明取证。

项目加载事件核对真实代码对象、冻结文件摘要和入口。纯 Python 标准库代码可核对；未纳入冻结范围的第三方包、自定义加载、未声明动态代码（含 FunctionType/代码对象替换）、子进程/原生执行、未结束线程及缺失终止回执均保留缺口。新加载原生扩展即使来自标准库，也不推定为来源完整。该策略不承诺对恶意同用户进程的安全隔离。启动前探针单独保存，不能代替实际过程证明。

最终来源元数据发布永久对象并回读核对，与当前检查点、source_check 证据、load 检查事实及新 ExecutionFacts 快照原子挂接。新快照可更新同一次 Attempt 的当前来源投影；旧快照、准入材料、旧报告不改。已经保存的实际观察不可被后续检查点重写。报告只排除有准确证据归属的已替换 Attempt 来源，归属未知或当前来源缺口继续阻断；这不修改 E/R/V、完整范围或关键链路规则。

组件回归、完整样例链、产物与未验证范围统一见[本批证据](../../../validation/p1-runtime-source-20261015/README.md)。真实 Trae、客户业务、物理掉电和全部一期验收仍未通过；本增量不代填双方签署，整体 原批次 reviewing/partial/not_run（非当前状态） 保留。



## 本次技术决定与当前实现（2026-10-15）

现行版本：1.28。纯函数输出独立比对与报告交付投影已接；继续真实动态复用、业务/恢复和全同源对拍。

用户已授权代理决定本轮接口方案；DEC-015/016 已回写并归档，无需等待该两项技术选项确认。实现边界和回归见[本批记录](../../../validation/p1-implementation-audit-20261015/README.md)。整体 原批次 reviewing/partial/not_run（非当前状态） 保留，历史双方签署与真实验收不代填。下文带日期的旧批次结论只证明其原版本。


## 纯函数核验兼容增量（2026-10-15）

依据一期能力清单 §6，落实 [DEC-015](../../归档/裁定/DEC-015-纯函数独立预期比对.md)。依据 `aitest.pure-function-basis/1` 只包含 schema_version 与非空 expected_facts；从准确 Case 读取预期，与当前 Attempt 完整且未过滤的永久 stdout JSON 对象按既有领域规则比较。核验方法保留 `<project_id>:pure-function-stdout`，对象为 Attempt ID，目标为 `attempt:<attempt_id>`，证据引用随核验保存。无 HTTP/数据库/模型依赖，不重跑函数，不作为持久副作用的独立回读；原 verify_pending 形状、HTTP 依据与判定语义兼容。技术裁定由用户授权本次代理作出，实现和验证另记，不代签真实验收。


> 2026-10-15 当前实现补充：按保存引用准备、默认无凭据 HTTP 核验与分步输入已接，见 [CORE 1.88](../CORE-001-A包公共接口/contract.md)、[BC 0.51](../BC-001-PreparedRun/contract.md)及[本批证据](../../../validation/p1-workflow-20261015/README.md)。本合同字段/版本未变，旧章节缺口按各自历史日期理解；真实宿主、其他核验适配与验收仍开放，不代签。


## 1.25 动态核验证据与可携带导出

Verification.evidence_refs 保留动态对象 ID 提取所用的当前 Attempt 输出引用；对象提取合同见 BC 0.52。默认命令适配器接持久 spool，过滤摘要随证据保存，不能把缺摘要材料写成有效核验。ZIP 附件使用 evidence/加证据编号 UTF-8 的 SHA256 十六进制加 .bin，清单 attachments 新增 evidence_id 映射；业务编号不再直接拼接路径，保留路径安全校验及既有规范包回读。不改变原证据 ID 或摘要，不重写历史制品。

## 2026-10-15 全目录复核

报告/问题/复核/制品已有生产者，复用选择与准确来源消费已接；动态资格、独立预期/观察、真实业务恢复仍欠。 本批合同/消费方检查与未验证范围见[证据](../../../validation/p1-interface-review-20261015/README.md)。历史章节只证明对应旧版本，签署与整体对拍状态不代填。


## 当前续修与验证范围（76ab7d2 起点）

R 准确源快照/证据冻结与原快照导出已补；继续准确独立预期、真实动态 R、业务/恢复及全同源对拍。 [本次准确实现/证据](../../../validation/p1-completion-repair-20261013/README.md)。生成 ReadViewQuery 来自 Python 源码；旧协议字段/历史记录可读，旧核心按能力显式降级。原批次 reviewing/partial/not_run（非当前状态） 和历史签署/last_verified_commit 保持，局部测试不等于真实对拍。

## 上一批实现修复（2026-10-13）

现行版本：1.25；修复起点 fix@7b931af，准确代码指纹与原输出见[本轮修复](../../../validation/p1-boundary-fixes-20261013/README.md)。原标准/核验关闭、完整阻塞、后补确认、冻结导出回读已修；继续准确独立预期、动态 R 和真实业务/恢复对拍。

本轮依一期功能第3/12/15节及架构01/03/04/05修实现，没有另定 FR/AC 或修改项目规范；历史签署/last_verified_commit 保留，原批次 reviewing/partial/not_run（非当前状态） 不变。新增报告内部伴随执行快照/阻塞引用可读旧记录，旧无来源指针的材料明确缺口，不落回最新运行；公开 Command/Response/生成 Schema 不改。select/inspect 使用既有执行 SnapshotContentRef 的 snapshot_commit_id/snapshot_cursor/digest，修复错误摘要字段读取，不混业务源码快照。

原 A-20、C-18、D-11—14 的代码关闭仅依本轮回归；C-17 无可信动态资格时继续拒绝 R。局部/类型夹具不等于真实 Trae、业务数据/供应方故障、物理掉电或完整合同对拍。下方原审查内容仅保留历史问题来源。


## 修复前源码复核（7b931af 的原记录）

现行版本：1.22；源码 `fix@a51c2b4`。本版只同步实现、阻塞与交付边界，不改公开协议/Schema、字段签名、保存数据或 FR/AC，不需迁移。历史双方确认、last_verified_commit=8d9883c 的旧阶段引用 保留；原批次 reviewing/partial/not_run（非当前状态） 不因局部回归改成对拍完成。

**已实现及原缺陷修复**：D-10 原当前 Attempt/来源/必需证据守卫已修；issue/report/local_review/report_export 生产用例、实际对象与摘要/ZIP 制品、复测简报已有。assertion_outcomes_from_facts 已有生产者，剩余是期望登记/同源有效依据及本轮四项反例，不能再写成无生产报告/问题/导出或 R 恒空。

**当前阻塞与下一动作**：D 先修关闭依据、完整阻塞集合、有效确认和冻结导出意图回读；C 修 R 资格/统计及选择修订；按同一保存边界对拍报告/问题/制品。 具体反例、组件替身范围与原输出见[本轮验证](../../../validation/p1-fix-review-20261013/README.md)和[逐 FR/AC 对照](../../../一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)，当前状态只查[项目现状](../../../现状.md)。

本轮未完成真实 Trae、实际业务对象/供应方故障、物理掉电或全部合同场景提供/消费方对拍，未新增双方签署。以下历史复核段中的旧缺项/数量只对应原日期；仍有效的正式字段和版本增量继续按正文消费。


## 历史源码复核记录（2026-10-12）

本版 1.18 只同步实现状态、阻塞项及历史内容的生效边界；**不改变端口签名、字段/枚举、协议版本、生成 Schema/夹具或已保存记录语义**，无需数据迁移。取证 `develop@845e697`，整改/证据文档基线 `90baba0`。此记录为自动化源码复核，**不替代提供方/消费方确认**；reviewing/partial 与原确认历史保留。

**已实现**：ExecutionFactsAssembler、准确事实读取、公开夹具、build_decision_facts 及真实文件往返测试已存在；不能把适配器重新列成未实现。

**仍缺／优先动作**：D-10 已修（2026-10-12）：来源身份改为同一快照内集合级判定（任一非 verified 或带缺口即 `unverified`）；E／pending 只认 **当前** Attempt；必需证据逐项核对完整性/脱敏/投影/缺口并覆盖声明项数，引用存在不再等于满足。原三组反例现为拒绝/降级（`incomplete`、`unverified`、`executed=[]`），对照组仍 passed/A。**R 在 D 适配器仍恒空**；生产报告/问题/制品链仍未接。冻结外字段与 execution-facts/1.0 不迁移。

**修复后状态（2026-10-12）**：本批只改 D 侧实现与回归，**不改端口签名/字段/枚举/协议版本/生成 Schema/夹具**；证据见[修复证据](../../../一期工程整改/03-修复证据/2026-10-12-C15-C16-D10-A19修复证据.md)与[修复轮验证](../../../validation/p1-defect-fix-20261012/README.md)。`verification_status` 保持 `not_run`，`last_verified_commit` 保持 `8d9883c`（旧阶段引用），不因局部回归标为对拍完成。

**问题侧接生产（2026-10-12 第二批）**：D 侧新增 `issue` 记录与 `issue_intent` 幂等（同一工作单元、按修订不可变），随 payload 写入 `issues.list` 的 `view/mask/facet_value/severity` 索引入口；`update_issue`／`record_fix`／`close_issue` 已并入默认装配，`close_issue` 的六项依据布尔改由**同一提交快照的执行事实 + 已保存准备与受控确认链 + 可读证据字节**派生，确认人取受控会话身份（AI 中继拒绝）。另修 `PortsRecordReader.query` 丢 `view/facet/severity` 条件导致 `issues.list` 退化为通用目录查询的问题。证据见[FR15 修复证据](../../../一期工程整改/03-修复证据/2026-10-12-FR15问题生命周期生产用例.md)。

**报告/复核接生产（2026-10-12 第三批）**：新增 `report` 记录（判定由**已提交执行事实**经 `build_decision_facts` + `evaluate_review` 派生，冻结输入取自已保存准备与准确范围修订；`reports.list`／`reports.by_id` 摘要键齐备）与 `local_review` 记录（绑定准确报告修订、复核人取受控会话）；`create_report`／`record_local_review` 并入默认装配。依据确认核对抽成 `application/review/authoritative_basis.py::SavedCaseBasisCheck`，问题关闭与报告共用同一权威路径。证据见[FR14 报告证据](../../../一期工程整改/03-修复证据/2026-10-12-FR14报告与复核生产用例.md)。

**导出与复测简报接生产（2026-10-12 第四批，版本升至 1.21）**：实现 `ReportArtifactPort`（临时写入→摘要核对→原子发布，规范相对路径底线、篡改拒绝、幂等复用），导出制品**先入对象库**再发布交付副本；`export_report` 生成 Markdown 摘要与 ZIP 证据包（`report.md` + `manifest.json` + `evidence/`，逐文件 SHA-256、排除/缺失原因），导出身份由冻结输入派生、相同输入复用产物；只读 `copy_repair_brief` 输出脱敏复测指引且不含证据正文。默认可派发动作 50。证据见[FR14 导出证据](../../../一期工程整改/03-修复证据/2026-10-12-FR14导出与复测简报生产用例.md)。仍缺：`request_regression`（合同未定义）、断言结果生产者、R 的持久选择；`verification_status`/`last_verified_commit` 与 FR/AC 不变。

**验证范围**：本轮局部合同/Schema/夹具和文件往返按[接口复核证据](../../../validation/p1-interface-review-20261012/README.md)登记。合同全部场景的统一提供/消费方对拍未完成，因此 verification_status 保持 not_run，不能因部分回归自动标为对接完成。原 last_verified_commit=8d9883c 仅为旧阶段引用，不代表本次统一对拍；本轮失败反例另存。真实 Trae/供应方/掉电与 AC 证据另验。

字段和业务语义仍以对应主责分册与本合同正文为准；以下带日期的版本增量、草案、确认和实施记录保留其当时基线，其中“未注入／无签名／待接线”和旧动作数量不再作为今天的状态。当前关闭条件查四包检查和[项目现状](../../../现状.md)。


# C包-D包 ExecutionFacts 对接说明

历史正文版本：1.18（2026-10-12 状态复核；历史修订与确认按各自日期）
日期：2026-10-12（历史记录保留原条目日期）
状态：1.0历史确认保留；1.1—1.18增量评审中，D-10 守卫待修、完整复用/生产消费与统一对拍未完成
提供方：C包（执行与证据）-赵
调用方：D包（判定、报告与用户入口）
保存方：A包（核心底座与存储）
Schema版本：aitest.execution-facts/1.0
合同源码：src/aitest/contracts/execution_facts.py
生成Schema：src/aitest/contracts/schemas/ExecutionFacts.json

1.3 准确检查点来源：非空 Attempt 快照与不可变 `execution_checkpoint_refs@1`、当前指针在同一工作单元提交，快照仍为最后发布记录。引用表 Schema 为 `aitest.execution-checkpoint-refs/1.0`，身份由项目、运行、快照编号规范摘要生成，冻结 workspace、snapshot_cursor、snapshot_revision=1、snapshot_digest，以及完整 Attempt 身份到 `{record_id, revision, digest}` 的映射。revision 是实际 `stage_record` 返回的检查点仓储修订，不能由 Attempt 正文版本推算；digest 核对完整保存检查点正文。未改变的历史 Attempt 可保留上一快照准确检查点，当前事实变更须核对相应暂存或准确保存检查点投影。仓储修订必须为实际正整数，检查点项目/运行/步骤/Attempt及正文投影均须与快照一致。

历史复用消费只按表中准确修订读取，不回退最新检查点。缺表、表或引用错位、缺失正文、摘要或投影不符时阻塞复用；旧快照仍可按原 DTO 展示，但不能补猜可复用来源。空 Attempt 初始快照不创建空表，也不产生复用资格。新增内部伴随记录不改变 ExecutionFacts DTO、Schema及旧快照摘要；只证明检查点来源，R 资格、选择持久化、证据和独立核验仍另行核对。该记录随 A 原提交清单、索引与备份保存；不得写成恢复旁录或上传许可。FR/AC不变，原批次 reviewing/partial/not_run（非当前状态）不变。

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

目录：docs/接口对接/已完成/CD-001-ExecutionFacts/fixtures/

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

双方确认后，本文件作为 C-D ExecutionFacts 唯一对接依据；消费方接入与真实对拍由当前交付说明跟踪，旧评审已归档。

## 11 准确冻结来源读取（1.1增量）

跨运行复用或修订核对使用准确 `snapshot_commit_id`、`snapshot_cursor`、原正文摘要及明确的project/run，读取不可变 `execution_facts@1`；不能拿最新current或run_revision代替仓储修订。原正文和记录信封、快照自身身份及Step/Attempt当前引用必须一致；整数、布尔等JSON类型严格校验，不以类型转换修饰已保存材料。缺失、损坏、另存修订或错误归属拒绝，没有历史扫描或回退到另一份成功快照。

current回读与冻结来源回读共用同一事实校验。可读历史仅证明该快照的原事实，不证明当前复用资格；来源/环境动态状态、准确用例/依据确认、必要核验/证据、依赖有效性和目标无新Attempt均须另核对。旧缺证明仍可按原合同展示历史，不能自动增加R/V，也不能为复用创建空Attempt。1.1不改变公开DTO/Schema字段或1.0已确认的业务词汇，完整持久选择和D消费继续实施。

## 12 整用例准确来源映射（1.2增量）

C内部读取一份准确源快照、原run@1、原prepared_run@1及原准备回执，按源Case的每个本地步骤位置核对完整冻结正文、准确StepRevisionRef和原稳定步骤身份，返回该源快照的Step→当前Attempt映射。源Run的物理step_id与其他Run不同，本地冻结step_id与case_step_index用于对照；不能把其他Run的物理ID当作本轮步骤或拼接多个快照。

缺原准备/信封/摘要、未知Case、遗漏/重复位置、正文修订混合、原步骤身份或必需标记被替换均拒绝。原准备及记录只按准确引用读取，不回填缺失证明。未执行源步骤的Attempt保持null；可读源映射不等于有效整用例复用、不增加R/V，也不制造本轮Attempt。实际动态环境、规则/输入、确认/核验/证据、源当前有效性、持久选择及新Attempt撤销仍由后续完整守卫核对。公开ExecutionFacts/Schema在此阶段不增加字段。


### 历史来源的实际输出与证据可读性（1.4，2026-10-08）

整 Case 来源读取除准确快照、检查点及步骤正文外，须经 A spool 端口按准确检查点读取选定源 Attempt 的输出块，证据经对象端口读取；已有准确发布的COMMAND_OUTPUT证据须核对其稳定块编号、归属、摘要/长度及实际对象，允许从永久对象读取，不依赖已合法回收的spool，逐项核对项目、运行、Step/Attempt 归属、实际字节长度与 sha256，不以记录或文件存在代替可读性。材料非空但缺相应读取端口、缺失/损坏字节或错误归属时拒绝来源读取；空的初始历史仍可展示且不产生 R。对象核对证明可读性，不证明脱敏摘要来源、断言/独立核验或当前动态环境有效。

纯聚合的 R 还须按 BC-001 0.45 消费完整资格依据；继承标记不能授予 R/V/H。实际环境/依赖核实、完整证据与脱敏来源闭包、持久选择及 D 默认接线仍开放，公开 ExecutionFacts/Schema 不变，原记录不迁移，原批次 reviewing/partial/not_run（非当前状态） 和真实 AC 不变。


### 默认输出证据同提交（1.5，2026-10-08）

默认命令进度/采集保存除spool和检查点外，经CORE-001 1.62将准确输出对象、EvidenceRef及对应快照可靠保存；D不能把completed/完整spool当成永久证据已发布。复用同一投影函数，保留现有核验、来源缺口和历史证据，不计算业务通过/证据等级。可靠终态重传只核对准确引用及永久对象，材料齐全不依赖spool、不新增提交。旧未发布材料只补原证据及历史投影，不自动重执行、不更换当前Attempt；准确历史正文/仓储修订缺证明保持阻塞。完整脱敏来源、业务核验和真实AC继续，公开DTO/Schema不变。

1.6 永久脱敏摘要投影（2026-10-08）：准确execution_redaction_summary@1的冻结正文与对象遵循CORE-001 1.65；默认收集核心在同一工作单元将实际摘要投影到EvidenceFact，不能只放摘要号占位。来源不可核对与统计不完整为不同缺口；后者保留真实unknown/gap/partial和原原因。终态重传对拍完整EvidenceFact（含摘要），缺投影时仅补原材料；历史Attempt不成为当前。整用例历史来源消费时，承诺已保存摘要的命令输出须逐项核对固定@1、准确归属/块依据、对象字节与冻结inline摘要；旧缺证明材料保留缺口，不能以临时目录或后来最新摘要替代。DTO/Schema及复用资格口径不变，真实AC未验证。

1.7 默认命令引用来源（2026-10-08）：CORE-001 1.67要求冻结EvidenceFact与准确evidence_ref仓储正文逐项核对，摘要号与原检查点块核对；实际对象和永久摘要单独校验。不使用最新引用，也不凭inline标记证明完整资格。正文与仓储对齐仅沿1.62默认命令合同，旧/其他类别缺证明继续保留缺口。准确材料可读仍不授予本轮R/V，不改变DTO/Schema。

1.8 历史运行修订来源（2026-10-08）：整Case读取沿CORE-001 1.68共用冻结计划/范围/初始用例材料，再核对准确run_plan_revision链派生的有效正文与实际步骤映射。无链的新内容/driver、伪造标签、缺失/错位的初始正文或修订链拒绝；合法修订与原历史快照独立回读。不得以可读来源代替当前动态资格、保存选择或R/V；公开ExecutionFacts/Schema不变，原批次 reviewing/partial/not_run（非当前状态）与真实对拍边界保持。

1.9 当前复用核对预览（2026-10-08）：inspect_case_reuse输入/输出及缺口口径唯一引用CORE-001 1.69；准确源/目标快照、两侧冻结身份、完整逻辑位置/ID核对后的物理Step/源Attempt映射可供D展示。状态只为incompatible/unverified；当前业务动态状态、依赖、加载、确认/核验及证据资格缺证明时保持缺口，不得把摘要一致或可读历史计入R/V，不生成空Attempt。目标任何已建Attempt、源用例依据变化或读中漂移继续按1.69守卫。该预览无保存选择/授予资格语义，D消费与完整持久选择及真实对拍继续；ExecutionFacts/Schema不变，原批次 reviewing/partial/not_run（非当前状态）保持。

1.10 复用依据确认预览（2026-10-08）：准确受控确认消费及aitest.case-reuse-inspection/1.1字段唯一引用CORE-001 1.71。D可展示两侧派生确认状态及匹配准确引用；准备/冻结confirmed标签不能作为依据，旧无受控来源仍待核实。已确认仍不代表独立核验、业务通过或R/V；公开ExecutionFacts/Schema不变，完整选择/D消费和真实对拍继续，原批次 reviewing/partial/not_run（非当前状态）保持。

1.11 stdio MCP只读核对（2026-10-08）：aitest_inspect_case_reuse沿用CORE-001 1.72所选项目/绑定、准确引用及漂移/待核实守卫，原样转交核心核对DTO，不产生新的ExecutionFacts、R/V或选择。D完整资格/选择消费及真实宿主对拍继续；公开DTO/生成Schema及原批次 reviewing/partial/not_run（非当前状态）不变。

1.12 历史规范独立查询来源（2026-10-08）：按CORE-001 1.73共同只读材料派生核对business-query-*的准入、准确原/结果快照、对象与证据引用；inline核验及证据须与原材料一致，源码身份绑定准确准备，全部引证属于原Attempt。缺证明/错观察/错误归属阻塞该来源，不以可读对象授予R/V；不重新查询或改写历史。公开DTO/生成Schema、FR/AC和原批次 reviewing/partial/not_run（非当前状态）保持，完整资格/选择及D实际消费继续。

1.13 独立查询轮询材料（2026-10-08）：按CORE-001 1.74冻结毫秒策略及同一总截止，1.1安全对象保留实际逐次耗时/JSON/错误，落盘前过滤所有样本。VerificationFact沿原DTO派生，明确不匹配/查询错误不自动重试，迟到成功和截止材料不足保持未确认；D不得将deadline_reached解释为业务回滚。旧1.0不补造轮询，当前资格/完整选择及真实消费另验，公开Schema与原批次 reviewing/partial/not_run（非当前状态）保持。

1.14 HTTP独立核验端口（2026-10-08）：CORE-001 1.75实现同一截止下的独立GET及完整同对象JSON，供既有1.74轮询/安全保存派生VerificationFact。错误目标/对象/截断/异常状态不产生matched；404为未见对象，截止不证明回滚。D仍只消费共同核心准确材料，URL/凭据正文不扩入DTO/Schema或上传。默认配置/其他核验/当前资格及真实验收继续，原批次 reviewing/partial/not_run（非当前状态）保持。

1.15 HTTP单次兼容（2026-10-08）：CORE-0011.76使现有immediate核验可调用生产HTTP reader，单次实际总截止且不重试，轮询绝对截止不变。单次超时保留query_error、不产生轮询时间线；既有保存及重启按1.0原材料回读。D仅消费共同核心准确事实，公开Schema及原批次 reviewing/partial/not_run（非当前状态）保持。

1.17 来源核验期望侧语义澄清（2026-10-09，**非破坏性**）：按 [DEC-010](../../归档/裁定/DEC-010-来源核验期望摘要字段命名与口径.md) 裁定，SourceVerificationFact.expected_source_binding_digest 的**字段名与 aitest.execution-facts/1.0 冻结形状保持不变**；本合同澄清其含义是“本次来源核验所比对的**来源内容期望摘要**”，**不是** BC-001 第 6 节的 source_binding_digest（期望→实际路径映射摘要），二者不得混用；映射级比较归 start 准入。为消除歧义，C 侧**内部**类型（SourceCheckRequest、领域 ExecutionSourceVerification）的同名字段改名为 expected_source_content_digest，由 facts.py **显式映射**到冻结字段名；因此对外 DTO/Schema、生成 Schema 与已保存材料**均不迁移**，D 侧无需改动。FR/AC、原批次 reviewing/partial/not_run（非当前状态） 不变。

1.16复用当前材料（2026-10-08）：CORE-0011.78将同Attempt的后补核验/证据及实际消费祖先当前指针纳入旧源比对。改变为incompatible、缺材料保持unverified，不推导R/V；集合重排不改义，完整资格/选择及实际字节消费继续。D只消费共同核心准确结果，Schema及原批次 reviewing/partial/not_run（非当前状态）保持。

## 2026-10-13 后续补齐合同（76ab7d2 起点，实施与验证中）

复用报告保存准确源执行快照引用与所采用 ReuseReference，证据集合包含已核对的整用例来源引用；导出仅从报告冻结的原目标/源快照读取对应证据，不回落最新源运行。缺来源引用或材料列缺口，身份不一致或重复冲突拒绝。动态环境/目标依赖仍须可信真实观察，不从配置或路径推断。此为既有一期功能15/报告架构03冻结输入的内部伴随记录，不改变旧公开 DTO，旧无来源材料仍可读但不给完整证据。

新增协商式只读动作 `read_view`，仅广告已装配能力，`project_id` 必填。参数为有限三种投影：`view=run|panel` 必带 `run_id`；`view=delivery` 必带 `delivery_id`、可选准确 `revision` 与 `submission_id`。拒绝其他字段/组合，经同一核心 CommittedViewReader 返回既有 DTO，宿主不得重算判定或写文件。读取检查提交边界漂移，未知不伪造完整视图；旧核心未广告时显式降级，不伪装有新能力。

`select_case_reuse` 保持人工动作，统一入口及 MCP 分类均排除 relay 自签；新读取/宿主配置不授权业务写入。宿主项目/运行选择显式声明、核对请求作用域，配置缺失清空上下文。状态仍 原批次 reviewing/partial/not_run（非当前状态），原 last_verified_commit 和确认记录保持。

**2026-10-14 路径/旧制品续修（既有报告架构 03 第 5 节实现补齐）**：生成与旧意图/冻结键回读均核对 ZIP 规范相对成员、保留名、大小写/Unicode 重名及普通文件属性；清单路径与实际目录精确关联，元数据读取预算 8 MiB，仅支持 stored/deflate，未知投影策略不以当前 1.0 产物冒充。原材料/历史失败不改写；五文件 98、导出最终整文件 42 通过，修后全量见[新取证](../../../validation/p1-final-closure-20261014/README.md)。此为实现与安全预算说明，公开 DTO/FR/AC、原批次 reviewing/partial/not_run（非当前状态）、签署与 last_verified_commit 均不变。


2026-10-14 隔离续检修复（本次 fix 发布）：准入源观察随内部 resolved-execution-action/1.1 保存原始对象元数据，旧 1.0 字节形状保留；原授权核对后与启动快照/evidence_ref 同事务发布。没有原始对象或过滤改变材料时不得升为已核实；普通 DTO ExecutionFacts 1.0 不新增字段。缓存、ZIP 父子冲突等证据与原子/重启未完成检查见[候选续检](../../../validation/p1-unproven-audit-20261014/README.md)。保持 原批次 reviewing/partial/not_run（非当前状态），不写双方实测或修改旧签署。


2026-10-14 准入/实际运行来源发布边界：准入原始证据保存不等于后续业务进程加载范围已证明；当前快照保留 unverified/actual_execution_load_source_unverified，原授权准入观察不追改，mismatch/blocked/unknown 不降级。64项轻量选择通过，真实事务和实际加载仍待；公开DTO字段、接口版本/签署/not_run保持，见[准确反例](../../../validation/p1-unproven-audit-20261014/README.md#准入与实际运行来源不得混同)。

本批持久化兼容：包含实际来源策略的动作使用 `aitest.resolved-execution-action/1.2`，包含实际来源观察的采集回执使用 `aitest.execution-collection/1.1`；旧动作/回执按原版本读取，不补造来源。首次登记步骤以冻结正文摘要和期望生成唯一断言引用；核验结果仍须满足当前 Attempt、准确依据确认、证据完整和依赖有效才能进入 V。旧运行缺引用时不追溯补写。
