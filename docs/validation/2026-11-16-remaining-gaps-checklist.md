# 剩余功能缺口清单（2026-11-16）

> 依据：修改计划 §4.2 / §4.4 / §4.6 / §5.6 与 FR 追踪矩阵的“部分/缺失”项。
> 状态：`[x]` 完成并验证；`[~]` 部分；`[ ]` 未完成。

## H 功能缺口

- [x] H1 AI 起草交付说明：模型从提交 diff、README、启动脚本生成，用户可改后保存
- [x] H2 模板运行时：加载模板预设、AI 逐项处置、不适用的写理由、结果进计划/报告
- [x] H3 Python 自动准备：缺失时下载便携 Python、建 venv、离线安装自带 wheel（`integrations/trae/README.md`；真实 GitHub 下载未联网实测）
- [x] H4 AI 起草模块与关系：扫描项目后给模块/依赖建议，用户确认后写入
- [x] H5 链路定位展示：首错节点、上下游、代码位置或未知范围，面板可见
- [x] H6 面板输入精简：项目页交付字段收进抽屉、向导按需输入、设置页 JSON 收进高级配置；正常路径不再出现常驻文本框（`panel npm test` 50 项、`panel npm run build`、`integrations/trae npm run build`、静态 mock 截图 `docs/validation/2026-11-16-panel-v3/`；真实 Trae 本轮未重新安装验证）
- [x] H7 单页测试单：建议 / 运行 / 结果在同一张测试单内切换，历史与调整改为右侧抽屉；删除四步向导、项目仪表盘、运行页、结果页与独立证据/问题页；开始测试一次点击、再测一次复用最近计划、历史回放、内联证据与问题（`panel npm test` 43 项、`panel npm run build`、`integrations/trae npm run build`、静态 mock 截图 `docs/validation/2026-11-16-panel-v4/`；真实 Trae 本轮未重新安装，未在真实 webview 重验）

- [x] H8 多轮与大项目实跑：Flaskr 连续 3 次通过；requests 修正 AI 脚本后连续 3 次通过
- [x] H9 同类问题修复：分析未生成按 `analysis.reason` 区分文案；`PLAN_STALE` 自动重生成计划且只重试一次；`task.list` 同 title+goal 任务复用；演示宿主版本 0.5.0（`panel npm test` 54 项、`panel npm run build`、`integrations/trae npm run build`；真实 Trae 本轮未重新安装重验）
- [x] H10 通用冒烟快速检查：不再默认跑全量 pytest，改为入口导入 + pytest 收集 + 最小测试模块，支持 `l1.smoke_command` 覆盖；requests 两次 3/3 通过，真实 Trae 面板显示“项目检查 3/3 通过”并完成再测（`docs/validation/2026-11-17-trae-smoke/`）
- [x] H11 项目级结果与不需要分析的文案：没有验收项时显示检查通过数而非 0/0；通过运行显示“本次没有失败，不需要生成分析”（`panel npm test` 59 项，真实 Trae 截图 `analysis.png`）
- [x] H12 前端全功能浏览器回归：无宿主/无项目/建议单/换目标/调整抽屉/交付/模板/模块建议/运行/取消/结果/证据/问题/报告/意见/历史/项目详情/设置/窄幅/键盘共 14 组通过；取消文案、结果页分析传参、webview 资源缓存与演示宿主一致性修复；真实 Trae 验证最新结果与调整抽屉（`panel npm run test:e2e`，`docs/validation/2026-11-17-frontend-full/`）
- [x] H13 前端 AITEST 控制台重设计：全宽无卡片、`AITEST` 字标与 `READY`/状态线、铜色信号、`RANGE` 刻度与 `CHECKS` 编号清单、结论大号记分牌与 `[01]…` 分区、折叠失败行、分段进度、`INSPECTOR` 抽屉、`Ctrl+Enter` 启动、人工记录写回；真实 Trae 白名单补齐 `task.update/module.add/delivery.save/run.manual`；最终控制台在真实 Trae 完全重启后确认（`panel npm test` 59 项、扩展 48 项、浏览器 14 组）

## I 验证缺口

- [x] I1 真实 Trae 设置页保存、证据页、问题处置页
- [x] I3 真实 Trae 冒烟结果、同计划再测、项目检查与分析文案（`docs/validation/2026-11-17-trae-smoke/`）
- [~] I2 GitHub Actions 远端运行：本地等价命令通过；未推送，远端未运行

## 已完成

- [x] 修改计划 53 项清单
- [x] 26 条验收全部 verified
- [x] 共享前端面板与真实 Trae 主流程
