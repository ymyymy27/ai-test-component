# 试点项目验收：Flaskr（2026-11-16）

> 真实 CLI + 真实模型 + 真实服务 + 真实浏览器；**不是真实 Trae 面板验收**。所有结论来自本次运行，未手工修改。

## 1 环境

| 项 | 值 |
| --- | --- |
| 项目 | `pallets/flask` 的 `examples/tutorial`（Flaskr 教程博客） |
| 提交 | `49943b5` |
| Python | 3.11.9（项目 `.venv`） |
| Flask | 3.1.3 |
| Playwright | 已安装 Chromium（项目 `.venv`） |
| 模型 | `deepseek-chat`（DeepSeek 兼容 API） |
| 工作空间 | `%APPDATA%/aitest/workspaces/pilot` |
| 服务 | `flask --app flaskr run --port 5000 --no-reload`（人工启动，非插件托管） |

## 2 输入

- 项目根：`E:/pilot/flask/examples/tutorial`
- 任务：博客冒烟，验证注册登录发帖
- 验收项：
  1. 首页显示博客列表
  2. 注册用户后可以登录
  3. 登录用户可以发布帖子并在列表看到

## 3 AI 计划

模型根据项目上下文（README、路由源码、pyproject）生成 6 条用例：

| 层级 | 验证方式 | 结果 |
| --- | --- | --- |
| L1 | HTTP GET `/`，断言状态 200 | passed |
| L2 | Playwright 脚本：首页列表 | passed |
| L1 | Python 脚本：注册 + 登录（requests） | passed |
| L2 | Playwright 脚本：注册登录 UI | passed |
| L1 | Python 脚本：登录 + 发帖 + 列表（requests） | passed |
| L2 | Playwright 脚本：发帖 UI | passed |

- 计划：`plan_753fd6b155dc`（自动发布）
- 运行：`run_3e0dc2a0387e`，执行前确认人 `pilot`
- 结论：`passed`，范围 3/3，通过 3，失败 0

## 4 证据

- `report.md`：导出的报告（含“AI 摘要未采用，以下为事实摘要”）
- `report.json`：完整报告记录，含 Mock 判定：
  - `flaskr` → `real`
  - 依据：测试直接导入真实包、使用临时 SQLite；仅 `test_db` 局部 monkeypatch
- `run.json`：运行元数据（ID、状态、确认人）

原始证据在运行目录：

```text
%APPDATA%/aitest/workspaces/pilot/projects/<project_id>/runs/run_3e0dc2a0387e/
  run.json
  plan.json
  generated/*.py
  evidence/*.log
```

## 5 这次验证了什么

- 真实模型生成目标与用例（含 L1/L2/L3 分层）；
- HTTP runner 无重定向断言状态码；
- 项目 `.venv` 发现与 `requests`/`playwright` 脚本执行；
- Playwright Chromium 真实页面操作；
- 运行目录、证据、报告、Mock 判定、事实摘要降级；
- 运行前确认与单项目单运行。

## 6 仍未验证

- 真实 Trae 面板安装与点击流程；
- 服务由插件启动/停止（本次人工启动）；
- 链路日志/数据库采集；
- GitHub 关联；
- 失败分析与问题自动生成的真实样本；
- 磁盘不足、worker 被杀等故障注入。
