from ai_test.application.errors import FeatureNotImplemented


def create_defect(*_args: object, **_kwargs: object) -> None:
    raise FeatureNotImplemented("defect management is planned for a later increment")

