from aitest.contracts.prepared_run import BindingFormFact
from aitest.domain.project.context import BindingForm


def test_binding_form_values_match_across_layers() -> None:
    """`domain/` 不得 import `contracts/`，故两层各自声明，由本测试锁定值一致。

    同一做法见 `tests/contracts/test_run_vocabulary.py`。
    """
    assert {form.value for form in BindingForm} == {fact.value for fact in BindingFormFact}


def test_binding_form_has_no_extra_or_missing_members() -> None:
    assert {form.name for form in BindingForm} == {fact.name for fact in BindingFormFact}
