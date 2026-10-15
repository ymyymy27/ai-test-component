"""Frozen host-owned HTTP query registry; no query URL is accepted from a command."""

import os
from pathlib import Path

from aitest.application.ports import CapturedBusinessVerification, SecretPort, VerificationRequest
from aitest.domain.json_material import decode_json
from aitest.infrastructure.adapters.execution.business_query import (
    HttpBusinessQueryReader,
    HttpBusinessQuerySpec,
)
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter
from aitest.infrastructure.python_environment import RegisteredPythonEnvironmentResolver

CONFIG_VERSION = "aitest.business-queries/1"
MAX_CONFIG_BYTES = 256 * 1024


class HostConfiguredBusinessQueries:
    def __init__(self, path: Path, secrets: SecretPort | None = None):
        self.secrets = secrets
        self.entries: dict[str, HttpBusinessQuerySpec] = {}
        self.valid = False
        try:
            if not path.is_absolute():
                raise ValueError("query configuration path must be absolute")
            RegisteredPythonEnvironmentResolver._no_links(path)
            with path.open("rb") as stream:
                raw = stream.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ValueError("query configuration exceeds limit")
            data = decode_json(raw)
            if (
                not isinstance(data, dict) or set(data) != {"schema_version", "queries"}
                or data["schema_version"] != CONFIG_VERSION
                or not isinstance(data["queries"], list) or len(data["queries"]) > 256
            ):
                raise ValueError("invalid query configuration")
            for row in data["queries"]:
                if (
                    not isinstance(row, dict)
                    or set(row) - {"credential_reference"} != {
                        "query_method", "target_deployment_ref", "url_template", "object_id_field"}
                    or any(not isinstance(v, str) or not v.strip() for v in row.values())
                    or row["query_method"].count(":") != 1
                    or any(not part.strip() for part in row["query_method"].split(":"))
                    or row["query_method"] in self.entries
                ):
                    raise ValueError("invalid or duplicate query registration")
                self.entries[row["query_method"]] = HttpBusinessQuerySpec(
                    **{key: value for key, value in row.items() if key != "query_method"}
                )
            self.valid = True
        except (OSError, ValueError, TypeError):
            # Discard partial configuration; never expose raw file content in diagnostics.
            self.entries.clear()

    @classmethod
    def from_environment(
        cls, secrets: SecretPort | None = None,
    ) -> "HostConfiguredBusinessQueries | None":
        value = os.environ.get("AITEST_BUSINESS_QUERIES")
        return cls(Path(value), secrets) if value else None

    def validate(self, request: VerificationRequest) -> None:
        spec = self.entries.get(request.query_method)
        if (
            not self.valid or spec is None
            or spec.target_deployment_ref != request.target_deployment_ref
        ):
            raise ValueError("independent query target is not registered in host configuration")

    def capture(self, request: VerificationRequest) -> CapturedBusinessVerification:
        self.validate(request)
        spec = self.entries[request.query_method]
        secret = None
        try:
            if spec.credential_reference is not None:
                if self.secrets is None:
                    raise ValueError("independent query credentials are unavailable")
                try:
                    secret = self.secrets.resolve(spec.credential_reference, purpose="http")
                except Exception:
                    raise ValueError("independent query credentials are unavailable") from None
            reader = HttpBusinessQueryReader(spec, secret=secret)
            return BusinessVerificationAdapter(reader).capture(request)
        finally:
            if secret is not None:
                secret.clear()
