# 轻量插件重构进度（2026-11-16）

> 本文件只记录“做到了什么、验证到哪一步”，不替代验收登记。验收状态以 `tests/acceptance/p1/status.json` 为准。

## 1 已完成

| 阶段 | 内容 | 证据 |
| --- | --- | --- |
| D1 | 18 FR / 26 AC 需求基线、业务方案、功能清单 | `docs/项目文档/一期/02-需求/`、`tests/unit/test_acceptance_status.py` |
| D2 | CLI 与数据契约：44 个命令、错误码、枚举、文件字段 | `docs/项目文档/一期/03-设计/03-CLI与数据契约.md` |
| D3 | 页面、状态与关键流程设计 | `docs/项目文档/一期/03-设计/01-产品方案与界面设计.md` |
| D4 | 四册架构：总览、CLI/扩展、执行与证据、存储与备份 | `docs/项目文档/一期/04-架构/` |
| WP1 | 旧需求/设计/架构/验收文档标记废止；新文档就位 | `docs/归档/系统级文档-2026-11-16/` |
| WP2 | CLI、原子文件存储、锁、schema、运行模型、备份恢复 | `src/aitest/`、`tests/unit/test_plugin_cli.py`、`test_plugin_runtime.py` |
| WP3 | 系统级实现与旧测试移出 `src/`、`tests/` | `legacy/` |
| WP4 | worker、Runner、模型适配器、AI 目标/计划/分析/Mock/摘要、服务生命周期、脚本生成 | `src/aitest/runners/`、`adapters/model.py`、`ai_features.py` |
| WP5 | GitHub（gh api 只读）与链路（日志/查询命令）适配器 | `src/aitest/adapters/github.py`、`link.py` |
| WP6 | 项目、运维、测试验收文档重写 | `docs/项目文档/一期/` |

## 2 已通过的静态与自动化检查

- `python -m pytest -q`：107 节点通过；
- `python -m ruff check .`：通过；
- `.venv/Scripts/mypy.exe src/aitest`：27 个源文件通过；
- `python scripts/check_versions.py`：0.5.0 一致；
- `python scripts/check_docs_facts.py`：0 处差异；
- `integrations/trae`：`npm test` 22 项通过，`npm run build` 成功。

## 3 已做的真实环境验证

- `docs/validation/2026-11-16-plugin-local-smoke/README.md`：真实 CLI 进程完成 project → task → plan → run → report → backup，结论 passed；
- `docs/validation/2026-11-16-pilot-flaskr/README.md`：Flaskr 试点，真实模型生成 6 条用例，HTTP + Playwright 全部通过，报告 passed；
- `docs/validation/2026-11-16-trae-panel/README.md`：真实 Trae CN 面板完成项目、目标、计划、执行确认、运行、报告与导出；
- `docs/validation/2026-11-16-github-real/README.md`：`github.read` 读取公开仓库成功。

已登记验收：26 条 `verified`。

## 4 未验证（不得写成通过）

| 项 | 缺什么 |
| --- | --- |
| 真实 Trae 安装、面板点击、确认 | 已完成（见 trae-panel 证据） |
| 试点项目 L1/L2/L3 | 已完成（Flaskr 试点） |
| 模型失败路径 | 非法输出已测；超时与限额未测 |
| GitHub 失败路径 | 私有仓库/不存在仓库已测；未登录与网络失败未测 |
| 链路采集 | 已完成日志+只读查询实测 |
| Playwright 执行 | 前端项目与浏览器环境 |
| 服务启动/停止 | 真实服务与端口 |
| 磁盘不足、worker 被杀 | 故障注入证据 |
| 验收登记 | 26 verified |

## 5 结论

重构主体已从系统级实现切换为轻量插件形态，自动化检查通过；真实 Trae 与试点项目验收尚未开始，不能宣称一期完成。
