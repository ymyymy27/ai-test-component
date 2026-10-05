"""Every pipe termination calls the exact core-owned session finalizer once."""

import pytest

from aitest.contracts.commands import Command
from aitest.interfaces.local.actor_context import CoreActorContext
from aitest.interfaces.local.api import EntryKind, LocalAPI
from aitest.interfaces.local.core_worker import serve_connection, shutdown_frame
from tests.unit.test_a_a02_core_dispatch import _FakeServer


@pytest.mark.parametrize("kind", [EntryKind.HUMAN_UI, EntryKind.AGENT_RELAY])
@pytest.mark.parametrize("ending", ["read_failure", "write_failure", "shutdown"])
def test_pipe_allocates_context_and_finalizes_exact_origin_on_every_exit(kind, ending):
    actors, observed, closed = CoreActorContext(), [], []

    def handler(command):
        observed.append(actors.current())
        return {"status": "read"}

    api = LocalAPI(
        "instance",
        "workspace",
        handlers={"fixture_read": handler},
        actors=actors,
        session_finalizer=closed.append,
    )
    frames = [
        Command(action="fixture_read", request_id="read-request", project_id="project")
        .model_dump_json()
        .encode()
    ]
    if ending == "shutdown":
        frames.append(shutdown_frame())

    class Server(_FakeServer):
        def write_message(self, payload):
            if ending == "write_failure":
                raise OSError("peer disappeared during reply")
            super().write_message(payload)

    outcome = serve_connection(Server(frames), api, connection_no=1, entry_kind=kind)
    assert outcome == ("shutdown" if ending == "shutdown" else "disconnected")
    assert len(observed) == len(closed) == 1
    assert observed[0].session_id == closed[0].session_id
    assert observed[0].entry_kind is closed[0].entry_kind is kind
    assert observed[0].interactive is (kind is EntryKind.HUMAN_UI)
    assert observed[0].interaction is None  # Ordinary frames never manufacture a gesture.


def test_finalizer_failure_is_reported_instead_of_claiming_a_completed_close():
    def failed(session):
        raise OSError("revocation could not be published")

    api = LocalAPI("instance", "workspace", session_finalizer=failed)
    with pytest.raises(OSError, match="could not be published"):
        serve_connection(_FakeServer([]), api, connection_no=1)
