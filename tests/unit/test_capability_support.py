from aitest.application.capability_support import evaluate_capability_support


def test_capability_requires_adapter_template_and_ac_evidence() -> None:
    missing_adapter = evaluate_capability_support(capability="http", adapter_version=None)
    assert missing_adapter.status == "unsupported"

    missing_template = evaluate_capability_support(capability="http", adapter_version="http/1.0")
    assert missing_template.status == "unverified"

    missing_ac = evaluate_capability_support(
        capability="http",
        adapter_version="http/1.0",
        template_ids=("http-workflow",),
    )
    assert missing_ac.status == "unverified"

    supported = evaluate_capability_support(
        capability="http",
        adapter_version="http/1.0",
        template_ids=("http-workflow", "http-workflow"),
        ac_evidence_verified=True,
    )
    assert supported.status == "supported"
    assert supported.template_ids == ("http-workflow",)
