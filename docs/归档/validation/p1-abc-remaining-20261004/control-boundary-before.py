import subprocess
from pathlib import Path
import sys
sys.path[:0]=[str(Path.cwd()),str(Path.cwd()/"src")]
from dataclasses import replace
from tests.unit.test_execution_control import _attempt,_run
from tests.unit.test_execution_observation_identity import observation
from aitest.domain.execution.runs import AttemptState,RunControlState
old=subprocess.check_output(['git','show','25fea85:src/aitest/application/execution/control.py']).decode()
from types import ModuleType
module=ModuleType('old_control_component')
sys.modules[module.__name__]=module
namespace=module.__dict__
exec(compile(old,'25fea85-control.py','exec'),namespace)
lines=['基线fix/25fea85，读取Git中的原控制规则在独立命名空间运行，不修改工作区源码。']
for state in (AttemptState.COMPLETED,AttemptState.CANCELLED,AttemptState.INVALIDATED):
 item=replace(_attempt(state),execution_handle_ref=None,exit_fact_ref=None)
 before=namespace['_requires_boundary_verification'](item)
 assert before is False
 lines.append(f'{state.value}: 原规则未要求核实（False），预期必须核实；反例确认。')
item,_,collection=observation()
item=replace(item,state=AttemptState.PENDING_VERIFICATION,exit_fact_ref=collection.exit_fact_ref)
before=namespace['RunControlService'](None).pause(replace(_run(),control_state=RunControlState.RUNNING),(item,))
assert before.run_state is RunControlState.PAUSE_REQUESTED
lines.append('同原进程已核对退出但业务核验待完成：原规则为pause_requested，预期paused并保留原业务待完成；反例确认。')
(private:=Path('.git/p1-abc-fix-20261003')/'control-boundary-before.log').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
