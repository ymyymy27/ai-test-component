import hashlib
import json
from pathlib import Path

import pytest

from aitest.contracts.execution_facts import ExecutionFacts

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "docs/接口对接/已完成/CD-001-ExecutionFacts/fixtures"
)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "success.json",
        "failure.json",
        "unknown.json",
        "quick.json",
        "timeout.json",
        "multistream.json",
        "non_utf8.json",
        "business_failure.json",
    ],
)
def test_public_execution_facts_fixture_validates(fixture_name: str) -> None:
    payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    facts = ExecutionFacts.model_validate(payload)
    assert facts.schema_version == "aitest.execution-facts/1.0"
    consumer = Path(__file__).parent / "fixtures/execution_facts" / fixture_name
    assert payload == json.loads(consumer.read_text(encoding="utf-8"))


def test_public_non_utf8_fixture_matches_sidecar_bytes() -> None:
    payload = json.loads((FIXTURES / "non_utf8.json").read_text(encoding="utf-8"))
    facts = ExecutionFacts.model_validate(payload)
    blocks = {block.stream_name: block for block in facts.attempts[0].output_blocks}
    for stream_name, filename in (
        ("stdout", "non_utf8.stdout.bin"),
        ("stderr", "non_utf8.stderr.bin"),
    ):
        content = (FIXTURES / filename).read_bytes()
        block = blocks[stream_name]
        assert len(content) == block.length
        assert "sha256:" + hashlib.sha256(content).hexdigest() == block.digest
