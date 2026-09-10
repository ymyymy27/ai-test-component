from importlib import import_module
from pathlib import Path
from typing import Any

from ai_test.application.use_cases.capabilities import describe_capabilities
from ai_test.composition import create_component


def create_mcp_server(workspace: str | Path) -> Any:
    """Build the optional stdio MCP server without imposing MCP on library users."""
    try:
        fastmcp = import_module("mcp.server.fastmcp")
    except ImportError as error:
        raise RuntimeError("install the 'mcp' extra to enable MCP") from error

    create_component(workspace)
    server: Any = fastmcp.FastMCP("ai-test-component")

    def capabilities() -> dict[str, Any]:
        """Return the component capabilities available in this build."""
        return describe_capabilities()

    server.tool()(capabilities)
    return server
