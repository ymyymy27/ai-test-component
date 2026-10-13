# C 重试入口 `retry_step`（2026-10-13 第十批）

基线起点：`fix@9c0f65a` + 本会话前九批。本批只补一期动作表内已有的 `retry_step`，不改协议、不改门禁规则、不改验收台账。

## 1. 修了什么

| 项 | 触点 |
| --- | --- |
| `retry_step`：前置条件 `attempt_index >= 2`（已有尝试的新一轮动作），随后委派既有步骤执行用例 | `src/aitest/application/execution/commands.py` |
| 下游失效不另造机制（沿用执行提交路径的 `invalidate_downstream_attempts`）；旧尝试保留为历史 | 同上 |
| 默认装配（紧邻 `execute_step`） | `src/aitest/bootstrap.py` |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 新增 `tests/unit/test_default_retry_step.py` | 9 passed（含真装配可派发断言） |
| 受影响路径回归 | **121 passed / 0 failed / 0 error / 0 skipped**（468.92 秒） |
| `ruff check src tests scripts` / `mypy`（strict，241 文件） | 通过 |
| `scripts/check_docs_facts.py --full` | 0 差异（动作数 55 与文档一致） |

## 3. 文档同步

- [项目现状](../../现状.md)：227 模块 / 68,767 行、tests 333 文件 / 76,403 行、节点 5123、**默认可派发动作 54 → 55**；缺 handler 6 → 5 项。
- [整改索引](../README.md) 新增本批证据；新增[证据文档](../03-修复证据/2026-10-13-C重试入口retry-step.md)；CHANGELOG 新增条目。

## 4. 未做（不掩盖）

- `return_stage`：分册只写"形成新修订，将依赖下游标为需重跑，旧记录可查"；"不重新执行该步骤就持久化下游需重跑"的记录形状/目标未冻结 → 不猜。
- `backup`／`migrate`／`test_connection`／`request_regression`：分别缺受控产品入口与合同定义。
- 重试的真实副作用授权由既有准备/授权链决定（本入口不绕过）；真实核心执行、真实宿主未验收；全量 5123 节点未跑完。
