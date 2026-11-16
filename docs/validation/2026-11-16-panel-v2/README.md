# 共享面板 v2 截图（2026-11-16）

- 用途：`panel/` 重构后的结果页视觉记录，不是真实 Trae 验收证据。
- 尺寸：640×900，CSS 像素，deviceScaleFactor=1。
- 数据：开发模式静态 mock 宿主（`?mock=result`），演示项目“订单服务”；结论 `failed`、覆盖 3/8，均取自 mock 返回字段。
- 复现：`cd panel && npm install && npm run dev`，另开终端 `npm run screenshot -- ../docs/validation/2026-11-16-panel-v2/panel.png`（默认访问 5173）（使用本机 Chrome，不下载浏览器）。
- 截图未覆盖：真实 Trae webview 注入、真实 CLI 往返、窗口缩放与键盘操作。
- 同一轮还人工查看了 400×900 的项目/向导/运行/设置页与 900×900 的结果页，未逐张入库。
