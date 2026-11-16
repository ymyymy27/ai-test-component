# 一期 FR 追踪矩阵（FR01—FR18）

> 基线：2026-11-16 轻量插件重构。需求来源：[需求规格](../项目文档/一期/02-需求/02-需求规格.md)第 3 节。
> 命令集合以 `src/aitest/commands.py` 的 `REGISTRY`（含 `runtime_commands`、`integration_commands` 的 `update`）为准，由 `tests/unit/test_contract.py` 与 `tests/unit/test_fr_traceability.py` 强制。
> `覆盖` 只描述**自动化测试**范围：`完整` = 该 FR 的主要规则都有对应测试；`部分` = 有实现和测试，但仍列出的子项没有直接自动化测试。真实环境验收另见 `tests/acceptance/p1/status.json`，不能由本表替代。

| FR | 主题 | 主责模块/文件 | CLI 命令 | 测试文件 | 覆盖 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| FR01 | 项目与模块 | `src/aitest/commands.py`、`src/aitest/storage/workspace.py` | `project.add` `project.list` `project.show` `project.update` `project.config` `module.add` `module.update` `module.list` | `tests/unit/test_plugin_cli.py`、`tests/unit/test_plan_completion_cd.py` | 完整 | 根目录重叠、配置白名单由 CLI 测试覆盖 |
| FR02 | 任务与验收项 | `src/aitest/commands.py`、`src/aitest/adapters/model.py` | `task.suggest` `task.add` `task.update` `task.list` `task.show` | `tests/unit/test_plugin_cli.py`、`tests/unit/test_model_adapter.py`、`tests/unit/test_plan_completion.py` | 完整 | 候选目标与不可观察项转 `pending` 均有测试 |
| FR03 | 交付说明 | `src/aitest/commands.py`、`src/aitest/models.py` | `delivery.save` `delivery.show` | `tests/unit/test_plugin_cli.py` | 部分 | 保存与“交付信息不完整”已测；AI 起草交付说明未实现 |
| FR04 | 规则与模板预设 | `src/aitest/commands.py` | `rules.save` `rules.list` `rules.show` `rules.bind` `rules.import` `rules.export` | `tests/unit/test_plugin_cli.py` | 部分 | 版本保存与绑定已测；Markdown 导入导出无直接单测 |
| FR05 | 计划与用例 | `src/aitest/runtime_commands.py`、`src/aitest/core/expectations.py` | `plan.generate` `plan.update` `plan.show` `plan.publish` | `tests/unit/test_plugin_runtime.py`、`tests/unit/test_plan_completion.py`、`tests/unit/test_model_adapter.py` | 完整 | 覆盖、预期引用、自动发布与修订均有测试 |
| FR06 | L1 源码可运行性 | `src/aitest/worker.py` | `run.start` `run.status` | `tests/unit/test_plan_completion_cd.py` | 完整 | 构建/入口成功、失败只阻塞 `depends_on=build` 已测 |
| FR07 | L2 函数与模块 | `src/aitest/runners/command_runner.py`、`src/aitest/runners/junit.py`、`src/aitest/worker.py` | `run.start` | `tests/unit/test_runners.py`、`tests/unit/test_plan_completion_cd.py`、`tests/unit/test_plugin_runtime.py` | 完整 | 命令退出码、超时、JUnit 解析与运行落盘已测 |
| FR08 | L2 接口与集成 | `src/aitest/runners/http_runner.py`、`src/aitest/runners/assertions.py` | `run.start` | `tests/unit/test_runners.py`、`tests/unit/test_plan_completion_cd.py` | 完整 | 请求序列、断言与 `request_id` 注入已测 |
| FR09 | L3 用户场景 | `src/aitest/core/manual.py`、`src/aitest/worker.py` | `run.start` `run.manual` | `tests/unit/test_plugin_runtime.py`、`tests/unit/test_plan_completion.py` | 部分 | 脚本生成/危险脚本拦截与人工步骤已测；Playwright L3 无单测 |
| FR10 | Mock 与证据 | `src/aitest/ai_features.py`、`src/aitest/core/evidence.py`、`src/aitest/core/verdict.py` | `evidence.add` `evidence.list` `report.generate` | `tests/unit/test_ai_features.py`、`tests/unit/test_plan_completion_cd.py` | 完整 | Mock 降级、读取预算、证据上限与真实链路门禁已测 |
| FR11 | 问题与回归 | `src/aitest/core/issues.py`、`src/aitest/runtime_commands.py` | `issue.add` `issue.list` `issue.update` `issue.close` | `tests/unit/test_plan_completion.py`、`panel/tests/evidence-issues.spec.ts` | 部分 | 签名去重与草稿已测；`issue.close` 新证据门禁仅面板测试覆盖 |
| FR12 | 报告与导出 | `src/aitest/core/reports.py`、`src/aitest/runtime_commands.py` | `report.generate` `report.export` `report.opinion` | `tests/unit/test_plan_completion.py`、`tests/unit/test_plugin_runtime.py`、`tests/unit/test_plan_completion_cd.py` | 部分 | 结论/计数/覆盖/导出已测；验收意见仅面板与扩展测试覆盖 |
| FR13 | Trae 接入与环境 | `src/aitest/commands.py`、`src/aitest/cli.py`、`integrations/trae/src/extension.ts`、`panel/src/` | `doctor` | `tests/unit/test_plugin_cli.py`、`integrations/trae/tests/extension.test.mjs`、`panel/tests/wizard.spec.ts` | 部分 | CLI/扩展/面板单测通过；真实 Trae 宿主另按验收登记 |
| FR14 | GitHub 变更与检查 | `src/aitest/adapters/github.py`、`src/aitest/integration_commands.py` | `github.read` | `tests/unit/test_github_link.py` | 完整 | `gh` 缺失、非零退出、账号探测降级已测 |
| FR15 | 链路采集 | `src/aitest/adapters/link.py`、`src/aitest/integration_commands.py` | `link.import` | `tests/unit/test_github_link.py`、`tests/unit/test_plan_completion_cd.py` | 完整 | JSON lines/正则、时间窗、缺口与只读 DB 已测 |
| FR16 | 链路断言与定位 | `src/aitest/adapters/link.py`、`src/aitest/runners/assertions.py`、`src/aitest/integration_commands.py` | `link.assert` | `tests/unit/test_runners.py`、`tests/unit/test_github_link.py` | 部分 | `evaluate` 与节点/缺口已测；定位展示无端到端单测 |
| FR17 | 工作空间备份与恢复 | `src/aitest/runtime_commands.py` | `backup.create` `backup.restore` | `tests/unit/test_plugin_runtime.py`、`tests/unit/test_plan_completion_cd.py` | 完整 | sha256/manifest、缺证据、篡改与路径穿越已测 |
| FR18 | AI 失败分析 | `src/aitest/ai_features.py`、`src/aitest/core/analysis.py` | `analysis.run` | `tests/unit/test_ai_features.py`、`tests/unit/test_model_adapter.py`、`tests/unit/test_plan_completion.py` | 完整 | 四段校验、缺“未验证”不合格与自动异步生成已测 |
