# 2026-11-19 扩展验证

覆盖：根包 samplemod、失败 faildemo、依赖缺失 missingdep、取消 slowdemo、大型 packaging、备份恢复、交付读取、无模型降级，以及 samplemod 面板模型负例。

- `results.json`：CLI 扩展矩阵；
- `samplemod/`：显式根包导入，1/1 通过；
- `samplemod-panel-negative/`：真实 Trae 面板按兜底候选生成计划，模型猜错包名（samplemod vs sample），产品判 failed；
- `packaging/`：大型项目 2/2 通过，基线 64,636 passed / 4 skipped；
- `jsdemo/`：Node 项目 `npm test`，1/1 通过；
- `flask/`：Flask tutorial，1/1 通过（基线 24 passed）；
- `monorepo/`：多包 Node 项目，1/1 通过；
- `concurrency/`、`concurrency2/`：两个运行同时启动，均通过；`workspace-integrity.json` 记录 9 项目/10 运行完整；
- `faildemo/`：故意失败，产品判 failed；
- `missingdep/`：依赖不存在，产品判 blocked；
- `slow_cancel/`：取消后 cancelled + blocked；
- `backup-manifest.json`：32 文件、3 项目、含 evidence、无模型 key；
- `panel-backup.json` / `panel-backup.zip` / `panel-backup-restore.json`：真实 Trae 设置页创建 185 文件/5 项目备份，CLI 恢复 verified，`evidence_included=false`；
- `delivery-show.json`、`no-model.json`、`link-import.json`、`link-assert.json`、`github-read.json`、`import-warning.json`、`workspace-integrity.json`、`packaging-map.json`、`packaging-suggest.json`；
- `environment.json`：版本与 commit。
