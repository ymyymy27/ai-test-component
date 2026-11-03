"""Review-only probes. Zero exit means current defects are reproduced, not fixed.

Known component fixtures are explicit; no real Trae/AC is claimed. Product code unchanged.
"""
from __future__ import annotations
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from aitest.application.review import defect_management
from aitest.application.review.authoritative_basis import SavedCaseBasisCheck
from aitest.application.review.execution_decision_adapter import build_decision_facts
from aitest.application.review.saved_defects import blocking_state, issue_to_payload
from aitest.application.review.saved_reports import SavedReportService
from aitest.contracts.execution_facts import ExecutionFacts, VerificationFact
from aitest.domain.review.defects import IssueRecord, IssueSeverity, IssueStatus
from aitest.interfaces.local.api import LocalAPI, Session, EntryKind
from aitest.contracts.commands import Command
from tests.unit.test_saved_defect_management import _Harness as Defects, _facts as defect_facts, _evidence, _verified_source as defect_source, _ready_issue, _CLOSE_PARAMETERS, _Objects
from tests.unit.test_reuse_consumption import _outcome, _facts, _attempt, _verified_source, _verification, _reference
from tests.unit.test_case_reuse_selection import _harness as selection_harness, _command as selection_command
from tests.unit.test_saved_reports import _Harness as Reports, _create
from tests.unit.test_saved_exports import _export

runtime=ROOT/'.runtime'/('fix-review-'+uuid4().hex)
runtime.mkdir(parents=True)
assert runtime.resolve().is_relative_to((ROOT/'.runtime').resolve())
observed={}

# D-11: saved failed business verification is not consumed by closure; parameter is.
failed=VerificationFact.model_validate(dict(verification_id='verify-failed',verification_of='attempt-1',business_object_id='order-1',query_method='read_only_business_query',observation='mismatched',target_deployment_ref='deployment-1',evidence_refs=['evidence-1'],gap_ids=[],verified_at='2026-10-13T08:00:00+00:00'))
facts=defect_facts(evidence=(_evidence(),),verifications=(defect_source(),))
facts=ExecutionFacts.model_validate({**facts.model_dump(mode='json'),'verifications':[failed.model_dump(mode='json')]})
h=Defects(runtime/'closure',facts=facts,objects=_Objects())
closure=h.service._closure_evidence(_ready_issue(),dict(_CLOSE_PARAMETERS))
closed=defect_management.close_issue(_ready_issue(),expected_revision=5,evidence=closure)
observed['D-11']={'scope':'production closure derivation plus domain closure; real reference repository, explicit facts/basis/object fixtures','saved_observation':facts.verifications[0].observation.value,'caller_assertion_outcome':_CLOSE_PARAMETERS['assertion_outcome'],'satisfies_original_criteria':closure.satisfies_original_criteria,'closed_status':closed.status.value}
assert closed.status.value=='closed' and closure.satisfies_original_criteria

# C-17: valid real-path-style source facts cannot match; weak source variants can R.
source=_facts(run_id='run-source',attempts=(_attempt(),),source_verifications=(_verified_source(),),verifications=(_verification(),))
cases={'control':source,'source_attempt_not_current':source.model_copy(update={'attempts':(source.attempts[0].model_copy(update={'is_current':False}),)}),'unrelated_no_result_verification':source.model_copy(update={'verifications':(source.verifications[0].model_copy(update={'verification_of':'other-attempt','observation':__import__('aitest.contracts.execution_facts',fromlist=['VerificationObservationFact']).VerificationObservationFact.NO_RESULT}),)})}
reuse={}
for name,current in cases.items():
    current=ExecutionFacts.model_validate(current.model_dump(mode='json'))
    result=_outcome(source=current)
    reuse[name]={'reused':sorted(result.reused),'denials':result.named_reasons}
digest='sha256:'+'a'*64
actual_path_source=_facts(run_id='run-source',attempts=(_attempt(),),source_verifications=(_verified_source(materialized=str(runtime/'workdirs/run-source')),),verifications=(_verification(),))
current=actual_path_source.source_verifications[0].model_copy(update={'expected_source_binding_digest':digest,'observed_source_digest':digest})
result=_outcome(source=actual_path_source.model_copy(update={'source_verifications':(current,)}),references=(_reference(source_content_identity=digest),))
reuse['path_vs_content_identity']={'reused':sorted(result.reused),'denials':result.named_reasons}
adapted=build_decision_facts(_facts(),reuse=_outcome())
reuse['decision_consumption']={'R':sorted(adapted.coverage.reused),'V':sorted(adapted.coverage.verified),'P':sorted(adapted.coverage.passed)}
observed['C-17']={'scope':'typed ExecutionFacts component boundary and pure consumption, source/evidence/basis readers are explicit fixtures','cases':reuse}
assert reuse['source_attempt_not_current']['reused']==['case-1']
assert reuse['unrelated_no_result_verification']['reused']==['case-1']
assert reuse['path_vs_content_identity']['reused']==[]
assert reuse['decision_consumption']=={'R':['case-1'],'V':[],'P':[]}

# C-18: current choice revision is ignored, so stale expected=0 overwrites at @2.
core,selection,inspection=selection_harness(runtime/'selection')
try:
    first=selection.apply(selection_command())
    second=selection.apply(selection_command(request_id='req-stale',intent_id='intent-stale',expected_revision=0))
    observed['C-18']={'scope':'real default UOW/repository; inspection and controlled actor fixtures','first':dict(first),'stale_expected_revision':0,'second':dict(second)}
    assert second['revision']==2
finally:
    core.lifetime_lock.release()

# D-12: reading only caller-supplied issue IDs omits existing P1 issues.
reports=Reports(runtime/'reports')
issue=IssueRecord(issue_id='issue-blocking',project_id=reports.facts.project_id,revision=1,title='blocking defect',status=IssueStatus.CONFIRMED,severity=IssueSeverity.P1,owner='owner',confirmed_by='session:user')
reports.repository.append('issue',issue.issue_id,0,issue_to_payload(issue,links={'run_id':reports.facts.run_id,'case_id':reports.cases[0],'module_ids':('module-1',),'layers':('L2',),'attempt_id':None,'regressions':[]},updated_sequence=1,open_state=True,blocking='blocking'))
omitted=reports.service._issues(reports.facts.project_id,())
included=reports.service._issues(reports.facts.project_id,(issue.issue_id,))
observed['D-12']={'scope':'production issue-reading method with real issue repository; report facts/preparation component fixtures','saved_issue_blocking_state':blocking_state(included[0]),'omitted_issue_ids':[x.issue_id for x in omitted],'included_issue_ids':[x.issue_id for x in included]}
assert not omitted and blocking_state(included[0])=='blocking'

# D-13: late confirmations are never looked up when the frozen label is pending.
check=SavedCaseBasisCheck.__new__(SavedCaseBasisCheck)
check._override=None
check._entry=lambda *_:SimpleNamespace(assertion_basis_state=SimpleNamespace(value='present_unconfirmed'),confirmation_refs=())
check._bases=Mock()
result=check('project-1','intent-1','case-1')
observed['D-13']={'scope':'pending frozen-entry branch; confirmation reader mocked, static implementation separately reviewed','effective_confirmed':result,'confirmation_reader_calls':check._bases.read.call_count}
assert not result and check._bases.read.call_count==0

# D-14: immutable export intent replay is resolved after current report/reviews.
_create(reports)
first=_export(reports,review_ids=[],run_id=None)
_create(reports,expected=1,intent_id='intent-report-2',request_id='req-report-2')
try:
    replay=_export(reports,review_ids=[],run_id=None,request_id='req-repeat-export')
    failure=None
except Exception as error:
    replay=None
    failure={'type':type(error).__name__,'code':getattr(error,'code',None),'message':str(error)}
observed['D-14']={'scope':'production report/export services, real UOW/object/artifact/projector; saved preparation/runtime fixtures','first_export':first,'current_report_revision':reports.saved_report('report-1')['content_revision'],'same_intent_same_command_replay':replay,'error':failure}
assert failure is not None and failure['code']=='INTENT_CONFLICT'

# A-20: extra registered write aliases are outside the maintenance write gate.
invoked=[]
api=LocalAPI(instance_id='maintenance-probe',workspace_id='workspace-1',handlers={'save_rules':lambda cmd:invoked.append(cmd.action) or {'entered':True},'import_rules':lambda cmd:invoked.append(cmd.action) or {'entered':True}},maintenance=lambda:'migration active')
gate={}
for name in ('save_rules','import_rules'):
    response=api.dispatch(Command(action=name,request_id='request-'+name,project_id='project-1',intent_id='intent-'+name,expected_revision=0,parameters={}),Session('session-1',EntryKind.INTERACTIVE_CLI,True))
    gate[name]={'error':None if response.error is None else response.error.code,'result':response.result}
observed['A-20']={'scope':'actual LocalAPI maintenance dispatch; harmless marker handlers, no business files mutated','responses':gate,'entered_handlers':invoked}
assert gate['save_rules']['error']=='maintenance_required' and gate['import_rules']['error'] is None, gate

output=Path(__file__).with_suffix('.json')
output.write_text(json.dumps({'baseline':'a51c2b4','product_modified':False,'probes':observed},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Reproduced',','.join(observed),'->',output.name)
