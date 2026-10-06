"""Reconcile an exact saved response; never repeat an external model call."""

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import datetime
from typing import cast

from aitest.application.planning.model_basis import ModelGenerationBasis
from aitest.application.planning.model_orchestration import (
    CREDENTIAL_FILTER_POLICY,
    OUTBOUND_UNRESOLVED,
    OutboundOutcome,
    OutboundRequest,
    _current_payload,
    _generation_identity,
    _publish_model_response,
    _recall_outcome,
    policy_record_id,
)
from aitest.application.planning.model_ports import ModelCallResult, ModelCallStatus
from aitest.application.planning.substrate import (
    RecordReader,
    UnitOfWork,
    current_record,
    read_scoped_record,
)
from aitest.application.ports import ModelResponseStore
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.domain.planning.model_outbound import MaterialKind, ModelTaskType, ResponseCurrency
from aitest.domain.planning.templates import TemplateRef


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("saved response text identity is missing")
    return value


def _integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("saved response revision/count is invalid")
    return value


def _same(left: object, right: object) -> bool:
    # Python equality alone equates True with 1; these are different identities.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def _request(payload: Mapping[str, object], project_id: str, request_id: str) -> OutboundRequest:
    if (
        payload.get("project_id") != project_id
        or payload.get("request_id") != request_id
        or payload.get("state") != "intent"
    ):
        raise ValueError("original model intent ownership/state is invalid")
    kinds = payload.get("material_kinds")
    if not isinstance(kinds, list) or not kinds or len(kinds) != len(set(map(str, kinds))):
        raise ValueError("original model material kinds are invalid")
    requested_at = datetime.fromisoformat(_text(payload.get("requested_at")))
    if requested_at.tzinfo is None or requested_at.utcoffset() is None:
        raise ValueError("original model intent time is not explicit")
    return OutboundRequest(
        project_id=project_id,
        request_id=request_id,
        task_type=ModelTaskType(_text(payload.get("task_type"))),
        policy_revision=_integer(payload.get("policy_revision"), 1),
        source_revision=_integer(payload.get("source_revision")),
        base_manual_revision=_integer(payload.get("base_manual_revision")),
        material_kinds=tuple(MaterialKind(_text(kind)) for kind in kinds),
        item_count=_integer(payload.get("item_count"), 1),
        projection_digest=_text(payload.get("projection_digest")),
        endpoint_address=_text(payload.get("endpoint_address")),
        model_id=_text(payload.get("model_id")),
        credential_purpose=_text(payload.get("credential_purpose")),
        requested_at=requested_at,
    )


def _response(body: Mapping[str, object]) -> tuple[ModelCallResult, Mapping[str, object]]:
    value = body.get("response")
    required = {
        "draft_text",
        "provider_request_id",
        "call_status",
        "error_kind",
        "error_detail_digest",
        "credential_filter",
        "provider_call_started",
        "observed_currency",
    }
    if (
        body.get("schema_version") != "aitest.model-response-receipt/1.0"
        or not isinstance(value, Mapping)
        or not required <= value.keys()
        or value.keys() - required - {"error_detail_chars"}
    ):
        raise ValueError("saved response shape is invalid")
    text, provider, error = value["draft_text"], value["provider_request_id"], value["error_kind"]
    if not isinstance(text, str) or (provider is not None and not isinstance(provider, str)):
        raise ValueError("saved response text/provider identity is invalid")
    if error is not None:
        _text(error)
    digest = value["error_detail_digest"]
    if digest is not None and (
        not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
    ):
        raise ValueError("saved error detail digest is invalid")
    if "error_detail_chars" in value:
        count = _integer(value["error_detail_chars"])
        if (count == 0) != (digest is None):
            raise ValueError("saved error detail length/digest disagree")
    started = value["provider_call_started"]
    if type(started) is not bool:
        raise ValueError("saved provider call state is unknown")
    currency = ResponseCurrency(_text(value["observed_currency"]))
    filter_fact = value["credential_filter"]
    if (
        not isinstance(filter_fact, Mapping)
        or set(filter_fact) != {"policy", "replacements", "filtered"}
        or filter_fact.get("policy") != CREDENTIAL_FILTER_POLICY
        or type(filter_fact.get("filtered")) is not bool
        or filter_fact["filtered"] != (_integer(filter_fact.get("replacements")) > 0)
    ):
        raise ValueError("saved credential filtering cannot be verified")
    result = ModelCallResult(
        status=ModelCallStatus(_text(value["call_status"])),
        draft_text=text,
        provider_request_id=provider,
        error_kind=error if isinstance(error, str) else None,
    )
    if not started and (
        result.status is not ModelCallStatus.FAILED
        or error != "basis_revoked_before_send"
        or currency is ResponseCurrency.CURRENT
    ):
        raise ValueError("saved unsent outcome is inconsistent")
    return result, value


def resolve_model_response(
    *,
    project_id: str,
    request_id: str,
    expected_revision: int,
    saved_response_ref: Mapping[str, object],
    reader: RecordReader,
    unit_of_work: UnitOfWork,
    response_store: ModelResponseStore,
    sources: SourceAnalysisService | None,
) -> OutboundOutcome:
    if type(expected_revision) is not int or expected_revision != 1:
        raise ValueError("response reconciliation requires original intent revision 1")
    original = read_scoped_record(
        reader,
        project_id=project_id,
        aggregate_kind="model_outbound_request",
        record_id=request_id,
        revision=1,
    )
    request = _request(original.payload, project_id, request_id)

    def unresolved() -> OutboundOutcome:
        return OutboundOutcome(
            status=OUTBOUND_UNRESOLVED,
            request=request,
            blocked_by=("original authority or safe response cannot be verified",),
            saved_response_ref=saved_response_ref,
        )

    try:
        identity = original.payload.get("generation_identity")
        if not isinstance(identity, Mapping) or not isinstance(
            identity.get("basis_identity"), Mapping
        ):
            return unresolved()
        if not _same(
            _generation_identity(request),
            {key: identity.get(key) for key in _generation_identity(request)},
        ):
            return unresolved()
        receipt = response_store.find(
            project_id=project_id, request_id=request_id, identity=identity
        )
        if receipt is None or not _same(asdict(receipt[0]), dict(saved_response_ref)):
            return unresolved()
        result, response = _response(receipt[1])
        request = replace(
            request,
            call_status=result.status.value,
            error_kind=result.error_kind,
            provider_request_id=result.provider_request_id,
        )
        current = _current_payload(reader, project_id=project_id, record_id=request_id)
        if current is not None and current.get("state") == "outcome":
            if not _same(current.get("generation_identity"), dict(identity)) or not _same(
                current.get("saved_response_ref"), dict(saved_response_ref)
            ):
                return unresolved()
            return _recall_outcome(reader, request, current)
        frozen = identity["basis_identity"]
        basis = ModelGenerationBasis(
            reader=reader,
            project_id=project_id,
            source_revision=request.source_revision,
            base_manual_revision=request.base_manual_revision,
            source_ref=frozen.get("source_ref"),
            manual_ref=frozen.get("manual_ref"),
            sources=sources,
        )
        if not _same(basis.identity(), dict(frozen)):
            return unresolved()
        project_revision = _integer(identity.get("project_revision"), 1)
        if not _same(identity.get("binding_revision"), basis.binding_revision):
            return unresolved()
        draft_kind = _text(identity.get("draft_kind"))
        template = TemplateRef(
            template_id=_text(identity.get("template_id")),
            version=_text(identity.get("template_version")),
        )
        basis.refresh_source()  # File/Git observation stays outside the short transaction.

        def currency() -> ResponseCurrency:
            project = current_record(
                reader, project_id=project_id, aggregate_kind="project", record_id=project_id
            )
            policy = current_record(
                reader,
                project_id=project_id,
                aggregate_kind="model_outbound_policy",
                record_id=policy_record_id(project_id),
            )
            return basis.currency(
                other_basis_current=(
                    project is not None
                    and project.revision == project_revision
                    and policy is not None
                    and policy.revision == request.policy_revision
                )
            )

        def slot_available() -> bool:
            return (
                current_record(
                    reader,
                    project_id=project_id,
                    aggregate_kind="generated_content",
                    record_id=f"draft:{project_id}:{draft_kind}:{request_id}:1",
                )
                is None
            )

        return _publish_model_response(
            request=request,
            result=result,
            identity=identity,
            reader=reader,
            unit_of_work=unit_of_work,
            intent_revision=1,
            draft_kind=draft_kind,
            template_ref=template,
            project_revision=project_revision,
            binding_revision=basis.binding_revision,
            source_revision=request.source_revision,
            basis=basis,
            current_currency=currency,
            slot_available=slot_available,
            filtered_text=result.draft_text,
            filter_fact=cast(Mapping[str, object], response["credential_filter"]),
            provider_call_started=cast(bool, response["provider_call_started"]),
            saved_response_ref=saved_response_ref,
            observed_currency=ResponseCurrency(str(response["observed_currency"])),
            error_detail_digest=response["error_detail_digest"],
            error_detail_chars=cast(int | None, response.get("error_detail_chars")),
        )
    except (ValueError, TypeError, KeyError, OSError, RuntimeError):
        return unresolved()
