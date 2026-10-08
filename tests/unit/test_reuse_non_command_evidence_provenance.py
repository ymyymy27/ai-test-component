"""历史复用必须核对**所有证据类型**的准确 ``evidence_ref`` 记录（C-01/C-05）。

原实现只对 ``COMMAND_OUTPUT`` 核对准确 ``evidence_ref`` 记录与脱敏摘要来源；
验证/HTTP/外部导入等证据只核对对象字节。于是伪造或漂移的历史引用（换来源、
换修订、声称脱敏摘要）仍会被当作已核对的可复用来源。本文件固定该缺口已修。
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.facts import _evidence_fact
from aitest.application.execution.reuse_material import validate_source_material
from aitest.contracts.execution_facts import RedactionSummaryFact
from aitest.domain.evidence.evidence import EvidenceRef
from tests.unit.test_permanent_redaction_summary import saved_summary

_REFERENCE = TypeAdapter(EvidenceRef)

EXTRA_ID = "verification-1"


def _source_with_extra_evidence(
    tmp_path, *, kind: str = "verification", summary: bool = False
):
    """已提交命令检查点 + 一条带准确记录的**非命令**证据。"""
    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    facts = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint).facts
    template = dict(
        unit.repo.read(
            aggregate_kind="evidence_ref",
            record_id=facts.evidence_refs[0].evidence_id,
            revision=1,
        ).payload
    )
    stored = collector.objects.publish_bytes(
        "project-1", b"verified by an independent query\n", media_type="text/plain"
    )
    raw = {
        **deepcopy(template),
        "evidence_id": EXTRA_ID,
        "evidence_revision": 1,
        "evidence_kind": kind,
        "object_digest": stored.digest,
        "object_size": stored.size,
        "media_type": stored.media_type,
        "redaction_summary_ref": None,
    }
    unit.begin("extra-evidence", "project-1")
    unit.stage_record_exact(
        aggregate_kind="evidence_ref",
        record_id=EXTRA_ID,
        expected_revision=0,
        payload=raw,
    )
    unit.commit()
    fact = _evidence_fact(_REFERENCE.validate_python(raw), {})
    if summary:
        fact = fact.model_copy(
            update={
                "redaction_summary": RedactionSummaryFact(
                    policy_version="aitest.redaction/1.0",
                    filtered_streams=("stdout",),
                    filtered_ranges=("stdout:0-1",),
                    replacement_count=0,
                    completeness="complete",
                )
            }
        )
    selected = SimpleNamespace(
        facts=facts.model_copy(update={"evidence_refs": (*facts.evidence_refs, fact)}),
        steps=(SimpleNamespace(attempt=facts.attempts[0], checkpoint=checkpoint),),
    )
    return selected, unit, collector


@pytest.mark.parametrize("kind", ["verification", "http_response", "external_import"])
def test_non_command_evidence_with_its_exact_reference_is_accepted(tmp_path, kind):
    selected, unit, collector = _source_with_extra_evidence(tmp_path, kind=kind)
    validate_source_material(selected, collector.objects, collector.spool, unit.repo)


@pytest.mark.parametrize("kind", ["verification", "http_response", "model_response"])
def test_non_command_evidence_cannot_hide_a_drifted_exact_reference(tmp_path, kind):
    selected, unit, collector = _source_with_extra_evidence(tmp_path, kind=kind)
    original = unit.repo.read
    reader = Mock(wraps=unit.repo)

    def corrupt(**kwargs):
        record = original(**kwargs)
        if kwargs.get("record_id") != EXTRA_ID:
            return record
        raw = deepcopy(record.payload)
        raw["capture_source"] = (
            "manual" if raw["capture_source"] != "manual" else "plugin_runtime"
        )
        return SimpleNamespace(**kwargs, payload=raw)

    reader.read.side_effect = corrupt
    with pytest.raises(ValueError, match="evidence reference"):
        validate_source_material(selected, collector.objects, collector.spool, reader)


def test_non_command_evidence_without_its_exact_reference_is_rejected(tmp_path):
    selected, unit, collector = _source_with_extra_evidence(tmp_path)
    original = unit.repo.read
    reader = Mock(wraps=unit.repo)

    def absent(**kwargs):
        if kwargs.get("record_id") == EXTRA_ID:
            raise FileNotFoundError("synthetic missing non-command reference")
        return original(**kwargs)

    reader.read.side_effect = absent
    with pytest.raises(ValueError, match="evidence reference is unreadable"):
        validate_source_material(selected, collector.objects, collector.spool, reader)


def test_non_command_evidence_revision_cannot_be_swapped(tmp_path):
    selected, unit, collector = _source_with_extra_evidence(tmp_path)
    facts = selected.facts
    selected.facts = facts.model_copy(
        update={
            "evidence_refs": (
                *facts.evidence_refs[:-1],
                facts.evidence_refs[-1].model_copy(update={"evidence_revision": 2}),
            )
        }
    )
    with pytest.raises(ValueError, match="evidence reference"):
        validate_source_material(selected, collector.objects, collector.spool, unit.repo)


def test_non_stream_evidence_cannot_claim_a_redaction_summary(tmp_path):
    selected, unit, collector = _source_with_extra_evidence(tmp_path, summary=True)
    with pytest.raises(ValueError, match="non-stream evidence"):
        validate_source_material(selected, collector.objects, collector.spool, unit.repo)


def test_record_with_a_summary_reference_cannot_back_summary_free_evidence(tmp_path):
    """记录声称脱敏摘要、内联事实却没有 ⇒ 引用与冻结投影不一致，拒绝。"""
    selected, unit, collector = _source_with_extra_evidence(tmp_path)
    original = unit.repo.read
    reader = Mock(wraps=unit.repo)

    def attach(**kwargs):
        record = original(**kwargs)
        if kwargs.get("record_id") != EXTRA_ID:
            return record
        raw = deepcopy(record.payload)
        raw["redaction_summary_ref"] = "redaction:attempt-1:stdout"
        return SimpleNamespace(**kwargs, payload=raw)

    reader.read.side_effect = attach
    with pytest.raises(ValueError, match="evidence reference"):
        validate_source_material(selected, collector.objects, collector.spool, reader)
