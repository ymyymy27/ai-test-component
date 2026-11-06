# p1 AI 计划审阅与集中授权（2026-11-06）

## 1. 范围与版本

- 产品版本：0.4.10（`pyproject.toml`）。
- 分支：`develop`；本批为工作树实现，未提交、未打 tag。
- 目标：路线 A——AI 生成结构化计划提案 → 人一屏全字段审阅 → 发布 → 启动前逐动作集中授权 → 计划驱动自动执行。
- 不改变 FR/AC 编号与验收登记；真实 Trae、真实模型、真实 Tk 窗口和端到端自动执行仍属未验证。

## 2. 输入

- 模板：`python-library@1.0.0`（4 个模板项：syntax/normal/boundary/error）。
- 提案夹具：`tests/unit/test_plan_proposal.py` 与 `tests/unit/test_onboarding.py` 中的 `aitest.plan-proposal/1.0` 样例。
- 真实核心夹具：`tests/unit/test_authoritative_preparation.py::authoritative`（准备、登记、动作解析、授权）。
- 不存在真实供应方响应、真实 Windows 桌面窗口或真实 Trae 宿主输入。

## 3. 已执行命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest -q tests/unit/test_plan_proposal.py` | 10 passed |
| `pytest -q tests/unit/test_onboarding.py` | 13 passed（含提案 `commit`→`prepare`→`register` 真实核心链路） |
| `pytest -q tests/unit/test_model_orchestration.py tests/unit/test_model_basis_authority.py tests/unit/test_a_ab001_model_wiring.py` | 84 passed |
| `pytest -q tests/unit/test_abc_root_cause_regressions.py::test_model_draft_returns_structured_plan_proposal` | 1 passed（真实核心 + 假模型传输：system prompt、`response_format`、提案解析） |
| `pytest -q tests/unit/test_default_execution_authorization.py::test_native_batch_uses_one_window_and_one_gesture_per_action` | 1 passed |
| `pytest -q tests/unit/test_native_confirmation.py tests/unit/test_onboarding.py tests/unit/test_cli_core_forwarding.py` | 49 passed（本轮新增 onboarding 用例前） |
| `pytest -q tests/unit/test_native_confirmation.py` | 21 passed（单动作原生确认） |
| `pytest -q tests/unit/test_cli_core_forwarding.py` | 18 passed |
| `pytest -q tests/contracts/test_prepare_run_entrypoint.py` | 10 passed |
| `ruff check`（本批修改的 Python 文件） | 通过 |
| `ruff check src tests` | 通过 |
| `mypy`（strict，全包） | 通过，退出 0（最近一次 278 文件；文件计数随 mypy 缓存/调用方式变化，以 `check_docs_facts.py --full` 为准） |
| `python scripts/check_docs_facts.py --full` | 通过（263 模块 / 81,162 行 / 5,781 节点 / 62 动作 / 278 mypy 文件） |
| `npm --prefix src/aitest/resources/panel run check` / `build` | 通过，`dist/index.js` 已重建 |
| `npm --prefix integrations/trae run check` | 通过 |
| `npm --prefix integrations/trae test` | 32 passed（含 `nativeBatch` 宿主接线） |
| `python scripts/generate_schemas.py` | 生成 `PlanProposal.json`；未手改生成物 |
| `npm --prefix src/aitest/resources/panel test` | **23 passed / 7 failed**（安装 Chromium 后）。失败集中在 `guided.spec.ts`（执行后刷新读回执未闭合导致按钮 pending）、`evidence-view.spec.ts`、`panel.spec.ts` 与 `report-revision.spec.ts`；其中 `view-correlation` 的自动补读竞态已通过"不完整 run 视图不追补读"修复并转为通过。失败不是本批 Python/合同断言，但本批审阅页与授权列表仍缺浏览器级断言。 |

## 4. 已覆盖的行为

- `PlanProposal` 严格 JSON 解析、模板项覆盖、层级一致性、scope 排除理由、服务端生成依据摘要。
- 提案解析失败返回 `structured_output_invalid`，原始草稿仍保存；不自动修补。
- 向导 `review` 只返回预览、不写记录；`commit` 生成 `save_case` × N、`save_acceptance`、`confirm_basis` × N、`publish_plan`、`analyze_project`，随后 `prepare_run`、`register_run` 走真实核心合同。
- 普通写入通道只允许非 `HUMAN_ACTIONS`/非 `CHALLENGE_ACTIONS` 的动作；发布、依据确认、逐项授权仍走原生确认。
- 批量授权窗口一个窗口、每项独立点击；每项独立挑战/交互/确认/授权记录，不提供“全部确认”；重复点击已确认项幂等，越界索引拒绝。
- 面板新增全字段审阅、授权列表、`start_run` 按钮与视图刷新接线。

## 5. 未验证边界

- 真实模型供应方是否稳定返回符合 Schema 的 JSON；本批只用固定夹具。
- 真实 Windows Tk 批量窗口（本批用注入的 `prompt` 回调验证编排，未打开真实窗口）。
- 真实 Trae 中的审阅页、批量授权窗口、授权列表与自动执行；面板 Playwright 23 passed / 7 failed，失败集中在既有刷新/证据视图用例，本批新增审阅页与授权列表没有独立通过的浏览器断言。
- 真实核心进程通过命名管道接收 `native-batch/1` 信封的端到端路径；本批只测了 `dispatch_native_batch` 服务函数。
- 计划驱动自动执行到报告的全链路；本批只验证到 `register_run` 和单个授权。
- 多动作（>1 步）批量授权与 `waiting_authorization` 后续恢复。
- 运行中修订、取消/暂停、掉电恢复、跨机导出和平台上传。

## 6. 结论

本批证明：结构化提案、审阅/提交编排、普通写入白名单、批量授权的核心编排与 `start_run` 接线具备可运行的组件级实现。**不证明**真实宿主、真实模型、真实 Tk 窗口或端到端自动执行通过；`tests/acceptance/p1/status.json` 未改动。
