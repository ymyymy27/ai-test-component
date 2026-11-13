# 2026-11-12 阶段 B 问题闭环与阶段 C 基线/回归建议验证

## 1 范围

本轮完成“大项目 pytest 支持”阶段 B 的服务端问题草稿、真实核心问题闭环，以及阶段 C 的基线对比和只读回归建议入口。未执行真实 Trae 端到端，未修改验收登记。

- 失败签名簇 → 问题草稿：服务端从已保存 JUnit 重算，调用方只提交 `run_id/step_id/signature`。
- 问题草稿冻结失败测试、`failure_frame`、`suspect_source_frame`、失败类别、消息、堆栈和证据引用；位置未知保留 `location_unknown`。
- `update_issue` 创建 pytest 草稿时校验簇存在于已保存运行，拒绝伪造节点、位置或证据。
- 真实核心问题生命周期：draft → awaiting_confirmation → confirmed → in_progress → ready_for_retest → closed；原始失败尝试不能关闭，重试后的新尝试可关闭。
- pytest 执行增加模块模式运行时来源观察：`python -m pytest` 在冻结 workdir 中执行，按项目文件摘要核对实际加载来源；因此 pytest 回归尝试可提供 `verified` 来源事实。
- 计划的 `mock_scope` 在运行登记时进入 `mock_declarations`（unverified，development_declaration），报告出现 `critical_mock`，不把未核验 Mock 写成模块正确。
- 报告冻结 `pytest_failures`；同范围上一份已保存报告生成 `pytest_baseline`（新增/既有/已修复节点）。
- 面板工作台显示失败签名簇、基线比较、问题草稿入口，以及只读“回归建议”卡片（依赖图精确修订 + 已发布计划修订）。

## 2 执行命令与结果

均为定向验证，不是全量 Python 回归。

| 命令 | 结果 |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest -q --import-mode=importlib tests/unit/test_pytest_issue_drafts.py` | 8 passed |
| `... tests/integration/test_pytest_defect_chain.py` | 1 passed |
| `... tests/integration/test_pytest_core_chain.py tests/integration/test_pytest_scope_execution.py` | 3 passed |
| `... tests/unit/test_runtime_source_capture.py tests/unit/test_command_adapter.py tests/unit/test_live_runner_collection.py tests/unit/test_command_handle_reattach.py tests/unit/test_live_command_capture.py` | 51 passed |
| `... tests/unit/test_saved_defect_management.py tests/unit/test_issue_lifecycle.py tests/unit/test_request_regression.py tests/unit/test_saved_exports.py tests/unit/test_default_defect_management.py tests/unit/test_defect_management_application.py` | 93 passed |
| `... tests/unit/test_saved_reports.py tests/unit/test_view_queries.py tests/unit/test_pytest_issue_drafts.py tests/integration/test_pytest_defect_chain.py tests/integration/test_pytest_core_chain.py` | 40 passed |
| `... tests/architecture/test_boundaries.py tests/unit/test_action_allowlists.py tests/contracts/test_b_use_case_registration.py tests/unit/test_runtime_source_complete_chain.py` | 19 passed |
| `cd src\aitest\resources\panel; npm run check` | tsc --noEmit 通过 |
| `cd src\aitest\resources\panel; npm test` | 32 passed |
| `cd integrations\trae; npm test` | 39 passed |
| `cd integrations\trae; npm run package` | 构建 `dist/aitest-trae.vsix`（7 files, 90.85 KB） |
| `tests/unit/test_test_discovery.py` + `test_test_selection.py` + `tests/integration/test_pytest_scope_execution.py` | 通过（含定向 node ID、unittest.TestCase 风格执行 4 项） |
| `tests/unit/test_saved_reports.py` | 13 passed（含项目级运行汇总） |
| `tests/unit/test_action_allowlists.py` + `test_trae_read_only_allowlist.py` | 通过 |
| 最终定向组合（草稿/发现/选择/报告/视图/动作/两条 pytest 链/HTTP 链） | 61 passed |
| `mypy --strict`（本轮新增/修改的 Python 源文件） | 通过 |
| `ruff check`（本轮修改的 Python 文件与测试） | 通过 |

`test_runtime_source_capture.py` 中集合回执版本断言已从 1.1 更新为 1.2，与阶段 A 的通用 artifact 回执一致。

## 3 真实 Trae 部分验证（计划已发布、运行已登记，执行/报告未完成）

本轮在隔离 profile 中安装了 VSIX 并启动真实 Trae CN 2.3.102035：

- 临时安装目录（验证后已删除）：`%TEMP%\aitest-trae-install`
- 隔离用户数据与扩展目录：`%TEMP%\aitest-trae-iso\{user-data,extensions}`
- 多包夹具：`%TEMP%\aitest-trae-iso\fixture`，含 `pkgA/tests/test_order.py`、`pkgB/tests/test_order.py` 同名测试
- 真实用户数据工作空间：`%LOCALAPPDATA%\aitest\guided-935db490-d7d6-476f-ba25-60a260428424\workspace`

已确认：

- VSIX 通过 Trae CLI 安装成功；真实 Trae 打开多包夹具，命令面板出现 `AI Test: 打开测试面板` / `配置工作空间与项目`。
- AI Test 面板加载并通过面板内确认发布计划、登记运行。
- 核心工作空间 `current.json` 的提交为 **commit 54**；查询到 2 个真实 Case：
  `case:pytest:1a39890edbc99737`、`case:pytest:d5e1f325eb3619e8`。
- 真实运行：`run-08b7fd9099c759c28ad59fa42a43c0cb4e2b233d26e97957088f47a183ba988d`；
  项目：`guided-935db490-d7d6-476f-ba25-60a260428424`；计划：`plan-guided-935db490-d7d6-476f-ba25-60a260428424@1`。
- 主窗口截图：[trae-panel.png](trae-panel.png)。

本轮在真实宿主中发现并绕过的环境问题：

1. 向导 `setup` 创建的项目 venv 只有标准库，没有 pytest；`discover_tests` 返回
   `No module named pytest`。手动向该 venv 安装 pytest 后重新发现，得到 2 个文件 / 4 个测试。
2. pip/setuptools 生成的 `distutils-precedence.pth` 被环境校验拒绝
   （`environment .pth needs an explicit loading mapping`）；移除该 `.pth` 后才完成发现。
3. 模型关闭时，pytest 范围向导会落到“四项独立检查”手工表单，没有 pytest 范围选择控件；
   本次通过面板控制器注入 `aitest.test-selection/1.0` 选择单（pkgA/pkgB 两个 dir scope）继续，
   **这不是正常用户路径**，属于测试夹具注入。
4. 向导 `setup` 更新 `workspaceRoot/projectId/runId` 后，面板工作台仍按旧配置读取并超时；
   重载窗口后核心连接/读取在限定时间内不可连接。执行与报告因此未完成。

未完成：

- 未执行 `prepare_execution → authorize_step → execute_step → verify_pending`；
  未生成报告、失败详情或问题草稿。
- 未在用户主 profile 安装；临时安装目录已删除，隔离数据保留作证据。

结论：**真实 Trae 已走到“计划发布 + 运行登记”（commit 54，2 个 Case），但执行、核验、报告未完成**；
不能写真实业务流通过；验收登记不变。

本轮后续代码修复（未重新在真实 Trae 重跑）：

- 新增 pytest 测试范围的模型关闭手工选择页，用户可从 Inventory 勾选文件生成选择单，不再落到四项独立检查。
- 向导 pytest 模式在 `setup` 时创建带 pip 的独立环境并安装 pytest，删除 setuptools 的 `distutils-precedence.pth`；下次真实向导应不再需要手工安装/删除。
- 报告冻结 `layers` 并在报告页显示；问题簇补充环境、计划修订、源码引用、显式 `expected/actual` 和复现命令。
- 面板 32 项、扩展 39 项、相关 Python 定向测试通过；这些修复仍需真实 Trae 重新验证。

## 4 未验证 / 未执行

- Python 全量测试未运行；上表分块结果不能写成“全量通过”。
- 真实 Trae 多包 discover → 计划 → 执行 → 失败详情 → 报告 → 问题闭环未完成（原因见第 3 节）。
- 定向 node ID 已接入后端、执行与计划校验；面板仍通过计划 JSON 编辑使用，未新增专用节点选择控件。
- 项目级汇总只读取已保存报告，不重跑执行；真实多解释器项目仍需验收。
- unittest 风格测试已通过 pytest 收集/JUnit 验证；不新增独立 `entry_type=unittest` 运行器。服务/数据库环境缺少启动/健康检查/迁移合同与真实环境，未实现。
- `tests/acceptance/p1/status.json` 未改；任何 AC 未登记为 verified。


## 6 2026-11-12 追加诊断（真实 Trae 连接）

- `ensure-core` 在干净工作空间和真实项目工作空间用 `E:\Python311\python.exe` 直接执行均返回 `READY`。
- 真实核心工作空间曾出现陈旧 `core/launch.json`、`launch.lock` 与约 2.6 MB `connection-ledger.jsonl`；清理陈旧 launch 状态并截断连接台账后，命令行 `ensure-core` 恢复 `READY`。
- 同一工作空间下，真实 Trae 面板的 `ensure-core` 仍在限定时间内不可核实；面板的每次读写都会先走该入口，因此工作台无法读取运行、也无法派发执行。
- 修复：扩展对 `ensure-core` 增加单飞控制（同一 python/workspace/carriers 只允许一次启动声明），避免面板并发读写互相抢占。安装新 VSIX 后真实 Trae 面板首次显示“核心已连接 · 本地工作空间”。
- 仍不稳定：点击“读取 / 刷新本次运行”后，真实 Trae 进程 CPU 接近 100%、内存约 80%，随后退出；同一工作空间命令行 `read_view` 仅约 6–7 秒、返回 1.5–4.8 KB，说明热循环不在核心业务读取。运行视图未取得，执行/报告仍未完成。
- 结论：真实 Trae 的阻塞是扩展宿主连接/读取稳定性，不是核心业务链或 pytest 执行问题；不能用命令行/组件测试替代真实 Trae 验收。未改 `tests/acceptance/p1/status.json`。

## 7 2026-11-12 追加：真实运行执行/核验/报告/问题草稿

在同一隔离 profile 与同一真实工作空间（项目 `guided-935db490-d7d6-476f-ba25-60a260428424`，运行
`run-08b7fd9099c759c28ad59fa42a43c0cb4e2b233d26e97957088f47a183ba988d`）继续完成。执行与查询经
CLI `dispatch` 驱动同一真实核心，授权使用核心原生确认窗口；**不是 Trae 面板点击链路**：

| 项目 | 结果 |
| --- | --- |
| 第 1 步 `execution-attempt-d069419d…` | pkgA 两个测试失败（`assert 1 == 2`），核验 `mismatched`，证据完整性 `complete` |
| 第 2 步 `execution-attempt-524cfb09…` | pkgB 两个测试通过，核验 `matched` |
| 运行视图 | `control_state=completed`，覆盖 2/2 executed、2/2 verified；failed 1 / passed 1 |
| 报告 `report-real-1` 修订 3 | `business_outcome=failed`；决定性失败 1（case `…1a39890…`）；`pytest_failures` 去重后 2 个节点，`location_unknown=false` |
| 问题草稿 `issue:pytest:f434ca836e25232fa22ab75a` | 状态 `draft`；冻结 2 个节点、`pkgA\tests\test_order.py:4/7`、JUnit 证据；links 含 case/run/attempt/L2 |
| 修复后源码 | `pkgA/order.py` 的 `total()` 改为 `2`；新快照 `source-88a37b8e…`，内容摘要 `sha256:8a287a8c…` |
| 修复后运行 `run-5aa8d0d7…` | 两个新 attempt 均 `matched`，来源核验 `verified` 且无缺口；`observed_source_digest` 等于修复快照摘要 |
| 问题关闭 | 修订 6 `status=closed`；`fix_version=sha256:8a287a8c…`；关闭依据为 run2 第 1 步新 attempt `a62d0891…`（不是原失败 attempt） |
| 修复后报告 `report-real-2` | 修订 1；覆盖 2/2 verified/passed，无 pytest 失败；唯一缺口 `critical_mock`，结论 `incomplete`（不是 passed） |

本轮修复（均有定向回归）：

1. `domain/execution/junit.py`：存在失败帧但缺少 `suspect_source_frame` 时不再把位置判成未知；缺 suspect 是独立事实。
2. `application/evidence/junit_artifact.py`：pytest 的 `actual_fields` 用同一 `compare_business_fields` 生成观察值与缺口，消除 `BUSINESS_VERIFICATION_BLOCKED`。
3. `application/review/execution_decision_adapter.py`：`business_fact_mismatch:*` 是明确比较结果，按决定性失败处理；缺失/过滤/未知缺口仍不产出。
4. `application/evidence/query_materials.py`：业务字段不匹配只留在核验缺口，不再把完整证据标成有缺口。
5. `application/evidence/pytest_failures.py`：报告/问题草稿只统计每步当前 attempt，历史 attempt 不重复计入。
6. `infrastructure/.../runtime_source.py` 与 `application/execution/runtime_source.py`：模块模式 pytest 的目录 scope（`pkgA/tests`）只要其下有已观察文件即算入口已观察。
7. `bootstrap.py` 的 `_entry_actual_path`：冻结入口参数是目录时，允许 workdir 内该目录作为实际入口，不再回落成清单第一个文件。
8. `application/execution/runtime_source.py`：模块模式运行时入口是 workdir 内目录、准入入口是同一 workdir 内文件时视为一致；解释器仍要求精确相同。修复后真实新 attempt 来源变为 `verified`。
9. `infrastructure/python_environment.py`：carrier 缺失与模式不符的错误带上 environment/requested/registered 三项身份，便于修复配置且不泄露路径。

定向回归命令与结果：

| 命令（省略 `.venv\Scripts\python.exe -m pytest -q --import-mode=importlib`） | 结果 |
| --- | --- |
| `tests/unit/test_junit_xml_results.py tests/unit/test_pytest_issue_drafts.py tests/integration/test_pytest_defect_chain.py tests/integration/test_pytest_scope_execution.py` | 18 passed |
| `tests/unit/test_assertion_outcome_producer.py … test_p1_review_boundaries.py` | 49 passed |
| `tests/unit/test_saved_business_verification.py test_assertion_outcome_producer.py test_configured_business_verification.py test_verification_json_semantics.py` | 65 passed |
| `tests/unit/test_pytest_issue_drafts.py tests/integration/test_pytest_split_draft.py tests/integration/test_pytest_defect_chain.py tests/unit/test_saved_reports.py` | 25 passed |
| `tests/unit/test_runtime_source_capture.py test_command_adapter.py test_runtime_source_complete_chain.py test_live_command_capture.py` | 49 passed |
| 10 个受影响单测/架构/验收对账文件 | 130 passed |
| `tests/unit/test_source_entry_path_mapping.py` | 2 passed（目录入口映射；文件入口仍精确） |
| `tests/integration/test_pytest_defect_chain.py test_pytest_scope_execution.py test_pytest_split_draft.py test_pytest_core_chain.py` | 7 passed（含目录 scope 来源入口断言） |
| `tests/unit/test_runtime_source_capture.py test_runtime_source_complete_chain.py test_source_entry_path_mapping.py test_assertion_outcome_producer.py test_saved_business_verification.py tests/integration/test_pytest_core_chain.py` | 56 passed（最后一次来源入口修复后） |

未完成 / 不能写成通过：

- 修复前的 `report-real-1` 仍保留历史 `source_identity_unverified`（已提交事实不追改）；修复后 `report-real-2` 已无来源缺口，且来源摘要与修复快照一致。
- `report-real-2` 仍有 `critical_mock`：计划 `mock_scope=unknown`，未核验 Mock；因此结论是 `incomplete`，不能写成 passed。
- 真实问题已在核心链路关闭（修订 6），但关闭由 CLI + 原生确认驱动，不是 Trae 面板点击链路。
- Python 全量未跑；服务/数据库、L3 完整业务、独立 `entry_type=unittest`、多解释器聚合仍未完成。
- `tests/acceptance/p1/status.json` 未改；任何 AC 未登记为 verified。
