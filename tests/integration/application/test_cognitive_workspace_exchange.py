from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from super_scientist.application.transactions import coordinator as coordinator_module
from super_scientist.application.transactions.harness_extensions import (
    _trace_hash_bound_evidence,
    _trace_id_only_evidence,
)
from super_scientist.application.workspace_exchange import (
    WorkspaceExport,
    WorkspaceProjectionExpectation,
    export_workspace,
    import_workspace,
)
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CapabilityRequirement,
    CohortPlanReceiptRef,
    CohortRequest,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.cognition.grounding import assess_capability
from super_scientist.domain.collaboration import (
    CollaborationSession,
    TopologyOperation,
    TopologySnapshot,
    advance_collaboration,
    apply_topology_event,
    evaluate_termination,
    initial_collaboration_state,
    next_peer,
)
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessEvidenceSnapshotIndex,
    harness_cell_evidence_chain_receipt,
)
from super_scientist.domain.harness_eval.guidance import (
    EvaluationReferenceComponent,
    GuidanceCondition,
    GuidanceEvaluationCell,
    MetricMissingReason,
    ReferenceMissingness,
)
from super_scientist.domain.harness_eval.matrix import (
    HarnessIdentity,
    ModelBudgetBinding,
    ModelHarnessCell,
    ModelIdentity,
    analyze_model_harness,
)
from super_scientist.domain.harness_eval.traces import _canonicalize_hash_mapping
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.models import ResourceBudget
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.procedures import (
    AcceptedSourceReceiptRef,
    ArtifactCatalogEntry,
    CandidateMethod,
    CompiledProgressPlanBinding,
    GroundedCapabilityAssessment,
    MethodDirectionOutcome,
    MethodDirectionStatus,
    OpaqueProcedureCompilationEnvelope,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureCompilationRequest,
    ProcedureEvidenceSourceKind,
    canonical_model_hash,
    compile_method,
    procedure_to_progress_plan,
)
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
    ProposalAttempt,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RecordDiversityAssessment,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordMethodDirectionOutcome,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordProcedureCompilation,
    RecordRewardAssessment,
    RejectionCode,
)
from super_scientist.providers.storage.procedure_sources import (
    ProcedureSourceBinding,
    ProcedureSourceSnapshot,
)
from tests.integration.application.test_cognitive_workspace_integrity import (
    _governed_policy,
    _profile_for_policy,
)
from tests.integration.application.test_workspace_exchange import (
    ExchangeRuntime,
    FixedClock,
    _runtime,
)
from tests.unit.collaboration.conftest import (
    profile as collaboration_profile,
)
from tests.unit.collaboration.conftest import (
    session_factory,
    unit_usage,
)
from tests.unit.collaboration.test_engine import _contribution, _request
from tests.unit.collaboration.test_topology import _event
from tests.unit.harness_eval import test_harness_security_contracts as security_fixtures
from tests.unit.harness_eval import test_model_harness_matrix as matrix_fixtures
from tests.unit.harness_eval import test_traces as trace_fixtures
from tests.unit.harness_eval.test_guidance import _cell as guidance_cell
from tests.unit.harness_eval.test_guidance import _protocol as guidance_protocol
from tests.unit.harness_eval.test_harness_security_contracts import _matrix_evidence_chain
from tests.unit.harness_eval.test_model_harness_matrix import _budget as matrix_budget
from tests.unit.harness_eval.test_model_harness_matrix import _grid as matrix_grid
from tests.unit.harness_eval.test_model_harness_matrix import _metrics as matrix_metrics
from tests.unit.harness_eval.test_model_harness_matrix import _protocol as matrix_protocol
from tests.unit.procedures.test_compiler import valid_request

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _service_actor() -> ActorIdentity:
    return ActorIdentity(actor_id="complete-slice-service", kind=ActorKind.SERVICE, created_at=NOW)


def _approval(runtime: ExchangeRuntime) -> Approval:
    return Approval(approver=runtime.actor, approved_at=NOW)


def _submit(runtime: ExchangeRuntime, proposal: BaseModel) -> None:
    decision = runtime.coordinator.submit(proposal)  # type: ignore[arg-type]
    assert decision.accepted is True, decision


def _accepted_binding(
    runtime: ExchangeRuntime,
    proposal_id: str,
) -> tuple[str, str, str]:
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
    assert transaction is not None
    assert len(events) == 1
    return transaction.proposal_hash, events[0].event_id, events[0].event_hash


def _add_evidence(
    runtime: ExchangeRuntime,
    evidence_id: str,
    data: bytes,
    *,
    suffix: str,
    evidence_type: str = "complete-slice-evidence",
) -> AddEvidence:
    artifact = runtime.artifact_store.put(data, "application/json")
    proposal = AddEvidence(
        proposal_id=f"complete-evidence-{suffix}",
        idempotency_key=f"complete-evidence-{suffix}",
        proposer=_service_actor(),
        evidence=EvidenceRecord(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            source_locator=f"fixture:{evidence_id}",
            retrieved_at=NOW,
            artifact=artifact,
            provenance={"fixture": "task-15-complete-round-trip"},
            ingestion_actor_id="complete-slice-service",
        ),
    )
    _submit(runtime, proposal)
    return proposal


def _canonical_hash_preimage(
    record: BaseModel,
    *,
    exclude: frozenset[str] = frozenset(),
) -> bytes:
    payload = to_jsonable_python(
        _canonicalize_hash_mapping(record.model_dump(mode="python", warnings=False))
    )
    for field_name in {"content_hash", *exclude}:
        payload.pop(field_name, None)
    return canonical_json_bytes(payload)


def _record_cognition_and_collaboration(runtime: ExchangeRuntime) -> None:
    policy = _governed_policy()
    actor = _service_actor()
    approval = _approval(runtime)

    profiles = []
    profile_receipts = []
    for peer_id in ("peer-a", "peer-b"):
        base = collaboration_profile(peer_id)
        values = base.model_dump(mode="python", exclude={"content_hash"})
        values["governing_policy_hash"] = policy.policy_hash
        retained = CapabilityProfile.build(**values)
        proposal = RecordCapabilityProfile(
            proposal_id=f"complete-profile-{peer_id}",
            idempotency_key=f"complete-profile-{peer_id}",
            proposer=actor,
            approval=approval,
            profile=retained,
        )
        _submit(runtime, proposal)
        proposal_hash, audit_id, audit_hash = _accepted_binding(runtime, proposal.proposal_id)
        profiles.append(retained)
        profile_receipts.append(
            CapabilityProfileReceiptRef(
                proposal_id=proposal.proposal_id,
                proposal_hash=proposal_hash,
                audit_event_id=audit_id,
                audit_event_hash=audit_hash,
            )
        )

    request = CohortRequest.build(
        request_id="complete-cohort-request",
        task_id="research",
        min_members=2,
        max_members=2,
        candidate_actor_ids=("peer-a", "peer-b"),
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="requirement-analysis",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        prohibited_combinations=(),
        governing_policy_hash=policy.policy_hash,
    )
    cohort = build_cohort(request, tuple(profiles))
    cohort_proposal = RecordCohortPlan(
        proposal_id="complete-cohort",
        idempotency_key="complete-cohort",
        proposer=actor,
        approval=approval,
        request=request,
        profile_receipts=tuple(profile_receipts),
        plan=cohort,
    )
    _submit(runtime, cohort_proposal)
    cohort_hash, cohort_audit_id, cohort_audit_hash = _accepted_binding(
        runtime, cohort_proposal.proposal_id
    )
    cohort_receipt = CohortPlanReceiptRef(
        proposal_id=cohort_proposal.proposal_id,
        proposal_hash=cohort_hash,
        audit_event_id=cohort_audit_id,
        audit_event_hash=cohort_audit_hash,
    )
    _submit(
        runtime,
        RecordDiversityAssessment(
            proposal_id="complete-diversity",
            idempotency_key="complete-diversity",
            proposer=actor,
            approval=approval,
            cohort_plan_receipt=cohort_receipt,
            profile_receipts=tuple(profile_receipts),
            error_correlations=(),
            assessment=assess_diversity(cohort, tuple(profiles), ()),
        ),
    )

    base_session = session_factory.__wrapped__()(
        "peer-a",
        "peer-b",
        completion_count=1,
    )
    collaboration_artifact = runtime.artifact_store.put(
        b"complete collaboration input",
        "application/json",
    )
    session_values = base_session.model_dump(mode="python", exclude={"content_hash"})
    session_values.update(
        task_id=request.task_id,
        cohort_plan=cohort,
        peers=tuple(item.actor for item in profiles),
        allowed_artifacts=(collaboration_artifact,),
        governing_policy_hash=policy.policy_hash,
    )
    session = CollaborationSession.build(**session_values)
    _submit(
        runtime,
        RecordCollaborationSession(
            proposal_id="complete-session",
            idempotency_key="complete-session",
            proposer=actor,
            approval=approval,
            session=session,
        ),
    )
    state = initial_collaboration_state(session)
    after = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    topology = _event(
        session,
        state.topology,
        after,
        TopologyOperation.DISABLE_EDGE,
        edge=("peer-a", "peer-b"),
    )
    _submit(
        runtime,
        AppendTopologyEvent(
            proposal_id="complete-topology",
            idempotency_key="complete-topology",
            proposer=actor,
            approval=approval,
            event=topology,
        ),
    )
    state = apply_topology_event(session, state, topology)
    recipient = next_peer(session, state)
    base_request = _request(session, recipient)
    request_record = type(base_request).build(
        **{
            **base_request.model_dump(mode="python", exclude={"content_hash"}),
            "artifact_refs": (collaboration_artifact,),
        }
    )
    _submit(
        runtime,
        AppendPeerRequest(
            proposal_id="complete-peer-request",
            idempotency_key="complete-peer-request",
            proposer=actor,
            approval=approval,
            request=request_record,
        ),
    )
    base_contribution = _contribution(session, recipient)
    contribution = type(base_contribution).build(
        **{
            **base_contribution.model_dump(mode="python", exclude={"content_hash"}),
            "artifact_refs": (collaboration_artifact,),
        }
    )
    usage = unit_usage()
    _submit(
        runtime,
        AppendPeerContribution(
            proposal_id="complete-peer-contribution",
            idempotency_key="complete-peer-contribution",
            proposer=actor,
            approval=approval,
            contribution=contribution,
            usage=usage,
        ),
    )
    state = advance_collaboration(session, state, request_record, contribution, usage)
    _submit(
        runtime,
        RecordCollaborationTermination(
            proposal_id="complete-termination",
            idempotency_key="complete-termination",
            proposer=actor,
            approval=approval,
            session_id=session.session_id,
            termination=evaluate_termination(state),
        ),
    )


def _procedure_source_receipt(
    runtime: ExchangeRuntime,
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
        runtime, proposal.proposal_id
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


def _record_procedure(runtime: ExchangeRuntime) -> None:
    policy = _governed_policy()
    actor = _service_actor()
    approval = _approval(runtime)
    base = valid_request()

    candidate_artifact = runtime.artifact_store.put(
        b"complete candidate evidence",
        "application/json",
    )
    catalog_artifact = runtime.artifact_store.put(
        b"complete source artifact",
        "application/json",
    )
    candidate_values = base.candidate.model_dump(mode="python", exclude={"content_hash"})
    candidate_values["evidence_refs"] = (candidate_artifact,)
    candidate = CandidateMethod.build(**candidate_values)
    artifact_catalog = (
        ArtifactCatalogEntry(
            artifact_id=base.artifact_catalog[0].artifact_id,
            artifact=catalog_artifact,
            availability=base.artifact_catalog[0].availability,
        ),
    )

    capability_snapshot = ProcedureSourceSnapshot(
        snapshot_family_id="complete-capability-snapshot",
        snapshot_id="complete-capability-snapshot",
        source_bindings=(),
    )
    capability_snapshot_bytes = canonical_json_bytes(capability_snapshot.model_dump(mode="json"))
    capability_snapshot_proposal = _add_evidence(
        runtime,
        capability_snapshot.snapshot_id,
        capability_snapshot_bytes,
        suffix="procedure-capability-snapshot",
        evidence_type="procedure-source",
    )
    capability_snapshot_hash = capability_snapshot_proposal.evidence.content_hash

    original_grounded = base.capability_assessments[0]
    assertion = original_grounded.profile.assertions[0].model_copy(
        update={"evidence_snapshot_hash": capability_snapshot_hash}
    )
    profile_values = original_grounded.profile.model_dump(mode="python", exclude={"content_hash"})
    profile_values.update(
        assertions=(assertion,),
        governing_policy_hash=policy.policy_hash,
    )
    profile = CapabilityProfile.build(**profile_values)
    requirement = original_grounded.assessment.requirement.model_copy(
        update={"evidence_snapshot_hash": capability_snapshot_hash}
    )
    profile_proposal = RecordCapabilityProfile(
        proposal_id="complete-procedure-profile",
        idempotency_key="complete-procedure-profile",
        proposer=actor,
        approval=approval,
        profile=profile,
    )
    _submit(runtime, profile_proposal)
    profile_receipt = _procedure_source_receipt(
        runtime,
        profile_proposal,
        receipt_id="complete-procedure-profile-receipt",
        source_kind=ProcedureEvidenceSourceKind.CAPABILITY_PROFILE,
        source_record_id=profile.profile_id,
        source_schema_version=profile.schema_version,
        source_content_hash=profile.content_hash,
        source_snapshot_id=capability_snapshot.snapshot_id,
        source_snapshot_hash=capability_snapshot_hash,
    )
    grounded = GroundedCapabilityAssessment.build(
        profile=profile,
        assessment=assess_capability(profile, requirement),
        profile_receipt=profile_receipt,
    )

    catalog_specs = (
        (
            ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
            artifact_catalog,
            base.artifact_catalog_complete,
        ),
        (
            ProcedureEvidenceSourceKind.TOOL_CATALOG,
            base.tool_catalog,
            base.tool_catalog_complete,
        ),
        (
            ProcedureEvidenceSourceKind.VALIDATOR_CATALOG,
            base.validator_catalog,
            base.validator_catalog_complete,
        ),
    )
    catalog_sources = []
    for index, (kind, entries, complete) in enumerate(catalog_specs, start=1):
        catalog_bytes = canonical_json_bytes(
            {
                "catalog_kind": kind.value,
                "entries": tuple(item.model_dump(mode="json") for item in entries),
                "complete": complete,
            }
        )
        proposal = _add_evidence(
            runtime,
            f"complete-{kind.value.lower()}",
            catalog_bytes,
            suffix=f"procedure-catalog-{index}",
            evidence_type="procedure-source",
        )
        catalog_sources.append((kind, proposal))

    catalog_snapshot = ProcedureSourceSnapshot(
        snapshot_family_id="complete-catalog-snapshot",
        snapshot_id="complete-catalog-snapshot",
        source_bindings=tuple(
            sorted(
                (
                    ProcedureSourceBinding(
                        source_record_id=proposal.evidence.evidence_id,
                        source_content_hash=proposal.evidence.content_hash,
                    )
                    for _kind, proposal in catalog_sources
                ),
                key=lambda item: item.source_record_id,
            )
        ),
    )
    catalog_snapshot_proposal = _add_evidence(
        runtime,
        catalog_snapshot.snapshot_id,
        canonical_json_bytes(catalog_snapshot.model_dump(mode="json")),
        suffix="procedure-catalog-snapshot",
        evidence_type="procedure-source",
    )
    catalog_snapshot_hash = catalog_snapshot_proposal.evidence.content_hash
    catalog_receipts = tuple(
        _procedure_source_receipt(
            runtime,
            proposal,
            receipt_id=f"complete-{kind.value.lower()}-receipt",
            source_kind=kind,
            source_record_id=proposal.evidence.evidence_id,
            source_schema_version=1,
            source_content_hash=proposal.evidence.content_hash,
            source_snapshot_id=catalog_snapshot.snapshot_id,
            source_snapshot_hash=catalog_snapshot_hash,
        )
        for kind, proposal in catalog_sources
    )
    request_values = base.model_dump(mode="python")
    request_values.update(
        candidate=candidate,
        capability_assessments=(grounded,),
        artifact_catalog=artifact_catalog,
        artifact_catalog_receipt=catalog_receipts[0],
        tool_catalog_receipt=catalog_receipts[1],
        validator_catalog_receipt=catalog_receipts[2],
    )
    request = ProcedureCompilationRequest.model_validate(request_values, strict=True)
    compilation_proposal = RecordProcedureCompilation(
        proposal_id="complete-compilation-proposal",
        idempotency_key="complete-compilation-proposal",
        proposer=actor,
        approval=approval,
        compilation=OpaqueProcedureCompilationEnvelope.build(
            compilation_id="complete-compilation",
            result=compile_method(request),
            created_at=NOW,
            governing_policy_hash=policy.policy_hash,
        ),
    )
    _submit(runtime, compilation_proposal)
    compilation = ProcedureCompilationRecord.build_from_untrusted_envelope(
        compilation_proposal.compilation
    )
    _submit(
        runtime,
        RecordMethodDirectionOutcome(
            proposal_id="complete-method-outcome-proposal",
            idempotency_key="complete-method-outcome-proposal",
            proposer=actor,
            approval=approval,
            compilation_id=compilation.compilation_id,
            outcome=MethodDirectionOutcome.build(
                outcome_id="complete-method-outcome",
                status=MethodDirectionStatus.UNSUPPORTED,
                evidence_refs=(),
                failed_method_ids=(compilation.result.procedure.source_candidate.method_id,),
                rejected_procedure_ids=(compilation.result.procedure.procedure_id,),
                budget_reference_ids=(),
                terminal_rule="Stop after deterministic independent rejection",
                created_at=NOW,
                governing_policy_hash=policy.policy_hash,
            ),
        ),
    )

    budget = ResourceBudget(
        cost_usd=100.0,
        compute_units=100.0,
        tokens=10_000,
        elapsed_seconds=1_000.0,
        tool_calls=100,
        human_interventions=10,
    )
    run = ResearchRun(
        run_id="complete-run",
        charter="Replay the complete governed slice",
        scope=("Task 15",),
        creator=actor,
        created_at=NOW,
        active_governance_policy_hash=policy.policy_hash,
        model_configuration_version_id=None,
        scaffold_configuration_version_id=None,
        budget_allocation=RunBudgetAllocation(
            execution=budget,
            search=budget,
            evaluation=budget,
            judging=budget,
            human=budget,
        ),
        final_validator=runtime.actor,
        final_validator_version="complete-validator-v1",
        environment_snapshot_id="complete-environment",
    )
    _submit(
        runtime,
        CreateResearchRun(
            proposal_id="complete-run-proposal",
            idempotency_key="complete-run-proposal",
            proposer=actor,
            approval=approval,
            run=run,
        ),
    )
    proposal_hash, audit_event_id, audit_event_hash = _accepted_binding(
        runtime, compilation_proposal.proposal_id
    )
    compilation_receipt = ProcedureCompilationReceiptRef(
        proposal_id=compilation_proposal.proposal_id,
        proposal_hash=proposal_hash,
        audit_event_id=audit_event_id,
        audit_event_hash=audit_event_hash,
    )
    plan = procedure_to_progress_plan(
        compilation.result,
        run_id=run.run_id,
        plan_version_id="complete-progress-plan",
        version=1,
        created_at=NOW,
        governing_policy_hash=policy.policy_hash,
    )
    binding = CompiledProgressPlanBinding.build(
        binding_id="complete-progress-binding",
        compilation_receipt=compilation_receipt,
        compilation_id=compilation.compilation_id,
        compilation_hash=compilation.content_hash,
        procedure_id=compilation.result.procedure.procedure_id,
        procedure_hash=compilation.result.procedure.content_hash,
        plan=plan,
        plan_hash=canonical_model_hash(plan),
        created_at=NOW,
        governing_policy_hash=policy.policy_hash,
    )
    _submit(
        runtime,
        BindCompiledProgressPlan(
            proposal_id="complete-binding-proposal",
            idempotency_key="complete-binding-proposal",
            proposer=actor,
            approval=approval,
            compilation_receipt=compilation_receipt,
            binding=binding,
            plan=plan,
        ),
    )


def _record_evaluation_extensions(
    runtime: ExchangeRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _governed_policy()
    actor = _service_actor()
    approval = _approval(runtime)

    guidance = guidance_protocol(protocol_id="complete-guidance-protocol")
    _submit(
        runtime,
        RecordGuidanceEvaluationProtocol(
            proposal_id="complete-guidance-protocol-proposal",
            idempotency_key="complete-guidance-protocol-proposal",
            proposer=actor,
            approval=approval,
            protocol=guidance,
        ),
    )
    guidance_metrics = guidance_cell(protocol=guidance).metrics
    missing_references = tuple(
        ReferenceMissingness(
            component=component,
            reason=MetricMissingReason.NOT_OBSERVED,
        )
        for component in EvaluationReferenceComponent
    )
    guidance_result = GuidanceEvaluationCell.build(
        cell_id="complete-guidance-cell",
        protocol=guidance,
        condition=GuidanceCondition.FULL_PROCEDURE_GUIDANCE,
        distractor_artifact_ids=(),
        metrics=guidance_metrics,
        output_artifact_id=None,
        trace_id=None,
        verifier_result_id=None,
        reward_assessment_id=None,
        observed_at=NOW,
        reference_missingness=missing_references,
    )
    _submit(
        runtime,
        AppendGuidanceEvaluationCell(
            proposal_id="complete-guidance-cell-proposal",
            idempotency_key="complete-guidance-cell-proposal",
            proposer=actor,
            approval=approval,
            cell=guidance_result,
        ),
    )

    base_bytes = b"x"
    retained_hash = sha256_hex(base_bytes)
    for module, names in (
        (trace_fixtures, ("HASH_A", "HASH_B", "HASH_C", "HASH_D")),
        (security_fixtures, ("HASH_A", "HASH_B", "HASH_C", "HASH_D")),
        (matrix_fixtures, ("HASH_A", "HASH_B")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, retained_hash)

    models = tuple(
        ModelIdentity(model_id=f"complete-model-{index}", model_version="v1") for index in range(2)
    )
    harnesses = tuple(
        HarnessIdentity(harness_id=f"complete-harness-{index}", harness_version="v1")
        for index in range(2)
    )
    grid = matrix_grid(models=models, harnesses=harnesses)
    protocol = matrix_protocol(
        protocol_id="complete-model-harness-protocol",
        models=models,
        harnesses=harnesses,
        expected_grid=grid,
        model_budgets=tuple(
            ModelBudgetBinding.build(model=model, budget=matrix_budget(model)) for model in models
        ),
        governing_policy_hash=policy.policy_hash,
    )
    _submit(
        runtime,
        RecordModelHarnessProtocol(
            proposal_id="complete-model-harness-protocol-proposal",
            idempotency_key="complete-model-harness-protocol-proposal",
            proposer=actor,
            approval=approval,
            protocol=protocol,
        ),
    )
    fixtures = tuple(
        _matrix_evidence_chain(protocol, coordinate, index)
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    evidence_bytes: dict[str, bytes] = {}

    def retain(record_id: str, content_hash: str, data: bytes) -> None:
        assert sha256_hex(data) == content_hash
        existing = evidence_bytes.get(record_id)
        if (existing == base_bytes and data != base_bytes) or existing is None:
            evidence_bytes[record_id] = data
            existing = data
        assert existing == data

    for fixture in fixtures:
        assessment = fixture.assessment
        trace = fixture.trace
        for record_id, content_hash in _trace_hash_bound_evidence(trace):
            if content_hash == retained_hash:
                retain(record_id, content_hash, base_bytes)
            elif record_id == trace.observed_binding.context_id:
                retain(
                    record_id,
                    content_hash,
                    canonical_json_bytes(
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
                    ),
                )
        for record_id in _trace_id_only_evidence(trace):
            evidence_bytes.setdefault(record_id, base_bytes)

        observation = trace.reward_observation
        assert observation is not None
        observation_bytes = _canonical_hash_preimage(observation)
        retain(observation.observation_id, observation.content_hash, observation_bytes)
        if observation.evidence_id is not None:
            retain(observation.evidence_id, observation.content_hash, observation_bytes)

        expectation = assessment.expectation
        retain(
            expectation.resolution.expectation_source.record_id,
            expectation.resolution.expectation_source.content_hash,
            _canonical_hash_preimage(expectation, exclude=frozenset({"resolution"})),
        )
        for snapshot in (
            assessment.verification.verifier_result,
            assessment.verification.checker_result,
        ):
            retain(
                snapshot.source.record_id,
                snapshot.source.content_hash,
                _canonical_hash_preimage(
                    snapshot,
                    exclude=frozenset({"snapshot_id", "source", "resolver"}),
                ),
            )
        for diagnostic in assessment.diagnostic_coverage.diagnostics:
            retain(
                diagnostic.source.record_id,
                diagnostic.source.content_hash,
                _canonical_hash_preimage(
                    diagnostic,
                    exclude=frozenset({"source", "resolver"}),
                ),
            )

        required_receipts = (
            assessment.evidence_inventory.resolved_by,
            expectation.resolution.expectation_source,
            expectation.resolution.resolver,
            *expectation.resolution.provenance,
            *(item.receipt for item in assessment.evidence_inventory.records),
        )
        for receipt in required_receipts:
            if receipt.record_id in {
                protocol.protocol_id,
                trace.trace_id,
                assessment.assessment_id,
            }:
                continue
            if receipt.record_id not in evidence_bytes:
                assert receipt.content_hash == retained_hash, receipt
                evidence_bytes[receipt.record_id] = base_bytes
    for fixture in fixtures:
        assert all(
            record_id in evidence_bytes and sha256_hex(evidence_bytes[record_id]) == content_hash
            for record_id, content_hash in _trace_hash_bound_evidence(fixture.trace)
        )
        assert all(
            record_id in evidence_bytes for record_id in _trace_id_only_evidence(fixture.trace)
        )
    for index, (record_id, data) in enumerate(sorted(evidence_bytes.items()), start=1):
        _add_evidence(
            runtime,
            record_id,
            data,
            suffix=f"harness-{index:03d}",
        )

    cells = []
    for index, fixture in enumerate(fixtures):
        _submit(
            runtime,
            RecordHarnessExecutionTrace(
                proposal_id=f"complete-trace-proposal-{index}",
                idempotency_key=f"complete-trace-proposal-{index}",
                proposer=actor,
                approval=approval,
                envelope=HarnessExecutionTraceEnvelope(
                    metadata=HarnessTraceRecordMetadata(
                        received_at=NOW,
                        source_id="complete-harness-runtime",
                    ),
                    trace=fixture.trace,
                ),
            ),
        )
        _submit(
            runtime,
            RecordRewardAssessment(
                proposal_id=f"complete-reward-proposal-{index}",
                idempotency_key=f"complete-reward-proposal-{index}",
                proposer=actor,
                approval=approval,
                observation=fixture.assessment.observation,
                findings=fixture.assessment.findings,
                assessment=fixture.assessment,
            ),
        )
        cell = ModelHarnessCell.from_protocol(
            cell_id=f"complete-model-harness-cell-{index}",
            protocol=protocol,
            coordinate=protocol.expected_grid[index],
            metrics=matrix_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(fixture.chain),
            observed_at=NOW,
        )
        _submit(
            runtime,
            AppendModelHarnessCell(
                proposal_id=f"complete-model-harness-cell-proposal-{index}",
                idempotency_key=f"complete-model-harness-cell-proposal-{index}",
                proposer=actor,
                approval=approval,
                cell=cell,
            ),
        )
        cells.append(cell)
    evidence_index = HarnessEvidenceSnapshotIndex.build(
        records=tuple(
            sorted(
                (fixture.record for fixture in fixtures),
                key=lambda item: item.chain_receipt.record_id,
            )
        )
    )
    analysis = analyze_model_harness(
        protocol,
        tuple(cells),
        evidence_chains=tuple(fixture.chain for fixture in fixtures),
        evidence_index=evidence_index,
    )
    _submit(
        runtime,
        RecordModelHarnessAnalysis(
            proposal_id="complete-model-harness-analysis-proposal",
            idempotency_key="complete-model-harness-analysis-proposal",
            proposer=actor,
            approval=approval,
            analysis=analysis,
        ),
    )


def test_cognitive_projection_expectations_remain_schema_one() -> None:
    expectation = WorkspaceProjectionExpectation(
        projection_kind="capability_profile_record",
        stable_identity="profile-1",
        content_hash="a" * 64,
    )

    assert expectation.schema_version == 1


def test_030_bundle_round_trip_preserves_governed_integrity_snapshot(tmp_path) -> None:
    policy = _governed_policy()
    source = _runtime(tmp_path, "cognitive-source", policy_snapshot=policy)
    target = _runtime(tmp_path, "cognitive-target", policy_snapshot=policy)
    proposal = RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-proposal",
        proposer=ActorIdentity(
            actor_id="source-service",
            kind=ActorKind.SERVICE,
            created_at=NOW,
        ),
        approval=Approval(
            approver=ActorIdentity(
                actor_id="reviewer",
                kind=ActorKind.HUMAN,
                created_at=NOW,
            ),
            approved_at=NOW,
        ),
        profile=_profile_for_policy(policy),
    )
    try:
        assert source.coordinator.submit(proposal).accepted is True
        exported = export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        legacy_payload = exported.model_dump(mode="json")
        for record in legacy_payload["records"]:
            record.pop("replay_intent")
        legacy_payload_without_hash = dict(legacy_payload)
        del legacy_payload_without_hash["bundle_hash"]
        legacy_payload["bundle_hash"] = sha256_hex(
            canonical_json_bytes(legacy_payload_without_hash)
        )
        legacy = WorkspaceExport.model_validate_json(canonical_json_bytes(legacy_payload))

        result = import_workspace(
            legacy,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        assert result.projections_verified is True
        assert (
            "capability_profile_record",
            proposal.profile.profile_id,
            proposal.profile.content_hash,
        ) in {
            (item.projection_kind, item.stable_identity, item.content_hash)
            for item in exported.projection_expectations
        }
        with source.uow_factory() as source_uow, target.uow_factory() as target_uow:
            assert (
                source_uow.repositories().cognitive_workspace_integrity_snapshot()
                == target_uow.repositories().cognitive_workspace_integrity_snapshot()
            )
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_030_bundle_round_trip_retains_direct_submit_receipt_audit_identity(tmp_path) -> None:
    policy = _governed_policy()
    source = _runtime(tmp_path, "direct-receipt-source", policy_snapshot=policy)
    target = _runtime(tmp_path, "direct-receipt-target", policy_snapshot=policy)
    try:
        _record_cognition_and_collaboration(source)
        exported = export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )

        result = import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        assert result.projections_verified is True
        assert all(record.replay_intent is not None for record in exported.records)
        with source.uow_factory() as source_uow, target.uow_factory() as target_uow:
            source_repositories = source_uow.repositories()
            target_repositories = target_uow.repositories()
            assert target_repositories.audit.list_all() == source_repositories.audit.list_all()
            assert (
                target_repositories.cognitive_workspace_integrity_snapshot()
                == source_repositories.cognitive_workspace_integrity_snapshot()
            )
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_governed_direct_submit_uses_normal_canonical_intent_identity(tmp_path) -> None:
    policy = _governed_policy()
    runtime = _runtime(tmp_path, "canonical-intent", policy_snapshot=policy)
    try:
        _record_cognition_and_collaboration(runtime)
        exported = export_workspace(
            uow_factory=runtime.uow_factory,
            artifact_store=runtime.artifact_store,
        )
        record = next(
            item for item in exported.records if type(item.proposal) is RecordCapabilityProfile
        )
        attempt = ProposalAttempt(
            proposal_id=record.proposal.proposal_id,
            idempotency_key=record.proposal.idempotency_key,
            proposer=record.proposal.proposer,
            proposal_kind=record.proposal.proposal_type,
            intent_digest=record.proposal_hash,
        )

        replay = runtime.coordinator.submit_intent(attempt, lambda: record.proposal)
        changed_profile = record.proposal.profile.model_copy(
            update={"profile_id": "changed-profile"}
        )
        changed = record.proposal.model_copy(update={"profile": changed_profile})
        changed_hash = sha256_hex(
            canonical_json_bytes(changed.model_dump(mode="json", warnings="none"))
        )
        conflict = runtime.coordinator.submit_intent(
            attempt.model_copy(update={"intent_digest": changed_hash}),
            lambda: changed,
        )

        assert replay.accepted is True and replay.replayed is True
        assert conflict.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
    finally:
        runtime.engine.dispose()


def test_internal_workspace_context_cannot_replay_changed_content(tmp_path) -> None:
    policy = _governed_policy()
    runtime = _runtime(tmp_path, "spoofed-replay-context", policy_snapshot=policy)
    try:
        _record_cognition_and_collaboration(runtime)
        exported = export_workspace(
            uow_factory=runtime.uow_factory,
            artifact_store=runtime.artifact_store,
        )
        record = next(
            item for item in exported.records if type(item.proposal) is RecordCapabilityProfile
        )
        assert record.replay_intent is not None
        changed = record.proposal.model_copy(
            update={
                "profile": record.proposal.profile.model_copy(
                    update={"profile_id": "spoofed-profile"}
                )
            }
        )
        attempt = ProposalAttempt(
            proposal_id=record.proposal.proposal_id,
            idempotency_key=record.proposal.idempotency_key,
            proposer=record.proposal.proposer,
            proposal_kind=record.proposal.proposal_type,
            intent_digest=record.proposal_hash,
        )

        def context(
            proposal: Proposal,
            *,
            proposal_hash: str = record.proposal_hash,
            intent_fingerprint: str | None = record.replay_intent.intent_fingerprint,
            audit_event_hash: str = record.replay_intent.audit_event_hash,
        ) -> coordinator_module._WorkspaceReplayProposalFactory:
            return coordinator_module._WorkspaceReplayProposalFactory(
                proposal=proposal,
                proposal_hash=proposal_hash,
                intent_fingerprint=intent_fingerprint,
                governing_policy_hash=record.governing_policy_hash,
                expected_decision=record.expected_decision,
                audit_event_id=record.replay_intent.audit_event_id,
                audit_event_hash=audit_event_hash,
            )

        decisions = (
            runtime.coordinator.submit_intent(attempt, context(changed)),
            runtime.coordinator.submit_intent(
                attempt.model_copy(update={"intent_digest": "f" * 64}),
                context(record.proposal),
            ),
            runtime.coordinator.submit_intent(
                attempt,
                context(record.proposal, intent_fingerprint=None),
            ),
            runtime.coordinator.submit_intent(
                attempt,
                context(record.proposal, intent_fingerprint="e" * 64),
            ),
            runtime.coordinator.submit_intent(
                attempt,
                context(record.proposal, audit_event_hash="d" * 64),
            ),
        )

        assert all(decision.replayed is False for decision in decisions)
        assert all(
            decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT for decision in decisions
        )
        ordinary_replay = runtime.coordinator.submit_intent(
            attempt,
            context(record.proposal, proposal_hash="c" * 64),
        )
        assert ordinary_replay.accepted is True and ordinary_replay.replayed is True
    finally:
        runtime.engine.dispose()


def test_030_bundle_round_trip_preserves_complete_18_family_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _governed_policy()
    source = _runtime(tmp_path, "complete-source", policy_snapshot=policy)
    target = _runtime(tmp_path, "complete-target", policy_snapshot=policy)
    try:
        _record_cognition_and_collaboration(source)
        _record_procedure(source)
        _record_evaluation_extensions(source, monkeypatch)

        exported = export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        governed_expectations = tuple(
            item
            for item in exported.projection_expectations
            if item.projection_kind.endswith("_record")
        )
        assert len({item.projection_kind for item in governed_expectations}) == 18

        result = import_workspace(
            exported,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        assert result.projections_verified is True
        with source.uow_factory() as source_uow, target.uow_factory() as target_uow:
            source_snapshot = source_uow.repositories().cognitive_workspace_integrity_snapshot()
            target_snapshot = target_uow.repositories().cognitive_workspace_integrity_snapshot()
        assert target_snapshot == source_snapshot
    finally:
        source.engine.dispose()
        target.engine.dispose()


def test_020_bundle_import_creates_no_synthetic_0007_records(tmp_path) -> None:
    source = _runtime(tmp_path, "legacy-source")
    target = _runtime(tmp_path, "legacy-target")
    try:
        source.add_evidence(b"legacy 0.2 workspace evidence")
        exported = export_workspace(
            uow_factory=source.uow_factory,
            artifact_store=source.artifact_store,
        )
        legacy_payload = exported.model_dump(mode="json")
        for record in legacy_payload["records"]:
            record.pop("replay_intent")
        legacy_payload_without_hash = dict(legacy_payload)
        del legacy_payload_without_hash["bundle_hash"]
        legacy_payload["bundle_hash"] = sha256_hex(
            canonical_json_bytes(legacy_payload_without_hash)
        )
        legacy = WorkspaceExport.model_validate_json(canonical_json_bytes(legacy_payload))

        result = import_workspace(
            legacy,
            uow_factory=target.uow_factory,
            artifact_store=target.artifact_store,
            source_artifact_store=source.artifact_store,
            clock=FixedClock(),
        )

        assert result.projections_verified is True
        assert (
            tuple(
                item
                for item in legacy.projection_expectations
                if item.projection_kind.endswith("_record")
            )
            == ()
        )
        with target.uow_factory() as target_uow:
            governed = target_uow.repositories().cognitive_workspace_integrity_snapshot()
        assert governed.cognitive.capability_profiles == ()
        assert governed.cognitive.cohort_plans == ()
        assert governed.cognitive.diversity_assessments == ()
        assert governed.cognitive.collaboration_sessions == ()
        assert governed.cognitive.peer_requests == ()
        assert governed.cognitive.peer_contributions == ()
        assert governed.cognitive.topology_events == ()
        assert governed.cognitive.terminations == ()
        assert governed.cognitive.compilations == ()
        assert governed.cognitive.method_outcomes == ()
        assert governed.cognitive.bindings == ()
        assert governed.evaluation_extension.guidance_protocols == ()
        assert governed.evaluation_extension.guidance_cells == ()
        assert governed.evaluation_extension.model_harness_protocols == ()
        assert governed.evaluation_extension.model_harness_cells == ()
        assert governed.evaluation_extension.model_harness_analyses == ()
        assert governed.evaluation_extension.harness_execution_traces == ()
        assert governed.evaluation_extension.reward_assessments == ()
    finally:
        source.engine.dispose()
        target.engine.dispose()
