# 面板 VSIX 安装与真实 Trae 验证记录（2026-11-09）

## 1. 范围

- 分支：`前端优化`，HEAD `7ef65ed`（面板重写与联调提交）。
- 目标：把当前面板打包成 VSIX，安装到本机 Trae CN，并尝试用 Computer Use 操作真实界面走完整流程。
- 不改 FR/AC，不改 `tests/acceptance/p*/status.json`。

## 2. 环境

| 项 | 值 |
| --- | --- |
| 宿主 | Trae CN 3.4.15（VS Code 内核 1.107.1），Windows 11 x64 |
| 扩展 | `aitest-local.aitest-trae@0.4.10`，VSIX 来源 `integrations/trae/dist/aitest-trae.vsix` |
| 核心运行时 | `C:\Users\y'm'y\AppData\Local\AITest\python\Scripts\python.exe`（Python 3.11.9） |
| 数据工作空间 | `C:\Users\y'm'y\AppData\Local\AITest\workspace-preview` |

## 3. 已执行命令与结果

| 检查 | 结果 |
| --- | --- |
| 面板 `npm run check` | 通过 |
| 面板 `npm run build` | 通过，`dist/index.js` 42.9 KB |
| 面板 `npm test` | 4/4 通过（空面板、项目与运行页、设置、首次向导） |
| 扩展 `npm test` | 33/33 通过（桥接身份、作用域、模型配置） |
| 扩展 `npm run package` | 通过，VSIX 27.3 KB |
| `trae-cn --install-extension ... --force` | 成功，注册为 `aitest-local.aitest-trae@0.4.10` |
| 安装后 `dist/panel.js` SHA-256 | 与构建产物一致（`e2974648…024b1`） |
| 核心包与仓库源码比对 | 271 个 `.py` 文件，缺失/多余/内容差异均为 0 |
| `aitest doctor --workspace …` | `READY`，已暴露 `create_plan_grant`、`plan_confirmation_state` 等新动作 |

## 4. 未完成：真实 Trae UI 全流程

尝试用 Computer Use 操作本机 Trae，宿主返回：

```text
Computer Use is unavailable for this node_repl session
```

进一步核对：

- `agent` 未注入（`typeof agent === "undefined"`）；
- node_repl 的插件根指向 `browser-use/0.5.1`，会话未挂载 computer-use 能力；
- `~/.zcode/cli/plugins/installed_plugins.json` 只登记了 `github` 与 `lark-cli`，`computer-use` 仅有缓存目录、未安装/启用。

因此以下项目**未验证**，不得写成真实宿主通过：

- 侧栏/编辑器标签页中的面板渲染、主题与缩放；
- 首次向导在真实宿主里的项目选择、模型配置、计划审阅与发布；
- 运行页的开始/暂停/继续/取消、执行、核验；
- 报告、设置、诊断页与真实核心的联调；
- 安装后窗口重载与扩展激活。当前窗口的 exthost 日志中没有 `aitest` 激活记录，扩展的激活事件是 `onView:aitest.sidebar` / `onCommand:aitest.openPanel`。

## 5. 复现步骤（待启用 Computer Use 或人工执行）

1. 在 Trae 中执行 `Developer: Reload Window`；
2. 打开活动栏“AI 测试”；
3. 按首次向导选择项目、配置模型、核对并发布计划；
4. 在运行页执行开始/暂停/继续/取消与准备、授权、执行、核验；
5. 检查报告、设置与诊断页。

Trae 用户设置已包含：

```json
"aitest.pythonPath": "C:\\Users\\y'm'y\\AppData\\Local\\AITest\\python\\Scripts\\python.exe",
"aitest.workspaceRoot": "C:\\Users\\y'm'y\\AppData\\Local\\AITest\\workspace-preview",
"aitest.pythonCarriersFile": "C:\\Users\\y'm'y\\AppData\\Local\\AITest\\python-carriers.json"
```

## 6. 结论

本次完成的是**构建、安装与运行时装包核对**；真实 Trae 界面验收仍未完成。该结论不改变任何 AC 状态。
