from ai_test.application.errors import FeatureNotImplemented


def create_test_plan(*_args: object, **_kwargs: object) -> None:
    raise FeatureNotImplemented("test planning is planned for a later increment")

