# 源码模块逐文件索引

全量 AST、导入、调用点与职责清点；默认链路及风险函数另见报告和反例。tests 和历史取证脚本仅清点，不计作产品完成。

## TypeScript 与构建配置

- `integrations/trae/scripts/build.mjs` (7 行)：全文核对面板/宿主只读桥、打包或浏览器配置；不据构建配置认定业务功能已完成。
- `integrations/trae/src/core-bridge.ts` (282 行)：全文核对面板/宿主只读桥、打包或浏览器配置；不据构建配置认定业务功能已完成。
- `integrations/trae/src/extension.ts` (133 行)：全文核对面板/宿主只读桥、打包或浏览器配置；不据构建配置认定业务功能已完成。
- `src/aitest/resources/panel/playwright.config.ts` (8 行)：全文核对面板/宿主只读桥、打包或浏览器配置；不据构建配置认定业务功能已完成。
- `src/aitest/resources/panel/src/index.ts` (335 行)：全文核对面板/宿主只读桥、打包或浏览器配置；不据构建配置认定业务功能已完成。

## __init__.py

- `src/aitest/__init__.py` (3 行)：Phase-one engineering skeleton, not product acceptance.；定义：；保存调用=False

## application

- `src/aitest/application/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/application/ai_assistance.py` (43 行)：AI 辅助用例侧入口：模型出站编排。；定义：；保存调用=False
- `src/aitest/application/approval_service.py` (675 行)：Persist a controlled single-use challenge and exact user-confirmation receipt.；定义：_digest, _payload, _decode, ApprovalService；保存调用=True
- `src/aitest/application/connectivity.py` (9 行)：The single bounded transport retry policy; no execution or sleeping here.；定义：retry_delay；保存调用=False
- `src/aitest/application/controlled_write.py` (553 行)：Atomic controlled record writes and exact saved origin, shared by registered actions.；定义：ControlledWrite, controlled_write, SavedControlledWriteResolver, ControlledWriteService；保存调用=True
- `src/aitest/application/errors.py` (13 行)：Stable application errors; messages must not contain secrets or raw logs.；定义：CapabilityUnavailable, WorkspaceInUse, PersistentIntentConflict；保存调用=False
- `src/aitest/application/ports.py` (848 行)：A-package abstract ports; implementations live in infrastructure.；定义：EnvironmentResolutionRequest, EnvironmentResolver, ExecutionActionResolver, ExecutionAuthorizationProof, RecordIntentOpening, ExactRecordStaging, RecordValueProtector, TransactionPort, StoragePort, BackupPort, IndexPort, QueryPort, LocalProtocolPort, BackgroundWorkDirectory, Clock, ControlledActorContext, ApprovalActionResolver, ApprovalIdentitySource, ApprovalRecords, BasisConfirmationProof, ModelPolicyConfirmationProof, ControlledWriteProof, WorkspaceUnitOfWork, StageableWorkspaceUnitOfWork, RecordRepository, RuntimeExecutionReader, RuntimeRevisionBasisReader, RuntimeRevisionOriginProof, RevisionRecordReader, EvidenceReferenceValidator, EvidenceObjectStore, ExecutionEvidenceCollector, CollectedRedactionSummary, ExecutionRedactionCollector, ModelResponseStore, SourceSnapshotPort, SourceControlPort, ExecutionPort, SpoolStreamWriter, SpoolRedactionInspection, SpoolRedactionSummaryReader, SpoolStore, CheckpointPort, VerificationPort, VerificationRequest, BusinessVerificationPort, ReadOnlyBusinessQueryPort, DeadlineBusinessQueryPort, CapturedBusinessVerification, BusinessVerificationCapturePort, BusinessVerificationResolver, ModelProvider, ProjectionPort, ReportArtifactPort, SecretPort, MaintenancePort, CoreMessagePort；保存调用=False
- `src/aitest/application/record_write.py` (238 行)：An ordinary record and its exact original business result share one transaction.；定义：RecordWriteBlocked, RecordWriteConflict, _bytes, _digest, RecordWriteIntent, _require_safe；保存调用=True
- `src/aitest/application/usecase_registry.py` (2349 行)：B 包用例注册表：把 B 的用例接到统一入口（`aitest.local/2.0`）。；定义：BUseCaseError, _as_text, _as_mapping, _as_limit, _optional_text, _stage_result, _command_project_id, _command_expected_revision, _command_parameters, _command_action, _require_owned_by_command, _draft_result, _plain_gap, _generated_content, _publication_result, _enum_or, _rule_versions_for, _rule_version_from_payload, _template_refs_for, _rule_draft_of, _required, _model_of, _models_of, _text_list, _enum_of, _bool_of, _int_of, _revision_of, _payload_revision_or_none, _expected_revision_or_none, _optional_revision, _gaps_of, _optional_commit, _preparation_inputs, _guard, BUseCaseDependencies, BUseCaseRegistry, build_b_use_case_registry；保存调用=True

## application/evidence

- `src/aitest/application/evidence/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/application/evidence/evidence_review.py` (98 行)：Independent evidence review framework; no final business decision logic.；定义：freeze_verification_request, EvidenceReviewService, _freeze_query_fields；保存调用=False
- `src/aitest/application/evidence/execution_outputs.py` (273 行)：Frozen source binding and actual output bytes for default command evidence.；定义：SavedExecutionEvidence；保存调用=False
- `src/aitest/application/evidence/external_imports.py` (371 行)：Keep immutable safe external material without inventing plugin execution facts.；定义：ExternalImportBlocked, _encoded, _json_material, _object_ref, SavedExternalResultImport；保存调用=True
- `src/aitest/application/evidence/publication.py` (164 行)：Publish verified spool blocks into immutable evidence references.；定义：EvidencePublicationContext, TransactionalCommitPort, EvidencePublisher；保存调用=False
- `src/aitest/application/evidence/query_materials.py` (559 行)：Exact saved independent-query material shared by replay and historic sources.；定义：BusinessVerificationBlocked, _bytes, _request_payload, _request, _evidence_record, _polling, SavedBusinessQueryReader；保存调用=False
- `src/aitest/application/evidence/redaction_materials.py` (189 行)：Exact immutable summary bytes and their original output stream basis.；定义：encode_summary, summary_basis, verify_redaction_material, read_saved_redaction, SavedRedactionMaterials；保存调用=False
- `src/aitest/application/evidence/saved_verification.py` (381 行)：Save independent business reads before publishing their verification facts.；定义：SavedBusinessVerification；保存调用=True

## application/execution

- `src/aitest/application/execution/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/application/execution/action_resolver.py` (519 行)：生产动作解析器：把 B 的冻结绑定与 C 的 start 准入事实构造成一次动作。；定义：ActionResolutionBlocked, SavedActionResolver, default_side_effect_class, FactsAttemptIndex, _binding, _snapshot, _environment_resolution, _environment_identity, _frozen_timeout_ms, _step_content_scope, _admission_digest, build_saved_action_resolver；保存调用=False
- `src/aitest/application/execution/authorization.py` (982 行)：Original single-action consent, unused state and saved occupation authority.；定义：_identity, ExecutionResolutionUnavailable, SavedExecutionAuthorizationResolver, ExecutionAuthorizationService；保存调用=True
- `src/aitest/application/execution/authorization_index.py` (75 行)：Exact current unused authority, read by run identity rather than history scans.；定义：authorization_index_id, index_payload, read_authorization_index, stage_authorization_index；保存调用=True
- `src/aitest/application/execution/checkpoint_refs.py` (200 行)：Immutable checkpoint provenance alongside snapshots; never a reuse grant.；定义：_text, _digest, _revision, CheckpointRef, checkpoint_map_id, _envelope, read_checkpoint_refs, validate_checkpoint_payload, read_referenced_checkpoint, build_checkpoint_map；保存调用=False
- `src/aitest/application/execution/commands.py` (141 行)：Adapt public saved identities to the existing authorization use cases.；定义：InvalidExecutionCommand, RunRegistrationBlocked, ExecutionCommands；保存调用=False
- `src/aitest/application/execution/commit.py` (1850 行)：Atomic C-package publication through the A workspace unit of work.；定义：_validate_original_grant_proof, _require_record_envelope, CheckpointPayloadCodec, ExecutionCommitBatch, ExecutionCommitResult, ExecutionCommitCoordinator, _json_payload, _intent_record_id, _authorization_record_id, _run_pointer_id, _payload_digest, _validate_current_facts, _validate_frozen_step_basis, _validate_frozen_run_basis, _validate_publication_current；保存调用=True
- `src/aitest/application/execution/continuation.py` (41 行)：Stage a scheduling projection through the same business unit of work.；定义：read_continuation, stage_continuation, stage_existing_activity；保存调用=True
- `src/aitest/application/execution/control.py` (184 行)：Pause, cancellation, rollback and evidence-supplement control facts.；定义：RunControlAction, RunControlDecision, RunControlService, _requires_boundary_verification, boundary_pending；保存调用=False
- `src/aitest/application/execution/current.py` (163 行)：Project committed current-attempt changes without recalculating business results.；定义：project_current_update, _step_state；保存调用=False
- `src/aitest/application/execution/facts.py` (706 行)：Assemble the frozen ExecutionFacts contract from package C facts.；定义：execution_payload_digest, validate_attempt_projection, validate_execution_facts, project_attempt_update, validate_frozen_step_basis, validate_frozen_run_basis, _enum, ExecutionFactsBoundary, ExecutionFactsAssembly, ExecutionFactsAssembler, _validate_assembly_boundary, _effective_completeness, _plan_revision, _step_revision, _run_fact, _step_fact, project_attempt_fact, _evidence_fact, _redaction_summary_fact, _source_verification_fact, _verification_fact, _mock_fact；保存调用=False
- `src/aitest/application/execution/frozen_plan.py` (109 行)：Exact original plan/scope/cases shared by execution and history consumers.；定义：FrozenRunPlan, FrozenRunPlanReader；保存调用=False
- `src/aitest/application/execution/output_material.py` (50 行)：One saved-output identity and byte check for collection and exit admission.；定义：SavedOutputMaterialError, require_saved_output_material；保存调用=False
- `src/aitest/application/execution/recovery.py` (345 行)：Recovery decisions, spool salvage, and dependency invalidation.；定义：RecoveryAction, CheckpointStore, RecoveryResult, restore_checkpoint_attempt, recover_attempt, _recovery_material, _verify_material, _validate_manifest, _result；保存调用=False
- `src/aitest/application/execution/registration.py` (276 行)：Freeze an initial run from saved B authority before any C external effect.；定义：_digest, InitialRunRegistration, _initial_domain；保存调用=True
- `src/aitest/application/execution/reuse_basis.py` (73 行)：Selected historic proof and actual consumed ancestor fingerprints; no reuse grant.；定义：source_evidence_basis, consumed_dependency_basis；保存调用=False
- `src/aitest/application/execution/reuse_inspection.py` (267 行)：Compare exact historic material with a current target; never grant reuse.；定义：CaseReuseUnverified, CaseReuseInspection；保存调用=False
- `src/aitest/application/execution/reuse_material.py` (327 行)：Read the saved bytes of a historic case, without granting current eligibility.；定义：validate_saved_business_queries, validate_source_material；保存调用=False
- `src/aitest/application/execution/reuse_sources.py` (190 行)：Whole-case historic provenance; readable material alone never grants reuse.；定义：CaseReuseStepSource, CaseReuseSource, CaseReuseSourceReader；保存调用=False
- `src/aitest/application/execution/run_record.py` (36 行)：Canonical Run records preserve set meaning without losing strict field checks.；定义：run_record_payload, read_run_record；保存调用=False
- `src/aitest/application/execution/run_schedule.py` (589 行)：Advance one registered planned Run using only saved per-action consent.；定义：RunScheduleBlocked, dispatch_plan, SavedRunSchedule；保存调用=True
- `src/aitest/application/execution/runner.py` (995 行)：Serial execution loop and dependency dispatch skeleton.；定义：_ExecutionObservationMismatch, SerialExecutionItem, SerialExecutionResult, StartValidationPort, SerialRunner, _validate_collection_identity, _start_fingerprint；保存调用=False
- `src/aitest/application/execution/runtime_actions.py` (445 行)：Default controlled revision commands accept saved identities, never replacement facts.；定义：RuntimeRevisionBlocked, SavedRuntimeRevisionActions；保存调用=True
- `src/aitest/application/execution/runtime_revision.py` (711 行)：Persisted runtime revision material and exact effective-case provenance.；定义：SnapshotContentRef, StepContentChange, RunRevisionRecord, revision_record_id, revision_request_payload, request_from_payload, revision_input_digest, require_runtime_boundary, SavedRuntimeRevisionReader, RuntimePlanningRecordReader；保存调用=False
- `src/aitest/application/execution/runtime_revision_service.py` (70 行)：Short transaction ownership for the controlled C runtime revision component.；定义：RuntimeRevisionService；保存调用=True
- `src/aitest/application/execution/saved_control.py` (427 行)：Durable run control: save admission before observing or stopping original executions.；定义：RunControlBlocked, SavedRunControl；保存调用=True
- `src/aitest/application/execution/snapshots.py` (51 行)：Exact immutable execution snapshots shared by current and historic consumers.；定义：read_execution_snapshot；保存调用=False
- `src/aitest/application/execution/source_checks.py` (270 行)：实际物化源码的入口、导入与来源身份核验。；定义：SourceCheckRequest, SourceProbeObservation, SourceCheckOutcome, SourceProbePort, SourceVerificationService, _derive_state, _check_failure_class, _outside_materialized；保存调用=False
- `src/aitest/application/execution/start_identity.py` (33 行)：One fingerprint for the original executable request and its Attempt basis.；定义：execution_start_fingerprint；保存调用=False
- `src/aitest/application/execution/start_materialization.py` (154 行)：start 物化与工作目录解析：把固定快照物化到 `workdirs/<run_id>` 并完成准入。；定义：StartMaterializationBlocked, StartMaterializer；保存调用=False
- `src/aitest/application/execution/start_source_binding.py` (463 行)：C 侧 start 来源绑定解析：把 A 的物化映射变成 `source_binding_digest`。；定义：_escapes_via_link, SourceBindingUnverified, StartSourceBindingResolver, _require_exact_coverage, _entries, _actual_key, _verify_file, _mapping_digest, _is_digest；保存调用=False
- `src/aitest/application/execution/step_content.py` (177 行)：Immutable, independently readable step content behind a frozen StepRevisionRef.；定义：PreparedContentRef, StepContent, FrozenStepContent, freeze_initial_step_contents, StepContentReader；保存调用=False
- `src/aitest/application/execution/step_execution.py` (71 行)：Advance an authorized saved action without accepting executable input.；定义：StepExecutionBlocked, SavedStepExecution；保存调用=False

## application/planning

- `src/aitest/application/planning/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/application/planning/basis_approval.py` (100 行)：Freeze the exact saved assertion and owner for a controlled basis challenge.；定义：SavedBasisApprovalResolver；保存调用=False
- `src/aitest/application/planning/basis_confirmation.py` (155 行)：Append an exact basis confirmation; never rewrite plans or execution facts.；定义：BasisConfirmationError, BasisConfirmationConflict, BasisConfirmationService；保存调用=True
- `src/aitest/application/planning/basis_proof.py` (92 行)：Read an exact controlled confirmation; frozen state labels are not authority.；定义：SavedCaseBasisReader；保存调用=False
- `src/aitest/application/planning/basis_validation.py` (242 行)：Verify authoritative records behind a candidate, without producing execution facts.；定义：validate_prepared_material, _verify_links；保存调用=False
- `src/aitest/application/planning/controlled_publication.py` (238 行)：Canonical publication input uses the existing domain gates and exact saved materials.；定义：PublicationContent, _refs, publication_content；保存调用=False
- `src/aitest/application/planning/draft.py` (416 行)：草稿生成：应用模板产出**草稿**，并做**定向失效**。；定义：TemplateNotFoundError, TemplateSummary, load_template, list_templates, RevisionContext, DraftKind, GeneratedContent, DraftResult, apply_template, text_digest, template_draft_text, generated_content_payload, draft_expiry, TemplateRequirements, ProjectCapabilities, ApplicabilityAssessment, assess_template；保存调用=False
- `src/aitest/application/planning/drift.py` (328 行)：按冻结的仓储引用核对依据；旧记录缺少冻结标识时要求重新准备。；定义：BasisCheck, DriftReport, _readable, _snapshot_identity_check, _template_readable, check_frozen_basis；保存调用=False
- `src/aitest/application/planning/model_basis.py` (247 行)：Frozen model source/manual identities; external source observation stays outside UOW.；定义：_digest, ModelGenerationBasis；保存调用=False
- `src/aitest/application/planning/model_orchestration.py` (1189 行)：模型请求编排：策略 → 准入 → 投影 → 登记意图 → 调用 → 登记结果。；定义：OutboundRequest, OutboundOutcome, _blocked, policy_record_id, outbound_request_id, ModelGenerationConflictError, _current_revision, _current_payload, _payload_revision, _optional_revision, _recall_existing_draft, _filter_known_credentials, _reject_short_credentials, _credential_filter_payload, _generation_identity, _admission_reason, request_model_draft, _combined_currency, _recall_outcome, _publish_model_response, _template_version, _intent_payload, _outcome_payload, _identity_fields, ResponseSettlement, settle_response；保存调用=True
- `src/aitest/application/planning/model_policy_confirmation.py` (426 行)：Save and consume an outbound policy's exact controlled confirmation atomically.；定义：policy_payload, policy_parameters, SavedModelPolicyApprovalResolver, SavedHumanActionResolver, ModelPolicyConfirmationService；保存调用=True
- `src/aitest/application/planning/model_ports.py` (234 行)：模型出站的端口协议：**B 侧窄接口**，与薄底座同一路线。；定义：ProjectionStatus, ModelCallStatus, CredentialStatus, ProjectedMaterial, Projection, CredentialResolution, ModelCall, ModelCallResult, MaterialProjector, CredentialResolver, ModelCaller；保存调用=False
- `src/aitest/application/planning/model_response_resolution.py` (281 行)：Reconcile an exact saved response; never repeat an external model call.；定义：_text, _integer, _same, _request, _response, resolve_model_response；保存调用=False
- `src/aitest/application/planning/persistence.py` (327 行)：计划域材料的落盘编排：把 `Case` / `AcceptanceScope` / `ConfirmationRecord` 存成不可变记录。；定义：_stage_and_commit, _require_revision_matches_assignment, _load_payload, save_case, load_case, save_acceptance_scope, load_acceptance_scope, save_rule_draft, save_confirmation, load_confirmation；保存调用=True
- `src/aitest/application/planning/plan_builder.py` (127 行)：由**发布请求**构造 `Plan`：请求形状的落点。；定义：case_revision_ref, template_version_ref, rule_revision_ref, build_plan；保存调用=False
- `src/aitest/application/planning/portable.py` (264 行)：模板与规则的导入导出：**可逐字节往返的规范 JSON**。；定义：canonical_json, canonical_digest, export_template, import_template, rule_draft_to_payload, rule_draft_from_payload, export_rule_version, import_rule_version, export_rule_payloads, import_rule_payloads, _required_text, _text_list；保存调用=False
- `src/aitest/application/planning/preparation.py` (486 行)：准备意图与幂等规则：身份、输入摘要与四态判定。；定义：PreparationDecision, _require_text, _require_revision, InputRevisions, preparation_identity_digest, preparation_record_id, preparation_intent_id, record_id_for_intent_id, _canonical_item, payload_hash, PreparationRequest, PreparationRecord, preparation_record_payload, _payload_text, preparation_record_from_payload, PreparationLookup, changed_inputs, decide_preparation；保存调用=False
- `src/aitest/application/planning/preparation_origin.py` (101 行)：Read the exact B preparation and original receipt without inventing a new intent.；定义：_exact_payload, load_saved_preparation, validate_preparation_origin；保存调用=False
- `src/aitest/application/planning/prepare_run.py` (514 行)：`prepare_run` 用例：把业务输入编排成不可变的 `PreparedRun`。；定义：PreparationInputs, _require_text, preparation_payload, _prepared_run_id, _invalidation_rules_for, _build, prepare_run；保存调用=True
- `src/aitest/application/planning/publish.py` (352 行)：发布编排：把领域门禁通过的草稿落成**不可变修订**。；定义：payload_digest, PublicationResult, _blocked, _blocking_or_none, _current_revision, _revision_to_stage, publish_rules, publish_plan, _plan_payload, plan_publication_digest；保存调用=True
- `src/aitest/application/planning/regression.py` (18 行)：Reverse dependency propagation including cycles and the changed module itself.；定义：affected_modules；保存调用=False
- `src/aitest/application/planning/regression_graph.py` (169 行)：回归影响面：从**已冻结的模块依赖图**做反向传播。；定义：graph_dependency_map, affected_modules_from_graph, affected_modules_from_store, _normalise_source_path, _covers, RegressionScope, changed_modules, regression_scope；保存调用=False
- `src/aitest/application/planning/rules_markdown.py` (419 行)：规则的可携带 **Markdown** 形态：人类可读、可往返、不引入新依赖。；定义：rule_markdown_from_payload, _render_section, _render_body, _render_list_section, _render_unknown_section, _require_portable_payload, _as_text_list, rule_markdown_to_payload, rule_draft_from_markdown, _first_non_blank, _parse_title, _parse_metadata_line, _parse_body, _parse_list, _parse_unknown；保存调用=False
- `src/aitest/application/planning/run_mode.py` (187 行)：运行中修订的**消费侧**：把 C 的 `ExecutionFacts` 翻译成领域纯值，再交给领域守卫。；定义：runtime_facts_from_execution_facts, request_runtime_revision；保存调用=False
- `src/aitest/application/planning/saved_runtime_revision.py` (295 行)：Assess a runtime change using saved authority; persist and recheck separately.；定义：SavedRuntimeRevisionAssessment；保存调用=False
- `src/aitest/application/planning/serialization.py` (331 行)：计划域对象的落盘形状：`Case` / `AcceptanceScope` / `ConfirmationRecord`。；定义：_require_mapping, _text, _optional_text, _revision, _text_tuple, _text_set, _pairs, _enum, case_link_to_payload, case_link_from_payload, assertion_basis_to_payload, assertion_basis_from_payload, case_to_payload, case_from_payload, acceptance_scope_to_payload, acceptance_scope_from_payload, confirmation_to_payload, confirmation_from_payload, case_content_digest, case_revision_digest；保存调用=False
- `src/aitest/application/planning/substrate.py` (655 行)：B 侧薄底座协议：提交、按修订读、幂等查询。；定义：ConcurrentEditError, SubstrateContractError, IndexMaintenanceRequired, InvalidQueryCursor, PreparationConflictError, CommittedRecord, RecordQuery, RecordPage, StagedRevision, CommitResult, UnitOfWork, Transaction, transaction, RecordReader, require_record_identity, require_scoped_record, read_scoped_record, current_record；保存调用=True
- `src/aitest/application/planning/substrate_adapter.py` (667 行)：薄转接头：把 A 的正式工作单元/仓储接到 B 的窄底座协议。；定义：WorkspacePorts, RecordRepositoryPorts, CommitSequenceSource, _require, _release_on_collection, _latest_committed, _preparation_entry, _require_preparation_identity, PortsRecordReader, PortsUnitOfWork, _created_rows；保存调用=True

## application/project

- `src/aitest/application/project/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/application/project/context.py` (509 行)：项目上下文用例：建立项目、绑定、模块、依赖与环境，并**列缺口而阻塞**。；定义：ContextGap, blocking_gaps, _require_text, _require_revision, create_project, revise_project, path_is_canonical_absolute, BindingInputs, BindingResult, create_binding, move_binding, EnvironmentInputs, EnvironmentResult, create_environment, register_graph, detect_context_gaps；保存调用=False
- `src/aitest/application/project/delivery_submission.py` (119 行)：Normalize only exact saved delivery/task/source references for controlled submission.；定义：_reference, submission_content；保存调用=False
- `src/aitest/application/project/environment_resolution.py` (161 行)：Resolve actual carrier observations and recheck exact saved input authority.；定义：EnvironmentResolutionBlocked, EnvironmentResolutionService；保存调用=False
- `src/aitest/application/project/persistence.py` (520 行)：项目上下文的落盘编排：把已建好的上下文对象存成不可变记录。；定义：dependency_graph_record_id, _stage_and_commit, _load_payload, _verify_payload_project, save_project, save_binding, save_environment, save_dependency_graph, save_source_snapshot, load_source_snapshot, load_project, load_binding, load_environment, load_dependency_graph, save_task, save_delivery, load_task, load_delivery；保存调用=True
- `src/aitest/application/project/serialization.py` (785 行)：项目与绑定的 payload 序列化：不适用键真正省略。；定义：_binding_key_order, binding_to_payload, binding_from_payload, _module_to_payload, _module_from_payload, project_to_payload, project_from_payload, _secret_ref_to_payload, environment_to_payload, environment_from_payload, _dependency_to_payload, dependency_graph_to_payload, dependency_graph_from_payload, _require_payload_mapping, _payload_text, _payload_optional_text, _payload_revision, _payload_text_tuple, acceptance_item_to_payload, acceptance_item_from_payload, task_to_payload, task_from_payload, delivery_to_payload, delivery_from_payload, source_manifest_to_payload, source_manifest_from_payload, _payload_non_negative_int；保存调用=False
- `src/aitest/application/project/source_analysis.py` (585 行)：Bind actual pinned bytes to B's saved, project-owned source identity.；定义：SourceAnalysisError, SourceIntentConflict, _digest, _strings, _metadata_strings, SourceAnalysisService；保存调用=True

## application/review

- `src/aitest/application/review/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/application/review/defect_management.py` (185 行)：Application facade for issue lifecycle and disposition commands.；定义：_require_expected_revision, mark_awaiting_confirmation, confirm_issue, start_issue, record_fix, close_issue, record_failed_regression, update_issue_disposition；保存调用=False
- `src/aitest/application/review/execution_decision_adapter.py` (328 行)：Consume C ExecutionFacts into the unique D DecisionFacts contract.；定义：AssertionOutcome, AssertionOutcomeFact, DecisionContextFacts, _source_state, _evidence_valid, _assertion_valid, _assertion_sets, _case_steps, _case_executed, _case_pending, _required_evidence_valid, _critical_paths_satisfied, build_decision_facts；保存调用=False
- `src/aitest/application/review/issue_queries.py` (24 行)：Application boundary for the finite issues.list query.；定义：list_issues；保存调用=False
- `src/aitest/application/review/reporting.py` (111 行)：Application use cases for immutable reports, local review, and export.；定义：create_report, record_local_review, register_report_export；保存调用=False

## bootstrap.py

- `src/aitest/bootstrap.py` (1756 行)：唯一 A 包装配入口。B/C/D 只能提交用例注册表。；定义：CoreInstance, CoreAssemblyBlocked, CoreConnectionLedger, _fsync_directory, _json_value, CoreAssembly, _interpreter_verifier, _module_names_from_project, _pattern_relative_dir, _source_verification_provider, _required_text, _positive_int, _entry_actual_path, _environment_ref, _frozen_adapter_version, _rules_revision, assemble_workspace_core, UseCaseRegistry, CoreBootstrap, registered_use_cases, SystemProcessLauncher, await_core_launch_claim, make_pipe_connector, make_editor_host, acquire_endpoint, acquire_existing_endpoint, shutdown_endpoint, register_use_cases, create_api；保存调用=False

## contracts

- `src/aitest/contracts/__init__.py` (24 行)：；定义：；保存调用=False
- `src/aitest/contracts/capabilities.py` (35 行)：Capability advertisement for the local core, independent of business rules.；定义：Capability, CapabilitySet；保存调用=False
- `src/aitest/contracts/commands.py` (113 行)：Local protocol boundary; known actions are not necessarily implemented capabilities.；定义：Command；保存调用=False
- `src/aitest/contracts/errors.py` (49 行)：Structured, transport-safe A-package errors.；定义：ErrorCode, ErrorDTO；保存调用=False
- `src/aitest/contracts/events.py` (25 行)：Committed immutable event envelope.；定义：Event；保存调用=False
- `src/aitest/contracts/execution_facts.py` (600 行)：Frozen cross-package schema for package C execution facts.；定义：ContractModel, RunControlStateFact, StepLevelFact, StepStateFact, AttemptStateFact, AdapterKindFact, SideEffectClassFact, CaptureCompletenessFact, ProcessTerminationReasonFact, EvidenceKindFact, EvidenceCaptureSourceFact, EvidenceIntegrityFact, RedactionStateFact, ProjectionStateFact, EvidenceLevelFact, SourceBindingKindFact, SourceVerificationStateFact, MockDeclarationSourceFact, MockVerificationStateFact, VerificationObservationFact, FactCompleteness, PlanRevisionRefFact, StepRevisionRefFact, ConsumedOutputFact, ConsumedConditionFact, ExecutionHandleFact, OutputCursorFact, RedactionSummaryFact, OutputBlockFact, ExitFactDTO, RunFact, StepFact, AttemptFact, CodeIdentityFact, EvidenceFact, SourceCheckFact, SourceVerificationFact, VerificationFact, MockDeclarationFact, DependencyInvalidationFact, UnknownReasonFact, EvidenceGapFact, CoverageSummary, ExecutionFacts；保存调用=False
- `src/aitest/contracts/identity.py` (28 行)：Stable identity value types for the local protocol.；定义：；保存调用=False
- `src/aitest/contracts/prepared_run.py` (512 行)：Versioned PreparedRun contract: the single business deliverable of package B.；定义：ContractModel, RunTierFact, RunDriverFact, ConclusionCeilingFact, AssertionBasisStateFact, BindingFormFact, EnvironmentIsolationModeFact, PreparedRunStatusFact, conclusion_ceiling_for, SnapshotRef, EnvironmentResolutionFact, EnvironmentRefFact, ExecutionSourceBinding, PlanRevisionRef, RuleVersionRef, TemplateVersionRef, CaseRevisionRef, SkippedScopeEntry, ExclusionEntry, ConfirmationRef, AssertionBasisEntry, FrozenCaseStep, CaseLinks, FrozenCase, AuthorizationRequirement, InvalidationRule, BlockingReason, GapEntry, PreparedRun；保存调用=False
- `src/aitest/contracts/queries.py` (140 行)：Finite query contracts; callers cannot request an implicit full scan.；定义：QueryUnsupportedFilter, QuerySpec, Query；保存调用=False
- `src/aitest/contracts/redaction.py` (177 行)：纯凭据脱敏原语：无业务依赖，供投影、协议出口与出站适配共用。；定义：_is_redaction_marker, scrub_secret_text, redact_structure, redact_json_text, safeguard_projector；保存调用=False
- `src/aitest/contracts/responses.py` (29 行)：Protocol responses containing facts and storage references only.；定义：PageInfo, Response；保存调用=False
- `src/aitest/contracts/reuse_reference.py` (170 行)：存量运行之间的整用例复用引用记录（合同形状 + 严格校验）。；定义：ReuseReference, _validate_step_map, _require_unique_text, reuse_reference_payload, reuse_reference_digest, parse_reuse_reference；保存调用=False
- `src/aitest/contracts/secrets.py` (49 行)：Secret resolution contracts shared by application ports and adapters.；定义：ResolvedSecret；保存调用=False
- `src/aitest/contracts/templates.py` (87 行)：Strict shipped template format. Samples are draft inputs, never execution evidence.；定义：TemplateImplementationStatus, TemplateItem, CriticalPath, DraftExample, TemplatePack；保存调用=False
- `src/aitest/contracts/verification.py` (35 行)：Independent read-only verification contract.；定义：VerificationState, VerificationFact；保存调用=False
- `src/aitest/contracts/versions.py` (10 行)：Stable protocol and storage schema versions owned by A.；定义：；保存调用=False
- `src/aitest/contracts/views.py` (19 行)：DTOs serialize core facts; views must not calculate business outcomes.；定义：CoverageDTO；保存调用=False

## domain

- `src/aitest/domain/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/domain/approvals.py` (170 行)：Single-use user confirmation rules; actors come from a controlled core context.；定义：EntryKind, ChallengeState, ApprovalRequired, ApprovalConflict, ApprovalMaterialRef, ActionBasis, UserInteraction, TrustedActor, ApprovalChallenge, ActionConfirmation, require_human_actor, require_controlled_actor, require_challenge_confirmation, _text；保存调用=False
- `src/aitest/domain/json_material.py` (56 行)：Pure strict JSON decoding shared by transport and immutable material readers.；定义：_unique, _nonfinite, _finite_decimal, require_json_text, decode_json；保存调用=False

## domain/evidence

- `src/aitest/domain/evidence/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/domain/evidence/evidence.py` (370 行)：Evidence domain models.；定义：_require_text, _require_non_negative, EvidenceKind, EvidenceCaptureSource, EvidenceIntegrity, RedactionState, ProjectionState, TraceNodeType, TraceNodeState, TruthClass, MockDeclarationSource, MockVerificationState, VerificationObservation, EvidenceGapKind, EvidenceLevel, StoredObjectRef, CodeIdentity, RedactionSummary, EvidenceRef, TraceNode, MockDeclaration, Verification, EvidenceGap, ExternalImportRef；保存调用=False
- `src/aitest/domain/evidence/polling.py` (129 行)：Frozen independent-query timing and observations; no clock or I/O here.；定义：QueryPollingPolicy, query_polling_policy, BusinessQueryObservation, derive_polled_query, query_observation_result；保存调用=False
- `src/aitest/domain/evidence/redaction_material.py` (101 行)：One strict meaning for temporary and frozen safe stream summaries.；定义：_require_str, _require_int, _require_list, parse_redaction_summary, redaction_summary_payload；保存调用=False
- `src/aitest/domain/evidence/verification.py` (34 行)：One comparison of actual JSON fields; filtered required data stays unknown.；定义：compare_business_fields；保存调用=False

## domain/execution

- `src/aitest/domain/execution/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/domain/execution/assertions.py` (132 行)：Pure JSON assertion rules; unavailable observations remain unknown.；定义：HttpAssertionOperator, HttpAssertion, json_equal, evaluate_http_assertion, compare_expected_fields, read_json_path, freeze_json_value；保存调用=False
- `src/aitest/domain/execution/authorization.py` (79 行)：Resolved actions and the one-use authorization state are separate facts.；定义：AuthorizationState, ResolvedExecutionAction, require_unused；保存调用=False
- `src/aitest/domain/execution/cases.py` (163 行)：Pure whole-case execution/reuse aggregation, separate from business outcome.；定义：StepExecutionBasis, CaseExecutionAggregate, aggregate_case_execution, _has_attempt；保存调用=False
- `src/aitest/domain/execution/continuation.py` (49 行)：Stable, saved scheduling identity; an active flag grants no execution.；定义：continuation_id, RunContinuation, _identity_text；保存调用=False
- `src/aitest/domain/execution/dependencies.py` (127 行)：Pure invalidation of actual output/condition consumers and whole-case reuse.；定义：AttemptInvalidation, CaseReuseBasis, CaseReuseInvalidation, invalidate_downstream_attempts, downstream_consumers, invalidate_reuse_bases；保存调用=False
- `src/aitest/domain/execution/output.py` (47 行)：A saved stream position proves its entire attached, contiguous prefix.；定义：require_saved_output_cursors；保存调用=False
- `src/aitest/domain/execution/payload_identity.py` (12 行)：One canonical identity for immutable execution facts and their references.；定义：execution_payload_digest；保存调用=False
- `src/aitest/domain/execution/reuse.py` (133 行)：Legal whole-case reuse requires a complete, current basis, not inheritance.；定义：_text, ReuseIdentity, ReuseConditionKind, ReuseConditionState, ReuseCondition, WholeCaseReuseBasis；保存调用=False
- `src/aitest/domain/execution/runs.py` (908 行)：Execution domain models.；定义：_require_text, _require_positive, _require_non_negative, RunTier, RunControlState, StepLevel, StepState, AttemptState, AdapterKind, SideEffectClass, FailureClass, CaptureCompleteness, OutputStreamName, ProcessTerminationReason, TransportErrorClass, PlanRevisionRef, StepRevisionRef, InputRef, RegisteredEntryRef, EvidenceRequirement, DependencyEdge, ConsumedOutput, ConsumedCondition, AuthorizationRef, authorization_action_basis, ExecutionRequest, ExecutionHandle, OutputCursor, OutputBlockRef, CapturedOutputBlock, SpoolManifest, ExitFact, RecoveryCheckpoint, StructuredExecutionError, Run, Step, Attempt, attempt_start_basis, is_verified_exit_fact, has_verified_exit, execution_boundary_pending, has_complete_capture, _capture_counts, has_reliable_terminal_fact, RecoveryRecord, ExecutionInspectionState, ExecutionInspectionResult, ExecutionCollectionResult, StopRequestResult；保存调用=False
- `src/aitest/domain/execution/scheduling.py` (50 行)：Single dependency rule for serial dispatch; eligibility is not authorization.；定义：DispatchPlan, plan_serial_dispatch；保存调用=False
- `src/aitest/domain/execution/sources.py` (137 行)：Source identity verification facts owned by the execution domain.；定义：SourceBindingKind, SourceVerificationState, SourceCheckType, SourceFile, SourceSnapshot, ExecutionSourceVerification, SourceCheckResult；保存调用=False

## domain/planning

- `src/aitest/domain/planning/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/domain/planning/model_outbound.py` (438 行)：模型出站策略与出站准入规则；无 I/O 或框架依赖。；定义：MaterialKind, ModelTaskType, ResponseCurrency, _require_text, _require_commit, ModelEndpoint, OutboundConfirmation, endpoint_digest, material_kinds_digest, ModelOutboundPolicy, OutboundItem, validate_outbound_material, ModelResponseEnvelope, response_currency, response_currency_from_facts；保存调用=False
- `src/aitest/domain/planning/plans.py` (512 行)：计划、用例、断言依据与发布门禁；无 I/O 或框架依赖。；定义：RunTier, RunDriver, ConclusionCeiling, AssertionBasisState, CaseLayer, CaseImportance, PlanPublicationStatus, _require_text, _require_items, _require_unique, conclusion_ceiling_for, narrow_driver, AssertionBasis, ConfirmationRecord, effective_assertion_basis_state, CaseLink, Case, CaseRevisionRef, validate_case_publication, AcceptanceScope, TemplateVersionRef, Plan, validate_plan_publication；保存调用=False
- `src/aitest/domain/planning/rules.py` (155 行)：规则草稿与不可变发布版本；发布编排属应用用例。；定义：RuleEnablement, _require_text, _require_items, _require_unique, RuleDraft, RuleVersion, RuleRevisionRef, validate_draft_publication；保存调用=False
- `src/aitest/domain/planning/runtime_revision.py` (714 行)：运行中修订的纯守卫规则：吃 C 的**执行事实视图**，不吃调用者自报的结论。；定义：StepRuntimeState, AttemptRuntimeState, RunRuntimeState, StepRuntimeProgress, step_runtime_progress, StepRuntimeFacts, RunRuntimeFacts, RuntimeRevisionRefusalCode, RuntimeRevisionRefusal, CaseRuntimeChange, RuntimeRevisionRequest, RuntimeRevisionDecision, RuntimeRevisionRefused, _require_text, _require_items, _require_unique, assertion_basis_weakened, evaluate_runtime_revision, assert_runtime_revision_accepted；保存调用=False
- `src/aitest/domain/planning/templates.py` (9 行)：Pure template reference. Boundary validation lives in contracts/templates.py.；定义：TemplateRef；保存调用=False

## domain/project

- `src/aitest/domain/project/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/domain/project/context.py` (760 行)：Project, module, dependency, environment and delivery domain models.；定义：_require_text, _require_items, _require_unique, _canonical_portable_path, BindingForm, SourceForm, IsolationMode, DriveKind, WorkspaceLocationRejection, ImplementationStatus, DependencyOrigin, LocalProject, LocalProjectBinding, reject_workspace_location, Module, Dependency, ModuleDependencyGraph, affected_modules, SecretRef, EnvironmentRef, ResolvedEnvironment, SourceFileDigest, SourceManifest, source_content_identity, AcceptanceItem, Task, SelfReport, Delivery；保存调用=False
- `src/aitest/domain/project/deliveries.py` (52 行)：A formal delivery freezes references; it never turns declarations into verification.；定义：DeliverySubmission；保存调用=False
- `src/aitest/domain/project/source_identity.py` (22 行)：Name an immutable source record separately from its byte-level content identity.；定义：source_snapshot_record_identity；保存调用=False

## domain/review

- `src/aitest/domain/review/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/domain/review/defects.py` (458 行)：Unique issue records, lifecycle guards, and effective blocking rules.；定义：_require_text, _require_evidence, IssueStatus, IssueSeverity, IssueDisposition, IssueRecord, IssueClosureEvidence, _new_revision, _require_status, mark_awaiting_confirmation, confirm_issue, start_issue, record_fix, close_issue, record_failed_regression, mark_non_defect, mark_duplicate, defer_issue, undo_disposition, resolve_canonical_issue_id, effective_severity, effective_blocking_issue_ids；保存调用=False
- `src/aitest/domain/review/issue_queries.py` (403 行)：Finite issues.list query semantics.；定义：_require_text, IssueListScope, IssueFacet, IssueFilterMask, IssueReviewState, IssueBlockingState, IssueSeverityFilter, IssueQueryError, IssueQueryCursorMismatch, IssueListProjection, IssueListQuery, IssueListCursor, IssueListPage, _validate_facet_value, _parse_enum, _resolve_duplicate_root, is_issue_open, _matches_facet, _matches_severity, query_issue_list；保存调用=False
- `src/aitest/domain/review/reports.py` (803 行)：Phase-one review decisions and coverage sets.；定义：_require_text, _require_positive, Coverage, BusinessOutcome, EvidenceGrade, SourceIdentityState, ReviewGapCode, DecisiveFailure, FailureCandidate, derive_decisive_failures, ReviewGap, DecisionFacts, DecisionResult, _join_ids, _derive_gaps, _derive_evidence_grade, _full_pass_conditions_met, evaluate_review, ReportExportKind, ReportContext, ReportSnapshot, ReportDraft, create_report_revision, LocalReview, ReportExportKey, ReportExport, validate_reviews_for_report, create_report_export；保存调用=False

## examples

- `examples/python_library/sample.py` (2 行)：；定义：add；保存调用=False

## infrastructure

- `src/aitest/infrastructure/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/infrastructure/capabilities.py` (435 行)：动作级能力门（A-10）：按动作的真实依赖降级，不扩散为整体不可用。；定义：CapabilityState, DependencyCondition, GateDecision, CapabilityConditionStore, FileCapabilityConditionStore, CapabilityGate；保存调用=False
- `src/aitest/infrastructure/clock.py` (10 行)：；定义：SystemClock；保存调用=False
- `src/aitest/infrastructure/connections.py` (432 行)：连接配置及传输事实，重试策略属于应用层。；定义：EndpointError, TransportErrorKind, EndpointConfig, TransportFact, ConnectionState, classify_os_error, ConnectionProbe, ConnectionProbePort, ConnectionMonitor, ConnectionFactStore, LocalAPIConnectionBridge, _fact_object, _transport_fact；保存调用=False
- `src/aitest/infrastructure/credential_resolver.py` (63 行)：AB-001 §8.16.3 第 3 条候选甲：SecretPort → B 侧 CredentialResolver 窄适配。；定义：PurposeBoundCredentialResolver；保存调用=False
- `src/aitest/infrastructure/credentials.py` (232 行)：OS 按用途凭据解析；无明文工作空间回退。；定义：SecretUnavailable, SecretProvider, EnvironmentSecretProvider, _CredentialW, WindowsCredentialProvider, SecretManager；保存调用=False
- `src/aitest/infrastructure/exports.py` (4 行)：生成并核对摘要或证据包，不自行登记成功。；定义：；保存调用=False
- `src/aitest/infrastructure/path_compat.py` (36 行)：路径判定的版本兼容：`Path.is_junction` 自 Python 3.12 起才存在。；定义：；保存调用=False
- `src/aitest/infrastructure/projections.py` (192 行)：冻结策略的安全字节投影、真实摘要及排除原因。；定义：_looks_like_credential, _is_printable_text, _utf8_size, _gap, _safe_text, sha256_text, SafeMaterialProjector；保存调用=False
- `src/aitest/infrastructure/python_environment.py` (353 行)：Bounded, content-based probing of explicitly registered Python carriers.；定义：_digest, PythonEnvironmentCarrier, RegisteredPythonEnvironmentResolver；保存调用=False
- `src/aitest/infrastructure/security.py` (439 行)：落盘前凭据安全底线（A-09）。；定义：_flex, _stems, UnsafeMaterialError, KnownSecretRegistry, known_secrets, scrub_text, guard_bytes, guard_value, _guard, guard_json_text, _utf8_cut_point, StreamSecretFilter, copy_unchanged_safe_bytes；保存调用=False

## infrastructure/adapters

- `src/aitest/infrastructure/adapters/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/infrastructure/adapters/deepseek.py` (72 行)：可配置提供方及模型，不硬编码型号。；定义：ModelProfile, DeepSeekModelProvider；保存调用=False
- `src/aitest/infrastructure/adapters/execution/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/infrastructure/adapters/execution/agent.py` (127 行)：Agent reference capture; model claims require independent business verification.；定义：AgentToolCall, AgentEvaluation, _require_references, AgentAdapter；保存调用=False
- `src/aitest/infrastructure/adapters/execution/business_query.py` (182 行)：Independent GET of one registered business object with an absolute cutoff.；定义：HttpBusinessQuerySpec, HttpBusinessQueryReader；保存调用=False
- `src/aitest/infrastructure/adapters/execution/command.py` (1322 行)：Controlled local command adapter with redaction and streaming spool output.；定义：CommandHandleStore, CommandRegistration, _CommandRuntime, _JobBasicAccountingInformation, CommandAdapter, _cursors_from_buffers, _captured_blocks, _sha256_digest, _terminate_process_group, _process_group_stopped, _job_name, _resume_windows_process, _assign_windows_job, _close_job, _terminate_and_wait_windows_job, _kernel32, _stop_owned_windows_job, _process_start_identity, _real_pid, _process_is_alive, _wait_for_process_exit, _windows_process_start_identity, _windows_handle_identity, _linux_process_start_identity；保存调用=False
- `src/aitest/infrastructure/adapters/execution/external_result.py` (143 行)：External JSON recomputation; transient duplicate detection is not persistence.；定义：ExternalResultPayload, ExternalValidationResult, ExternalResultAdapter, _content_digest；保存调用=False
- `src/aitest/infrastructure/adapters/execution/http.py` (269 行)：HTTP execution adapter framework with structured exchange facts.；定义：HttpAssertionResult, HttpRequestSpec, HttpExchangeResult, HttpAdapter, _json_payload, _unique_fields, _json_path；保存调用=False
- `src/aitest/infrastructure/adapters/execution/http_transport.py` (250 行)：One direct HTTP exchange with a total deadline and bounded response material.；定义：NetworkExchange, _DeadlineSocket, _resolve, _connect, _valid_framing, exchange；保存调用=False
- `src/aitest/infrastructure/adapters/execution/manual_evidence.py` (94 行)：Manual operation capture with explicit provenance and attachments.；定义：ManualOperationType, ManualStep, ManualEvidenceRecord, ManualEvidenceAdapter；保存调用=False
- `src/aitest/infrastructure/adapters/execution/python_checks.py` (515 行)：实际解释器/入口/加载来源探针（A-08，一期执行来源合同）。；定义：_ModulePayload, _ProbePayload, PythonSourceProbeError, LoadedModuleFact, LoadSourceFact, SourceBindingVerdict, PythonLoadSourceProbe, _extract_payload, _validate_probe_payload, evaluate_source_binding, file_sha256, PythonChecksAdapter；保存调用=False
- `src/aitest/infrastructure/adapters/execution/redaction.py` (129 行)：Streaming byte redaction applied before command output reaches the spool.；定义：RedactionStats, StreamingRedactor, redact_bytes；保存调用=False
- `src/aitest/infrastructure/adapters/execution/registered_entry.py` (94 行)：默认执行端口：把已验证的 `ExecutionRequest` 绑成登记命令并真正启动进程。；定义：RegisteredEntryExecutionAdapter；保存调用=False
- `src/aitest/infrastructure/adapters/execution/verification.py` (328 行)：同业务对象独立只读核验。；定义：IndependentFileVerifier, BusinessVerificationAdapter, _mapping_digest；保存调用=False
- `src/aitest/infrastructure/adapters/model.py` (326 行)：模型响应和错误归一，不重试或决定通过。；定义：ModelAdapterError, ModelCredentialResolver, HttpResponse, HttpTransport, UrllibTransport, _joined_prompt, HttpModelProvider；保存调用=False
- `src/aitest/infrastructure/adapters/source_control.py` (367 行)：本地 Git 与可选 GitHub 只读信息，plain 不调用。；定义：GitUnavailable, GitCommandFailed, _run_git, _is_not_a_repository, _diff_identity, GitHubRef, GitSourceControl, GitHubReadOnlyClient；保存调用=False
- `src/aitest/infrastructure/adapters/source_snapshot.py` (496 行)：读取、固定与物化历史字节，拒绝路径逃逸。；定义：SnapshotError, _hash_file, _materialized_mapping_digest, _as_digest, _fsync_file, _fsync_directory, _try_fsync_directory, _is_excluded, _is_safe_relative, _has_link_ancestor, _files, _exclusion_rules, _selected_paths, FileSourceSnapshotStore；保存调用=False

## infrastructure/file_store

- `src/aitest/infrastructure/file_store/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/infrastructure/file_store/atomic.py` (87 行)：Atomic JSON publication with bounded transient filesystem retries.；定义：is_transient, configured_attempts, replace_with_retry, write_json；保存调用=False
- `src/aitest/infrastructure/file_store/backup.py` (382 行)：Closure backup and verification; no business material deletion.；定义：BackupError, RestoreReport, _contained_path, _chain_has_symlink, _read_manifest, FileBackupStore；保存调用=False
- `src/aitest/infrastructure/file_store/business_changes.py` (335 行)：Sparse, immutable business changes published only through CommitManifest.；定义：compare, BusinessChangeIndex；保存调用=False
- `src/aitest/infrastructure/file_store/canonical_manifest.py` (328 行)：Version 2 manifest material, preserving version 1 historical bytes.；定义：validate_complete, _validate_ref, source_files, required_files, file_reference, complete_manifest, verify_complete；保存调用=False
- `src/aitest/infrastructure/file_store/checkpoints.py` (89 行)：File-backed RecoveryCheckpoint records.；定义：_safe_component, FileCheckpointStore；保存调用=False
- `src/aitest/infrastructure/file_store/commit_manifest.py` (603 行)：Immutable commit material and one compare-and-publish pointer.；定义：CommitMaterialError, canonical_bytes, _hex, _text, _integer, _compare, FileCommitStore；保存调用=False
- `src/aitest/infrastructure/file_store/commit_migration.py` (209 行)：Verified, backed-up transition to a shared record/index/event boundary.；定义：migrate_commit_closure；保存调用=False
- `src/aitest/infrastructure/file_store/continuations.py` (159 行)：Bounded active projection published inside the existing authority root.；定义：ContinuationCapacityExceeded, active_catalog, update_catalog, verify_catalog_record, verify_dependencies；保存调用=False
- `src/aitest/infrastructure/file_store/core_exit.py` (84 行)：Explicit exit admission: sidecars cannot hide authoritative unknown execution.；定义：FileCoreExitGuard；保存调用=False
- `src/aitest/infrastructure/file_store/core_launch.py` (289 行)：Finite startup facts and a launch mutex; no business execution authority.；定义：ProcessFact, valid_instance_filename, python_launch_program, probe_process, FileCoreLaunchStore；保存调用=False
- `src/aitest/infrastructure/file_store/events.py` (759 行)：提交边界、快照游标、事件续读与重复事件去重。；定义：EventMaintenanceRequired, BoundaryHandle, EventReadResult, ReconcileReport, encode_cursor, decode_cursor, derive_event_id, FileEventJournal；保存调用=False
- `src/aitest/infrastructure/file_store/execution_handles.py` (285 行)：Durable command execution handles for cross-core reattachment.；定义：PersistedExecutionHandle, FileExecutionHandleStore, _handle_id, _text, _strict_result, _json_scalar, _read_json；保存调用=False
- `src/aitest/infrastructure/file_store/fake.py` (91 行)：In-memory A substrate for parallel B/C/D development only.；定义：MemoryStore；保存调用=False
- `src/aitest/infrastructure/file_store/index.py` (1462 行)：Finite, read-only QuerySpec execution over sharded sorted index directories.；定义：re_root_id, IndexQueryResult, IndexMissing, build_index_row, _condition_binding, _query_id, DecodedCursor, encode_cursor, decode_cursor, _Min, _Max, _key_cmp, _key_lt, _key_le, _key_gt, _key_ge, _prefix_equals, _ShardDirectory, _issue_mask, _row_int, _QueryPlan, FileQueryIndex, migrate_query_layout, rollback_query_layout；保存调用=False
- `src/aitest/infrastructure/file_store/integrity.py` (424 行)：Read-only integrity inspection for the committed workspace boundary.；定义：_iter_digest_refs, _rel, _check_whitelist, _verify_spool_manifest, _check_permanent_record, _verify_snapshot_manifests, check_workspace；保存调用=False
- `src/aitest/infrastructure/file_store/locking.py` (181 行)：OS writer locks: per-transaction locks and the process-lifetime writer lock.；定义：_Admission, _normalize, admitted_writer_epoch, _open_os_lock, writer_lock, LifetimeWriterLock；保存调用=False
- `src/aitest/infrastructure/file_store/maintenance.py` (519 行)：完整性、空间诊断与受控临时回收，无业务删除。；定义：MaintenanceError, ReclaimCandidate, SpaceReport, ReclaimReport, detect_activity_blocker, _pid_is_dead, _is_empty_staging, FileMaintenanceService；保存调用=False
- `src/aitest/infrastructure/file_store/manifest_migration.py` (119 行)：Backed-up canonical manifest migration with a frozen, resumable index build.；定义：migrate_canonical_manifest；保存调用=False
- `src/aitest/infrastructure/file_store/material_json.py` (22 行)：Strict JSON and bounded bytes for immutable tree materials.；定义：decode_material, read_material_bytes；保存调用=False
- `src/aitest/infrastructure/file_store/migrations.py` (643 行)：备份及活动守卫后的 inspect/plan/apply/resume/rollback。；定义：MigrationError, Migration, PlanResult, ApplyReport, RollbackReport, _load_json, _write_json, _ensure_workspace_schema_version, _step_added_keys, _remove_workspace_schema_version, _ensure_records_intents, _remove_records_intents, _shard_records, _unshard_records, _restore_query_layout, _upgrade_current_pointer, _plan_token, FileMigrationManager；保存调用=False
- `src/aitest/infrastructure/file_store/model_responses.py` (187 行)：Safe immutable model receipt material; never a replacement business authority.；定义：_canonical, _unique, FileModelResponseStore；保存调用=False
- `src/aitest/infrastructure/file_store/objects.py` (120 行)：Content-addressed project object storage.；定义：_safe_component, _reject_links, FileObjectStore；保存调用=False
- `src/aitest/infrastructure/file_store/ordered_events.py` (196 行)：Bounded event pages prepared before the shared commit pointer is switched.；定义：_compare, OrderedEventStore；保存调用=False
- `src/aitest/infrastructure/file_store/ordered_index.py` (325 行)：Immutable ordered pages; ordinary reads/writes visit only relevant tree paths.；定义：OrderedIndexTree；保存调用=False
- `src/aitest/infrastructure/file_store/publication_backend.py` (361 行)：Checked file publication and retained, hashed pointer recovery material.；定义：PublicationUncertainError, _digest, _canonical, _reject_links, _safe, _read, _decode, WindowsFileAPI, FilePublicationBackend；保存调用=False
- `src/aitest/infrastructure/file_store/records.py` (1201 行)：File-backed immutable record repository used by the A substrate.；定义：RecordQueryResult, FileRecordRepository；保存调用=False
- `src/aitest/infrastructure/file_store/recovery.py` (443 行)：Conservative recovery; never replays unknown external effects.；定义：_read_marker, RecoveryBlocked, RecoveryState, recover_workspace, RecoveryOrchestrator, seal_inflight_outputs；保存调用=False
- `src/aitest/infrastructure/file_store/references.py` (97 行)：Verify only explicit permanent object references, never inline content hashes.；定义：verify_record_objects, _add_reference；保存调用=False
- `src/aitest/infrastructure/file_store/sharded_records.py` (449 行)：Immutable, content addressed authority with bounded identity lookup.；定义：_key, exact_counter, AuthorityTree, ShardedRows, ShardedIdentities, ShardedKinds, ShardedIntents, ShardedCommits, open_authority, authority_header, migrate_to_shards, materialize_legacy, rollback_shards, find_commit；保存调用=False
- `src/aitest/infrastructure/file_store/source_material.py` (557 行)：Read-only checks of immutable source metadata and its actual pinned bytes.；定义：SourceMaterialError, _PendingSourceRows, _PendingSourceLookup, prospective_source_authority, reject_links, metadata_path, _relative, _unique, read_metadata, verify_pinned_material, verify_source_reference, source_record_files, _body_digest, _model_basis_files, _owned_record；保存调用=False
- `src/aitest/infrastructure/file_store/spool.py` (824 行)：Persist verified command output streams in the workspace spool.；定义：_safe_component, _require_str, _require_int, _require_bool, _require_list, _require_optional_str, _FileSpoolStreamWriter, FileSpoolStore；保存调用=False
- `src/aitest/infrastructure/file_store/unit_of_work.py` (366 行)：Short file-backed unit of work with immutable revisions and idempotency.；定义：_has_content_fingerprint, FileUnitOfWork；保存调用=False
- `src/aitest/infrastructure/file_store/workspace.py` (119 行)：Workspace identity and admission.；定义：Workspace；保存调用=False

## interfaces

- `src/aitest/interfaces/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/interfaces/dto.py` (310 行)：Stable DTO projections for review, reports, and issues.；定义：coverage_dto, ReviewGapDTO, DecisiveFailureDTO, CoverageSummaryDTO, _coverage_summary_dto, DecisionDTO, decision_dto, ReportSummaryDTO, report_summary_dto, IssueListCursorDTO, IssueListRowDTO, issue_list_row_dto, IssueListPageDTO, issue_list_page_dto, IssueSummaryDTO, issue_summary_dto；保存调用=False

## interfaces/local

- `src/aitest/interfaces/local/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/interfaces/local/actor_context.py` (26 行)：Request-scoped core actor; command parameters never populate this context.；定义：CoreActorContext；保存调用=False
- `src/aitest/interfaces/local/api.py` (590 行)：Local protocol adapter: routing, lifecycle guards and safe projections.；定义：Session, _TransactionOwnership, LocalAPI；保存调用=False
- `src/aitest/interfaces/local/b_registration.py` (73 行)：把 B 的用例注册表交给统一入口。；定义：b_registration_for, register_b_use_cases；保存调用=False
- `src/aitest/interfaces/local/core_client.py` (60 行)：CLI/relay facade: one verified endpoint, exact receipts, no automatic retransmission.；定义：CoreResultUnverified, CoreClient, read_command；保存调用=False
- `src/aitest/interfaces/local/core_worker.py` (507 行)：长生命周期唯一核心子进程入口，由 :class:`SystemProcessLauncher` 启动。；定义：ShutdownControl, shutdown_frame, serialize_response, _safe_request_id, _protocol_error, dispatch_frame, classify_entry_kind, prepare_core_shutdown, serve_connection, watch_parent, ShutdownCoordinator, main；保存调用=False
- `src/aitest/interfaces/local/editor_host.py` (134 行)：发现或连接唯一核心，不能连接则 WORKSPACE_IN_USE。；定义：CoreLauncher, CoreStartupObservation, CoreEndpoint, EditorHost, _wait_seconds, _reject_connection；保存调用=False
- `src/aitest/interfaces/local/pipe.py` (765 行)：Windows 当前用户及会话命名管道、对端及实例校验；不监听 TCP。；定义：_Overlapped, PipeUnavailable, PeerRejected, check_peer_identity, validate_workspace_id, process_exists, _Kernel, _TokenUser, _PipeIO, NamedPipeServer, NamedPipeClient, _shared_kernel, current_session_id, _query_process_user_sid, query_process_image_basename, current_user_sid；保存调用=False

## interfaces/tools

- `src/aitest/interfaces/tools/__init__.py` (0 行)：；定义：；保存调用=False
- `src/aitest/interfaces/tools/agent_relay.py` (454 行)：Bounded MCP commands forward original business intents to the same verified core.；定义：_RpcError, McpRelay, run；保存调用=False
- `src/aitest/interfaces/tools/cli.py` (148 行)：Offline resources or bounded JSON forwarding to an existing verified workspace core.；定义：main；保存调用=False

## scripts

- `scripts/check_docs_facts.py` (231 行)：Report current-status docs whose numeric claims disagree with the code.；定义：Rule, _number, module_count, line_count, mypy_file_count, node_count, dispatchable_action_count, _environment, facts, report；保存调用=False
- `scripts/check_versions.py` (38 行)：Reject inconsistent core/panel/extension versions and mismatched release tags.；定义：version；保存调用=False
- `scripts/generate_cd001_fixtures.py` (683 行)：Generate CD-001 ExecutionFacts fixtures through the production assembler.；定义：_sha, _run, _step, _handle, _outputs, _attempt, _source_check, _source_verification, _verification, _assembly, _gap, _unknown, _success_like, _build, main；保存调用=False
- `scripts/generate_prepared_run_fixtures.py` (23 行)：Generate functional PreparedRun fixtures from their business scenario factory.；定义：main；保存调用=False
- `scripts/generate_schemas.py` (25 行)：Generate schemas from Python contracts; do not hand-edit generated files.；定义：main；保存调用=False
- `scripts/smoke_wheel.py` (50 行)：Smoke-test the built wheel outside the checkout and editable environment.；定义：main；保存调用=False
- `scripts/validate_deepseek.py` (304 行)：Live model component check; does not certify Trae or business acceptance.；定义：CountedTransport, ValidationBlocked, _text, _revision, _content, dispatch, _saved_policy, run；保存调用=False
- `scripts/verify_abc_phase_evidence.py` (87 行)：Verify frozen source/log evidence, optionally against the current checkout.；定义：digest, relative_file, symbol_lines, verify, main；保存调用=False
- `scripts/verify_artifacts.py` (42 行)：Verify deliverables contain shared generated resources, not old services.；定义：main；保存调用=False
