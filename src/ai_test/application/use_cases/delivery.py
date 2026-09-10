from ai_test.application.errors import FeatureNotImplemented


def register_delivery(*_args: object, **_kwargs: object) -> None:
    raise FeatureNotImplemented("delivery intake is planned for a later increment")

