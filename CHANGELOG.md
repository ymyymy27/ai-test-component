# 更新记录

## 0.5.0（2026-11-16—2026-11-28）

### 近期核心重构与加固（2026-11-26—2026-11-28）
- 后端核心链路重构：收敛已废弃的插槽与旧模板模块（移除 `plugins.py` / `templates.py` / `blind_expectation.py` / `live_page_probe.py` / `checkpoint.py`），引入只读仓库索引 `core/repo_index.py`、结构化 `core/context/` 契约提取、独立裁判与可信度分级 `core/judge.py`、双盲计划冲突复核及脚本修复预览（`plan.repair_case`）。
- 确定性质量门禁：新增 `core/preflight.py` 开测前安检、`core/probes.py` 免 AI 硬探针、`core/script_linter.py` 跑前 AST 静态安检、`core/service_logs.py` 后台日志窗口抓假通过，以及 `tests/unit/test_realworld_regression_suite.py` 与多项目真实环境双盲埋雷验证。
- 面板与扩展：共享 Vue 3 面板完成 Playwright E2E 51 项全量回归（含 `AITEST_E2E_HOST=1`）；同步生成 `integrations/trae/dist/aitest-trae.vsix` 并完成 16 项产物 SHA-256 校验。
- 自动化规模：后端 pytest 554 个节点、面板 vitest 158 项、扩展 node test 101 项；CLI 注册 54 条命令。

### 轻量 Trae 插件重构基线（2026-11-16—2026-11-18）

- 轻量 Trae 插件重构：核心 CLI + Trae 薄扩展 + 共享面板；覆盖项目/任务/计划、运行、证据、问题、报告、备份、GitHub 与链路。
- 面板改为任务台账：任务 → 计划 → 运行，顶层只留测试/问题/历史 + 设置；计划/报告/问题详情为完整文档页，每个状态一个主操作；主台账只列本次纳入项，未纳入项折叠为摘要；结果页可直接调整范围。
- 减少人工动作：Trae 已打开目录且工作空间为空时自动绑定当前目录；交付说明展开即自动起草，并与范围共用一次“保存调整”落盘。
- 面板测试 48 项、扩展测试 48 项、Python 测试 265 个节点，264 通过、1 跳过（Playwright 未安装时）。
- 加固：选中验收项无对应用例不再算通过；`run.start` 必须有显式确认或可复用同指纹确认记录；任务/计划/证据/日志/报告/备份统一脱敏；运行中 CLI 不覆盖 worker 记录；问题关闭必须绑定新运行证据；医生检查真实模型/GitHub 连通性并分级修复；报告新增验收项主表与项目级检查区；计划引入任务契约/计划序号/来源签名，任务或来源变化返回 `PLAN_STALE`；运行写入任务快照并记录最近报告。
- 真实 Trae 验证：本机 Trae CN 1.107.1 中完成目标选择、生成计划、执行前确认、运行、结果、报告、历史、问题与设置；面板最终构建另有结果页调整范围、计划文档与设置页截图。

历史版本与逐次变更见 Git 历史及[轻量 Trae 插件重构计划](docs/修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md)。
