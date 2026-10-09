# 2026-10-09 会话证据索引

本文件只索引**本会话新增**的证据文件（其余历史证据见同目录 `README.md` 与各自基线）。每项都注明命令、结果与边界；组件/合同证据**不等于**真实 Trae、真实供应方、物理掉电或 35 项 AC。

| 主题 | 证据文件 | 内容 |
| --- | --- | --- |
| 模型材料 JSON 投影（交接反例） | [pending-model-projection-json-safety.py](pending-model-projection-json-safety.py)、[pending-model-projection-counterexample.txt](pending-model-projection-counterexample.txt)（旧基线，原字节保留）、[pending-model-projection-json-safety-after.txt](pending-model-projection-json-safety-after.txt) | 原 14 failed → 修后 15 passed；严格解码后在键与值上过滤编码变体 |
| 同批受影响面与环境说明 | [json-projection-affected-and-environment.txt](json-projection-affected-and-environment.txt) | 受影响文件、受限环境下的 633 失败/错误节点与同基线 A/B 结论 |
| 复用来源非命令证据 | [reuse-non-command-reference-before-after.txt](reuse-non-command-reference-before-after.txt) | 基线 7 failed/3 passed → 修后 10 passed（逐一核对准确 `evidence_ref`） |
| 冻结步骤截止准入 | [frozen-step-deadline-before-after.txt](frozen-step-deadline-before-after.txt) | 基线 4 failed（`DID NOT RAISE`）→ 修后 5 passed；另 14 项快速回归 |
| unit 全树分块回归 | [unit-file-sweep-2026-10-09.txt](unit-file-sweep-2026-10-09.txt) | 六路轮转：927 + 861 + 629（剩余 42 文件三批）；除环境 skip 与已修夹具外无失败 |
| 汇总回归（四轮） | [session-consolidated-regression-2026-10-09.txt](session-consolidated-regression-2026-10-09.txt) | 418 → 447 → 463 passed / 1 skipped；逐轮列出新增节点与 skip 原因 |
| 构建与隔离 wheel 冒烟 | [build-and-wheel-smoke-2026-10-09.txt](build-and-wheel-smoke-2026-10-09.txt) | wheel 729363 → 733035 B、sdist 593691 → 597520 B；隔离冒烟通过（需 `TEMP`/`TMP` 指向工作区内） |
| C-12/A-07 相关套件复核（HEAD `843e8c5`） | 本文件同批记录 | `test_saved_run_control` + `test_unverified_terminal_observation` + `test_recovery_marker_authority` + `test_background_work`：**89 passed（423.68s）**，即"待核实控制处置/未知终态/恢复事实/后台续行"区域在本会话改动后仍全绿；仅组件证据，未覆盖项见检查表 C-12/A-07 |

配套文字记录：[修改日志](../../修改日志/袁/)（每批根因/源码/反例/修后）、[整改计划](../../一期工程整改/02-整改计划/ABC剩余问题整改计划-2026-10-04.md)、[交接说明-2026-10-09](../../一期工程整改/04-交接/交接说明-2026-10-09.md)、[裁定记录](../../一期工程整改/02-整改计划/裁定-2026-10-09-start准入四项.md)、[待裁定草案](../../一期工程整改/02-整改计划/待裁定项草案-2026-10-09.md)。
