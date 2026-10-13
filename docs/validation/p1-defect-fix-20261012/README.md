# C-15 / C-16 / D-10 / A-19 修复轮验证证据（2026-10-12）

基线：`fix@9c0f65a`（产品源码与 `develop@845e697` 相同）。本目录只保存**修复后**的可复现命令与原输出；修复前反例的原输出仍留在各自历史目录，未改动、未覆盖。

来源任务：[当前代码分析与一期工程对比](../../一期工程整改/01-现状检查/当前代码分析与一期工程对比.md)（C-15／C-16 新增，A-19／D-10 既有）。逐项修复说明与关闭条件见[修复证据](../../一期工程整改/03-修复证据/2026-10-12-C15-C16-D10-A19修复证据.md)。

## 1. 修复前反例（保持原样，勿重跑覆盖）

| 脚本 | 断言的是 | 修复后状态 |
| --- | --- | --- |
| [source-provider-counterexamples.py](../p1-source-completion-20261012/source-provider-counterexamples.py) | 默认链**必须**报 `SnapshotError`；三组语法/模块错误**必须** `verified` | **按设计失败**（断言的是缺陷行为）；其原输出见同目录 JSON |
| [decision-counterexamples.py](../p1-code-review-20261012/decision-counterexamples.py) | 冲突必需证据仍 passed/A、同源 verified+unverified 仍 matched、非当前 Attempt 仍计 E | 三例已变为拒绝/降级；原输出见 [counterexamples.json](../p1-code-review-20261012/counterexamples.json) |

修复后行为由新脚本与回归测试断言，见下。

## 2. 修复后取证

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'

# D-10（纯适配输入）与 C-16（规则层 + 真实临时文件）
.venv/Scripts/python.exe docs/validation/p1-defect-fix-20261012/verify-fixed-behaviors.py

# C-15 真实默认链（真实核心/UOW/固定字节/物化/默认解析器与来源 provider）、
# C-16 真实语法反例、D-10 合同回归、解析器/来源边界
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_start_source_chain.py `
  tests/unit/test_start_source_verification.py tests/unit/test_source_checks.py `
  tests/unit/test_action_resolver.py tests/contracts/test_execution_facts_decision_adapter.py
```

受影响路径的既有用例回归（命令与原输出见下表的日志；**不含**本机已知不可完成的文件，见 §4）：

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider `
  --junitxml=docs/validation/p1-defect-fix-20261012/2026-10-12-regression.xml `
  tests/architecture tests/contracts/test_prepared_run_functional_fixtures.py `
  tests/contracts/test_execution_facts_decision_adapter.py tests/contracts/test_prepare_run_entrypoint.py `
  tests/integration tests/unit/test_action_resolver.py tests/unit/test_start_source_verification.py `
  tests/unit/test_source_checks.py tests/unit/test_default_start_source_chain.py `
  tests/unit/test_a_a08_load_source_probe.py tests/unit/test_source_probe_protocol.py `
  tests/unit/test_review_decision.py tests/unit/test_authoritative_preparation.py `
  tests/unit/test_initial_run_registration.py tests/unit/test_prepare_run.py `
  tests/unit/test_prepare_run_flow.py tests/unit/test_substrate_adapter.py `
  tests/unit/test_default_source_analysis.py tests/unit/test_execution_source_binding.py `
  tests/unit/test_start_materialization.py tests/unit/test_start_source_binding.py `
  tests/unit/test_start_source_identity.py tests/unit/test_source_failure_identity.py `
  tests/unit/test_pinned_source_closure.py tests/unit/test_consumed_source_closure.py

# A-19 锁定安装
uv lock --check
$env:UV_PROJECT_ENVIRONMENT="$PWD/.runtime/uv-locked-venv"; uv sync --locked
```

产物：

| 文件 | 内容 |
| --- | --- |
| [fixed-behaviors.json](fixed-behaviors.json) | D-10 四例（对照 + 三个反例）与 C-16 三类（语法/逐模块/探针失败）的机器可读结果 |
| [2026-10-12-regression.log](2026-10-12-regression.log) / [.xml](2026-10-12-regression.xml) | 上述受影响路径批次的原始输出/JUnit：**346 passed / 0 failed / 0 error / 1 skipped**（1259.96 秒；skip 为已知目录符号链接宿主权限不足项） |
| [2026-10-12-locked-install.txt](2026-10-12-locked-install.txt) | `uv --version`、`uv lock --check`、全新环境 `uv sync --locked` 与导入核对的原输出与退出码 |

耗时说明：修复前默认链在 C-15 处早退，**真实来源核验从未执行**；修复后它会实际运行（`verify_saved` 逐 blob 复核 + 子进程探针导入并逐个 `.py` 编译），因此相关用例耗时上升属预期，不代表功能回归。

关键断言（修复后）：

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 默认链 `prepare_execution` | `SnapshotError`（未启动业务） | 返回 `execution_action_id`，`materialized_snapshot_ref` 为固定 workdir，业务 source-ID ≠ 技术 snap-ID |
| 平铺入口语法错误 | `verified`、空 gap | `mismatch` + `source_syntax_error:main.py` |
| 好坏双包 | `verified`、空 gap | `unverified` + `module[bad] load_error=...` |
| 命名空间包坏子文件 | `verified`、空 gap | `mismatch`（语法错） |
| D-10 必需证据 conflict | `required_evidence_valid=True`、passed/A | `False`、`incomplete`、grade C |
| D-10 同源 verified+unverified | `matched`、passed/A | `unverified`、`incomplete`、grade C |
| D-10 非当前 Attempt | 计 E、`incomplete`/B | `executed=[]`、grade D |
| `uv sync --locked` | 退出 1（范围不符） | 退出 0（全新环境） |

## 5. 第二批：FR15 问题生命周期生产用例（同日）

报告 P1-FR15 行的缺口（`update_issue`/`record_fix`/`close_issue` 无入口、关闭依据由调用方自报）已在本会话第二批修复：新增 `issue` 记录 + `issue_intent` 幂等 + `issues.list` 索引入口，三个动作并入默认装配，关闭依据六项事实由已提交事实派生；并修掉 `PortsRecordReader.query` 丢条件使 `issues.list` 退化的缺陷。逐项说明、命令与结果见[FR15 修复证据](../../一期工程整改/03-修复证据/2026-10-12-FR15问题生命周期生产用例.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_saved_defect_management.py tests/unit/test_default_defect_management.py
# 受影响路径批量：199 passed / 0 failed（130.16 秒），日志在 .runtime/affected/fr15-batch.log
# 两批合并的会话级回归：
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --junitxml=<...> `
  tests/unit/test_saved_defect_management.py tests/unit/test_default_defect_management.py tests/unit/test_substrate_adapter.py `
  tests/unit/test_bounded_query_directory.py tests/unit/test_a_a05_bound_cursor.py tests/unit/test_a_a02_core_dispatch.py `
  tests/unit/test_defect_management_application.py tests/unit/test_reporting_application.py tests/unit/test_review_decision.py `
  tests/unit/test_acceptance_status.py tests/contracts/test_execution_facts_decision_adapter.py tests/contracts/test_local_api.py `
  tests/contracts/test_b_use_case_registration.py tests/contracts/test_prepared_run_functional_fixtures.py `
  tests/unit/test_default_maintenance_actions.py tests/unit/test_a_a10_capability_gate.py tests/unit/test_default_reuse_inspection.py `
  tests/unit/test_action_resolver.py tests/unit/test_start_source_verification.py tests/unit/test_source_checks.py `
  tests/unit/test_default_start_source_chain.py tests/unit/test_a_a08_load_source_probe.py tests/unit/test_source_probe_protocol.py `
  tests/unit/test_default_source_analysis.py tests/unit/test_execution_source_binding.py tests/unit/test_authoritative_preparation.py `
  tests/unit/test_initial_run_registration.py tests/unit/test_prepare_run.py tests/unit/test_prepare_run_flow.py `
  tests/unit/test_start_materialization.py tests/unit/test_start_source_binding.py tests/unit/test_start_source_identity.py `
  tests/unit/test_source_failure_identity.py tests/unit/test_pinned_source_closure.py tests/unit/test_consumed_source_closure.py `
  tests/unit/test_task_and_delivery_persistence.py tests/architecture
```

会话级回归结果：**518 passed / 0 failed / 0 error / 1 skipped**（2644.78 秒；skip 为已知目录符号链接宿主权限不足项），原输出与 JUnit 见 [2026-10-12-session-regression.log](2026-10-12-session-regression.log) / [.xml](2026-10-12-session-regression.xml)。

第二批未做：FR14 报告/复核/导出与 `exports.py` 制品；`request_regression`（合同未定义）。全量 5047 节点仍未跑完，不以分块冒充全量。

## 5b. 第三批：FR14 报告与复核生产用例（同日）

报告 `report` 记录（判定与冻结输入由已提交事实 + 已保存准备/范围派生）与 `local_review` 记录（绑定准确报告修订、复核人取受控会话）已接生产，`create_report`／`record_local_review` 并入默认装配（默认可派发动作 46→48），`reports.list`／`reports.by_id` 可命中；同时把"依据确认核对"抽成 `authoritative_basis.SavedCaseBasisCheck`，问题关闭与报告共用同一权威路径。导出（`export_report`／`copy_repair_brief` 与安全制品）仍未做。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_saved_reports.py tests/unit/test_saved_defect_management.py tests/unit/test_default_defect_management.py
```

第三批受影响路径回归：**351 passed / 0 failed / 0 error / 1 skipped**（1757.88 秒），原输出与 JUnit 见 [2026-10-12-fr14-regression.log](2026-10-12-fr14-regression.log) / [.xml](2026-10-12-fr14-regression.xml)。

## 6. 第四批：FR14 导出与复测简报（同日）

实现 `ReportArtifactPort`（临时写入→摘要核对→原子发布；规范相对路径底线、篡改拒绝、幂等复用），导出制品**先入对象库**再发布交付副本；`export_report` 产出 Markdown 摘要与 ZIP 证据包（`report.md` + `manifest.json` + `evidence/`，逐文件 SHA-256、排除/缺失原因、脱敏强制），导出身份由冻结输入派生、相同输入复用产物；只读 `copy_repair_brief` 输出脱敏复测指引且不含证据正文。默认可派发动作 48→50。逐项说明与边界见[FR14 导出证据](../../一期工程整改/03-修复证据/2026-10-12-FR14导出与复测简报生产用例.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_saved_exports.py
```

第四批受影响路径回归：**349 passed / 0 failed / 0 error / 0 skipped**（829.35 秒），原输出与 JUnit 见 [2026-10-12-fr14-export-regression.log](2026-10-12-fr14-export-regression.log) / [.xml](2026-10-12-fr14-export-regression.xml)。

## 7. 第五批：D 面板只读业务投影（2026-10-13）

面板接宿主声明的项目上下文（未声明即"未选择"，不猜身份）、项目作用域只读守卫（无上下文显式拒绝 `PROJECT_NOT_SELECTED`）、问题清单/报告修订只读视图（回执记录行原样列出），并新增 Python 只读白名单守卫锁定"面板只能派发 READ_ACTIONS 且源码不得出现写动作名"。逐项说明与边界见[面板证据](../../一期工程整改/03-修复证据/2026-10-13-D面板只读业务投影.md)。

```powershell
cd src/aitest/resources/panel; npx tsc --noEmit; npm run build; npx playwright test   # 3 passed
cd ../../../..; .venv/Scripts/python.exe -m pytest -q tests/unit/test_panel_read_only_allowlist.py tests/unit/test_trae_read_only_allowlist.py
```

第五批受影响路径回归：**76 passed / 0 failed / 0 error / 0 skipped**（55.41 秒，[JUnit](2026-10-13-panel-regression.xml)）；面板侧 `tsc` 与 Playwright（3 passed）另计。

## 8. 第六批：B 计划与规则草稿入口（2026-10-13）

补齐报告点名的两个名称缺口：`save_plan`（保存计划**草稿**，不跑发布门禁、不写发布标识、已发布不可覆盖，与 `publish_plan` 共用同一条"调用方所见修订"乐观锁）与 `save_rules`（与 `import_rules` 共用 `save_rule_draft` 落盘路径，**不产生 `rule_version`**，未发布不生效）。默认可派发动作 50 → 52。逐项说明与边界见[草稿入口证据](../../一期工程整改/03-修复证据/2026-10-13-B计划与规则草稿入口.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_save_plan_and_rules_entrypoints.py
```

第六批受影响路径回归：**170 passed / 0 failed / 0 error / 0 skipped**（133.58 秒），原输出与 JUnit 见 [2026-10-13-draft-entrypoints-regression.log](2026-10-13-draft-entrypoints-regression.log) / [.xml](2026-10-13-draft-entrypoints-regression.xml)。

## 9. 第七批：项目显示偏好（2026-10-13）

补齐 `save_project_view_preference`（P1-AC34）：记录类别 `project_view_preference`（`aitest.project-view-preference/1.0`）只记隐藏/恢复与提交序号，只写这一条、不动项目/绑定/运行/历史，也不停运行；只有已保存项目可被隐藏/恢复；`hidden` 必须是显式真布尔；隐藏与恢复各自是一次带乐观锁的修订追加，并可经共享 `query` 读回。默认可派发动作 52 → 53。见[草稿入口证据](../../一期工程整改/03-修复证据/2026-10-13-B计划与规则草稿入口.md)（同一批次记录）与 `tests/contracts/test_view_preference_entrypoint.py`。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/contracts/test_view_preference_entrypoint.py
```

第七批受影响路径回归：**194 passed / 0 failed / 0 error / 0 skipped**（111.38 秒），原输出与 JUnit 见 [2026-10-13-view-preference-regression.log](2026-10-13-view-preference-regression.log) / [.xml](2026-10-13-view-preference-regression.xml)。

## 10. 第八批：整用例复用（R）消费规则（2026-10-13）

`build_decision_facts` 过去把 `reused` 写成固定空集（"没有候选"与"消费不了"长得一样）。本批新增 `application/review/reuse_consumption.py`：候选取自已保存 `ReuseReference`，逐条按 **10 条完整守卫**核对，任一缺失/未知/不一致即拒绝并保留**具名原因**；报告 payload 记录 `reuse` 读数，候选来源未接时显式写 `candidates: null`。保存侧动作名仍缺合同定义，因此生产装配不伪造候选。见[R 消费证据](../../一期工程整改/03-修复证据/2026-10-13-C0105整用例复用消费规则.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_reuse_consumption.py tests/unit/test_saved_reports.py
```

第八批受影响路径回归：**129 passed / 0 failed / 0 error / 0 skipped**（62.77 秒），原输出与 JUnit 见 [2026-10-13-reuse-consumption-regression.log](2026-10-13-reuse-consumption-regression.log) / [.xml](2026-10-13-reuse-consumption-regression.xml)。

## 11. 第九批：C 受控人工补证入口（2026-10-13）

补齐 `attach_evidence`（报告点名"缺受控人工补证入口"）：登记人工**实际材料**并永久保存（内容寻址对象库 + 落盘前凭据过滤），同一工作单元写入 `evidence_ref`（`capture_source=manual`、`evidence_level=manual_associated`）+ `manual_evidence` 记录 + 意图回执；**不判业务通过、不改状态、不做安全替换**，非当前/未完成尝试与不安全材料一律结构化拒绝。默认可派发动作 53 → 54。见[补证入口证据](../../一期工程整改/03-修复证据/2026-10-13-C受控人工补证入口.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_manual_evidence.py
```

第九批受影响路径回归：**136 passed / 0 failed / 0 error / 0 skipped**（201.34 秒），原输出与 JUnit 见 [2026-10-13-manual-evidence-regression.log](2026-10-13-manual-evidence-regression.log) / [.xml](2026-10-13-manual-evidence-regression.xml)。

## 12. 第十批：C 重试入口 `retry_step`（2026-10-13）

补齐 `retry_step`（FR09 未注册动作）：入口只加前置条件（重试必须已解析出新一轮执行动作 `attempt_index >= 2`，否则提示改用 `execute_step`），随后委派既有步骤执行用例；"新尝试立即当前 + 下游标为需重跑"沿用执行提交路径的同一失效机制，旧尝试保留为历史。默认可派发动作 54 → 55。见[重试入口证据](../../一期工程整改/03-修复证据/2026-10-13-C重试入口retry-step.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_retry_step.py
```

第十批受影响路径回归：**121 passed / 0 failed / 0 error / 0 skipped**（468.92 秒），原输出与 JUnit 见 [2026-10-13-retry-step-regression.log](2026-10-13-retry-step-regression.log) / [.xml](2026-10-13-retry-step-regression.xml)。

## 13. 第十一批：C 受控备份入口 `backup`（2026-10-13）

补齐 `backup`（报告点名"工作空间级受控写身份与产品入口尚未完成"）：受控交互会话（Agent 中继拒绝）+ 工作空间之外的绝对目标 + 存储端口冻结闭包 + **建后立即校验** + 维护审计（时间/校验结果/缺失内容）+ 如实 `complete`/`known_missing`（缺必需永久材料不得称完整备份）；维护模式备份明确拒绝。应用层经新增存储级端口访问基础设施（架构方向测试拦截后改正）。默认可派发动作 55 → 56。见[备份入口证据](../../一期工程整改/03-修复证据/2026-10-13-C受控备份入口backup.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_backup_entry.py
```

第十一批受影响路径回归：**173 passed / 0 failed / 0 error / 0 skipped**（138.50 秒），原输出与 JUnit 见 [2026-10-13-backup-entry-regression.log](2026-10-13-backup-entry-regression.log) / [.xml](2026-10-13-backup-entry-regression.xml)。

## 14. 第十二批：C 结构迁移入口与维护窗口（2026-10-13）

补齐 `migrate`（报告点名"migrate 有底层组件、缺受控产品入口"）：五模式与 `aitest migrate` 子命令一一对应；`inspect` 只读，其余要求受控交互会话并在**维护窗口**内执行；窗口期间入口拒绝新的写动作（`maintenance_required`），只读与维护动作继续可用，成功/失败都解除窗口。迁移硬门禁（apply 前可校验备份、活动未核实不得迁移、不可逆回滚整体拒绝）继续由存储组件实现。`test_connection` 改为能力声明：仅在配置连接器时出现在 `doctor.supported_actions`。默认可派发动作 56 → 57。见[迁移入口证据](../../一期工程整改/03-修复证据/2026-10-13-C结构迁移入口与维护窗口.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_migrate_entry.py
```

第十二批受影响路径回归：**173 passed / 0 failed / 0 error / 0 skipped / 1 skipped**（183.69 秒），原输出与 JUnit 见 [2026-10-13-migrate-entry-regression.log](2026-10-13-migrate-entry-regression.log) / [.xml](2026-10-13-migrate-entry-regression.xml)。

> 注意：`tests/unit/test_a_a02_core_dispatch.py`（真实核心 worker）**单独运行**才稳定（18 passed，30.88 秒，[.xml](2026-10-13-core-worker-alone.xml)）；与重测试并发时会撞上已登记的真实核心启动等待超时负载类假失败（AGENTS.md §5.5）。本批先按此拆分留证，不把并发失败计入代码缺陷。

## 15. 第十三批：C 备份登记与恢复入口（2026-10-13）

补上接口级 `BackupPort`（`inspect`/`verify`/`restore` 按 `backup_id`）在入口侧的缺口：`backup` 扩展为 `create`/`inspect`/`restore` 三模式，`backup_id` = 清单摘要（内容寻址）；`inspect` 读回登记并可现场复核闭包（只读，任何受控会话可用）；`restore` 从登记 id 恢复到**新的空目标**，先现场复核源、不覆盖任何材料，并把索引重建/活动步骤待核实/双接管检查如实登记为后置条件与缺口。见[备份登记与恢复证据](../../一期工程整改/03-修复证据/2026-10-13-C备份登记与恢复入口.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_backup_modes.py
```

第十三批受影响路径回归：**185 passed / 0 failed / 0 error / 0 skipped / 1 skipped**（59.31 秒），原输出与 JUnit 见 [2026-10-13-backup-modes-regression.log](2026-10-13-backup-modes-regression.log) / [.xml](2026-10-13-backup-modes-regression.xml)。

## 16. 第十四批：D 面板问题详情业务闭环（2026-10-13）

面板问题清单行可选中，点击后按**回执给出的 `record_id`/`revision`** 派发 `query` 读详情（不猜身份/修订）；详情按设计 §10 字段渲染、缺失字段如实说明、保留原始 JSON 回执、界面不重算结论；「复制说明」只写剪贴板并提示"未执行修复、未派发核心动作"；核心拒绝时只显示 code/message 且不渲染字段。写入流程未接面板（设计 §12 只读 + `core-bridge` "界面点击不等于用户确认"）。见[面板问题详情证据](../../一期工程整改/03-修复证据/2026-10-13-D面板问题详情业务闭环.md)。

```powershell
cd src/aitest/resources/panel
npm run build
npx playwright test
```

面板用例 5 passed（3 既有 + 2 新增）；受影响 Python 批次 **79 passed / 0 failed / 0 error / 0 skipped**（53.47 秒），原输出与 JUnit 见 [2026-10-13-panel-issue-detail-regression.log](2026-10-13-panel-issue-detail-regression.log) / [.xml](2026-10-13-panel-issue-detail-regression.xml)。

### 第十四批补注（口径修正，2026-10-13）

B `query` 按存储合同第 13 节**只回记录行**（`{aggregate_kind, record_id, revision}`），不回记录载荷；新增第 3 个面板用例锁定"只回记录行的详情回执不得补造字段、复制说明禁用且剪贴板为空"。面板用例合计 **6 passed**。一期动作表没有问题/报告详情读取动作，这一环已登记到[待裁定清单](../../一期工程整改/05-待裁定/2026-10-13-剩余合同缺口与所需决定.md)。

## 17. 第十六批：D 面板已提交事件增量续读（2026-10-13）

面板新增事件流区块：首次不带游标，其后按回执 `next_cursor` 增量续读（游标只前移、拒绝不静默重置、空回执不丢行、事件序号去重）；真实核心把 `invalid_cursor` 放在 `result.status` 内，面板已按此处理并有用例覆盖。新增 Python 契约用例锁定 `events` 结果形状。见[事件续读证据](../../一期工程整改/03-修复证据/2026-10-13-D面板已提交事件增量续读.md)。

```powershell
cd src/aitest/resources/panel
npm run build
npx playwright test
```

面板用例 **8 passed**；受影响 Python 批次 **93 passed / 0 failed / 0 error / 0 skipped**（32.28 秒），原输出与 JUnit 见 [2026-10-13-panel-event-stream-regression.log](2026-10-13-panel-event-stream-regression.log) / [.xml](2026-10-13-panel-event-stream-regression.xml)。

## 18. 第十七批：D 面板复测简报只读视图（2026-10-13）

问题详情内新增「读取复测简报」：按清单回执的问题身份与宿主 run 派发只读 `copy_repair_brief`，正文与元数据原样展示、缺字段如实说明；复制简报不派发核心动作；拒绝时只显示代码且无正文可复制。面板用例 **10 passed**；受影响 Python 批次 **93 passed / 0 failed / 0 error / 0 skipped**（52.53 秒），原输出与 JUnit 见 [2026-10-13-panel-repair-brief-regression.log](2026-10-13-panel-repair-brief-regression.log) / [.xml](2026-10-13-panel-repair-brief-regression.xml)。见[复测简报证据](../../一期工程整改/03-修复证据/2026-10-13-D面板复测简报只读视图.md)。

## 19. 第十八批：D 导出安全与可携带包验证（2026-10-13）

用真实反例验证导出链：已注册凭据放入人工复核说明后，复核记录与导出 Markdown 正文都不含该凭据（写入层 + 导出层两层脱敏），导出记录声明脱敏策略；证据包 ZIP 的包内条目与清单路径全为相对路径。导出用例 **12 passed**；受影响 Python 批次 **106 passed / 0 failed / 0 error / 0 skipped**（64.16 秒），原输出与 JUnit 见 [2026-10-13-export-safety-regression.log](2026-10-13-export-safety-regression.log) / [.xml](2026-10-13-export-safety-regression.xml)。见[导出安全证据](../../一期工程整改/03-修复证据/2026-10-13-D导出安全与可携带包验证.md)。

## 20. 第十九批：受影响路径**整合回归**（2026-10-13）

把本会话各批改动涉及的路径一次性合并跑一遍，验证"改了代码、旧断言变红却无人发现"这一风险在本轮改动面上为零：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_retry_step.py tests/unit/test_default_manual_evidence.py tests/unit/test_default_backup_entry.py tests/unit/test_default_backup_modes.py tests/unit/test_default_migrate_entry.py tests/unit/test_default_maintenance_actions.py tests/unit/test_saved_reports.py tests/unit/test_saved_exports.py tests/unit/test_saved_defect_management.py tests/unit/test_default_defect_management.py tests/unit/test_reuse_consumption.py tests/unit/test_panel_read_only_allowlist.py tests/unit/test_acceptance_status.py tests/unit/test_source_checks.py tests/unit/test_start_source_verification.py tests/contracts/test_b_use_case_registration.py tests/contracts/test_local_api.py tests/contracts/test_execution_facts_decision_adapter.py tests/architecture
```

结果：**201 passed / 0 failed / 0 error / 0 skipped**（75.89 秒），原输出与 JUnit 见 [2026-10-13-consolidated-affected-paths.log](2026-10-13-consolidated-affected-paths.log) / [.xml](2026-10-13-consolidated-affected-paths.xml)。面板侧另有 Playwright **10 passed**（`src/aitest/resources/panel`，`npx playwright test`）。

**这不等于全量通过**：全量 5168 节点在本机需数小时，仍未跑完（AGENTS.md §5.5），本整合批次只覆盖本轮改动路径与其既有用例。

## 21. 第二十批：`request_regression` 与 `return_stage` 落地（2026-10-13）

按分册冻结语义补齐两个一期动作：`return_stage` 与上游重试使用同一失效机制并如实回执下游需重跑清单（影响面来自已提交事实的依赖边，读不到事实即拒绝、不先执行）；`request_regression` 只登记回归关联（修复版本/回归方式/原关闭条件/原复现尝试与证据），不创建 Attempt、不执行、不授权、不关闭。顺带修复「重复声明同一证据会写出不可读记录」的缺陷。

新增用例：`tests/unit/test_request_regression.py`（6）、`tests/unit/test_default_return_stage.py`（6）；受影响路径回归 **142 passed / 0 failed / 0 error / 0 skipped**（144.14 秒），原输出与 JUnit 见 [2026-10-13-request-regression-return-stage.log](2026-10-13-request-regression-return-stage.log) / [.xml](2026-10-13-request-regression-return-stage.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-request-regression与return-stage.md)。

### 空白检查的调用方式（2026-10-13 补记）

本仓库 `core.autocrlf=true` 且**已提交 blob 使用 CRLF**，而默认 `core.whitespace` 会把行尾 CR 当成 trailing whitespace，于是同一份改动会报出**上百条假告警**（仅发生在新增行上，`git diff --stat` 只显示插入行数、无整文件重写）。凡改动落在 CRLF 文件上的批次，空白检查按下面这条命令执行（`cr-at-eol` 的语义正是「行尾 CR 不算 trailing whitespace」）：

```powershell
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

该调用在第二十批上返回 0 条；用默认配置会报 102 条纯 CR 告警，属配置口径差异，不是代码问题。


## 22. 第二十一批：FR02 核验状态与自述分列（2026-10-13）

按功能 §14 与 BD-001 §7.2 的冻结语义：新增 `VerificationState` 三态词汇，由 `Delivery.verification_state` 派生（有已验证范围⇒核验有效，否则⇒必要核验未完成）；`submit_delivery` 回执不再硬编码 `unverified`，并把自述完成/未完成与验证状态分列。投影写入点仍待裁定。

新增 `tests/unit/test_delivery_verification_state.py`（5 passed）；既有提测套件 `tests/unit/test_delivery_submission_origin.py` 23 passed（两处旧断言改为冻结取值）。受影响路径回归 **123 passed / 0 failed / 0 error / 0 skipped**（770.25 秒），原输出与 JUnit 见 [2026-10-13-fr02-verification-state.log](2026-10-13-fr02-verification-state.log) / [.xml](2026-10-13-fr02-verification-state.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-FR02核验状态与自述分列.md)。

## 23. 第二十二批：C-03 核验声明点自述（2026-10-13）

回查分册与代码后的结论：只读查询的能力与配置形状已冻结（`HttpBusinessQuerySpec`、`FrozenCase.independent_verification`、领域 `EnvironmentRef` 的网络目标/凭据），但 `business_object_id`、`url_template` 登记来源、`expected_facts` 三个输入**没有声明点**，因此不实现默认 resolver；把默认装配的拒绝升级为逐次缺口自述（附分册引用，错误码不变）。

新增用例锁定该自述；受影响路径回归 **139 passed / 0 failed / 0 error / 0 skipped**（203.53 秒），原输出与 JUnit 见 [2026-10-13-c03-verification-declarations.log](2026-10-13-c03-verification-declarations.log) / [.xml](2026-10-13-c03-verification-declarations.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-C03核验声明点自述.md)。

## 24. 第二十三批：运行视图 DTO 与只读接缝（2026-10-13）

按架构 06 §1/§5 新增 `RunViewDTO`（S/M 两组、E/R/V/P/F/U、两套带分母摘要、步骤/待人工/阻塞/待核实/失效），并按 §54 新增 `CommittedViewReader` 只读接缝：判定复用 D 侧 `SavedReportService.live_decision`（新增公开方法，报告与本视图同一装配点），不写记录、不新增协议动作；无已保存运行时结构化拒绝。

新增 `tests/unit/test_run_view_dto.py`（4）与 `tests/unit/test_view_queries.py`（3）。受影响路径回归 **123 passed / 0 failed / 0 error / 0 skipped**（89.65 秒），原输出与 JUnit 见 [2026-10-13-run-view-dto.log](2026-10-13-run-view-dto.log) / [.xml](2026-10-13-run-view-dto.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-运行视图DTO与只读接缝.md)。

## 25. 第二十四批：对象分块与按需范围读取（2026-10-13）

按架构 04 §12 实现存储侧的"分块清单 + 按需范围读取"：`ChunkManifest`（总大小/固定顺序块/块摘要/完整摘要）与 `FileObjectStore.read_range`（只读取并验证触及块；旧对象先完整校验并建立可复用清单；块摘要不符立即删除清单并拒绝）。协议入口仍待裁定。

新增 `tests/unit/test_object_chunk_reads.py`（6 passed）。受影响路径回归 **193 passed / 0 failed / 0 error / 2 skipped**（266.74 秒），原输出与 JUnit 见 [2026-10-13-object-chunk-reads.log](2026-10-13-object-chunk-reads.log) / [.xml](2026-10-13-object-chunk-reads.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-对象分块与按需范围读取.md)。

## 26. 第二十五批：D-02 断言结果生产者（2026-10-13）

按 CD-001 §16「D 补断言结果生产者」补齐 D 侧生产端：`assertion_outcomes_from_facts` 只从已保存的 `VerificationFact.observation` 派生 `AssertionOutcomeFact`（不发明期望值），接入报告与运行视图共用的 `live_decision`；V/P/F/H 首次可从真实事实产生。期望值的登记点仍待裁定。

新增 `tests/unit/test_assertion_outcome_producer.py`（5 passed）。受影响路径回归 **162 passed / 0 failed / 0 error / 0 skipped**（343.65 秒），原输出与 JUnit 见 [2026-10-13-assertion-outcome-producer.log](2026-10-13-assertion-outcome-producer.log) / [.xml](2026-10-13-assertion-outcome-producer.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-D02断言结果生产者.md)。

## 27. 第二十六批：FR02 交付说明视图（2026-10-13）

显示侧落地：`DeliveryViewDTO`（自述与验证分列、核验状态取冻结三态）与 `CommittedViewReader.delivery_view(...)`（只读准确修订、跨项目拒绝、不猜提测）。同时核到 AB-001 §1.27 末句「交付验证投影…仍待接通」，故**写入点仍登记为待裁定**。

新增 `tests/unit/test_delivery_view.py`（6 passed）。受影响路径回归 **128 passed / 0 failed / 0 error / 0 skipped**（759.04 秒，含真实底座的交付提测套件），原输出与 JUnit 见 [2026-10-13-fr02-delivery-view.log](2026-10-13-fr02-delivery-view.log) / [.xml](2026-10-13-fr02-delivery-view.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-FR02交付说明视图.md)。

## 28. 第二十七批：迁移前备份可追溯性（2026-10-13）

迁移前强制备份进入维护审计（`kind=migration_pre_apply`，含清单摘要/文件数/字节数，只登记校验通过的备份），可经 `backup_facts`/`backup inspect` 读回；`migrate apply` 回执新增 `backup_outside_active_workspace`，如实呈现「迁移前备份与活动目录同盘」的位置事实。位置参数与跨进程维护态仍待裁定。

新增 `tests/unit/test_migration_backup_traceability.py`（2 passed）。受影响路径回归 **180 passed / 0 failed / 0 error / 2 skipped**（67.10 秒），原输出与 JUnit 见 [2026-10-13-pre-migration-backup-audit.log](2026-10-13-pre-migration-backup-audit.log) / [.xml](2026-10-13-pre-migration-backup-audit.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-迁移前备份可追溯性.md)。

## 29. 第二十八批：列表与事件配对边界（2026-10-13）

按架构 06 §4/§8/§136 给 `query` 响应补上**同一已提交边界**的 `commit_id` 与 `event_cursor`：存储层 `IndexQueryResult` 本就有这两个值，此前在 `RecordQueryResult`（AC-001 §147 三字段）处被丢弃；现由并行的 `RecordQueryPage` 一并返回（**冻结形状未改**），`RecordPage`/`substrate_adapter`/入口逐层透传，取不到时如实 `None`。

新增 `tests/unit/test_query_boundary_pairing.py`（3 passed）。受影响路径回归 **228 passed / 0 failed / 0 error / 0 skipped**（121.77 秒），原输出与 JUnit 见 [2026-10-13-query-event-boundary-pairing.log](2026-10-13-query-event-boundary-pairing.log) / [.xml](2026-10-13-query-event-boundary-pairing.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-列表与事件配对边界.md)。

## 30. 第二十九批：面板主屏当前段与情形事实（2026-10-13）

按架构 06 §1 落地 `PanelViewDTO`：当前段只由已提交 `control_state` 判定，情形事实用既有词汇（断言失败用例、`ReviewGapCode`、待人工/待核实步骤），判定复用同一次 `live_decision`；`primary_action` 的动作标识未冻结故不给候选。

新增 `tests/unit/test_panel_view_dto.py`（5 passed）。受影响路径回归 **118 passed / 0 failed / 0 error / 0 skipped**（660.37 秒，含真实底座交付提测套件），原输出与 JUnit 见 [2026-10-13-panel-stage-facts.log](2026-10-13-panel-stage-facts.log) / [.xml](2026-10-13-panel-stage-facts.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-面板主屏当前段与情形事实.md)。

## 31. 第三十批：C-03 声明点按路径细分（2026-10-13）

默认装配的核验缺口自述按路径区分（HTTP/数据库核验 vs 纯函数独立预期比对），并附功能 §4 与架构 01 §1.5 的环境声明清单差异证据；穷举检索确认 `business_object_id` 无声明载体，所需决定收窄为「是否在环境声明增该字段并同步架构清单/序列化」。

受影响路径回归 **121 passed / 0 failed / 0 error / 0 skipped**（331.47 秒），原输出与 JUnit 见 [2026-10-13-c03-declaration-paths.log](2026-10-13-c03-declaration-paths.log) / [.xml](2026-10-13-c03-declaration-paths.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-C03声明点按路径细分.md)。

## 32. 第三十一批：R 复用保存侧收窄 + 八项处置总表（2026-10-13）

复核确认 R 保存侧只差动作名与幂等键（记录形状/消费侧/分类位/入参均已冻结并实现），新增 `tests/unit/test_reuse_reference_contract.py`（4 passed）锁定合同一致性；待裁定清单开头新增「八项处置状态总表」。

受影响路径回归见本目录 [2026-10-13-reuse-save-side.log](2026-10-13-reuse-save-side.log) / [.xml](2026-10-13-reuse-save-side.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-复用保存侧收窄.md)。

## 33. 第三十一批（收尾）：八项合同缺口逐项处置 + 合并回归（2026-10-13）

本轮目标（按项目文档逐项裁定并落地《待裁定清单》8 项合同缺口）的收尾证据：

- **已冻结且已落地**：`request_regression`（安排回归）、`return_stage`（退回阶段，同一失效机制 + 影响面回执）、迁移前备份的校验/登记/位置事实、维护窗口与单写锁、对象分块按需范围读取、运行视图/面板视图 DTO 与宿主只读接缝、列表与事件同边界配对（`commit_id`+`event_cursor`）、D 侧断言结果生产者、FR02 三态显示与交付说明视图、C-03 缺口自述（按路径细分）；
- **文档确实未定义（保留登记，不猜）**：R 复用保存侧动作名与幂等键、C-03 的三条声明点、迁移前备份位置参数名与跨进程维护态标记、D-02 期望值登记点、FR02 投影写入点、`primary_action` 动作标识与主屏快照取法/附件分块协议入口。

逐项状态见[待裁定清单「八项处置状态总表」](../../一期工程整改/05-待裁定/2026-10-13-剩余合同缺口与所需决定.md)与[项目现状 §5.1](../../现状.md)。

**合并回归**（覆盖全部被改动路径的既有用例）：**189 passed / 0 failed / 0 error / 0 skipped**（121.45 秒），原输出与 JUnit 见 [2026-10-13-eight-items-consolidated.log](2026-10-13-eight-items-consolidated.log) / [.xml](2026-10-13-eight-items-consolidated.xml)。

边界：真实 Trae、物理掉电、完整 P1-AC 仍未验收；`tests/acceptance/p1/status.json` 未改动；全量 5225 节点未跑完（本目录各批数字不得相加冒充全量）。

## 34. 第三十二批：CORE-001 1.81 台账同步与测试临时目录卫生（2026-10-13）

**接口台账**：按[接口目录规则第 5.5/5.7 条](../../接口对接/AGENTS.md)，把第 8 轮实现的「`query` 响应追加同一已提交边界的 `commit_id`/`event_cursor`」正式登记进 CORE-001：

- 合同头部 `contract_version: 1.80 → 1.81`，正文新增 **1.81** 条目（冻结依据=架构 06 §4/§8/§136；追加字段不改既有语义；`RecordQueryResult(status, items, next_cursor)` 三字段与 AC-001 §147 不变；取不到如实 `null`，不退回最新水位）；后半段版本行同步为 1.81；
- 总台账 `docs/接口对接/README.md` 版本 2.82 → 2.83，CORE-001 行版本与下一动作同步；
- `docs/现状.md` §4 接口合同表 CORE-001 版本 1.80 → 1.81；
- 新增合同检查 `tests/contracts/test_query_boundary_response.py`（键集合、类型、边界一致性、既有字段未变）。

**测试临时目录卫生**：本机 TEMP 回退到检出目录时，备份/迁移/重试/退回用例的 `mkdtemp` 会在仓库根累积 `aitest-*` 目录（本轮实测清理 **466 个**）。已在 `tests/conftest.py` 加**会话级**清理（开始清历史遗留、结束清本轮遗留，只匹配测试专用前缀且必须位于仓库根），并在 `.gitignore` 锚定根目录忽略这些前缀；实测：会话开始把上一次残留 29 个清到 1 个（Windows 句柄在会话内无法删除的剩余部分由下一会话清理，且已不再进入 `git status`）。

**生成夹具**：`tests/contracts/fixtures/prepared_run_functional/*.json` 的 4 处差异为 C-15 修复后的真实结果（`cwd_mapping` 由 `<project-root> -> workdirs/run-1` 变为 `workdir:.`，`payload_hash` 随之更新），由契约夹具检查覆盖，**保留不还原**。

验证：`tests/contracts` 全量 **289 passed**（含新合同检查）；备份/迁移/重试/退回相关 **59 passed**；链接检查（CORE-001 与接口台账本地链接）0 坏链；`generate_schemas.py` 无生成物差异。

## 35. 第三十三批：整用例复用选择动作（2026-10-13）

按裁定 DEC-014 新增受控写动作 `select_case_reuse`：参数沿用只读检查的四个引用 + `case_id`，记录 `reuse_decision@1`（`ReuseReference` 列表，架构 01 §229 形状），幂等按 intent，人工动作，**只登记引用关系、不授予资格**；报告侧候选来源已接该记录。

新增 `tests/unit/test_case_reuse_selection.py`（12 passed）。相关回归 **205 passed / 0 failed / 0 error / 0 skipped**（67.46 秒），原输出与 JUnit 见 [2026-10-13-case-reuse-selection.log](2026-10-13-case-reuse-selection.log) / [.xml](2026-10-13-case-reuse-selection.xml)。见[证据](../../一期工程整改/03-修复证据/2026-10-13-复用选择动作.md)。

## 3. 边界（不冒充验收）
- 环境仍是**本机 Windows + 受控/合成夹具**；真实 Trae、真实业务对象与供应方故障、物理掉电、完整可携带导出**未验证**。
- `tests/acceptance/p1/status.json` 未改：本轮的组件/真实文件级证据不满足 `verified` 的非空证据路径 + 真实环境要求。
- `APPLICABLE_BUILD`／显式 `MINIMAL_START` 仍未实现（合同未冻结字段来源），故 L1 核验仍**不覆盖**「适用构建 / 最小启动」两类。
- 3.11／3.12 只做了锁定解析；**运行**复核未做，不写"各版本可复现安装"。
- **全量 5047 节点未跑完**：本机全量需数小时（`AGENTS.md` 第 5.5 条已写明）。本轮按"覆盖被改动路径的既有用例"分批执行，未以分块冒充全量，也未把未跑完写成通过。

## 4. 本机环境事实（复现要点，非产品结论）

1. **DSH 沙箱默认模式拒绝 `ReplaceFileW`**（工作区/`%TEMP%`/新目录均报 WinError 5），而本项目 `FilePublicationBackend` 的 `current.json`/提交清单发布只能走该 API ⇒ 任何装配核心的测试都会在迁移/发布处失败。实测与未改动文件的失败一致，非本批回归。本轮改以**完全权限**会话执行验证（用户授权切换）。
2. **沙箱拒绝访问以 `mode 0o700` 创建的目录**（同目录 0o755/0o750/0o777 均正常），而 pytest 的 basetemp/tmp_path/`tempfile.mkdtemp` 恰好都用该 mode ⇒ `pytest_sessionfinish` 在 `cleanup_dead_symlinks` 报 WinError 5、整轮退出码变 1。临时规避：仅本机的 `.runtime/run_pytest.py` 把该 mode 归一为 0o777 并每次给新 basetemp（**不改产品/测试语义**，未纳入仓库）。
3. **`tests/unit/test_execution_authorization_origin.py` 在本机单文件即超过 7 分钟不返回**（`faulthandler` 显示卡在 `pathlib.is_junction` 调用处，CPU 持续占用）。用 `git worktree` 在**修复前基线 `9c0f65a`** 重跑同一文件同样超过 7 分钟 ⇒ 与本次改动无关，属既有环境/性能问题，故该文件未纳入本批证据，且**不计入通过**。同类"真实起核心等待"问题在历史记录中已有一例（2026-10-12 负载型假失败）。

