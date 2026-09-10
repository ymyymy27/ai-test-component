from ai_test.interfaces.panel import asset_text
from ai_test.interfaces.python_api import AITestAPI


def test_python_api_and_panel_resource(tmp_path) -> None:
    api = AITestAPI(tmp_path / "workspace")
    created = api.create_project("p1", "Project")
    assert api.get_project("p1") == created
    javascript = asset_text("index.js")
    assert "ai-test-panel" in javascript
