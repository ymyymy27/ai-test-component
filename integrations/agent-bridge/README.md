# 跨智能体工具协议薄适配器（Codex / Google Antigravity / Claude Code）

本目录提供独立于核心 Python 包（`src/aitest/`）的超薄外部协议适配层与标准 `SKILL.md`：

- [`server.py`](server.py)：纯 Python 3.11+ 标准库实现的 stdio JSON-RPC 2.0 适配器（兼容 `Content-Length` 帧与 NDJSON 帧），全部动作通过子进程调用 `python -m aitest.cli` 执行。
- [`SKILL.md`](SKILL.md)：面向 Codex、Google Antigravity、Claude Code 等智能体的标准流程技能说明。

> **架构边界**：测试核心（`src/aitest/`）严格保持零绑定纯 CLI 形态，**不做**常驻守护进程，也**不内置**任何 MCP 服务端或数据库。本适配器仅做外部只读投影与安全转发，不能绕过人工确认或重算四态结论。

## 客户端接入配置示例（`mcpServers`）

在 Codex、Google Antigravity 或 Claude Code 的客户端配置中加入：

```json
{
  "mcpServers": {
    "aitest": {
      "command": "E:\\ai-test-component\\.venv\\Scripts\\python.exe",
      "args": [
        "E:\\ai-test-component\\integrations\\agent-bridge\\server.py",
        "--workspace",
        "C:\\Users\\yourname\\AppData\\Roaming\\aitest\\workspaces\\default"
      ]
    }
  }
}
```

如果已将 `ai-test-component` 安装到全局或特定 venv，将 `command` 替换为对应 Python 解释器路径即可。

## 暴露工具清单

| 工具名 | 对应 CLI 命令 | 说明 |
| --- | --- | --- |
| `aitest_doctor` | `doctor` | 检查环境与工作空间状态 |
| `aitest_project_list` | `project.list` | 列出已登记项目 |
| `aitest_project_map` | `project.map` / `project.map.refresh` | 获取 4 层代码图、跨层冲突与 Git 影响面 |
| `aitest_task_list` | `task.list` | 列出任务与阶段 |
| `aitest_plan_get` | `plan.show`（支持按 `task_id` 自动追踪） | 查看测试计划详情与覆盖 |
| `aitest_run_status` | `run.status`（支持默认查最新 run） | 查看运行进展、安检门、硬探针与 Token 消耗 |
| `aitest_run_start` | `run.start --confirm` | 启动运行（强制要求 `human_confirmed=true` 与真实人类 `confirmed_by`） |
| `aitest_report_summary` | `report.show`（支持按 `run_id`/最新运行自动追踪） | 读取四态结论、S/M 覆盖、三档可信度与铁证缺陷 |
| `aitest_issues_list` | `issue.list` | 读取去重后的问题账本与证据引用 |
| `aitest_conventions_get` | `project.config` (`get`) | 读取运行配置与错题本正常约定 |
