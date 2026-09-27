from typing import get_args

from aitest.contracts.prepared_run import (
    BindingFormFact,
    EnvironmentIsolationModeFact,
    FrozenCase,
    FrozenCaseStep,
)
from aitest.contracts.templates import TemplateItem
from aitest.domain.planning.plans import CaseLayer
from aitest.domain.project.context import BindingForm, IsolationMode


def test_binding_form_values_match_across_layers() -> None:
    """`domain/` 不得 import `contracts/`，故两层各自声明，由本测试锁定值一致。

    同一做法见 `tests/contracts/test_run_vocabulary.py`。
    """
    assert {form.value for form in BindingForm} == {fact.value for fact in BindingFormFact}


def test_binding_form_has_no_extra_or_missing_members() -> None:
    assert {form.name for form in BindingForm} == {fact.name for fact in BindingFormFact}


def test_isolation_mode_values_match_across_layers() -> None:
    """隔离方式是三态，布尔无法区分"未配置"与"显式不隔离"（需求 P1-FR07）。"""
    assert {mode.value for mode in IsolationMode} == {
        mode.value for mode in EnvironmentIsolationModeFact
    }


def test_isolation_mode_has_no_extra_or_missing_members() -> None:
    assert {mode.name for mode in IsolationMode} == {
        mode.name for mode in EnvironmentIsolationModeFact
    }


def test_isolation_mode_keeps_all_three_states_distinct() -> None:
    assert {mode.value for mode in IsolationMode} == {"venv", "none", "unmanaged"}


def _literal_values(annotation: object) -> set[str]:
    return {str(value) for value in get_args(annotation)}


def test_case_layer_matches_the_contract_literals() -> None:
    """层级在合同层以 `Literal["L1","L2","L3"]` 声明，不是枚举。

    因此锁定方式是"领域枚举取值集合 == 合同 Literal 参数集合"。
    见 `07-规则版本与计划用例领域层设计说明.md` 第 2.2 节。
    """
    domain_values = {layer.value for layer in CaseLayer}
    assert domain_values == {"L1", "L2", "L3"}
    assert domain_values == _literal_values(FrozenCase.model_fields["layer"].annotation)
    assert domain_values == _literal_values(
        FrozenCaseStep.model_fields["layer"].annotation
    )
    assert domain_values == _literal_values(
        TemplateItem.model_fields["layer"].annotation
    )


def test_case_layer_literals_agree_across_the_contract() -> None:
    """合同三处各自声明层级，必须彼此一致，防止各自漂移。"""
    declarations = [
        _literal_values(FrozenCase.model_fields["layer"].annotation),
        _literal_values(FrozenCaseStep.model_fields["layer"].annotation),
        _literal_values(TemplateItem.model_fields["layer"].annotation),
    ]
    assert declarations[0] == declarations[1] == declarations[2]
