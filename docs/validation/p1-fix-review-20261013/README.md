# fix 最新代码复核与文档更新（2026-10-13）

基线 `a51c2b48ab5595fd74daf7ab4c99ad8efa354df2`，比较 `9c0f65a`。本轮只做代码检查、组件取证和文档维护，产品代码/规范分册/验收登记没有修改；当前完成性见[17 FR / 35 AC 对照](../../一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)，规模和状态唯一入口为[项目现状](../../现状.md)。

## 1. 检查范围与真实边界

[源码清单](source-inventory.json)逐个登记核心模块的字节哈希、行数及 AST 符号，同时列出本次改动路径。深查新增默认装配、来源准备/核验、草稿/修订/控制/补证、问题/报告/制品、R 选择/消费、维护与面板。全核心清点不等于逐行安全审计；旧未改模块的先前清点保留在[上一轮](../p1-source-completion-20261012/README.md)。

[commands.json](commands.json)及[extra-commands.json](extra-commands.json)保存准确命令、退出码、耗时和日志 SHA-256；测试范围为[本次改动的既有单元/合同文件 + 架构 + 登记](test-scope.json)，完整全量本轮未执行。Windows/CPython 3.13.0，[facts.json](facts.json)记录环境与接受登记原哈希。没有叠加旧分块成绩。

| 检查 | 本轮结果 | 边界 |
| --- | --- | --- |
| ruff / mypy / lock check | 退出 0 | 静态/锁声明检查；未另做独立干净安装 |
| 受改动路径回归 | 418 passed，0 failed/error/skipped；[JUnit](changed-paths.xml) 1278.968 秒 | 包含旧默认链/判定/合同与新增用例；测试未覆盖的反例仍存在 |
| 面板 build / Playwright | 退出 0 / 10 passed；[JUnit](panel.xml) | 模拟 host/projectContext，非实际 Trae |
| Schema / 版本核对 | 退出 0，生成物无 diff | 不表示业务或真实 AC 通过 |
| 全套 collect | 退出 0 | 只收集，不执行全量 |
| 新边界探针 | 退出 0、7 项缺陷成立 | 原输出/替身范围见下节；退出 0 不是修复通过 |
| 最终文档检查 | [最终检查命令](final-checks.json)与[链接/编号/元数据检查](document-checks.json) | 不降低业务验收范围 |

## 2. 新反例、替身与关闭条件

[boundary-counterexamples.py](boundary-counterexamples.py)是只读/独立诊断脚本，不属于产品测试通过证明；在独立 `.runtime` 工作空间构造，未改默认产品实现。结果见[boundary-counterexamples.json](boundary-counterexamples.json)，实际执行见[探针命令](probe-command.json)与[原日志](probe-run.log)，完整触点/关联 AC/关闭条件见[findings.json](findings.json)。

| 编号 | 实际观察 | 证据边界 |
| --- | --- | --- |
| A-20 | maintenance_active 下 save_rules 被拦，import_rules 进入 handler | 实际 LocalAPI 闸门；无害 marker handler，没有改业务文件 |
| C-17 | 非当前 Attempt/无关 no_result 核验仍计 R；物化路径错比内容身份；R 未带入 V/P | 严格 ExecutionFacts；来源/材料/依据 reader 是组件夹具，非真实完整业务运行 |
| C-18 | 选择已 @1，新意图 expected_revision=0 仍追加 @2 | 真实默认 UOW/仓储；核对器和受控身份为明确夹具 |
| D-11 | 保存事实夹具 mismatched + 调用方 passed 仍得到关闭条件 true/closed | 生产关闭派生与领域关闭、真实引用仓储；执行/依据/对象为组件夹具，不是用户 AC |
| D-12 | 保存 P1 问题后，省略 issue_ids 得到空集合，包含才 blocking | 生产 _issues 方法与真实 issue 仓储；没有声称真实完整报告已经 passed |
| D-13 | 冻结 present_unconfirmed 分支直接 false，确认 reader 未调用 | 静态路径 + 分支探针/reader mock；不冒充真实人工后补确认端到端验收 |
| D-14 | 导出 @1 后报告变 @2，原意图原参数回传却 INTENT_CONFLICT | 生产服务、真实 UOW/对象/制品/投影；准备和执行来自明确夹具 |

探针使用当前产品函数/服务及项目既有 fixture 帮助器；D-14 只确认原导出意图重传漂移，源码另显示新意图引用旧制品/当前 run 取附件的冻结风险，不能把静态风险写成已全部复现。七项都未修产品代码，不计入“验收通过”。

## 3. 原反例与新增实现

C-15/C-16/A-19/D-10 原代码反例已有修复及拒绝型回归，本轮既有路径仍通过；原失败和干净安装日志保留在[修复轮证据](../p1-defect-fix-20261012/README.md)。新增草稿/控制/补证/维护/生产报告问题制品/选择与只读 DTO 确实存在，不能继续按旧“无默认入口/R 恒空/D 无生产者”维护文档。新增边界未闭环，默认业务核验缺配置，真实 Trae 用户流程和完整恢复/跨包对拍仍未验收。

## 4. 文档维护与恢复

[documents-before.json](documents-before.json)列出修改前字节哈希和恢复基线，本机备份为 `.runtime/p1-review-docs-before-a51c2b4.zip`；跨机器使用 `git show a51c2b4:<记录的原路径>` 恢复。原带日期的实施/确认/测试记录保留原结果，仅加最新复核入口及影响说明，未以删除历史失败掩盖缺口。

[interface-changes.json](interface-changes.json)记录六主题状态版本增量；保持 reviewing/partial/not_run 和原 last_verified_commit，没有代签或迁移数据。CORE 1.82 已明确 expected_revision，C-18 是实现不遵守；正文引用的 DEC-014 独立裁定记录未找到，不伪造批准。BD 的只读消费从 not_started 修正为 partial，实际宿主接线仍待补。

所有整改 Markdown 的维护用途见[目录复核](document-audit.json)与[变更记录](../../一期工程整改/文档清理与逐项变更记录-2026-10-13.md)。保留已有修复证据和历史记录，无需再次删除；旧阶段待办/状态已加边界或由当前索引替代。验收台账 SHA-256 不变，35 项未新增真实通过，字段/scenario/expected/FR 映射原样保留。

首轮本地链接检查发现 12 处既有断链，已改为准确相对路径；另 1 处为首轮结果文件尚未生成。首检原输出保留在[首检记录](document-checks-initial.json)，最终重检结果见 document-checks.json，没有修改原测试结果。

暂存空白门禁首检因 collect.log 末尾多余空行失败，见[原检查](staged-diff-initial.log)。可读收集日志只去掉末尾多余空行，原 stdout/stderr 字节完整保存在[压缩原输出](collect.raw.gz)，原/可读哈希同时记入 extra-commands.json；重新检查记录在 final-checks.json。
