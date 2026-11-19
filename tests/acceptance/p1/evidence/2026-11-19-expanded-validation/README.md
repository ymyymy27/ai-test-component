# 2026-11-19 扩展验证

覆盖：根包 samplemod、失败 faildemo、依赖缺失 missingdep、取消 slowdemo、大型 packaging、备份恢复、交付读取、无模型降级，以及 samplemod 面板模型负例。

- `results.json`：CLI 扩展矩阵；
- `samplemod/`：显式根包导入，1/1 通过；
- `samplemod-panel-negative/`：真实 Trae 面板按兜底候选生成计划，模型猜错包名（samplemod vs sample），产品判 failed；
- `packaging/`：大型项目 2/2 通过，基线 64,636 passed / 4 skipped；
- `jsdemo/`：Node 项目 `npm test`，1/1 通过；
- `flask/`：Flask tutorial，1/1 通过（基线 24 passed）；
- `django/`：Django `manage.py test`，1/1 通过；
- `vite/`：真实 `vite build`，1/1 通过；
- `monorepo/`：多包 Node 项目，1/1 通过；
- `concurrency/`、`concurrency2/`：两个运行同时启动，均通过；`workspace-integrity.json` 记录工作区完整；
- `long-run.json`：唯一请求 ID 连续 10 次运行，全部完成，约 6.4 KB/次；
- `key-rotation.json`：临时 env key 降级、恢复正式 key 后生成 5 条候选；
- `performance.json`：真实 Trae 工作区快照 0.5s，旧加载约 50 次 CLI → 1 次；宿主 E2E 2.1m → 39.4s；
- `faildemo/`：故意失败，产品判 failed；
- `missingdep/`：依赖不存在，产品判 blocked；
- `slow_cancel/`：取消后 cancelled + blocked；
- `backup-manifest.json`：32 文件、3 项目、含 evidence、无模型 key；
- `panel-backup.json` / `panel-backup.zip` / `panel-backup-restore.json`：真实 Trae 设置页创建 185 文件/5 项目备份，CLI 恢复 verified，`evidence_included=false`；
- `delivery-show.json`、`no-model.json`、`link-import.json`、`link-assert.json`、`github-read.json`、`import-warning.json`、`key-rotation.json`、`workspace-integrity.json`、`packaging-map.json`、`packaging-suggest.json`；
- `environment.json`：版本与 commit。
