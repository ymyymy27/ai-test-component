# 一期打包门禁验证（2026-10-12）

本目录记录**打包身份的重新确立**。此前 `dist/` 不存在（本机从未构建 wheel），且 `integrations/trae/dist/aitest-trae.vsix` 是 2026-10-04 的而扩展/面板源码在 10-11 重建过——因此当时**任何"打包通过"的说法都不成立**。本页记录重建后的实测结果。

## 1 命令与结果

```powershell
npm --prefix src/aitest/resources/panel run build          # tsc --noEmit + esbuild 打包面板
npm --prefix integrations/trae run package                 # tsc + node scripts/build.mjs + vsce package
.venv/Scripts/python.exe -m build --wheel                  # 构建 wheel
.venv/Scripts/python.exe scripts/verify_artifacts.py       # 打包物内容与共享面板身份
.venv/Scripts/python.exe scripts/smoke_wheel.py            # 隔离安装冒烟
```

| 门禁 | 结果 | 它证明了什么 |
| --- | --- | --- |
| `scripts/verify_artifacts.py` | **passed** | wheel 内含 6 份模板与 `Command.json`；无 `node_modules`、无旧包名 `ai_test/`；**面板字节在 wheel／VSIX／源码构建产物三处完全一致**（共享面板只有一份源码） |
| `scripts/smoke_wheel.py` | **passed** | 新建 uv venv（CPython 3.13）、装 wheel、**在检出目录外用 `-I` 跑 CLI**：`templates` 返回 6 份、`doctor` 得 `NOT_READY`（离线）、`mcp-relay` 得 `CAPABILITY_UNAVAILABLE` |

**产物指纹（本次构建）**：

| 产物 | SHA256 |
| --- | --- |
| `dist/ai_test_component-0.4.0-py3-none-any.whl` | `d9165b7bb56f59472ca565c218cce95878e7b97c0ffdc815b1fa07ce43d00894` |
| `integrations/trae/dist/aitest-trae.vsix` | `b8171458b7996c423bb00f974b1dc9b7b3d9794632504f94c61390b5ba5ec3b4` |

`dist/` 与 `integrations/trae/dist/` **不受版本控制**（`.gitignore` 的 `dist/`），因此上述指纹只对**本次本机构建**有效；换机器或改源码后必须重建并重跑两个门禁。

## 2 本机环境前提（不设就跑不起来，且症状具有误导性）

本机 `%LOCALAPPDATA%\Temp` 对**派生的子进程不可写**（os error 5 拒绝访问）。后果有两条，都容易被误读成仓库缺陷：

1. Python 的 `tempfile` 回退到**检出目录**创建临时目录（本会话见过 `tmp<8位随机>` 与 `aitest-wheel-*` 两类落在仓库根；前者已加 `/tmp????????` 忽略规则）。
2. `uv` 会直接失败，报错先是
   `Failed to initialize cache at ...\uv\cache ... 拒绝访问 (os error 5)`，
   设了 `UV_CACHE_DIR` 后又变成
   `Failed to create virtual environment ... os error 5 at path ...\Temp\.tmp1xNMDD`。

**可复现的解法**（无需改仓库代码，`uv` 与 `tempfile` 都尊重这些环境变量）：

```powershell
$env:TEMP = "$(Get-Location)\.runtime\tmp"
$env:TMP  = $env:TEMP
$env:UV_CACHE_DIR = "$(Get-Location)\.runtime\uv-cache"
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
.venv/Scripts/python.exe scripts/smoke_wheel.py
```

## 3 边界（不得代填）

- 本页只证明**打包物内容、共享面板身份与隔离安装后可运行**。它**不**证明真实 Trae 内的行为——那需要按 [`docs/validation/p1-trae-entry-20261011/README.md`](../p1-trae-entry-20261011/README.md) 实机执行。
- 不改变一期验收结论：**0/35 verified**。
- 门禁脚本本身只核对打包物的**内容与一致性**（其自带 `scope` 字段即写明 `artifact_contents_and_shared_panel_identity_only`），不核对功能正确性。
