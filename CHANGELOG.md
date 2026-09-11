# Changelog

## 0.2.0 - 2026-09-11

### Added

- Added task and acceptance-item domain models.
- Added task creation, lookup, and project filtering.
- Added referential validation so tasks cannot reference missing projects.
- Added Python API methods for task and acceptance-item workflows.
- Added `task-create`, `task-get`, and `task-list` CLI commands.
- Added unit tests for task invariants and task context persistence.

### Fixed

- Fixed Windows CLI output encoding for JSON containing Chinese text.

### Scope

This release is the FR02 task and acceptance-item slice. It does not complete delivery, test planning, execution, evidence, defect, report, AI, or host-protocol workflows.
