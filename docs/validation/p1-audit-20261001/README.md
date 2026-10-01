# 2026-10-01 一期源码检查证据

产品基线：`0c890eded76fe6c31ad84c211445025de67f599e`（拉取的 develop）。检查结论见[整体对比](../../当前代码分析与一期工程对比.md)及一期工程检查：[A 包](../../一期工程检查-A包.md)、[B 包](../../一期工程检查-B包.md)、[C 包](../../一期工程检查-C包.md)、[D 包](../../一期工程检查-D包.md)。

补充复核基线：`38ce918a0e67726e107569d15e9d4e5f19506751`（2026-10-01 当前 develop）。原材料与历史结果保留；新增 4 个反例及当前测试摘要分开保存，见下表和文末补充记录。

## 材料

| 文件 | 用途与限制 |
| --- | --- |
| [test-summary.json](test-summary.json) | 两次 JUnit 提取的失败名称、原因、环境/质量摘要；全量收集失败，诊断运行排除 1 个收集错误文件 |
| [probes.py](probes.py) / [probe-results.json](probe-results.json) | 虚构输入/临时目录的故障反例观察；20 个观察中 19 个具体问题，D-COVERAGE-01 额外约束待核对；执行成功不代表产品通过 |
| [additional_probes.py](additional_probes.py) / [additional-probe-results.json](additional-probe-results.json) | 当前基线新增 B-CONFIRMATION-01、B-PUBLICATION-01、B-HASH-01、C-DURABILITY-01 四个观察；确认有对照输入，持久性只观测 close 前调用顺序，不是掉电测试 |
| [recheck-test-summary.json](recheck-test-summary.json) | 当前基线 pytest JUnit 的套件统计、失败名称/原因与 Schema 测试结果；已移除机器名，不保存完整日志及无关本机路径 |

保留上述失败摘要、隔离复现和观察结果。一遍式 `inventory.py`/`check_documents.py` 及生成的全量 AST/校验 JSON 已清理；清点范围、54 个本地链接与 17 FR/35 AC 校验结果仍保留在[原检查日志](../../修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md)，被移除文件可从 PR #40 的 Git 历史恢复。后续中间产物和 CI 改动见[清理日志](../../修改日志/袁/2026-10-01-中间产物清理与CI复核.md)。

路径穿越复现的源/目标/越界文件均在新建 TemporaryDirectory 内，结束自动移除；凭据为 `AUDIT_FAKE_SECRET`，无真实凭据、外部模型、真实宿主或业务系统调用。复现依赖仓库内确定性测试工厂/内存替身，应在上述源码基线上重放；未来修复后需重新审核结果。

## 本机命令与结果

工作目录为仓库根目录。Windows 11 x64/build 22631、Python 3.13.13、Node 24.14.1、npm 11.13.0，版本 0.4.0。本地过程日志/JUnit 长期只保存提取摘要，避免提交无关路径/线程输出。

```powershell
uv sync --extra dev --locked
uv run python scripts/check_versions.py
uv run ruff check .
uv run mypy
uv run python scripts/generate_schemas.py
git diff --exit-code -- src/aitest/contracts/schemas
uv run pytest --junitxml=.audit-p1-pytest.xml
uv run pytest --ignore=tests/unit/test_a_pipe_peer_rejection.py --junitxml=.audit-p1-partial.xml
uv run python -m docs.validation.p1-audit-20261001.probes
npm.cmd --prefix src/aitest/resources/panel ci
npm.cmd --prefix src/aitest/resources/panel run build
npm.cmd --prefix src/aitest/resources/panel test
node src/aitest/resources/panel/node_modules/playwright/cli.js install chromium
npm.cmd --prefix src/aitest/resources/panel test
npm.cmd --prefix integrations/trae ci
npm.cmd --prefix integrations/trae run package
```

- 版本/ruff/mypy/Schema 通过；mypy 基线 123、文档复核 125 个分析文件包含跟随导入，不是产品文件数。
- 全量 pytest：1 collection error，缺 `check_peer_identity`。诊断排除该文件：737 passed / 16 failed / 4 warnings，19.47 s；完整失败名称见摘要。15 管道/宿主/装配失败，另 1 个 `begin()` 返回 dict 多 `intent_id: None`，该失败本身不证明锁失效。
- 面板/Trae 构建通过；首次面板测试缺浏览器，安装 Chromium 后静态导航 1 passed（2.5 s）。VSIX 6 文件，仅证明可构建，未作真实 Trae 接入。
- [基线远端 CI](https://github.com/ymyymy27/ai-test-component/actions/runs/36729829040)同样因 pytest 收集失败；静态步骤通过。CI runner Python 3.13.15，分别记录本机/远端版本。
- 真实 AC 保持 verified=0 / not_verified=7 / blocked=1 / untested=27，缺真实宿主、业务核验/人工导入、掉电/跨核心恢复与完整留存/导出证据。

## 复现与问题编号

结果键对应分包引证：A-SOURCE-01/02/03（字节/范围/元数据），A-PATH-01/02（物化/恢复目标），A-INTENT-01，A-COMMIT-01，A-QUERY-01/02，A-BACKUP-01，A-MIGRATION-01，A-SECRET-01/02，B-PREPARE-01/02，C-INVALIDATION-01，D-DECISION-01，D-ISSUE-01/02。D-COVERAGE-01 只证明实现额外要求 R⊆V，不作为已确认错误。

PR SHA/CI/合并状态以[袁的修改日志](../../修改日志/袁/2026-10-01-一期工程分包与整体源码检查.md)及 GitHub 为准。

## 当前基线的去重补充

对照 A/B/C/D 四份工程检查与整体报告，原 19 项具体复现问题已登记并重放确认；D-COVERAGE-01 仍待主责合同核对。追加 B-07—09、C-07，合计 24 个反例观察中 23 个具体问题、1 个待核对项。根 AGENTS.md 入口现状/路径过时作为 DOC-ENTRY-01 单独记在整体报告，不混入反例数量。

新增脚本只调用确定性测试工厂、纯规则、内存底座及临时 spool；不调用真实模型、业务系统或宿主。运行前后没有产品源码、规范、接口合同或真实验收登记变更。C-DURABILITY-01 在 writer.close 前截取同步调用，不伪造崩溃或据此宣称已做真实掉电实验；B-PUBLICATION-01 调用实际 publish_plan，但使用内存底座。

```powershell
uv run --no-sync python -m docs.validation.p1-audit-20261001.additional_probes
uv run --no-sync ruff check docs/validation/p1-audit-20261001/additional_probes.py
```

结果：四个新增问题均复现，脚本 ruff 通过。JSON 是本次运行生成的观察值；未来修复后重放会更新它，需同时核对源码基线及预期行为，不能把脚本 exit=0 当作产品通过。

当前仓库复核还逐个阅读 123 个跟踪源码/工程目标（Python 116、TypeScript 4、JavaScript 1、YAML 2，无跳过），并按调用链补读合同/测试。`uv run --no-sync ruff check .`、`uv run --no-sync mypy`、`uv run --no-sync python scripts/check_versions.py` 均通过；全量 `pytest --continue-on-collection-errors` 为 737 passed / 16 failed / 1 collection error / 4 warnings，19.814 s，失败仍为既有 A 包问题，Schema 一致性测试通过。面板与 Trae 的 check/build 通过，生成 dist 后面板 Playwright 1 passed；本次没有重新打包 VSIX、查询新远端 CI 或真实宿主验收。

真实一期验收保持 0 verified / 7 not_verified / 1 blocked / 27 untested。原报告的初查记录不被当前结果覆盖；新增编号、源码位置、FR/AC 关联与完成条件见[补充日志](../../修改日志/袁/2026-10-01-去重补充一期检查问题.md)。
