# 一期工程检查-C包

复核日期：2026-10-12；源码基线：`develop@845e697`；文档提交目标：`fix`。

本表已按实际代码重新复核，保留历史编号；“部分实现”不等于整项关闭，“待验收”不等于未写代码。项目规模、门禁结果、合同版本与验收数字统一查[项目现状](现状.md)；本轮源码清单、命令与反例见[复核证据](validation/p1-code-review-20261012/README.md)。

## 1. 主责与依据

执行与证据，主责 P1-FR08—13；牵头 P1-AC04—09、13、19、24、25。跨包协作与验收归属仍以[机器台账](../tests/acceptance/p1/status.json)及[接口总台账](接口对接/README.md)为准。

先读[阅读索引](项目文档/阅读索引.md)、[一期需求](项目文档/一期/需求文档/01-需求文档.md)、[功能唯一判定口径](项目文档/一期/功能文档/01-功能文档.md#3-判定与统计唯一口径)和一期架构分册。此表不增删 FR/AC，也不覆盖字段、枚举和恢复合同。

## 2. 当前开放条目

| 编号／复核状态 | 已实现并核对的边界 | 剩余工作与整项关闭条件 | 源码触点／涉及验收 |
| --- | --- | --- | --- |
| C-01 · 部分实现／复用保存缺口 | 新 Attempt 撤销旧复用，实际消费旧上游的下游失效，准确复用检查/字节核对已有实现。 | 完整当前 R 资格及选择持久化、实际消费来源核对；新失败/取消/阻塞不能恢复旧 R；E/R/V 不能拼跨轮部分执行。 | `application/execution/reuse_inspection.py；reuse_sources.py；domain/execution/cases.py`；AC19/20/24 |
| C-02 · 默认接通／待真实授权 | 原许可/挑战、占用、intent、检查点、当前引用及定向失效同提交，未知执行禁止重放。 | 真实宿主每动作授权、两入口同意图仅一次 start、范围变化撤销/重新授权、业务核验及复用；事务外实际执行。 | `application/execution/authorization.py；commit.py；runner.py`；AC19/24/31 |
| C-03 · 部分实现／缺默认业务核验端口 | 命令/HTTP/Agent/人工/外部导入适配器及持久独立查询已有组件；verify_pending 已注册。 | bootstrap 默认 business_verification_resolver/port 仍为 None；五类生产配置、同对象独立查询/最终一致性、Mock/混合/未知、人工与请求日志关联待完整接入/实测。 | `bootstrap.py；application/evidence/saved_verification.py；infrastructure/adapters/execution/`；FR08—13／AC04—09/24/25 |
| C-04 · 组件已注入／来源核验已生效 | 入口/解释器/来源 provider 默认注入，实际 Job/进程出生身份及恢复抢救有实现；C-15/C-16 修复后默认链的来源核验真实生效（语法/逐模块/解释器/原始输出）。 | 真实 Trae 退出、跨进程强杀、实际 PYTHONPATH/editable 加载和掉电；失联先 inspect/collect，不能按 PID 猜归属。 | `application/execution/source_checks.py；infrastructure/adapters/execution/command.py`；AC13/19/25 |
| C-05 · 部分实现／复用与 D 消费未闭合 | 命令永久对象/脱敏摘要、外部导入附件、独立核验、准确检查点及快照已有保存链。 | 整用例复用引用尚无保存动作，D 报告/问题尚未消费生产事实；所有类型按准确修订/字节核对，缺材料不发布完整结果。 | `application/execution/commit.py；contracts/reuse_reference.py；application/evidence/publication.py`；AC07/09/21/24/27/28 |
| C-06 · 部分实现／待半程与宿主验收 | 暂停/恢复/取消、修订/收窄已接持久意图和原句柄。 | retry_step/attach_evidence/return_stage 无默认 handler；实际半程应用、补证/退回、失败分支/停止未确认/未知副作用、真实宿主保活。 | `application/execution/saved_control.py；runtime_actions.py；interfaces/local/core_worker.py`；C 牵头 AC／AC20/31/34 |
| C-12 · 默认装配存在／端到端已贯通至受控动作 | 解析器、冻结截止（改从权威环境记录读取）、物化摘要、来源准入、RegisteredEntryExecutionAdapter 与有界续行组件已注入；真实材料现可经默认解析器完成执行准备并保存受控动作。 | 实际未知控制处置、业务执行截止/切片后续行、换核心/宿主恢复；业务端口缺失显式阻塞，副作用未知不自动重放；真实 start 业务执行与重启回读未验收。 | `application/execution/action_resolver.py；step_execution.py；run_schedule.py`；FR09/10／AC19/23/34 |
| C-15 · **已修（2026-10-12）**／待真实验收 | 默认解析器改为经权威业务记录（`verify_saved` 重新核对固定 blob）取得技术 `pinned_snapshot_id`；`ExecutionRequest.materialized_snapshot_ref` 改填**实际固定 workdir**；provider 读取合同的 `snapshot.source_snapshot_id` + `record_revision`。真实核心/UOW/固定字节/物化 + 未替换默认解析器的 `prepare_execution` 已贯通并保存受控动作。 | 同族新暴露缺陷一并对齐：冻结步骤截止从**权威环境记录**读取（投影不带该字段，旧默认链必在"未冻结的截止"处阻塞）；测试夹具的 `cwd_mapping` 改用裁定 1A 的 `workdir:<相对路径>` 语法。仍欠：真实 Trae 端到端执行与重启回读、editable/PYTHONPATH 实际对拍。 | `bootstrap.py；action_resolver.py；start_materialization.py`；FR08—11／AC13/19/25/30；[修复证据](一期工程整改/03-修复证据/2026-10-12-C15-C16-D10-A19修复证据.md) |
| C-16 · **已修（2026-10-12）**／待真实验收 | 来源核验在**登记解释器**下逐个 `.py` 实际 `compile`（语法错即 mismatch + `source_syntax_error:<path>`）；逐**全部登记模块**核对实际加载文件/字节/失败分类（命名空间包按"无文件且无错误"处理，越界/摘要不符即 mismatch）；实际解释器取探针自报 realpath 与冻结值比对；探针原始输出按内容寻址永久保存为证据引用。三组已复现反例（平铺语法错入口、好坏双包、命名空间坏子文件）均不再 verified。 | 仍欠：`APPLICABLE_BUILD`／显式 `MINIMAL_START`（合同字段来源未冻结，仍未实现）；真实 Trae 来源对拍与 physical 掉电。 | `bootstrap.py；infrastructure/adapters/execution/python_checks.py；application/execution/source_checks.py`；FR08／AC04/13/23/25/30 |

## 3. 已修条目与验证边界

本次全源码完成性复核新增 C-15/C-16，更正“默认注入即端到端接通”。真实默认链与兼容替身下游反例分开保存，见[完整对照](一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)和[脚本/原输出](validation/p1-source-completion-20261012/README.md)；既有绿测试不能关闭这两项。

历史关闭问题保留原编号，索引见[历史修复证据](一期工程整改/03-修复证据/ABC包问题修复证据清单-2026-10-03.md)。

本轮没有修改产品源码或代填 `verified`。规则/合同/真实文件与进程组件通过，只证明各自测试边界；实际宿主、供应方/业务对象、物理掉电和完整 AC 需单独记录输入、软件/制品版本、预期、实际及证据位置。执行后可靠保存材料再发布结果，未知副作用不重放。

续接顺序见[整改计划](一期工程整改/02-整改计划/ABC剩余问题整改计划-2026-10-04.md)和[交接](一期工程整改/04-交接/交接说明-2026-10-12.md)。清理前原文及完成条件可用 `git show 845e697:docs/一期工程检查-C包.md` 恢复，完整 SHA-256 见[原文清单](validation/p1-code-review-20261012/documents-before.json)。
