# C-D ExecutionFacts夹具

版本：aitest.execution-facts/1.0

文件：

- success.json
- failure.json
- unknown.json
- quick.json
- timeout.json
- multistream.json
- non_utf8.json
- business_failure.json
- non_utf8.stdout.bin
- non_utf8.stderr.bin

说明：

- JSON文件全部通过ExecutionFacts Pydantic模型校验。
- 所有JSON夹具均由 `scripts/generate_cd001_fixtures.py` 通过
  `ExecutionFactsAssembler` 生成，不手写组装字段。
- 非UTF-8场景的摘要和长度以对应bin文件实际字节计算。
- 本目录是交付夹具的唯一公共副本。

## 运行中与修订边界

- running.json：R1，一个完成步骤、一个运行步骤、一个待执行步骤。
- pause_requested.json / paused.json：R2，同一运行的暂停请求与步骤边界暂停两个快照。
- revision_invalidated.json：R3，历史保留、失效依据与未执行步骤修订。

以上四份由同一生产装配器生成，并同步消费副本；生成命令为 `python scripts/generate_cd001_fixtures.py running pause_requested paused revision_invalidated`。`tests/contracts/test_halfway_execution_fixtures.py` 检查生产/消费一致、D 侧不得判成完整通过及连续状态。它们是合成合同样例，不是实际运行、持久修订对象或人工验收的证据。
