from __future__ import annotations

from collections.abc import Mapping
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
    if (
        receipt.schema_version == 1
        and retained is not None
        and retained.content_hash == receipt.content_hash
    ):
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


def _evidence_id_is_current(evidence: EvidenceRepository, record_id: str) -> bool:
    return evidence.get(record_id) is not None


def _evidence_hash_is_current(
    evidence: EvidenceRepository,
    record_id: str,
    content_hash: str,
) -> bool:
    retained = evidence.get(record_id)
    return retained is not None and retained.content_hash == content_hash


def _trace_hash_bound_evidence(trace: HarnessExecutionTrace) -> tuple[tuple[str, str], ...]:
    binding = trace.observed_binding
    evidence = (
        (binding.task_id, binding.task_input_hash),
        (binding.model.model_id, binding.model_hash),
        (binding.harness.harness_id, binding.harness_hash),
        (binding.procedure_id, binding.procedure_hash),
        (binding.environment_id, binding.environment_hash),
        (binding.context_id, binding.context_hash),
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
    if trace.reward_observation is not None:
        evidence = (
            *evidence,
            (trace.reward_observation.observation_id, trace.reward_observation.content_hash),
        )
    sampling_hash = trace.generation_metadata.sampling_parameters_hash
    if sampling_hash.evidence_id is not None and sampling_hash.value is not None:
        evidence = (*evidence, (sampling_hash.evidence_id, sampling_hash.value))
    return evidence


def _trace_id_only_evidence(trace: HarnessExecutionTrace) -> tuple[str, ...]:
    available_values = (
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
    reward_evidence = (
        ()
        if trace.reward_observation is None or trace.reward_observation.evidence_id is None
        else (trace.reward_observation.evidence_id,)
    )
    return (
        *(item.evidence_id for item in trace.context_transformations),
        *(item.evidence_id for item in trace.tool_observations),
        *(item.evidence_id for item in trace.environment_events),
        *trace.provenance_evidence_ids,
        *(value.evidence_id for value in available_values if value.evidence_id is not None),
        *reward_evidence,
    )


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
    return all(checks) and _guidance_cell_evidence_matches(
        cell,
        traces=traces,
        rewards=rewards,
    )


def _guidance_cell_evidence_matches(
    cell: GuidanceEvaluationCell,
    *,
    traces: HarnessExecutionTraceRepository,
    rewards: RewardAssessmentRepository,
) -> bool:
    trace = None if cell.trace_id is None else traces.get(cell.trace_id)
    assessment = (
        None if cell.reward_assessment_id is None else rewards.get(cell.reward_assessment_id)
    )
    trace_dependent_references_exist = any(
        item is not None
        for item in (
            cell.output_artifact_id,
            cell.verifier_result_id,
            cell.reward_assessment_id,
        )
    )
    if trace is None:
        if cell.trace_id is not None:
            return True
        return not trace_dependent_references_exist
    binding = trace.observed_binding
    if (
        binding.guidance_protocol != cell.protocol
        or binding.protocol_id != cell.protocol_id
        or binding.protocol_version != cell.protocol_version
        or binding.protocol_hash != cell.protocol_hash
        or binding.guidance_condition is not cell.condition
    ):
        return False
    if cell.output_artifact_id is not None and cell.output_artifact_id not in {
        item.artifact_id for item in trace.output_artifacts
    }:
        return False
    if cell.verifier_result_id is not None and cell.verifier_result_id != trace.verifier_result_id:
        return False
    return assessment is None or (
        assessment.trace_id == trace.trace_id and assessment.trace_hash == trace.content_hash
    )


def _resolved_chain_for_cell(
    cell: ModelHarnessCell,
    *,
    protocol: ModelHarnessProtocol,
    traces: tuple[HarnessExecutionTrace, ...],
    rewards_by_trace: Mapping[str, tuple[RewardValidityAssessment, ...]],
) -> tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord] | None:
    matches: list[tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord]] = []
    for trace in traces:
        for assessment in rewards_by_trace.get(trace.trace_id, ()):
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
                matches.append((chain, snapshot))
                if len(matches) > 1:
                    return None
    return matches[0] if matches else None


def _trace_coordinate_key(trace: HarnessExecutionTrace) -> tuple[str, str, str, str, object]:
    binding = trace.observed_binding
    return (
        binding.model.model_id,
        binding.model.model_version,
        binding.harness.harness_id,
        binding.harness.harness_version,
        binding.partition,
    )


def _cell_coordinate_key(cell: ModelHarnessCell) -> tuple[str, str, str, str, object]:
    coordinate = cell.coordinate
    return (
        coordinate.model.model_id,
        coordinate.model.model_version,
        coordinate.harness.harness_id,
        coordinate.harness.harness_version,
        coordinate.partition,
    )


def _index_harness_evidence(
    traces: tuple[HarnessExecutionTrace, ...],
    assessments: tuple[RewardValidityAssessment, ...],
) -> tuple[
    Mapping[tuple[str, str, str, str, object], tuple[HarnessExecutionTrace, ...]],
    Mapping[str, tuple[RewardValidityAssessment, ...]],
]:
    traces_by_coordinate: dict[tuple[str, str, str, str, object], list[HarnessExecutionTrace]] = {}
    rewards_by_trace: dict[str, list[RewardValidityAssessment]] = {}
    for trace in traces:
        traces_by_coordinate.setdefault(_trace_coordinate_key(trace), []).append(trace)
    for assessment in assessments:
        rewards_by_trace.setdefault(assessment.trace_id, []).append(assessment)
    return (
        {key: tuple(value) for key, value in traces_by_coordinate.items()},
        {key: tuple(value) for key, value in rewards_by_trace.items()},
    )


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

    def guidance_cell_evidence_matches(self, cell: GuidanceEvaluationCell) -> bool:
        return _guidance_cell_evidence_matches(
            cell,
            traces=self.traces,
            rewards=self.rewards,
        )

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
        if protocol is None:
            return False
        traces = self.traces.list_for_protocol(cell.protocol_id)
        trace_index, reward_index = _index_harness_evidence(
            traces,
            self.rewards.list_for_traces(tuple(item.trace_id for item in traces)),
        )
        return (
            _resolved_chain_for_cell(
                cell,
                protocol=protocol,
                traces=trace_index.get(_cell_coordinate_key(cell), ()),
                rewards_by_trace=reward_index,
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
        cells = self.cells.list_for_protocol(protocol_id)
        traces = self.traces.list_for_protocol(protocol_id)
        traces_by_coordinate, rewards_by_trace = _index_harness_evidence(
            traces,
            self.rewards.list_for_traces(tuple(item.trace_id for item in traces)),
        )
        resolved = tuple(
            _resolved_chain_for_cell(
                cell,
                protocol=protocol,
                traces=traces_by_coordinate.get(_cell_coordinate_key(cell), ()),
                rewards_by_trace=rewards_by_trace,
            )
            for cell in cells
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
        if not all(
            _evidence_hash_is_current(self.evidence, record_id, content_hash)
            for record_id, content_hash in _trace_hash_bound_evidence(trace)
        ):
            return False
        return all(
            _evidence_id_is_current(self.evidence, record_id)
            for record_id in _trace_id_only_evidence(trace)
        )

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
    proposal_mro = type.__getattribute__(type(proposal), "__mro__")
    if RecordGuidanceEvaluationProtocol in proposal_mro:
        return GuidanceProtocolCapabilities(
            active_policy,
            cast(RecordGuidanceEvaluationProtocol, proposal),
            GuidanceEvaluationProtocolRepository(connection),
            current_transaction_created_at,
        )
    if AppendGuidanceEvaluationCell in proposal_mro:
        return GuidanceCellCapabilities(
            active_policy,
            cast(AppendGuidanceEvaluationCell, proposal),
            GuidanceEvaluationProtocolRepository(connection),
            GuidanceCellRepository(connection),
            HarnessExecutionTraceRepository(connection),
            RewardAssessmentRepository(connection),
            EvidenceRepository(connection),
            current_transaction_created_at,
        )
    if RecordModelHarnessProtocol in proposal_mro:
        return ModelHarnessProtocolCapabilities(
            active_policy,
            cast(RecordModelHarnessProtocol, proposal),
            ModelHarnessProtocolRepository(connection),
            current_transaction_created_at,
        )
    if AppendModelHarnessCell in proposal_mro:
        return ModelHarnessCellCapabilities(
            active_policy,
            cast(AppendModelHarnessCell, proposal),
            ModelHarnessProtocolRepository(connection),
            ModelHarnessCellRepository(connection),
            HarnessExecutionTraceRepository(connection),
            RewardAssessmentRepository(connection),
            current_transaction_created_at,
        )
    if RecordModelHarnessAnalysis in proposal_mro:
        return ModelHarnessAnalysisCapabilities(
            active_policy,
            cast(RecordModelHarnessAnalysis, proposal),
            ModelHarnessProtocolRepository(connection),
            ModelHarnessCellRepository(connection),
            ModelHarnessAnalysisRepository(connection),
            HarnessExecutionTraceRepository(connection),
            RewardAssessmentRepository(connection),
            current_transaction_created_at,
        )
    if RecordHarnessExecutionTrace in proposal_mro:
        return HarnessTraceCapabilities(
            active_policy,
            cast(RecordHarnessExecutionTrace, proposal),
            GuidanceEvaluationProtocolRepository(connection),
            ModelHarnessProtocolRepository(connection),
            HarnessExecutionTraceRepository(connection),
            EvidenceRepository(connection),
            current_transaction_created_at,
        )
    if RecordRewardAssessment in proposal_mro:
        return RewardAssessmentRecordCapabilities(
            active_policy,
            cast(RecordRewardAssessment, proposal),
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
