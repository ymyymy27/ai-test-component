"""An unrelated progress update must not invalidate pre-reviewed independent actions."""

from dataclasses import replace

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.runner import SerialRunner
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_execution_authorization_origin import resolved as resolved
from tests.unit.test_execution_authorization_origin import review, save, started
from tests.unit.test_serial_runner import FakeExecutionPort


def test_independent_pregranted_action_survives_unrelated_progress(resolved):
    core, inputs, _, service, first_parameters, first = resolved
    coordinator = ExecutionCommitCoordinator(
        core.unit_of_work, records=core.unit_of_work.repo, execution_authorizations=service
    )
    facts = coordinator.read_current_facts(
        project_id=inputs.project_id, run_id=first.request.run_id
    )
    second_step = next(step for step in facts.steps if step.step_id != first.request.step_id)
    second_parameters = service.prepare(
        project_id=inputs.project_id,
        run_id=first.request.run_id,
        step_id=second_step.step_id,
        intent_id="independent-pregranted",
        request_id="second-action",
    )
    _, second = service.resolver.read(inputs.project_id, second_parameters["execution_action_id"])
    assert second.attempt.attempt_id != first.attempt.attempt_id
    actor, challenge = review(service, inputs.project_id, first, first_parameters)
    save(service, inputs.project_id, first, first_parameters, actor, challenge)
    actor, challenge = review(service, inputs.project_id, second, second_parameters)
    actor = replace(actor, interaction=replace(actor.interaction, interaction_id="second-gesture"))
    save(
        service,
        inputs.project_id,
        second,
        second_parameters,
        actor,
        challenge,
        request="second-grant",
    )
    service.validate_new(project_id=inputs.project_id, attempt=started(second))
    # Actual serial start consumes the original grant in the common file transaction.
    SerialRunner(FakeExecutionPort(), commit_coordinator=coordinator).start_attempt(
        first.attempt, first.request
    )
    service.validate_new(project_id=inputs.project_id, attempt=started(second))
