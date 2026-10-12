# 一期工程检查-B包

复核日期：2026-10-12；源码基线：`develop@845e697`；文档提交目标：`fix`。

本表已按实际代码重新复核，保留历史编号；“部分实现”不等于整项关闭，“待验收”不等于未写代码。项目规模、门禁结果、合同版本与验收数字统一查[项目现状](现状.md)；本轮源码清单、命令与反例见[复核证据](validation/p1-code-review-20261012/README.md)。

## 1. 主责与依据

项目与计划，主责 P1-FR01—07；牵头 P1-AC01、03、12、17、20、30、31、32。跨包协作与验收归属仍以[机器台账](../tests/acceptance/p1/status.json)及[接口总台账](接口对接/README.md)为准。

先读[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[功能唯一判定口径](项目文档/一期/功能文档/01-功能文档.md#3-判定与统计唯一口径)和一期架构分册。此表不增删 FR/AC，也不覆盖字段、枚举和恢复合同。

## 2. 当前开放条目

| 编号／复核状态 | 已实现并核对的边界 | 剩余工作与整项关闭条件 | 源码触点／涉及验收 |
| --- | --- | --- | --- |
| B-01 · 默认接通／待人工产品链 | 普通保存、依据确认、分析、模型草稿、发布/prepare、运行修订/收窄均已有注册。 | 真实受控人工会话及 C/D 保存→重启→准确回读；HUMAN_UI 测试夹具不等于真实用户确认。 | `application/usecase_registry.py；application/controlled_write.py`；FR01—07／牵头 AC |
| B-03 · 部分实现／待供应方验收 | 冻结模型策略/材料，写前过滤，意图先保存、事务外调用，响应旁录与补登记已实现。 | 完整实际依赖/选定材料映射，真实供应方失败/迟到响应/中断，关闭 AI 后人工路径；不可读旧结果不再次出站。 | `application/planning/model_orchestration.py；model_response_resolution.py；infrastructure/adapters/model.py`；AC15/17/30/32 |
| B-04 · 默认接通／待来源变化验收 | 真实 Git/plain 快照、固定内容身份、当前字节/准备漂移及 start LOAD 核对已接。 | 真实宿主中来源变化→重新准备→重新授权→实际加载；旧历史字节不得声称当前环境/业务源码一致。 | `application/project/source_analysis.py；application/planning/preparation.py`；AC03/12/13/25/30 |
| B-05 · 部分实现／待半程产品验收 | 持久运行修订与驱动收窄有默认受控入口，旧 Attempt 正文和依赖失效保留。 | 实际半程活动、暂停/失效/重启、入口重登记/授权、D 双序列呈现；预览 accepted 不能替代保存和执行许可。 | `application/planning/saved_runtime_revision.py；application/execution/runtime_revision_service.py`；AC20/21/31 |
| B-06 · 待真实环境验收 | 牵头 AC 的前置已在 status.json 刷新。 | 按机器台账逐项填真实输入、制品版本、预期/实际、证据；AC20 包含实际半程运行与 C/D 消费。不降低验收条件。 | `tests/acceptance/p1/status.json（仓库根）`；AC01/03/12/17/20/30/31/32 |
| B-13 · 部分实现／待验证投影 | 正式交付核对准确任务/草稿/固定源码/绑定和一次性来源；拒绝自述 verified_in_scope。 | 同任务/版本的新有效执行与独立核验派生验证投影，失效/跨任务证据拒绝、无执行仅自述、面板/真实宿主对拍。 | `application/project/delivery_submission.py；domain/project/deliveries.py`；FR02／AC01/21 |
| B-15 · 部分实现／待运行消费验收 | 计划、Case、确认、当前/历史快照的准确身份与锁内重评估已有实现。 | 真实活动步骤边界应用、布局/入口重登记及授权、D 消费；原验收关联集合不弱化，已完成事实不得回改。 | `domain/planning/runtime_revision.py；application/execution/runtime_revision_service.py`；AC20/24/31 |

## 3. 已修条目与验证边界

历史关闭问题保留原编号，索引见[历史修复证据](一期工程整改/03-修复证据/ABC包问题修复证据清单-2026-10-03.md)。

本轮没有修改产品源码或代填 `verified`。规则/合同/真实文件与进程组件通过，只证明各自测试边界；实际宿主、供应方/业务对象、物理掉电和完整 AC 需单独记录输入、软件/制品版本、预期、实际及证据位置。执行后可靠保存材料再发布结果，未知副作用不重放。

续接顺序见[整改计划](一期工程整改/02-整改计划/ABC剩余问题整改计划-2026-10-04.md)和[交接](一期工程整改/04-交接/交接说明-2026-10-12.md)。清理前原文及完成条件可用 `git show 845e697:docs/一期工程检查-B包.md` 恢复，完整 SHA-256 见[原文清单](validation/p1-code-review-20261012/documents-before.json)。
