# 共享面板（Vue 3 + Vite）

编辑器无关的测试面板；生产构建由 Trae 扩展注入 `window.aitestHost`，开发时可用静态演示宿主查看页面。

```bash
npm install
npm run dev                 # 默认 http://localhost:5173
npm test                    # 48 项组件/状态测试
npm run build               # vue-tsc + vite build
```

当前测试基线（2026-11-17，版本 0.5.0）：面板 vitest 48 项；扩展 node test 48 项、后端 pytest 260 项见各自目录。

## 演示宿主

开发模式在 URL 带 `?mock=<scenario>` 时加载 `src/mock/host.ts`，场景包括：

- `proposal`：有计划、未运行（执行前确认）
- `running`：运行中
- `result`：结果台账
- `passed`：全部通过
- `no-project`：无项目
- `smoke`：项目快速检查
- 追加 `&view=issues|history|settings` 可直接打开问题、历史或设置

演示数据只用于界面回归，不连接真实工作空间，也不进入生产构建。`no-project` 只用于手工预览，e2e 不覆盖该场景。

## 浏览器端到端回归

先启动开发服务器，再运行脚本。`test:e2e` 默认输出到 `test-results/`（可用第二个参数指定目录）；`npm run screenshot` 默认输出到 `../tests/acceptance/p1/evidence/frontend`。

```bash
npm run dev -- --port 5174
# 另一个终端
npm run test:e2e -- http://localhost:5174
```

脚本覆盖无宿主、计划与确认、运行/停止、结果、计划文档、报告、问题详情、历史、设置、420px 横向溢出；不覆盖“无项目”场景。使用本机 Chrome，不下载浏览器。

## 截图

```bash
npm run dev
# 另一个终端
npm run screenshot
```

默认 640×900，另生成 420×900 的结果页；目前只生成深色截图，浅色截图待补。演示数据只用于界面回归，不代表真实验收。
