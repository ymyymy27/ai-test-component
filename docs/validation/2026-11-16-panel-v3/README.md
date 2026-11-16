# 共享面板 v3 截图（2026-11-16）

- 用途：信息密度重构后的主路径记录；表单墙已移除，正常路径最多一个按需文本框。
- 采集：`cd panel && npm run dev`，另开终端 `npm run screenshot -- ../docs/validation/2026-11-16-panel-v3`；Playwright + 本机 Chrome，640×900，减少动效。
- `project.png`：项目概况四格（模块 / 入口 / 最近运行 / 上次结论）、交付说明 chips、“更多”里折叠的模块建议。
- `project-drawer.png`：点“查看 / 编辑”后的交付字段抽屉；主视图本身没有输入框。
- `wizard-step1.png`：候选卡片网格 + 一行“或自己写一句”；点卡片自动进入下一步。
- `wizard-step2.png`：验收项开关列表，默认全部纳入；底部“+ 添加一条”按需展开一行输入。
- `wizard-step4.png`：只读摘要（范围 8/8、6 条用例、本机）、模板处置与单个“开始测试”主按钮。
- `settings.png`：主视图只有环境自检、模型、工作空间与 GitHub；服务/链路等收进“高级配置”。
- `settings-advanced.png`：高级配置一次只编辑一个分组，单个 JSON 框 + 保存按钮。
- `project-420.png`：420px 窄幅下项目页单列可读、主操作可达。
- 已验证：`panel npm test` 50 项通过、`panel npm run build` 与 `integrations/trae npm run build` 通过；截图来自开发模式静态 mock。
- 未验证：本轮未在真实 Trae webview 重走主路径；真实 CLI 数据下的抽屉与高级配置待下次宿主验收。
