# 轻量插件本地冒烟（2026-11-16）

范围：真实 CLI 进程 + 临时工作空间；**不是真实 Trae，也不代表验收通过**。

| 步骤 | 结果 |
| --- | --- |
| project.add | proj_5c5bd0bc1aab |
| task.add | task_b2fec12560fe，验收项 ai_b593c454d3c6 |
| plan.generate | plan_f57e0d185f8f，状态 published |
| run.start | run_de038a98bdcb |
| run.status | completed，结论 passed |
| report.generate | rep_14b99bfbed59，结论 passed |
| report.export | C:\Users\y'm'y\AppData\Local\Temp\aitest-smoke-a1cp67il\report.md |
| backup.create | 7 个文件，含证据=False |

## 未覆盖

- 真实 Trae 安装、面板点击与确认；
- 试点项目的 L1/L2/L3、GitHub 与链路；
- 模型 API、Mock 判定、AI 分析；
- 中断接管、磁盘不足、服务启动失败。
