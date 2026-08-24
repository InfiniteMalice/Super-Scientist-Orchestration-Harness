from __future__ import annotations

from contextlib import suppress
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.application.progress.service import (
    ProgressPlanAdmissionContext,
    RecordProgressPlanHandler,
)
from super_scientist.application.transactions.contracts import (
    HandlerReadCapability,
    HandlerWriteCapability,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.procedures import (
    CompiledProgressPlanBinding,
    MethodDirectionOutcome,
    ProcedureBoundaryValidationError,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureCompilationRequest,
    ProcedureCompilationResult,
    ProcedureValidationStatus,
    compile_method,
    parse_untrusted_procedure_compilation_envelope,
    parse_untrusted_procedure_compilation_result,
    procedure_to_progress_plan,
)
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    BindCompiledProgressPlan,
    RecordMethodDirectionOutcome,
    RecordProcedureCompilation,
    RecordProgressPlan,
    RejectionCode,
    TransactionDecision,
)


class ProcedureCompilationReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None: ...

    def procedure_sources_are_current(self, request: ProcedureCompilationRequest) -> bool: ...


class MethodDirectionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None: ...

    def get_outcome(self, outcome_id: str) -> MethodDirectionOutcome | None: ...

    def retained_evidence_exists(self, reference: ArtifactRef) -> bool: ...

    def budget_exists(self, budget_id: str) -> bool: ...


class ProcedureBindingReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_compilation(self, compilation_id: str) -> ProcedureCompilationRecord | None: ...

    def procedure_sources_are_current(self, request: ProcedureCompilationRequest) -> bool: ...

    def resolve_compilation_receipt(
        self,
        receipt: ProcedureCompilationReceiptRef,
    ) -> ProcedureCompilationRecord | None: ...

    def get_binding(self, binding_id: str) -> CompiledProgressPlanBinding | None: ...

    def progress_capability(self) -> HandlerReadCapability: ...


class _CompilationContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    envelope_valid: bool
    result: ProcedureCompilationResult | None
    request: ProcedureCompilationRequest | None
    sources_current: bool
    existing_compilation: ProcedureCompilationRecord | None


class _MethodDirectionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    compilation: ProcedureCompilationRecord | None
    existing_outcome: MethodDirectionOutcome | None
    evidence_present: bool
    method_references_present: bool
    procedure_references_present: bool
    budgets_present: bool


class _BindingContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    compilation: ProcedureCompilationRecord | None
    receipt_compilation: ProcedureCompilationRecord | None
    request: ProcedureCompilationRequest | None
    sources_current: bool
    existing_binding: CompiledProgressPlanBinding | None
    progress_proposal: RecordProgressPlan
    progress_context: ProgressPlanAdmissionContext


class RecordProcedureCompilationHandler:
    proposal_type = "record_procedure_compilation"

    def build_context(
        self,
        proposal: RecordProcedureCompilation,
        reads: HandlerReadCapability,
    ) -> _CompilationContext:
        capability = cast(ProcedureCompilationReadCapability, reads)
        result: ProcedureCompilationResult | None = None
        request: ProcedureCompilationRequest | None = None
        envelope_valid = False
        compilation_id: str | None = None
        try:
            envelope = parse_untrusted_procedure_compilation_envelope(proposal.compilation)
            result = parse_untrusted_procedure_compilation_result(envelope)
            request = result.parse_request()
            compilation_id = envelope.compilation_id
            envelope_valid = True
        except ProcedureBoundaryValidationError:
            pass
        sources_current = request is not None and capability.procedure_sources_are_current(request)
        return _CompilationContext(
            active_policy=capability.policy_snapshot(),
            envelope_valid=envelope_valid,
            result=result,
            request=request,
            sources_current=sources_current,
            existing_compilation=(
                capability.get_compilation(compilation_id) if compilation_id is not None else None
            ),
        )

    def decide(
        self,
        proposal: RecordProcedureCompilation,
        context: _CompilationContext,
    ) -> TransactionDecision:
        if not context.envelope_valid or context.result is None or context.request is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROCEDURE,
                "procedure compilation failed safe boundary validation",
            )
        if not context.sources_current:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "procedure compilation sources are absent, mismatched, or stale",
            )
        expected = compile_method(context.request)
        if expected != context.result:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "procedure compilation does not match deterministic recomputation",
            )
        envelope = parse_untrusted_procedure_compilation_envelope(proposal.compilation)
        if envelope.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "procedure compilation does not name the active policy",
            )
        if context.existing_compilation is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "procedure compilation already exists",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordProcedureCompilation,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(
            ProcedureCompilationRecord.build_from_untrusted_envelope(proposal.compilation)
        )


class RecordMethodDirectionOutcomeHandler:
    proposal_type = "record_method_direction_outcome"

    def build_context(
        self,
        proposal: RecordMethodDirectionOutcome,
        reads: HandlerReadCapability,
    ) -> _MethodDirectionContext:
        capability = cast(MethodDirectionReadCapability, reads)
        compilation = capability.get_compilation(proposal.compilation_id)
        method_ids: frozenset[str] = frozenset()
        procedure_ids: frozenset[str] = frozenset()
        if compilation is not None:
            method_ids = frozenset((compilation.result.procedure.source_candidate.method_id,))
            procedure_ids = frozenset((compilation.result.procedure.procedure_id,))
        outcome = proposal.outcome
        return _MethodDirectionContext(
            active_policy=capability.policy_snapshot(),
            compilation=compilation,
            existing_outcome=capability.get_outcome(outcome.outcome_id),
            evidence_present=all(
                capability.retained_evidence_exists(reference)
                for reference in outcome.evidence_refs
            ),
            method_references_present=all(
                method_id in method_ids for method_id in outcome.failed_method_ids
            ),
            procedure_references_present=all(
                procedure_id in procedure_ids for procedure_id in outcome.rejected_procedure_ids
            ),
            budgets_present=all(
                capability.budget_exists(budget_id) for budget_id in outcome.budget_reference_ids
            ),
        )

    def decide(
        self,
        proposal: RecordMethodDirectionOutcome,
        context: _MethodDirectionContext,
    ) -> TransactionDecision:
        outcome = proposal.outcome
        if context.compilation is None:
            return _missing_reference(proposal.proposal_id, "procedure compilation")
        if (
            outcome.governing_policy_hash != context.active_policy.policy_hash
            or context.compilation.governing_policy_hash != context.active_policy.policy_hash
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "method direction outcome does not name current compilation policy",
            )
        if context.existing_outcome is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "method direction outcome already exists",
            )
        if not all(
            (
                context.evidence_present,
                context.method_references_present,
                context.procedure_references_present,
                context.budgets_present,
            )
        ):
            return _missing_reference(proposal.proposal_id, "method direction evidence")
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordMethodDirectionOutcome,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.outcome)


class BindCompiledProgressPlanHandler:
    proposal_type = "bind_compiled_progress_plan"

    def __init__(self, progress_handler: RecordProgressPlanHandler | None = None) -> None:
        self._progress_handler = progress_handler or RecordProgressPlanHandler()

    def build_context(
        self,
        proposal: BindCompiledProgressPlan,
        reads: HandlerReadCapability,
    ) -> _BindingContext:
        capability = cast(ProcedureBindingReadCapability, reads)
        compilation = capability.get_compilation(proposal.binding.compilation_id)
        request: ProcedureCompilationRequest | None = None
        if compilation is not None:
            with suppress(ProcedureBoundaryValidationError):
                request = parse_untrusted_procedure_compilation_result(
                    compilation.result
                ).parse_request()
        sources_current = request is not None and capability.procedure_sources_are_current(request)
        receipt_compilation = (
            capability.resolve_compilation_receipt(proposal.compilation_receipt)
            if sources_current
            else None
        )
        progress_proposal = RecordProgressPlan(
            proposal_id=proposal.proposal_id,
            idempotency_key=proposal.idempotency_key,
            proposer=proposal.proposer,
            approval=proposal.approval,
            plan=proposal.plan,
        )
        progress_reads = capability.progress_capability()
        return _BindingContext(
            active_policy=capability.policy_snapshot(),
            compilation=compilation,
            receipt_compilation=receipt_compilation,
            request=request,
            sources_current=sources_current,
            existing_binding=capability.get_binding(proposal.binding.binding_id),
            progress_proposal=progress_proposal,
            progress_context=self._progress_handler.build_context(
                progress_proposal,
                progress_reads,
            ),
        )

    def decide(
        self,
        proposal: BindCompiledProgressPlan,
        context: _BindingContext,
    ) -> TransactionDecision:
        compilation = context.compilation
        if compilation is None or context.request is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROCEDURE,
                "compiled progress binding requires a valid stored compilation",
            )
        if not context.sources_current:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "compiled progress binding sources are absent, mismatched, or stale",
            )
        if context.receipt_compilation != compilation:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "compiled progress binding receipt does not resolve exactly",
            )
        if compile_method(context.request) != compilation.result:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "stored procedure compilation does not match recomputation",
            )
        if compilation.result.report.status is not ProcedureValidationStatus.VALID:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROCEDURE,
                "only a valid procedure compilation can create a progress plan",
            )
        binding = proposal.binding
        procedure = compilation.result.procedure
        if (
            compilation.governing_policy_hash != context.active_policy.policy_hash
            or binding.governing_policy_hash != context.active_policy.policy_hash
            or binding.compilation_receipt != proposal.compilation_receipt
            or binding.compilation_id != compilation.compilation_id
            or binding.compilation_hash != compilation.content_hash
            or binding.procedure_id != procedure.procedure_id
            or binding.procedure_hash != procedure.content_hash
            or binding.plan != proposal.plan
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "compiled progress binding does not match current compilation authority",
            )
        if context.existing_binding is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "compiled progress plan binding already exists",
            )
        expected_plan = procedure_to_progress_plan(
            compilation.result,
            run_id=proposal.plan.run_id,
            plan_version_id=proposal.plan.plan_version_id,
            version=proposal.plan.version,
            created_at=proposal.plan.created_at,
            governing_policy_hash=context.active_policy.policy_hash,
        )
        if expected_plan != proposal.plan:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "compiled progress plan does not match deterministic mapping",
            )
        return self._progress_handler.decide(
            context.progress_proposal,
            context.progress_context,
        )

    def project(
        self,
        proposal: BindCompiledProgressPlan,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        progress_proposal = RecordProgressPlan(
            proposal_id=proposal.proposal_id,
            idempotency_key=proposal.idempotency_key,
            proposer=proposal.proposer,
            approval=proposal.approval,
            plan=proposal.plan,
        )
        self._progress_handler.project(progress_proposal, decision, writes)
        writes.append_authoritative(proposal.binding)


def _missing_reference(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.MISSING_ENTITY,
        f"{label} reference does not exist",
    )


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)


__all__ = [
    "BindCompiledProgressPlanHandler",
    "RecordMethodDirectionOutcomeHandler",
    "RecordProcedureCompilationHandler",
]
