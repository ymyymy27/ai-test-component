# Changelog

## 0.4.0 - 2026-09-22

- Merge guo and ma history, adopt the rewritten phase-one documentation.
- Replace the old ai_test package with the documented aitest module layout.
- Reuse project/task/delivery validation and ma atomic-write primitives.
- Remove ASGI, direct business writers, team signatures and old panel resources.
- Add strict local contracts, six template packs, shared TypeScript panel and candidate Trae VSIX.
- Add architecture/contract/fault tests, Windows/Linux CI and explicit untested product ACs.
- Breaking: old imports and workspace formats are not supported by this skeleton.
- Execution, durable transactions, IPC, recovery and real Trae acceptance remain pending.


## 0.3.0 - 2026-09-13

### Added

- Added chunked record indexes so writes only rewrite the affected shard.
- Added automatic migration from the legacy full-index workspace state.
- Added real workspace writability probes for `doctor`.
- Added FR03 delivery declarations linked to tasks.
- Added delivery Python API and `delivery-create`, `delivery-get`, and `delivery-list` CLI commands.
- Added tests for chunked indexes, legacy migration, writability checks, delivery invariants, and delivery persistence.

### Fixed

- Updated `uv.lock` to match the package version.
- Fixed the unbounded `state.json` record map that conflicted with the v2.4 architecture.
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
