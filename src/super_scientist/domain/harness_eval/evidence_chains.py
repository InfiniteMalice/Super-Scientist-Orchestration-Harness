from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from super_scientist.domain.harness_eval.matrix import (
    ModelHarnessCoordinate,
    ModelHarnessProtocol,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.harness_eval.rewards import RewardValidityAssessment
from super_scientist.domain.harness_eval.traces import (
    BoundedTraceIdentifier,
    HarnessExecutionTrace,
    TraceFreshness,
)
from super_scientist.domain.primitives import Sha256Hex, canonical_json_bytes, sha256_hex


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


class _HarnessCellEvidenceChainPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    chain_id: BoundedTraceIdentifier
    protocol: ModelHarnessProtocol
    coordinate: ModelHarnessCoordinate
    trace: HarnessExecutionTrace
    freshness: TraceFreshness
    assessment: RewardValidityAssessment

    @model_validator(mode="after")
    def require_exact_chain(self) -> Self:
        binding = self.trace.observed_binding
        if (
            binding.model_harness_protocol != self.protocol
            or binding.protocol_id != self.protocol.protocol_id
            or binding.protocol_version != self.protocol.version
            or binding.protocol_hash != self.protocol.content_hash
            or binding.task_id != self.protocol.task_set_id
            or binding.task_input_hash != self.protocol.task_set_hash
            or binding.model != self.coordinate.model
            or binding.harness != self.coordinate.harness
            or binding.partition is not self.coordinate.partition
            or binding.validator_id != self.protocol.verifier_id
            or binding.validator_version != self.protocol.verifier_version
            or binding.checker_id != self.protocol.checker_id
            or binding.checker_version != self.protocol.checker_version
            or binding.artifact_ids != self.protocol.artifact_ids
            or binding.output_schema_hash != self.protocol.output_schema_hash
        ):
            raise ValueError("cell evidence trace must match the exact protocol coordinate")
        if (
            self.freshness.trace_id != self.trace.trace_id
            or self.freshness.trace_hash != self.trace.content_hash
        ):
            raise ValueError("cell evidence freshness must bind the exact trace")
        if (
            self.assessment.trace != self.trace
            or self.assessment.trace_id != self.trace.trace_id
            or self.assessment.trace_hash != self.trace.content_hash
            or self.assessment.freshness != self.freshness
            or self.assessment.freshness_hash != self.freshness.content_hash
        ):
            raise ValueError("cell evidence assessment must bind the exact trace freshness")
        return self


def _chain_id(
    protocol: ModelHarnessProtocol,
    coordinate: ModelHarnessCoordinate,
    trace: HarnessExecutionTrace,
    freshness: TraceFreshness,
    assessment: RewardValidityAssessment,
) -> str:
    return "cell-evidence-" + sha256_hex(
        canonical_json_bytes(
            {
                "protocol_hash": protocol.content_hash,
                "coordinate": coordinate.model_dump(mode="json"),
                "trace_hash": trace.content_hash,
                "freshness_hash": freshness.content_hash,
                "assessment_hash": assessment.content_hash,
            }
        )
    )


class HarnessCellEvidenceChain(_HarnessCellEvidenceChainPayload):
    """One resolved trace/freshness/reward chain for exactly one matrix coordinate."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        supplied = dict(values)
        supplied.setdefault(
            "chain_id",
            _chain_id(
                supplied["protocol"],
                supplied["coordinate"],
                supplied["trace"],
                supplied["freshness"],
                supplied["assessment"],
            ),
        )
        payload = _HarnessCellEvidenceChainPayload(**supplied)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=harness_cell_evidence_chain_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_identity_and_hash(self) -> Self:
        expected_id = _chain_id(
            self.protocol,
            self.coordinate,
            self.trace,
            self.freshness,
            self.assessment,
        )
        if self.chain_id != expected_id:
            raise ValueError("chain_id must address the exact cell evidence inputs")
        if self.content_hash != harness_cell_evidence_chain_hash(self):
            raise ValueError("content_hash must canonically address the cell evidence chain")
        return self


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
    "harness_cell_evidence_chain_hash",
    "harness_cell_evidence_chain_receipt",
]
