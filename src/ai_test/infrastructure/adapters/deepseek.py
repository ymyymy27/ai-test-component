from ai_test.application.errors import FeatureNotImplemented
from ai_test.application.ports.model_provider import ModelRequest, ModelResult


class DeepSeekProvider:
    """DeepSeek adapter boundary; transport and credentials arrive in the AI increment."""

    provider = "deepseek"
    default_model = "deepseek-chat"

    def complete(self, request: ModelRequest) -> ModelResult:
        raise FeatureNotImplemented("DeepSeek transport is not implemented in the skeleton")

