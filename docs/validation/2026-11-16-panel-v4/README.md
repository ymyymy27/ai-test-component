# 共享面板 v4 截图（2026-11-16）

- 用途：单页测试单重构的主路径记录；四步向导、项目仪表盘页、运行页、结果页与独立证据/问题页已删除，历史与调整改为右侧抽屉。
- 采集：`cd panel && npm run dev`，另开终端 `npm run screenshot -- ../docs/validation/2026-11-16-panel-v4`；Playwright + 本机 Chrome，640×900，减少动效。
- `proposal.png`：建议测试单——第一条候选为目标、一句依据、8 条验收标准默认全选、范围 8/8、环境/副作用/耗时，一个“开始测试”主按钮与“换一个目标/调整”。
- `adjust-drawer.png`：调整抽屉——验收项勾选/编辑/移除/补充、范围全部或自选、模块标记、交付说明 AI 起草与字段、模板处置、模块建议。
- `running.png`：运行相位——同一张单子顶部 4/6 进度、当前用例、所选验收项的实时状态、实时证据入口与取消。
- `result.png`：结果相位——结论失败、覆盖 3/8 与未覆盖说明、失败用例内联证据卡、AI 失败分析、内联问题处置、导出报告与验收意见。
- `history-drawer.png`：历史时间线，当前运行高亮；点一项在当前视图回放该次结果。
- `settings.png`：设置主视图仍只有环境/模型/工作空间/GitHub 事实，服务与链路等 JSON 收在“高级配置”。
- `proposal-420.png`：420px 窄幅下建议测试单为单列、主操作可达。
- 已验证：`panel npm test` 43 项通过、`panel npm run build` 与 `integrations/trae npm run build` 通过；mock 截图见上。
- 真实 Trae：已安装 0.5.0 VSIX，打开面板直接回放上次结果；`再测一次` 复用计划并完成运行（Flask 服务未启动，结果按 blocked 记“未完成”）；`新目标` 生成建议测试单（`trae-proposal.png`）。
- 未验证：真实 Trae 中的历史抽屉回放、调整抽屉保存、键盘操作；未启动 Flask 服务时的 blocked 结果不代表产品失败。
