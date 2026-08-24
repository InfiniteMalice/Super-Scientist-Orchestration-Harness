from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import Connection

from super_scientist.application.procedures.service import (
    BindCompiledProgressPlanHandler,
    RecordMethodDirectionOutcomeHandler,
    RecordProcedureCompilationHandler,
)
from super_scientist.application.transactions.contracts import ProposalHandler
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.cognition.grounding import assess_capability
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.primitives import Sha256Hex, UtcTimestamp
from super_scientist.domain.procedures import (
    CompiledProgressPlanBinding,
    MethodDirectionOutcome,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureCompilationRequest,
    ProcedureEvidenceSourceKind,
)
from super_scientist.domain.progress.models import ProgressPlan, ProgressSubtask
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    BindCompiledProgressPlan,
    RecordMethodDirectionOutcome,
    RecordProcedureCompilation,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.cognitive_records import (
    CapabilityProfileRepository,
    CompiledProgressPlanBindingRepository,
    MethodDirectionOutcomeRepository,
    ProcedureCompilationRepository,
)
from super_scientist.providers.storage.domain_records import (
    ProgressPlanRepository,
    ProgressSubtaskRepository,
    ResearchRunRepository,
    RunBudgetRepository,
)
from super_scientist.providers.storage.procedure_sources import (
    AcceptedProcedureSourceReceiptReader,
    ArtifactCatalogSnapshotRepository,
    ProcedureSourceSnapshot,
    ProcedureSourceSnapshotRepository,
    ToolCatalogSnapshotRepository,
    ValidatorCatalogSnapshotRepository,
)
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    EvidenceRepository,
    TransactionRepository,
)

type FixedProcedureHandler = ProposalHandler[BaseModel, BaseModel]

_SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


@dataclass(frozen=True, slots=True)
class _ProcedureSourceReaders:
    accepted: AcceptedProcedureSourceReceiptReader
    profiles: CapabilityProfileRepository
    artifacts: ArtifactCatalogSnapshotRepository
    tools: ToolCatalogSnapshotRepository
    validators: ValidatorCatalogSnapshotRepository
    snapshots: ProcedureSourceSnapshotRepository

    def all_current(self, request: ProcedureCompilationRequest) -> bool:
        capability_items = tuple(
            (item.profile_receipt, item) for item in request.capability_assessments
        )
        catalog_items = (
            (
                request.artifact_catalog_receipt,
                ProcedureEvidenceSourceKind.ARTIFACT_CATALOG,
                request.artifact_catalog,
                request.artifact_catalog_complete,
            ),
            (
                request.tool_catalog_receipt,
                ProcedureEvidenceSourceKind.TOOL_CATALOG,
                request.tool_catalog,
                request.tool_catalog_complete,
            ),
            (
                request.validator_catalog_receipt,
                ProcedureEvidenceSourceKind.VALIDATOR_CATALOG,
                request.validator_catalog,
                request.validator_catalog_complete,
            ),
        )
        references = tuple(item[0] for item in capability_items) + tuple(
            item[0] for item in catalog_items
        )
        receipt_ids = tuple(item.receipt_id for item in references)
        if len(set(receipt_ids)) != len(receipt_ids):
            return False
        catalog_snapshot_keys = {
            (reference.source_snapshot_id, reference.source_snapshot_hash)
            for reference, _kind, _entries, _complete in catalog_items
        }
        if len(catalog_snapshot_keys) != 1:
            return False
        if any(self.accepted.resolve(reference) is None for reference in references):
            return False
        snapshot_keys = {
            (reference.source_snapshot_id, reference.source_snapshot_hash)
            for reference in references
        }
        snapshots: dict[tuple[str, str], ProcedureSourceSnapshot] = {}
        for snapshot_id, snapshot_hash in snapshot_keys:
            snapshot = self.snapshots.resolve_exact(snapshot_id, snapshot_hash)
            if snapshot is None or not self.snapshots.is_current(snapshot_id, snapshot_hash):
                return False
            snapshots[(snapshot_id, snapshot_hash)] = snapshot
        for reference, _kind, _entries, _complete in catalog_items:
            snapshot = snapshots[(reference.source_snapshot_id, reference.source_snapshot_hash)]
            matching_bindings = tuple(
                binding
                for binding in snapshot.source_bindings
                if binding.source_record_id == reference.source_record_id
            )
            if (
                len(matching_bindings) != 1
                or matching_bindings[0].source_content_hash != reference.source_content_hash
            ):
                return False
        for reference, grounded in capability_items:
            retained = self.profiles.resolve(reference)
            if (
                retained != grounded.profile
                or assess_capability(retained, grounded.assessment.requirement)
                != grounded.assessment
            ):
                return False
        for reference, kind, expected_entries, expected_complete in catalog_items:
            if kind is ProcedureEvidenceSourceKind.ARTIFACT_CATALOG:
                artifact_source = self.artifacts.resolve(reference)
                if (
                    artifact_source is None
                    or artifact_source.entries != expected_entries
                    or artifact_source.complete is not expected_complete
                ):
                    return False
            elif kind is ProcedureEvidenceSourceKind.TOOL_CATALOG:
                tool_source = self.tools.resolve(reference)
                if (
                    tool_source is None
                    or tool_source.entries != expected_entries
                    or tool_source.complete is not expected_complete
                ):
                    return False
            else:
                validator_source = self.validators.resolve(reference)
                if (
                    validator_source is None
                    or validator_source.entries != expected_entries
                    or validator_source.complete is not expected_complete
                ):
                    return False
        return True


@dataclass(frozen=True, slots=True)
class ProcedureCompilationCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordProcedureCompilation
    compilations: ProcedureCompilationRepository
    sources: _ProcedureSourceReaders
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None:
        return self.compilations.get(compilation_id)

    def procedure_sources_are_current(self, request: ProcedureCompilationRequest) -> bool:
        return self.sources.all_current(request)

    def append_authoritative(self, record: BaseModel) -> None:
        expected = ProcedureCompilationRecord.build_from_untrusted_envelope(
            self.proposal.compilation
        )
        if record != expected:
            raise TypeError("procedure compilation projection does not match its proposal")
        self.compilations.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("procedure compilations have no mutable projection")


@dataclass(frozen=True, slots=True)
class MethodDirectionCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordMethodDirectionOutcome
    compilations: ProcedureCompilationRepository
    outcomes: MethodDirectionOutcomeRepository
    evidence: EvidenceRepository
    budgets: RunBudgetRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None:
        return self.compilations.get(compilation_id)

    def get_outcome(self, outcome_id: str) -> MethodDirectionOutcome | None:
        return self.outcomes.get(outcome_id)

    def retained_evidence_exists(self, reference: ArtifactRef) -> bool:
        return any(item.artifact == reference for item in self.evidence.list_all())

    def budget_exists(self, budget_id: str) -> bool:
        return self.budgets.get(budget_id) is not None

    def append_authoritative(self, record: BaseModel) -> None:
        if record != self.proposal.outcome:
            raise TypeError("method direction projection does not match its proposal")
        self.outcomes.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("method direction outcomes have no mutable projection")


@dataclass(frozen=True, slots=True)
class ProcedureBindingCapabilities:
    active_policy: PolicySnapshot
    proposal: BindCompiledProgressPlan
    compilations: ProcedureCompilationRepository
    bindings: CompiledProgressPlanBindingRepository
    transactions: TransactionRepository
    audit: AuditRepository
    sources: _ProcedureSourceReaders
    runs: ResearchRunRepository
    plans: ProgressPlanRepository
    subtasks: ProgressSubtaskRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None:
        return self.compilations.get(compilation_id)

    def procedure_sources_are_current(self, request: ProcedureCompilationRequest) -> bool:
        return self.sources.all_current(request)

    def resolve_compilation_receipt(
        self,
        receipt: ProcedureCompilationReceiptRef,
    ) -> ProcedureCompilationRecord | None:
        transaction = self.transactions.get_by_proposal_id(receipt.proposal_id)
        if (
            transaction is None
            or not transaction.decision.accepted
            or transaction.proposal_hash != receipt.proposal_hash
            or not isinstance(transaction.proposal, RecordProcedureCompilation)
        ):
            return None
        matching_events = tuple(
            event
            for event in self.audit.list_all()
            if event.event_id == receipt.audit_event_id
            and event.event_hash == receipt.audit_event_hash
            and _audit_event_matches_compilation(
                event,
                transaction.proposal,
                transaction.decision,
                self.active_policy.policy_hash,
            )
        )
        if len(matching_events) != 1:
            return None
        try:
            resolved = ProcedureCompilationRecord.build_from_untrusted_envelope(
                transaction.proposal.compilation
            )
        except (TypeError, ValueError):
            return None
        stored = self.compilations.get(resolved.compilation_id)
        return stored if stored == resolved else None

    def get_binding(self, binding_id: str) -> CompiledProgressPlanBinding | None:
        return self.bindings.get(binding_id)

    def progress_capability(self) -> ProcedureBindingCapabilities:
        return self

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self.runs.get(run_id)

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None:
        return self.plans.get(plan_version_id)

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]:
        return self.plans.list_for_run(run_id)

    def list_subtasks(self, subtask_ids: tuple[str, ...]) -> tuple[ProgressSubtask, ...]:
        return self.subtasks.get_many(subtask_ids)

    def append_authoritative(self, record: BaseModel) -> None:
        if isinstance(record, ProgressPlan):
            if record != self.proposal.plan:
                raise TypeError("compiled progress plan projection does not match its proposal")
            self.plans.add(record.plan_version_id, record, record.created_at)
            return
        if isinstance(record, ProgressSubtask):
            if record not in self.proposal.plan.subtasks:
                raise TypeError("compiled progress subtask is not declared by its plan")
            self.subtasks.add(record.subtask_id, record, self.created_at)
            return
        if isinstance(record, CompiledProgressPlanBinding):
            if record != self.proposal.binding:
                raise TypeError("compiled progress binding projection does not match its proposal")
            self.bindings.add_from_proposal(
                self.proposal,
                created_at=self.created_at,
                transaction_id=self.proposal.proposal_id,
                governing_policy_hash=self.active_policy.policy_hash,
            )
            return
        raise TypeError(f"unsupported compiled progress record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("compiled progress plans have no mutable projection")


def _audit_event_matches_compilation(
    event: AuditEvent,
    proposal: RecordProcedureCompilation,
    decision: TransactionDecision,
    expected_policy_hash: str,
) -> bool:
    if event.event_type != "transaction_decision":
        return False
    payload = json_compatible_payload(event.payload)
    try:
        exact_expected_policy_hash = _SHA256_ADAPTER.validate_python(
            expected_policy_hash,
            strict=True,
        )
        policy_hash = _SHA256_ADAPTER.validate_python(payload["policy_hash"], strict=True)
        stored_policy_hash = _SHA256_ADAPTER.validate_python(
            payload["stored_policy_hash"],
            strict=True,
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        payload.get("transaction_persisted") is True
        and payload.get("proposal") == proposal.model_dump(mode="json")
        and payload.get("decision") == decision.model_dump(mode="json")
        and policy_hash == exact_expected_policy_hash
        and stored_policy_hash == exact_expected_policy_hash
    )


def fixed_procedure_handlers() -> tuple[FixedProcedureHandler, ...]:
    return (  # type: ignore[return-value]
        RecordProcedureCompilationHandler(),
        RecordMethodDirectionOutcomeHandler(),
        BindCompiledProgressPlanHandler(),
    )


def procedure_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    artifact_store: ArtifactStore,
    *,
    current_transaction_created_at: UtcTimestamp,
) -> ProcedureCompilationCapabilities | MethodDirectionCapabilities | ProcedureBindingCapabilities:
    compilations = ProcedureCompilationRepository(connection)
    if isinstance(proposal, RecordProcedureCompilation):
        return ProcedureCompilationCapabilities(
            active_policy=active_policy,
            proposal=proposal,
            compilations=compilations,
            sources=_source_readers(connection, artifact_store),
            created_at=current_transaction_created_at,
        )
    if isinstance(proposal, RecordMethodDirectionOutcome):
        return MethodDirectionCapabilities(
            active_policy=active_policy,
            proposal=proposal,
            compilations=compilations,
            outcomes=MethodDirectionOutcomeRepository(connection),
            evidence=EvidenceRepository(connection),
            budgets=RunBudgetRepository(connection),
            created_at=current_transaction_created_at,
        )
    if isinstance(proposal, BindCompiledProgressPlan):
        return ProcedureBindingCapabilities(
            active_policy=active_policy,
            proposal=proposal,
            compilations=compilations,
            bindings=CompiledProgressPlanBindingRepository(connection),
            transactions=TransactionRepository(connection),
            audit=AuditRepository(connection),
            sources=_source_readers(connection, artifact_store),
            runs=ResearchRunRepository(connection),
            plans=ProgressPlanRepository(connection),
            subtasks=ProgressSubtaskRepository(connection),
            created_at=current_transaction_created_at,
        )
    raise TypeError(f"no fixed procedure capability for proposal: {type(proposal)!r}")


def _source_readers(
    connection: Connection,
    artifact_store: ArtifactStore,
) -> _ProcedureSourceReaders:
    return _ProcedureSourceReaders(
        accepted=AcceptedProcedureSourceReceiptReader(connection),
        profiles=CapabilityProfileRepository(connection),
        artifacts=ArtifactCatalogSnapshotRepository(connection, artifact_store),
        tools=ToolCatalogSnapshotRepository(connection, artifact_store),
        validators=ValidatorCatalogSnapshotRepository(connection, artifact_store),
        snapshots=ProcedureSourceSnapshotRepository(connection, artifact_store),
    )


__all__ = [
    "MethodDirectionCapabilities",
    "ProcedureBindingCapabilities",
    "ProcedureCompilationCapabilities",
    "fixed_procedure_handlers",
    "procedure_capabilities",
]
