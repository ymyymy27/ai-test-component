# 一期源码详细复核与文档收敛（2026-10-12）

源码基线：`develop@845e6978d522def651d5fec5a0b046126ffaaadd`；文档交付分支 `fix`。本轮更新检查/整改文档及文档事实核对脚本，没有改产品源码、生成 Schema 或验收结果。

## 1. 范围与版本

本机 Windows、CPython 3.13.0、uv 0.11.8。首次 --locked 干净安装失败；诊断使用 --frozen 安装既有版本（pydantic 2.13.5、pytest 9.1.1、mypy 1.20.2、ruff 0.16.6），不可将它写成 --locked 成功。

静态枚举全部源码与测试及 AST 符号，按主责合同详细核对默认装配、A 提交/恢复/查询/安全、B 冻结/模型/修订、C 授权/执行/证据/复用、D 聚合/报告/问题/DTO/入口调用链。**没有声明逐行审计全部文件或实际跑遍真实宿主/供应方**。[inventory.json](inventory.json) 为准确源码/测试的 SHA-256、行数及符号位置；[actions.json](actions.json) 是真实临时工作空间装配库存/缺省端口事实。

## 2. 源码核对触点

行号仅对应本轮基线；文件原始 SHA-256 和完整符号表在 inventory.json，后续变动不能沿用行号/原测试结果。

| 源码／符号／行号 | 核对结论 |
| --- | --- |
| [src/aitest/bootstrap.py](../../../src/aitest/bootstrap.py)：`assemble_workspace_core`，L583 | 默认解析器/端口、核验缺依赖和 handler 装配 |
| [src/aitest/bootstrap.py](../../../src/aitest/bootstrap.py)：`_source_verification_provider`，L412 | 准确固定源码重核与 LOAD/SYNTAX 来源 |
| [src/aitest/application/execution/action_resolver.py](../../../src/aitest/application/execution/action_resolver.py)：`resolve`，L90 | 冻结绑定/环境/来源/尝试构造 |
| [src/aitest/application/execution/start_materialization.py](../../../src/aitest/application/execution/start_materialization.py)：`materialize`，L42 | 物化清单与映射摘要准入 |
| [src/aitest/application/execution/authorization.py](../../../src/aitest/application/execution/authorization.py)：`prepare`，L413 | 原授权/准确输入/冻结截止准入 |
| [src/aitest/application/execution/commit.py](../../../src/aitest/application/execution/commit.py)：`ExecutionCommitCoordinator`，L176 | 同提交当前引用/证据/依赖与意图 |
| [src/aitest/application/execution/run_schedule.py](../../../src/aitest/application/execution/run_schedule.py)：`SavedRunSchedule`，L74 | 有界串行续行与活动控制 |
| [src/aitest/application/evidence/saved_verification.py](../../../src/aitest/application/evidence/saved_verification.py)：`_claim`，L96 | 默认缺 resolver/verifier，不自造业务核验 |
| [src/aitest/application/execution/reuse_inspection.py](../../../src/aitest/application/execution/reuse_inspection.py)：`CaseReuseInspection`，L43 | 只读资格/材料检查不授予 R |
| [src/aitest/application/project/delivery_submission.py](../../../src/aitest/application/project/delivery_submission.py)：`submission_content`，L30 | 提测的准确任务/源码/来源与自述分离 |
| [src/aitest/application/planning/model_orchestration.py](../../../src/aitest/application/planning/model_orchestration.py)：`request_model_draft`，L450 | 冻结材料/模型出站与原意图恢复 |
| [src/aitest/application/review/execution_decision_adapter.py](../../../src/aitest/application/review/execution_decision_adapter.py)：`_case_executed`，L175 | D-10：E 漏当前性检查 |
| [src/aitest/application/review/execution_decision_adapter.py](../../../src/aitest/application/review/execution_decision_adapter.py)：`_required_evidence_valid`，L212 | D-10：仅有引用，不完整证据仍标有效 |
| [src/aitest/application/review/execution_decision_adapter.py](../../../src/aitest/application/review/execution_decision_adapter.py)：`_source_state`，L85 | D-10：任何 verified 即 matched，未核适用/冲突 |
| [src/aitest/application/review/execution_decision_adapter.py](../../../src/aitest/application/review/execution_decision_adapter.py)：`build_decision_facts`，L241 | 适配器已存在，R 恒空，外部断言事实尚待权威派生 |
| [src/aitest/domain/review/reports.py](../../../src/aitest/domain/review/reports.py)：`_full_pass_conditions_met`，L459 | D-09：非空 S/M 门禁已修 |
| [src/aitest/application/review/reporting.py](../../../src/aitest/application/review/reporting.py)：`create_report`，L24 | 纯报告门面无 UOW 保存 |
| [src/aitest/application/review/defect_management.py](../../../src/aitest/application/review/defect_management.py)：`close_issue`，L107 | 关闭依据为参数，未接权威查询/持久提交 |
| [src/aitest/interfaces/dto.py](../../../src/aitest/interfaces/dto.py)：`DecisionDTO`，L137 | S/M/H 与来源投影已有定义 |
| [src/aitest/infrastructure/file_store/canonical_manifest.py](../../../src/aitest/infrastructure/file_store/canonical_manifest.py)：`validate_complete`，L27 | 清单 v2/完整引用与别名逐项校验 |
| [src/aitest/infrastructure/file_store/publication_backend.py](../../../src/aitest/infrastructure/file_store/publication_backend.py)：`FilePublicationBackend`，L146 | Windows 发布/不确定状态守卫 |
| [src/aitest/interfaces/tools/agent_relay.py](../../../src/aitest/interfaces/tools/agent_relay.py)：`McpRelay`，L56 | 同核心冻结绑定和七类普通保存 |

`infrastructure/exports.py` 的全部正文明确“尚未实现，不注册为可用能力”；扩展 `extension.ts/core-bridge.ts` 为只读白名单桥，面板业务操作未接。底座/生成 Schema/构建物存在不等于其业务已验收。

## 3. 新反例与尚未修复

**A-19（工程阻断）**：uv.lock 的 requires-python 为 `==3.13.*`，pyproject.toml 为 `>=3.11,<3.14`。命令 `uv sync --extra dev --locked --python 3.13` 退出 1，原文明确需要更新锁文件；见 locked-install.log。不手改锁文件，本轮只登记任务。

**D-10（适配边界 P1）**：[decision-counterexamples.py](decision-counterexamples.py) 从既有 CD-001 success.json 取对照（没有改原夹具），三个最小变体均被 ExecutionFacts Schema 接受，输出见[counterexamples.json](counterexamples.json)：

| 输入 | 期望的保守边界 | 实际 |
| --- | --- | --- |
| 对照，完整当前执行/来源/证据和明确断言 | 允许通过 | passed/A |
| 增加步骤必需证据声明，将该步骤 stderr 证据完整性改 conflict | 必需材料不完整不能标 required_evidence_valid=True 并通过 | 仍 valid=True、E/V 含 case-1、passed/A |
| 同源/同计划增加 unverified 来源事实 | 必须先核适用/时序及准确来源，不能只取任意 verified | 仍 matched、passed/A |
| 当前映射仍指 Attempt，但 is_current=False | 矛盾输入拒绝或不计 E | E 仍含 case-1（V 为空，incomplete/B） |

脚本退出 0 是完成取证；它没有断言“这些错误已被拒绝”，因此不是通过的修复测试。D 适配器尚未接生产报告，本轮没有宣称真实报告已遭错误升级；生产接入前应先修此边界并补拒绝型回归。

**旧问题复核**：D-09 非空 S/M 门禁和既有空 full 回归已通过；D-08 精确计划按钮定位、返回/窄屏的浏览器既有测试本轮 2 passed。真实宿主条件仍 D-07 开放。

## 4. 本轮实际命令与结果

每条完整 argv、退出码与时长见[results.json](results.json)，命令按时间记录；重跑使用不同日志名，初次差异没有覆盖为成功。

| 批次 | 退出码 | 秒 | 日志／解释 |
| --- | --- | --- | --- |
| versions | 0 | 0.09 | [versions.log](versions.log)；实际退出码，仅证明该命令范围 |
| ruff | 0 | 1.26 | [ruff.log](ruff.log)；实际退出码，仅证明该命令范围 |
| mypy | 0 | 6.11 | [mypy.log](mypy.log)；实际退出码，仅证明该命令范围 |
| schemas | 0 | 0.64 | [schemas.log](schemas.log)；实际退出码，仅证明该命令范围 |
| collect | 0 | 8.19 | [collect.log](collect.log)；实际退出码，仅证明该命令范围 |
| targeted | 0 | 13.16 | [targeted.log](targeted.log)；426 passed / 1 skipped；symlink 权限条件跳过 |
| full | 4294967295 | 939.95 | [full.log](full.log)；939.95 秒后约 18% 主动中止，无最终统计/JUnit，不能写通过 |
| panel-install | 0 | 14.43 | [panel-install.log](panel-install.log)；实际退出码，仅证明该命令范围 |
| panel-build | 0 | 2.77 | [panel-build.log](panel-build.log)；实际退出码，仅证明该命令范围 |
| panel-test | 0 | 6.24 | [panel-test.log](panel-test.log)；实际退出码，仅证明该命令范围 |
| trae-install | 0 | 11.77 | [trae-install.log](trae-install.log)；实际退出码，仅证明该命令范围 |
| trae-package | 0 | 30.35 | [trae-package.log](trae-package.log)；实际退出码，仅证明该命令范围 |
| wheel-build | 0 | 14.99 | [wheel-build.log](wheel-build.log)；实际退出码，仅证明该命令范围 |
| artifacts | 0 | 0.13 | [artifacts.log](artifacts.log)；实际退出码，仅证明该命令范围 |
| wheel-smoke | 0 | 6.86 | [wheel-smoke.log](wheel-smoke.log)；实际退出码，仅证明该命令范围 |
| counterexamples | 0 | 0.37 | [counterexamples.log](counterexamples.log)；反例脚本完成；退出 0 表示复现，不表示产品修复 |
| locked-install | 1 | 0.82 | [locked-install.log](locked-install.log)；A-19 的失败复现，未修 |
| source-diff | 0 | 0.05 | [source-diff.log](source-diff.log)；实际退出码，仅证明该命令范围 |
| schema-diff | 0 | 0.05 | [schema-diff.log](schema-diff.log)；实际退出码，仅证明该命令范围 |
| docs-facts | 1 | 5.13 | [docs-facts.log](docs-facts.log)；初次检测：mypy 文档写首次 230，第二次实际 232；最终修正后另跑 |
| tooling-ruff | 0 | 0.13 | [tooling-ruff.log](tooling-ruff.log)；实际退出码，仅证明该命令范围 |
| counterexamples-recheck | 0 | 0.34 | [counterexamples-recheck.log](counterexamples-recheck.log)；反例脚本完成；退出 0 表示复现，不表示产品修复 |
| locked-install-recheck | 1 | 0.09 | [locked-install-recheck.log](locked-install-recheck.log)；A-19 的失败复现，未修 |
| source-diff-recheck | 0 | 0.06 | [source-diff-recheck.log](source-diff-recheck.log)；实际退出码，仅证明该命令范围 |
| schema-diff-recheck | 0 | 0.05 | [schema-diff-recheck.log](schema-diff-recheck.log)；实际退出码，仅证明该命令范围 |
| docs-facts-recheck | 0 | 5.06 | [docs-facts-recheck.log](docs-facts-recheck.log)；实际退出码，仅证明该命令范围 |
| tooling-ruff-recheck | 0 | 0.13 | [tooling-ruff-recheck.log](tooling-ruff-recheck.log)；实际退出码，仅证明该命令范围 |
| mypy-final | 0 | 0.41 | [mypy-final.log](mypy-final.log)；实际退出码，仅证明该命令范围 |

专项 JUnit：[targeted.xml](targeted.xml)。完整 Python 运行中止，没有最终汇总和 full.xml，不能由部分绿色点或历史分块推算为通过。[source-diff.log](source-diff.log) 与 Git 对比证明 c9e5807→845e697 的产品源码、测试、工程/锁文件和扩展没有变化；[历史全量](../p1-fullsuite-20261012/README.md)仅作为原版本原机器的证据引用。本轮完整运行记录不冒充它。

面板 build/2 条 Playwright、VSIX package、wheel build、verify_artifacts 和隔离 wheel smoke 都本次执行。隔离 smoke 另外解析依赖（pydantic 2.14.1/pydantic-core 2.50.1），与本轮锁定测试环境不同，只证明其 CLI 冒烟范围。构建物不跟踪，准确 SHA-256/大小在[artifact-fingerprints.json](artifact-fingerprints.json)，内容/共享面板核对报告在[artifact-verification.json](artifact-verification.json)。这些不证明真实 Trae 兼容/业务功能。

## 5. 文档清理与原文恢复

全部原整改 Markdown 和四包检查表共 21 份的 Git 字节、SHA-256 与基线见[documents-before.json](documents-before.json)，另有本机 `.runtime/p1-docs-before-845e697.zip`。删除 9 份过时报告/计划/交接，保留有效不变量/关闭条件并刷新所有保留文档，详细[变更记录](../../一期工程整改/文档清理与逐项变更记录-2026-10-12.md)。历史导航对已删除文件改为固定 Git 基线链接，见[link-migrations.json](link-migrations.json)；原验证日志/脚本/manifest 不改字节。

原文恢复示例：`git show 845e697:docs/一期工程整改/04-交接/交接说明-2026-10-11.md`。当前导航/锚点/表格/编号检查见[document-checks.json](document-checks.json)，当前任务见[项目现状](../../现状.md)及四包表。FR/AC 没有增删，新增缺陷使用独立 A-19/D-10，原 D-08/D-09 关闭只对应代码问题。

## 6. 未验证

真实 Trae 及完整产品业务 UI、实际供应方故障/同业务对象验证、多版本全量、符号链接权限场景、真实进程强杀/物理掉电与所有 AC 的真实环境证据均未在本轮取得。台账保持原结果和空证据路径，不用静态/单元/真实文件/子进程/浏览器/制品成功代填 verified。
