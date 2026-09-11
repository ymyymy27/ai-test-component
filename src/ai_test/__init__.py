"""Stable public API for the AI test component."""

from ai_test.composition import Component, create_component
from ai_test.interfaces.asgi_host import AitestASGIApp

__all__ = ["AitestASGIApp", "Component", "create_component"]
__version__ = "0.2.0"

