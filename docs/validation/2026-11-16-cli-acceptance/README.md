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
| P1-AC01 | 根目录与子目录重叠登记 | 第二次登记返回 `INVALID_REQUEST` |
| P1-AC04 | 交付说明缺运行方式与提交 | `delivery.save` 返回 `complete=false` |
| P1-AC09 | L1 失败阻塞 build 依赖 | L1 `failed`，L2 `blocked` |
| P1-AC10 | L2 模块 pytest | 在项目 venv 运行 `pytest tests/test_blog.py` 通过 |
| P1-AC20 | `gh` 不可用 | 返回 `GITHUB_UNAVAILABLE`，本地数据仍可用 |
| P1-AC21 | 缺请求 ID | `link.import` 返回 request_id/log/db 三个 gap |
| P1-AC23 | worker 被杀 | 心跳超时且 PID 不在，状态转 `interrupted` |
| P1-AC03 | AI 提取验收项 | 提取 4 条，不可观察项标 pending 并排除 |
| P1-AC05 | 两个规则版本 | 旧计划保留 v1，新计划使用 v2 |
| P1-AC11 | 接口成功但库无记录 | 用例 failed，运行结论 failed |
| P1-AC25 | 环境缺项与分级修复 | doctor 自动重建目录/写探针；高风险只给 winget 命令；AI 给出解释 |

## 3 证据

- `mock-report.json`：含 Mock 判定的报告；
- `analysis-run.json`：失败运行及四段分析；
- `issue.json`：签名去重后的问题记录；
- `rerun.json`：重跑运行元数据；
- `opinion-report.json`：填写验收意见后的报告；
- `ac03-task-extract.json`、`ac05-rules-versions.json`、`ac11-run.json`、`ac25-doctor-repair.json`、`ac01-overlap.json`、`ac04-delivery-incomplete.json`、`ac09-run.json`、`ac10-pytest-run.json`、`ac20-gh-missing.json`、`ac21-link-gaps.json`、`ac23-interrupt.json`。

原始工作空间路径见上；报告与运行目录永久保留。

## 4 失败路径实测

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 私有/不存在仓库 | `GITHUB_UNAVAILABLE` | `github-private.json` |
| 模型返回非 JSON | `MODEL_OUTPUT_INVALID` | `model-invalid-output.json` |
| 真实日志 + 只读查询 | 2 个日志节点 + 1 个 DB 节点，无缺口 | `link-real.json` |
| 模型超时 | `MODEL_UNAVAILABLE` | `model-timeout.json` |
| 服务启动失败 | 运行 completed，依赖服务用例 `blocked` | `service-start-failure.json` |

## 5 未覆盖

- Trae 面板中的失败分析、验收意见、重跑入口（本次走 CLI）；
- 磁盘不足；
- 端口被占用（未构造）。
