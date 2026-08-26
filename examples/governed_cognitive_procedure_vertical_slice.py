from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import product
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Engine

from super_scientist.application.cognitive import (
    CognitiveOrchestrationService,
    ResearchCoordinator,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.workspace_exchange import export_workspace, import_workspace
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.cognition import (
    CapabilityAssertion,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CapabilityRequirement,
    CohortPlanReceiptRef,
    CohortRequest,
    DiversityAxisStatus,
    DiversityFingerprint,
    assess_capability,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.collaboration import (
    CollaborationBudget,
    CollaborationCompletionPredicate,
    CollaborationSession,
    PeerContribution,
    PeerRequest,
    PeerRoleAssignment,
    TopologyEvent,
    TopologyOperation,
    TopologySnapshot,
    advance_collaboration,
    apply_topology_event,
    evaluate_termination,
    initial_collaboration_state,
    next_peer,
)
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.harness_eval import (
    AvailableValue,
    EvaluationBudget,
    EvaluationMetricVector,
    EvaluationReferenceComponent,
    EvidenceReceipt,
    FeedbackMode,
    GuidanceCondition,
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
    HarnessEvidenceSnapshotIndex,
    HarnessIdentity,
    HarnessPartition,
    MetadataAvailability,
    MetricMissingReason,
    ModelBudgetBinding,
    ModelHarnessCell,
    ModelHarnessComparisonKind,
    ModelHarnessCoordinate,
    ModelHarnessProtocol,
    ModelIdentity,
    ObservableArtifactRef,
    ReferenceMissingness,
    ResolvedEvidenceInventory,
    ResolvedEvidenceKind,
    ResolvedEvidenceRecord,
    ResolvedRewardHackingDiagnostic,
    ResolvedVerificationResultSnapshot,
    RewardHackingCoverageAttestation,
    RewardHackingFamily,
    RewardHackingFinding,
    RewardHackingFindingStatus,
    RewardObservation,
    RewardValidityAssessment,
    RewardValidityStatus,
    TraceBinding,
    TraceExpectation,
    TraceExpectationResolutionAttestation,
    VerificationOutcomeEvidence,
    VerificationOutcomeStatus,
    analyze_model_harness,
    assess_reward_validity,
    evaluation_resource_envelope_hash,
    harness_cell_evidence_chain_receipt,
    project_harness_evidence_snapshots,
    reward_hacking_diagnostic_status_snapshot_hash,
    valid_reward_evidence,
    verification_result_status_snapshot_hash,
)
from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessCellEvidenceChain,
    HarnessEvidenceSnapshotRecord,
)
from super_scientist.domain.harness_eval.traces import (
    CaptureRewardValidityStatus,
    EnvironmentEvent,
    EnvironmentEventKind,
    ExecutionStatus,
    GenerationMetadata,
    GenerationStopReason,
    HarnessExecutionTrace,
    ToolObservation,
    ToolObservationStatus,
    artifact_collection_hash,
    trace_expectation_snapshot_hash,
    trace_freshness,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    ResourceBudget,
    ResourceUsage,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.procedures import (
    AcceptedSourceReceiptRef,
    ArtifactCatalogEntry,
    CandidateMethod,
    CatalogFactStatus,
    CompiledProgressPlanBinding,
    DeclaredProcedureArtifact,
    GroundedCapabilityAssessment,
    MethodDirectionStatus,
    OpaqueProcedureCompilationEnvelope,
    ProcedureAuthority,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureCompilationRequest,
    ProcedureEvidenceSourceKind,
    ProcedureOperation,
    ProcedureStep,
    ProcedureTerminalOutcome,
    ProcedureValidationStatus,
    ProgressBudgetCategory,
    RecoveryDirective,
    RegisteredTool,
    RegisteredValidator,
    canonical_model_hash,
    compile_method,
    procedure_to_progress_plan,
)
from super_scientist.domain.progress.models import BudgetReserves
from super_scientist.domain.research_runs.models import ResearchRun, RunBudgetAllocation
from super_scientist.kernel.audit.models import json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    Approval,
    BindCompiledProgressPlan,
    CreateResearchRun,
    HarnessExecutionTraceEnvelope,
    HarnessTraceRecordMetadata,
    Proposal,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RecordDiversityAssessment,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordProcedureCompilation,
    RecordRewardAssessment,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.procedure_sources import (
    ProcedureSourceBinding,
    ProcedureSourceSnapshot,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
BASE_EVIDENCE = b"deterministic-offline-evidence-v1"
BASE_HASH = sha256_hex(BASE_EVIDENCE)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class LocalRuntime:
    engine: Engine
    uow_factory: Callable[[], DatabaseUnitOfWork]
    coordinator: TransactionCoordinator
    artifact_store: FileArtifactStore
    policy: PolicySnapshot
    reviewer: ActorIdentity


@dataclass(frozen=True)
class EvaluationFixture:
    chain: HarnessCellEvidenceChain
    record: HarnessEvidenceSnapshotRecord
    trace: HarnessExecutionTrace
    assessment: RewardValidityAssessment
    findings: tuple[RewardHackingFinding, ...]
    evidence: dict[str, bytes]


def fixed_policy() -> PolicySnapshot:
    policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
        ),
    )
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def create_local_runtime(workspace_root: Path, policy: PolicySnapshot) -> LocalRuntime:
    workspace_root.mkdir(parents=True, exist_ok=False)
    database_url = f"sqlite:///{(workspace_root / 'scientist-harness.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(workspace_root / "artifacts")
    reviewer = ActorIdentity(
        actor_id="offline-reviewer",
        kind=ActorKind.HUMAN,
        created_at=NOW,
    )

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(policy, NOW)
    return LocalRuntime(
        engine=engine,
        uow_factory=uow_factory,
        coordinator=TransactionCoordinator(uow_factory, policy, FixedClock(), artifacts),
        artifact_store=artifacts,
        policy=policy,
        reviewer=reviewer,
    )


def _service_actor() -> ActorIdentity:
    return ActorIdentity(
        actor_id="offline-orchestrator",
        kind=ActorKind.SERVICE,
        created_at=NOW,
    )


def _approval(runtime: LocalRuntime) -> Approval:
    return Approval(approver=runtime.reviewer, approved_at=NOW)


def _run_slice(
    research: ResearchCoordinator,
    submitter: CognitiveOrchestrationService,
    runtime: LocalRuntime,
    proposals: tuple[Proposal, ...],
    *,
    allow_last_rejection: bool = False,
) -> tuple[TransactionDecision, ...]:
    decisions = research.run_declared_slice(submitter, runtime.coordinator, proposals)
    expected_count = len(proposals)
    if allow_last_rejection:
        if len(decisions) != expected_count or any(not item.accepted for item in decisions[:-1]):
            raise RuntimeError("declared slice stopped before its final expected rejection")
    elif len(decisions) != expected_count or any(not item.accepted for item in decisions):
        raise RuntimeError("declared slice was not accepted exactly")
    return decisions


def _accepted_binding(runtime: LocalRuntime, proposal_id: str) -> tuple[str, str, str]:
    with runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        transaction = repositories.transactions.get_by_proposal_id(proposal_id)
        events = tuple(
            event
            for event in repositories.audit.list_all()
            if (
                type(json_compatible_payload(event.payload).get("proposal")) is dict
                and json_compatible_payload(event.payload)["proposal"].get("proposal_id")
                == proposal_id
            )
        )
    if transaction is None or len(events) != 1:
        raise RuntimeError("accepted proposal binding is unavailable")
    return transaction.proposal_hash, events[0].event_id, events[0].event_hash


def _profile_receipt(runtime: LocalRuntime, proposal_id: str) -> CapabilityProfileReceiptRef:
    proposal_hash, audit_event_id, audit_event_hash = _accepted_binding(runtime, proposal_id)
    return CapabilityProfileReceiptRef(
        proposal_id=proposal_id,
        proposal_hash=proposal_hash,
        audit_event_id=audit_event_id,
        audit_event_hash=audit_event_hash,
    )


def _cohort_receipt(runtime: LocalRuntime, proposal_id: str) -> CohortPlanReceiptRef:
    proposal_hash, audit_event_id, audit_event_hash = _accepted_binding(runtime, proposal_id)
    return CohortPlanReceiptRef(
        proposal_id=proposal_id,
        proposal_hash=proposal_hash,
        audit_event_id=audit_event_id,
        audit_event_hash=audit_event_hash,
    )


def _model_actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL,
        created_at=NOW,
        provider_id="offline-provider",
        model_id="offline-model",
        adapter_id="offline-adapter",
        configuration_hash=BASE_HASH,
    )


def _capability_profile(
    policy: PolicySnapshot,
    actor_id: str,
    status: CapabilityEvidenceStatus,
    prompt_strategy: str,
) -> CapabilityProfile:
    verified = status is CapabilityEvidenceStatus.VERIFIED
    assertions = (
        ()
        if status is CapabilityEvidenceStatus.UNKNOWN
        else (
            CapabilityAssertion(
                assertion_id=f"analysis-{actor_id}",
                capability_id="analysis",
                task_family_id="offline-research",
                status=status,
                evidence_ids=(f"capability-evidence-{actor_id}",),
                validator_id="offline-validator" if verified else None,
                validator_version="v1" if verified else None,
                evidence_snapshot_hash=BASE_HASH,
            ),
        )
    )
    return CapabilityProfile.build(
        profile_id=f"profile-{actor_id}",
        actor=_model_actor(actor_id),
        diversity_fingerprint=DiversityFingerprint(
            fingerprint_id=f"fingerprint-{actor_id}",
            model_family="offline-model-family",
            model_version="v1",
            scale_class="small",
            provider="offline-provider",
            adapter_hash=BASE_HASH,
            configuration_hash=BASE_HASH,
            prompt_strategy=prompt_strategy,
            methodological_prior="falsification",
            tools=(),
            evidence_partitions=("public",),
            modalities=("text",),
            previous_error_clusters=(),
            prior_task_specializations=("offline-research",),
            assigned_role="analyst",
            procedure_family="deductive",
        ),
        modalities=("text",),
        execution_constraints=("offline-only",),
        assertions=assertions,
        governing_policy_hash=policy.policy_hash,
    )


def _record_cognition_and_collaboration(
    runtime: LocalRuntime,
    research: ResearchCoordinator,
    submitter: CognitiveOrchestrationService,
) -> tuple[list[dict[str, str]], dict[str, object], dict[str, object]]:
    actor = _service_actor()
    approval = _approval(runtime)
    requirement = CapabilityRequirement(
        requirement_id="offline-analysis-requirement",
        capability_id="analysis",
        task_family_id="offline-research",
        evidence_snapshot_hash=BASE_HASH,
        required_modalities=("text",),
        required_execution_constraints=("offline-only",),
    )
    profiles = (
        _capability_profile(
            runtime.policy,
            "peer-critique",
            CapabilityEvidenceStatus.VERIFIED,
            "critique-first",
        ),
        _capability_profile(
            runtime.policy,
            "peer-direct",
            CapabilityEvidenceStatus.VERIFIED,
            "direct",
        ),
        _capability_profile(
            runtime.policy,
            "peer-self-report",
            CapabilityEvidenceStatus.SELF_REPORTED,
            "self-report",
        ),
        _capability_profile(
            runtime.policy,
            "peer-unknown",
            CapabilityEvidenceStatus.UNKNOWN,
            "unknown",
        ),
    )
    profile_proposals = tuple(
        RecordCapabilityProfile(
            proposal_id=f"record-{profile.profile_id}",
            idempotency_key=f"record-{profile.profile_id}",
            proposer=actor,
            approval=approval,
            profile=profile,
        )
        for profile in profiles
    )
    _run_slice(research, submitter, runtime, profile_proposals)
    receipts = tuple(
        _profile_receipt(runtime, proposal.proposal_id) for proposal in profile_proposals
    )
    request = CohortRequest.build(
        request_id="offline-cohort-request",
        task_id="offline-task",
        min_members=2,
        max_members=2,
        candidate_actor_ids=tuple(profile.actor_id for profile in profiles),
        required_capabilities=(requirement,),
        preferred_capabilities=(),
        prohibited_combinations=(),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    cohort = build_cohort(request, profiles)
    cohort_proposal = RecordCohortPlan(
        proposal_id="record-offline-cohort",
        idempotency_key="record-offline-cohort",
        proposer=actor,
        approval=approval,
        request=request,
        profile_receipts=receipts,
        plan=cohort,
    )
    _run_slice(research, submitter, runtime, (cohort_proposal,))
    selected_actor_ids = tuple(member.actor_id for member in cohort.members)
    selected = tuple(profile for profile in profiles if profile.actor_id in selected_actor_ids)
    selected_receipts = tuple(
        receipt
        for profile, receipt in zip(profiles, receipts, strict=True)
        if profile.actor_id in selected_actor_ids
    )
    diversity = assess_diversity(cohort, selected, ())
    _run_slice(
        research,
        submitter,
        runtime,
        (
            RecordDiversityAssessment(
                proposal_id="record-offline-diversity",
                idempotency_key="record-offline-diversity",
                proposer=actor,
                approval=approval,
                cohort_plan_receipt=_cohort_receipt(runtime, cohort_proposal.proposal_id),
                profile_receipts=selected_receipts,
                error_correlations=(),
                assessment=diversity,
            ),
        ),
    )
    capability_summary = [
        {
            "actor_id": profile.actor_id,
            "disposition": assess_capability(profile, requirement).disposition.value,
            "evidence_status": assess_capability(profile, requirement).evidence_status.value,
        }
        for profile in (profiles[0], profiles[2], profiles[3])
    ]
    diversity_summary = {
        "independent": diversity.axes.model_family is DiversityAxisStatus.DIFFERENT,
        "member_ids": list(diversity.member_actor_ids),
        "model_family": selected[0].diversity_fingerprint.model_family,
        "prompt_strategies": sorted(
            profile.diversity_fingerprint.prompt_strategy for profile in selected
        ),
    }

    collaboration_artifact = runtime.artifact_store.put(
        b"bounded offline collaboration input",
        "application/json",
    )
    edges = tuple(
        (left.actor_id, right.actor_id)
        for left in selected
        for right in selected
        if left.actor_id != right.actor_id
    )
    resource_budget = ResourceBudget(
        cost_usd=5.0,
        compute_units=5.0,
        tokens=500,
        elapsed_seconds=60.0,
        tool_calls=5,
        human_interventions=0,
    )
    session = CollaborationSession.build(
        session_id="offline-collaboration",
        task_id=request.task_id,
        cohort_plan=cohort,
        peers=tuple(profile.actor for profile in selected),
        role_assignments=tuple(
            PeerRoleAssignment(peer_id=profile.actor_id, role_id="challenger")
            for profile in selected
        ),
        tools=(),
        allowed_artifacts=(collaboration_artifact,),
        budget=CollaborationBudget(
            max_peers=2,
            max_hops=2,
            max_contributions=2,
            max_contributions_per_peer=1,
            max_topology_changes=2,
            max_parent_depth=1,
            max_state_repetitions=1,
            max_topology_churn=2,
            max_peer_contribution_share=1.0,
            resources=resource_budget,
            allowed_tool_ids=(),
        ),
        allowed_contribution_kinds=("challenge",),
        declared_edges=edges,
        initial_active_peer_ids=tuple(profile.actor_id for profile in selected),
        scheduling_policy_version="lexicographic-v1",
        topology_policy_version="declared-edge-v1",
        completion_predicate=CollaborationCompletionPredicate(
            min_contributions=1,
            required_contribution_kind="challenge",
        ),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    session_proposal = RecordCollaborationSession(
        proposal_id="record-offline-collaboration",
        idempotency_key="record-offline-collaboration",
        proposer=actor,
        approval=approval,
        session=session,
    )
    _run_slice(research, submitter, runtime, (session_proposal,))
    state = initial_collaboration_state(session)
    disabled_edge = edges[0]
    after_topology = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=tuple(edge for edge in edges if edge != disabled_edge),
    )
    topology_event = TopologyEvent.build(
        event_id="offline-topology-update",
        session_id=session.session_id,
        sequence=1,
        before_topology_hash=state.topology.content_hash,
        operation=TopologyOperation.DISABLE_EDGE,
        peer_id=None,
        edge=disabled_edge,
        reason_code="BOUNDED_CHALLENGE",
        after_topology_hash=after_topology.content_hash,
    )
    state = apply_topology_event(session, state, topology_event)
    recipient = next_peer(session, state)
    if recipient is None:
        raise RuntimeError("bounded collaboration has no eligible peer")
    challenge = PeerRequest.build(
        request_id="challenge-request",
        session_id=session.session_id,
        sequence=1,
        sender_id=None,
        recipient_id=recipient,
        requested_capability_id="analysis",
        question="Challenge the claimed result using only retained public evidence.",
        artifact_refs=(collaboration_artifact,),
        parent_contribution_id=None,
        tool_ids=(),
        remaining_budget=resource_budget,
    )
    contribution = PeerContribution.build(
        contribution_id="challenge-contribution",
        session_id=session.session_id,
        request_id=challenge.request_id,
        peer_id=recipient,
        parent_contribution_ids=(),
        contribution_kind="challenge",
        rationale_summary="The retained evidence supports a bounded counter-check.",
        candidate_content='{"finding":"supported"}',
        artifact_refs=(collaboration_artifact,),
        tool_ids=(),
    )
    usage = ResourceUsage(
        cost_usd=0.1,
        compute_units=0.1,
        tokens=25,
        elapsed_seconds=1.0,
        tool_calls=0,
        human_interventions=0,
    )
    state = advance_collaboration(session, state, challenge, contribution, usage)
    collaboration_proposals: tuple[Proposal, ...] = (
        AppendTopologyEvent(
            proposal_id="append-offline-topology",
            idempotency_key="append-offline-topology",
            proposer=actor,
            approval=approval,
            event=topology_event,
        ),
        AppendPeerRequest(
            proposal_id="append-offline-challenge",
            idempotency_key="append-offline-challenge",
            proposer=actor,
            approval=approval,
            request=challenge,
        ),
        AppendPeerContribution(
            proposal_id="append-offline-contribution",
            idempotency_key="append-offline-contribution",
            proposer=actor,
            approval=approval,
            contribution=contribution,
            usage=usage,
        ),
        RecordCollaborationTermination(
            proposal_id="record-offline-termination",
            idempotency_key="record-offline-termination",
            proposer=actor,
            approval=approval,
            session_id=session.session_id,
            termination=evaluate_termination(state),
        ),
    )
    _run_slice(research, submitter, runtime, collaboration_proposals)
    challenge_summary = {
        "bounded": (
            challenge.remaining_budget == session.budget.resources
            and len(challenge.question.encode("utf-8")) <= 512
        ),
        "question_bytes": len(challenge.question.encode("utf-8")),
        "request_id": challenge.request_id,
    }
    return (
        capability_summary,
        diversity_summary,
        {
            "challenge": challenge_summary,
            "session_id": session.session_id,
            "topology_event_id": topology_event.event_id,
            "topology_operation": topology_event.operation.value,
        },
    )


class EvidenceRetainer:
    def __init__(
        self,
        runtime: LocalRuntime,
        research: ResearchCoordinator,
        submitter: CognitiveOrchestrationService,
    ) -> None:
        self._runtime = runtime
        self._research = research
        self._submitter = submitter
        self._retained: dict[str, bytes] = {}

    def retain(
        self,
        evidence: dict[str, bytes],
        *,
        evidence_type: str = "offline-evaluation",
    ) -> tuple[AddEvidence, ...]:
        proposals: list[AddEvidence] = []
        for record_id, data in sorted(evidence.items()):
            prior = self._retained.get(record_id)
            if prior is not None:
                if prior != data:
                    raise RuntimeError("one evidence identifier resolved to different content")
                continue
            artifact = self._runtime.artifact_store.put(data, "application/json")
            digest = sha256_hex(record_id.encode("utf-8"))[:32]
            proposals.append(
                AddEvidence(
                    proposal_id=f"retain-{digest}",
                    idempotency_key=f"retain-{digest}",
                    proposer=_service_actor(),
                    evidence=EvidenceRecord(
                        evidence_id=record_id,
                        evidence_type=evidence_type,
                        source_locator=f"offline:{record_id}",
                        retrieved_at=NOW,
                        artifact=artifact,
                        provenance={"fixture": "governed-cognitive-procedure-v1"},
                        ingestion_actor_id="offline-orchestrator",
                    ),
                )
            )
            self._retained[record_id] = data
        if proposals:
            _run_slice(
                self._research,
                self._submitter,
                self._runtime,
                tuple(proposals),
            )
        return tuple(proposals)


def _resource_budget(value: int = 10) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=float(value),
        compute_units=float(value),
        tokens=value,
        elapsed_seconds=float(value),
        tool_calls=value,
        human_interventions=value,
    )


def _procedure_step(
    step_id: str,
    order: int,
    *,
    inputs: tuple[str, ...],
    output_id: str,
    dependencies: tuple[str, ...],
    operation: ProcedureOperation,
    authority: tuple[ProcedureAuthority, ...],
    tool_ids: tuple[str, ...] = (),
    category: ProgressBudgetCategory = ProgressBudgetCategory.EXPLORATION,
) -> ProcedureStep:
    return ProcedureStep.build(
        step_id=step_id,
        order=order,
        operation=operation,
        objective=f"Complete {step_id}",
        input_artifact_ids=inputs,
        outputs=(
            DeclaredProcedureArtifact.build(
                artifact_id=output_id,
                media_type="application/json",
                integrity_sha256=BASE_HASH,
            ),
        ),
        dependency_ids=dependencies,
        allowed_tool_ids=tool_ids,
        required_authorities=authority,
        preconditions=("Declared inputs are available",),
        completion_criteria=("Structured output matches schema",),
        evidence_requirements=("retained-output",),
        validator=ActorIdentity(
            actor_id="offline-validator",
            kind=ActorKind.HUMAN,
            created_at=NOW,
        ),
        validator_version="v1",
        failure_signals=("validator-rejected",),
        recovery=RecoveryDirective(terminal_outcome=ProcedureTerminalOutcome.ABANDONED),
        capability_requirement_ids=("procedure-capability",),
        progress_budget_category=category,
        resource_budget=_resource_budget(1),
        progress_weight=Decimal("0.50"),
    )


def _procedure_source_receipt(
    runtime: LocalRuntime,
    proposal: AddEvidence | RecordCapabilityProfile,
    *,
    receipt_id: str,
    source_kind: ProcedureEvidenceSourceKind,
    source_record_id: str,
    source_schema_version: int,
    source_content_hash: str,
    source_snapshot_id: str,
    source_snapshot_hash: str,
) -> AcceptedSourceReceiptRef:
    proposal_hash, audit_event_id, audit_event_hash = _accepted_binding(
        runtime,
        proposal.proposal_id,
    )
    return AcceptedSourceReceiptRef.build(
        receipt_id=receipt_id,
        source_kind=source_kind,
        source_record_id=source_record_id,
        source_schema_version=source_schema_version,
        source_content_hash=source_content_hash,
        source_snapshot_id=source_snapshot_id,
        source_snapshot_hash=source_snapshot_hash,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal_hash,
        audit_event_id=audit_event_id,
        audit_event_hash=audit_event_hash,
    )


def _record_procedures_and_binding(
    runtime: LocalRuntime,
    research: ResearchCoordinator,
    submitter: CognitiveOrchestrationService,
    retainer: EvidenceRetainer,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    actor = _service_actor()
    approval = _approval(runtime)
    capability_snapshot = ProcedureSourceSnapshot(
        snapshot_family_id="offline-capability-snapshot",
        snapshot_id="offline-capability-snapshot",
        source_bindings=(),
    )
    capability_snapshot_bytes = canonical_json_bytes(capability_snapshot.model_dump(mode="json"))
    capability_snapshot_proposal = retainer.retain(
        {capability_snapshot.snapshot_id: capability_snapshot_bytes},
        evidence_type="procedure-source",
    )[0]
    capability_snapshot_hash = capability_snapshot_proposal.evidence.content_hash
    requirement = CapabilityRequirement(
        requirement_id="procedure-capability",
        capability_id="procedure-analysis",
        task_family_id="offline-procedure",
        evidence_snapshot_hash=capability_snapshot_hash,
    )
    procedure_profile = CapabilityProfile.build(
        profile_id="offline-procedure-profile",
        actor=ActorIdentity(
            actor_id="offline-procedure-worker",
            kind=ActorKind.HUMAN,
            created_at=NOW,
        ),
        diversity_fingerprint=DiversityFingerprint(
            fingerprint_id="offline-procedure-fingerprint",
            model_family=None,
            model_version=None,
            scale_class=None,
            provider=None,
            adapter_hash=None,
            configuration_hash=None,
            prompt_strategy=None,
            methodological_prior="deductive",
            tools=(),
            evidence_partitions=("public",),
            modalities=("text",),
            previous_error_clusters=(),
            prior_task_specializations=("offline-procedure",),
        ),
        assertions=(
            CapabilityAssertion(
                assertion_id="offline-procedure-assertion",
                capability_id=requirement.capability_id,
                task_family_id=requirement.task_family_id,
                status=CapabilityEvidenceStatus.VERIFIED,
                evidence_ids=("offline-procedure-capability-evidence",),
                validator_id="offline-validator",
                validator_version="v1",
                evidence_snapshot_hash=capability_snapshot_hash,
            ),
        ),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    profile_proposal = RecordCapabilityProfile(
        proposal_id="record-offline-procedure-profile",
        idempotency_key="record-offline-procedure-profile",
        proposer=actor,
        approval=approval,
        profile=procedure_profile,
    )
    _run_slice(research, submitter, runtime, (profile_proposal,))
    grounded = GroundedCapabilityAssessment.build(
        profile=procedure_profile,
        assessment=assess_capability(procedure_profile, requirement),
        profile_receipt=_procedure_source_receipt(
            runtime,
            profile_proposal,
            receipt_id="offline-procedure-profile-receipt",
            source_kind=ProcedureEvidenceSourceKind.CAPABILITY_PROFILE,
            source_record_id=procedure_profile.profile_id,
            source_schema_version=procedure_profile.schema_version,
            source_content_hash=procedure_profile.content_hash,
            source_snapshot_id=capability_snapshot.snapshot_id,
            source_snapshot_hash=capability_snapshot_hash,
        ),
    )

    source_artifact = runtime.artifact_store.put(
        b"declared offline source",
        "application/json",
    )
    artifact_catalog = (
        ArtifactCatalogEntry(
            artifact_id="source",
            artifact=source_artifact,
            availability=CatalogFactStatus.PRESENT,
        ),
    )
    tool_catalog = (
        RegisteredTool(
            tool=ActorIdentity(
                actor_id="offline-tool",
                kind=ActorKind.TOOL,
                created_at=NOW,
            ),
            availability=CatalogFactStatus.PRESENT,
            authorization=CatalogFactStatus.PRESENT,
        ),
    )
    validator_catalog = (
        RegisteredValidator(
            validator=ActorIdentity(
                actor_id="offline-validator",
                kind=ActorKind.HUMAN,
                created_at=NOW,
            ),
            validator_version="v1",
            registration=CatalogFactStatus.PRESENT,
        ),
    )
    catalogs: tuple[tuple[ProcedureEvidenceSourceKind, tuple[BaseModel, ...]], ...] = (
        (ProcedureEvidenceSourceKind.ARTIFACT_CATALOG, artifact_catalog),
        (ProcedureEvidenceSourceKind.TOOL_CATALOG, tool_catalog),
        (ProcedureEvidenceSourceKind.VALIDATOR_CATALOG, validator_catalog),
    )
    source_proposals: list[tuple[ProcedureEvidenceSourceKind, AddEvidence]] = []
    for source_kind, entries in catalogs:
        source_id = f"offline-{source_kind.value.lower()}"
        data = canonical_json_bytes(
            {
                "catalog_kind": source_kind.value,
                "entries": tuple(item.model_dump(mode="json") for item in entries),
                "complete": True,
            }
        )
        source_proposal = retainer.retain(
            {source_id: data},
            evidence_type="procedure-source",
        )[0]
        source_proposals.append((source_kind, source_proposal))
    catalog_snapshot = ProcedureSourceSnapshot(
        snapshot_family_id="offline-catalog-snapshot",
        snapshot_id="offline-catalog-snapshot",
        source_bindings=tuple(
            sorted(
                (
                    ProcedureSourceBinding(
                        source_record_id=proposal.evidence.evidence_id,
                        source_content_hash=proposal.evidence.content_hash,
                    )
                    for _, proposal in source_proposals
                ),
                key=lambda item: item.source_record_id,
            )
        ),
    )
    catalog_snapshot_proposal = retainer.retain(
        {
            catalog_snapshot.snapshot_id: canonical_json_bytes(
                catalog_snapshot.model_dump(mode="json")
            )
        },
        evidence_type="procedure-source",
    )[0]
    catalog_snapshot_hash = catalog_snapshot_proposal.evidence.content_hash
    catalog_receipts = tuple(
        _procedure_source_receipt(
            runtime,
            proposal,
            receipt_id=f"receipt-{source_kind.value.lower()}",
            source_kind=source_kind,
            source_record_id=proposal.evidence.evidence_id,
            source_schema_version=1,
            source_content_hash=proposal.evidence.content_hash,
            source_snapshot_id=catalog_snapshot.snapshot_id,
            source_snapshot_hash=catalog_snapshot_hash,
        )
        for source_kind, proposal in source_proposals
    )
    candidate_evidence = runtime.artifact_store.put(
        b"candidate method evidence",
        "application/json",
    )
    first = _procedure_step(
        "prepare",
        1,
        inputs=("source",),
        output_id="prepared",
        dependencies=(),
        operation=ProcedureOperation.INSPECT_DECLARED_ARTIFACT,
        authority=(ProcedureAuthority.READ_DECLARED_ARTIFACT,),
    )
    second = _procedure_step(
        "validate",
        2,
        inputs=("prepared",),
        output_id="final",
        dependencies=("prepare",),
        operation=ProcedureOperation.EVALUATE_WITH_REGISTERED_VALIDATOR,
        authority=(ProcedureAuthority.RUN_REGISTERED_TOOL,),
        tool_ids=("offline-tool",),
        category=ProgressBudgetCategory.VERIFICATION,
    )
    candidate = CandidateMethod.build(
        method_id="offline-method",
        objective="Produce a validated offline artifact",
        assumptions=("All inputs are retained public fixtures",),
        stages=(first, second),
        evidence_refs=(candidate_evidence,),
        claimed_capability_requirement_ids=(requirement.requirement_id,),
        expected_output_ids=("final",),
        verifier_requirement_ids=("offline-validator:v1",),
        resource_estimate=_resource_budget(2),
        termination_conditions=("The validator accepts or the method is abandoned",),
        provenance_contribution_ids=("challenge-contribution",),
    )
    reserves = BudgetReserves(
        exploration=_resource_budget(),
        implementation=_resource_budget(),
        verification=_resource_budget(),
        recovery=_resource_budget(),
        finalization=_resource_budget(),
    )
    request = ProcedureCompilationRequest(
        request_id="offline-valid-request",
        compiler_id="procedure-compiler",
        compiler_version="1.0.0",
        candidate=candidate,
        capability_assessments=(grounded,),
        artifact_catalog=artifact_catalog,
        artifact_catalog_complete=True,
        artifact_catalog_receipt=catalog_receipts[0],
        tool_catalog=tool_catalog,
        tool_catalog_complete=True,
        tool_catalog_receipt=catalog_receipts[1],
        validator_catalog=validator_catalog,
        validator_catalog_complete=True,
        validator_catalog_receipt=catalog_receipts[2],
        budget_envelope=reserves,
    )
    invalid_candidate_values = candidate.model_dump(mode="python", exclude={"content_hash"})
    invalid_candidate_values["expected_output_ids"] = ("undefined-output",)
    invalid_request_values = request.model_dump(mode="python")
    invalid_request_values.update(
        request_id="offline-invalid-request",
        candidate=CandidateMethod.build(**invalid_candidate_values),
    )
    invalid_request = ProcedureCompilationRequest.model_validate(
        invalid_request_values,
        strict=True,
    )
    invalid_proposal = RecordProcedureCompilation(
        proposal_id="record-offline-invalid-compilation",
        idempotency_key="record-offline-invalid-compilation",
        proposer=actor,
        approval=approval,
        compilation=OpaqueProcedureCompilationEnvelope.build(
            compilation_id="offline-invalid-compilation",
            result=compile_method(invalid_request),
            created_at=NOW,
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )
    valid_proposal = RecordProcedureCompilation(
        proposal_id="record-offline-valid-compilation",
        idempotency_key="record-offline-valid-compilation",
        proposer=actor,
        approval=approval,
        compilation=OpaqueProcedureCompilationEnvelope.build(
            compilation_id="offline-valid-compilation",
            result=compile_method(request),
            created_at=NOW,
            governing_policy_hash=runtime.policy.policy_hash,
        ),
    )
    compilation_decisions = _run_slice(
        research,
        submitter,
        runtime,
        (invalid_proposal, valid_proposal),
    )
    invalid_record = ProcedureCompilationRecord.build_from_untrusted_envelope(
        invalid_proposal.compilation
    )
    valid_record = ProcedureCompilationRecord.build_from_untrusted_envelope(
        valid_proposal.compilation
    )
    if valid_record.result.procedure is None:
        raise RuntimeError("valid fixture did not compile a procedure")
    run = ResearchRun(
        run_id="offline-run",
        charter="Demonstrate governed cognitive procedures offline",
        scope=("Task 17",),
        creator=actor,
        created_at=NOW,
        active_governance_policy_hash=runtime.policy.policy_hash,
        model_configuration_version_id=None,
        scaffold_configuration_version_id=None,
        budget_allocation=RunBudgetAllocation(
            execution=_resource_budget(100),
            search=_resource_budget(100),
            evaluation=_resource_budget(100),
            judging=_resource_budget(100),
            human=_resource_budget(100),
        ),
        final_validator=runtime.reviewer,
        final_validator_version="v1",
        environment_snapshot_id="offline-environment",
    )
    _run_slice(
        research,
        submitter,
        runtime,
        (
            CreateResearchRun(
                proposal_id="create-offline-run",
                idempotency_key="create-offline-run",
                proposer=actor,
                approval=approval,
                run=run,
            ),
        ),
    )
    proposal_hash, audit_event_id, audit_event_hash = _accepted_binding(
        runtime,
        valid_proposal.proposal_id,
    )
    compilation_receipt = ProcedureCompilationReceiptRef(
        proposal_id=valid_proposal.proposal_id,
        proposal_hash=proposal_hash,
        audit_event_id=audit_event_id,
        audit_event_hash=audit_event_hash,
    )
    plan = procedure_to_progress_plan(
        valid_record.result,
        run_id=run.run_id,
        plan_version_id="offline-progress-plan-v1",
        version=1,
        created_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    binding = CompiledProgressPlanBinding.build(
        binding_id="offline-progress-binding",
        compilation_receipt=compilation_receipt,
        compilation_id=valid_record.compilation_id,
        compilation_hash=valid_record.content_hash,
        procedure_id=valid_record.result.procedure.procedure_id,
        procedure_hash=valid_record.result.procedure.content_hash,
        plan=plan,
        plan_hash=canonical_model_hash(plan),
        created_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    binding_decision = _run_slice(
        research,
        submitter,
        runtime,
        (
            BindCompiledProgressPlan(
                proposal_id="bind-offline-progress-plan",
                idempotency_key="bind-offline-progress-plan",
                proposer=actor,
                approval=approval,
                compilation_receipt=compilation_receipt,
                binding=binding,
                plan=plan,
            ),
        ),
    )[0]
    return (
        {
            "accepted": compilation_decisions[0].accepted,
            "compilation_id": invalid_record.compilation_id,
            "finding_codes": [item.code.value for item in invalid_record.result.report.findings],
            "status": invalid_record.result.report.status.value,
        },
        {
            "accepted": compilation_decisions[1].accepted,
            "compilation_id": valid_record.compilation_id,
            "procedure_id": valid_record.result.procedure.procedure_id,
            "status": valid_record.result.report.status.value,
        },
        {
            "accepted": binding_decision.accepted,
            "binding_id": binding.binding_id,
            "compilation_id": binding.compilation_id,
            "plan_hash": binding.plan_hash,
            "plan_id": plan.plan_version_id,
        },
    )


def _evaluation_budget(model: ModelIdentity | None = None) -> EvaluationBudget:
    return EvaluationBudget.model_validate(
        {
            "model_id": "guidance-model" if model is None else model.model_id,
            "model_version": "v1" if model is None else model.model_version,
            "adapter_id": None,
            "feedback_mode": FeedbackMode.NONE,
            "tool_ids": ("offline-evaluator",),
            "attempts": 1,
            "token_limit": 100,
            "reasoning_limit": 50,
            "evaluator_call_limit": 1,
            "wall_clock_seconds": Decimal("5"),
            "cost_limit": Decimal("1"),
            "human_intervention_limit": 0,
        }
    )


def _evaluation_metrics(score: str = "0.8") -> EvaluationMetricVector:
    return EvaluationMetricVector(
        task_score=Decimal(score),
        procedure_compilation_status=ProcedureValidationStatus.VALID,
        procedure_execution_success=True,
        method_selection_result=MethodDirectionStatus.SUPPORTED,
        execution_failure_events=(),
        recovery_attempt_events=(),
        resource_usage=ResourceUsage(
            cost_usd=0.1,
            compute_units=1.0,
            tokens=10,
            elapsed_seconds=1.0,
            tool_calls=1,
            human_interventions=0,
        ),
        final_validation=AssessmentOutcome.PASSED,
        missingness=(),
    )


def _record_guidance_grid(
    runtime: LocalRuntime,
    research: ResearchCoordinator,
    submitter: CognitiveOrchestrationService,
) -> dict[str, object]:
    protocol = GuidanceEvaluationProtocol.build(
        protocol_id="offline-guidance-protocol",
        version=1,
        objective_hash=BASE_HASH,
        task_id="offline-guidance-task",
        task_input_hash=BASE_HASH,
        output_schema_hash=BASE_HASH,
        model_id="guidance-model",
        model_version="v1",
        harness_id="guidance-harness",
        harness_version="v1",
        verifier_id="offline-validator",
        verifier_version="v1",
        checker_id="offline-checker",
        checker_version="v1",
        artifact_ids=("guidance-artifact",),
        declared_distractor_artifact_ids=("guidance-distractor",),
        random_seed=7,
        evaluation_budget=_evaluation_budget(),
    )
    actor = _service_actor()
    approval = _approval(runtime)
    _run_slice(
        research,
        submitter,
        runtime,
        (
            RecordGuidanceEvaluationProtocol(
                proposal_id="record-offline-guidance-protocol",
                idempotency_key="record-offline-guidance-protocol",
                proposer=actor,
                approval=approval,
                protocol=protocol,
            ),
        ),
    )
    missing_references = tuple(
        ReferenceMissingness(
            component=component,
            reason=MetricMissingReason.NOT_OBSERVED,
        )
        for component in EvaluationReferenceComponent
    )
    cells = tuple(
        GuidanceEvaluationCell.build(
            cell_id=f"guidance-{condition.value.lower()}",
            protocol=protocol,
            condition=condition,
            distractor_artifact_ids=(
                protocol.declared_distractor_artifact_ids
                if condition is GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS
                else ()
            ),
            metrics=_evaluation_metrics(),
            output_artifact_id=None,
            trace_id=None,
            verifier_result_id=None,
            reward_assessment_id=None,
            observed_at=NOW,
            reference_missingness=missing_references,
        )
        for condition in GuidanceCondition
    )
    _run_slice(
        research,
        submitter,
        runtime,
        tuple(
            AppendGuidanceEvaluationCell(
                proposal_id=f"append-{cell.cell_id}",
                idempotency_key=f"append-{cell.cell_id}",
                proposer=actor,
                approval=approval,
                cell=cell,
            )
            for cell in cells
        ),
    )
    return {
        "cell_count": len(cells),
        "conditions": [cell.condition.value for cell in cells],
    }


def _receipt(record_id: str, content_hash: str = BASE_HASH) -> EvidenceReceipt:
    return EvidenceReceipt(
        record_id=record_id,
        schema_version=1,
        content_hash=content_hash,
    )


def _resolved_inventory(
    entries: Iterable[tuple[object, ResolvedEvidenceKind]],
    *,
    inventory_id: str,
    resolver_id: str,
) -> ResolvedEvidenceInventory:
    typed_entries = tuple(
        (receipt, kind) for receipt, kind in entries if isinstance(receipt, EvidenceReceipt)
    )
    unique = {(item.record_id, kind): (item, kind) for item, kind in typed_entries}
    records = tuple(
        sorted(
            (
                ResolvedEvidenceRecord.build(
                    schema_version=1,
                    receipt=receipt,
                    kind=kind,
                    snapshot_hash=receipt.content_hash,
                )
                for receipt, kind in unique.values()
            ),
            key=lambda item: (
                item.receipt.record_id,
                item.receipt.schema_version,
                item.receipt.content_hash,
                item.kind.value,
            ),
        )
    )
    return ResolvedEvidenceInventory.build(
        schema_version=1,
        inventory_id=inventory_id,
        resolved_by=_receipt(resolver_id),
        records=records,
    )


def _trace_expectation(
    binding: TraceBinding,
    *,
    suffix: str,
) -> tuple[TraceExpectation, ResolvedEvidenceInventory]:
    values: dict[str, object] = {
        "protocol": _receipt(binding.protocol_id, binding.protocol_hash),
        "task": _receipt(binding.task_id, binding.task_input_hash),
        "model": _receipt(binding.model.model_id, binding.model_hash),
        "harness": _receipt(binding.harness.harness_id, binding.harness_hash),
        "procedure": _receipt(binding.procedure_id, binding.procedure_hash),
        "environment": _receipt(binding.environment_id, binding.environment_hash),
        "context": _receipt(binding.context_id, binding.context_hash),
        "validator": _receipt(binding.validator_id, binding.validator_hash),
        "checker": _receipt(binding.checker_id, binding.checker_hash),
        "artifacts": tuple(
            _receipt(artifact_id, artifact_hash)
            for artifact_id, artifact_hash in zip(
                binding.artifact_ids,
                binding.artifact_hashes,
                strict=True,
            )
        ),
        "output_schema": _receipt(binding.output_schema_id, binding.output_schema_hash),
    }
    snapshot_hash = trace_expectation_snapshot_hash(values)
    resolution = TraceExpectationResolutionAttestation.build(
        attestation_id=f"expectation-resolution-{suffix}",
        expectation_source=_receipt(f"expectation-source-{suffix}", snapshot_hash),
        resolver=_receipt("expectation-resolver"),
        resolved_snapshot_hash=snapshot_hash,
        provenance=(_receipt("expectation-provenance"),),
    )
    values["resolution"] = resolution
    expectation = TraceExpectation.build(**values)
    inventory = _resolved_inventory(
        (
            (resolution.expectation_source, ResolvedEvidenceKind.EXPECTATION_SOURCE),
            (resolution.resolver, ResolvedEvidenceKind.RESOLVER),
            *((item, ResolvedEvidenceKind.PROVENANCE) for item in resolution.provenance),
        ),
        inventory_id=f"expectation-inventory-{suffix}",
        resolver_id="expectation-inventory-resolver",
    )
    return expectation, inventory


def _verification(
    trace: HarnessExecutionTrace,
    *,
    suffix: str,
) -> tuple[
    VerificationOutcomeEvidence,
    tuple[tuple[object, ResolvedEvidenceKind], ...],
]:
    verifier = _receipt(
        trace.observed_binding.validator_id,
        trace.observed_binding.validator_hash,
    )
    checker = _receipt(
        trace.observed_binding.checker_id,
        trace.observed_binding.checker_hash,
    )

    def result_snapshot(
        role: str,
        executor: object,
        result_id: str,
        result_hash: str,
    ) -> ResolvedVerificationResultSnapshot:
        snapshot_id = f"{role}-snapshot"
        values: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "executor": executor,
            "result": _receipt(result_id, result_hash),
            "status": VerificationOutcomeStatus.SUCCEEDED,
            "observable_evidence": (_receipt(f"{role}-evidence"),),
            "resolver": _receipt("verification-resolver"),
        }
        values["source"] = _receipt(
            snapshot_id,
            verification_result_status_snapshot_hash(values),
        )
        return ResolvedVerificationResultSnapshot.build(**values)

    verifier_result = result_snapshot(
        "verifier",
        verifier,
        trace.verifier_result_id,
        trace.verifier_result_hash,
    )
    checker_result = result_snapshot(
        "checker",
        checker,
        trace.checker_result_id,
        trace.checker_result_hash,
    )
    outcome = VerificationOutcomeEvidence.build(
        outcome_id=f"verification-outcome-{suffix}",
        verifier=verifier,
        verifier_result=verifier_result,
        checker=checker,
        checker_result=checker_result,
    )
    entries: list[tuple[object, ResolvedEvidenceKind]] = []
    for snapshot in (verifier_result, checker_result):
        entries.extend(
            (
                (snapshot.source, ResolvedEvidenceKind.VERIFICATION_RESULT_SOURCE),
                (snapshot.result, ResolvedEvidenceKind.VERIFICATION_RESULT),
                (snapshot.resolver, ResolvedEvidenceKind.RESOLVER),
            )
        )
        entries.extend(
            (item, ResolvedEvidenceKind.OBSERVABLE_EVIDENCE)
            for item in snapshot.observable_evidence
        )
    return outcome, tuple(entries)


def _findings_and_coverage(
    trace: HarnessExecutionTrace,
    *,
    suffix: str,
    invalid: bool,
) -> tuple[
    tuple[RewardHackingFinding, ...],
    RewardHackingCoverageAttestation,
    tuple[tuple[object, ResolvedEvidenceKind], ...],
]:
    observation = trace.reward_observation
    if observation is None:
        raise RuntimeError("evaluation fixture requires a reward observation")
    findings = tuple(
        RewardHackingFinding.build(
            finding_id=f"finding-{index:02d}-{suffix}",
            family=family,
            status=(
                RewardHackingFindingStatus.INVALIDATING
                if invalid and index == 0
                else RewardHackingFindingStatus.CLEARED
            ),
            trace_id=trace.trace_id,
            trace_hash=trace.content_hash,
            observation_id=observation.observation_id,
            observation_hash=observation.content_hash,
            evidence_ids=(f"diagnostic-evidence-{index:02d}",),
        )
        for index, family in enumerate(RewardHackingFamily)
    )
    diagnostics = []
    entries: list[tuple[object, ResolvedEvidenceKind]] = []
    for index, finding in enumerate(findings):
        values: dict[str, object] = {
            "family": finding.family,
            "status": finding.status,
            "observable_evidence": tuple(
                _receipt(evidence_id) for evidence_id in finding.evidence_ids
            ),
            "resolver": _receipt("diagnostic-resolver"),
        }
        source_id = (
            f"diagnostic-source-invalid-{index:02d}"
            if invalid and index == 0
            else f"diagnostic-source-{index:02d}"
        )
        values["source"] = _receipt(
            source_id,
            reward_hacking_diagnostic_status_snapshot_hash(values),
        )
        diagnostic = ResolvedRewardHackingDiagnostic.build(**values)
        diagnostics.append(diagnostic)
        entries.extend(
            (
                (diagnostic.source, ResolvedEvidenceKind.DIAGNOSTIC_SOURCE),
                (diagnostic.resolver, ResolvedEvidenceKind.RESOLVER),
            )
        )
        entries.extend(
            (item, ResolvedEvidenceKind.OBSERVABLE_EVIDENCE)
            for item in diagnostic.observable_evidence
        )
    provenance = (_receipt("diagnostic-provenance"),)
    entries.extend((item, ResolvedEvidenceKind.PROVENANCE) for item in provenance)
    coverage = RewardHackingCoverageAttestation.build(
        attestation_id=f"diagnostic-coverage-{suffix}",
        trace=_receipt(trace.trace_id, trace.content_hash),
        observation=_receipt(observation.observation_id, observation.content_hash),
        diagnostics=tuple(diagnostics),
        provenance=provenance,
    )
    return findings, coverage, tuple(entries)


def _canonical_preimage(
    record: BaseModel,
    *,
    exclude: frozenset[str] = frozenset(),
) -> bytes:
    return canonical_json_bytes(
        record.model_dump(
            mode="json",
            exclude={"content_hash", *exclude},
            warnings=False,
        )
    )


def _add_evidence_bytes(
    evidence: dict[str, bytes],
    record_id: str,
    expected_hash: str,
    data: bytes,
) -> None:
    if sha256_hex(data) != expected_hash:
        raise RuntimeError(f"evidence preimage mismatch for {record_id}")
    prior = evidence.setdefault(record_id, data)
    if prior != data:
        raise RuntimeError("one evaluation evidence identifier has multiple preimages")


def _trace_supporting_evidence(trace: HarnessExecutionTrace) -> dict[str, bytes]:
    evidence: dict[str, bytes] = {}
    binding = trace.observed_binding
    hash_bound = (
        (binding.task_id, binding.task_input_hash),
        (binding.model.model_id, binding.model_hash),
        (binding.harness.harness_id, binding.harness_hash),
        (binding.procedure_id, binding.procedure_hash),
        (binding.environment_id, binding.environment_hash),
        (binding.validator_id, binding.validator_hash),
        (binding.checker_id, binding.checker_hash),
        (binding.output_schema_id, binding.output_schema_hash),
        (trace.verifier_result_id, trace.verifier_result_hash),
        (trace.checker_result_id, trace.checker_result_hash),
        *zip(binding.artifact_ids, binding.artifact_hashes, strict=True),
        *((item.artifact_id, item.sha256) for item in trace.output_artifacts),
        *(
            (item.evidence_id, item.response_hash.value)
            for item in trace.tool_observations
            if item.response_hash.value is not None
        ),
    )
    for record_id, content_hash in hash_bound:
        if content_hash is None:
            continue
        _add_evidence_bytes(evidence, record_id, content_hash, BASE_EVIDENCE)
    context_data = canonical_json_bytes(
        {
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "sha256": item.sha256,
                    "content_hash": item.content_hash,
                }
                for item in trace.context_artifacts
            ]
        }
    )
    _add_evidence_bytes(evidence, binding.context_id, binding.context_hash, context_data)
    observation = trace.reward_observation
    if observation is not None:
        observation_data = _canonical_preimage(observation)
        _add_evidence_bytes(
            evidence,
            observation.observation_id,
            observation.content_hash,
            observation_data,
        )
        if observation.evidence_id is not None:
            _add_evidence_bytes(
                evidence,
                observation.evidence_id,
                observation.content_hash,
                observation_data,
            )
    sampling = trace.generation_metadata.sampling_parameters_hash
    if sampling.evidence_id is not None and sampling.value is not None:
        _add_evidence_bytes(evidence, sampling.evidence_id, sampling.value, BASE_EVIDENCE)
    id_only = (
        *(item.evidence_id for item in trace.context_transformations),
        *(item.evidence_id for item in trace.tool_observations),
        *(item.evidence_id for item in trace.environment_events),
        *trace.provenance_evidence_ids,
        *(
            value.evidence_id
            for value in (
                trace.reward_observation_hash,
                trace.capture_reward_validity,
                trace.generation_metadata.token_ids,
                trace.generation_metadata.token_count,
                trace.generation_metadata.log_probabilities,
                trace.generation_metadata.sampling_parameters_hash,
                trace.generation_metadata.stop_reason,
                trace.generation_metadata.provider_request_id,
                trace.artifact_integrity,
                trace.protected_boundary_crossed,
                trace.evaluator_succeeded,
            )
            if value.evidence_id is not None
        ),
    )
    for record_id in id_only:
        evidence.setdefault(record_id, BASE_EVIDENCE)
    return evidence


def _assessment_supporting_evidence(
    assessment: RewardValidityAssessment,
    evidence: dict[str, bytes],
) -> None:
    expectation = assessment.expectation
    _add_evidence_bytes(
        evidence,
        expectation.resolution.expectation_source.record_id,
        expectation.resolution.expectation_source.content_hash,
        _canonical_preimage(expectation, exclude=frozenset({"resolution"})),
    )
    for snapshot in (
        assessment.verification.verifier_result,
        assessment.verification.checker_result,
    ):
        _add_evidence_bytes(
            evidence,
            snapshot.source.record_id,
            snapshot.source.content_hash,
            _canonical_preimage(
                snapshot,
                exclude=frozenset({"snapshot_id", "source", "resolver"}),
            ),
        )
    for diagnostic in assessment.diagnostic_coverage.diagnostics:
        _add_evidence_bytes(
            evidence,
            diagnostic.source.record_id,
            diagnostic.source.content_hash,
            _canonical_preimage(
                diagnostic,
                exclude=frozenset({"source", "resolver"}),
            ),
        )
    receipts = (
        assessment.evidence_inventory.resolved_by,
        *(item.receipt for item in assessment.evidence_inventory.records),
    )
    for receipt in receipts:
        if receipt.record_id in {
            assessment.trace_id,
            assessment.assessment_id,
            assessment.trace.observed_binding.protocol_id,
        }:
            continue
        if receipt.record_id in evidence:
            if sha256_hex(evidence[receipt.record_id]) != receipt.content_hash:
                raise RuntimeError("retained assessment evidence has a mismatched hash")
            continue
        _add_evidence_bytes(
            evidence,
            receipt.record_id,
            receipt.content_hash,
            BASE_EVIDENCE,
        )


def _evaluation_fixture(
    protocol: ModelHarnessProtocol,
    coordinate: ModelHarnessCoordinate,
    index: int,
    *,
    invalid: bool = False,
    trace: HarnessExecutionTrace | None = None,
) -> EvaluationFixture:
    suffix = f"matrix-{index:02d}" if not invalid else f"matrix-invalid-{index:02d}"
    if trace is None:
        context_artifact = ObservableArtifactRef.build(
            artifact_id="matrix-artifact",
            sha256=BASE_HASH,
            size_bytes=len(BASE_EVIDENCE),
            media_type="application/json",
        )
        context_hash = artifact_collection_hash((context_artifact,))
        binding = TraceBinding.from_model_harness_protocol(
            protocol,
            coordinate,
            artifacts=(context_artifact,),
            model_hash=BASE_HASH,
            harness_hash=BASE_HASH,
            procedure_id="offline-procedure",
            procedure_version="v1",
            procedure_hash=BASE_HASH,
            environment_id="offline-environment-evidence",
            environment_version="v1",
            environment_hash=BASE_HASH,
            context_id="matrix-context",
            context_hash=context_hash,
            output_schema_id="matrix-output-schema",
            validator_hash=BASE_HASH,
            checker_hash=BASE_HASH,
        )
        observation = RewardObservation.build(
            observation_id=f"matrix-reward-{index:02d}",
            task_id=protocol.task_set_id,
            task_input_hash=protocol.task_set_hash,
            verifier_id=protocol.verifier_id,
            verifier_version=protocol.verifier_version,
            checker_id=protocol.checker_id,
            checker_version=protocol.checker_version,
            checker_result_id="checker-result",
            checker_result_hash=BASE_HASH,
            evaluator_id="offline-evaluator",
            evaluator_version="v1",
            value="HIGH" if index == 0 else Decimal("0.9"),
            evidence_id=f"matrix-reward-evidence-{index:02d}",
            observed_at=NOW,
        )
        tool_observation = ToolObservation.build(
            sequence=0,
            tool_id="offline-evaluator",
            tool_version="v1",
            request_hash=BASE_HASH,
            response_hash=AvailableValue[str](
                status=MetadataAvailability.AVAILABLE,
                value=BASE_HASH,
                evidence_id="tool-call",
            ),
            status=ToolObservationStatus.SUCCEEDED,
            evidence_id="tool-call",
        )
        events = (
            EnvironmentEvent.build(
                sequence=0,
                environment_id=binding.environment_id,
                environment_version=binding.environment_version,
                kind=EnvironmentEventKind.STARTED,
                evidence_id="environment-started",
            ),
            EnvironmentEvent.build(
                sequence=1,
                environment_id=binding.environment_id,
                environment_version=binding.environment_version,
                kind=EnvironmentEventKind.COMPLETED,
                evidence_id="environment-completed",
            ),
        )
        output = ObservableArtifactRef.build(
            artifact_id="matrix-output",
            sha256=BASE_HASH,
            size_bytes=len(BASE_EVIDENCE),
            media_type="application/json",
        )
        trace = HarnessExecutionTrace.build(
            trace_id=f"matrix-trace-{index:02d}",
            observed_binding=binding,
            context_artifacts=(context_artifact,),
            initial_context_hash=context_hash,
            context_transformations=(),
            final_context_hash=context_hash,
            tool_observations=(tool_observation,),
            environment_events=events,
            output_artifacts=(output,),
            output_hash=artifact_collection_hash((output,)),
            verifier_result_id="verifier-result",
            verifier_result_hash=BASE_HASH,
            checker_result_id="checker-result",
            checker_result_hash=BASE_HASH,
            reward_observation=observation,
            reward_observation_hash=AvailableValue[str](
                status=MetadataAvailability.AVAILABLE,
                value=observation.content_hash,
                evidence_id=observation.observation_id,
            ),
            capture_reward_validity=AvailableValue[CaptureRewardValidityStatus](
                status=MetadataAvailability.AVAILABLE,
                value=CaptureRewardValidityStatus.INCONCLUSIVE,
                evidence_id="capture-validity",
            ),
            generation_metadata=GenerationMetadata.build(
                token_ids=AvailableValue[tuple[int, ...]](
                    status=MetadataAvailability.UNAVAILABLE,
                    value=None,
                    evidence_id=None,
                ),
                token_count=AvailableValue[int](
                    status=MetadataAvailability.AVAILABLE,
                    value=17,
                    evidence_id="usage-meter",
                ),
                log_probabilities=AvailableValue[tuple[Decimal, ...]](
                    status=MetadataAvailability.UNAVAILABLE,
                    value=None,
                    evidence_id=None,
                ),
                sampling_parameters_hash=AvailableValue[str](
                    status=MetadataAvailability.AVAILABLE,
                    value=BASE_HASH,
                    evidence_id="request-envelope",
                ),
                stop_reason=AvailableValue[GenerationStopReason](
                    status=MetadataAvailability.AVAILABLE,
                    value=GenerationStopReason.COMPLETED,
                    evidence_id="response-envelope",
                ),
                provider_request_id=AvailableValue[str](
                    status=MetadataAvailability.UNAVAILABLE,
                    value=None,
                    evidence_id=None,
                ),
            ),
            resource_usage=ResourceUsage(
                cost_usd=0.1,
                compute_units=1.0,
                tokens=17,
                elapsed_seconds=0.5,
                tool_calls=1,
                human_interventions=0,
            ),
            execution_status=ExecutionStatus.COMPLETED,
            artifact_integrity=AvailableValue[bool](
                status=MetadataAvailability.AVAILABLE,
                value=True,
                evidence_id="artifact-integrity",
            ),
            protected_boundary_crossed=AvailableValue[bool](
                status=MetadataAvailability.AVAILABLE,
                value=False,
                evidence_id="boundary-monitor",
            ),
            evaluator_succeeded=AvailableValue[bool](
                status=MetadataAvailability.AVAILABLE,
                value=True,
                evidence_id="evaluator-run",
            ),
            provenance_evidence_ids=(
                "fixture-run",
                "protocol-provenance",
            ),
            observed_at=NOW,
        )
    expectation, expectation_inventory = _trace_expectation(
        trace.observed_binding,
        suffix=suffix,
    )
    verification, verification_entries = _verification(trace, suffix=suffix)
    findings, coverage, diagnostic_entries = _findings_and_coverage(
        trace,
        suffix=suffix,
        invalid=invalid,
    )
    observation = trace.reward_observation
    if observation is None or observation.evidence_id is None:
        raise RuntimeError("evaluation fixture requires retained reward evidence")
    inventory = _resolved_inventory(
        (
            *((item.receipt, item.kind) for item in expectation_inventory.records),
            *verification_entries,
            *diagnostic_entries,
            (
                _receipt(observation.evidence_id, observation.content_hash),
                ResolvedEvidenceKind.OBSERVABLE_EVIDENCE,
            ),
        ),
        inventory_id=f"reward-inventory-{suffix}",
        resolver_id="reward-inventory-resolver",
    )
    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=verification,
        diagnostic_coverage=coverage,
        inventory=inventory,
    )
    chain, record = project_harness_evidence_snapshots(
        protocol=protocol,
        coordinate=coordinate,
        trace=trace,
        freshness=trace_freshness(expectation, trace, inventory=inventory),
        assessment=assessment,
    )
    evidence = _trace_supporting_evidence(trace)
    _assessment_supporting_evidence(assessment, evidence)
    return EvaluationFixture(
        chain=chain,
        record=record,
        trace=trace,
        assessment=assessment,
        findings=findings,
        evidence=evidence,
    )


def _record_model_harness_grid(
    runtime: LocalRuntime,
    research: ResearchCoordinator,
    submitter: CognitiveOrchestrationService,
    retainer: EvidenceRetainer,
) -> tuple[dict[str, object], EvaluationFixture, ModelHarnessProtocol]:
    models = (
        ModelIdentity(model_id="model-a", model_version="v1"),
        ModelIdentity(model_id="model-b", model_version="v1"),
    )
    harnesses = (
        HarnessIdentity(harness_id="harness-a", harness_version="v1"),
        HarnessIdentity(harness_id="harness-b", harness_version="v1"),
    )
    partitions = (HarnessPartition.HARNESS_DISCOVERY_TASKS,)
    grid = tuple(
        ModelHarnessCoordinate(model=model, harness=harness, partition=partition)
        for model, harness, partition in product(models, harnesses, partitions)
    )
    budgets = tuple(
        ModelBudgetBinding.build(model=model, budget=_evaluation_budget(model)) for model in models
    )
    protocol = ModelHarnessProtocol.build(
        protocol_id="offline-model-harness-protocol",
        version=1,
        models=models,
        harnesses=harnesses,
        partitions=partitions,
        task_set_id="offline-model-harness-task-set",
        task_set_hash=BASE_HASH,
        verifier_id="offline-validator",
        verifier_version="v1",
        checker_id="offline-checker",
        checker_version="v1",
        artifact_ids=("matrix-artifact",),
        random_seed=7,
        output_schema_hash=BASE_HASH,
        model_budgets=budgets,
        matched_resource_envelope_hash=evaluation_resource_envelope_hash(
            _evaluation_budget(models[0])
        ),
        expected_grid=grid,
        comparison_kinds=(
            ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,
            ModelHarnessComparisonKind.HARNESS_HELD_CONSTANT,
            ModelHarnessComparisonKind.INTERACTION_DESCRIPTIVE,
        ),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    actor = _service_actor()
    approval = _approval(runtime)
    _run_slice(
        research,
        submitter,
        runtime,
        (
            RecordModelHarnessProtocol(
                proposal_id="record-offline-model-harness-protocol",
                idempotency_key="record-offline-model-harness-protocol",
                proposer=actor,
                approval=approval,
                protocol=protocol,
            ),
        ),
    )
    fixtures: list[EvaluationFixture] = []
    cells: list[ModelHarnessCell] = []
    for index, coordinate in enumerate(protocol.expected_grid):
        fixture = _evaluation_fixture(protocol, coordinate, index)
        if fixture.assessment.status is not RewardValidityStatus.VALID:
            raise RuntimeError("model-harness fixture reward was not valid")
        retainer.retain(fixture.evidence)
        cell = ModelHarnessCell.from_protocol(
            cell_id=f"offline-model-harness-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_evaluation_metrics(str(Decimal("0.5") + Decimal(index) / 10)),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(fixture.chain),
            observed_at=NOW,
        )
        _run_slice(
            research,
            submitter,
            runtime,
            (
                RecordHarnessExecutionTrace(
                    proposal_id=f"record-offline-trace-{index:02d}",
                    idempotency_key=f"record-offline-trace-{index:02d}",
                    proposer=actor,
                    approval=approval,
                    envelope=HarnessExecutionTraceEnvelope(
                        metadata=HarnessTraceRecordMetadata(
                            received_at=NOW,
                            source_id="offline-harness-runtime",
                        ),
                        trace=fixture.trace,
                    ),
                ),
                RecordRewardAssessment(
                    proposal_id=f"record-offline-reward-{index:02d}",
                    idempotency_key=f"record-offline-reward-{index:02d}",
                    proposer=actor,
                    approval=approval,
                    observation=fixture.assessment.observation,
                    findings=fixture.findings,
                    assessment=fixture.assessment,
                ),
                AppendModelHarnessCell(
                    proposal_id=f"append-offline-model-harness-cell-{index:02d}",
                    idempotency_key=f"append-offline-model-harness-cell-{index:02d}",
                    proposer=actor,
                    approval=approval,
                    cell=cell,
                ),
            ),
        )
        fixtures.append(fixture)
        cells.append(cell)
    evidence_index = HarnessEvidenceSnapshotIndex.build(
        records=tuple(
            sorted(
                (item.record for item in fixtures),
                key=lambda item: item.chain_receipt.record_id,
            )
        )
    )
    analysis = analyze_model_harness(
        protocol,
        tuple(cells),
        evidence_chains=tuple(item.chain for item in fixtures),
        evidence_index=evidence_index,
    )
    _run_slice(
        research,
        submitter,
        runtime,
        (
            RecordModelHarnessAnalysis(
                proposal_id="record-offline-model-harness-analysis",
                idempotency_key="record-offline-model-harness-analysis",
                proposer=actor,
                approval=approval,
                analysis=analysis,
            ),
        ),
    )
    availability = sorted(
        {
            item.status.value
            for fixture in fixtures
            for item in (
                fixture.trace.generation_metadata.token_count,
                fixture.trace.generation_metadata.token_ids,
            )
        }
    )
    return (
        {
            "cell_count": len(cells),
            "harnesses": [item.harness_id for item in protocol.harnesses],
            "metadata_availability": availability,
            "models": [item.model_id for item in protocol.models],
        },
        fixtures[0],
        protocol,
    )


def _record_invalid_high_reward(
    runtime: LocalRuntime,
    research: ResearchCoordinator,
    submitter: CognitiveOrchestrationService,
    retainer: EvidenceRetainer,
    protocol: ModelHarnessProtocol,
    valid_fixture: EvaluationFixture,
) -> dict[str, object]:
    coordinate = protocol.expected_grid[0]
    invalid = _evaluation_fixture(
        protocol,
        coordinate,
        0,
        invalid=True,
        trace=valid_fixture.trace,
    )
    if invalid.assessment.status is not RewardValidityStatus.INVALID:
        raise RuntimeError("invalid reward fixture was not invalid")
    retainer.retain(invalid.evidence)
    decision = _run_slice(
        research,
        submitter,
        runtime,
        (
            RecordRewardAssessment(
                proposal_id="record-offline-invalid-high-reward",
                idempotency_key="record-offline-invalid-high-reward",
                proposer=_service_actor(),
                approval=_approval(runtime),
                observation=invalid.assessment.observation,
                findings=invalid.findings,
                assessment=invalid.assessment,
            ),
        ),
        allow_last_rejection=True,
    )[0]
    reason = decision.reasons[0].code.value if decision.reasons else None
    return {
        "accepted": decision.accepted,
        "assessment_id": invalid.assessment.assessment_id,
        "decision_code": reason,
        "promotion_evidence": bool(valid_reward_evidence((invalid.assessment,))),
        "reward": invalid.assessment.observation.value,
        "status": invalid.assessment.status.value,
    }


def _verify_export_import_replay(
    source: LocalRuntime,
    workspace_root: Path,
) -> dict[str, object]:
    with source.uow_factory() as unit_of_work:
        source_verification = verify_workspace(
            unit_of_work.repositories(),
            source.artifact_store,
        )
    exported = export_workspace(
        uow_factory=source.uow_factory,
        artifact_store=source.artifact_store,
    )
    target = create_local_runtime(workspace_root / "imported", source.policy)
    try:
        imported = import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )
        replayed = import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )
    finally:
        target.engine.dispose()
    return {
        "exported_record_count": len(exported.records),
        "import_verified": imported.projections_verified,
        "replay_count": replayed.replayed,
        "replay_verified": replayed.projections_verified and not replayed.conflicts,
        "verified": source_verification.valid,
    }


def run_example(workspace_root: Path) -> dict[str, object]:
    """Run the deterministic offline governed cognitive-procedure demonstration."""
    if not isinstance(workspace_root, Path):
        raise TypeError("workspace_root must be a Path")
    runtime = create_local_runtime(workspace_root, fixed_policy())
    submitter = CognitiveOrchestrationService()
    research = ResearchCoordinator()
    retainer = EvidenceRetainer(runtime, research, submitter)
    try:
        capabilities, diversity, collaboration = _record_cognition_and_collaboration(
            runtime,
            research,
            submitter,
        )
        invalid_compilation, valid_compilation, valid_binding = _record_procedures_and_binding(
            runtime,
            research,
            submitter,
            retainer,
        )
        guidance = _record_guidance_grid(runtime, research, submitter)
        model_harness, first_fixture, protocol = _record_model_harness_grid(
            runtime,
            research,
            submitter,
            retainer,
        )
        invalid_reward = _record_invalid_high_reward(
            runtime,
            research,
            submitter,
            retainer,
            protocol,
            first_fixture,
        )
        workspace = _verify_export_import_replay(runtime, workspace_root)
        return {
            "schema_version": 1,
            "capabilities": capabilities,
            "collaboration": collaboration,
            "diversity": diversity,
            "guidance": guidance,
            "invalid_compilation": invalid_compilation,
            "invalid_reward": invalid_reward,
            "model_harness": model_harness,
            "valid_binding": valid_binding,
            "valid_compilation": valid_compilation,
            "workspace": workspace,
        }
    finally:
        runtime.engine.dispose()


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic governed cognitive-procedure vertical slice."
    )
    parser.add_argument("--root", type=Path, default=Path("cognitive-procedure-workspace"))
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)
    result = run_example(arguments.root)
    if arguments.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
