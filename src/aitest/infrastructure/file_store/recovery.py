"""Conservative recovery; never replays unknown external effects."""
from pathlib import Path
import json
from .integrity import check_workspace
def recover_workspace(root: Path) -> dict[str, object]:
    report = check_workspace(root)
    pending = root / "transactions" / "active.json"
    report["recovery_required"] = pending.exists()
    if pending.exists(): report["active_request_id"] = json.loads(pending.read_text(encoding="utf-8")).get("request_id")
    return report
