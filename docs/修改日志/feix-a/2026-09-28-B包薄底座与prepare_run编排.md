# 2026-09-28 B 包薄底座与 prepare_run 编排（Sprint 2／5／6 共用前置）

分支：`feat/package-b-substrate`
对应 FR：P1-FR07（准备意图与幂等）、P1-FR01—FR06（编排所依赖的记录底座）
设计依据：`docs/文档-feix-a/B包/11-薄底座与prepare_run编排设计说明.md`

---

## 1 为什么做这件事

核对三个 Sprint 的剩余工作后发现它们**卡在同一处**：

| Sprint | 剩余内容 | 前置 |
| --- | --- | --- |
| 2 | 项目/绑定/环境用例、计划发布、`prepare_run` | `WorkspaceUnitOfWork` + `RecordRepository` |
| 5 | 模型请求编排、迟到响应落盘、脱敏投影 | 同上 + `ProjectionPort` / `ModelProvider` / `SecretPort` |
| 6 | 交接一：真实 `PreparedRun` 替换 C 的夹具 | **Sprint 2 的产物** |

即 Sprint 2 → 5 编排 → 6 交接一是一条串联链，前置条件是同一处；
而该处在 `application/ports.py` 中**仍只有文档字符串**，A 至今未回应。

**采取的办法**：在自己的目录里定一份**窄接口**（薄底座），用内存实现让编排真实跑起来；
A 的签名落地后只写一层薄转接头。**不碰 A 的任何目录，也不修改 `application/ports.py`。**

> 用户已明确决定："先不接 A 的存储层，继续等"。本分支遵守该决定：
> `infrastructure/file_store/` 一个字节都没动。

---

## 2 本次做了什么

| 产物 | 位置 |
| --- | --- |
| 薄底座协议（值对象 + `UnitOfWork` + `RecordReader` + 两个错误类） | `src/aitest/application/planning/substrate.py`（新建） |
| `prepare_run` 用例（编排，零 I/O） | `src/aitest/application/planning/prepare_run.py`（新建） |
| 内存实现 + 固定时钟 | `tests/support/memory_substrate.py`（新建） |
| A 端口转接头骨架（三处翻译职责写在 docstring） | `src/aitest/application/planning/substrate_adapter.py`（新建） |
| 全链路演示测试 | `tests/unit/test_prepare_run_flow.py`（新建） |
| 底座不变量测试 | `tests/unit/test_substrate_contract.py`（新建） |
| `prepare_run` 分支测试 | `tests/unit/test_prepare_run.py`（新建） |
| 设计说明 | `docs/文档-feix-a/B包/11-薄底座与prepare_run编排设计说明.md`（新建） |

### 2.1 薄底座的九条不变量

未开事务即暂存/提交/回滚需拒绝；`expected_revision=None` 表示新建，覆盖已有记录即冲突；
陈旧修订抛错并**携带当前修订**；同事务内同一记录不得重复暂存；
`stage_preparation` 同键同摘要复用、**同键异摘要抛冲突且不覆盖**；提交序号前进且提交后可见；
回滚丢弃全部暂存；读操作**必须显式给修订号**（协议里根本没有"读最新"的方法）；历史只追加。

### 2.2 `prepare_run` 的四条硬约束落点

| 约束 | 落点 | 测试 |
| --- | --- | --- |
| 上下文缺失**列缺口并阻塞**，不编造依赖与结论（P1-AC17） | 缺口分支返回 `status=blocked`，**不抛异常，也不登记准备记录** | `test_context_gaps_block_without_raising`、`test_context_gaps_do_not_register_a_preparation_record` |
| **不把新字节塞进旧意图** | `NEEDS_REPREPARE` 分支**不调用** `stage_preparation` | `test_reprepare_does_not_write_new_bytes_into_the_old_intent`、`test_reprepare_keeps_the_original_intent` |
| **同键异摘要返回冲突，不覆盖** | `CONFLICTED` 分支抛出 `PreparationConflictError`，不写任何记录 | `test_same_request_with_different_input_conflicts`、`test_conflict_writes_nothing` |
| **同请求同输入幂等返回** | `REUSED` 分支直接返回既有结果，提交序号不前进 | `test_second_prepare_with_the_same_input_reuses_the_intent`、`test_replay_returns_the_stored_run_and_does_not_commit_again` |

### 2.3 转接头骨架

`substrate_adapter.py` 的方法体一律 `NotImplementedError`，但把**三处翻译职责**先写下来了：
修订语义（本地 `None` = 新建 ↔ A 的表示法）、错误类型（A 若回传元组则包成异常）、
payload 形状（A 若回领域对象则在适配层转换）。A 的签名一到，只需填方法体，**用例与测试不动**。

---

## 3 实现中发现并修正的一处语义分歧

**分歧**：来源修订（`InputRevisions` 的八项）算不算"输入摘要"的一部分？

- 若算 → 来源一变摘要就变 → 被判成 **`conflicted`（同键异输入冲突）**；
- 但架构文档第 11 节要求这种情况报 **"依据需重新准备"**。

两者互斥。**取值原则**：分册把三句话分开写——"输入摘要不同返回冲突"讲的是**请求内容**不同；
"同请求期间源码已变则返回原依据及'依据需重新准备'"讲的是**来源漂移**。因此：

| 概念 | 含义 | 处理 |
| --- | --- | --- |
| `payload_hash` | **请求要什么**（业务意图） | 进摘要；不同 → 冲突 |
| `input_revisions` | **实际观察到什么**（来源漂移） | **不进摘要**；不同 → 需重新准备 |

**已同步修改** `application/planning/preparation.py` 的 `PAYLOAD_FIELDS`（去掉八项修订），
并更新 `tests/unit/test_preparation.py`：原有一条"八项修订必须在摘要字段内"的断言改为
"**不在**摘要字段内"，并新增两条反例（来源修订变化不改变摘要、请求侧变化改变摘要）。

**这是本分支唯一改动既有代码文件的地方**（`preparation.py` 是上一分支 `feat/package-b-sprint2-preparation` 新增的文件），
理由是原口径会把"需重新准备"误报成"冲突"，属实质缺陷而非风格问题。

---

## 4 检查结果

| 检查 | 结果 |
| --- | --- |
| `uv run ruff check .` | 通过 |
| `uv run mypy`（strict） | 通过，107 源文件 |
| 新增测试 | **45 passed**（底座 22 + `prepare_run` 21 + 全链路 2） |
| `uv run python scripts/generate_schemas.py` + `git diff --exit-code` | **无变化**（未改 `contracts/`） |
| 全量 `uv run pytest` | 1 failed, 22 errors（与基线一致，未新增失败） |

唯一 failed 为 `tests/unit/test_command_adapter.py::test_command_adapter_stops_running_process`
（PR #20，C 包；本机稳定失败，沙箱不允许创建信号管道）。
22 errors 为 `tmp_path` / 子进程夹具的权限限制。**均须在常规终端或 CI 复跑确认。**

---

## 5 已知缺口（登记，不在本次修）

| 缺口 | 说明 | 归属 |
| --- | --- | --- |
| `binding_revision` 等修订**未与底座核对** | `prepare_run` 目前直接采用调用方传入的修订号，没有读回实际值核对。架构文档第 11 节要求"提交时校验项目、绑定、计划、规则、模板、环境修订是否仍有效" | 等 A 的 `RecordRepository`（B-Q02） |
| `PreparedRun` 自身的落盘 | 本次只产出对象 | Sprint 6 交接前需定 |

**不得据此声称"准备功能完整"**：规则已可执行，但修订核对与落盘未完成。

---

## 6 交付说明（四类）

| 类别 | 内容 |
| --- | --- |
| **已实现** | 薄底座协议与九条不变量；`prepare_run` 编排；内存实现；转接头骨架；全链路演示 |
| **已通过静态·单元·合同检查** | `ruff`、`mypy --strict`（107 源文件）、新增 45 项测试、Schema 一致性 |
| **已通过真实环境验收** | **无**。内存实现不是真实存储，无端到端运行结果 |
| **未验证** | 真实文件存储的全部存储侧验收（崩溃恢复、单写锁、不可变历史与备份闭包、索引分页）；`SourceSnapshotPort` 实读；模型出站编排与脱敏投影；GitHub 只读；P1-AC17／AC19／AC30／AC32 端到端验收 |

---

## 7 跨包影响

| 对象 | 影响 | 处理 |
| --- | --- | --- |
| A | **未修改其任何文件**；本协议是 B 侧临时底座 | `B-A` 文档第 8 节已提需求；本分支说明"B 已按该草案自行验证" |
| C | 无：`prepare_run` 只产出 `PreparedRun` | 本次未改 C 的目录 |
| D | 面板可读 `PreparedRun`；`status=blocked` + `blocking_reasons` 是缺口与来源变化的展示来源 | 本次未改 `B-D` 文档 |
| 全组 | 新建 4 个模块与 3 个测试文件；改动 1 个既有文件（`preparation.py` 的摘要字段口径） | PR 说明 |

---

## 8 文档与清单

- 新增 `docs/文档-feix-a/B包/11-薄底座与prepare_run编排设计说明.md`。
- **施工检查项清单本次未更新**：它属于前一个未合并分支（PR #22）的改动范围，
  两个未合并分支改同一份清单会冲突（此前已发生过一次）。
  待 PR #22 合并后一并补勾。可勾选项：第 7 节"源码读取与摘要计算在短事务外"（底座已提供事务边界）、
  第 7 节"提交时校验各来源修订"（**部分**完成，修订核对仍缺，见第 5 节），
  以及新增的底座与 `prepare_run` 验证证据。

---

## 9 未执行的操作

- 未推送、未创建 PR、未发送消息（本条在提交时成立；推送与建 PR 见后续操作）。
- 未修改 `main`／`develop`。
- 未修改 `contracts/`（Schema 无变化）。
- **未修改 `application/ports.py`**。
- **未修改 `infrastructure/`**（A 的目录，一个字节都没动）。
- 未修改 C／D 的任何目录。
- 未修改 `docs/项目文档/**`。
