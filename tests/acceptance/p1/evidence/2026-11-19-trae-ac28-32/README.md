# 2026-11-19 真实 Trae AC28—32 走查

环境：Trae CN 3.4.22、扩展 `aitest-local.aitest-trae@0.5.0`、Python 3.11.9、工作空间 `%APPDATA%/aitest/workspaces/trae`。

| AC | 证据 | 观察 |
| --- | --- | --- |
| P1-AC28 | `ac28-quick-check.json` | 快速检查 `run_e29920e19d31`（`project_check`，completed）；只生成 L1 结果，不进入验收项覆盖。 |
| P1-AC29 | `ac29-auto-plan.png` | 新建任务页显示候选目标与预计范围；选择候选后“按预计范围生成计划”自动创建任务并生成计划，计划页“预期未证实 0 条”。 |
| P1-AC30 | `ac30-scope-modes.png` | 范围页提供推荐范围/自己勾选/全量三种模式，可按业务功能整组勾选或逐项调整；范围变化生成新计划，旧计划保留快照。 |
| P1-AC31 | `ac31-map-insufficient.png`、`ac31-map-insufficient.json` | 项目 `aitest-ac02-demo` 无业务功能/可测项时，范围页提示“还没有识别到可测业务功能”，提供改用目标测试与快速检查，不把未识别内容计入通过。 |
| P1-AC32 | `ac32-export.json` | 报告页导出 Markdown 生成 `exports/rep_1dcbcd4af67d.md`（5846 bytes、125 行、sha256 `a19c0894ccb05a7e…`），无凭据字段；返回路径与摘要。 |

同批次还验证了：候选自动计划生成计划第 1 版（11 项/11 条检查，0 条预期未证实）；一次 6 项运行得到 2 通过、0 失败、4 未完成；报告 `rep_1dcbcd4af67d` 第 1 版由 CLI 生成并在面板显示。

未覆盖：真实 GitHub 成功路径、真实宿主备份恢复、`link.assert`/`delivery.show` 真实读写、完整宿主闭环与试点项目。
