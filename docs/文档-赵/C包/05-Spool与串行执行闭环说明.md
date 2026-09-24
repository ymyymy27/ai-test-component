# Spool与串行执行闭环说明

版本：0.1  
日期：2026-09-25  
状态：单机Mock闭环已实现并验证  
分支：feat/package-c-execution

## 1. 本次范围

本次实现：

- 文件系统SpoolStore。
- 已封口输出块的不可变持久化。
- Spool块清单。
- 确定性FakeExecutionPort。
- SerialRunner持续轮询、采集、持久化和步骤状态回写。
- 上游失败只阻塞下游的串行闭环。

本次不实现：

- 对象库发布和EvidenceRef正式认领。
- current.json和业务记录事务。
- 真实命令包装器、进程组、真实ExitFact和恢复重放。
- 多流独立游标的完整模型。
- 暂停、取消收敛和并行组。

## 2. FileSpoolStore

路径：

- 块文件：spool/<attempt_id>/<stream>-<block_index>.bin
- 清单：spool/<attempt_id>/manifest.json
- 清单版本：aitest.spool/1.0

持久化规则：

- 只接受complete为true的已封口块。
- 同一批块必须共享run_id、step_id和attempt_id。
- 块内容按不可变文件写入，已存在同字节文件可幂等复用。
- 同块位置出现不同字节时明确报冲突。
- 块摘要为sha256，长度从实际字节计算。
- 清单原子写入，包含块序号、offset、length、digest、完整标志和采集来源。
- attempt_id、run_id和step_id必须是安全路径分量，拒绝目录穿越。
- read_block重新检查长度和摘要，不能只相信清单。

## 3. 当前Mock决策

### 决策1：适配器返回捕获块，Runner负责Spool

FakeExecutionPort在ExecutionCollectionResult中返回CapturedOutputBlock，SerialRunner通过SpoolStore保存。

这是单机Mock和测试专用过渡方案，不是最终真实命令包装器合同。最终CommandAdapter/PythonChecksAdapter应由受控包装器先完成过滤和封口写入spool，再由核心读取已经封口的块并认领。该差异已明确，不将Fake路径当成生产恢复路径。

### 决策2：只保存封口块

未封口尾部仍视为不可持久化内容。SpoolStore拒绝complete为false的块，因此不会用游标替代真实字节，也不会把未决尾部写入默认证据库。

### 决策3：先Spool后更新Attempt

SerialRunner只有在SpoolStore成功返回清单后，才把块引用写入Attempt。持久化失败时Attempt不进入completed。

### 决策4：退出码不等于业务结论

Fake ExitFact中的退出码只表示执行事实。SerialRunner最多派生出completed、execution_error、cancelled或pending_verification，不生成业务passed或failed。

### 决策5：失败和未知分开

- error_ref存在：Attempt为execution_error。
- inspect为lost或unknown：Attempt为pending_verification。
- stopped但stop_confirmed为false：Attempt为pending_verification。
- exited但缺ExitFact或采集未完成：Attempt为pending_verification。
- 上游execution_error、cancelled、invalidated或blocked：下游blocked。
- 上游pending_verification：下游waiting。

## 4. FakeExecutionPort

FakeExecutionPort提供确定性测试能力：

- register(spec)注册尝试。
- start返回稳定假句柄，重复start返回同一句柄。
- inspect按running_observations_before_exit推进。
- collect返回注册的捕获块、假ExitFact和假结构化结果引用。
- request_stop立即确认停止，仅用于Mock。
- execution_order记录实际启动顺序。

该适配器不注册为产品可用能力，也不通过bootstrap加载。

## 5. SerialRunner闭环

run_serial执行顺序：

1. 根据依赖计算ready步骤。
2. 串行执行ready步骤中的Attempt。
3. start调用ExecutionPort。
4. inspect轮询至终态，超过max_polls保持pending_verification。
5. collect取回捕获块和ExitFact。
6. 通过SpoolStore持久化封口块。
7. 更新Attempt块引用、游标、结构化结果、ExitFact、采集完整性和错误。
8. 回写Step状态。
9. 下一轮重新计算ready和blocked，直到无可执行步骤。

## 6. 已知模型缺口

- 当前OutputCursor只描述单个stream，Fake实现以最后一个块作为游标；真实多流采集需要每流独立游标或游标集合。
- SpoolManifest已包含Run/Step/Attempt归属，但块文件正文尚未发布到objects/<project>/<sha256>。
- Attempt和Step目前只存在内存结果，尚未提交到WorkspaceUnitOfWork。
- 还没有检查点持久化，进程崩溃后不能从本次内存Runner恢复。
- 真实CommandAdapter必须采用包装器写spool路径，不能直接复制Fake的“适配器返回bytes”方案。

## 7. 验证

- pytest完整测试：55 passed。
- ruff check：通过。
- ruff format --check：152个文件全部符合格式。
- mypy strict：97个源文件无问题。
