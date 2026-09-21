"""Stable application errors; messages must not contain secrets or raw logs."""


class CapabilityUnavailable(RuntimeError):
    code = "CAPABILITY_UNAVAILABLE"


class WorkspaceInUse(RuntimeError):
    code = "WORKSPACE_IN_USE"
