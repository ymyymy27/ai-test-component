"""AI 辅助用例侧入口：模型出站编排。

架构文档《01-项目与计划》第 9 节「AI 调用边界」把**模型调用的用例侧**指定在本模块。
实现位于 `aitest.application.planning.model_orchestration`，本模块只做**显式转出**，
使架构文档指定的位置与真实实现一致，调用方不必知道内部布局。

三条边界（同第 9 节）：

- 模型配置**独立于平台上传**；本模块不涉及上传；
- 模型**只输出草稿**：编排产出 `GeneratedContent(status=draft)`；
- 迟到响应**不覆盖人工版本**：由 `settle_response()` 判定。

所需的三个端口（`MaterialProjector` / `ModelCaller` / `CredentialResolver`）
是 B 侧窄接口，定义在 `aitest.application.planning.model_ports`；
A 的 `ProjectionPort` / `ModelProvider` / `SecretPort` 签名落地后由转接头对接
（见 `docs/接口对接/B-A-端口与保存需求.md`）。
"""

from __future__ import annotations

from aitest.application.planning.model_orchestration import (
    OUTBOUND_BLOCKED,
    OUTBOUND_DRAFT_READY,
    OutboundOutcome,
    OutboundRequest,
    ResponseSettlement,
    outbound_request_id,
    policy_record_id,
    request_model_draft,
    settle_response,
)

__all__ = [
    "OUTBOUND_BLOCKED",
    "OUTBOUND_DRAFT_READY",
    "OutboundOutcome",
    "OutboundRequest",
    "ResponseSettlement",
    "outbound_request_id",
    "policy_record_id",
    "request_model_draft",
    "settle_response",
]
