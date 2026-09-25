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

- 领域层的项目、模块、环境、交付对象扩展（Sprint 1）。
- 应用用例与端口接入（Sprint 2）。

## 验证

在具备开发依赖的环境（Python 3.13 + `pip install -e ".[dev]"`）执行：

| 检查 | 结果 |
| --- | --- |
| `ruff check .` | 首轮失败：1 处 E501 超长；修复后通过 |
| `mypy`（strict） | 通过，95 个源文件无问题 |
| `pytest -q` | 首轮 1 项失败；修复后见下方说明 |
| `python scripts/generate_schemas.py` | 通过，`PreparedRun.json` 已生成并提交 |
| Schema 一致性 | 通过 |

首轮失败的两项均已在提交 `a96c7c5` 中修正：

1. `prepared_run.py` 中一处 f-string 超过 100 字符（E501），拆为两个字符串字面量。
2. `test_conclusion_ceiling_must_be_derived_from_tier` 原以取值 `"full"` 验证派生检查，
   但该取值先被枚举本身拒绝，断言正则不匹配。已拆为两个用例，分别覆盖
   "取值不在枚举内"与"取值合法但与档位不符"两层防护。
   这比原测试声称的保护更强：未知取值会在更早一层被拦下。

首轮运行结果：三份夹具全部解析成功，合同测试 17 项中 16 项通过。

## 已知风险

- `scripts/generate_schemas.py` 与 `tests/contracts/test_generated_schemas.py` 为共享文件，
  C 包已在其分支修改（加入 `ExecutionFacts`）。合并时需注意冲突，只保留各自的模型条目。
- 词汇表重命名影响 `tests/unit/test_phase_one_rules.py`，已同步；无其他引用点。
