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
- non_utf8.stdout.bin
- non_utf8.stderr.bin

说明：

- JSON文件全部通过ExecutionFacts Pydantic模型校验。
- 非UTF-8场景的摘要和长度以对应bin文件实际字节计算。
- 本目录是交付夹具的唯一公共副本。
