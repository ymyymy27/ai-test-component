from ai_test.application.errors import FeatureNotImplemented


def export_report(*_args: object, **_kwargs: object) -> None:
    raise FeatureNotImplemented("reporting is planned for a later increment")

