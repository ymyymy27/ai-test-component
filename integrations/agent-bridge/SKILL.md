---
name: aitest-local-qa
description: 当需要通过本地 AI Test Component（aitest）分析仓库全栈代码图、基于本地 Git 差异定位受影响范围、核查环境安检门、启动人类确认的测试运行或读取铁证缺陷修复任务包时使用本技能。
---

# AI 测试插件跨智能体协同技能（Codex / Google Antigravity / Claude Code）

本技能指导外部编码智能体通过薄适配器 [`server.py`](file:///e:/ai-test-component/integrations/agent-bridge/server.py) 或 CLI（`python -m aitest.cli`）安全调用本地零绑定测试内核，严禁越权修改工作空间 JSON 或篡改判定结果。

## 推荐调用顺序

1. **环境就绪检查**：先调用 `aitest_doctor` 核验本地 Python 运行时、包版本、工作空间权限与模型/`gh` 状态；若无项目，提示用户在面板或 CLI 执行 `project.add`。
2. **定位代码图与 Git 影响面**：调用 `aitest_project_list` 拿到目标 `project_id`，再调 `aitest_project_map`（需要全量重扫时加 `refresh=true`），获取：
   - 4 层全栈代码图（前端 → 接口 → 后端模块 → 数据存储）；
   - 跨层契约与静态资源冲突清单（`conflicts`）；
   - 基于本地 Git Diff 反推的受影响模块/路由（`impact`）。
3. **核对测试范围与错题本**：调用 `aitest_task_list`、`aitest_plan_get` 与 `aitest_conventions_get`，向用户确认要跑的计划 ID 与验收项范围（避免误杀项目已知正常约定）。
4. **人工显式确认后再启动运行**：
   - 将计划修订号、验收项范围、依赖服务与预期副作用清楚呈现给人类用户；
   - **只有获得人类明确答复同意并给出确认人姓名/标识后**，才可调用 `aitest_run_start` 并传入 `human_confirmed=true` 与真实 `confirmed_by`；
   - 严禁填写 `"ai"`、`"agent"`、`"assistant"`、`"bot"` 或代人类自动确认。
5. **读取结论与铁证缺陷**：运行完成后调用 `aitest_run_status`、`aitest_report_summary` 与 `aitest_issues_list`，优先处理三档可信度为 **铁证缺陷（`confirmed`）** 且附带源码行号、窗口期后台堆栈日志或最小复现命令的问题。

## 不可逾越的业务与安全边界

- **四态结论不可篡改**：业务结论只有 `passed`、`failed`、`incomplete`、`not_applicable`，所有结论严格以核心计算结果为准，未知/阻塞/跳过绝不能包装成“通过”。
- **只读与脱敏**：所有模型上下文和工具响应已做凭据脱敏；严禁把 token、password、secret 写入项目配置或报告摘要。
- **范围完整性**：当 `selected < applicable`（部分选测）时，只能汇报“本范围通过”，不得总结为“项目全部通过”。
