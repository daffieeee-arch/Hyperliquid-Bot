"""PAPER-only FastAPI control-service baseline."""

from .app import app, create_control_service

__all__ = ["app", "create_control_service"]
