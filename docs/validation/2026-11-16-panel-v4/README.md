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
- 本轮补充（2026-11-16，无新增截图）：
  - `AI 失败分析` 按 `analysis.reason` 显示未生成原因：`no_failures`（本次没有失败，不需要生成分析）、`model_missing`（模型未配置或不可用）、`model_invalid`（模型输出不合格，已隐藏）、`model_error`（模型调用失败，可稍后重试），其他情况只说“分析未生成”，不再统一归因为模型不可用。
  - `再测一次` 遇到 `PLAN_STALE` 时，按同一 task/scope 自动调用 `plan.generate` 生成新计划，再用新 `plan_id` 启动一次；成功后提示“源码或配置已变化，已重新生成计划”，第二次仍失败显示真实错误，不循环重试。
  - `新目标` 或首次生成目标时先调用 `task.list`，同 title+goal 的任务直接复用，避免重复任务堆积。
  - 演示宿主 `panel/src/mock/host.ts` 的版本号改为 0.5.0，`task.suggest`/`task.add`/`plan.generate` 演示数据与后端新字段对齐。
- 已验证：`panel npm test` 54 项通过、`panel npm run build` 与 `integrations/trae npm run build` 通过；mock 截图见上。
- 真实 Trae：已安装 0.5.0 VSIX，打开面板直接回放上次结果；`再测一次` 复用计划并完成运行（Flask 服务未启动，结果按 blocked 记“未完成”）；`新目标` 生成建议测试单（`trae-proposal.png`）。本轮新行为（`analysis.reason` 文案、`PLAN_STALE` 自动重生成、任务复用）未在真实 Trae 重新安装重验。
- 未验证：真实 Trae 中的历史抽屉回放、调整抽屉保存、键盘操作；未启动 Flask 服务时的 blocked 结果不代表产品失败。
