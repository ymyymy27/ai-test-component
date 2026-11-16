"""Generate functional PreparedRun fixtures from their business scenario factory."""

import json
from pathlib import Path

from tests.support.prepared_run_factory import SCENARIOS, fixture_payload


def main() -> None:
    target = (
        Path(__file__).resolve().parents[1] / "tests/contracts/fixtures/prepared_run_functional"
    )
    target.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        (target / f"{scenario}.json").write_text(
            json.dumps(fixture_payload(scenario), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
