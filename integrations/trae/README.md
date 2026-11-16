# Trae 轻量扩展 0.5.0

薄 UI：每个动作调用一次本地 `python -m aitest.cli`，不启动常驻核心，不使用命名管道。扩展不读写业务文件、不计算结论、不代签确认；面板只展示 CLI 返回值。

## 结构

```text
src/
  extension.ts   # 激活、打开面板、命令、读取设置、调用 bridge
  bridge.ts      # child_process 调 CLI、解析一行 JSON、超时/退出码/非法 JSON 转结构化错误
  runtime.ts     # Python 发现（设置 → 工作区 .venv → py -3 → python）与版本/import 校验
  process.ts     # 子进程最小接口与统一环境
media/
  panel/         # 共享面板构建产物（由 panel/ 构建后复制，不手工编辑）
  testing.svg    # 活动栏图标
```

前端源码在仓库根目录 `panel/`（Vue 3 + Vite + TypeScript），不在扩展目录内。扩展只做宿主适配：注入 `window.aitestHost`（call / getState / setState），把 `project.*`、`task.*`、`plan.*`、`run.*`、`analysis.run`、`issue.list`、`report.*` 与 `doctor` 转给 CLI bridge。

## 设置

- `aitest.pythonPath`：已安装 `aitest` 的 Python 3.11+ 解释器；留空时自动发现。
- `aitest.workspacePath`：用户数据工作空间（不是源码目录）。CLI 以 `--workspace` 使用。

找不到可用解释器时，面板与命令只显示缺项和安装提示；扩展不会自动下载运行时（见 `src/runtime.ts` 的 TODO）。

## 命令

| 命令 | 作用 |
| --- | --- |
| `aitest.openPanel` | 打开测试面板 |
| `aitest.checkEnvironment` | 打开面板并运行 `doctor` |
| `aitest.runStatus` | 打开面板并查看当前项目最近一次运行 |

面板经由扩展白名单调用 `project.*`、`task.*`、`plan.*`、`run.*`、`analysis.run`、`issue.list`、`report.*` 与 `doctor`；其余命令不暴露。`run.start` 只有在面板确认页点击“开始测试”时才带 `--confirm`；其他命令即使请求确认也不会带上。

## 构建与测试

```bash
npm ci
npm test
npm run build          # 先构建 panel/，再复制到 media/panel 并打包扩展入口
npm run build:panel    # 只重建面板产物
```

`npm run build` 需要仓库根目录 `panel/` 的依赖已安装（`cd panel && npm install`）。面板构建失败时不会覆盖 `media/panel`。

`npm test` 只使用假子进程验证 bridge/runtime 与扩展接线，不启动真实 Trae 或真实 CLI。真实 Trae 中的安装、面板交互与完整业务闭环仍需单独验收，未验证项不写成通过。
