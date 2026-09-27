# 闭环组装与Evidence发布说明

版本：0.1  
日期：2026-09-27  
状态：本地闭环组装已实现并验证  
分支：feat/package-c-execution

## 1 ExecutionFacts生产组装

- src/aitest/application/execution/facts.py

实现Run、Step、Attempt、EvidenceRef、Verification、来源验证、缺口和覆盖摘要到ExecutionFacts的映射。
stdout/stderr独立游标映射为output_cursors数组，ExitFact包含timeout和采集完整性。

## 2 内容寻址对象库

- src/aitest/infrastructure/file_store/objects.py

对象保存到objects/<project_id>/<sha256>，按实际字节计算摘要，读取时重新校验长度和摘要。

## 3 EvidenceRef发布

- src/aitest/application/evidence/publication.py

流程为Spool块读取、对象库发布、EvidenceRef生成。证据引用保留项目、来源实例、Run/Step/Attempt、代码身份、摘要、完整性、脱敏和投影状态。

## 4 Checkpoint持久化与恢复接入

- src/aitest/infrastructure/file_store/checkpoints.py
- src/aitest/application/execution/recovery.py
- src/aitest/application/execution/runner.py

每个Attempt保存一个checkpoints/<attempt_id>.json；SerialRunner开始时写started检查点，终态或待核实状态后更新。recover_pending扫描记录、重新inspect句柄、抢救Spool并回写恢复决策。

## 5 依赖失效接入

SerialRunner.invalidate_dependencies调用精确依赖失效规则，只失效引用受影响上游Attempt的活跃下游，并更新checkpoint。

## 6 RedactionSummary持久化

过滤时记录resolved_secret、key_value、bearer、basic、github_token、openai_key和jwt命中。每流写入spool/<attempt_id>/redaction-<stream>.json，块和EvidenceRef指向同一summary_id。

## 7 本地完整闭环

tests/recovery/test_execution_closure.py验证：

CommandAdapter -> Redactor -> FileSpoolStore -> Checkpoint -> EvidencePublisher -> FileObjectStore -> EvidenceRef -> ExecutionFactsAssembler -> FakeUnitOfWork

## 8 验证

- pytest：177 passed。
- ruff check：通过。
- 本次修改文件ruff format检查：通过。
- mypy strict：101个源文件无问题。

## 9 尚未完成

- A包正式WorkspaceUnitOfWork替换FakeUnitOfWork。
- D包正式确认C-D合同。
- composition root启动时自动调用recover_pending。
- 掉电一致性、超大单行输出和真实宿主验收。
