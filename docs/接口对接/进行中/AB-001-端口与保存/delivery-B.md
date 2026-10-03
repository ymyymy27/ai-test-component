# B 侧实现交付：端口与保存语义（AB-001）

**合同**：`AB-001`（提供方 A ↔ 消费方 B）
**交付方**：B 包（项目与计划）
**日期**：2026-10-02
**基线**：`origin/develop` = `dcde6e3`（PR #51 合并后）
**分支**：`feat/b-usecase-registry`（本交付所在分支，**尚未合并**）

> 本文件只登记 B 侧的**实现、接线、验证与缺口**，不定义新字段、不改变合同语义。
> 现行字段与语义仍以 `contract.md`（含第 8、11 节）为准。

---

## 1 已实现（B 侧，可复核）

### 1.1 薄转接头（B 的窄底座协议 ↔ A 的实现）

| 项 | 位置 | 说明 |
| --- | --- | --- |
| `PortsUnitOfWork` / `PortsRecordReader` | `src/aitest/application/planning/substrate_adapter.py` | 把 A 的 `FileUnitOfWork` / `FileRecordRepository` 翻成 B 的 `UnitOfWork` / `RecordReader`；缺失能力时抛 `SubstrateContractError` 并指向 `contract.md` 第 8.8 节，**不用默认值顶替** |
| 准备记录身份与形状 | `src/aitest/application/planning/preparation.py` | 记录标识与意图标识由 `(project_id, client_id, prepare_request_id)` 三元组派生，带命名空间 |
| 提交序号临时来源 | 同上第 8.8 节 | 测试侧用 A 的 `RecoveryOrchestrator.inspect()` 提供；A 冻结签名后由装配点换成正式访问器 |

### 1.2 事务作用域（B-Q10）

| 项 | 位置 | 说明 |
| --- | --- | --- |
| `Transaction` / `transaction()` | `src/aitest/application/planning/substrate.py` | **锁只在 `__enter__()` 取**，所以"只创建不进入"不可能泄漏；`__exit__` 必定收尾（没提交就回滚，**不自动提交**） |
| 协议不加成员 | 同上 | 上下文只用已声明的 `open` / `commit` / `rollback` 构造，因此 A 与 `tests/support/memory_substrate.py` **都不用改** |
| 转接头回收兜底 | `substrate_adapter.py` | `weakref.finalize`：转接头被回收而事务还开着时补一次回滚放锁 |
| 四个写编排改用上下文 | `application/project/persistence.py`、`application/planning/prepare_run.py`、`publish.py`、`model_orchestration.py` | 全部 `with transaction(...) as tx:` |

### 1.3 用例进统一入口（B-01 剩余项）

| 项 | 位置 | 说明 |
| --- | --- | --- |
| 动作表与依赖包 | `src/aitest/application/usecase_registry.py` | `BUseCaseDependencies(unit_of_work, reader, clock)`；**不 import** `bootstrap` / `interfaces` / `infrastructure` |
| 注册到入口 | `src/aitest/interfaces/local/b_registration.py` | `register_b_use_cases(api, deps)`、`b_registration_for(deps)`；同名动作**拒绝覆盖** |
| 已打通的动作 | 同上 | `save_context`、`save_binding`、`save_environment`、`save_dependency_graph`、`prepare_run`、`query` |
| `prepare_run` 参数适配 | `usecase_registry.py` 的 `_preparation_inputs()` | 键名与 `PreparationInputs` **逐字一致**，不别名、不补默认值；嵌套模型用 `contracts` 的 `model_validate` 解析；成功返回 `PreparedRun.model_dump(mode="json")`（与 `BC-001` 同一套字段） |
| 结构化错误码 | 同上 `BUseCaseError` + `_guard()` | `B_INVALID_PARAMETER`、`B_REVISION_CONFLICT`、`B_PREPARATION_CONFLICT`、`B_INDEX_MAINTENANCE_REQUIRED`、`B_INVALID_QUERY_CURSOR` |

### 1.4 保存语义收紧（2026-10-03，B-12 / B-13 / B-14）

合同依据：本目录 `contract.md` **第 8.12 节**；检查文档 2026-10-03 版 B-12／B-13／B-14。

| 项 | 位置 | 说明 |
| --- | --- | --- |
| 正文/命令项目一致性 | `usecase_registry.py` 的 `_require_owned_by_command()` | `save_delivery` / `save_task` 在构造领域对象**之前**比对；不一致报 `B_INVALID_PARAMETER`。**过去是拿命令项目覆盖正文项目再落盘** |
| `Delivery` 正文带项目 | `application/project/serialization.py` 的 `delivery_to_payload(..., project_id=)` | 新增**必填**关键字参数，与 `case` / `acceptance_scope` / `rule_draft` 同一做法 |
| 读侧归属严格化 | `application/project/persistence.py` 的 `_verify_payload_project()` | **缺失与不符都拒绝**；写入前与读取时共用。旧记录缺 `project_id` 时显式报错，不读成"任何项目都能读" |
| 拒绝自报已验证 | 同上 `save_delivery()` | 非空 `verified_in_scope` 被拒（入口翻成 `B_INVALID_PARAMETER`）；请改用 `unverified_scope` 声明 |
| 发布核对预期修订 | `application/planning/publish.py` 的 `_revision_to_stage()` + `usecase_registry.py` 的 `_expected_revision_or_none()` | 入口透传 `Command.expected_revision`，同事务比对，不符抛 `ConcurrentEditError` → `B_REVISION_CONFLICT`（带当前修订）。**第二次及以后的发布须声明 `@N`** |
| 模型策略归属 | `application/planning/model_orchestration.py` | `request_model_draft()` 在准入前比对 `policy.project_id`；不一致即 `blocked`，不调用供应方、不落出站记录 |

**仍需 A 守住的一半**：检查文档要求"A 同时守住持久命名空间"（A-11）。
本交付只做 B 侧的准入与引用校验。

---

## 2 已通过的静态 / 单元 / 合同检查（实测）

环境：Windows 11 x64 / CPython 3.13.3；命令在 `E:\project\project1\repo` 下执行，
`$env:UV_CACHE_DIR='E:\project\project1\.uvcache'`；`uv run` 一律 `py -3.13 -m uv run`。

| 检查 | 命令 | 实测结果 |
| --- | --- | --- |
| 静态检查 | `uv run ruff check .` | All checks passed |
| 类型检查 | `uv run mypy` | Success: no issues found in 129 source files |
| 架构边界 | `uv run pytest -p no:cacheprovider tests/architecture/test_boundaries.py` | **2 passed** |
| 统一入口合同（项目上下文类） | `uv run pytest -p no:cacheprovider tests/contracts/test_b_use_case_registration.py` | **10 passed** |
| 统一入口合同（`prepare_run` 参数适配） | `uv run pytest -p no:cacheprovider tests/contracts/test_prepare_run_entrypoint.py` | **10 passed** |
| 事务作用域 | `uv run pytest -p no:cacheprovider tests/unit/test_substrate_adapter.py` | **25 passed** |
| B 侧与合同（含真实落盘）综合 | `uv run pytest -p no:cacheprovider tests/unit tests/contracts tests/architecture --ignore=tests/unit/test_a_pipe_peer_rejection.py` | **879 项：780 passed / 11 failed / 88 errors**；失败与错误**全部**在 A 包（`test_a_*`）与 C 包（`test_command_adapter`、`test_serial_execution_loop`）模块，**B/D 模块零失败** |
| Schema 一致性 | `uv run python scripts/generate_schemas.py` + `git diff --exit-code -- src/aitest/contracts/schemas` | 无差异（本交付未改 Schema） |

> **本机全量数字不作为交付结论**：本环境禁止子进程/命名管道与 `tmp_path` 目录枚举，
> A/C 的用例会大面积报 error；**以 CI 为准**。这里给出它是为了满足
> "改动前后必须比对"——**B/D 的失败数为 0，没有新增失败**。

---

## 3 未通过真实环境验收（**不得读成已验收**）

B 侧本轮**没有**任何真实环境证据：

- 没有真实 Trae 宿主；
- 没有掉电恢复；
- 没有真实模型出站（出站编排仍全部经内存替身）；
- P1-AC 登记未改：B 牵头 8 项仍是 `not_verified` 7 项 + `blocked` 1 项（AC20），
  四包合计 `verified = 0`（`tests/acceptance/p1/status.json`，2026-09-28 登记）。

---

## 4 缺口与仍归 A 的事项（本交付不声称完成）

| # | 缺什么 | 影响 | 归属 |
| --- | --- | --- | --- |
| 1 | 装配点把 `BUseCaseDependencies` 交给 `register_b_use_cases()` | 产品路径（跨进程唯一核心）里 B 的动作**不生效**；现在只在测试里生效 | **A** |
| 2 | 冻结 `current_revision` / `commit_seq` / `next_commit_seq` 进 `application/ports.py` | `prepare_run` 现在**已依赖提交序号**（实测：缺它就抛 `SubstrateContractError`），临时来源随时可能在 A 重构时断 | **A**（签名草案见第 8.8 节） |
| 3 | 运行中修订的落盘与查读端口 | P1-AC20 一直 `blocked` | **A**（仓储） |
| 4 | `Handler` 依赖注入（**可选**） | B 现在靠"注册时闭包"绕过；若 A 改成装配时注入，B 的动作表与测试都不用改 | **A**，B 不主张必须改 |
| 5 | `rule_version` 的索引与查询面 | DEC-004 只定了发布落 `rule_version`；索引键是否要为它增项未核实 | **A**（索引）／D（展示口径见 `BD-001`） |
| 6 | `generate_draft` 之外的模型出站类动作与运行修订动作**未注册** | 这些动作在产品入口上仍不可用 | **B**（后续分批，需要各自的参数适配层） |

> **2026-10-03 更新**：`publish_rules`、`publish_plan` 已注册并接入统一入口
> （见第 1.4 节与 `contract.md` 第 8.12 节）；本节第 6 行据此收窄为
> 模型出站类与运行修订动作。

**已如实登记但不在本合同的**：`serialization.py` 与 `prepare_run.py` 原本带 UTF-8 BOM，
导致 `tests/architecture/test_boundaries.py` 在 `develop` 上一直失败，本交付已修（见第 2 节）。

---

## 5 验证方式（可复核）

1. 取本分支，按第 2 节的命令逐条执行；
2. 重点看三条合同测试：
   - `tests/contracts/test_b_use_case_registration.py`：真实 `Command` + 真实 `FileUnitOfWork`
     经 `LocalAPI.dispatch()` 写入并**重启读回**；
   - `tests/contracts/test_prepare_run_entrypoint.py`：`prepare_run` 的参数形状与
     `PreparationInputs` 逐字一致，且失败时**指名到子字段**（`input_revisions.plan_revision`）；
   - `tests/unit/test_substrate_adapter.py`：锁探针**直接再取一次非阻塞排他锁**，
     "还能取到锁"即"上一事务真的放锁了"。
3. 反例覆盖：未注册动作 → `CAPABILITY_UNAVAILABLE`；索引缺失 → `B_INDEX_MAINTENANCE_REQUIRED`
   （**不返回空列表**）；同 `expected_revision` 写两次 → `B_REVISION_CONFLICT`；
   参数非法/缺失 → `B_INVALID_PARAMETER` 且带字段名；写动作缺 `project_id` → 由 `Command` 拒绝。
