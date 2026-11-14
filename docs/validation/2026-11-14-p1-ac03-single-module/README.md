# P1-AC03 单模块项目适用范围与回归建议

## 范围

真实核心（Windows 11 x64，0.4.10，提交 `072d760`）上的单模块 plain 项目。

- 项目：`guided-4ca9d60d-675d-4dd7-8768-2749124eb82b`
- 源码：`single.py` + `tests/test_single.py`
- 运行：`run-1dd2dc4f…`，Attempt `d0033f71…`
- 报告：`report-ac03-1`
- 依赖图：`graph:guided-4ca9d60d-…` 修订 1，1 个模块 `module-single`
- 计划：`plan-guided-4ca9d60d-…` 修订 2，Case 修订 2 关联 `module-single`

## 实际结果

- L1 来源检查：`syntax=passed`、`load=passed`。
- L2 pytest：执行并核验 `matched`，来源 `verified`，无缺口。
- 报告：L2 1/1 completed；唯一缺口 `critical_mock`，业务结论 `incomplete`（不冒充通过）。
- 回归建议：`changed_files=["single.py"]` →
  - `directly_changed=["module-single"]`
  - `affected_modules=["module-single"]`
  - `suggested_case_ids=["case:pytest:6040cee6b7b48076"]`
  - `cross_module={"state":"not_applicable","reason":"single_module_project"}`

单模块的本模块变更与回归检查可用；跨模块传播明确为不适用并给出原因。

## 代码同步

- `test-selection` 新增 `module_ids`，冻结到 `CaseLink.module_ids`（提交 `072d760`）。
- `suggest_regression` 返回显式 `cross_module` 状态；单模块项目为 `not_applicable`。
- 新增单测：`test_selected_modules_are_frozen_into_the_case_link`。

## 边界

- 由 CLI `dispatch` + 核心面板/原生确认驱动，不是 Trae 面板点击链路。
- `critical_mock` 仍存在，报告不能写成 passed。
- 只升级 AC03；其他 AC 不变。
