# 共享面板 v2 截图（2026-11-16）

- 用途：`panel/` 从零重构后的视觉记录。
- `panel.png`：640×900，开发模式静态 mock（`?mock=result`），演示结果页；结论 `failed`、覆盖 3/8 均来自 mock 字段。
- `trae-wizard.png`：真实 Trae CN 1.107.1 webview，加载共享面板后的“新建测试”第一步；数据来自真实 CLI（tutorial 项目）。
- 复现 mock 图：`cd panel && npm install && npm run dev`，另开终端 `npm run screenshot -- ../docs/validation/2026-11-16-panel-v2/panel.png`。
- 真实 Trae 图：VSIX 安装后命令面板执行 `AI Test: 打开测试面板`。
- 已验证：面板在真实 Trae 中加载、项目页与向导第一步渲染、环境状态正常。
- 已验证：真实 Trae 中走完四步向导、生成计划、开始测试、2/2 用例通过、结果页与 Markdown 导出（`trae-result.png`）。
- 未验证：真实 Trae 中的设置页保存与问题处置操作。
