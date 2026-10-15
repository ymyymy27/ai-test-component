# C 重试入口（`retry_step`）（2026-10-13，第十批）

> 当前续修（2026-10-15）：0.4.2 补宿主 Python 载体配置/默认装配、CLI 登记生成和项目名称/目标表单；完整计划/执行向导与独立业务核验仍欠。准确验证见[本批记录](../../validation/p1-environment-entry-20261015/README.md)，旧批次数字不累计为全量。

> 隔离候选续检（2026-10-14）：ZIP 父子路径、准入来源保存及缓存误核对有候选修复；完整事务/重启/既有路径回归仍待串行验证，纳入本次 fix 发布，完整回归尚未完成。准确证明及剩余范围见[本批证据](../../validation/p1-unproven-audit-20261014/README.md)；下文历史结果只属于原版本。

> 本次续修（fix@76ab7d2 起点）：整用例复用源快照/证据冻结、同次查询与视图边界、交付提测关联、宿主显式上下文/迟到回执及迁移检查点跨重启闸门已补。最新实现、ZIP/原生进程探针修复与最终全量见[续修证据](../../validation/p1-final-closure-20261014/README.md)；下方原批次数字和结论仅证明原版本，不替代当前真实验收。

> 2026-10-13 复核说明（fix@a51c2b4）：本文件为原实施批次的证据记录，保留原输入、命令、结果和未做项；当前完成性以[最新对照](../01-现状检查/当前代码分析与一期工程对比.md)及[项目现状](../../现状.md)为准，原专项通过不替代新增边界与真实 AC。

来源任务：[当前代码分析与一期工程对比](../01-现状检查/当前代码分析与一期工程对比.md) P1-FR09 行「retry_step/attach_evidence/return_stage 未注册」与 P1-AC19 行「retry/return_stage 缺入口」。

本批只补 `retry_step`（`attach_evidence` 已在第九批接入）；不新增动作名、不改协议、不改门禁规则、不改验收台账。

## 1. 交付内容

| 项 | 说明 | 触点 |
| --- | --- | --- |
| `retry_step` 入口 | `ExecutionCommands.retry`：对**已有尝试**的步骤再执行一次。前置条件是已解析出**新一轮**执行动作（`attempt_index >= 2`），否则拒绝并提示改用 `execute_step`；随后委派既有的步骤执行用例（准备/授权/执行链一字不改） | `application/execution/commands.py` |
| 下游失效不另造机制 | "新尝试立即当前 + 依赖旧尝试的下游依据标为需重跑"由执行提交路径在**同一业务提交**内完成（`commit.py::invalidate_downstream_attempts`）；本入口不新建失效逻辑，避免两套语义 | 同上（引用既有实现） |
| 旧尝试保留为历史 | 回执只增加 `retried_from_attempt_index` 与 `previous_attempt_preserved`，不改写旧事实、不回退旧通过/复用 | 同上 |
| 严格动作形状 | 参数仍须恰为 `{execution_action_id, record_revision=1}`，`target` 必须等于已冻结动作的 `step_id`（调用方自报步骤无效）；未配置执行端口时 `CAPABILITY_UNAVAILABLE` | 同上 |
| 默认装配 | `retry_step` 注册（紧邻 `execute_step`），doctor 广告；默认可派发动作 54 → **55** | `bootstrap.py` |

## 2. 验证

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -m pytest -q tests/unit/test_default_retry_step.py
```

| 检查 | 结果 |
| --- | --- |
| 新增 `tests/unit/test_default_retry_step.py` | **9 passed**：委派目标与回执（`retried_from_attempt_index`/`previous_attempt_preserved`、动作身份经解析器读取）、首次尝试拒绝并提示 `execute_step`、目标与冻结动作不一致拒绝、四种非法动作引用拒绝、未配置执行端口 `CAPABILITY_UNAVAILABLE`、**真装配**里 `retry_step` 与 `execute_step` 均在 `handlers` 与 doctor 广告中 |
| 受影响路径回归（D 侧 + 补证 + 执行授权 + 注册表 + 架构） | **121 passed / 0 failed / 0 error / 0 skipped**（468.92 秒，[JUnit](../../validation/p1-defect-fix-20261012/2026-10-13-retry-step-regression.xml)） |
| `ruff check src tests scripts` / `mypy`（strict，241 文件） | 通过 |
| `scripts/check_docs_facts.py --full` | 0 差异 |

## 3. 未做与为什么

- **`return_stage` 未接**：分册只写"退回阶段：形成新修订，将依赖下游标为需重跑，旧记录可查"。若"退回"只是对**更早的步骤**重试，则与 `retry_step` 同一机制（本批已具备）；但"形成新修订"意味着要在**不重新执行该步骤**的前提下持久化"下游需重跑"，这个记录形状/目标（阶段？步骤？修订）在三份分册里没有冻结到可直接实现的粒度 → **不猜**，如实登记。
- 重试的**真实副作用授权**由既有准备/授权链决定（本入口不绕过）；真实核心执行与真实宿主仍未验收。
- `backup`／`migrate`／`test_connection`／`request_regression` 仍未接；证据进入 `ExecutionFacts` 的发布路径、C-03 默认核验配置、D-02 断言结果生产者、面板流程继续待办。

## 4. 边界

本记录只证明**入口形状 + 委派 + 真装配可派发**层面的行为；不构成 P1-AC09/19 的验收证据，`tests/acceptance/p1/status.json` 未改动。
