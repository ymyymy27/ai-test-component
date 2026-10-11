"""Each workspace retains its own transaction owner across independent close order."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from aitest.application.errors import WorkspaceInUse
from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.file_store.locking import LifetimeWriterLock, writer_lock


@pytest.mark.parametrize("with_lifetime", [False, True])
@pytest.mark.parametrize("first_closed", [0, 1])
def test_closing_one_workspace_preserves_the_other_owner(
    tmp_path: Path, with_lifetime: bool, first_closed: int
) -> None:
    paths = (tmp_path / "a" / "writer.lock", tmp_path / "b" / "writer.lock")
    lifetimes = tuple(LifetimeWriterLock(path) for path in paths) if with_lifetime else ()
    for lifetime in lifetimes:
        lifetime.acquire()

    def exercise() -> None:
        contexts = tuple(writer_lock(path) for path in paths)
        active = []
        try:
            for index, context in enumerate(contexts):
                context.__enter__()
                active.append(index)
            contexts[first_closed].__exit__(None, None, None)
            active.remove(first_closed)
            remaining = 1 - first_closed
            # Publication re-entry must still recognize this exact workspace owner.
            with writer_lock(paths[remaining], reentrant=True):
                pass
            with (
                pytest.raises(WorkspaceInUse, match="already has a writer in this thread"),
                writer_lock(paths[remaining]),
            ):
                pytest.fail("ordinary duplicate acquisition was accepted")
            if with_lifetime:
                with pytest.raises(WorkspaceInUse, match="active transaction"):
                    lifetimes[remaining].release()
            # Closing the other workspace neither keeps nor invents ownership here.
            with writer_lock(paths[first_closed]):
                pass
        finally:
            for index in reversed(active):
                contexts[index].__exit__(None, None, None)
        for path in paths:
            with writer_lock(path):
                pass

    try:
        # Isolate thread-local failed-baseline cleanup from other pytest fixtures.
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(exercise).result()
    finally:
        for lifetime in reversed(lifetimes):
            lifetime.release()


def test_two_real_units_can_finish_in_independent_workspace_order(tmp_path: Path) -> None:
    cores = tuple(
        assemble_workspace_core(tmp_path / name, instance_id=name) for name in ("a", "b")
    )

    def exercise() -> None:
        first, second = (core.unit_of_work for core in cores)
        try:
            first.begin("first-request", "project-a", intent_id="first-intent")
            second.begin("second-request", "project-b", intent_id="second-intent")
            first.rollback()
            second.stage_record(
                aggregate_kind="project",
                record_id="project-b",
                expected_revision=0,
                payload={"project_id": "project-b", "value": "saved"},
            )
            second.commit("second-request")
            saved = second.repo.read(aggregate_kind="project", record_id="project-b", revision=1)
            assert saved.payload["value"] == "saved"
            assert first.project is None and second.project is None
        finally:
            first.rollback()
            second.rollback()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(exercise).result()
    finally:
        for core in reversed(cores):
            core.lifetime_lock.release()


@pytest.mark.parametrize("with_lifetime", [False, True])
def test_foreign_thread_finalization_clears_the_actual_owner(
    tmp_path: Path, with_lifetime: bool
) -> None:
    path = tmp_path / "writer.lock"
    lifetime = LifetimeWriterLock(path) if with_lifetime else None
    if lifetime is not None:
        lifetime.acquire()

    with ThreadPoolExecutor(max_workers=2) as executor:

        def acquire_and_collect() -> None:
            holder = [writer_lock(path)]
            holder[0].__enter__()
            # Last-reference destruction runs the generator cleanup on the other thread.
            executor.submit(holder.clear).result(timeout=10)
            with writer_lock(path):
                pass
            if lifetime is not None:
                lifetime.release()
                lifetime.acquire()
                with writer_lock(path):
                    pass

        try:
            executor.submit(acquire_and_collect).result(timeout=20)
        finally:
            if lifetime is not None:
                lifetime.release()


def test_foreign_cleanup_does_not_authorize_foreign_business_mutation(tmp_path: Path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="thread-guard-core")
    unit = core.unit_of_work
    request = "owner-request"
    unit.begin(request, "project", intent_id="owner-intent")
    unit.stage_record(
        aggregate_kind="project",
        record_id="project",
        expected_revision=0,
        payload={"project_id": "project", "value": "owner"},
    )
    sequence = unit.current_commit_sequence()
    cleanup_path = tmp_path / "cleanup-workspace" / "writer.lock"
    cleanup_holder = [writer_lock(cleanup_path)]
    cleanup_holder[0].__enter__()

    def unauthorized() -> None:
        cleanup_holder.clear()
        for action in (
            lambda: unit.commit(request),
            lambda: unit.rollback(request),
            lambda: unit.stage_record(
                aggregate_kind="project",
                record_id="foreign",
                expected_revision=0,
                payload={"project_id": "project", "value": "foreign"},
            ),
        ):
            with pytest.raises(WorkspaceInUse, match="owning core thread"):
                action()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(unauthorized).result(timeout=10)
        assert unit.current_commit_sequence() == sequence
        assert len(unit.pending) == 1
        with writer_lock(cleanup_path):
            pass
        unit.commit(request)
        assert unit.repo.read(aggregate_kind="project", record_id="project", revision=1).payload[
            "value"
        ] == "owner"
    finally:
        cleanup_holder.clear()
        unit.rollback()
        core.lifetime_lock.release()
