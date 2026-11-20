# 面板 e2e 的两种模式

`panel/e2e/` 里的用例分两类，结论边界不同：

- **本地演示模式（大多数 spec）**
  - 不注入 `window.aitestHost`，走 `LocalStorageRepository`。
  - 项目分析、验收项、用例和运行结果来自 `panel/src/domain/analysis.ts` / `factory.ts` 的演示数据。
  - 全绿只证明**展示与交互**，不能证明 Python 核心的判定、执行或真实链路。

- **真实 CLI 宿主模式（`host.spec.ts`）**
  - 由 `hostFixture.ts` 注入 `window.aitestHost`，通过 `spawnSync` 调真实 `python -m aitest.cli`。
  - 覆盖项目登记、任务、计划、确认、运行、结果、报告、设置键名、快速检查与问题登记门禁。
  - 依赖本机 Python 与仓库 `src/`，耗时更长；命令层 CI 仍以 `npm --prefix panel test` 与 `build` 为准。

新增用例时：只验证界面行为放本地演示模式；要验证业务事实必须走 `hostFixture.ts` 并断言落盘结果。
