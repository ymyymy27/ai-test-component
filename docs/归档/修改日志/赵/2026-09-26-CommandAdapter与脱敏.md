# 2026-09-26 CommandAdapter与脱敏

## 本次完成

- 实现真实CommandAdapter，替换产品路径中的占位实现。
- 使用参数数组、shell=False和入口注册启动本地命令。
- 增加环境变量白名单和argv Secret拒绝。
- 增加Windows/Linux进程启动身份采集。
- 增加stdout和stderr独立读取线程。
- 实现StreamingRedactor，按行缓冲并在Spool前过滤。
- 支持精确Secret、键值敏感字段、Bearer/Basic、GitHub token、OpenAI key和JWT过滤。
- CommandAdapter接入SerialRunner。
- SerialRunner增加可配置轮询间隔。
- 增加CommandAdapter到Spool的端到端测试。

## 验证

- pytest：153 passed。
- ruff check：通过。
- 本次修改文件ruff format检查：通过。
- mypy strict：99个源文件无问题。

## 未完成

- 多流独立游标。
- 命令超时和完整进程组回收。
- 持久化spool流式写块。
- 崩溃恢复与检查点。
