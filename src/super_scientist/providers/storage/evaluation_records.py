from __future__ import annotations

import json
from typing import Self

from pydantic import model_validator
from sqlalchemy import Connection

from super_scientist.domain.harness_eval.guidance import (
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
)
from super_scientist.domain.harness_eval.matrix import (
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
)
from super_scientist.domain.harness_eval.rewards import RewardValidityAssessment
from super_scientist.domain.harness_eval.traces import (
    HarnessExecutionTrace,
    parse_untrusted_harness_execution_trace,
)
from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordRewardAssessment,
    parse_untrusted_proposal_json,
)
from super_scientist.providers.storage.cognitive_records import (
    BoundedStorageIdentifier,
    GovernedAppendOnlyRecordRepository,
    _require_proposal_transaction,
    _StrictGovernedStorageEnvelope,
)
from super_scientist.providers.storage.schema import (
    guidance_cells,
    guidance_protocols,
    harness_execution_traces,
    model_harness_analyses,
    model_harness_cells,
    model_harness_protocols,
    reward_assessments,
)

__all__ = [
    "GuidanceCellRepository",
    "GuidanceEvaluationCellRepository",
    "GuidanceEvaluationProtocolRepository",
    "GuidanceProtocolRepository",
    "HarnessExecutionTraceRepository",
    "HarnessExecutionTraceStorageEnvelope",
    "ModelHarnessAnalysisRepository",
    "ModelHarnessCellRepository",
    "ModelHarnessProtocolRepository",
    "RewardAssessmentRepository",
    "RewardAssessmentStorageEnvelope",
]


class HarnessExecutionTraceStorageEnvelope(_StrictGovernedStorageEnvelope):
    trace_id: BoundedStorageIdentifier
    protocol_id: BoundedStorageIdentifier
    record: HarnessExecutionTrace

    @classmethod
    def from_harness_execution_trace(cls, record: HarnessExecutionTrace) -> Self:
        return cls(
            trace_id=record.trace_id,
            protocol_id=record.observed_binding.protocol_id,
            record=record,
        )

    @model_validator(mode="after")
    def require_exact_relationships(self) -> Self:
        if (
            self.trace_id != self.record.trace_id
            or self.protocol_id != self.record.observed_binding.protocol_id
        ):
            raise ValueError("harness trace storage relationship mismatch")
        return self


class RewardAssessmentStorageEnvelope(_StrictGovernedStorageEnvelope):
    assessment_id: BoundedStorageIdentifier
    trace_id: BoundedStorageIdentifier
    observation_id: BoundedStorageIdentifier
    record: RewardValidityAssessment

    @classmethod
    def from_reward_assessment(cls, record: RewardValidityAssessment) -> Self:
        return cls(
            assessment_id=record.assessment_id,
            trace_id=record.trace_id,
            observation_id=record.observation.observation_id,
            record=record,
        )

    @model_validator(mode="after")
    def require_exact_relationships(self) -> Self:
        if (
            self.assessment_id != self.record.assessment_id
            or self.trace_id != self.record.trace_id
            or self.observation_id != self.record.observation.observation_id
        ):
            raise ValueError("reward assessment storage relationship mismatch")
        return self


class GuidanceEvaluationProtocolRepository(
    GovernedAppendOnlyRecordRepository[GuidanceEvaluationProtocol]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=guidance_protocols,
            model_type=GuidanceEvaluationProtocol,
            identifier_field="protocol_id",
        )

    def add_from_proposal(
        self,
        proposal: RecordGuidanceEvaluationProtocol,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.protocol.protocol_id,
            proposal.protocol,
            created_at,
            transaction_id,
            governing_policy_hash,
        )


GuidanceProtocolRepository = GuidanceEvaluationProtocolRepository


class GuidanceCellRepository(GovernedAppendOnlyRecordRepository[GuidanceEvaluationCell]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=guidance_cells,
            model_type=GuidanceEvaluationCell,
            identifier_field="cell_id",
            relationship_fields={"protocol_id": "protocol_id"},
        )

    def add_from_proposal(
        self,
        proposal: AppendGuidanceEvaluationCell,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.cell.cell_id,
            proposal.cell,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_protocol(self, protocol_id: str) -> tuple[GuidanceEvaluationCell, ...]:
        return self._list_by_relationship("protocol_id", protocol_id)


GuidanceEvaluationCellRepository = GuidanceCellRepository


class ModelHarnessProtocolRepository(GovernedAppendOnlyRecordRepository[ModelHarnessProtocol]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=model_harness_protocols,
            model_type=ModelHarnessProtocol,
            identifier_field="protocol_id",
        )

    def add_from_proposal(
        self,
        proposal: RecordModelHarnessProtocol,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.protocol.protocol_id,
            proposal.protocol,
            created_at,
            transaction_id,
            governing_policy_hash,
        )


class ModelHarnessCellRepository(GovernedAppendOnlyRecordRepository[ModelHarnessCell]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=model_harness_cells,
            model_type=ModelHarnessCell,
            identifier_field="cell_id",
            relationship_fields={"protocol_id": "protocol_id"},
        )

    def add_from_proposal(
        self,
        proposal: AppendModelHarnessCell,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.cell.cell_id,
            proposal.cell,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def list_for_protocol(self, protocol_id: str) -> tuple[ModelHarnessCell, ...]:
        return self._list_by_relationship("protocol_id", protocol_id)


class ModelHarnessAnalysisRepository(GovernedAppendOnlyRecordRepository[ModelHarnessAnalysis]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=model_harness_analyses,
            model_type=ModelHarnessAnalysis,
            identifier_field="protocol_id",
        )

    def add_from_proposal(
        self,
        proposal: RecordModelHarnessAnalysis,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        self.add(
            proposal.analysis.protocol_id,
            proposal.analysis,
            created_at,
            transaction_id,
            governing_policy_hash,
        )


class _HarnessExecutionTraceEnvelopeRepository(
    GovernedAppendOnlyRecordRepository[HarnessExecutionTraceStorageEnvelope]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_execution_traces,
            model_type=HarnessExecutionTraceStorageEnvelope,
            identifier_field="trace_id",
            relationship_fields={"protocol_id": "protocol_id"},
        )
        self._record_decoder = _decode_harness_trace_storage_envelope


class HarnessExecutionTraceRepository:
    def __init__(self, connection: Connection) -> None:
        self._records = _HarnessExecutionTraceEnvelopeRepository(connection)

    def add_from_proposal(
        self,
        proposal: RecordHarnessExecutionTrace,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        envelope = HarnessExecutionTraceStorageEnvelope.from_harness_execution_trace(
            proposal.envelope.trace
        )
        self._records.add(
            envelope.trace_id,
            envelope,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def get(self, record_id: str) -> HarnessExecutionTrace | None:
        envelope = self._records.get(record_id)
        return None if envelope is None else envelope.record

    def list_all(self) -> tuple[HarnessExecutionTrace, ...]:
        return tuple(item.record for item in self._records.list_all())

    def list_for_protocol(self, protocol_id: str) -> tuple[HarnessExecutionTrace, ...]:
        return tuple(
            item.record for item in self._records._list_by_relationship("protocol_id", protocol_id)
        )


class _RewardAssessmentEnvelopeRepository(
    GovernedAppendOnlyRecordRepository[RewardAssessmentStorageEnvelope]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=reward_assessments,
            model_type=RewardAssessmentStorageEnvelope,
            identifier_field="assessment_id",
            relationship_fields={"trace_id": "trace_id", "observation_id": "observation_id"},
        )
        self._record_decoder = _decode_reward_assessment_storage_envelope


class RewardAssessmentRepository:
    def __init__(self, connection: Connection) -> None:
        self._records = _RewardAssessmentEnvelopeRepository(connection)

    def add_from_proposal(
        self,
        proposal: RecordRewardAssessment,
        *,
        created_at: UtcTimestamp,
        transaction_id: str,
        governing_policy_hash: str,
    ) -> None:
        _require_proposal_transaction(proposal, transaction_id)
        envelope = RewardAssessmentStorageEnvelope.from_reward_assessment(proposal.assessment)
        self._records.add(
            envelope.assessment_id,
            envelope,
            created_at,
            transaction_id,
            governing_policy_hash,
        )

    def get(self, record_id: str) -> RewardValidityAssessment | None:
        envelope = self._records.get(record_id)
        return None if envelope is None else envelope.record

    def list_all(self) -> tuple[RewardValidityAssessment, ...]:
        return tuple(item.record for item in self._records.list_all())

    def list_for_trace(self, trace_id: str) -> tuple[RewardValidityAssessment, ...]:
        return tuple(
            item.record for item in self._records._list_by_relationship("trace_id", trace_id)
        )

    def list_for_observation(
        self,
        observation_id: str,
    ) -> tuple[RewardValidityAssessment, ...]:
        return tuple(
            item.record
            for item in self._records._list_by_relationship("observation_id", observation_id)
        )


def _decode_harness_trace_storage_envelope(
    record_json: str,
) -> HarnessExecutionTraceStorageEnvelope:
    decoded = json.loads(record_json)
    if type(decoded) is not dict or set(decoded) != {"trace_id", "protocol_id", "record"}:
        raise ValueError("harness trace storage envelope has the wrong shape")
    record_payload = decoded["record"]
    if type(record_payload) is not dict:
        raise ValueError("harness trace storage record must be an object")
    trace = parse_untrusted_harness_execution_trace(canonical_json_bytes(record_payload))
    return HarnessExecutionTraceStorageEnvelope(
        trace_id=decoded["trace_id"],
        protocol_id=decoded["protocol_id"],
        record=trace,
    )


def _decode_reward_assessment_storage_envelope(
    record_json: str,
) -> RewardAssessmentStorageEnvelope:
    decoded = json.loads(record_json)
    if type(decoded) is not dict or set(decoded) != {
        "assessment_id",
        "trace_id",
        "observation_id",
        "record",
    }:
        raise ValueError("reward assessment storage envelope has the wrong shape")
    record_payload = decoded["record"]
    if type(record_payload) is not dict:
        raise ValueError("reward assessment storage record must be an object")
    # Reward values use the transaction boundary's tagged numeric/categorical wire format.
    # Reusing that public safe parser avoids a second decoder; the synthetic proposal identity
    # is discarded immediately and grants no repository or execution authority.
    proposal = parse_untrusted_proposal_json(
        canonical_json_bytes(
            {
                "proposal_id": "storage-reward-decoder",
                "idempotency_key": "storage-reward-decoder",
                "proposer": {
                    "actor_id": "storage-decoder",
                    "kind": "model",
                    "created_at": "2026-01-01T00:00:00Z",
                    "provider_id": "storage",
                    "model_id": "storage",
                    "adapter_id": "storage",
                    "configuration_hash": "0" * 64,
                },
                "approval": None,
                "proposal_type": "record_reward_assessment",
                "observation": record_payload.get("observation"),
                "findings": record_payload.get("findings"),
                "assessment": record_payload,
            }
        )
    )
    if not isinstance(proposal, RecordRewardAssessment):
        raise ValueError("reward assessment storage record has the wrong proposal type")
    return RewardAssessmentStorageEnvelope(
        assessment_id=decoded["assessment_id"],
        trace_id=decoded["trace_id"],
        observation_id=decoded["observation_id"],
        record=proposal.assessment,
    )
