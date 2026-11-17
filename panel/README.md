# 共享面板（Vue 3 + Vite）

编辑器无关的测试面板；生产构建由 Trae 扩展注入 `window.aitestHost`，开发时可用静态演示宿主查看页面。

```bash
npm install
npm run dev                 # 默认 http://localhost:5173
npm test                    # 组件/状态测试
npm run build               # vue-tsc + vite build
```

## 演示宿主

开发模式在 URL 带 `?mock=<scenario>` 时加载 `src/mock/host.ts`，场景包括：

- `proposal`：建议测试单（默认）
- `running`：运行相位
- `result`：结果相位
- `settings`：设置页
- `no-project`：无项目，走选择目录流程
- `passed`：全部通过的结果，验证“不需要分析”与项目检查/验收项记分牌

演示数据只用于界面回归，不连接真实工作空间，也不进入生产构建。

## 浏览器全功能回归

先启动开发服务器，再运行脚本（默认输出到 `../tests/acceptance/p1/evidence/frontend`）：

```bash
npm run dev -- --port 5174
# 另一个终端
npm run test:e2e -- http://localhost:5174
```

脚本覆盖无宿主、无项目、建议单、换目标、调整抽屉、交付/模板/模块建议、运行取消、结果与问题、证据、报告、意见、历史、项目详情、设置、窄幅与键盘。使用本机 Chrome，不下载浏览器。
