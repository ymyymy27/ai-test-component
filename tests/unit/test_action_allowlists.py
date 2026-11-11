"""Host/panel action allowlists must not drift from the core contract.

The shared panel keeps two explicit sets: ordinary writes go through
`writeDispatch`, human-owned actions through `dispatchControlled` and the
core-owned confirmation window.
"""

import re
from pathlib import Path

from aitest.contracts.commands import HUMAN_ACTIONS, PHASE_ONE_ACTIONS, READ_ACTIONS

ROOT = Path(__file__).resolve().parents[2]


def _ts_set(path: Path, name: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}:[^=]*= new Set\(\[(.*?)\]\);", text, re.S)
    if match is None:
        raise AssertionError(f"{name} not found in {path}")
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_read_only_and_plain_write_allowlists_match_the_core_contract() -> None:
    bridge = ROOT / "integrations/trae/src/core-bridge.ts"
    read_only = _ts_set(bridge, "READ_ONLY_ACTIONS")
    extension_plain = _ts_set(bridge, "PLAIN_WRITE_ACTIONS")
    core_plain = PHASE_ONE_ACTIONS - HUMAN_ACTIONS - READ_ACTIONS

    assert read_only == set(READ_ACTIONS)
    assert extension_plain <= core_plain


def test_panel_write_allowlists_match_the_core_contract() -> None:
    panel = ROOT / "src/aitest/resources/panel/src/wizard.ts"
    bridge = ROOT / "integrations/trae/src/core-bridge.ts"
    panel_plain = _ts_set(panel, "PLAIN_WRITE_ACTIONS")
    panel_controlled = _ts_set(panel, "CONTROLLED_ACTIONS")
    extension_plain = _ts_set(bridge, "PLAIN_WRITE_ACTIONS")
    core_plain = PHASE_ONE_ACTIONS - HUMAN_ACTIONS - READ_ACTIONS

    assert panel_controlled == set(HUMAN_ACTIONS)
    assert panel_plain == extension_plain
    assert panel_plain <= core_plain
    assert not (panel_plain & panel_controlled)
