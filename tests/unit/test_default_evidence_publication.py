"""Default commands need actual durable output evidence, not only spool facts."""
import sys

from aitest.contracts.execution_facts import ExecutionFacts
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_default_step_execution import ActualCommandResolver, execution_command
from tests.unit.test_execution_authorization_origin import review, save
from tests.unit.test_initial_run_registration import register


def test_default_command_publishes_output_evidence_before_success_reply(authoritative):
    core, inputs, source = authoritative
    facts = register(core, prepare(core, inputs).result)
    service = core.execution_authorizations
    service.action_resolver = ActualCommandResolver(core.unit_of_work)
    parameters = service.prepare(
        project_id=inputs.project_id, run_id=facts.run_id, step_id=facts.steps[0].step_id,
        intent_id='evidence-execution-intent', request_id='evidence-action',
    )
    _, action = service.resolver.read(inputs.project_id, parameters['execution_action_id'])
    actor, challenge = review(service, inputs.project_id, action, parameters)
    save(service, inputs.project_id, action, parameters, actor, challenge)
    port = CommandAdapter(handle_store=FileExecutionHandleStore(core.workspace.root))
    port.register(CommandRegistration('public-python', sys.executable, source))
    core.step_execution.execution_port = port
    response = core.api.dispatch(execution_command(inputs.project_id, action, parameters), RELAY)
    assert response.error is None, response.error
    saved = ExecutionFacts.model_validate(response.result['execution_facts'])
    assert saved.attempts[0].state.value == 'completed'
    assert saved.attempts[0].output_blocks
    assert saved.evidence_refs, 'default command never published permanent evidence references'
