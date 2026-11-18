# 一期验收登记

`status.json` 是唯一逐项验收台账；`scenario` / `expected` / `related_frs` 逐字来自[需求规格第 4 节](../../../docs/项目文档/一期/02-需求/02-需求规格.md)，`verified` 需非空 `evidence.path` 且 `real_environment_verified=true`。

## 当前状态

- 27 条 AC：27 条 `verified`、0 条 `not_verified`、0 条 `untested`；
- 2026-11-16 先按当前代码重跑 26 条场景（[G3 批次](evidence/2026-11-16-g3-rerun/README.md)），随后补齐 P1-AC04/06/07/12 的行为并用真实 CLI/模型/Playwright 重跑（[G3b 批次](evidence/2026-11-16-g3b/README.md)）；
- 2026-11-18 用当前代码重跑 P1-AC09（[批次](evidence/2026-11-18-ac09-current/README.md)）；P1-AC12 改由真实模型生成 Playwright 脚本并执行（[批次](evidence/2026-11-18-ac12-ai-current/README.md)）；
- 每条 `verified` 都登记 `owner`、`verified_at` 与 `artifact_versions`；`baseline_commit` 记录登记所依据的提交；
- 单元、合同、集成测试通过不等于验收通过。

## 使用方式

1. 先读需求规格第 4 节与[测试计划](../../../docs/项目文档/一期/05-测试验收/01-测试计划.md)；
2. 按批次执行，把真实输入、版本、步骤、实际结果与证据路径写入 `tests/acceptance/p1/evidence/`；
3. 更新 `status.json` 时只改结果字段，不改 `scenario` / `expected` / `related_frs`；
4. 运行 `python -m pytest tests/unit/test_acceptance_status.py -q` 对账。

## 记录字段

| 字段 | 要求 |
| --- | --- |
| `result` | `untested` / `blocked` / `not_verified` / `verified` |
| `evidence.path` | `verified` 时必填，指向真实证据 |
| `real_environment_verified` | `verified` 时必须为 `true` |
| `blockers` | `blocked` 时写清缺什么、由谁解决 |
| `unverified_reason` | 非 `untested` 时说明原因 |
| `owner` | `verified` 时必填，写验收责任人 |
| `verified_at` | 真实环境验证日期 |
| `artifact_versions` | `verified` 时必填，写 Python、包、模型、宿主或 Playwright 等实际版本 |
| `baseline_commit` | 文件顶层；登记依据的代码提交 |

## 边界

- 文档一致、模拟器、组件测试、夹具通过都不能写成真实验收；
- 没有证据位置就不改 `verified`；
- 发现登记与证据不符时，先改登记再改文档。
