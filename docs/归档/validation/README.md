# 验证档案（归档）

本目录保存 2026-10-01—2026-10-16 的历史实测、反例与修复证据，共 40 个批次。每个批次对应一个 `p1-<主题>-<YYYYMMDD>/` 目录，目录内 `README.md` 是该批次的入口，记录版本/提交、输入、命令、预期/实际、证据文件和未验证原因。

**新增验证批次请写入 [`docs/validation/`](../../validation/README.md)（现行）**，不要继续加到本归档。

验证档案是**历史证据**：保留当时的命令、日志、JUnit/JSON、失败与跳过结果，不因后续版本变化而追溯改写。现行结论以[项目现状](../../现状.md)、[项目现状](../../现状.md)和[接口台账](../../接口对接/README.md)为准。

## 1. 批次索引

| 批次 | 文件数 | 入口 |
| --- | ---: | --- |
| `p1-abc-fix-20261003` | 532 | [README](p1-abc-fix-20261003/README.md) |
| `p1-abc-remaining-20261004` | 3238 | [README](p1-abc-remaining-20261004/README.md) |
| `p1-audit-20261001` | 8 | [README](p1-audit-20261001/README.md) |
| `p1-audit-20261002` | 8 | [README](p1-audit-20261002/README.md) |
| `p1-boundary-fixes-20261013` | 75 | [README](p1-boundary-fixes-20261013/README.md) |
| `p1-code-review-20261012` | 40 | [README](p1-code-review-20261012/README.md) |
| `p1-completion-repair-20261013` | 55 | [README](p1-completion-repair-20261013/README.md) |
| `p1-completion-repair-20261014` | 85 | [README](p1-completion-repair-20261014/README.md) |
| `p1-current-closure-20261014` | 53 | [README](p1-current-closure-20261014/README.md) |
| `p1-defect-fix-20261012` | 73 | [README](p1-defect-fix-20261012/README.md) |
| `p1-e2e-audit-20261003` | 13 | [README](p1-e2e-audit-20261003/README.md) |
| `p1-empty-reference-20261014` | 17 | [README](p1-empty-reference-20261014/README.md) |
| `p1-environment-entry-20261015` | 48 | [README](p1-environment-entry-20261015/README.md) |
| `p1-final-closure-20261014` | 12 | [README](p1-final-closure-20261014/README.md) |
| `p1-fix-recheck-20261003` | 10 | [README](p1-fix-recheck-20261003/README.md) |
| `p1-fix-recovery-20261014` | 13 | [README](p1-fix-recovery-20261014/README.md) |
| `p1-fix-recovery2-20261014` | 60 | [README](p1-fix-recovery2-20261014/README.md) |
| `p1-fix-review-20261013` | 37 | [README](p1-fix-review-20261013/README.md) |
| `p1-fullsuite-20261012` | 1 | [README](p1-fullsuite-20261012/README.md) |
| `p1-implementation-audit-20261015` | 46 | [README](p1-implementation-audit-20261015/README.md) |
| `p1-interface-review-20261012` | 13 | [README](p1-interface-review-20261012/README.md) |
| `p1-interface-review-20261015` | 29 | [README](p1-interface-review-20261015/README.md) |
| `p1-migration-backup-identity-20261014` | 45 | [README](p1-migration-backup-identity-20261014/README.md) |
| `p1-migration-closure-20261014` | 53 | [README](p1-migration-closure-20261014/README.md) |
| `p1-migration-location-20261014` | 26 | [README](p1-migration-location-20261014/README.md) |
| `p1-native-entry-20261015` | 31 | [README](p1-native-entry-20261015/README.md) |
| `p1-onboarding-live-20261016` | 5 | [README](p1-onboarding-live-20261016/README.md) |
| `p1-packaging-20261012` | 1 | [README](p1-packaging-20261012/README.md) |
| `p1-panel-guidance-20261016` | 13 | [README](p1-panel-guidance-20261016/README.md) |
| `p1-query-auth-20261015` | 47 | [README](p1-query-auth-20261015/README.md) |
| `p1-runtime-source-20261015` | 72 | [README](p1-runtime-source-20261015/README.md) |
| `p1-source-completion-20261012` | 24 | [README](p1-source-completion-20261012/README.md) |
| `p1-state-reader-security-20261014` | 44 | [README](p1-state-reader-security-20261014/README.md) |
| `p1-trae-entry-20261011` | 1 | [README](p1-trae-entry-20261011/README.md) |
| `p1-trae-live-20261016` | 52 | [README](p1-trae-live-20261016/README.md) |
| `p1-trae-ui-20261016` | 114 | [README](p1-trae-ui-20261016/README.md) |
| `p1-unproven-audit-20261014` | 109 | [README](p1-unproven-audit-20261014/README.md) |
| `p1-usability-recheck-20261015` | 2 | [README](p1-usability-recheck-20261015/README.md) |
| `p1-workflow-20261015` | 70 | [README](p1-workflow-20261015/README.md) |

## 2. 阅读顺序

1. 先读批次的 `README.md`：范围、版本、命令与结论。
2. 需要复核数字时，读同目录的 `facts.json`、`source-inventory.json`、JUnit/XML 或制品核对 JSON；不要用零散日志替代汇总事实。
3. 需要追源码改动时，按 README 指向的提交/文件与[修改日志](../修改日志/README.md)交叉核对。

## 3. 完整性说明

- 历史批次中的相对链接可能指向未入库的构建产物、临时日志或其他机器的绝对路径；这是原记录的一部分，不回填、不改写。
- 批次之间的“通过项”存在重叠，不能相加为全量；中断、失败、跳过和未取到回执的批次保留原状态。
- 新增批次写入[现行验证档案](../../validation/README.md)，使用 `p1-<主题>-<YYYYMMDD>/`，至少包含 `README.md` 与可复核的结果文件；命名与写法见[文档总览](../../README.md#5-新增文档约定)。
