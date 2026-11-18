import json
import sys

print(
    json.dumps(
        {
            "node_id": "db:1",
            "parent_id": "log:2",
            "status": "passed",
            "message": f"order row {sys.argv[1]}",
        }
    )
)
