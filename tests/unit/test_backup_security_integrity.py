"""An exact backup must not repersist unsafe legacy bytes or certify corrupt closure."""

import hashlib
import json
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.backup import BackupError, FileBackupStore
from aitest.infrastructure.security import KnownSecretRegistry


def _legacy(root, content, name="legacy.txt"):
    workspace = root / "workspace"
    (workspace / "exports").mkdir(parents=True)
    source = workspace / "exports" / name
    source.write_bytes(content)
    return workspace, source


@pytest.mark.parametrize("secret", ["fictional-backup-key", "虚构凭据"])
@pytest.mark.parametrize("offset", [0, 65535])
@pytest.mark.parametrize("binary", [False, True])
def test_unsafe_legacy_backup_is_rejected_before_any_destination_write(
    tmp_path, secret, offset, binary
):
    content = b"line\n" * (offset // 5) + b" " * (offset % 5)
    content += secret.encode() + (b"\xff\x00" if binary else b"\n")
    workspace, source = _legacy(tmp_path, content)
    registry = KnownSecretRegistry()
    registry.register(secret)
    destination = tmp_path / "backup"
    with pytest.raises(BackupError, match="敏感材料"):
        FileBackupStore(workspace, registry=registry).create(destination)
    assert not destination.exists()
    assert source.read_bytes() == content


def test_newly_registered_credential_blocks_legacy_backup_restore_without_rewriting_it(tmp_path):
    secret = "fictional-backup-restart-key"
    workspace, _source = _legacy(tmp_path, ("old " + secret + "\n").encode())
    registry = KnownSecretRegistry()
    backup = FileBackupStore(workspace, registry=registry).create(tmp_path / "backup")
    original = (backup / "exports/legacy.txt").read_bytes()
    registry.register(secret)
    restarted = FileBackupStore(workspace, registry=registry)
    target = tmp_path / "restored"
    with pytest.raises(BackupError, match="敏感材料"):
        restarted.restore(backup=backup, target=target)
    assert not target.exists()
    assert (backup / "exports/legacy.txt").read_bytes() == original


def test_corrupted_backup_is_rejected_before_creating_the_restore_target(tmp_path):
    workspace, _source = _legacy(tmp_path, b"safe\n")
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "backup")
    (backup / "exports/legacy.txt").write_bytes(b"corrupt\n")
    target = tmp_path / "restored"
    with pytest.raises(BackupError, match="摘要"):
        store.restore(backup=backup, target=target)
    assert not target.exists()


def test_unmanifested_backup_file_is_rejected_before_creating_restore_target(tmp_path):
    workspace, _source = _legacy(tmp_path, b"safe\n")
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "backup")
    (backup / "extra.txt").write_bytes(b"safe but not frozen\n")
    target = tmp_path / "restored"
    with pytest.raises(BackupError, match="闭包"):
        store.restore(backup=backup, target=target)
    assert not target.exists()


@pytest.mark.parametrize("operation", ["create", "restore"])
def test_change_after_preflight_never_writes_credential_bytes(tmp_path, monkeypatch, operation):
    workspace, source = _legacy(tmp_path, b"safe\n")
    registry = KnownSecretRegistry()
    secret = "fictional-backup-race-key"
    registry.register(secret)
    store = FileBackupStore(workspace, registry=registry)
    backup = store.create(tmp_path / "original-backup")
    original = store._copy_safe_file
    writes = []
    path_open = Path.open

    class WriteSpy:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, value):
            writes.append(value)
            return self.handle.write(value)

    def spy(path, mode="r", *args, **kwargs):
        handle = path_open(path, mode, *args, **kwargs)
        return WriteSpy(handle) if mode == "xb" else handle

    def replace_before_copy(old_source, destination, digest):
        old_source.write_bytes(secret.encode() + b"\n")
        original(old_source, destination, digest)

    monkeypatch.setattr(store, "_copy_safe_file", replace_before_copy)
    monkeypatch.setattr(Path, "open", spy)
    with pytest.raises(BackupError, match="敏感材料"):
        if operation == "create":
            store.create(tmp_path / "new-backup")
        else:
            store.restore(backup=backup, target=tmp_path / "restored")
    assert secret.encode() not in b"".join(writes)
    assert not (tmp_path / "new-backup/backup.json").exists()
    assert source.exists()


def test_changed_safe_bytes_cannot_publish_a_manifest_for_the_previous_digest(
    tmp_path, monkeypatch
):
    workspace, _source = _legacy(tmp_path, b"safe\n")
    store = FileBackupStore(workspace)
    original = store._copy_safe_file

    def replace_before_copy(source, destination, digest):
        source.write_bytes(b"another safe value\n")
        original(source, destination, digest)

    monkeypatch.setattr(store, "_copy_safe_file", replace_before_copy)
    backup = tmp_path / "backup"
    with pytest.raises(BackupError, match="变化"):
        store.create(backup)
    assert not (backup / "backup.json").exists()


@pytest.mark.parametrize("field", ["token", "Authorization", "escaped_registered_value"])
def test_structured_legacy_credentials_cannot_bypass_backup_scan(tmp_path, field):
    secret = "虚构凭据"
    value = {field: "Bearer fictional" if field == "Authorization" else secret}
    content = json.dumps(value, ensure_ascii=True).encode()
    workspace, source = _legacy(tmp_path, content, "legacy.json")
    registry = KnownSecretRegistry()
    registry.register(secret)
    target = tmp_path / "backup"
    with pytest.raises(BackupError, match="敏感材料"):
        FileBackupStore(workspace, registry=registry).create(target)
    assert not target.exists()
    assert source.read_bytes() == content


def test_safe_backup_and_restore_preserve_exact_non_ascii_bytes_and_digests(tmp_path):
    content = "已过滤的历史材料\n".encode()
    workspace, _source = _legacy(tmp_path, content)
    registry = KnownSecretRegistry()
    registry.register("unrelated-fictional-key")
    store = FileBackupStore(workspace, registry=registry)
    backup = store.create(tmp_path / "backup")
    manifest = json.loads((backup / "backup.json").read_text())
    assert manifest["files"]["exports/legacy.txt"] == hashlib.sha256(content).hexdigest()
    target = tmp_path / "restored"
    result = store.restore(backup=backup, target=target)
    assert result.verified is True
    assert (target / "exports/legacy.txt").read_bytes() == content


def test_sensitive_backup_filename_is_rejected_before_manifest_or_data_write(tmp_path):
    secret = "fictional-name-credential"
    workspace, _source = _legacy(tmp_path, b"safe\n", secret + ".txt")
    registry = KnownSecretRegistry()
    registry.register(secret)
    target = tmp_path / "backup"
    with pytest.raises(BackupError, match="敏感材料"):
        FileBackupStore(workspace, registry=registry).create(target)
    assert not target.exists()


def test_unresolved_legacy_material_is_bounded_before_backup_destination_creation(tmp_path):
    workspace, _source = _legacy(tmp_path, b"z" * (2 * 1024 * 1024))
    target = tmp_path / "backup"
    with pytest.raises(BackupError, match="安全内存范围"):
        FileBackupStore(workspace).create(target)
    assert not target.exists()
