# 一期工程检查-D包

复核日期：2026-10-13；源码基线 `fix@a51c2b4`；文档推送 `fix`。数量/门禁/合同版本/验收只读[项目现状](现状.md)。判定、报告、问题与入口，主责 FR14/15/17。 FR/AC 完整映射和真实结果仍遵守[机器台账](../tests/acceptance/p1/status.json)与[一期主责分册](项目文档/阅读索引.md)，本表不另定语义。

## 1. 当前开放条目

| 编号／状态 | 已实现/已核对 | 剩余工作与关闭条件 |
| --- | --- | --- |
| D-02 · 生产者与报告已接／准确断言消费未闭合 | assertion_outcomes_from_facts/build_decision_facts/S/M/H、生产报告/live_decision 已有。 | C-17 R/P/V/M消费、D-13 有效确认/运行修订依据；断言对应的准确独立预期/观察仍需完整权威证明。 |
| D-04 · 生产持久入口已接／权威关闭与冻结仍有缺陷 | 问题/修复/安排回归/关闭、报告/复核/导出/简报已有 UOW/意图/索引/真实制品链。 | D-11 关闭结果不能自报，D-12 全阻塞集合，D-14 导出原意图/冻结来源；真实业务恢复验收未完。 |
| D-05 · DTO/查询配对已接／全同源对拍未完 | Run/Panel/Delivery DTO、S/M/H、issues/reports 有限查询及 commit_id/event_cursor 已有。 | 整个视图的来源/确认/问题/R 需固定准确同提交，不能只配一对元数据；D-12/D-13/C-17 仍开放。 |
| D-06 · 只读业务组件已接／实际宿主用户流未接 | 面板问题/报告/简报/详情/事件续读及核心 DTO 接缝已有。 | 扩展未送 projectContext 或接 view_queries；计划/授权/工作台/设置、原因主动作与实际配对订阅未闭合。 |
| D-07 · 待真实产品验收 | 浏览器组件与可携带包实现/测试已有。 | 真实 Trae全业务/生命周期、七类人工操作、完整同源统计/主动作/键盘/历史/跨机导出 AC 未验收。 |
| D-11 · 新增已复现／未修 | 代表保存事实的 ExecutionFacts 夹具中 VerificationFact.observation=mismatched，调用方 assertion_outcome=passed 仍派生 satisfies_original_criteria=True，领域关闭为 closed；未核对实际修复版本/原标准。 | 从原问题/新实际回归/同对象断言核验及准确标准修订派生，不能用用户传入结果覆盖已知失败；关闭和失败重开同源保存。 触点：application/review/saved_defects.py::_closure_evidence；AC11/14/33 |
| D-12 · 新增已复现／未修 | 已保存同范围 P1 问题，_issues 只取请求 issue_ids；省略或空集合返回零问题。live_decision 默认 issue_ids=()，会漏掉应阻塞的问题。 | 在固定提交边界按验收范围/模块/用例派生完整有效阻塞集合，归并主问题重开及最强级别按唯一领域规则；不接受调用方白名单规避。 触点：application/review/saved_reports.py::_issues/_live；AC14/27/34 |
| D-13 · 新增已复现／未修 | SavedCaseBasisCheck 先读冻结 PreparedRun.assertion_bases 的标签；present_unconfirmed 直接 False，不读取后补准确确认；运行修订依据也只取原准备。 | 按准确 Case/依据修订与已保存有效 ConfirmationRecord 计算 effective 状态，并核对实际核验证据；不改冻结 Case/Plan/旧报告。 触点：application/review/authoritative_basis.py::__call__/_entry；AC20/21/27 |
| D-14 · 新增已复现／未修 | 报告 @1 导出成功后生成 @2，完全相同 export intent/参数重传却 INTENT_CONFLICT；回读在取最新报告并重新派生键之后。 | 先读取原意图及冻结报告/复核/附件/策略，再核对原制品返回；新输入才冲突。既有键复用也要保存本 intent，证据取冻结来源而非任意当前 run。 触点：application/review/saved_exports.py::export_report/_recall；AC16/19/29/35 |

## 2. 已修与验证边界

D-08/D-09/D-10 原代码反例已修；本次 D-11—14 为不同边界的新发现。 保留历史编号/记录，不将已修代码反例重编号，不代填真实验收。

本轮只审查/取证/修文档，产品代码未改。新反例、改动路径专项/静态结果和明确夹具边界见[复核证据](validation/p1-fix-review-20261013/README.md)，全部 17 FR/35 AC 当前对照见[现状检查](一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)。真实 Trae/业务对象/供应方故障/掉电与完整 AC 另验，完整套件未结束不能记通过。

原文可从 `git show a51c2b4:docs/一期工程检查-D包.md` 恢复，逐文件 SHA-256 与本机可恢复备份见[修改前清单](validation/p1-fix-review-20261013/documents-before.json)。实施次序读[整改计划](一期工程整改/02-整改计划/ABC剩余问题整改计划-2026-10-04.md)，跨包状态读[接口台账](接口对接/README.md)。
