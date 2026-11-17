# G3 按当前代码重跑 26 条验收（2026-11-16 19:00 批次）

> 目的：在 `重构` 分支现行代码（`3f0ba0c`）上重跑一期 26 条验收场景，只记录实际执行结果。
> 真实结果以本目录 JSON 与运行目录为准；本页只做索引，不替代原始输出。

## 1 环境

| 项 | 值 |
| --- | --- |
| 代码 | `E:/ai-test-component`，提交 `3f0ba0c`（2026-11-16 18:49） |
| Python | 3.11.9 |
| 包版本 | 0.5.0（`doctor` 输出） |
| 模型 | `deepseek-chat`（用户配置，真实调用） |
| CLI | `python -m aitest.cli <command> --workspace <dir> --input <json>` |
| 工作空间 | `%APPDATA%/aitest/workspaces/g3`（本轮新建） |
| 试点项目 | `E:/pilot/flask/examples/tutorial`（Flaskr，提交 `49943b5`），`.venv` = `E:/pilot/flask/.venv` |
| 服务 | Flaskr `flask --app flaskr run --port 5000 --no-reload`（人工启动）；支付缺口样例 `python server.py`（端口 5055） |
| 面板证据 | 复用同日 `2026-11-16-panel-v2/trae-wizard.png`、`trae-result.png`（真实 Trae CN 1.107.1） |

每条命令的完整 JSON 响应保存在本目录；运行类场景的 `run.json`、证据日志在
`%APPDATA%/aitest/workspaces/g3/projects/<project_id>/runs/<run_id>/` 永久保留。

## 2 逐条结果

| AC | 结果 | 证据 | 实际 |
| --- | --- | --- | --- |
| P1-AC01 | verified | `ac01-overlap-recheck.json` | 根目录登记后，子目录登记返回 `INVALID_REQUEST`（根目录重叠） |
| P1-AC02 | verified | `2026-11-16-panel-v2/trae-wizard.png` | 无历史时显示通用目标“验证项目能否启动并跑通主流程”，目标输入可编辑 |
| P1-AC03 | verified | `ac03-task-extract-recheck.json` | AI 提取 4 条，其中“系统要稳定”为 `pending`，其余 `active` |
| P1-AC04 | not_verified | `ac04-delivery-incomplete-recheck.json` | 缺运行命令与提交时 `complete=false`；未发现后续 L1 因交付不完整而阻塞的实现 |
| P1-AC05 | verified | `ac05-rules-versions-recheck.json` | 两个规则版本并存；旧计划保留 v1，新计划使用 v2 |
| P1-AC06 | not_verified | `ac06-partial-scope-report.json` | 报告显示 `S/M=1/2`，但未覆盖清单为空，未列出未勾选的适用项 |
| P1-AC07 | not_verified | `ac07-ai-plan-recheck.json`、`ac07-ai-plan-detail-recheck.json` | 每个验收项均有用例，但模型给出的 `expectation_source` 引用源码而非验收项原文，全部 `expectation_verified=false` |
| P1-AC08 | verified | `ac08-confirmation-recheck.json`、`ac08-run-record.json` | 未确认返回 `CONFIRMATION_REQUIRED`；确认后记录 `confirmed_by=g3-runner` 与时间 |
| P1-AC09 | verified | `ac09-l1-block-run-recheck.json` | L1 build 退出码 1；`depends_on=build` 用例 `blocked`，两个独立用例继续并 `passed` |
| P1-AC10 | verified | `ac10-pytest-failure-recheck.json` | pytest 断言失败；保留 stdout 日志与 JUnit XML，用例 `failed` |
| P1-AC11 | verified | `ac11-interface-db-missing-recheck.json`、`ac11-report-recheck.json` | 接口返回 200，数据库无对应记录；用例 `failed`，报告 `failed` |
| P1-AC12 | not_verified | `2026-11-16-panel-v2/README.md` | 面板 v2 的 2/2 运行使用 pytest 脚本；当前运行器只采集 stdout/stderr/JUnit，不采集截图，未复现 L3 Playwright + 截图证据 |
| P1-AC13 | verified | `ac13-interface-persistence-recheck.json` | 注册→登录→发帖→首页可见，并查询 SQLite 确认记录存在；用例 `passed` |
| P1-AC14 | verified | `ac14-mock-judgement-recheck.json` | `pay` 判为 `mock` 并给出依据；`external_payment_api`、`nonexistent_dep_xyz` 判为 `unknown` |
| P1-AC15 | verified | `ac15-analysis-recheck.json`、`ac15-invalid-analysis-recheck.json` | 真实模型生成四段（含 4 条未验证）；故障注入缺“未验证”时 `status=invalid`、`analysis=null` |
| P1-AC16 | verified | `ac16-issue-dedupe-recheck.json` | 同一签名第二次登记返回同一 `issue_id`、`existing=true`、修订 2 |
| P1-AC17 | verified | `ac17-rerun-recheck.json` | 新建运行仅含 1 个验收项；旧运行与 4 次运行记录完整保留 |
| P1-AC18 | verified | `ac18-mixed-run-recheck.json`、`ac18-mixed-report-recheck.json`、`ac18-mixed-report.md` | 单次运行含 failed/blocked/passed/not_run；计数 1/1/2，结论 `failed`，未覆盖 3 项 |
| P1-AC19 | verified | `ac19-opinion-recheck.json`、`ac19-report-before-opinion.md`、`ac19-report-after-opinion.md` | 未填时导出“未填写验收意见”；填写后记录署名、时间与正文 |
| P1-AC20 | verified | `ac20-gh-missing-recheck.json` | PATH 无 `gh` 时返回 `GITHUB_UNAVAILABLE`；同一工作空间运行查询仍可用 |
| P1-AC21 | verified | `ac21-link-gaps-recheck.json` | 无请求 ID 时返回 request_id/log/db 三个缺口，不拼接节点 |
| P1-AC22 | verified | `ac22-active-run-recheck.json`、`ac22-parallel-projects-recheck.json` | 同项目第二次启动返回 `RUN_ACTIVE`；不同项目两个运行并行完成 |
| P1-AC23 | verified | `ac23-interrupted-recheck.json` | worker 被杀且心跳超时后状态 `interrupted`；已完成用例的结果与证据保留 |
| P1-AC24 | verified | `ac24-backup-restore-recheck.json` | 备份 18 个文件（默认不含证据），恢复校验通过，缺证据标“证据未包含” |
| P1-AC25 | verified | `ac25-doctor-repair-recheck.json` | 逐项报告 GitHub 缺失；自动重建目录/写探针；高风险仅给 winget 命令；AI 给出解释 |
| P1-AC26 | verified | `ac26-model-unavailable-recheck.json`、`ac26-no-model-report.md`、`2026-11-16-panel-v2/trae-result.png` | 无密钥时空 `APPDATA` 下 `plan.generate` 返回 `MODEL_UNAVAILABLE`；显式用例仍可运行、出证据、出报告；面板显示“分析未生成：模型当前不可用” |

## 3 降级项

- **P1-AC04**：只实现交付完整性标记，未找到“后续 L1 阻塞并说明”的实现。
- **P1-AC06**：`S/M` 有显示，但未覆盖清单不包含未勾选的适用项。
- **P1-AC07**：模型未按验收项原文生成 `expectation_source`，全部用例被标“预期未证实”。
- **P1-AC12**：未复现 AI 生成 Playwright 脚本；运行器不采集截图。

## 4 观察（未影响上述登记）

- 项目配置的 `services` 是“名称→服务配置”的映射，但 worker 的 `_start_service` 按单个服务读取
  `health`/`start`，本轮 AC11 的自动启动没有生效，改为人工启动服务后完成验证。
- 运行中读取 `run.status` 偶发遇到 worker 正在写 `run.json`，本轮脚本做了重试；不影响最终登记。

## 5 后续（2026-11-16 19:35）

第 3 节的 4 条降级项随后已补齐实现并用真实 CLI/模型/Playwright 重跑，登记改为
`verified`，证据见 [G3b 批次](../2026-11-16-g3b/README.md)；第 4 节记录的服务配置形状
问题也已修复并在 G3b 中用真实 CLI 验证。本页保留当时的降级结论与原始输出，不追溯改判；
其中 `uncovered_items` 的旧定义也按 G3b 第 4 节说明处理。
