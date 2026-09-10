from ai_test.application.errors import FeatureNotImplemented
from ai_test.application.ports.execution import ExecutionRequest, ExecutionResult


class LocalProcessExecutor:
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise FeatureNotImplemented("safe process execution is not implemented in the skeleton")

