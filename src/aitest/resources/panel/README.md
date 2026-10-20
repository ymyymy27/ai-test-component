# AI 测试面板

共享编辑器面板，包含按 10 张交互参考图还原的侧栏、详情标签页和整窗预览。

## 参考图状态

`preview/index.html` 复刻 1024×1110 整窗交互草图，支持：

`?view=prepare|running|driver|environment|issue|ai-records|records|regression|result|single`

全部状态入口：`preview/gallery.html`。

## 已实现交互

- 顶部菜单：文件、编辑、查看、终端及下拉操作。
- 活动栏：资源管理器、搜索、源代码管理、扩展、AI 测试之间切换。
- 侧栏：设置、更多、项目菜单、区块折叠、计划用例选中。
- 详情：计划、工作台、证据、问题、报告、环境、回归、AI 生成记录。
- 详情通过 `openDetail` 消息打开为编辑器侧边标签页。
- 写动作不会绕过受控用户入口。

## 命令

```powershell
npm ci
npm run build
npm run test:replica
npm run preview
```

本地预览：`http://127.0.0.1:4173/`。

## 边界

- 参考图数据和正式核心投影分开：演示数据必须显式开启。
- 当前交互草图验证 DOM、文案、结构和点击；不宣称真实 Trae 或产品 AC 已验证。