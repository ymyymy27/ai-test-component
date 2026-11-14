# P1-AC25 无 Git 仓库的 plain 项目

## 范围

真实核心（Windows 11 x64，0.4.10，提交 `d76c713`）上的 plain 单模块项目。

- 项目：`guided-4ca9d60d-675d-4dd7-8768-2749124eb82b`
- 绑定：`binding-guided-4ca9d60d-…`，`binding_form=plain`，未初始化 Git
- 来源：`single.py` + `tests/test_single.py`
- 运行：`run-1dd2dc4f…`，Attempt `d0033f71…`
- 报告：`report-ac03-1` 修订 1
- 导出：`export-c10d4c43…`，ZIP 摘要 `sha256:6db251ab…`

## 实际结果

- 项目建立、绑定、环境、分析、计划、运行、报告、导出均成功，未因无版本库被拒绝或降级。
- L1：`syntax=passed`、`load=passed`；L2：pytest 核验 `matched`，来源 `verified`。项目是库，无 L3 适用步骤。
- 报告 `report.md` 只含项目、运行、范围、来源摘要、环境、规则、模板、判定与证据；未出现仓库、分支、提交或远端字段。
- `manifest.json` 无 `git/branch/commit/repo/remote` 键。
- 源码变化：把 `single.py` 的返回值从 7 改为 8 后重新分析，内容摘要由 `sha256:adb1b67d…` 变为 `sha256:c3321c57…`，`source_current_ref` 从修订 1 变为 2；恢复为 7 后摘要回到 `sha256:adb1b67d…`。变化以文件清单摘要判定，不依赖 Git。
- 结论口径使用同一核心判定；无版本库未改变 L1/L2 检查范围。

## 边界

- 由 CLI `dispatch` + 核心面板/原生确认驱动，未在真实 Trae 面板打开报告页；面板读取同一报告 payload，未单独截图。
- 报告仍有 `critical_mock`（`mock_scope=unknown`），业务结论 `incomplete`，不冒充 passed。
- 只升级 AC25；其他 AC 不变。
