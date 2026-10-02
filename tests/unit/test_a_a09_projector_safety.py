"""A-09：自定义 projector 的统一安全底线与落盘/出站过滤链联合验证。

覆盖：
- 自定义投影器原样放行、新增敏感键、返回非 Mapping、抛异常——均无法
  绕过出口脱敏（LocalAPI 装配即套 safeguard）；
- 模型正文（sk-/ghp_/JWT）、敏感键名、结构化错误消息在协议出口被过滤；
- 事务出口与 handler 出口同一份底线；
- 出站材料链 SafeMaterialProjector：结构化/纯文本凭据被过滤并登记缺口，
  干净材料才允许 COMPLETE；
- 安全报告/附件导出投影中不出现凭据字节。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.contracts.commands import Command
from aitest.contracts.redaction import safeguard_projector
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.infrastructure.projections import SafeMaterialProjector
from aitest.interfaces.local.api import LocalAPI, Session

_SESSION = Session(session_id="s-1", entry_kind="interactive_cli")
_SECRET = "sk-" + "A" * 24
_GITHUB_TOKEN = "ghp_" + "0" * 30


def _command(action: str = "report", *, request_id: str = "req-1") -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id="project-a09",
        intent_id=f"intent-{request_id}",
        expected_revision=0,
    )


def _api(**kwargs: object) -> LocalAPI:
    return LocalAPI(instance_id="core", workspace_id="ws", **kwargs)  # type: ignore[arg-type]


def _serialized(response: object) -> str:
    return json.dumps(response, default=lambda o: o.__dict__, ensure_ascii=False)


# ------------------------------------------------------------ 自定义投影器底线


def test_custom_projector_passing_raw_secret_is_still_redacted() -> None:
    def passthrough(value: Mapping[str, object]) -> Mapping[str, object]:
        return value  # 恶意/有缺陷：原样放行

    api = _api(
        handlers={"report": lambda _command: {"token": _SECRET, "ok": True}},
        credential_projector=passthrough,
    )
    response = api.dispatch(_command(), _SESSION)
    assert response.result is not None
    assert response.result["token"] == "[REDACTED]"
    assert _SECRET not in _serialized(response.result)


def test_custom_projector_injecting_new_sensitive_key_is_redacted() -> None:
    def injector(value: Mapping[str, object]) -> Mapping[str, object]:
        return {**dict(value), "api_key": _SECRET, "nested": {"x": _GITHUB_TOKEN}}

    api = _api(
        handlers={"report": lambda _command: {"safe": "data"}},
        projector=injector,
    )
    response = api.dispatch(_command(), _SESSION)
    serialized = _serialized(response.result)
    assert _SECRET not in serialized
    assert _GITHUB_TOKEN not in serialized
    assert response.result is not None
    assert response.result["api_key"] == "[REDACTED]"


def test_custom_projector_returning_non_mapping_falls_back_safely() -> None:
    def broken(value: Mapping[str, object]) -> Mapping[str, object]:
        return object()  # type: ignore[return-value]

    api = _api(
        handlers={"report": lambda _command: {"token": _SECRET}},
        projector=broken,
    )
    response = api.dispatch(_command(), _SESSION)
    assert _SECRET not in _serialized(response.result)


def test_custom_projector_raising_does_not_leak_input() -> None:
    def exploding(value: Mapping[str, object]) -> Mapping[str, object]:
        raise RuntimeError("projector exploded")

    api = _api(
        handlers={"report": lambda _command: {"secret": _SECRET, "k": 1}},
        projector=exploding,
    )
    response = api.dispatch(_command(), _SESSION)
    assert response.result is not None
    assert response.result["secret"] == "[REDACTED]"


def test_safeguard_projector_unit_handles_scalar_output() -> None:
    wrapped = safeguard_projector(lambda value: "raw " + _SECRET)  # type: ignore[arg-type]
    out = wrapped({"a": 1})
    assert _SECRET not in json.dumps(dict(out))


# ------------------------------------------------------------ 协议出口过滤


def test_model_body_secret_in_string_value_is_scrubbed() -> None:
    body = f"model answered with key {_SECRET} and gh {_GITHUB_TOKEN}"
    api = _api(handlers={"report": lambda _command: {"body": body}})
    response = api.dispatch(_command(), _SESSION)
    serialized = _serialized(response.result)
    assert _SECRET not in serialized
    assert _GITHUB_TOKEN not in serialized


def test_structured_error_message_is_scrubbed() -> None:
    def failing(command: Command) -> dict[str, object]:
        raise RuntimeError(f"upstream refused token={_SECRET}")

    api = _api(handlers={"report": failing})
    response = api.dispatch(_command(), _SESSION)
    assert response.error is not None
    assert _SECRET not in response.error.message
    assert "[REDACTED]" in response.error.message


def test_transaction_exit_uses_same_safety_floor() -> None:
    class FakeTransactionPort:
        def begin(self, **kwargs: object) -> dict[str, object]:
            return {"transaction": "t-1", "client_secret": _SECRET}

    api = _api(transaction_port=FakeTransactionPort())
    response = api.dispatch(_command("begin", request_id="req-begin"), _SESSION)
    assert response.result is not None
    assert response.result["client_secret"] == "[REDACTED]"


# --------------------------------------------- 安全报告与附件导出联合投影


def test_safe_report_and_attachment_export_contain_no_credentials() -> None:
    def report_handler(command: Command) -> dict[str, object]:
        return {
            "report": {
                "title": "验收报告",
                "sections": [
                    {"heading": "环境", "text": f"configured token: {_SECRET}"},
                ],
            },
            "attachments": [
                {"name": "run.log", "digest": "sha256:" + "a" * 64,
                 "preview": f"authorization: bearer {_GITHUB_TOKEN}"},
            ],
        }

    api = _api(handlers={"report": report_handler})
    response = api.dispatch(_command(), _SESSION)
    serialized = _serialized(response.result)
    assert _SECRET not in serialized
    assert _GITHUB_TOKEN not in serialized
    assert "[REDACTED]" in serialized


# ------------------------------------------------------------ 出站材料链


def test_outbound_material_chain_filters_structured_and_text_secrets() -> None:
    projector = SafeMaterialProjector()
    projection = projector.project(
        material={
            MaterialKind.DELIVERY_NOTE: (
                json.dumps({"api_key": _SECRET, "note": "ok"})
            ),
            MaterialKind.PROJECT_CONTEXT: f"safe line\nsecret={_GITHUB_TOKEN}\n",
            MaterialKind.TEMPLATE_CONTENT: "clean printable content",
        },
        source_snippets_enabled=True,
    )
    projected = {item.material_kind: item for item in projection.projected}
    # 结构化命中：保留脱敏后的安全投影，同时登记缺口（不能以 complete 夹带）。
    assert MaterialKind.DELIVERY_NOTE in projected
    assert _SECRET not in projected[MaterialKind.DELIVERY_NOTE].projected_text
    # 纯文本：凭据整行丢弃，其余行保留。
    context_text = projected[MaterialKind.PROJECT_CONTEXT].projected_text
    assert _GITHUB_TOKEN not in context_text
    assert "safe line" in context_text
    excluded_paths = {path for _kind, path in projection.excluded}
    assert any("delivery_note" in path for path in excluded_paths)
    serialized = json.dumps(
        [item.projected_text for item in projection.projected],
        ensure_ascii=False,
    )
    assert _SECRET not in serialized
    assert _GITHUB_TOKEN not in serialized
    assert projection.status.value == "partial"


def test_outbound_chain_complete_only_when_all_clean() -> None:
    projection = SafeMaterialProjector().project(
        material={MaterialKind.PROJECT_CONTEXT: "entirely clean context"},
        source_snippets_enabled=True,
    )
    assert projection.status.value == "complete"
    assert projection.excluded == ()


def test_outbound_chain_drops_credential_line_but_keeps_others() -> None:
    projection = SafeMaterialProjector().project(
        material={
            MaterialKind.PROJECT_CONTEXT: "real fact line\npassword=hunter2\nend"
        },
        source_snippets_enabled=True,
    )
    item = projection.projected[0]
    assert "real fact line" in item.projected_text
    assert "password=hunter2" not in item.projected_text
    # 真实摘要必须基于过滤后字节。
    import hashlib

    assert item.digest == "sha256:" + hashlib.sha256(
        item.projected_text.encode("utf-8")
    ).hexdigest()


# ------------------------------------------------------------ 自定义出口不可绕过出站链


def test_safeguard_wraps_custom_projector_used_in_report_chain() -> None:
    """自定义出口即便在报告链中把秘密塞回顶层结构，也无法穿过底线。"""

    def custom(value: Mapping[str, object]) -> Mapping[str, object]:
        return {"summary": "ok", "authorization": f"Bearer {_SECRET}"}

    wrapped = safeguard_projector(custom)
    out = wrapped({"anything": 1})
    assert out["authorization"] == "[REDACTED]"
    assert _SECRET not in json.dumps(dict(out))
