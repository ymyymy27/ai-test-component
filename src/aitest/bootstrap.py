"""Only composition root. Reserved adapters are never loaded or advertised."""

from uuid import uuid4

from aitest.interfaces.local.api import LocalAPI


def create_api() -> LocalAPI:
    return LocalAPI(instance_id=str(uuid4()))
