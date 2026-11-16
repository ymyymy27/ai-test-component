"""Host registration authentication uses purpose-bound secrets and real TLS."""

import json
import ssl
from pathlib import Path
from unittest.mock import Mock

import pytest

from aitest.contracts.secrets import ResolvedSecret
from aitest.infrastructure.business_query_configuration import HostConfiguredBusinessQueries
from tests.unit.test_configured_business_verification import basis, config, declared_request
from tests.unit.test_http_business_query import body_reply
from tests.unit.test_http_transport_limits import server


def registered(tmp_path, url, secrets):
    path = config(tmp_path / "queries.json", url)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["queries"][0]["credential_reference"] = "test-read"
    path.write_text(json.dumps(data), encoding="utf-8")
    return HostConfiguredBusinessQueries(path, secrets)


@pytest.mark.parametrize(
    "status,body",
    [
        (200, b'{"object_id":"order-1","paid":true}'),
        (401, b"{}"),
        (403, b"{}"),
    ],
)
def test_real_tls_sends_bearer_and_clears_value_on_all_statuses(
    tmp_path, monkeypatch, status, body
):
    certs = Path(__file__).resolve().parents[1] / "fixtures"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certs / "http_transport_test.crt", certs / "http_transport_test.key")
    create = ssl.create_default_context
    monkeypatch.setattr(
        ssl, "create_default_context", lambda: create(cafile=str(certs / "http_transport_test.crt"))
    )
    secret = ResolvedSecret("http", "test-read", "fixture", "synthetic-auth-query-371")
    port = Mock(resolve=Mock(return_value=secret))
    with server(body_reply(body, status), context) as (url, calls):
        registry = registered(tmp_path, ("https" + url[4:]).replace("127.0.0.1", "localhost"), port)
        req = declared_request(basis(), project_id="project-ticket", attempt_id="a")
        captured = registry.capture(req)
        assert captured.verification.observation.value == (
            "matched" if status == 200 else "query_error"
        )
        assert len(calls) == 1
        assert b"Authorization: Bearer synthetic-auth-query-371\r\n" in calls[0]
        assert secret.reveal() == ""
        port.resolve.assert_called_once_with("test-read", purpose="http")


@pytest.mark.parametrize("mode", ["missing", "wrong-purpose", "wrong-reference"])
def test_missing_or_wrong_scope_does_not_fall_back_to_anonymous(tmp_path, mode):
    secret = ResolvedSecret(
        "model" if mode == "wrong-purpose" else "http",
        "other" if mode == "wrong-reference" else "test-read",
        "fixture",
        "value",
    )
    port = Mock(
        resolve=Mock(
            side_effect=RuntimeError("do not leak") if mode == "missing" else None,
            return_value=secret,
        )
    )
    registry = registered(tmp_path, "https://example.invalid/", port)
    with pytest.raises(ValueError) as error:
        registry.capture(declared_request(basis(), project_id="project-ticket", attempt_id="a"))
    assert "do not leak" not in str(error.value)
    if mode != "missing":
        assert secret.reveal() == ""


def test_credentials_on_cleartext_target_rejected(tmp_path):
    assert not registered(tmp_path, "http://127.0.0.1:1/", Mock()).valid


def test_windows_native_credential_round_trip_is_purpose_bound_and_removed():
    import ctypes
    import sys
    from ctypes import wintypes
    from uuid import uuid4

    from aitest.infrastructure.credentials import SecretManager, SecretUnavailable, _CredentialW

    if sys.platform != "win32":
        pytest.skip("Windows credential manager only")
    reference = "aitest-query-fixture-" + uuid4().hex
    target = "aitest/http/" + reference
    raw = b"synthetic-native-query-fixture-37001"
    buffer = (wintypes.BYTE * len(raw)).from_buffer_copy(raw)
    record = _CredentialW()
    record.Type, record.TargetName, record.Persist = 1, target, 1
    record.CredentialBlobSize, record.CredentialBlob = len(raw), buffer
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    assert api.CredWriteW(ctypes.byref(record), 0), ctypes.get_last_error()
    manager = SecretManager.default()
    try:
        secret = manager.resolve(reference, purpose="http")
        assert secret.reveal().encode() == raw
        secret.clear()
        with pytest.raises(SecretUnavailable):
            manager.resolve(reference, purpose="model")
    finally:
        assert api.CredDeleteW(target, 1, 0), ctypes.get_last_error()
    assert not manager.has_secret(reference, purpose="http")
