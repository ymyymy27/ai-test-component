from ai_test.application.errors import FeatureNotImplemented


def analyze_with_ai(*_args: object, **_kwargs: object) -> None:
    raise FeatureNotImplemented("AI assistance is planned for a later increment")

