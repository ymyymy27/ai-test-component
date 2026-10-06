# CD-001 HTTP Assertion 映射确认

日期：2026-10-06  
提出方：D包 郭  
结论：**HTTP assertion result 统一转换成现有 VerificationFact，不新增原始 HTTP assertion 字段，不提升 ExecutionFacts 主版本**

## 1. 映射规则

| HttpAssertionResult／exchange | VerificationFact 表现 |
| --- | --- |
| `matched=true` | `observation=matched` |
| `matched=false` | `observation=mismatched` |
| HTTP 网络/查询错误 | `observation=query_error`，保留安全 gap |
| 无结果／无法读取断言对象 | `observation=no_result`，保留缺口 |
| 原始请求或响应 | 通过 `actual_result_ref`、`evidence_refs` 引用已保存材料 |

## 2. 不新增字段的原因

- ExecutionFacts 1.0 已有 `verifications` 数组，形状能够表达 HTTP assertion 观察事实。
- 原始 expected／actual 不应复制成新的未脱敏字段；需要追溯时保存为对象证据并按摘要引用。
- 新增 `http_assertions` 原始字段会改变公开 Schema，需要另行跨包合同主版本变更，当前没有必要。

## 3. D 侧消费

D 只把 `VerificationFact` 作为业务断言/核验事实输入之一，不因 HTTP 2xx、请求成功或响应文本直接判通过。Run 级证据等级仍由 D 的 DecisionFacts/evaluate_review 派生。

## 4. 当前实现

- `HttpAdapter._enrich()` 产生 `HttpAssertionResult`。
- `http_assertion_verifications()` 将结果转为 domain `Verification`。
- `ExecutionFactsAssembler._verification_fact()` 统一映射到 `ExecutionFacts.verifications`。
- 该链路不新增 Schema 主版本。