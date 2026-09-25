"""Lock the run vocabulary shared by the domain rules and the contract boundary.

The three enums are declared twice on purpose: `domain/` must not import
`contracts/` (see `tests/architecture/test_boundaries.py`), so each layer carries
its own declaration. This test is the guard that keeps the two in step.

Without it, a value can be changed on one side and silently disagree on the other
— which is exactly the failure mode recorded as C-01 in
`docs/接口对接/B-C-PreparedRun与词汇表合同.md`.
"""

from aitest.contracts.prepared_run import (
    ConclusionCeilingFact,
    RunDriverFact,
    RunTierFact,
)
from aitest.contracts.prepared_run import (
    conclusion_ceiling_for as contract_ceiling_for,
)
from aitest.domain.planning.plans import ConclusionCeiling, RunDriver, RunTier
from aitest.domain.planning.plans import conclusion_ceiling_for as domain_ceiling_for


def test_run_tier_values_match() -> None:
    assert {tier.value for tier in RunTier} == {tier.value for tier in RunTierFact}


def test_run_driver_values_match() -> None:
    assert {driver.value for driver in RunDriver} == {driver.value for driver in RunDriverFact}


def test_conclusion_ceiling_values_match() -> None:
    assert {ceiling.value for ceiling in ConclusionCeiling} == {
        ceiling.value for ceiling in ConclusionCeilingFact
    }


def test_ceiling_derivation_agrees_across_layers() -> None:
    for tier in RunTier:
        assert domain_ceiling_for(tier).value == contract_ceiling_for(
            RunTierFact(tier.value)
        ).value
