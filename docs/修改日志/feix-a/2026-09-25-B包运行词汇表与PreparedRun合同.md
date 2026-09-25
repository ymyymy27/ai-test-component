# 2026-09-25 B包运行词汇表与 PreparedRun 合同

分支：`feat/package-b-project-planning`
范围：B 包 Sprint 0（合同冻结）

## 本次完成

- 阅读架构文档《01-项目与计划》第 3、4、7、11、12 节，确定 `PreparedRun` 字段范围。
- 编写 `docs/文档-feix-a/B包/04-PreparedRun设计说明.md`（先行设计文档，依据项目协作规则第 3 条）。
- 在 `domain/planning/plans.py` 冻结运行词汇表：
  - `RunMode` → `RunTier`、`Driver` → `RunDriver`，与架构文档第 4 节命名一致；
  - 新增 `ConclusionCeiling` 与 `conclusion_ceiling_for()`（结论上限只由档位派生）。
- 新增跨包合同 `contracts/prepared_run.py`（Pydantic，`extra="forbid"`、`frozen=True`）。
- 新增三份夹具：成功、失败（被阻塞）、未知（plain 形态 + 依据未确认 + 来源待核实）。
- 新增合同测试 `tests/contracts/test_prepared_run.py`（17 项）与词汇表锁定测试
  `tests/contracts/test_run_vocabulary.py`（4 项）。
- 将 `PreparedRun` 纳入 `scripts/generate_schemas.py` 与 `tests/contracts/test_generated_schemas.py`。
- 同步更新 `tests/unit/test_phase_one_rules.py` 的导入与用例，并补充结论上限派生测试。

## 设计决定

1. **`needs_reprepare` 不作为快照状态。** `PreparedRun` 不可变，不会从"可用"变为"需重新准备"。
   该结论由应用用例按 `invalidation_rules` 比较当前来源修订后派生，快照保持 `prepared`。
   状态因此只有 `prepared` 与 `blocked`。
2. **`plain` 形态完全省略 Git 字段**，而非置空。合同校验拒绝在 `plain` 下出现
   `git_base_commit` / `git_diff_digest`，也拒绝在 `git` 下出现 `plain_manifest_digest`。
3. **`conclusion_ceiling` 由 `run_tier` 派生并校验**。写入 `"full"` 之类的取值会被直接拒绝，
   使 `B-C` 对接文档中的 C-01 类缺陷在 B 侧不可能出现。
4. **门禁只在快照可用时施加**。`status` 为 `blocked` 时跳过必测下限与范围自洽校验——
   被阻塞的原因正是这些门禁未通过；此时要求 `blocking_reasons` 非空。
5. **词汇表双层声明 + 锁定测试**。`domain/` 不得导入 `contracts/`，故两层各自声明，
   由 `test_run_vocabulary.py` 锁定值集合一致。

## 未完成

- `contracts/schemas/PreparedRun.json` 未生成：本机缺少 pydantic，生成脚本需在开发环境执行。
- 领域层的项目、模块、环境、交付对象扩展（Sprint 1）。
- 应用用例与端口接入（Sprint 2）。

## 验证

已执行：

- `python -m compileall` 语法检查：改动涉及的 7 个 Python 文件全部通过。
- 三份夹具 JSON 合法性检查：全部可解析，关键字段符合预期。
- `plain` 夹具不含 `git_base_commit` 与 `git_diff_digest`：已确认。

**未执行**（当前环境缺少 pydantic、pytest、ruff、mypy）：

- `ruff check .`、`mypy`、`pytest -q`
- `python scripts/generate_schemas.py` 及 `git diff --exit-code -- src/aitest/contracts/schemas`
- `tests/contracts/test_prepared_run.py`、`test_run_vocabulary.py`、`test_generated_schemas.py`

上述检查须在具备开发依赖的环境执行后再合并。

## 已知风险

- `test_generated_schemas.py` 现要求 `PreparedRun.json` 存在。生成脚本执行前该测试会失败。
- `scripts/generate_schemas.py` 与 `tests/contracts/test_generated_schemas.py` 为共享文件，
  C 包已在其分支修改（加入 `ExecutionFacts`）。合并时需注意冲突，只保留各自的模型条目。
- 词汇表重命名影响 `tests/unit/test_phase_one_rules.py`，已同步；无其他引用点。
