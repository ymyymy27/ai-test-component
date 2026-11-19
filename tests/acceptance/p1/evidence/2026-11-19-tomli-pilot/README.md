# 2026-11-19 tomli 真实项目试点

项目：https://github.com/hukkin/tomli，commit `43a86ade8b795c6ef9b4ec53372a607cd1ed426f`。
环境：Windows 11 x64、Python 3.11.9、pytest 9.0.3、Trae CN 3.4.22、真实 DeepSeek 模型。

## 基线

`PYTHONPATH=src python -m pytest -q`：**17 passed, 1 skipped, 744 subtests passed**（见 `baseline.txt`）。

## 面板流程

- 在真实 Trae 面板登记项目 `proj_640edce820e6`，模型分析得到 1 个业务功能、5 个可测项；
- 选择候选“TOML 解析核心功能（loads/load）”，自动创建任务 `task_3d5f24ea1f6b` 与计划 `plan_00c09a5259f1`；
- 计划 5 项/5 条检查，**预期未证实 0 条**；
- 第一次运行 `run_a7c4b5084744`：0 通过、0 失败、5 未完成；5 个脚本均 `ModuleNotFoundError: No module named 'tomli'`；
- 根因：worker 未把项目的 `src/` 加入 `PYTHONPATH`，已修复并加回归测试；
- 重跑 `run_82a180f8f4f0`：**5 通过、0 失败、0 未完成**，结论“通过”；
- 报告 `rep_633b7759ac67` 已导出 Markdown（`rep_633b7759ac67.md`）。

## 证据

- `baseline.txt`：项目原生测试基线；
- `environment.json`：项目 commit、Python/pytest、run/report/plan/task ID；
- `report.json` / `rep_633b7759ac67.md`：CLI 报告与导出 Markdown；
- `run.json`：重跑运行记录；
- `generated/`：5 个实际执行的验证脚本及 `index.json`；
- `pilot-report.png`：真实 Trae 报告页截图。

未覆盖：完整测试套件逐项、真实 GitHub、备份恢复、并发/规模、故障注入。
