"""执行提交协调器门面。

事务脚本按职责拆在 commit_base / commit_records / start_claim /
snapshot_staging / checkpoint_publication / runtime_revision_stage /
initial_registration；本模块只保留数据类 re-export 与组合后的
`ExecutionCommitCoordinator`，方法名与调用方式不变。
"""

from __future__ import annotations

from aitest.application.execution.checkpoint_publication import CheckpointPublicationMixin
from aitest.application.execution.commit_base import CommitCoordinatorBase
from aitest.application.execution.commit_guards import (
    _admission_source_check_fact as _admission_source_check_fact,
)
from aitest.application.execution.commit_guards import (
    _authorization_record_id as _authorization_record_id,
)
from aitest.application.execution.commit_guards import (
    _intent_record_id as _intent_record_id,
)
from aitest.application.execution.commit_guards import (
    _json_payload as _json_payload,
)
from aitest.application.execution.commit_guards import (
    _payload_digest as _payload_digest,
)
from aitest.application.execution.commit_guards import (
    _require_record_envelope as _require_record_envelope,
)
from aitest.application.execution.commit_guards import (
    _run_pointer_id as _run_pointer_id,
)
from aitest.application.execution.commit_guards import (
    _validate_current_facts as _validate_current_facts,
)
from aitest.application.execution.commit_guards import (
    _validate_frozen_run_basis as _validate_frozen_run_basis,
)
from aitest.application.execution.commit_guards import (
    _validate_frozen_step_basis as _validate_frozen_step_basis,
)
from aitest.application.execution.commit_guards import (
    _validate_original_grant_proof as _validate_original_grant_proof,
)
from aitest.application.execution.commit_guards import (
    _validate_publication_current as _validate_publication_current,
)
from aitest.application.execution.commit_records import CommitRecordsMixin
from aitest.application.execution.commit_types import (
    CheckpointPayloadCodec as CheckpointPayloadCodec,
)
from aitest.application.execution.commit_types import (
    ExecutionCommitBatch as ExecutionCommitBatch,
)
from aitest.application.execution.commit_types import (
    ExecutionCommitResult as ExecutionCommitResult,
)
from aitest.application.execution.initial_registration import InitialRegistrationMixin
from aitest.application.execution.runtime_revision_stage import RuntimeRevisionStageMixin
from aitest.application.execution.snapshot_staging import SnapshotStagingMixin
from aitest.application.execution.start_claim import StartClaimMixin
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork


class ExecutionCommitCoordinator(
    CommitCoordinatorBase,
    CommitRecordsMixin,
    StartClaimMixin,
    SnapshotStagingMixin,
    CheckpointPublicationMixin,
    RuntimeRevisionStageMixin,
    InitialRegistrationMixin,
):
    """组合各职责 mixin；所有公开方法保持不变。"""


__all__ = [
    "ExecutionCommitBatch",
    "ExecutionCommitCoordinator",
    "ExecutionCommitResult",
    "CheckpointPayloadCodec",
    "StageableWorkspaceUnitOfWork",
]
