# 多流游标、命令超时与流式Spool

版本：0.1  
日期：2026-09-27  
状态：第二阶段已实现并验证  
分支：feat/package-c-execution

## 1 多流独立游标

OutputCursor仍表示单个输出流的游标，但汇总位置改为集合：

- Attempt.output_cursors。
- ExecutionCollectionResult.output_cursors。
- RecoveryCheckpoint.output_cursors。
- SpoolManifest.cursors。

stdout和stderr分别维护offset、最后块序号和摘要，不再由最后一个非空流覆盖。

ExecutionFacts合同同步变更：

- AttemptFact.output_cursor替换为output_cursors数组。
- success夹具同时包含stdout和stderr游标。

## 2 命令超时监督

ExecutionRequest.timeout_ms非空时，CommandAdapter启动独立Timer。

超时行为：

- 标记runtime.timed_out。
- 强制终止完整进程组。
- 等待进程退出。
- ExitFact.timed_out为true。
- ExitFact.termination_reason为timeout。
- 采集完整性标记为partial。
- SerialRunner将Attempt置为pending_verification，未知原因command_timeout。
- 不把超时等同于业务失败或业务回滚。

## 3 子进程组回收

Windows：

- CommandAdapter尝试创建带KILL_ON_JOB_CLOSE的Job Object。
- 进程启动后加入Job。
- collect关闭Job句柄，清理仍然存活的子进程。
- 停止/超时使用taskkill /T /F作为进程树终止路径。

Linux/POSIX：

- 使用start_new_session创建独立进程组。
- 停止和超时使用os.killpg。
- 正常collect后再次检查并清理残留进程组。
- wait和reader thread join保证父进程被回收。

## 4 流式Spool

存储路径：

- spool/<attempt_id>/stdout.log
- spool/<attempt_id>/stderr.log
- spool/<attempt_id>/manifest.json

CommandAdapter读取线程在Redactor过滤后直接调用SpoolStreamWriter.append，不再等进程结束后一次性提交。

流式规则：

- 字节到达后直接追加写入对应流文件。
- 达到block_size时封口一个OutputBlockRef。
- 每次封口同步更新块清单和该流独立游标。
- 最后一小段在close时封口。
- read_block按offset和length读取流文件并重新校验摘要。
- Spool在进程运行期间即可出现已封口块，满足先保存再推进游标的原则。

## 5 验证

- pytest：160 passed。
- ruff check：通过。
- 本次修改文件ruff format检查：通过。
- mypy strict：99个源文件无问题。
- 覆盖：stdout/stderr独立游标、流式封口、命令超时、父子进程组回收、Spool重读校验、合同多游标Schema。

## 6 尚未完成

- 崩溃后从append文件恢复未封口尾部。
- Spool块发布到内容寻址对象库并生成正式EvidenceRef。
- 检查点持久化和启动恢复扫描。
- 更大规模输出的背压、磁盘空间预检查与暂停策略。
