# CLI 级验收补充（2026-11-16）

> 真实 CLI 入口 + 真实文件工作空间 + 真实模型；**非 Trae 面板路径**。用于覆盖面板未触达的场景。

## 1 环境

- 工作空间：`%APPDATA%/aitest/workspaces/pilot`
- 项目：Flaskr `examples/tutorial@49943b5`
- 模型：`deepseek-chat`
- 运行方式：`python -m aitest.cli <command> --workspace ... --input ...`

## 2 场景与结果

| AC | 场景 | 实际结果 |
| --- | --- | --- |
| P1-AC14 | AI 判定 Mock 真实性 | 报告 `mock_judgement`：`flaskr → real`，依据引用测试与源码文件 |
| P1-AC15 | 失败运行生成 AI 分析 | `analysis.run` 返回 `generated`，含 observed / evidence_refs / candidate_causes / unverified / next_steps |
| P1-AC16 | 同一失败签名重复登记 | 第一次 `existing=false`；第二次返回同一 `issue_id`，`existing=true`，revision 2 |
| P1-AC17 | 按验收项重跑 | 新建 `run_c32d7a2761f6`，范围 1/3，`completed/passed`；运行数 3→4，旧运行保留 |
| P1-AC19 | 填写验收意见 | `report.opinion` 写入 `by=pilot`、时间、正文“冒烟通过” |
| P1-AC22 | 同项目第二个运行 | 第一次启动成功；第二次返回 `RUN_ACTIVE` |
| P1-AC24 | 备份与恢复 | 备份 20 个文件（不含证据）；恢复到空目录后 `workspace.json` 存在 |
| P1-AC26 | 模型不可用 | 空 `APPDATA` 且无密钥时，`plan.generate` 返回 `MODEL_UNAVAILABLE` |

## 3 证据

- `mock-report.json`：含 Mock 判定的报告；
- `analysis-run.json`：失败运行及四段分析；
- `issue.json`：签名去重后的问题记录；
- `rerun.json`：重跑运行元数据；
- `opinion-report.json`：填写验收意见后的报告。

原始工作空间路径见上；报告与运行目录永久保留。

## 4 未覆盖

- Trae 面板中的失败分析、验收意见、重跑入口（本次走 CLI）；
- 链路日志/数据库；
- 磁盘不足、服务启动失败；
- AC23 中断接管单独记录。
