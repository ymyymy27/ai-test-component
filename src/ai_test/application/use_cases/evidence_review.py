from ai_test.application.errors import FeatureNotImplemented


def review_evidence(*_args: object, **_kwargs: object) -> None:
    raise FeatureNotImplemented("evidence review is planned for a later increment")

