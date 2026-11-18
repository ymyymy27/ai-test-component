# G3b 补齐 P1-AC04/06/07/12 后重跑（2026-11-16 19:35 批次）

> 目的：补齐 [G3 批次](../2026-11-16-g3-rerun/README.md) 中仍为 `not_verified` 的 4 条验收，
> 并修复“服务配置形状”问题；本页记录真实执行结果与证据索引，不替代原始输出。
> 原始命令响应、`summary.json` 与本目录的截图都在本目录；运行记录永久保存在
> `%APPDATA%/aitest/workspaces/g3b/projects/<project_id>/runs/<run_id>/`。

## 1 环境

| 项 | 值 |
| --- | --- |
| 代码 | `E:/ai-test-component`，`重构` 分支 `3f0ba0c` + 本轮未提交改动 |
| Python | 3.11.9（`E:/Python311`） |
| Python Playwright | 1.59.0，Chromium headless shell 147.0.7727.15（`python -m playwright install chromium`） |
| 模型 | `deepseek-chat`（用户配置，真实调用；AC07 使用） |
| CLI | `python -m aitest.cli <command> --workspace <dir> --input <json>` |
| 工作空间 | `%APPDATA%/aitest/workspaces/g3b`（本轮新建） |
| 试点项目 | `docs/validation/2026-11-16-g3b/project`（静态页 `index.html`） |
| 重跑脚本 | `python docs/validation/2026-11-16-g3b/run_recheck.py` |

`run_recheck.py` 逐条断言；任一条失败即以非 0 退出，不会写 `summary.json`。

## 2 逐条结果

| AC | 结果 | 证据 | 实际 |
| --- | --- | --- | --- |
| P1-AC04 | verified | `ac04-delivery.json`、`ac04-run-status.json` | `delivery.save` 缺运行命令与提交时 `complete=false`；`run.start` 后 `depends_on=build` 用例 `blocked`，message 逐字为“交付信息不完整：缺运行方式或版本”；无依赖用例 `passed`，结论 `incomplete` |
| P1-AC06 | verified | `ac06-report.json`、`ac06-run-status.json` | `S/M=1/3`；`report.generate.uncovered_items` 与 `run.status.uncovered_items` 都返回未勾选的 2 项（`ai_…：库存扣减一次`、`ai_…：订单可以取消`）；结论 `passed` 只描述所选范围 |
| P1-AC07 | verified | `ac07-plan.json`、`ac07-plan-show.json` | 真实模型为 2 个验收项生成 2 条用例并自动发布；`plan.show` 中两条 `expectation_source` 均等于对应验收项原文，`expectation_verified=true`，`unverified_cases=[]`（模型给出不一致或空引用时的自动对齐由单元测试覆盖） |
| P1-AC12 | verified | `ac12-run-status.json`、`ac12-0-home.png`、`ac12-1-detail.png` | 真实 Chromium 打开临时 HTTP 静态页并截图；`test-results/home.png` 与显式 `page.screenshot(path=ui/detail.png)` 都复制到 `evidence/` 并进入用例 `evidence_refs`；脚本落盘在 `runs/<id>/generated/shot.py`；用例 `passed` |

`summary.json` 汇总了四个运行 ID、未覆盖清单、预期引用与截图字节数。
AC12 截图从运行目录复制到本目录（`ac12-0-home.png`、`ac12-1-detail.png`），
两张均为 7151 字节的 PNG，内容为静态页“订单列表 / 订单 A-1001 / A-1002”。

## 3 服务配置形状

`worker._services` 现在同时接受：

- 单服务：`{"start": ..., "health": ..., "stop": ...}`；
- 映射：`{"服务名": {...}, ...}`，取存储文件中的第一个服务，并把服务名写入 `run.json.service_name`。

`service-config.json`、`service-run-status.json` 是真实 CLI 验证：配置 `api`（健康）与
`worker`（不可达）两项，运行选中的是 `api`，`service_name="api"`、
`service_started=false`，第二个服务的启动标记没有生成。面板设置页提示已同步为
“单服务：{ start, health, stop, ... }；或 服务名 → 配置，取第一个”。

注意：项目配置按字典序写入 JSON，因此映射形状的“第一个”是存储文件中的字典序第一个；
只配置一个服务时没有歧义，多服务且需要指定默认项时当前没有单独字段。

## 4 字段语义变化

- `report.uncovered_items` 由“已选但未通过”改为“当前任务适用验收项中未被选中”的
  `id：text` 列表，与[业务方案](../../../../../docs/项目文档/一期/02-需求/01-业务方案.md)第 4 节
  “未选验收项不属于本次范围”和[功能清单](../../../../../docs/项目文档/一期/02-需求/03-功能清单.md)
  第 3 节一致；全部覆盖时为空数组。
- `run.status` 新增同义只读字段 `uncovered_items`，供面板结果页展示；面板不重算结论。
- 因此 G3 批次里按旧定义生成的 `uncovered_items`（如 AC18 的 3 个“已选未通过” id）
  只代表当时代码输出；AC18 登记中的实际描述已改为按计数与 `not_run` 说明，结论未变。

## 5 新增验证

- Python：服务形状单元 + worker 映射集成、交付不完整阻塞、`test-results` 与显式
  `page.screenshot` 截图收集、真实 Playwright 截图（环境缺浏览器时 skip）；
  报告未覆盖清单与 `run.status`、模型预期引用自动对齐。
- 面板：结果页/确认页未覆盖清单（最多 5 条 + 剩余数量）、设置页服务形状提示；
  `npm test` 42 项通过。
- 未验证：本轮 AC12 使用手写 Playwright 脚本（计划允许“AI/手写”），没有让模型临时生成
  可执行的 Playwright 脚本；AC07 仅验证计划的预期引用与发布，没有执行模型生成的
  JS 脚本。

## 6 检查结果

| 检查 | 结果 |
| --- | --- |
| `python -m pytest -q` | 178 个节点全部通过（含真实 Playwright 用例；浏览器缺失时才 skip） |
| `python -m ruff check .` | 通过 |
| `.venv/Scripts/mypy.exe` | 42 个源文件，无问题 |
| `npm test`（`panel/`） | 7 个文件 / 42 项通过 |
| `npx vue-tsc --noEmit`（`panel/`） | 退出 0 |
| `tests/unit/test_acceptance_status.py`、`test_acceptance_status_phases.py` | 31 项通过 |
| `python scripts/check_docs_facts.py` | 3 处断言一致（34 模块 / 6,471 行 / 178 节点） |
