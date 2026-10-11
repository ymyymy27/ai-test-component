"""Selected historic proof and actual consumed ancestor fingerprints; no reuse grant."""

from collections.abc import Mapping

from aitest.application.execution.reuse_sources import CaseReuseSource
from aitest.application.planning.publish import payload_digest
from aitest.contracts.execution_facts import AttemptStateFact, CaptureCompletenessFact


def source_evidence_basis(source: CaseReuseSource) -> Mapping[str, object]:
    selected = {item.attempt.attempt_id for item in source.steps if item.attempt is not None}
    steps = {item.step.step_id for item in source.steps}
    facts = source.facts
    result: dict[str, object] = {}
    for name, field in (
        ("evidence_refs", "attempt_id"),
        ("source_check_results", "attempt_id"),
        ("verifications", "verification_of"),
        ("dependency_invalidations", "affected_attempt_id"),
    ):
        result[name] = tuple(sorted(
            payload_digest(item.model_dump(mode="json"))
            for item in getattr(facts, name, ()) if getattr(item, field) in selected or (
                name == "dependency_invalidations"
                and getattr(item, "affected_step_id", None) in steps
            )
        ))
    # These records have run/project scope, without an exact Attempt association.
    # A caller can request today's source boundary after a shared proof changes.
    for name in ("source_verifications", "mock_declarations", "unknowns", "gaps"):
        result[name] = tuple(sorted(
            payload_digest(item.model_dump(mode="json")) for item in getattr(facts, name, ())
        ))
    result["completeness"] = getattr(facts, "completeness", None)
    return result


def consumed_dependency_basis(source: CaseReuseSource) -> tuple[Mapping[str, object], bool]:
    """Follow saved actual consumption, including ancestors outside this case.

    The fingerprint checks identity/currentness, not value_ref/condition bytes.
    Equal fingerprints do not establish dependencies_valid or target consumption.
    """
    attempts = {item.attempt_id: item for item in source.facts.attempts}
    current = getattr(source.facts, "current_attempt_by_step", {})
    pending = [used.upstream_attempt_id for item in source.steps if item.attempt is not None
               for name in ("consumed_outputs", "consumed_conditions")
               for used in getattr(item.attempt, name, ())]
    result: dict[str, object] = {}
    verified = len(attempts) == len(source.facts.attempts)
    while pending:
        identity = pending.pop()
        if identity in result:
            continue
        fact = attempts.get(identity)
        if fact is None:
            verified = False
            result[identity] = None
            continue
        result[identity] = {
            "fact": payload_digest(fact.model_dump(mode="json")),
            "current_attempt_id": current.get(fact.step_id),
        }
        if (
            fact.is_current is not True or current.get(fact.step_id) != identity
            or fact.state is not AttemptStateFact.COMPLETED
            or fact.capture_completeness is not CaptureCompletenessFact.COMPLETE
        ):
            verified = False
        pending.extend(used.upstream_attempt_id
                       for name in ("consumed_outputs", "consumed_conditions")
                       for used in getattr(fact, name))
    return result, verified
