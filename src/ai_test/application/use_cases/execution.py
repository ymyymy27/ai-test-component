from ai_test.application.errors import FeatureNotImplemented


def run_test(*_args: object, **_kwargs: object) -> None:
    raise FeatureNotImplemented("test execution is planned for a later increment")

