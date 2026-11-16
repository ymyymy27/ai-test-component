"""Explicit synthetic core port; not real environment or Trae acceptance."""

from aitest.contracts.prepared_run import (
    EnvironmentIsolationModeFact,
    EnvironmentRefFact,
    EnvironmentResolutionFact,
)


class FixtureEnvironmentResolver:
    def __init__(self):
        self.dependency_digest = "sha256:synthetic-dependency-content"
        self.calls = 0

    def resolve(self, request):
        self.calls += 1
        detail = EnvironmentResolutionFact(
            carrier_id="explicit-synthetic-test-carrier",
            executable_path="synthetic-registered-python",
            executable_digest="sha256:fixture-exe",
            interpreter_version="3.13.1",
            base_executable_path="synthetic-base-python",
            base_executable_digest="sha256:fixture-base",
            probe_prefix="synthetic-prefix",
            base_prefix="synthetic-base-prefix",
            configuration_digest="sha256:fixture-config",
            dependency_roots=("synthetic-registered-dependencies",),
            dependency_set_digest=self.dependency_digest,
        )
        return EnvironmentRefFact(
            environment_id=request.environment_id,
            revision=1,
            isolation_mode=EnvironmentIsolationModeFact(request.isolation_mode),
            interpreter_identity=detail.interpreter_identity,
            dependency_set_digest=detail.dependency_set_digest,
            resolution=detail,
        )
