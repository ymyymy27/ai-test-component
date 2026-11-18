# 2026-11-18 真实 Trae 面板重跑

用当前提交构建的 VSIX 在真实 Trae CN 中重走面板主路径，并验证本轮新增的规则编辑入口。

## 环境

- Trae CN（本机已有安装，更新日志显示 v3.4.17）
- 扩展 `aitest-local.aitest-trae` 0.5.0
- VSIX：`integrations/trae/dist/aitest-trae.vsix`，sha256 `57ed292c8661fde8a6668c2c6de6317bbd789112724a743184d1b89bfebd64ee`
- 面板资产：`index-lnHucDoK.js`、`index-CRd6spLM.css`
- Python 3.11.9，工作空间 `%APPDATA%/aitest/workspaces/trae`
- 项目 `tutorial`（`proj_1c8be660cb0a`），任务“验证博客基本流程”

安装方式：同构建 VSIX 解包后覆盖 `~/.trae-cn/extensions/aitest-local.aitest-trae-0.5.0`；Trae 启动后加载的是新面板资产。没有使用市场安装。

## 已走通

1. `AI Test: 打开测试面板` 打开面板，加载新任务台账。
2. 设置页显示环境自检；项目规则区出现“新建规则版本”。
3. 新建规则“验收回归规则 1.0.0”，保存成功并显示在规则列表；绑定后显示“当前绑定”。
4. 从结果页点“再次运行”时，旧计划因规则绑定变化被判过期，页面显示“这次操作没有完成。重新生成计划后重试”。
5. 进入“调整本次范围”，生成新计划（计划 2，3 条用例），勾选“我已确认范围、环境和副作用”后开始。
6. 运行完成：3/3 通过，结论“通过”，底部显示“已完成 3 / 3 条检查”。
7. 打开报告：结论、验收项、用例明细、调用链、证据索引与导出入口正常显示。

运行记录：`run_e68a18cc3cc3`，`outcome=passed`，3 条用例均 `passed`，`latest_report_id=rep_f9bb62eb8255`，任务快照存在。

## 截图与记录

- `rules.png`：设置页规则列表与“当前绑定”。
- `stale-plan.png`：旧计划被拒绝的错误提示。
- `result.png`：新计划运行完成后的结果页。
- `report-new.png`：本次运行的报告。
- `report.png`：重跑前查看的旧报告，作为报告页加载证据。
- `run/`：运行目录副本；`report.json`：报告副本。

## 未覆盖

- 报告导出实际落盘、备份恢复、`link.assert` 真实宿主写入、`delivery.show` 真实宿主读取未在本批次重走。
- 这些仍按风险登记处理，不用本批次截图代替逐项验证。
