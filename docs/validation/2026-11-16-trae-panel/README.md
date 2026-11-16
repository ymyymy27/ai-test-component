# 真实 Trae 面板验收：Flaskr（2026-11-16）

> 真实 Trae CN 1.107.1 + 本地扩展 0.5.0 + 真实模型 + 真实 Flaskr 服务 + Playwright Chromium。

## 1 环境

| 项 | 值 |
| --- | --- |
| 宿主 | Trae CN 1.107.1（`E:/Trae CN`） |
| 扩展 | `aitest-local.aitest-trae` 0.5.0（VSIX 安装） |
| 工作空间 | `%APPDATA%/aitest/workspaces/trae` |
| Python | 项目 `.venv` 3.11.9 |
| 项目 | `pallets/flask` `examples/tutorial`，提交 `49943b5` |
| 模型 | `deepseek-chat` |
| 服务 | `flask --app flaskr run --port 5000 --no-reload`（人工启动） |

## 2 操作步骤（全部在 Trae 面板完成）

1. 命令面板执行 `AI Test: 打开测试面板`；
2. 环境自检：workspace、python、package、model、github、proxy 均为 ok；
3. 新建项目：`E:/pilot/flask/examples/tutorial`，登记为 `tutorial`；
4. 目标候选：无信号时显示通用目标“验证项目能否启动并跑通主流程”，允许自定义；
5. 输入自定义目标与三条验收项，创建任务 `task_afb72e434cc5`；
6. 范围：全部适用，3/3；
7. 生成计划：模型生成 6 条用例并自动发布 `plan_4f7138e7a4c`；
8. 准备执行 → 确认并执行，确认人 `cli`，运行 `run_29612b09066a`；
9. 运行完成：`completed`，业务结论 `passed`，6 条用例全部 `passed`；
10. 生成报告 `rep_dc77dc880d00`，导出 `reports/rep_dc77dc880d00.md`（298 字节）。

## 3 证据

- `trae-panel-result.png`：面板运行结果与导出成功提示；
- `plan.json`：6 条用例与范围；
- `run.json`：运行状态、确认人、用例结果；
- `report.json`：结论 `passed`、计数 3/3、摘要；
- `report.md`：导出报告。

## 4 对应验收场景

| AC | 结论 | 依据 |
| --- | --- | --- |
| P1-AC02 | verified | 无信号显示通用目标，自定义输入可用 |
| P1-AC06 | verified | 面板显示范围 3/3 |
| P1-AC07 | verified | AI 生成 6 条用例并自动发布 |
| P1-AC08 | verified | 必须“确认并执行”后才启动运行，确认人记录为 `cli` |
| P1-AC12 | verified | Playwright UI 用例在运行中通过 |
| P1-AC13 | verified | 接口/命令用例完成业务闭环并通过 |
| P1-AC18 | verified | 报告结论 `passed`，计数与范围一致 |

## 5 未覆盖

- 真实 GitHub 关联、链路日志/数据库；
- 中断接管、取消、磁盘不足；
- 失败分析与问题自动生成的真实样本；
- 验收意见填写与导出；
- 其他 AC 仍为 `untested`。
