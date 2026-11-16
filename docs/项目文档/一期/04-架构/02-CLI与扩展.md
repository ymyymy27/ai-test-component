# CLI 与扩展（一期）

> 状态：2026-11-16 设计基线（D4）。命令字段见[CLI 与数据契约](../03-设计/03-CLI与数据契约.md)。

## 1 CLI

### 1.1 入口

```bash
python -m aitest.cli <command> --workspace <dir> [--input <json-file>] [--confirm]
```

- 读 `--input` 或 stdin；输出一行 JSON；
- 日志写 stderr；
- 退出码 0/1/2。

### 1.2 分发

```text
main(argv)
 → 解析 command、workspace、input
 → 读取并校验信封（command、request_id、payload）
 → 路由到 handler
 → 成功：{"ok": true, ...}
 → 业务拒绝：{"ok": false, "error": {...}}，退出 1
 → 参数/环境错误：退出 2
```

handler 只做三件事：校验入参、调用 core/storage/runners/adapters、组装响应。不直接做业务判断。

### 1.3 幂等

- 有副作用的命令（add/update/save/publish/start）记录 `request_id → 结果`；
- 同一 `request_id` 重放返回原结果；
- 新意图必须使用新 `request_id`。

### 1.4 错误映射

内部异常统一映射到契约中的错误码；未预期异常映射 `INTERNAL`，附 `request_id`，不把堆栈写进 stdout。

## 2 扩展

### 2.1 文件

```text
integrations/trae/
  package.json
  src/
    extension.ts   # 激活、命令、配置
    bridge.ts      # 调用 CLI、解析 JSON、超时
    runtime.ts     # Python 发现、环境准备、版本校验
    panel/         # Webview 面板
```

### 2.2 命令

| 命令 | 作用 |
| --- | --- |
| `aitest.openPanel` | 打开测试面板 |
| `aitest.checkEnvironment` | 运行 doctor 并展示结果 |
| `aitest.runStatus` | 查看当前项目运行 |

### 2.3 调用

- 所有业务动作经 `bridge.ts` 调 CLI；
- 统一超时、取消、错误展示；
- 不直接读写工作空间文件；
- 不复制业务规则、不重算结论。

## 3 运行环境

### 3.1 Python 发现

按顺序，任一步成功即停：

1. 设置 `aitest.pythonPath`；
2. 工作区 `.venv/Scripts/python.exe`；
3. `py -3`；
4. PATH 中的 `python`。

校验：版本 ≥3.11、`import aitest` 成功、`aitest --version` 可执行。

### 3.2 自动准备

都没有时：

1. 下载便携 Python 到 `%APPDATA%/aitest/runtime/<版本>/`；
2. 创建 venv；
3. 用 VSIX 自带 wheel 执行 `pip install --no-index`；
4. 校验 SHA256。

失败显示具体原因，不静默降级；准备完成后后续离线可用。

### 3.3 升级

插件启动时比较 wheel 版本与已安装版本；不一致则重建运行时。旧运行目录不受影响。

## 4 面板

### 4.1 数据流

```text
面板 → bridge → CLI（读命令）
CLI → JSON → bridge → 面板渲染
面板动作 → bridge → CLI（写命令）
```

### 4.2 轮询

- 运行中 2 秒、空闲 10 秒；
- 只读 `run.status`/`run.list`；
- 轮询失败保留上次数据并标“连接中断”。

### 4.3 执行前确认

- 面板展示范围、用例数、环境、凭据边界、副作用、将安装依赖、预计耗时；
- 用户点击确认后，`run.start` 带确认记录；
- CLI 直调必须 `--confirm`；
- 确认记录写 `confirmed_by`、`confirmed_at`。

### 4.4 展示边界

- 结论、覆盖、Mock、分析都来自 CLI 输出；
- 面板不合并结论与摘要；
- 缺证据、截断、未知都显式标注。

## 5 安装与升级

- 扩展安装后首次打开面板触发环境检查；
- 缺 Python 时按 §3 准备；
- 缺 `gh` 时提示安装与登录命令，由人执行；
- 升级只替换 wheel 与面板资源，不动工作空间数据。

## 6 安全

- 密钥只从环境变量或用户配置读，不写入工作空间；
- CLI 输出前统一脱敏；
- 扩展不执行模型生成的代码，执行只发生在 worker；
- 生成脚本先过危险操作扫描。

## 7 测试

- 契约测试：命令、字段、错误码、枚举；
- bridge 单测：超时、非零退出、非法 JSON；
- 面板测试：确认流程、轮询、错误态；
- 环境测试：Python 发现顺序、缺失提示、版本不匹配。
