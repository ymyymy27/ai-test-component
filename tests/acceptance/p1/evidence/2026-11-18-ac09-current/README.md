# P1-AC09 当前代码重跑（2026-11-18）

用当前工作树和真实 CLI 重跑“L1 构建失败只阻塞依赖构建的用例”。

## 环境

- Python 3.11.9
- ai-test-component 0.5.0
- 工作空间：临时目录 `%TEMP%/aitest-ac09-current/ws`
- 模型：不需要（计划显式提供 cases）
- 命令：`python -m aitest.cli <command> --workspace <ws> --input <json>`

## 场景

项目配置 `l1.build = python -c "import sys; sys.exit(1)"`；任务有 3 条可观察验收项；计划包含：

1. `depends_on=build` 的用例；
2. 独立用例一；
3. 独立用例二。

## 结果

- `run.status`：`outcome=failed`，运行 `completed`。
- `l1.build.ok=false`，`exit_code=1`。
- 依赖构建的用例 `blocked`，message 为“L1 检查未通过”。
- 两个独立用例 `passed`。

原始输出在 `ac09-run-status.json`、`ac09-summary.json`；运行目录完整复制在 `run/`，包含 `plan.json`、`task.json`、`run.json` 和 `evidence/l1-build.log`。
