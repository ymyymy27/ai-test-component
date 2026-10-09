"""Bounded, content-based probing of explicitly registered Python carriers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Thread

from aitest.application.ports import EnvironmentResolutionRequest
from aitest.contracts.prepared_run import (
    EnvironmentIsolationModeFact,
    EnvironmentRefFact,
    EnvironmentResolutionFact,
)


def _digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class PythonEnvironmentCarrier:
    """Trusted bootstrap configuration; not constructible from public Commands."""

    project_id: str
    environment_id: str
    carrier_id: str
    executable: Path
    expected_executable_digest: str
    base_executable: Path
    expected_base_executable_digest: str
    dependency_roots: tuple[Path, ...]
    isolation_mode: str = "venv"
    venv_root: Path | None = None


_PROBE = """import sys,json,sysconfig
print(json.dumps(dict(executable=sys.executable,base_executable=sys._base_executable,
version=list(sys.version_info[:3]),implementation=sys.implementation.name,
prefix=sys.prefix,base_prefix=sys.base_prefix,
site_roots=[sysconfig.get_path('purelib'),sysconfig.get_path('platlib')],
isolated=sys.flags.isolated,no_site=sys.flags.no_site)))
"""


class RegisteredPythonEnvironmentResolver:
    def __init__(
        self,
        carriers: tuple[PythonEnvironmentCarrier, ...] = (),
        *,
        timeout_seconds: float = 15,
        max_files: int = 100_000,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        if timeout_seconds <= 0 or max_files < 1 or max_bytes < 1:
            raise ValueError("environment probe limits must be positive")
        self.timeout, self.max_files, self.max_bytes = timeout_seconds, max_files, max_bytes
        self.carriers = {(item.project_id, item.environment_id): item for item in carriers}
        if len(self.carriers) != len(carriers):
            raise ValueError("duplicate environment carrier registration")
        for item in carriers:
            if (
                not item.project_id
                or not item.environment_id
                or not item.carrier_id
                or not item.executable.is_absolute()
                or not item.base_executable.is_absolute()
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.expected_executable_digest)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.expected_base_executable_digest)
                or not item.dependency_roots
                or any(not root.is_absolute() for root in item.dependency_roots)
                or item.isolation_mode not in {"venv", "none", "unmanaged"}
                or (item.isolation_mode == "venv" and item.venv_root is None)
                or (item.venv_root is not None and not item.venv_root.is_absolute())
            ):
                raise ValueError("incomplete trusted environment carrier registration")

    @staticmethod
    def _no_links(path: Path) -> None:
        for part in (path, *path.parents):
            if part.is_symlink() or part.is_junction():
                raise ValueError("environment path contains an unsupported link")

    def _file_digest(self, path: Path) -> str:
        self._no_links(path)
        if not path.is_file() or path.stat().st_size > self.max_bytes:
            raise ValueError("environment file is missing or exceeds the byte limit")
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                if size > self.max_bytes:
                    raise ValueError("environment file exceeds the byte limit")
                digest.update(block)
        return "sha256:" + digest.hexdigest()

    def _dependencies(self, roots: tuple[Path, ...]) -> str:
        resolved = tuple(root.resolve() for root in roots)
        if len(set(resolved)) != len(resolved) or any(
            one != other and one.is_relative_to(other) for one in resolved for other in resolved
        ):
            raise ValueError("dependency roots overlap")
        facts: list[tuple[str, str, str]] = []
        total = 0
        for root in roots:
            self._no_links(root)
            if not root.is_dir():
                raise ValueError("registered dependency root is missing")

            # Walk without following links; refuse every link, including directory links.
            def refuse_walk_error(error: OSError) -> None:
                raise error

            for directory, directories, files in os.walk(
                root, followlinks=False, onerror=refuse_walk_error
            ):
                for name in (*directories, *files):
                    self._no_links(Path(directory) / name)
                for name in sorted(files):
                    path = Path(directory) / name
                    total += path.stat().st_size
                    if total > self.max_bytes or len(facts) >= self.max_files:
                        raise ValueError("environment dependency scan exceeds its limit")
                    if path.suffix.lower() == ".pth":
                        if path.stat().st_size > 16 * 1024:
                            raise ValueError("environment path configuration is too large")
                        if any(
                            line.strip() and not line.lstrip().startswith("#")
                            for line in path.read_text(encoding="utf-8").splitlines()
                        ):
                            raise ValueError("environment .pth needs an explicit loading mapping")
                    facts.append(
                        (
                            str(root.resolve()),
                            path.relative_to(root).as_posix(),
                            self._file_digest(path),
                        )
                    )
        return _digest(sorted(facts))

    def _configuration(self, carrier: PythonEnvironmentCarrier) -> str:
        if carrier.isolation_mode != "venv":
            if any(
                path.exists()
                for path in (
                    carrier.executable.parent / "pyvenv.cfg",
                    carrier.executable.parent.parent / "pyvenv.cfg",
                )
            ):
                raise ValueError("non-venv carrier has an active venv configuration")
            return _digest(
                {
                    "mode": carrier.isolation_mode,
                    "roots": [str(p.resolve()) for p in carrier.dependency_roots],
                }
            )
        assert carrier.venv_root is not None
        config = carrier.venv_root / "pyvenv.cfg"
        self._no_links(config)
        if not config.is_file() or config.stat().st_size > 16 * 1024:
            raise ValueError("registered venv configuration is missing or too large")
        fields: dict[str, str] = {}
        for line in config.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition("=")
            key = key.strip().lower()
            if not separator or key in fields:
                raise ValueError("venv configuration is ambiguous")
            fields[key] = value.strip()
        if fields.get("include-system-site-packages", "").lower() != "false":
            raise ValueError("venv includes unregistered system dependencies")
        home = fields.get("home")
        if (
            not home
            or not Path(home).is_absolute()
            or Path(home).resolve() != carrier.base_executable.resolve().parent
            or (
                os.name == "nt"
                and carrier.executable.name.casefold() != carrier.base_executable.name.casefold()
            )
        ):
            raise ValueError("venv home does not resolve to the registered base interpreter")
        root = carrier.venv_root.resolve()
        if not carrier.executable.resolve().is_relative_to(root) or any(
            not p.resolve().is_relative_to(root) for p in carrier.dependency_roots
        ):
            raise ValueError("registered venv paths escape the carrier")
        actual_sites = (
            {root / "Lib/site-packages"}
            if os.name == "nt"
            else {p.resolve() for p in root.glob("lib/python3.*/site-packages")}
        )
        if {p.resolve() for p in carrier.dependency_roots} != actual_sites:
            raise ValueError("registered roots do not match the venv dependency directory")
        return self._file_digest(config)

    def _probe(self, executable: Path) -> dict[str, object]:
        # Neither user credentials/PYTHONPATH nor project cwd enter this process.
        environment = {
            key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ
        }
        with (
            tempfile.TemporaryDirectory(
                prefix="aitest-environment-probe-", ignore_cleanup_errors=True
            ) as directory,
            subprocess.Popen(
                [str(executable), "-I", "-S", "-c", _PROBE],
                cwd=directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ) as process,
        ):
            output = bytearray()
            exceeded = [False]
            assert process.stdout is not None

            def read() -> None:
                assert process.stdout is not None
                while block := process.stdout.read(1024):
                    if len(output) + len(block) > 8192:
                        exceeded[0] = True
                        process.kill()
                        return
                    output.extend(block)

            thread = Thread(target=read, daemon=True)
            thread.start()
            try:
                process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                thread.join(timeout=2)
                raise ValueError("registered interpreter probe timed out") from error
            thread.join(timeout=2)
            if thread.is_alive() or exceeded[0] or process.returncode:
                raise ValueError("registered interpreter probe failed or exceeded output limit")
        try:
            facts = json.loads(output)
        except (UnicodeError, ValueError) as error:
            raise ValueError("registered interpreter returned invalid safe facts") from error
        if not isinstance(facts, dict):
            raise ValueError("registered interpreter returned invalid safe facts")
        return facts

    def resolve(self, request: EnvironmentResolutionRequest) -> EnvironmentRefFact:
        carrier = self.carriers.get((request.project_id, request.environment_id))
        if carrier is None or carrier.isolation_mode != request.isolation_mode:
            raise ValueError("tested environment carrier is not registered for this project/mode")
        # 允许的解释器范围由**登记的环境声明**给出（不再在代码里钉死 3.13）；
        # 观测到的解释器必须与声明的主.次（及可选补丁）版本一致。
        version_requirement = re.fullmatch(
            r"(?:Python\s+)?(\d+)\.(\d+)(?:\.(\d+))?",
            request.interpreter_requirement.strip(),
        )
        if version_requirement is None:
            raise ValueError("interpreter requirement needs a supported explicit Python version")
        executable = carrier.executable
        before_executable = self._file_digest(executable)
        before_base = self._file_digest(carrier.base_executable)
        if before_executable != carrier.expected_executable_digest:
            raise ValueError("registered interpreter content changed")
        if before_base != carrier.expected_base_executable_digest:
            raise ValueError("registered base interpreter content changed")
        configuration = self._configuration(carrier)
        dependencies = self._dependencies(carrier.dependency_roots)
        # Windows venv launchers spawn another process. Probe the explicitly
        # registered base directly: -S disables venv site setup anyway. The
        # carrier launcher/config are filesystem observations, not proof that
        # business loading ran through that launcher.
        probe = self._probe(carrier.base_executable)
        version = probe.get("version")
        if (
            not isinstance(version, list)
            or len(version) != 3
            or any(type(value) is not int or value < 0 for value in version)
            or version[0] != int(version_requirement[1])
            or version[1] != int(version_requirement[2])
            or (version_requirement[3] is not None and version[2] != int(version_requirement[3]))
            or probe.get("implementation") != "cpython"
            or probe.get("isolated") != 1
            or probe.get("no_site") != 1
            or not all(
                isinstance(probe.get(key), str) and probe[key]
                for key in ("executable", "base_executable", "prefix", "base_prefix")
            )
            or Path(str(probe.get("executable"))).resolve() != carrier.base_executable.resolve()
        ):
            raise ValueError("actual interpreter does not match the registered requirement")
        # Python may report an installer alias (e.g. uv's version junction).
        # Canonicalize and match the registered target BEFORE reading any bytes.
        base = Path(str(probe["base_executable"])).resolve()
        if base != carrier.base_executable.resolve():
            raise ValueError("actual base interpreter differs from the registered carrier")
        base_digest = self._file_digest(base)
        if carrier.isolation_mode != "venv":
            roots = probe.get("site_roots")
            if (
                not isinstance(roots, list)
                or not roots
                or not all(isinstance(root, str) and root for root in roots)
                or {Path(root).resolve() for root in roots}
                != {root.resolve() for root in carrier.dependency_roots}
            ):
                raise ValueError(
                    "registered roots do not match the actual interpreter dependencies"
                )
        if (
            base.resolve() != carrier.base_executable.resolve()
            or base_digest != before_base
            or self._file_digest(executable) != before_executable
            or self._configuration(carrier) != configuration
            or self._dependencies(carrier.dependency_roots) != dependencies
            or self._file_digest(base) != base_digest
        ):
            raise ValueError("environment content changed while resolving")
        detail = EnvironmentResolutionFact(
            carrier_id=carrier.carrier_id,
            executable_path=str(executable.resolve()),
            executable_digest=before_executable,
            interpreter_version=".".join(map(str, version)),
            base_executable_path=str(base.resolve()),
            base_executable_digest=base_digest,
            probe_prefix=str(probe["prefix"]),
            base_prefix=str(probe["base_prefix"]),
            configuration_digest=configuration,
            dependency_roots=tuple(str(root.resolve()) for root in carrier.dependency_roots),
            dependency_set_digest=dependencies,
        )
        return EnvironmentRefFact(
            environment_id=request.environment_id,
            revision=1,
            isolation_mode=EnvironmentIsolationModeFact(request.isolation_mode),
            interpreter_identity=detail.interpreter_identity,
            dependency_set_digest=dependencies,
            resolution=detail,
        )
