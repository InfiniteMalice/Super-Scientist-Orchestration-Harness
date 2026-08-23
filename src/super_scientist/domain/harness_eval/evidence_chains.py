from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.harness_eval.matrix import (
    ModelHarnessCoordinate,
    ModelHarnessProtocol,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.harness_eval.rewards import (
    RewardValidityAssessment,
    RewardValidityStatus,
)
from super_scientist.domain.harness_eval.traces import (
    BoundedTraceIdentifier,
    HarnessExecutionTrace,
    TraceFreshness,
    TraceFreshnessStatus,
)
from super_scientist.domain.primitives import Sha256Hex, canonical_json_bytes, sha256_hex


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


def _strictly_revalidate_snapshot[SnapshotT: BaseModel](
    record: object,
    expected_type: type[SnapshotT],
) -> SnapshotT:
    """Reject copied or constructed instances that bypassed canonical validators."""
    if type(record) is not expected_type:
        raise ValueError("projection requires canonical validated snapshots")
    try:
        return expected_type.model_validate(
            record.model_dump(mode="python"),
            strict=True,
        )
    except (TypeError, ValueError):
        raise ValueError("projection requires canonical validated snapshots") from None


class _HarnessCellEvidenceChainPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    chain_id: BoundedTraceIdentifier
    protocol_receipt: EvidenceReceipt
    coordinate: ModelHarnessCoordinate
    trace_receipt: EvidenceReceipt
    freshness_receipt: EvidenceReceipt
    assessment_receipt: EvidenceReceipt


def _chain_id(
    protocol_receipt: EvidenceReceipt,
    coordinate: ModelHarnessCoordinate,
    trace_receipt: EvidenceReceipt,
    freshness_receipt: EvidenceReceipt,
    assessment_receipt: EvidenceReceipt,
) -> str:
    return "cell-evidence-" + sha256_hex(
        canonical_json_bytes(
            {
                "protocol_receipt": protocol_receipt.model_dump(mode="json"),
                "coordinate": coordinate.model_dump(mode="json"),
                "trace_receipt": trace_receipt.model_dump(mode="json"),
                "freshness_receipt": freshness_receipt.model_dump(mode="json"),
                "assessment_receipt": assessment_receipt.model_dump(mode="json"),
            }
        )
    )


class HarnessCellEvidenceChain(_HarnessCellEvidenceChainPayload):
    """Compact receipt join for one coordinate and shared validated snapshots."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        supplied = dict(values)
        supplied.setdefault(
            "chain_id",
            _chain_id(
                supplied["protocol_receipt"],
                supplied["coordinate"],
                supplied["trace_receipt"],
                supplied["freshness_receipt"],
                supplied["assessment_receipt"],
            ),
        )
        payload = _HarnessCellEvidenceChainPayload(**supplied)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=harness_cell_evidence_chain_hash(payload),
        )

    @classmethod
    def from_snapshots(
        cls,
        *,
        protocol: ModelHarnessProtocol,
        coordinate: ModelHarnessCoordinate,
        trace: HarnessExecutionTrace,
        freshness: TraceFreshness,
        assessment: RewardValidityAssessment,
    ) -> Self:
        validated_protocol = _strictly_revalidate_snapshot(protocol, ModelHarnessProtocol)
        validated_coordinate = _strictly_revalidate_snapshot(coordinate, ModelHarnessCoordinate)
        validated_trace = _strictly_revalidate_snapshot(trace, HarnessExecutionTrace)
        validated_freshness = _strictly_revalidate_snapshot(freshness, TraceFreshness)
        validated_assessment = _strictly_revalidate_snapshot(
            assessment,
            RewardValidityAssessment,
        )
        return cls._from_validated_snapshots(
            protocol=validated_protocol,
            coordinate=validated_coordinate,
            trace=validated_trace,
            freshness=validated_freshness,
            assessment=validated_assessment,
        )

    @classmethod
    def _from_validated_snapshots(
        cls,
        *,
        protocol: ModelHarnessProtocol,
        coordinate: ModelHarnessCoordinate,
        trace: HarnessExecutionTrace,
        freshness: TraceFreshness,
        assessment: RewardValidityAssessment,
    ) -> Self:
        validated_protocol = protocol
        validated_coordinate = coordinate
        validated_trace = trace
        validated_freshness = freshness
        validated_assessment = assessment
        binding = validated_trace.observed_binding
        if (
            binding.protocol_id != validated_protocol.protocol_id
            or binding.protocol_version != validated_protocol.version
            or binding.protocol_hash != validated_protocol.content_hash
            or binding.task_id != validated_protocol.task_set_id
            or binding.task_input_hash != validated_protocol.task_set_hash
            or binding.model != validated_coordinate.model
            or binding.harness != validated_coordinate.harness
            or binding.partition is not validated_coordinate.partition
            or binding.validator_id != validated_protocol.verifier_id
            or binding.validator_version != validated_protocol.verifier_version
            or binding.checker_id != validated_protocol.checker_id
            or binding.checker_version != validated_protocol.checker_version
            or binding.authorized_artifact_ids != validated_protocol.artifact_ids
            or binding.artifact_ids != validated_protocol.artifact_ids
            or binding.output_schema_hash != validated_protocol.output_schema_hash
        ):
            raise ValueError("cell evidence trace must match the exact protocol coordinate")
        if (
            validated_freshness.trace_id != validated_trace.trace_id
            or validated_freshness.trace_hash != validated_trace.content_hash
            or validated_assessment.trace_id != validated_trace.trace_id
            or validated_assessment.trace_hash != validated_trace.content_hash
            or validated_assessment.freshness_hash != validated_freshness.content_hash
        ):
            raise ValueError("cell evidence snapshots must match the exact trace and coordinate")
        return cls.build(
            protocol_receipt=EvidenceReceipt(
                record_id=validated_protocol.protocol_id,
                schema_version=validated_protocol.schema_version,
                content_hash=validated_protocol.content_hash,
            ),
            coordinate=validated_coordinate,
            trace_receipt=EvidenceReceipt(
                record_id=validated_trace.trace_id,
                schema_version=validated_trace.schema_version,
                content_hash=validated_trace.content_hash,
            ),
            freshness_receipt=EvidenceReceipt(
                record_id=validated_freshness.freshness_id,
                schema_version=validated_freshness.schema_version,
                content_hash=validated_freshness.content_hash,
            ),
            assessment_receipt=EvidenceReceipt(
                record_id=validated_assessment.assessment_id,
                schema_version=validated_assessment.schema_version,
                content_hash=validated_assessment.content_hash,
            ),
        )

    @model_validator(mode="after")
    def require_canonical_identity_and_hash(self) -> Self:
        expected_id = _chain_id(
            self.protocol_receipt,
            self.coordinate,
            self.trace_receipt,
            self.freshness_receipt,
            self.assessment_receipt,
        )
        if self.chain_id != expected_id:
            raise ValueError("chain_id must address the exact cell evidence receipts")
        if self.content_hash != harness_cell_evidence_chain_hash(self):
            raise ValueError("content_hash must canonically address the cell evidence chain")
        return self


class _HarnessEvidenceSnapshotRecordPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    chain_receipt: EvidenceReceipt
    trace_receipt: EvidenceReceipt
    freshness_receipt: EvidenceReceipt
    assessment_receipt: EvidenceReceipt
    freshness_status: TraceFreshnessStatus
    assessment_status: RewardValidityStatus


class HarnessEvidenceSnapshotRecord(_HarnessEvidenceSnapshotRecordPayload):
    """Compact handler-resolved projection of one validated snapshot chain."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _HarnessEvidenceSnapshotRecordPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=_snapshot_record_hash(payload),
        )

    @classmethod
    def from_snapshots(
        cls,
        *,
        chain: HarnessCellEvidenceChain,
        trace: HarnessExecutionTrace,
        freshness: TraceFreshness,
        assessment: RewardValidityAssessment,
    ) -> Self:
        validated_chain = _strictly_revalidate_snapshot(chain, HarnessCellEvidenceChain)
        validated_trace = _strictly_revalidate_snapshot(trace, HarnessExecutionTrace)
        validated_freshness = _strictly_revalidate_snapshot(freshness, TraceFreshness)
        validated_assessment = _strictly_revalidate_snapshot(
            assessment,
            RewardValidityAssessment,
        )
        return cls._from_validated_snapshots(
            chain=validated_chain,
            trace=validated_trace,
            freshness=validated_freshness,
            assessment=validated_assessment,
        )

    @classmethod
    def _from_validated_snapshots(
        cls,
        *,
        chain: HarnessCellEvidenceChain,
        trace: HarnessExecutionTrace,
        freshness: TraceFreshness,
        assessment: RewardValidityAssessment,
    ) -> Self:
        validated_chain = chain
        validated_trace = trace
        validated_freshness = freshness
        validated_assessment = assessment
        trace_receipt = EvidenceReceipt(
            record_id=validated_trace.trace_id,
            schema_version=validated_trace.schema_version,
            content_hash=validated_trace.content_hash,
        )
        freshness_receipt = EvidenceReceipt(
            record_id=validated_freshness.freshness_id,
            schema_version=validated_freshness.schema_version,
            content_hash=validated_freshness.content_hash,
        )
        assessment_receipt = EvidenceReceipt(
            record_id=validated_assessment.assessment_id,
            schema_version=validated_assessment.schema_version,
            content_hash=validated_assessment.content_hash,
        )
        if (
            validated_chain.trace_receipt != trace_receipt
            or validated_chain.freshness_receipt != freshness_receipt
            or validated_chain.assessment_receipt != assessment_receipt
            or validated_freshness.trace_id != validated_trace.trace_id
            or validated_freshness.trace_hash != validated_trace.content_hash
            or validated_assessment.trace_id != validated_trace.trace_id
            or validated_assessment.trace_hash != validated_trace.content_hash
            or validated_assessment.freshness_hash != validated_freshness.content_hash
        ):
            raise ValueError("snapshot projection must match the exact compact evidence chain")
        return cls.build(
            chain_receipt=harness_cell_evidence_chain_receipt(validated_chain),
            trace_receipt=trace_receipt,
            freshness_receipt=freshness_receipt,
            assessment_receipt=assessment_receipt,
            freshness_status=validated_freshness.status,
            assessment_status=validated_assessment.status,
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _snapshot_record_hash(self):
            raise ValueError("content_hash must canonically address snapshot projection")
        return self


def _snapshot_record_hash(record: BaseModel | Mapping[str, object]) -> str:
    payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    payload.pop("content_hash", None)
    return sha256_hex(canonical_json_bytes(payload))


def project_harness_evidence_snapshots(
    *,
    protocol: ModelHarnessProtocol,
    coordinate: ModelHarnessCoordinate,
    trace: HarnessExecutionTrace,
    freshness: TraceFreshness,
    assessment: RewardValidityAssessment,
) -> tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord]:
    """Strictly validate one full chain once, then produce both compact projections."""
    validated_protocol = _strictly_revalidate_snapshot(protocol, ModelHarnessProtocol)
    validated_coordinate = _strictly_revalidate_snapshot(coordinate, ModelHarnessCoordinate)
    validated_trace = _strictly_revalidate_snapshot(trace, HarnessExecutionTrace)
    validated_freshness = _strictly_revalidate_snapshot(freshness, TraceFreshness)
    validated_assessment = _strictly_revalidate_snapshot(
        assessment,
        RewardValidityAssessment,
    )
    chain = HarnessCellEvidenceChain._from_validated_snapshots(
        protocol=validated_protocol,
        coordinate=validated_coordinate,
        trace=validated_trace,
        freshness=validated_freshness,
        assessment=validated_assessment,
    )
    record = HarnessEvidenceSnapshotRecord._from_validated_snapshots(
        chain=chain,
        trace=validated_trace,
        freshness=validated_freshness,
        assessment=validated_assessment,
    )
    return chain, record


class _HarnessEvidenceSnapshotIndexPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    records: tuple[HarnessEvidenceSnapshotRecord, ...] = Field(min_length=1, max_length=256)

    @field_validator("records")
    @classmethod
    def require_canonical_unique_records(
        cls,
        values: tuple[HarnessEvidenceSnapshotRecord, ...],
    ) -> tuple[HarnessEvidenceSnapshotRecord, ...]:
        keys = tuple(item.chain_receipt.record_id for item in values)
        trace_ids = tuple(item.trace_receipt.record_id for item in values)
        if len(keys) != len(set(keys)) or len(trace_ids) != len(set(trace_ids)):
            raise ValueError("shared snapshot index requires unique chain and trace identifiers")
        if keys != tuple(sorted(keys)):
            raise ValueError("shared snapshot index records must be canonically ordered")
        return values


class HarnessEvidenceSnapshotIndex(_HarnessEvidenceSnapshotIndexPayload):
    """Canonical compact index produced after a handler validates each snapshot once."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _HarnessEvidenceSnapshotIndexPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=_snapshot_index_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _snapshot_index_hash(self):
            raise ValueError("content_hash must canonically address shared snapshot index")
        return self


def _snapshot_index_hash(record: BaseModel | Mapping[str, object]) -> str:
    payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    payload.pop("content_hash", None)
    return sha256_hex(canonical_json_bytes(payload))


def harness_cell_evidence_chain_hash(
    record: BaseModel | Mapping[str, object],
) -> str:
    payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    payload.pop("content_hash", None)
    return sha256_hex(canonical_json_bytes(payload))


def harness_cell_evidence_chain_receipt(
    chain: HarnessCellEvidenceChain,
) -> EvidenceReceipt:
    validated = HarnessCellEvidenceChain.model_validate(chain)
    return EvidenceReceipt(
        record_id=validated.chain_id,
        schema_version=validated.schema_version,
        content_hash=validated.content_hash,
    )


__all__ = [
    "HarnessCellEvidenceChain",
    "HarnessEvidenceSnapshotIndex",
    "HarnessEvidenceSnapshotRecord",
    "harness_cell_evidence_chain_hash",
    "harness_cell_evidence_chain_receipt",
    "project_harness_evidence_snapshots",
]
