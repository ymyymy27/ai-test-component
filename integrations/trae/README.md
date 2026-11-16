# Trae 轻量扩展 0.5.0

薄 UI：每个动作调用一次本地 `python -m aitest.cli`，不启动常驻核心，不使用命名管道。扩展不读写业务文件、不计算结论、不代签确认；面板只展示 CLI 返回值。

## 结构

```text
src/
  extension.ts   # 激活、打开面板、命令、读取设置、调用 bridge
  bridge.ts      # child_process 调 CLI、解析一行 JSON、超时/退出码/非法 JSON 转结构化错误
  runtime.ts     # Python 发现（设置 → 工作区 .venv → py -3 → python）、版本/import 校验与便携运行时准备
  process.ts     # 子进程最小接口与统一环境
media/
  panel/         # 共享面板构建产物（由 panel/ 构建后复制，不手工编辑）
  runtime/       # aitest wheel 与依赖 wheel（由 scripts/build-wheel.mjs 生成，不入库）
  testing.svg    # 活动栏图标
```

前端源码在仓库根目录 `panel/`（Vue 3 + Vite + TypeScript），不在扩展目录内。扩展只做宿主适配：注入 `window.aitestHost`（call / getState / setState），把 `project.*`、`task.*`、`plan.*`、`run.*`、`analysis.run`、`issue.list`、`report.*` 与 `doctor` 转给 CLI bridge。

## 设置

- `aitest.pythonPath`：已安装 `aitest` 的 Python 3.11+ 解释器；留空时自动发现。
- `aitest.workspacePath`：用户数据工作空间（不是源码目录）。CLI 以 `--workspace` 使用。

发现顺序为设置 → 工作区 `.venv` → `py -3` → `python`，校验版本 ≥3.11 且 `import aitest` 成功。全部失败时自动准备便携运行时：

1. 从 GitHub `astral-sh/python-build-standalone` 最新 release 取 CPython 3.11–3.13 的 `x86_64-pc-windows-msvc-install_only.tar.gz`（优先 3.13），下载到 `%APPDATA%/aitest/runtime/<版本>/`；
2. 用资产自带的 `sha256:` 摘要校验下载文件；发布方没有摘要则拒绝安装，不回退到未校验的包；
3. 用系统 `tar`（Windows 10+ 自带）解压，在里面创建 venv；
4. 用 VSIX 自带的 `media/runtime/*.whl` 执行 `pip install --no-index --find-links <该目录>`，含 pydantic/portalocker 及其依赖；
5. 用同一份探测脚本核验 `import aitest`，失败即停。

准备成功后的后续启动只做本地核验，不再联网；已有合格解释器时完全跳过下载。任一步失败都返回阶段（download/extract/venv/install/verify）与稳定错误码，并把摘要写到 `%APPDATA%/aitest/runtime/prepare-failure.json`，面板与日志不回显下载 URL、绝对路径或 stderr。镜像或测试可用 `AITEST_PYTHON_URL` + `AITEST_PYTHON_SHA256` 覆盖下载源（两者必须同时提供）。

`aitest.pythonPath` 指向的解释器不会被自动替换：自动准备只在全部候选都不可用时发生。

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
npm run build          # 先打 wheel、构建 panel/，再复制到 media/ 并打包扩展入口
npm run build:wheel    # 只重建 media/runtime 的 aitest wheel 与依赖 wheel
npm run build:panel    # 只重建面板产物
npm run package        # 生成 dist/aitest-trae.vsix（含 media/runtime wheel）
```

`npm run build` 需要仓库根目录 `panel/` 的依赖已安装（`cd panel && npm install`）。面板构建失败时不会覆盖 `media/panel`。

`npm run build:wheel` 用 `uv build --wheel` 打 aitest wheel（没有 uv 时回退 `python -m build --wheel`，需要 `build` 包），再从 PyPI 为 CPython 3.11/3.12/3.13 win_amd64 下载 `pyproject.toml` 里的运行依赖 wheel（需要网络）。离线构建可设 `AITEST_RUNTIME_SKIP_DEPS=1`，但生成的 VSIX 无法在干净机器上离线安装，脚本会明确警告。`AITEST_BUILD_PYTHON` 可指定下载依赖用的解释器。

`npm test` 只使用假子进程与假下载器验证 bridge/runtime 与扩展接线，不启动真实 Trae、不联网、不下载真实便携 Python。真实 Trae 中的安装、面板交互、真实 GitHub 下载与完整业务闭环仍需单独验收，未验证项不写成通过。
