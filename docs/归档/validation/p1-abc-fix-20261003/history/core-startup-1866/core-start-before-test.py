import ctypes, os, pytest; from types import SimpleNamespace

import aitest.bootstrap as bootstrap

from aitest.bootstrap import SystemProcessLauncher

from aitest.application.errors import WorkspaceInUse

from aitest.interfaces.local import pipe

def test_failed_creation_does_not_publish_discovery(tmp_path, monkeypatch):

    def fail(*args, **kwargs):
        raise OSError('controlled creation failure')
    monkeypatch.setattr(bootstrap.subprocess, 'Popen', fail)
    with pytest.raises((WorkspaceInUse, OSError)):
        SystemProcessLauncher(tmp_path).start('workspace')
    assert not (tmp_path / '.core-instance-id').exists()

def test_repeated_claim_preserves_first_child_and_discovery(tmp_path, monkeypatch):
    children = []

    class Alive:
        pid = os.getpid()

        def __init__(self, cmd, **kwargs):
            children.append(cmd)

        def poll(self):
            return None
    monkeypatch.setattr(bootstrap.subprocess, 'Popen', Alive)
    launcher = SystemProcessLauncher(tmp_path)
    first = launcher.start('workspace')
    second = SystemProcessLauncher(tmp_path).start('workspace')
    assert len(children) == 1
    assert first == second == (tmp_path / '.core-instance-id').read_text(encoding='utf-8')

def test_client_completes_partial_writes_without_duplicating_frame():
    captured = bytearray()

    def write(handle, buffer, size, output, overlap):
        count = min(size, 3)
        captured.extend(bytes(buffer[:count]))
        ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = count
        return True
    client = pipe.NamedPipeClient.__new__(pipe.NamedPipeClient)
    client._handle = 99
    client._kernel = SimpleNamespace(kernel32=SimpleNamespace(WriteFile=write))
    payload = b'{"action":"query"}'
    client.write_message(payload)
    assert captured == len(payload).to_bytes(4, 'big') + payload

@pytest.mark.parametrize('success,count', [(False, 2), (True, 0), (True, 999)])
def test_client_failed_writes_cannot_claim_complete_command(success, count):

    def write(handle, buffer, size, output, overlap):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = count
        return success
    client = pipe.NamedPipeClient.__new__(pipe.NamedPipeClient)
    client._handle = 99
    client._kernel = SimpleNamespace(kernel32=SimpleNamespace(WriteFile=write))
    with pytest.raises(pipe.PipeUnavailable):
        client.write_message(b'query')

def test_client_rejects_oversized_response_before_allocating_or_reading_body():
    calls = []
    client = pipe.NamedPipeClient.__new__(pipe.NamedPipeClient)
    client._handle = 99

    def read(size):
        calls.append(size)
        assert size == 4
        return (pipe.MAX_MESSAGE_BYTES + 1).to_bytes(4, 'big')
    client._read_exact = read
    with pytest.raises(pipe.PipeUnavailable, match='超过上限'):
        client.read_message()
    assert calls == [4]

@pytest.mark.parametrize('success,count', [(False, 2), (True, 0), (True, 999)])
def test_client_failed_reads_do_not_accept_partial_or_false_success(success, count):

    def read(handle, buffer, size, output, overlap):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_ulong)).contents.value = count
        return success
    client = pipe.NamedPipeClient.__new__(pipe.NamedPipeClient)
    client._handle = 99
    client._kernel = SimpleNamespace(kernel32=SimpleNamespace(ReadFile=read))
    with pytest.raises(pipe.PipeUnavailable):
        client._read_exact(4)
