# CommandAdapter与脱敏说明

版本：0.1  
日期：2026-09-26  
状态：真实本地命令第一阶段已实现并验证  
分支：feat/package-c-execution

## 1 本次范围

已实现：

- 真实CommandAdapter。
- 使用参数数组拉起本地命令行进程，不经过shell。
- 注册入口、工作目录、环境变量白名单。
- stdout和stderr独立读取、独立形成捕获块。
- 在输出进入Spool前完成敏感信息过滤。
- 进程启动身份采集。
- 停止请求和退出事实采集。
- CommandAdapter接入SerialRunner和FileSpoolStore的端到端测试。

后续阶段已实现：

- stdout/stderr独立游标已迁移到Attempt.output_cursors和SpoolManifest.cursors。
- 命令超时监督和子进程组完整回收。
- 包装器级别的spool流式写块。
- 崩溃恢复和检查点重关联。
- ManualEvidence、HTTP、Agent等其他适配器。

## 2 CommandAdapter安全边界

CommandRegistration冻结：

- entry_id。
- executable绝对路径。
- cwd。
- 环境变量白名单。
- 最大参数数量。

start约束：

- 只接受已注册entry_id。
- request.entrypoint必须与注册的executable一致。
- 使用subprocess.Popen参数数组，显式shell=False。
- 不继承全部环境变量，只传入白名单。
- 如果解析出的敏感值出现在argv中，直接拒绝启动。
- stdout和stderr都通过管道读取，不写终端输出。

## 3 脱敏流程

顺序：

    子进程stdout/stderr
    -> 读取线程
    -> StreamingRedactor按行缓冲
    -> 过滤后的内存捕获块
    -> SerialRunner
    -> FileSpoolStore

不再将未过滤输出交给SpoolStore。

按行缓冲保证敏感值即使跨底层read块拆分，也会在完整行进入输出前被替换。

已过滤：

- 已解析的精确Secret值。
- password、passwd、pwd、token、secret、api_key等键值形式。
- Authorization: Bearer。
- Authorization: Basic。
- GitHub token形状。
- OpenAI风格密钥形状。
- JWT形状。

替换标记统一为：

    [REDACTED]

## 4 进程事实

- handle.adapter_kind为command。
- handle.real_execution_id为真实PID。
- process_start_identity在Windows使用进程创建时间，在Linux使用boot_id、PID和starttime组合。
- ExitFact保存真实退出码、各流最后块序号和已保存字节数。
- 停止请求确认后返回STOPPED；已退出进程不伪造停止成功。

## 5 多流现状

stdout和stderr分别生成输出块并按流保存。后续阶段已将Attempt和SpoolManifest改为独立游标集合，详见08-多流游标超时与流式Spool.md。

## 6 验证

- pytest：153 passed。
- ruff check：通过。
- 本次修改文件ruff format检查：通过。
- mypy strict：99个源文件无问题。
- 覆盖：跨chunk脱敏、双流输出、argv Secret拒绝、真实命令停止、CommandAdapter -> SerialRunner -> Spool端到端。
