# 2026-09-28 B 包 Sprint 6 交接一：PreparedRun 功能夹具

分支：`feat/package-b-handoff-fixtures`
对应交接：组长实施方案第 4 节「只做两次业务交接」的**交接一**
设计依据：`docs/接口对接/B包-feix-a-C包/B-C交接一-PreparedRun功能夹具.md`

---

## 1 本次做了什么

### 1.1 新增 `tests/support/prepared_run_factory.py`

用**真实链路**产出 `PreparedRun`：

```
项目上下文（项目/绑定/模块/依赖图/环境）
  → 领域发布门禁 validate_plan_publication()
  → prepare_run()
  → PreparedRun
```

四个场景：`git`、`plain`、`blocked`、`needs_reprepare`。
每次写入都是独立短事务，因此提交序号可预期（项目 1、绑定 2、计划 3、准备记录 4）。

### 1.2 新增 `tests/contracts/fixtures/prepared_run_functional/*.json`

四份**生成出来的**功能夹具（不是手写 JSON）。

### 1.3 新增 `tests/contracts/test_prepared_run_functional_fixtures.py`

22 项验证，含 **"重新生成并逐字节比对"** 的漂移守卫。

### 1.4 新增 `docs/接口对接/B包-feix-a-C包/B-C交接一-PreparedRun功能夹具.md`

给 C 的交接说明：两套夹具的区别、四个场景、覆盖的对象清单、对拍建议、三项待 C 回应的问题。
同时更新 `docs/接口对接/索引.md` 的目录现状与子目录约定。

---

## 2 相对 Sprint 0 合同级夹具的增益

B-C 合同第 13.3 节登记的缺口是：

> `PreparedRun` 的功能夹具 | 三份夹具为 Sprint 0 的**合同级样例**，未覆盖 Sprint 1 新增的项目上下文对象

本次补齐：功能夹具由真实编排产出，覆盖项目、绑定、模块、依赖图、环境（**解析事实**）、
执行来源绑定、计划与范围、规则与模板修订、冻结用例及其关联、断言依据三态
（含 `confirmed` 的**确认引用**）。所有值都是**算出来的**：`payload_hash`、提交序号、各来源修订。

---

## 3 本次踩到并修正的三处问题（都值得记）

1. **`model_dump()` 会把不适用的键写成 `null`。**
   `plain` 夹具最初带 `"git_base_commit": null`，与需求 P1-AC25
   "界面与报告中不出现仓库、分支、提交、远端任何内容，**也不显示为空值或"未知"**"**直接冲突**。
   已改为按产品 payload 规范**真正省略**这些键（与 `application/project/serialization.py` 同一条规范）。

2. **`confirmed` 断言依据必须带确认引用**（合同校验，源自 Sprint 4 领域规则）。
   最初没给，构造即被拒。已改为**按用例自身的依据修订与文本摘要确定性派生**确认引用，
   而不是写死一个假确认号——这样确认才真的绑定到准确的依据修订。

3. **`needs_reprepare` 场景需要先有一条准备记录。**
   首次调用时没有既有记录，判定必然是"新建"而不是"需重新准备"，
   因此最初那个场景其实跑成了 `prepared`（夹具名与内容不符）。
   已改为先用**原始来源修订 1** 登记一次，再用**变化后的来源 9** 重放。

---

## 4 检查结果

| 检查 | 结果 |
| --- | --- |
| `uv run ruff check .` | 通过 |
| `uv run mypy`（strict） | 通过，108 源文件 |
| 新增测试 | **22 passed** |
| `uv run python scripts/generate_schemas.py` + `git diff --exit-code` | **无变化**（未改 `contracts/`） |
| 全量 `uv run pytest` | 1 failed, 22 errors（**与基线一致**，未新增失败） |

唯一 failed 为 `tests/unit/test_command_adapter.py::test_command_adapter_stops_running_process`
（PR #20，C 包；本机稳定失败，沙箱不允许创建信号管道）。

---

## 5 分支基线说明（重要）

本分支基于 `origin/develop` 的 `aaefb52`（**PR #24 之后、PR #25 之前**）。

**后果**：本分支**不含** `application/planning/publish.py`（PR #25 的内容）。
因此本轮的工厂**没有调用** `publish_plan()`，计划门禁直接用
`validate_plan_publication()`（与 `publish_plan()` 内部调用的是同一个函数，效果等价）。

**处理**：等 PR #25 合并、`develop` 前移后，从最新 `develop` 切新分支或把 `develop` 并入本分支。
届时可让工厂改用 `publish_plan()` 完成落盘，使链路上的提交序号与真实产品链路完全一致。

**清单冲突**：本分支**刻意没有修改**第 4、5 节中属于 PR #25 范围的勾选，
避免与前一个未合并分支在同一处冲突（此前已踩过两次）。第 4、5 节的勾选待 #25 合并后补。

---

## 6 交付说明（四类）

| 类别 | 内容 |
| --- | --- |
| **已实现** | 功能夹具生成器；四份功能夹具；22 项验证；给 C 的交接说明 |
| **已通过静态·单元·合同检查** | `ruff`、`mypy --strict`（108 源文件）、新增 22 项测试、Schema 一致性 |
| **已通过真实环境验收** | **无**。夹具经内存底座产出，无真实存储端到端结果 |
| **未验证** | C 侧对四个场景的确认（已列三项问题待回复）；`start_run` 之后的事实；真实文件存储的存储侧验收；`SourceSnapshot` 实际建立与 Git 形态来源身份（归属未裁定） |

---

## 7 跨包影响

| 对象 | 影响 | 处理 |
| --- | --- | --- |
| A | 无 | 未改其任何文件 |
| C | **本次的接收方**：功能夹具替代其夹具用于对拍；已列三项问题待回复 | 新增 `docs/接口对接/B包-feix-a-C包/` 目录与文档；更新索引 |
| D | 无 | 未改 D 的目录 |
| 全组 | 新增生成器、四份夹具、一个测试文件、一份对接文档 | PR 说明 |

---

## 8 未执行的操作

- 未推送、未创建 PR、未发送消息。
- 未修改 `main`／`develop`。
- 未修改 `contracts/`（Schema 无变化）。
- **未修改 `application/ports.py`**、**未修改 `infrastructure/`**（A 的目录）。
- 未修改 C／D 的**代码**目录，未修改 `docs/项目文档/**`。
- **未修改第 4、5 节中属于 PR #25 范围的清单勾选**（避免未合并分支冲突）。
