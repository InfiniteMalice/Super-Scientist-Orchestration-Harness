from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from super_scientist.application.procedures.service import (
    BindCompiledProgressPlanHandler,
    RecordMethodDirectionOutcomeHandler,
    RecordProcedureCompilationHandler,
)
from super_scientist.application.progress.service import RecordProgressPlanHandler
from super_scientist.application.transactions.procedures import (
    fixed_procedure_handlers,
    procedure_capabilities,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.cognition.grounding import assess_capability
from super_scientist.domain.cognition.models import CapabilityProfile
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.domain.procedures import (
    AcceptedSourceReceiptRef,
    CompiledProgressPlanBinding,
    GroundedCapabilityAssessment,
    MethodDirectionOutcome,
    MethodDirectionStatus,
    OpaqueProcedureCompilationEnvelope,
    ProcedureAuthority,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureCompilationRequest,
    ProcedureEvidenceSourceKind,
    ProcedureValidationStatus,
    canonical_model_hash,
    compile_method,
    procedure_to_progress_plan,
)
from super_scientist.domain.progress.models import ProgressPlan, ProgressSubtask
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    BindCompiledProgressPlan,
    RecordCapabilityProfile,
    RecordMethodDirectionOutcome,
    RecordProcedureCompilation,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.cognitive_records import (
    CapabilityProfileRepository,
    CompiledProgressPlanBindingRepository,
    ProcedureCompilationRepository,
)
from super_scientist.providers.storage.domain_records import (
    ProgressPlanRepository,
    ProgressSubtaskRepository,
)
from super_scientist.providers.storage.procedure_sources import (
    ProcedureSourceBinding,
    ProcedureSourceSnapshot,
)
from tests.integration.application.test_progress_service import _research_run
from tests.integration.storage.test_procedure_source_repositories import _persist_accepted
from tests.unit.procedures.test_compiler import _rebuild_step, _replace_step, valid_request

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
pytest_plugins = ("tests.integration.application.test_progress_service",)


def test_fixed_procedure_handler_set_is_closed_and_unique() -> None:
    handlers = fixed_procedure_handlers()

    assert tuple(handler.proposal_type for handler in handlers) == (
        "record_procedure_compilation",
        "record_method_direction_outcome",
        "bind_compiled_progress_plan",
    )


@pytest.mark.integration
def test_concrete_capability_resolves_exact_task10_sources(v2_runtime) -> None:
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        repositories = unit_of_work.repositories()
        request = _retain_exact_sources(
            repositories,
            connection,
            v2_runtime.artifact_store,
            v2_runtime.policy.policy_hash,
        )
        result = compile_method(request)
        proposal = RecordProcedureCompilation(
            proposal_id="exact-source-compilation",
            idempotency_key="exact-source-compilation",
            proposer=v2_runtime.proposer,
            compilation=OpaqueProcedureCompilationEnvelope.build(
                compilation_id="exact-source-compilation",
                result=result,
                created_at=NOW,
                governing_policy_hash=v2_runtime.policy.policy_hash,
            ),
        )
        capabilities = procedure_capabilities(
            proposal,
            connection,
            v2_runtime.policy,
            v2_runtime.artifact_store,
            current_transaction_created_at=NOW,
        )
        handler = RecordProcedureCompilationHandler()

        decision = handler.decide(
            proposal,
            handler.build_context(proposal, capabilities),
        )

    assert decision.accepted is True


@pytest.mark.integration
def test_valid_binding_projects_plan_and_binding_in_one_unit_of_work(v2_runtime) -> None:
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        repositories = unit_of_work.repositories()
        request = _retain_exact_sources(
            repositories,
            connection,
            v2_runtime.artifact_store,
            v2_runtime.policy.policy_hash,
        )
        result = compile_method(request)
        compilation_proposal = RecordProcedureCompilation(
            proposal_id="accepted-compilation-proposal",
            idempotency_key="accepted-compilation-proposal",
            proposer=v2_runtime.proposer,
            compilation=OpaqueProcedureCompilationEnvelope.build(
                compilation_id="accepted-compilation",
                result=result,
                created_at=NOW,
                governing_policy_hash=v2_runtime.policy.policy_hash,
            ),
        )
        stored, event = _persist_accepted_at_policy(
            repositories,
            compilation_proposal,
            v2_runtime.policy.policy_hash,
        )
        compilations = ProcedureCompilationRepository(connection)
        compilations.add_from_proposal(
            compilation_proposal,
            created_at=NOW,
            transaction_id=compilation_proposal.proposal_id,
            governing_policy_hash=v2_runtime.policy.policy_hash,
        )
        compilation = compilations.get("accepted-compilation")
        assert compilation is not None
        receipt = ProcedureCompilationReceiptRef(
            proposal_id=compilation_proposal.proposal_id,
            proposal_hash=stored.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )
        plan = procedure_to_progress_plan(
            result,
            run_id="run-1",
            plan_version_id="compiled-plan",
            version=1,
            created_at=NOW,
            governing_policy_hash=v2_runtime.policy.policy_hash,
        )
        binding = CompiledProgressPlanBinding.build(
            binding_id="accepted-binding",
            compilation_receipt=receipt,
            compilation_id=compilation.compilation_id,
            compilation_hash=compilation.content_hash,
            procedure_id=result.procedure.procedure_id,
            procedure_hash=result.procedure.content_hash,
            plan=plan,
            plan_hash=canonical_model_hash(plan),
            created_at=NOW,
            governing_policy_hash=v2_runtime.policy.policy_hash,
        )
        proposal = BindCompiledProgressPlan(
            proposal_id="accepted-binding-proposal",
            idempotency_key="accepted-binding-proposal",
            proposer=v2_runtime.proposer,
            approval=v2_runtime.approval(),
            compilation_receipt=receipt,
            binding=binding,
            plan=plan,
        )
        capabilities = procedure_capabilities(
            proposal,
            connection,
            v2_runtime.policy,
            v2_runtime.artifact_store,
            current_transaction_created_at=NOW,
        )
        handler = BindCompiledProgressPlanHandler()
        context = handler.build_context(proposal, capabilities)
        decision = handler.decide(proposal, context)
        assert decision.accepted is True
        _persist_accepted_at_policy(
            repositories,
            proposal,
            v2_runtime.policy.policy_hash,
        )

        handler.project(proposal, decision, capabilities)

        assert ProgressPlanRepository(connection).get(plan.plan_version_id) == plan
        assert CompiledProgressPlanBindingRepository(connection).get(binding.binding_id) == binding


@pytest.mark.integration
def test_binding_projection_rolls_back_plan_when_binding_append_fails(v2_runtime) -> None:
    result = compile_method(valid_request())
    receipt = ProcedureCompilationReceiptRef(
        proposal_id="rollback-compilation-proposal",
        proposal_hash="a" * 64,
        audit_event_id="rollback-compilation-audit",
        audit_event_hash="b" * 64,
    )
    plan = procedure_to_progress_plan(
        result,
        run_id="run-1",
        plan_version_id="rollback-plan",
        version=1,
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    binding = CompiledProgressPlanBinding.build(
        binding_id="rollback-binding",
        compilation_receipt=receipt,
        compilation_id="rollback-compilation",
        compilation_hash="c" * 64,
        procedure_id=result.procedure.procedure_id,
        procedure_hash=result.procedure.content_hash,
        plan=plan,
        plan_hash=canonical_model_hash(plan),
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    proposal = BindCompiledProgressPlan(
        proposal_id="rollback-binding-proposal",
        idempotency_key="rollback-binding-proposal",
        proposer=v2_runtime.proposer,
        approval=v2_runtime.approval(),
        compilation_receipt=receipt,
        binding=binding,
        plan=plan,
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    with (
        pytest.raises(RuntimeError, match="forced binding failure"),
        v2_runtime.uow_factory() as unit_of_work,
    ):
        connection = unit_of_work.connection
        assert connection is not None
        handler = BindCompiledProgressPlanHandler()
        handler.project(
            proposal,
            decision,
            _ExplodingBindingWrites(connection),
        )

    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        assert ProgressPlanRepository(connection).get(plan.plan_version_id) is None
        assert all(
            ProgressSubtaskRepository(connection).get(subtask.subtask_id) is None
            for subtask in plan.subtasks
        )


@dataclass(frozen=True)
class _ExplodingBindingWrites:
    connection: object

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, ProgressPlan):
            ProgressPlanRepository(self.connection).add(
                record.plan_version_id,
                record,
                record.created_at,
            )
            return
        if isinstance(record, ProgressSubtask):
            ProgressSubtaskRepository(self.connection).add(
                record.subtask_id,
                record,
                NOW,
            )
            return
        if isinstance(record, CompiledProgressPlanBinding):
            raise RuntimeError("forced binding failure")
        raise AssertionError(f"unexpected projection {record!r}")

    def update_projection(self, record: BaseModel) -> None:
        raise AssertionError(f"unexpected mutable projection {record!r}")


@dataclass
class _CompilationCapabilities:
    policy: PolicySnapshot
    sources_current: bool = True
    existing: ProcedureCompilationRecord | None = None
    records: list[BaseModel] = field(default_factory=list)

    def policy_snapshot(self) -> PolicySnapshot:
        return self.policy

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None:
        del compilation_id
        return self.existing

    def procedure_sources_are_current(self, request: object) -> bool:
        del request
        return self.sources_current

    def append_authoritative(self, record: BaseModel) -> None:
        self.records.append(record)

    def update_projection(self, record: BaseModel) -> None:
        raise AssertionError(f"unexpected projection: {record!r}")


def _invalid_compilation_proposal(policy: PolicySnapshot) -> RecordProcedureCompilation:
    request = valid_request()
    forbidden = _rebuild_step(
        request.candidate.stages[0],
        required_authorities=(ProcedureAuthority.GOVERNANCE_WRITE,),
    )
    result = compile_method(_replace_step(request, 0, forbidden))
    assert result.report.status is ProcedureValidationStatus.INVALID
    return RecordProcedureCompilation(
        proposal_id="record-invalid-compilation",
        idempotency_key="record-invalid-compilation",
        proposer=_actor("compiler"),
        compilation=OpaqueProcedureCompilationEnvelope.build(
            compilation_id="compilation-invalid",
            result=result,
            created_at=NOW,
            governing_policy_hash=policy.policy_hash,
        ),
    )


@pytest.mark.integration
def test_invalid_compilation_is_history_but_creates_no_plan(v2_runtime) -> None:
    proposal = _invalid_compilation_proposal(v2_runtime.policy)
    capabilities = _CompilationCapabilities(v2_runtime.policy)
    handler = RecordProcedureCompilationHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))
    assert decision.accepted is True
    handler.project(proposal, decision, capabilities)

    assert [type(item) for item in capabilities.records] == [ProcedureCompilationRecord]
    assert capabilities.records[0].compilation_id == "compilation-invalid"
    assert not any(
        isinstance(item, (ProgressPlan, ProgressSubtask)) for item in capabilities.records
    )


@pytest.mark.integration
def test_stale_sources_reject_before_invalid_history_is_persisted(v2_runtime) -> None:
    proposal = _invalid_compilation_proposal(v2_runtime.policy)
    capabilities = _CompilationCapabilities(v2_runtime.policy, sources_current=False)
    handler = RecordProcedureCompilationHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.STALE_REFERENCE
    assert capabilities.records == []


@pytest.mark.integration
def test_opaque_boundary_failure_returns_only_fixed_invalid_procedure(v2_runtime) -> None:
    proposal = _invalid_compilation_proposal(v2_runtime.policy)
    private_marker = "PRIVATE-COMPILATION-PAYLOAD"
    forged_envelope = proposal.compilation.model_copy(update={"result_json_base64": private_marker})
    forged = proposal.model_copy(update={"compilation": forged_envelope})
    capabilities = _CompilationCapabilities(v2_runtime.policy)
    handler = RecordProcedureCompilationHandler()

    decision = handler.decide(forged, handler.build_context(forged, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_PROCEDURE
    assert private_marker not in str(decision)
    assert capabilities.records == []


@dataclass
class _BindingCapabilities(_CompilationCapabilities):
    compilation: ProcedureCompilationRecord | None = None
    receipt_compilation: ProcedureCompilationRecord | None = None
    existing_binding: CompiledProgressPlanBinding | None = None
    progress_reads: object | None = None

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None:
        if self.compilation is not None and self.compilation.compilation_id == compilation_id:
            return self.compilation
        return None

    def resolve_compilation_receipt(
        self,
        receipt: ProcedureCompilationReceiptRef,
    ) -> ProcedureCompilationRecord | None:
        del receipt
        return self.receipt_compilation

    def get_binding(self, binding_id: str) -> CompiledProgressPlanBinding | None:
        del binding_id
        return self.existing_binding

    def progress_capability(self) -> object:
        assert self.progress_reads is not None
        return self.progress_reads


@pytest.mark.integration
def test_binding_rejects_invalid_compilation_without_projecting_plan(v2_runtime) -> None:
    compilation_proposal = _invalid_compilation_proposal(v2_runtime.policy)
    compilation = ProcedureCompilationRecord.build_from_untrusted_envelope(
        compilation_proposal.compilation
    )
    result = compilation.result
    plan = procedure_to_progress_plan(
        compile_method(valid_request()),
        run_id="run-1",
        plan_version_id="compiled-plan-1",
        version=1,
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    receipt = ProcedureCompilationReceiptRef(
        proposal_id=compilation_proposal.proposal_id,
        proposal_hash="a" * 64,
        audit_event_id="compilation-audit",
        audit_event_hash="b" * 64,
    )
    binding = CompiledProgressPlanBinding.build(
        binding_id="binding-invalid",
        compilation_receipt=receipt,
        compilation_id=compilation.compilation_id,
        compilation_hash=compilation.content_hash,
        procedure_id=result.procedure.procedure_id,
        procedure_hash=result.procedure.content_hash,
        plan=plan,
        plan_hash=canonical_model_hash(plan),
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    proposal = BindCompiledProgressPlan(
        proposal_id="bind-invalid",
        idempotency_key="bind-invalid",
        proposer=v2_runtime.proposer,
        approval=v2_runtime.approval(),
        compilation_receipt=receipt,
        binding=binding,
        plan=plan,
    )
    progress_reads = _ProgressReads(v2_runtime.policy, _research_run(v2_runtime))
    capabilities = _BindingCapabilities(
        policy=v2_runtime.policy,
        compilation=compilation,
        receipt_compilation=compilation,
        progress_reads=progress_reads,
    )
    handler = BindCompiledProgressPlanHandler(RecordProgressPlanHandler())

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_PROCEDURE
    assert capabilities.records == []


@dataclass
class _ProgressReads:
    policy: PolicySnapshot
    run: ResearchRun

    def policy_snapshot(self) -> PolicySnapshot:
        return self.policy

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.run if self.run.run_id == run_id else None

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None:
        del plan_version_id
        return None

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]:
        del run_id
        return ()

    def list_subtasks(self, subtask_ids: tuple[str, ...]) -> tuple[ProgressSubtask, ...]:
        del subtask_ids
        return ()


@pytest.mark.integration
def test_method_terminal_outcome_requires_all_references(v2_runtime) -> None:
    compilation = ProcedureCompilationRecord.build_from_untrusted_envelope(
        _invalid_compilation_proposal(v2_runtime.policy).compilation
    )
    outcome = MethodDirectionOutcome.build(
        outcome_id="outcome-1",
        status=MethodDirectionStatus.UNSUPPORTED,
        evidence_refs=(),
        failed_method_ids=(compilation.result.procedure.source_candidate.method_id,),
        rejected_procedure_ids=(compilation.result.procedure.procedure_id,),
        budget_reference_ids=("missing-budget",),
        terminal_rule="Stop after independent rejection",
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    proposal = RecordMethodDirectionOutcome(
        proposal_id="outcome-proposal",
        idempotency_key="outcome-proposal",
        proposer=_actor("coordinator"),
        compilation_id=compilation.compilation_id,
        outcome=outcome,
    )
    capabilities = _OutcomeCapabilities(v2_runtime.policy, compilation)
    handler = RecordMethodDirectionOutcomeHandler()

    decision = handler.decide(proposal, handler.build_context(proposal, capabilities))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_ENTITY
    assert capabilities.records == []


@dataclass
class _OutcomeCapabilities(_CompilationCapabilities):
    compilation: ProcedureCompilationRecord | None = None

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None:
        if self.compilation is not None and self.compilation.compilation_id == compilation_id:
            return self.compilation
        return None

    def get_outcome(self, outcome_id: str) -> MethodDirectionOutcome | None:
        del outcome_id
        return None

    def retained_evidence_exists(self, reference: object) -> bool:
        del reference
        return True

    def budget_exists(self, budget_id: str) -> bool:
        del budget_id
        return False


def _actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def _retain_exact_sources(
    repositories,
    connection,
    artifact_store,
    governing_policy_hash: str,
) -> ProcedureCompilationRequest:
    base = valid_request()
    capability_snapshot_id, capability_snapshot_hash = _retain_source_snapshot(
        repositories,
        artifact_store,
        snapshot_id="capability-evidence-snapshot",
        bindings=(),
    )
    original_grounded = base.capability_assessments[0]
    original_profile = original_grounded.profile
    assertion = original_profile.assertions[0].model_copy(
        update={"evidence_snapshot_hash": capability_snapshot_hash}
    )
    profile_values = original_profile.model_dump(mode="python", exclude={"content_hash"})
    profile_values["assertions"] = (assertion,)
    profile_values["governing_policy_hash"] = governing_policy_hash
    retained_profile = CapabilityProfile.build(**profile_values)
    requirement = original_grounded.assessment.requirement.model_copy(
        update={"evidence_snapshot_hash": capability_snapshot_hash}
    )
    assessment = assess_capability(retained_profile, requirement)
    profile_proposal = RecordCapabilityProfile(
        proposal_id="accepted-capability-profile",
        idempotency_key="accepted-capability-profile",
        proposer=_actor("source-recorder"),
        profile=retained_profile,
    )
    stored_profile, profile_event = _persist_accepted_at_policy(
        repositories,
        profile_proposal,
        retained_profile.governing_policy_hash,
    )
    CapabilityProfileRepository(connection).add_from_proposal(
        profile_proposal,
        created_at=NOW,
        transaction_id=profile_proposal.proposal_id,
        governing_policy_hash=retained_profile.governing_policy_hash,
    )
    capability_receipt = AcceptedSourceReceiptRef.build(
        receipt_id="accepted-capability-receipt",
        source_kind=ProcedureEvidenceSourceKind.CAPABILITY_PROFILE,
        source_record_id=retained_profile.profile_id,
        source_schema_version=retained_profile.schema_version,
        source_content_hash=retained_profile.content_hash,
        source_snapshot_id=capability_snapshot_id,
        source_snapshot_hash=capability_snapshot_hash,
        proposal_id=profile_proposal.proposal_id,
        proposal_hash=stored_profile.proposal_hash,
        audit_event_id=profile_event.event_id,
        audit_event_hash=profile_event.event_hash,
    )
    grounded = GroundedCapabilityAssessment.build(
        profile=retained_profile,
        assessment=assessment,
        profile_receipt=capability_receipt,
    )

    catalog_specs = (
        (
            ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
            base.artifact_catalog,
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
    retained_catalogs = []
    for index, (kind, entries, complete) in enumerate(catalog_specs, start=1):
        catalog_bytes = canonical_json_bytes(
            {
                "catalog_kind": kind.value,
                "entries": tuple(item.model_dump(mode="json") for item in entries),
                "complete": complete,
            }
        )
        catalog_artifact = artifact_store.put(catalog_bytes, "application/json")
        evidence = _source_evidence(
            f"accepted-{kind.value.lower()}",
            catalog_artifact,
        )
        proposal = AddEvidence(
            proposal_id=f"accepted-catalog-proposal-{index}",
            idempotency_key=f"accepted-catalog-proposal-{index}",
            proposer=_actor("source-recorder"),
            evidence=evidence,
        )
        repositories.evidence.add(evidence)
        stored, event = _persist_accepted(repositories, proposal, NOW)
        retained_catalogs.append((kind, entries, complete, evidence, proposal, stored, event))

    catalog_snapshot_id, catalog_snapshot_hash = _retain_source_snapshot(
        repositories,
        artifact_store,
        snapshot_id="current-catalog-snapshot",
        bindings=tuple(
            sorted(
                (
                    ProcedureSourceBinding(
                        source_record_id=evidence.evidence_id,
                        source_content_hash=evidence.artifact.sha256,
                    )
                    for (
                        _kind,
                        _entries,
                        _complete,
                        evidence,
                        _proposal,
                        _stored,
                        _event,
                    ) in retained_catalogs
                ),
                key=lambda item: item.source_record_id,
            )
        ),
    )
    receipts = []
    for kind, _entries, _complete, evidence, proposal, stored, event in retained_catalogs:
        receipts.append(
            AcceptedSourceReceiptRef.build(
                receipt_id=f"accepted-{kind.value.lower()}-receipt",
                source_kind=kind,
                source_record_id=evidence.evidence_id,
                source_schema_version=1,
                source_content_hash=evidence.artifact.sha256,
                source_snapshot_id=catalog_snapshot_id,
                source_snapshot_hash=catalog_snapshot_hash,
                proposal_id=proposal.proposal_id,
                proposal_hash=stored.proposal_hash,
                audit_event_id=event.event_id,
                audit_event_hash=event.event_hash,
            )
        )

    values = base.model_dump(mode="python")
    values.update(
        capability_assessments=(grounded,),
        artifact_catalog_receipt=receipts[0],
        tool_catalog_receipt=receipts[1],
        validator_catalog_receipt=receipts[2],
    )
    return ProcedureCompilationRequest.model_validate(values, strict=True)


def _retain_source_snapshot(
    repositories,
    artifact_store,
    *,
    snapshot_id: str,
    bindings: tuple[ProcedureSourceBinding, ...],
) -> tuple[str, str]:
    snapshot = ProcedureSourceSnapshot(
        snapshot_family_id=snapshot_id,
        snapshot_id=snapshot_id,
        source_bindings=bindings,
    )
    artifact = artifact_store.put(
        canonical_json_bytes(snapshot.model_dump(mode="json")),
        "application/json",
    )
    evidence = _source_evidence(snapshot_id, artifact)
    proposal = AddEvidence(
        proposal_id=f"proposal-{snapshot_id}",
        idempotency_key=f"proposal-{snapshot_id}",
        proposer=_actor("source-recorder"),
        evidence=evidence,
    )
    repositories.evidence.add(evidence)
    _persist_accepted(repositories, proposal, NOW)
    return snapshot_id, artifact.sha256


def _source_evidence(evidence_id: str, artifact) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="procedure-source",
        source_locator=f"fixture:{evidence_id}",
        retrieved_at=NOW,
        artifact=artifact,
        provenance={"fixture": "task-12"},
        ingestion_actor_id="source-recorder",
        verification_state=VerificationState.HASH_VERIFIED,
    )


def _persist_accepted_at_policy(repositories, proposal, policy_hash: str):
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    repositories.transactions.add(proposal, decision, NOW)
    stored = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
    assert stored is not None
    event = append_event(
        repositories.audit.last(),
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": policy_hash,
            "stored_policy_hash": policy_hash,
            "transaction_persisted": True,
        },
        NOW,
    )
    repositories.audit.add(event)
    return stored, event
