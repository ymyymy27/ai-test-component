from aitest.contracts.prepared_run import BindingFormFact, EnvironmentIsolationModeFact
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
