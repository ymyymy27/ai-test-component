"""Replay the recorded finite-selector vectors; run from the repository root.

uv run --no-project --with jsonschema --with pydantic==2.13.5 --python 3.13 python docs/validation/p1-completion-repair-20261014/validate_read_view_schema.py
"""
import json
import sys
from pathlib import Path

from jsonschema.validators import validator_for

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "src"))
from aitest.contracts.read_views import ReadViewQuery

schema = json.loads((root / "src/aitest/contracts/schemas/ReadViewQuery.json").read_text(encoding="utf-8"))
engine = validator_for(schema)
engine.check_schema(schema)
validator = engine(schema)
vectors = json.loads(Path(__file__).with_name("schema-validator-vectors.json").read_text(encoding="utf-8"))
rows = []
for vector in vectors:
    try:
        ReadViewQuery.model_validate(vector["input"])
        model_accepts = True
    except ValueError:
        model_accepts = False
    schema_accepts = validator.is_valid(vector["input"])
    rows.append({**vector, "model_accepts": model_accepts, "schema_accepts": schema_accepts})
print(json.dumps(rows, ensure_ascii=False, indent=2))
if any(row["model_accepts"] != row["expected_accepts"] or row["schema_accepts"] != row["expected_accepts"] for row in rows):
    raise SystemExit(1)
