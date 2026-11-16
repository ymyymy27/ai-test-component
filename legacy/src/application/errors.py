"""Stable application errors; messages must not contain secrets or raw logs."""


class CapabilityUnavailable(RuntimeError):
    code = "CAPABILITY_UNAVAILABLE"


class WorkspaceInUse(RuntimeError):
    code = "WORKSPACE_IN_USE"


class CoreStartupFailed(WorkspaceInUse):
    """Core exited during startup: distinct from "running core cannot be connected"."""

    code = "CORE_STARTUP_FAILED"


class PersistentIntentConflict(ValueError):
    code = "INTENT_CONFLICT"
