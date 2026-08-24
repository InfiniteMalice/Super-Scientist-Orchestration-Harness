from __future__ import annotations

from contextlib import suppress
from decimal import Decimal
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.application.transactions.contracts import (
    HandlerReadCapability,
    HandlerWriteCapability,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.harness_eval.budget_bounds import PhaseAEvaluationBudget
from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessCellEvidenceChain,
    HarnessEvidenceSnapshotIndex,
)
from super_scientist.domain.harness_eval.guidance import (
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
)
from super_scientist.domain.harness_eval.matrix import (
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
    analyze_model_harness,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt, ResolvedEvidenceInventory
from super_scientist.domain.harness_eval.rewards import (
    RewardHackingCoverageAttestation,
    RewardValidityAssessment,
    VerificationOutcomeEvidence,
    assess_reward_validity,
    reward_validity_receipt,
)
from super_scientist.domain.harness_eval.traces import (
    HarnessExecutionTrace,
    TraceExpectation,
    TraceFreshness,
    parse_untrusted_harness_execution_trace,
    trace_freshness,
)
from super_scientist.domain.identity import ActorIdentity
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    HarnessExecutionTraceEnvelope,
    HarnessTraceRecordMetadata,
    ProposalBoundaryValidationError,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordRewardAssessment,
    RejectionCode,
    TransactionDecision,
    _fresh_actor_identity,
    _fresh_governed_identifier,
    _fresh_harness_trace_metadata,
    _fresh_reward_assessment_proposal,
    _invalid_reward_decision,
)


class _StrictContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class RewardAssessmentCapabilities(_StrictContext):
    expectation: TraceExpectation
    verification: VerificationOutcomeEvidence
    diagnostic_coverage: RewardHackingCoverageAttestation
    inventory: ResolvedEvidenceInventory


class _GuidanceProtocolContext(_StrictContext):
    existing: GuidanceEvaluationProtocol | None


class _GuidanceCellContext(_StrictContext):
    protocol: GuidanceEvaluationProtocol | None
    existing: GuidanceEvaluationCell | None
    evidence_matched: bool
    evidence_current: bool


class _ModelProtocolContext(_StrictContext):
    active_policy: PolicySnapshot
    existing: ModelHarnessProtocol | None


class _ModelCellContext(_StrictContext):
    active_policy: PolicySnapshot
    protocol: ModelHarnessProtocol | None
    existing: ModelHarnessCell | None
    evidence_current: bool


class _ModelAnalysisContext(_StrictContext):
    protocol: ModelHarnessProtocol | None
    cells: tuple[ModelHarnessCell, ...]
    evidence_chains: tuple[HarnessCellEvidenceChain, ...] | None
    evidence_index: HarnessEvidenceSnapshotIndex | None
    existing: ModelHarnessAnalysis | None


class _TraceContext(_StrictContext):
    guidance_protocol: GuidanceEvaluationProtocol | None
    matrix_protocol: ModelHarnessProtocol | None
    existing: HarnessExecutionTrace | None
    evidence_current: bool


class _RewardContext(_StrictContext):
    trace: HarnessExecutionTrace | None
    existing: RewardValidityAssessment | None
    capabilities: RewardAssessmentCapabilities | None


class _GuidanceProtocolReads(Protocol):
    def get_guidance_protocol(self, protocol_id: str) -> GuidanceEvaluationProtocol | None: ...


class _GuidanceCellReads(_GuidanceProtocolReads, Protocol):
    def get_guidance_cell(self, cell_id: str) -> GuidanceEvaluationCell | None: ...

    def guidance_cell_evidence_matches(self, cell: GuidanceEvaluationCell) -> bool: ...

    def guidance_cell_evidence_is_current(self, cell: GuidanceEvaluationCell) -> bool: ...


class _ModelProtocolReads(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_model_harness_protocol(self, protocol_id: str) -> ModelHarnessProtocol | None: ...


class _ModelCellReads(_ModelProtocolReads, Protocol):
    def get_model_harness_cell(self, cell_id: str) -> ModelHarnessCell | None: ...

    def model_harness_cell_evidence_is_current(self, cell: ModelHarnessCell) -> bool: ...


class _ModelAnalysisReads(_ModelProtocolReads, Protocol):
    def get_model_harness_analysis(self, protocol_id: str) -> ModelHarnessAnalysis | None: ...

    def list_model_harness_cells(self, protocol_id: str) -> tuple[ModelHarnessCell, ...]: ...

    def resolve_model_harness_evidence(
        self,
        protocol_id: str,
    ) -> tuple[tuple[HarnessCellEvidenceChain, ...], HarnessEvidenceSnapshotIndex] | None: ...


class _TraceReads(_GuidanceProtocolReads, _ModelProtocolReads, Protocol):
    def get_harness_execution_trace(self, trace_id: str) -> HarnessExecutionTrace | None: ...

    def trace_evidence_is_current(self, trace: HarnessExecutionTrace) -> bool: ...


class _RewardReads(Protocol):
    def get_harness_execution_trace(self, trace_id: str) -> HarnessExecutionTrace | None: ...

    def get_reward_assessment(self, assessment_id: str) -> RewardValidityAssessment | None: ...

    def resolve_reward_assessment_capabilities(
        self,
        *,
        trace_receipt: EvidenceReceipt,
        assessment_receipt: EvidenceReceipt,
        assessment: RewardValidityAssessment,
    ) -> RewardAssessmentCapabilities | None: ...


def _accepted(proposal_id: str) -> TransactionDecision:
    return TransactionDecision(proposal_id=proposal_id, accepted=True)


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)


def _existing_or_accept(proposal_id: str, existing: object | None) -> TransactionDecision:
    if existing is not None:
        return _rejected(
            proposal_id,
            RejectionCode.ENTITY_ALREADY_EXISTS,
            "evaluation evidence already exists",
        )
    return _accepted(proposal_id)


def _project(
    decision: TransactionDecision,
    writes: HandlerWriteCapability,
    record: BaseModel,
) -> None:
    if not decision.accepted:
        raise ValueError("rejected evaluation evidence cannot be projected")
    writes.append_authoritative(record)


class RecordGuidanceEvaluationProtocolHandler:
    proposal_type = "record_guidance_evaluation_protocol"

    def build_context(
        self,
        proposal: RecordGuidanceEvaluationProtocol,
        reads: HandlerReadCapability,
    ) -> _GuidanceProtocolContext:
        capability = cast(_GuidanceProtocolReads, reads)
        return _GuidanceProtocolContext(
            existing=capability.get_guidance_protocol(proposal.protocol.protocol_id)
        )

    def decide(
        self,
        proposal: RecordGuidanceEvaluationProtocol,
        context: _GuidanceProtocolContext,
    ) -> TransactionDecision:
        return _existing_or_accept(proposal.proposal_id, context.existing)

    def project(
        self,
        proposal: RecordGuidanceEvaluationProtocol,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project(decision, writes, proposal.protocol)


class AppendGuidanceEvaluationCellHandler:
    proposal_type = "append_guidance_evaluation_cell"

    def build_context(
        self,
        proposal: AppendGuidanceEvaluationCell,
        reads: HandlerReadCapability,
    ) -> _GuidanceCellContext:
        capability = cast(_GuidanceCellReads, reads)
        return _GuidanceCellContext(
            protocol=capability.get_guidance_protocol(proposal.cell.protocol_id),
            existing=capability.get_guidance_cell(proposal.cell.cell_id),
            evidence_matched=capability.guidance_cell_evidence_matches(proposal.cell),
            evidence_current=capability.guidance_cell_evidence_is_current(proposal.cell),
        )

    def decide(
        self,
        proposal: AppendGuidanceEvaluationCell,
        context: _GuidanceCellContext,
    ) -> TransactionDecision:
        if context.protocol is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "guidance cell requires its accepted protocol",
            )
        if proposal.cell.protocol != context.protocol:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNMATCHED_EVALUATION,
                "guidance cell does not match the accepted protocol",
            )
        if not context.evidence_matched:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNMATCHED_EVALUATION,
                "guidance cell evidence does not match the exact protocol execution",
            )
        if not context.evidence_current:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "guidance cell references stale evaluation evidence",
            )
        return _existing_or_accept(proposal.proposal_id, context.existing)

    def project(
        self,
        proposal: AppendGuidanceEvaluationCell,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project(decision, writes, proposal.cell)


class RecordModelHarnessProtocolHandler:
    proposal_type = "record_model_harness_protocol"

    def build_context(
        self,
        proposal: RecordModelHarnessProtocol,
        reads: HandlerReadCapability,
    ) -> _ModelProtocolContext:
        capability = cast(_ModelProtocolReads, reads)
        return _ModelProtocolContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_model_harness_protocol(proposal.protocol.protocol_id),
        )

    def decide(
        self,
        proposal: RecordModelHarnessProtocol,
        context: _ModelProtocolContext,
    ) -> TransactionDecision:
        if proposal.protocol.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "model-harness protocol must bind the active policy",
            )
        return _existing_or_accept(proposal.proposal_id, context.existing)

    def project(
        self,
        proposal: RecordModelHarnessProtocol,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project(decision, writes, proposal.protocol)


def _expected_model_cell(
    protocol: ModelHarnessProtocol,
    cell: ModelHarnessCell,
) -> ModelHarnessCell | None:
    expected: ModelHarnessCell | None = None
    with suppress(ArithmeticError, MemoryError, OverflowError, TypeError, ValueError):
        expected = ModelHarnessCell.from_protocol(
            cell_id=cell.cell_id,
            protocol=protocol,
            coordinate=cell.coordinate,
            metrics=cell.metrics,
            evidence_chain_receipt=cell.evidence_chain_receipt,
            observed_at=cell.observed_at,
        )
    return expected


class AppendModelHarnessCellHandler:
    proposal_type = "append_model_harness_cell"

    def build_context(
        self,
        proposal: AppendModelHarnessCell,
        reads: HandlerReadCapability,
    ) -> _ModelCellContext:
        capability = cast(_ModelCellReads, reads)
        return _ModelCellContext(
            active_policy=capability.policy_snapshot(),
            protocol=capability.get_model_harness_protocol(proposal.cell.protocol_id),
            existing=capability.get_model_harness_cell(proposal.cell.cell_id),
            evidence_current=capability.model_harness_cell_evidence_is_current(proposal.cell),
        )

    def decide(
        self,
        proposal: AppendModelHarnessCell,
        context: _ModelCellContext,
    ) -> TransactionDecision:
        if context.protocol is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "model-harness cell requires its accepted protocol",
            )
        if (
            proposal.cell.governing_policy_hash != context.active_policy.policy_hash
            or context.protocol.governing_policy_hash != context.active_policy.policy_hash
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "model-harness cell must bind the active policy",
            )
        expected = _expected_model_cell(context.protocol, proposal.cell)
        if expected is None or expected != proposal.cell:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNMATCHED_EVALUATION,
                "model-harness cell does not match protocol identity and budget",
            )
        if not context.evidence_current:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "model-harness cell references stale evidence",
            )
        return _existing_or_accept(proposal.proposal_id, context.existing)

    def project(
        self,
        proposal: AppendModelHarnessCell,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project(decision, writes, proposal.cell)


class RecordModelHarnessAnalysisHandler:
    proposal_type = "record_model_harness_analysis"

    def build_context(
        self,
        proposal: RecordModelHarnessAnalysis,
        reads: HandlerReadCapability,
    ) -> _ModelAnalysisContext:
        capability = cast(_ModelAnalysisReads, reads)
        protocol_id = proposal.analysis.protocol_id
        resolved = capability.resolve_model_harness_evidence(protocol_id)
        return _ModelAnalysisContext(
            protocol=capability.get_model_harness_protocol(protocol_id),
            cells=capability.list_model_harness_cells(protocol_id),
            evidence_chains=None if resolved is None else resolved[0],
            evidence_index=None if resolved is None else resolved[1],
            existing=capability.get_model_harness_analysis(protocol_id),
        )

    def decide(
        self,
        proposal: RecordModelHarnessAnalysis,
        context: _ModelAnalysisContext,
    ) -> TransactionDecision:
        if context.protocol is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "model-harness analysis requires its accepted protocol",
            )
        if context.evidence_chains is None or context.evidence_index is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "model-harness analysis evidence could not be resolved",
            )
        expected: ModelHarnessAnalysis | None = None
        with suppress(ArithmeticError, MemoryError, OverflowError, TypeError, ValueError):
            expected = analyze_model_harness(
                context.protocol,
                context.cells,
                evidence_chains=context.evidence_chains,
                evidence_index=context.evidence_index,
            )
        if expected is None or expected != proposal.analysis:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "model-harness analysis must equal the current pure derivation",
            )
        return _existing_or_accept(proposal.proposal_id, context.existing)

    def project(
        self,
        proposal: RecordModelHarnessAnalysis,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project(decision, writes, proposal.analysis)


def _trace_within_budget(
    trace: HarnessExecutionTrace,
    budget: PhaseAEvaluationBudget,
) -> bool:
    usage = trace.resource_usage
    observed_tools = tuple(item.tool_id for item in trace.tool_observations)
    token_count = trace.generation_metadata.token_count.value
    return bool(
        usage.tokens <= budget.token_limit
        and Decimal(str(usage.elapsed_seconds)) <= budget.wall_clock_seconds
        and Decimal(str(usage.cost_usd)) <= budget.cost_limit
        and usage.human_interventions <= budget.human_intervention_limit
        and usage.tool_calls == len(trace.tool_observations)
        and all(tool_id in budget.tool_ids for tool_id in observed_tools)
        and (token_count is None or token_count <= budget.token_limit)
    )


def _trace_matches_guidance(
    trace: HarnessExecutionTrace,
    protocol: GuidanceEvaluationProtocol,
) -> bool:
    binding = trace.observed_binding
    return bool(
        binding.guidance_protocol == protocol
        and binding.protocol_id == protocol.protocol_id
        and binding.protocol_version == protocol.version
        and binding.protocol_hash == protocol.content_hash
        and binding.task_id == protocol.task_id
        and binding.task_input_hash == protocol.task_input_hash
        and binding.model.model_id == protocol.model_id
        and binding.model.model_version == protocol.model_version
        and binding.harness.harness_id == protocol.harness_id
        and binding.harness.harness_version == protocol.harness_version
        and binding.validator_id == protocol.verifier_id
        and binding.validator_version == protocol.verifier_version
        and binding.checker_id == protocol.checker_id
        and binding.checker_version == protocol.checker_version
        and binding.output_schema_hash == protocol.output_schema_hash
    )


def _trace_matches_matrix(
    trace: HarnessExecutionTrace,
    protocol: ModelHarnessProtocol,
) -> bool:
    binding = trace.observed_binding
    budget = next(
        (item.budget for item in protocol.model_budgets if item.model == binding.model),
        None,
    )
    return bool(
        binding.model_harness_protocol_receipt
        == EvidenceReceipt(
            record_id=protocol.protocol_id,
            schema_version=protocol.schema_version,
            content_hash=protocol.content_hash,
        )
        and binding.protocol_id == protocol.protocol_id
        and binding.protocol_version == protocol.version
        and binding.protocol_hash == protocol.content_hash
        and binding.task_id == protocol.task_set_id
        and binding.task_input_hash == protocol.task_set_hash
        and binding.validator_id == protocol.verifier_id
        and binding.validator_version == protocol.verifier_version
        and binding.checker_id == protocol.checker_id
        and binding.checker_version == protocol.checker_version
        and binding.output_schema_hash == protocol.output_schema_hash
        and binding.artifact_ids == protocol.artifact_ids
        and binding.authorized_artifact_ids == protocol.artifact_ids
        and any(
            item.model == binding.model
            and item.harness == binding.harness
            and item.partition is binding.partition
            for item in protocol.expected_grid
        )
        and budget is not None
    )


class HarnessTraceProposalAdapter:
    def from_untrusted_payload(
        self,
        payload: str | bytes,
        metadata: HarnessTraceRecordMetadata,
        proposal_id: str,
        idempotency_key: str,
        proposer: ActorIdentity,
    ) -> RecordHarnessExecutionTrace:
        proposal: RecordHarnessExecutionTrace | None = None
        with suppress(
            MemoryError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            if type(metadata) is not HarnessTraceRecordMetadata:
                raise ValueError("trace metadata must have the exact declared type")
            parsed_trace = parse_untrusted_harness_execution_trace(payload)
            proposal = RecordHarnessExecutionTrace(
                proposal_id=_fresh_governed_identifier(proposal_id),
                idempotency_key=_fresh_governed_identifier(idempotency_key),
                proposer=_fresh_actor_identity(proposer),
                envelope=HarnessExecutionTraceEnvelope(
                    schema_version=1,
                    metadata=_fresh_harness_trace_metadata(metadata),
                    trace=HarnessExecutionTrace.model_validate(parsed_trace, strict=True),
                ),
            )
        if proposal is None:
            raise ProposalBoundaryValidationError(
                "transaction proposal failed validation"
            ) from None
        return proposal


class RecordHarnessExecutionTraceHandler:
    proposal_type = "record_harness_execution_trace"

    def build_context(
        self,
        proposal: RecordHarnessExecutionTrace,
        reads: HandlerReadCapability,
    ) -> _TraceContext:
        capability = cast(_TraceReads, reads)
        trace = proposal.envelope.trace
        protocol_id = trace.observed_binding.protocol_id
        return _TraceContext(
            guidance_protocol=capability.get_guidance_protocol(protocol_id),
            matrix_protocol=capability.get_model_harness_protocol(protocol_id),
            existing=capability.get_harness_execution_trace(trace.trace_id),
            evidence_current=capability.trace_evidence_is_current(trace),
        )

    def decide(
        self,
        proposal: RecordHarnessExecutionTrace,
        context: _TraceContext,
    ) -> TransactionDecision:
        trace = proposal.envelope.trace
        guidance_matched = context.guidance_protocol is not None and _trace_matches_guidance(
            trace, context.guidance_protocol
        )
        matrix_matched = context.matrix_protocol is not None and _trace_matches_matrix(
            trace, context.matrix_protocol
        )
        if not guidance_matched and not matrix_matched:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "harness trace does not bind current accepted execution evidence",
            )
        budget = (
            context.guidance_protocol.evaluation_budget
            if guidance_matched and context.guidance_protocol is not None
            else next(
                (
                    item.budget
                    for item in context.matrix_protocol.model_budgets
                    if item.model == trace.observed_binding.model
                ),
                None,
            )
            if matrix_matched and context.matrix_protocol is not None
            else None
        )
        if budget is None or not _trace_within_budget(trace, budget):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNMATCHED_BUDGETS,
                "harness trace exceeds or changes the exact protocol budget",
            )
        if not context.evidence_current:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "harness trace references stale accepted execution evidence",
            )
        return _existing_or_accept(proposal.proposal_id, context.existing)

    def project(
        self,
        proposal: RecordHarnessExecutionTrace,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project(decision, writes, proposal.envelope.trace)


class RecordRewardAssessmentHandler:
    proposal_type = "record_reward_assessment"

    def build_context(
        self,
        proposal: RecordRewardAssessment,
        reads: HandlerReadCapability,
    ) -> _RewardContext:
        capability = cast(_RewardReads, reads)
        validated: RecordRewardAssessment | None = None
        with suppress(
            ArithmeticError,
            MemoryError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            validated = _fresh_reward_assessment_proposal(proposal)
        if validated is None:
            return _RewardContext(trace=None, existing=None, capabilities=None)
        assessment = validated.assessment
        trace = capability.get_harness_execution_trace(assessment.trace_id)
        trace_receipt = EvidenceReceipt(
            record_id=assessment.trace_id,
            schema_version=assessment.trace.schema_version,
            content_hash=assessment.trace_hash,
        )
        return _RewardContext(
            trace=trace,
            existing=capability.get_reward_assessment(assessment.assessment_id),
            capabilities=capability.resolve_reward_assessment_capabilities(
                trace_receipt=trace_receipt,
                assessment_receipt=reward_validity_receipt(assessment),
                assessment=assessment,
            ),
        )

    def decide(
        self,
        proposal: RecordRewardAssessment,
        context: _RewardContext,
    ) -> TransactionDecision:
        validated: RecordRewardAssessment | None = None
        with suppress(
            ArithmeticError,
            MemoryError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            validated = _fresh_reward_assessment_proposal(proposal)
        if validated is None:
            return _invalid_reward_decision(proposal)
        if context.trace is None:
            return _rejected(
                validated.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "reward assessment requires its accepted trace",
            )
        if context.trace.reward_observation != validated.observation:
            return _rejected(
                validated.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "reward observation must match the accepted trace",
            )
        if context.capabilities is None:
            return _rejected(
                validated.proposal_id,
                RejectionCode.STALE_REFERENCE,
                "reward assessment evidence could not be resolved",
            )
        expected: RewardValidityAssessment | None = None
        freshness: TraceFreshness | None = None
        with suppress(
            ArithmeticError,
            MemoryError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            freshness = trace_freshness(
                context.capabilities.expectation,
                context.trace,
                inventory=context.capabilities.inventory,
            )
            expected = assess_reward_validity(
                validated.observation,
                context.trace,
                validated.findings,
                expectation=context.capabilities.expectation,
                verification=context.capabilities.verification,
                diagnostic_coverage=context.capabilities.diagnostic_coverage,
                inventory=context.capabilities.inventory,
            )
        if expected is None or freshness is None:
            return _invalid_reward_decision(validated)
        if expected != validated.assessment or expected.freshness != freshness:
            return _rejected(
                validated.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "reward assessment must equal the current pure derivation",
            )
        return _existing_or_accept(validated.proposal_id, context.existing)

    def project(
        self,
        proposal: RecordRewardAssessment,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        validated = _fresh_reward_assessment_proposal(proposal)
        _project(decision, writes, validated.assessment)


type FixedHarnessExtensionHandler = (
    RecordGuidanceEvaluationProtocolHandler
    | AppendGuidanceEvaluationCellHandler
    | RecordModelHarnessProtocolHandler
    | AppendModelHarnessCellHandler
    | RecordModelHarnessAnalysisHandler
    | RecordHarnessExecutionTraceHandler
    | RecordRewardAssessmentHandler
)


def fixed_harness_extension_handlers() -> tuple[FixedHarnessExtensionHandler, ...]:
    return (
        RecordGuidanceEvaluationProtocolHandler(),
        AppendGuidanceEvaluationCellHandler(),
        RecordModelHarnessProtocolHandler(),
        AppendModelHarnessCellHandler(),
        RecordModelHarnessAnalysisHandler(),
        RecordHarnessExecutionTraceHandler(),
        RecordRewardAssessmentHandler(),
    )


__all__ = [
    "AppendGuidanceEvaluationCellHandler",
    "AppendModelHarnessCellHandler",
    "HarnessTraceProposalAdapter",
    "RecordGuidanceEvaluationProtocolHandler",
    "RecordHarnessExecutionTraceHandler",
    "RecordModelHarnessAnalysisHandler",
    "RecordModelHarnessProtocolHandler",
    "RecordRewardAssessmentHandler",
    "RewardAssessmentCapabilities",
    "fixed_harness_extension_handlers",
]
