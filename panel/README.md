# AI 测试 · 共享面板

Vue 3 + TypeScript + Vite 的本地测试工作台。当前阶段用 `LocalStorageRepository` 提供可运行的本地数据实现；接入宿主后，用同一 `ProjectRepository` 接口替换为 Host/HTTP 实现，页面与领域规则不需要改。

## 运行

```bash
pnpm install
pnpm dev
pnpm typecheck
pnpm lint
pnpm test
pnpm build
pnpm exec playwright test
```

- `pnpm build` 产出可直接由静态服务器托管的 `dist/`（history 路由需要 SPA fallback）。
- `pnpm build:extension` 产出单文件 `panel.js` / `panel.css`，由 Trae 扩展内联进 webview；`integrations/trae/scripts/build-panel.mjs` 负责复制到 `media/panel`，避免依赖宿主资源 URI 与 `cspSource` 同源。
- 现有 CI 使用 npm：`package-lock.json` 一并保留；`pnpm-lock.yaml` 供本地与 pnpm 流程使用。

## 结构

```text
panel/
├─ DESIGN.md                 视觉与交互契约（token 以 src/styles/tokens.css 为准）
├─ src/
│  ├─ router/                  history 路由、项目/任务/阶段守卫、404
│  ├─ domain/                  类型、状态机、派生、计划、运行、问题门禁、报告、分析
│  ├─ repositories/            ProjectRepository + LocalStorageRepository + HostRepository
│  ├─ host/                    宿主 DTO、bridge、命令封装、映射、sidecar、环境检查
│  ├─ stores/                  workspace（领域状态）+ ui（抽屉/主题/Toast/确认）
│  ├─ services/                分析、运行模拟、快速检查、环境、导出、证据摘要
│  ├─ components/              外壳、侧栏、状态、台账、范围、确认、对话框
│  ├─ views/                   概览、任务、资料、范围、计划、运行、结果、报告、问题、历史、设置
│  └─ styles/                  设计 token、基础、布局、组件、动效
├─ tests/                      Vitest：领域、仓储、组件
└─ e2e/                        Playwright：主流程、门禁、路由、响应式、设置
```

## 路由

```text
/setup
/projects/:projectId/overview
/projects/:projectId/tasks/new
/projects/:projectId/tasks/:taskId/details
/projects/:projectId/tasks/:taskId/scope
/projects/:projectId/tasks/:taskId/plan
/projects/:projectId/tasks/:taskId/run
/projects/:projectId/tasks/:taskId/result
/projects/:projectId/quick-check
/projects/:projectId/issues
/projects/:projectId/issues/:issueId
/projects/:projectId/history
/projects/:projectId/settings
/projects/:projectId/reports/:reportId
/:pathMatch(.*)* → 404
```

## 边界

- 组件不直接读写 `localStorage`；所有持久化经过 `ProjectRepository`。
- 结论、覆盖、计数、问题关闭门禁和报告内容只在 `domain/` 计算一次。
- 当前没有连接 CLI、模型、GitHub 或真实文件系统；GitHub 可以手动登记缓存快照，明确标“未确认最新”：
  - 运行与快速检查是本地确定性模拟，UI 标“本地模拟”；
  - 目标候选和失败分析使用本地规则；
  - GitHub 未连接时显示空态；手动缓存快照只作为缓存，不冒充实时读取；
  - 报告导出是浏览器 Markdown 下载，不打包证据；
  - 备份是本地 ZIP（含 `workspace.json` 和 manifest，不打包证据正文）；真实 CLI 备份/恢复和按运行清理仍由命令行完成。
- 接入宿主后，`LocalStorageRepository` 替换为 `window.aitestHost` 适配器；领域状态与页面不变。

## 测试

- `tests/domain/`：状态机、派生结论、表单/配置校验。
- `tests/repositories/`：项目/任务/计划/运行、单运行锁、问题归并/关闭门禁、报告版本、人工结果、备份恢复、格式错误。
- `tests/components/`：状态记号、目标输入、候选、范围刻度、确认、空/错/加载态。
- `e2e/`：完整主流程、键盘主流程、失败→重跑→关闭门禁、快速检查隔离、直接刷新路由、320/768/1200 无横向滚动、抽屉 Esc/焦点、设置/ZIP 备份、GitHub 缓存快照、人工结果/证据、任务资料/模板、业务功能忽略/恢复。

Web 预览使用 history 路由并依赖静态服务器 SPA fallback；宿主 webview（检测到 `acquireVsCodeApi`）和 `file:` 环境自动切换为 hash 路由。真实 Trae 3.4.22 侧边栏已验证渲染与真实 CLI 数据读取；webview 内刷新回退尚未单独实测。
