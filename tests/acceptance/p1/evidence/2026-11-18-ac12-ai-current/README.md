# P1-AC12 AI 生成 Playwright 脚本（2026-11-18）

用真实模型生成 Playwright 脚本并执行，验证脚本落盘、截图证据进入运行记录。

## 环境

- Python 3.11.9
- Python Playwright 1.59.0（Chromium 已安装）
- 模型：`deepseek-chat`（真实调用）
- ai-test-component 0.5.0
- 工作空间：临时目录 `%TEMP%/aitest-ac12-current/ws`
- 被测页：本机 `http://127.0.0.1:5098/`，由项目配置 `services.web.start` 启动

## 场景

任务要求“首页显示订单列表标题，点击查看详情后显示订单详情”。`plan.generate` 不提供 `cases`，由模型生成计划；模型返回两条 `script:` 用例和 Python Playwright 脚本。

## 结果

- `plan.generate`：`ai_generated=true`，2 条用例，依赖 `playwright`。
- 运行前脚本写入 `generated/test_home_order_list_title.py` 与 `generated/test_order_detail_after_click.py`，运行目录保留副本。
- 两条用例均 `passed`；运行结论 `passed`。
- 每条用例的 `evidence_refs` 同时包含执行日志和截图：
  - `evidence/case_aad745d6cc4a-screenshot-0-home_order_list_title.png`
  - `evidence/case_c4c22bef04e4-screenshot-0-order_detail_after_click.png`

原始输出在 `ac12-plan-generate.json`、`ac12-run-status.json`、`ac12-summary.json`；`ac12-plan.json` 保留模型生成的完整脚本，`run/` 是运行目录副本。
