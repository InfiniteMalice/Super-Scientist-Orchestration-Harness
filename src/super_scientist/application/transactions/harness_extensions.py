from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.application.harness_eval.extensions import RewardAssessmentCapabilities
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessCellEvidenceChain,
    HarnessEvidenceSnapshotIndex,
    HarnessEvidenceSnapshotRecord,
    harness_cell_evidence_chain_receipt,
    project_harness_evidence_snapshots,
)
from super_scientist.domain.harness_eval.guidance import (
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
)
from super_scientist.domain.harness_eval.matrix import (
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.harness_eval.rewards import (
    RewardValidityAssessment,
    reward_validity_receipt,
)
from super_scientist.domain.harness_eval.traces import (
    HarnessExecutionTrace,
    trace_freshness_receipt,
)
from super_scientist.domain.primitives import UtcTimestamp
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordRewardAssessment,
)
from super_scientist.providers.storage.evaluation_records import (
    GuidanceCellRepository,
    GuidanceEvaluationProtocolRepository,
    HarnessExecutionTraceRepository,
    ModelHarnessAnalysisRepository,
    ModelHarnessCellRepository,
    ModelHarnessProtocolRepository,
    RewardAssessmentRepository,
)
from super_scientist.providers.storage.repositories import EvidenceRepository


def _no_projection(record: BaseModel) -> None:
    del record
    raise RuntimeError("harness extension evidence has no mutable projection")


def _require_exact_record(
    supplied: BaseModel,
    expected: BaseModel,
    expected_type: type[BaseModel],
) -> None:
    if type(supplied) is not expected_type or supplied != expected:
        raise TypeError("capability accepts only its exact proposal-bound evidence record")


def _receipt_matches_model(receipt: EvidenceReceipt, record: object) -> bool:
    if type(record) in (GuidanceEvaluationProtocol, ModelHarnessProtocol):
        protocol = cast(GuidanceEvaluationProtocol | ModelHarnessProtocol, record)
        return (
            EvidenceReceipt(
                record_id=protocol.protocol_id,
                schema_version=protocol.schema_version,
                content_hash=protocol.content_hash,
            )
            == receipt
        )
    if type(record) is HarnessExecutionTrace:
        trace = record
        return (
            EvidenceReceipt(
                record_id=trace.trace_id,
                schema_version=trace.schema_version,
                content_hash=trace.content_hash,
            )
            == receipt
        )
    if type(record) is RewardValidityAssessment:
        return reward_validity_receipt(record) == receipt
    return False


def _receipt_is_current(
    receipt: EvidenceReceipt,
    *,
    evidence: EvidenceRepository,
    guidance_protocols: GuidanceEvaluationProtocolRepository | None = None,
    matrix_protocols: ModelHarnessProtocolRepository | None = None,
    traces: HarnessExecutionTraceRepository | None = None,
    rewards: RewardAssessmentRepository | None = None,
) -> bool:
    retained = evidence.get(receipt.record_id)
    if retained is not None and retained.content_hash == receipt.content_hash:
        return True
    candidates: tuple[object | None, ...] = (
        None if guidance_protocols is None else guidance_protocols.get(receipt.record_id),
        None if matrix_protocols is None else matrix_protocols.get(receipt.record_id),
        None if traces is None else traces.get(receipt.record_id),
        None if rewards is None else rewards.get(receipt.record_id),
    )
    if any(item is not None and _receipt_matches_model(receipt, item) for item in candidates):
        return True
    if rewards is not None:
        return any(
            trace_freshness_receipt(item.freshness) == receipt
            or reward_validity_receipt(item) == receipt
            for item in rewards.list_all()
        )
    return False


def _guidance_cell_evidence_is_current(
    cell: GuidanceEvaluationCell,
    *,
    traces: HarnessExecutionTraceRepository,
    rewards: RewardAssessmentRepository,
    evidence: EvidenceRepository,
) -> bool:
    checks = (
        cell.output_artifact_id is None or evidence.get(cell.output_artifact_id) is not None,
        cell.trace_id is None or traces.get(cell.trace_id) is not None,
        cell.verifier_result_id is None or evidence.get(cell.verifier_result_id) is not None,
        cell.reward_assessment_id is None or rewards.get(cell.reward_assessment_id) is not None,
    )
    return all(checks)


def _resolved_chain_for_cell(
    cell: ModelHarnessCell,
    *,
    protocol: ModelHarnessProtocol,
    traces: HarnessExecutionTraceRepository,
    rewards: RewardAssessmentRepository,
) -> tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord] | None:
    for trace in traces.list_for_protocol(protocol.protocol_id):
        binding = trace.observed_binding
        if (
            binding.model != cell.coordinate.model
            or binding.harness != cell.coordinate.harness
            or binding.partition is not cell.coordinate.partition
        ):
            continue
        for assessment in rewards.list_for_trace(trace.trace_id):
            try:
                chain, snapshot = project_harness_evidence_snapshots(
                    protocol=protocol,
                    coordinate=cell.coordinate,
                    trace=trace,
                    freshness=assessment.freshness,
                    assessment=assessment,
                )
            except (ArithmeticError, MemoryError, OverflowError, TypeError, ValueError):
                continue
            if harness_cell_evidence_chain_receipt(chain) == cell.evidence_chain_receipt:
                return chain, snapshot
    return None


@dataclass(frozen=True)
class GuidanceProtocolCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordGuidanceEvaluationProtocol
    protocols: GuidanceEvaluationProtocolRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_guidance_protocol(self, protocol_id: str) -> GuidanceEvaluationProtocol | None:
        return self.protocols.get(protocol_id)

    def append_authoritative(self, record: BaseModel) -> None:
        _require_exact_record(record, self.proposal.protocol, GuidanceEvaluationProtocol)
        self.protocols.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        _no_projection(record)


@dataclass(frozen=True)
class GuidanceCellCapabilities:
    active_policy: PolicySnapshot
    proposal: AppendGuidanceEvaluationCell
    protocols: GuidanceEvaluationProtocolRepository
    cells: GuidanceCellRepository
    traces: HarnessExecutionTraceRepository
    rewards: RewardAssessmentRepository
    evidence: EvidenceRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_guidance_protocol(self, protocol_id: str) -> GuidanceEvaluationProtocol | None:
        return self.protocols.get(protocol_id)

    def get_guidance_cell(self, cell_id: str) -> GuidanceEvaluationCell | None:
        return self.cells.get(cell_id)

    def guidance_cell_evidence_is_current(self, cell: GuidanceEvaluationCell) -> bool:
        return _guidance_cell_evidence_is_current(
            cell,
            traces=self.traces,
            rewards=self.rewards,
            evidence=self.evidence,
        )

    def append_authoritative(self, record: BaseModel) -> None:
        _require_exact_record(record, self.proposal.cell, GuidanceEvaluationCell)
        self.cells.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        _no_projection(record)


@dataclass(frozen=True)
class ModelHarnessProtocolCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordModelHarnessProtocol
    protocols: ModelHarnessProtocolRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_model_harness_protocol(self, protocol_id: str) -> ModelHarnessProtocol | None:
        return self.protocols.get(protocol_id)

    def append_authoritative(self, record: BaseModel) -> None:
        _require_exact_record(record, self.proposal.protocol, ModelHarnessProtocol)
        self.protocols.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        _no_projection(record)


@dataclass(frozen=True)
class ModelHarnessCellCapabilities:
    active_policy: PolicySnapshot
    proposal: AppendModelHarnessCell
    protocols: ModelHarnessProtocolRepository
    cells: ModelHarnessCellRepository
    traces: HarnessExecutionTraceRepository
    rewards: RewardAssessmentRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_model_harness_protocol(self, protocol_id: str) -> ModelHarnessProtocol | None:
        return self.protocols.get(protocol_id)

    def get_model_harness_cell(self, cell_id: str) -> ModelHarnessCell | None:
        return self.cells.get(cell_id)

    def model_harness_cell_evidence_is_current(self, cell: ModelHarnessCell) -> bool:
        protocol = self.protocols.get(cell.protocol_id)
        return (
            protocol is not None
            and _resolved_chain_for_cell(
                cell,
                protocol=protocol,
                traces=self.traces,
                rewards=self.rewards,
            )
            is not None
        )

    def append_authoritative(self, record: BaseModel) -> None:
        _require_exact_record(record, self.proposal.cell, ModelHarnessCell)
        self.cells.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        _no_projection(record)


@dataclass(frozen=True)
class ModelHarnessAnalysisCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordModelHarnessAnalysis
    protocols: ModelHarnessProtocolRepository
    cells: ModelHarnessCellRepository
    analyses: ModelHarnessAnalysisRepository
    traces: HarnessExecutionTraceRepository
    rewards: RewardAssessmentRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_model_harness_protocol(self, protocol_id: str) -> ModelHarnessProtocol | None:
        return self.protocols.get(protocol_id)

    def get_model_harness_analysis(self, protocol_id: str) -> ModelHarnessAnalysis | None:
        return self.analyses.get(protocol_id)

    def list_model_harness_cells(self, protocol_id: str) -> tuple[ModelHarnessCell, ...]:
        return self.cells.list_for_protocol(protocol_id)

    def resolve_model_harness_evidence(
        self,
        protocol_id: str,
    ) -> tuple[tuple[HarnessCellEvidenceChain, ...], HarnessEvidenceSnapshotIndex] | None:
        protocol = self.protocols.get(protocol_id)
        if protocol is None:
            return None
        resolved = tuple(
            _resolved_chain_for_cell(
                cell,
                protocol=protocol,
                traces=self.traces,
                rewards=self.rewards,
            )
            for cell in self.cells.list_for_protocol(protocol_id)
        )
        if not resolved or any(item is None for item in resolved):
            return None
        complete = cast(
            tuple[tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord], ...],
            resolved,
        )
        chains = tuple(item[0] for item in complete)
        snapshots = tuple(
            sorted((item[1] for item in complete), key=lambda item: item.chain_receipt.record_id)
        )
        return chains, HarnessEvidenceSnapshotIndex.build(records=snapshots)

    def append_authoritative(self, record: BaseModel) -> None:
        _require_exact_record(record, self.proposal.analysis, ModelHarnessAnalysis)
        self.analyses.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        _no_projection(record)


@dataclass(frozen=True)
class HarnessTraceCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordHarnessExecutionTrace
    guidance_protocols: GuidanceEvaluationProtocolRepository
    matrix_protocols: ModelHarnessProtocolRepository
    traces: HarnessExecutionTraceRepository
    evidence: EvidenceRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_guidance_protocol(self, protocol_id: str) -> GuidanceEvaluationProtocol | None:
        return self.guidance_protocols.get(protocol_id)

    def get_model_harness_protocol(self, protocol_id: str) -> ModelHarnessProtocol | None:
        return self.matrix_protocols.get(protocol_id)

    def get_harness_execution_trace(self, trace_id: str) -> HarnessExecutionTrace | None:
        return self.traces.get(trace_id)

    def trace_evidence_is_current(self, trace: HarnessExecutionTrace) -> bool:
        binding = trace.observed_binding
        receipts = (
            EvidenceReceipt(
                record_id=binding.task_id, schema_version=1, content_hash=binding.task_input_hash
            ),
            EvidenceReceipt(
                record_id=binding.model.model_id,
                schema_version=binding.model.schema_version,
                content_hash=binding.model_hash,
            ),
            EvidenceReceipt(
                record_id=binding.harness.harness_id,
                schema_version=binding.harness.schema_version,
                content_hash=binding.harness_hash,
            ),
            EvidenceReceipt(
                record_id=binding.procedure_id,
                schema_version=1,
                content_hash=binding.procedure_hash,
            ),
            EvidenceReceipt(
                record_id=binding.environment_id,
                schema_version=1,
                content_hash=binding.environment_hash,
            ),
            EvidenceReceipt(
                record_id=binding.context_id, schema_version=1, content_hash=binding.context_hash
            ),
            EvidenceReceipt(
                record_id=binding.validator_id,
                schema_version=1,
                content_hash=binding.validator_hash,
            ),
            EvidenceReceipt(
                record_id=binding.checker_id, schema_version=1, content_hash=binding.checker_hash
            ),
            EvidenceReceipt(
                record_id=binding.output_schema_id,
                schema_version=1,
                content_hash=binding.output_schema_hash,
            ),
            EvidenceReceipt(
                record_id=trace.verifier_result_id,
                schema_version=1,
                content_hash=trace.verifier_result_hash,
            ),
            EvidenceReceipt(
                record_id=trace.checker_result_id,
                schema_version=1,
                content_hash=trace.checker_result_hash,
            ),
            *(
                EvidenceReceipt(record_id=artifact_id, schema_version=1, content_hash=artifact_hash)
                for artifact_id, artifact_hash in zip(
                    binding.artifact_ids, binding.artifact_hashes, strict=True
                )
            ),
        )
        return all(_receipt_is_current(item, evidence=self.evidence) for item in receipts)

    def append_authoritative(self, record: BaseModel) -> None:
        _require_exact_record(record, self.proposal.envelope.trace, HarnessExecutionTrace)
        self.traces.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        _no_projection(record)


@dataclass(frozen=True)
class RewardAssessmentRecordCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordRewardAssessment
    guidance_protocols: GuidanceEvaluationProtocolRepository
    matrix_protocols: ModelHarnessProtocolRepository
    traces: HarnessExecutionTraceRepository
    rewards: RewardAssessmentRepository
    evidence: EvidenceRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_harness_execution_trace(self, trace_id: str) -> HarnessExecutionTrace | None:
        return self.traces.get(trace_id)

    def get_reward_assessment(self, assessment_id: str) -> RewardValidityAssessment | None:
        return self.rewards.get(assessment_id)

    def resolve_reward_assessment_capabilities(
        self,
        *,
        trace_receipt: EvidenceReceipt,
        assessment_receipt: EvidenceReceipt,
        assessment: RewardValidityAssessment,
    ) -> RewardAssessmentCapabilities | None:
        trace = self.traces.get(trace_receipt.record_id)
        if (
            trace is None
            or not _receipt_matches_model(trace_receipt, trace)
            or assessment_receipt != reward_validity_receipt(assessment)
            or assessment.trace != trace
        ):
            return None
        inventory = assessment.evidence_inventory
        required = (
            inventory.resolved_by,
            assessment.expectation.resolution.expectation_source,
            assessment.expectation.resolution.resolver,
            *assessment.expectation.resolution.provenance,
            *(item.receipt for item in inventory.records),
        )
        if not all(
            _receipt_is_current(
                item,
                evidence=self.evidence,
                guidance_protocols=self.guidance_protocols,
                matrix_protocols=self.matrix_protocols,
                traces=self.traces,
                rewards=self.rewards,
            )
            for item in required
        ):
            return None
        return RewardAssessmentCapabilities(
            expectation=assessment.expectation,
            verification=assessment.verification,
            diagnostic_coverage=assessment.diagnostic_coverage,
            inventory=inventory,
        )

    def append_authoritative(self, record: BaseModel) -> None:
        _require_exact_record(record, self.proposal.assessment, RewardValidityAssessment)
        self.rewards.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        _no_projection(record)


type HarnessExtensionCapabilities = (
    GuidanceProtocolCapabilities
    | GuidanceCellCapabilities
    | ModelHarnessProtocolCapabilities
    | ModelHarnessCellCapabilities
    | ModelHarnessAnalysisCapabilities
    | HarnessTraceCapabilities
    | RewardAssessmentRecordCapabilities
)


def harness_extension_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    *,
    current_transaction_created_at: UtcTimestamp,
) -> HarnessExtensionCapabilities:
    if type(proposal) is RecordGuidanceEvaluationProtocol:
        return GuidanceProtocolCapabilities(
            active_policy,
            proposal,
            GuidanceEvaluationProtocolRepository(connection),
            current_transaction_created_at,
        )
    if type(proposal) is AppendGuidanceEvaluationCell:
        return GuidanceCellCapabilities(
            active_policy,
            proposal,
            GuidanceEvaluationProtocolRepository(connection),
            GuidanceCellRepository(connection),
            HarnessExecutionTraceRepository(connection),
            RewardAssessmentRepository(connection),
            EvidenceRepository(connection),
            current_transaction_created_at,
        )
    if type(proposal) is RecordModelHarnessProtocol:
        return ModelHarnessProtocolCapabilities(
            active_policy,
            proposal,
            ModelHarnessProtocolRepository(connection),
            current_transaction_created_at,
        )
    if type(proposal) is AppendModelHarnessCell:
        return ModelHarnessCellCapabilities(
            active_policy,
            proposal,
            ModelHarnessProtocolRepository(connection),
            ModelHarnessCellRepository(connection),
            HarnessExecutionTraceRepository(connection),
            RewardAssessmentRepository(connection),
            current_transaction_created_at,
        )
    if type(proposal) is RecordModelHarnessAnalysis:
        return ModelHarnessAnalysisCapabilities(
            active_policy,
            proposal,
            ModelHarnessProtocolRepository(connection),
            ModelHarnessCellRepository(connection),
            ModelHarnessAnalysisRepository(connection),
            HarnessExecutionTraceRepository(connection),
            RewardAssessmentRepository(connection),
            current_transaction_created_at,
        )
    if type(proposal) is RecordHarnessExecutionTrace:
        return HarnessTraceCapabilities(
            active_policy,
            proposal,
            GuidanceEvaluationProtocolRepository(connection),
            ModelHarnessProtocolRepository(connection),
            HarnessExecutionTraceRepository(connection),
            EvidenceRepository(connection),
            current_transaction_created_at,
        )
    if type(proposal) is RecordRewardAssessment:
        return RewardAssessmentRecordCapabilities(
            active_policy,
            proposal,
            GuidanceEvaluationProtocolRepository(connection),
            ModelHarnessProtocolRepository(connection),
            HarnessExecutionTraceRepository(connection),
            RewardAssessmentRepository(connection),
            EvidenceRepository(connection),
            current_transaction_created_at,
        )
    raise TypeError(f"no fixed harness-extension capability for proposal: {type(proposal)!r}")


__all__ = [
    "HarnessExtensionCapabilities",
    "harness_extension_capabilities",
]
