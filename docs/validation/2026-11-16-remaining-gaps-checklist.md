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

## I 验证缺口

- [x] I1 真实 Trae 设置页保存、证据页、问题处置页
- [~] I2 GitHub Actions 远端运行：本地等价命令通过；未推送，远端未运行

## 已完成

- [x] 修改计划 53 项清单
- [x] 26 条验收全部 verified
- [x] 共享前端面板与真实 Trae 主流程
