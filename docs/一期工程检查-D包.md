# 一期工程检查-D包

复核日期：2026-10-12；源码基线：`develop@845e697`；文档提交目标：`fix`。

本表已按实际代码重新复核，保留历史编号；“部分实现”不等于整项关闭，“待验收”不等于未写代码。项目规模、门禁结果、合同版本与验收数字统一查[项目现状](现状.md)；本轮源码清单、命令与反例见[复核证据](validation/p1-code-review-20261012/README.md)。

## 1. 主责与依据

判定、报告与入口，主责 P1-FR14/15/17；牵头 P1-AC02、10、11、14—16、18、21—23、26、27、29、33—35。跨包协作与验收归属仍以[机器台账](../tests/acceptance/p1/status.json)及[接口总台账](接口对接/README.md)为准。

先读[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[功能唯一判定口径](项目文档/一期/功能文档/01-功能文档.md#3-判定与统计唯一口径)和一期架构分册。此表不增删 FR/AC，也不覆盖字段、枚举和恢复合同。

## 2. 当前开放条目

| 编号／复核状态 | 已实现并核对的边界 | 剩余工作与整项关闭条件 | 源码触点／涉及验收 |
| --- | --- | --- | --- |
| D-02 · 适配器已实现／生产发布未接 | build_decision_facts 已消费 ExecutionFacts，合同夹具和真实文件往返测试存在；旧“适配器不存在”撤销。 | 生产快照→准确断言依据/核验→唯一 S/M/H；R 当前恒空，完整复用尚未消费；禁止把调用者有效性布尔当权威证明。先修 D-10。 | `application/review/execution_decision_adapter.py；application/execution/facts.py`；AC19/21/24/27 |
| D-04 · 明确实现缺口 | 报告/本地复核/问题生命周期仍为纯门面；exports.py 明确尚未实现。 | 权威查新的实际回归、当前 Attempt/有效核验后关闭问题；同 UOW 持久化报告/修复/复测/复核/导出，实际生成并校验安全 Markdown/portable 包，保留历史正文。 | `application/review/reporting.py；defect_management.py；infrastructure/exports.py`；AC11/14/16/29/33/35 |
| D-05 · DTO 已实现／生产同源未闭合 | DecisionDTO 含 S/M 摘要、H 引用和快照来源；CoverageDTO 仍为九平铺计数。 | 正式协议/报告/CLI/MCP/面板完整同源形状及准确修订对拍，IssueSummary 阻塞来源由权威事实派生；未知不补 0/false。 | `interfaces/dto.py；contracts/views.py；application/review/issue_queries.py`；AC21/24/27/34 |
| D-06 · 只读桥已接／业务交互缺口 | CLI dispatch/ensure-core、MCP 查询/七类保存、Trae 只读消息桥和受控挑战已有实现。 | 面板当前项目身份、计划/执行/修订/问题/报告业务动作及受控用户交互。17 项一期动作尚无默认 handler；MCP 禁止人工确认。目录/迟到响应与原意图恢复待产品验证。 | `interfaces/tools/cli.py；agent_relay.py；integrations/trae/src/core-bridge.ts（仓库根）`；AC26/31/33/34 |
| D-07 · 待真实产品验收 | 三段标题、同面板详情/返回、只读回执/能力呈现已有浏览器测试。 | 三段真实业务字段、缺口五类计数、主失败导航/复制说明、键盘、旧报告筛选/折叠/隐藏、事件重放、portable 导出与真实 Trae 生命周期。 | `resources/panel/src/index.ts；integrations/trae/src/extension.ts（仓库根）`；D 牵头 AC |
| D-10 · 新增 P1：事实适配守卫不足 | 本轮四组合法 Schema 输入反例：冲突必需步骤证据仍 required_evidence_valid=True 并 passed/A；同源 verified+unverified 仍 matched 并 passed/A；is_current=False 仍计 E。 | 按同一快照核对当前 Step/Attempt 身份、每项必需证据映射/完整性、来源适用性/冲突；缺失或未知保留 U/缺口，禁止 passed/A。增加真正拒绝错误输入的回归；当前未修。 | `application/review/execution_decision_adapter.py::_case_executed/_required_evidence_valid/_source_state`；FR14/17／AC21/24/25/27 |

## 3. 已修条目与验证边界

历史关闭问题保留原编号，索引见[历史修复证据](一期工程整改/03-修复证据/ABC包问题修复证据清单-2026-10-03.md)。

- **D-09 已关闭代码反例**：`domain/review/reports.py::_full_pass_conditions_met` 明确要求非空 S/M；`test_review_decision.py::test_empty_full_scope_fails_closed_instead_of_passing` 已有回归。显式无适用检查仍为 not_applicable，不显示 100%。
- **D-08 已关闭浏览器测试缺陷**：计划按钮已 `exact: true`，既有测试覆盖详情返回、窄屏、无宿主降级；本轮浏览器实际运行结果查复核证据。真实 Trae 仍由 D-07 跟踪。


本轮没有修改产品源码或代填 `verified`。规则/合同/真实文件与进程组件通过，只证明各自测试边界；实际宿主、供应方/业务对象、物理掉电和完整 AC 需单独记录输入、软件/制品版本、预期、实际及证据位置。执行后可靠保存材料再发布结果，未知副作用不重放。

续接顺序见[整改计划](一期工程整改/02-整改计划/ABC剩余问题整改计划-2026-10-04.md)和[交接](一期工程整改/04-交接/交接说明-2026-10-12.md)。清理前原文及完成条件可用 `git show 845e697:docs/一期工程检查-D包.md` 恢复，完整 SHA-256 见[原文清单](validation/p1-code-review-20261012/documents-before.json)。
