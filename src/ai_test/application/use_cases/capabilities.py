from typing import Any


def describe_capabilities() -> dict[str, Any]:
    return {
        "component": "ai-test-component",
        "version": "0.1.0",
        "ready": ["workspace", "project-context", "python-api", "cli", "asgi-health"],
        "planned": [
            "delivery-intake",
            "test-planning",
            "execution",
            "evidence-review",
            "defect-management",
            "reporting",
            "ai-assistance",
            "mcp-tools",
        ],
    }

