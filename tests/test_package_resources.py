from ai_test.domain.tasks import AcceptanceItem
from ai_test.interfaces.panel import asset_text
from ai_test.interfaces.python_api import AITestAPI


def test_python_api_and_panel_resource(tmp_path) -> None:
    api = AITestAPI(tmp_path / "workspace")
    created_project = api.create_project("p1", "Project")
    assert api.get_project("p1") == created_project

    created_task = api.create_task(
        task_id="t1",
        project_id="p1",
        goal="Create a record",
        scope="Record creation",
        acceptance_items=(AcceptanceItem("AC1", "The record can be queried"),),
    )
    assert api.get_task("t1") == created_task
    assert api.list_tasks("p1") == (created_task,)

    created_delivery = api.create_delivery(
        delivery_id="d1",
        task_id="t1",
        version="v1.0.0",
        run_method="uv run pytest",
        completed=("Create record",),
        mocks=("payment gateway",),
        submitted_by="developer-1",
    )
    assert api.get_delivery("d1") == created_delivery
    assert api.list_deliveries("t1") == (created_delivery,)

    javascript = asset_text("index.js")
    assert "ai-test-panel" in javascript