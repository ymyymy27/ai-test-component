"""Checked file publication and retained, hashed pointer recovery material.

The Windows backend uses FlushFileBuffers and ReplaceFileW with a backup.
Successful API calls are evidence of those calls, not of physical power-loss
durability on a particular device. Uncertain replacement is inspected, never
replayed and never repaired by deleting the current pointer.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from aitest.infrastructure.security import guard_bytes

from . import atomic

MAX_POINTER_BYTES = 16384
MAX_DESCRIPTOR_BYTES = 4096


class PublicationUncertainError(OSError):
    code = "PUBLICATION_UNVERIFIED"


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _reject_links(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise PublicationUncertainError("publication path contains a filesystem link")


def _safe(raw: bytes) -> None:
    safe, changed = guard_bytes(raw)
    if changed or safe != raw:
        raise PublicationUncertainError("publication identity cannot be safely preserved")


def _read(path: Path, limit: int = MAX_POINTER_BYTES) -> bytes:
    _reject_links(path)
    if path.stat().st_size > limit:
        raise PublicationUncertainError("publication material exceeds its read budget")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise PublicationUncertainError("publication material exceeds its read budget")
    return raw


def _decode(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PublicationUncertainError("duplicate publication field")
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except RecursionError as error:
        raise PublicationUncertainError("publication JSON exceeds parsing depth") from error
    if not isinstance(value, dict):
        raise PublicationUncertainError("publication material must be an object")
    return value


class WindowsFileAPI:
    """Pointer-sized Win32 declarations; no ignored ACL/merge errors or retries."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows publication backend is unavailable")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "FlushFileBuffers": ([wintypes.HANDLE], wintypes.BOOL),
            "ReplaceFileW": (
                [
                    wintypes.LPCWSTR,
                    wintypes.LPCWSTR,
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "MoveFileExW": ([wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL),
            "GetVolumePathNameW": (
                [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "GetVolumeNameForVolumeMountPointW": (
                [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "GetDriveTypeW": ([wintypes.LPCWSTR], wintypes.UINT),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result

    @staticmethod
    def _check(result: int) -> None:
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def flush(self, descriptor: int) -> None:
        import msvcrt

        self._check(self.kernel.FlushFileBuffers(msvcrt.get_osfhandle(descriptor)))

    def volume(self, directory: Path) -> str:
        mount, volume = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(128)
        self._check(self.kernel.GetVolumePathNameW(str(directory), mount, len(mount)))
        if self.kernel.GetDriveTypeW(mount.value) != 3:  # DRIVE_FIXED
            raise OSError("publication requires a local fixed volume")
        self._check(self.kernel.GetVolumeNameForVolumeMountPointW(mount.value, volume, len(volume)))
        return volume.value.casefold()

    def replace(self, current: Path, candidate: Path, backup: Path) -> None:
        # REPLACEFILE_WRITE_THROUGH is unsupported. Neither merge/ACL error is
        # ignored; the original pointer is retained at the specified backup.
        self._check(
            self.kernel.ReplaceFileW(str(current), str(candidate), str(backup), 0, None, None)
        )

    def initialize(self, candidate: Path, current: Path) -> None:
        # No REPLACE_EXISTING: a racing/current unexpected file cannot be erased.
        self._check(self.kernel.MoveFileExW(str(candidate), str(current), 8))


class FilePublicationBackend:
    def __init__(self, root: Path) -> None:
        _reject_links(root)
        self.root = root.resolve()
        self.current = self.root / "current.json"
        self.directory = self.root / "transactions/publications"
        self.marker = self.root / "transactions/publication.json"
        self.windows = WindowsFileAPI() if os.name == "nt" else None

    def _flush(self, descriptor: int) -> None:
        if self.windows is not None:
            self.windows.flush(descriptor)
        else:
            os.fsync(descriptor)

    def _flush_directory(self, directory: Path) -> None:
        if self.windows is None:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def write_and_flush(self, path: Path, raw: bytes) -> None:
        _safe(raw)
        _reject_links(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            if handle.write(raw) != len(raw):
                raise OSError("short publication material write")
            handle.flush()
            self._flush(handle.fileno())

    def publish_immutable(self, path: Path, raw: bytes) -> None:
        """Exclusive publication; a saved object is never overwritten."""
        _safe(raw)
        _reject_links(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".prepare-{uuid4().hex}"
        self.write_and_flush(temporary, raw)
        try:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                if _read(path, max(len(raw), 1)) != raw:
                    raise PublicationUncertainError(
                        "existing immutable publication was changed"
                    ) from error
            self._flush_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def inspect_publication(self) -> Literal["none", "old", "new", "unknown"]:
        """Bounded inspection of the latest sealed attempt, without promoting it."""
        _reject_links(self.marker)
        if not self.marker.exists():
            return "none"
        try:
            marker = _decode(_read(self.marker, MAX_DESCRIPTOR_BYTES))
            if set(marker) != {"schema", "descriptor_digest"} or (
                marker["schema"] != "aitest.publication-marker/1"
            ):
                raise ValueError
            digest = marker["descriptor_digest"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError
            raw = _read(self.directory / f"{digest}.json", MAX_DESCRIPTOR_BYTES)
            if _digest(raw) != digest:
                raise ValueError
            descriptor = _decode(raw)
            if set(descriptor) != {"schema", "candidate_digest", "previous_digest", "attempt"}:
                raise ValueError
            if descriptor["schema"] != "aitest.pointer-publication/1":
                raise ValueError
            attempt = descriptor["attempt"]
            if (
                not isinstance(attempt, str)
                or len(attempt) != 32
                or any(c not in "0123456789abcdef" for c in attempt)
            ):
                raise ValueError
            materials: dict[str, bytes | None] = {}
            for name in ("candidate", "previous"):
                identity = descriptor[f"{name}_digest"]
                if identity is None and name == "previous":
                    materials[name] = None
                    continue
                if (
                    not isinstance(identity, str)
                    or len(identity) != 64
                    or any(c not in "0123456789abcdef" for c in identity)
                ):
                    raise ValueError
                saved = _read(self.directory / f"{identity}.pointer")
                if _digest(saved) != identity:
                    raise ValueError
                materials[name] = saved
            _reject_links(self.current)
            if not self.current.exists():
                return "unknown"  # Do not silently resurrect a legacy/empty root.
            current = _read(self.current)
            backup = self.directory / attempt / "backup.json"
            _reject_links(backup)
            if backup.exists() and _read(backup) != materials["previous"]:
                return "unknown"
            if current == materials["candidate"]:
                if materials["previous"] is not None and not backup.exists():
                    return "unknown"
                return "new"
            if current == materials["previous"]:
                return "old"
            return "unknown"
        except (OSError, ValueError, TypeError, KeyError):
            return "unknown"

    def _replace(self, candidate: Path, backup: Path, *, initial: bool) -> None:
        if self.windows is not None:
            volumes = {self.windows.volume(p.parent) for p in (self.current, candidate, backup)}
            if len(volumes) != 1:
                raise OSError("publication materials are on different volumes")
            if initial:
                self.windows.initialize(candidate, self.current)
            else:
                self.windows.replace(self.current, candidate, backup)
        elif initial:
            os.link(candidate, self.current)
            candidate.unlink()
        else:
            os.link(self.current, backup)
            os.replace(candidate, self.current)
        self._flush_directory(self.current.parent)

    def replace_current(self, raw: bytes, *, previous: bytes | None) -> None:
        """Retain candidate/previous facts, replace once, inspect and flush again.

        An error with a verifiable new root is a lost response and may succeed.
        An old root is a definite uncommitted attempt. Any other state blocks
        subsequent publication, including missing current with a valid backup.
        """
        if (
            len(raw) > MAX_POINTER_BYTES
            or previous is not None
            and (len(previous) > MAX_POINTER_BYTES)
        ):
            raise PublicationUncertainError("pointer exceeds its publication budget")
        _safe(raw)
        if previous is not None:
            _safe(previous)
        state = self.inspect_publication()
        if state == "unknown":
            raise PublicationUncertainError("prior pointer publication cannot be verified")
        _reject_links(self.current)
        actual = _read(self.current) if self.current.exists() else None
        if actual != previous:
            raise PublicationUncertainError("current pointer changed before publication")
        if actual is not None:
            self.confirm_current()
        if raw == previous:
            return
        attempt = uuid4().hex
        candidate_digest = _digest(raw)
        previous_digest = _digest(previous) if previous is not None else None
        self.publish_immutable(self.directory / f"{candidate_digest}.pointer", raw)
        if previous is not None:
            self.publish_immutable(self.directory / f"{previous_digest}.pointer", previous)
        descriptor = _canonical(
            dict(
                schema="aitest.pointer-publication/1",
                attempt=attempt,
                candidate_digest=candidate_digest,
                previous_digest=previous_digest,
            )
        )
        digest = _digest(descriptor)
        self.publish_immutable(self.directory / f"{digest}.json", descriptor)
        folder = self.directory / attempt
        _reject_links(folder)
        folder.mkdir()
        candidate, backup = folder / "replacement.json", folder / "backup.json"
        self.write_and_flush(candidate, raw)
        # This non-business locator is flushed before the only business switch.
        _reject_links(self.marker)
        _safe(_canonical(dict(schema="aitest.publication-marker/1", descriptor_digest=digest)))
        atomic.write_json(
            self.marker, dict(schema="aitest.publication-marker/1", descriptor_digest=digest)
        )
        failure: OSError | None = None
        try:
            self._replace(candidate, backup, initial=previous is None)
        except OSError as error:
            failure = error
        state = self.inspect_publication()
        if state == "new":
            # Open with write access: FlushFileBuffers requires GENERIC_WRITE.
            # A post-switch flush failure leaves the new fact readable but
            # unacknowledged. Every later write must repeat this checked flush.
            self.confirm_current()
            return
        if state == "old":
            if failure is not None:
                raise failure
            raise OSError("pointer replacement did not publish the candidate")
        raise PublicationUncertainError(
            "pointer replacement result cannot be verified"
        ) from failure

    def confirm_current(self) -> None:
        _reject_links(self.current)
        if self.inspect_publication() == "unknown":
            raise PublicationUncertainError("current publication is uncertain")
        with self.current.open("r+b") as handle:
            self._flush(handle.fileno())
