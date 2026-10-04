"""Contended writers must retry instead of waiting or corrupting one shared UOW."""

from contextlib import contextmanager
from threading import Event, Thread

from aitest.application.errors import WorkspaceInUse
from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.file_store import locking


def test_contended_short_writer_returns_within_queue_wait_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(locking, "_TRANSACTION_WAIT_SECONDS", 0.05, raising=False)
    lock = locking.LifetimeWriterLock(tmp_path / "writer.lock")
    lock.acquire()
    held, release, finished = Event(), Event(), Event()
    errors = []

    def owner():
        with locking.writer_lock(tmp_path / "writer.lock"):
            held.set()
            release.wait(5)

    def waiting():
        try:
            with locking.writer_lock(tmp_path / "writer.lock"):
                pass
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    first, second = Thread(target=owner), Thread(target=waiting)
    first.start()
    assert held.wait(1)
    second.start()
    try:
        assert finished.wait(0.5), "writer exceeded its bounded wait"
        assert len(errors) == 1 and isinstance(errors[0], WorkspaceInUse)
    finally:
        release.set()
        first.join(2)
        second.join(2)
        lock.release()


def test_shared_unit_cannot_replace_another_begin_admission_context(tmp_path, monkeypatch):
    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    unit = core.unit_of_work
    first_entered, release = Event(), Event()
    first_errors, second_errors = [], []

    @contextmanager
    def suspended_acquire(**kwargs):
        if not first_entered.is_set():
            first_entered.set()
            release.wait(5)
        yield unit.workspace

    monkeypatch.setattr(unit.workspace, "acquire", suspended_acquire)

    def first_begin():
        try:
            unit.begin("first-request", "first-project", intent_id="first-intent")
            unit.rollback("first-request")
        except Exception as error:
            first_errors.append(error)

    def second_begin():
        try:
            unit.begin("second-request", "second-project", intent_id="second-intent")
            unit.rollback("second-request")
        except Exception as error:
            second_errors.append(error)

    first, second = Thread(target=first_begin), Thread(target=second_begin)
    first.start()
    assert first_entered.wait(1)
    second.start()
    second.join(1)
    try:
        assert len(second_errors) == 1 and isinstance(second_errors[0], WorkspaceInUse)
    finally:
        release.set()
        first.join(2)
        second.join(2)
        core.lifetime_lock.release()
    assert not first_errors
    assert unit.project is None
