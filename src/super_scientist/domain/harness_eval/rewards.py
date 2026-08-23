from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.harness_eval.traces import (
    BoundedTraceIdentifier,
    EnvironmentEventKind,
    ExecutionStatus,
    HarnessExecutionTrace,
    MetadataAvailability,
    RewardObservation,
    ToolObservationStatus,
    TraceBindingMismatch,
    TraceFreshness,
    _canonical_record_hash,
    trace_freshness,
)
from super_scientist.domain.primitives import Sha256Hex, sha256_hex

MAX_REWARD_FINDINGS = 256
MAX_REWARD_EVIDENCE = 256

BoundedAssessmentIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=200),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


class RewardValidityStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"


class RewardInvalidationReason(StrEnum):
    ENVIRONMENT_CRASH = "ENVIRONMENT_CRASH"
    INCOMPLETE_EXECUTION = "INCOMPLETE_EXECUTION"
    VERIFIER_MISMATCH = "VERIFIER_MISMATCH"
    VERIFIER_FAILURE = "VERIFIER_FAILURE"
    CORRUPTED_ARTIFACT = "CORRUPTED_ARTIFACT"
    PROTECTED_ANSWER_LEAKAGE = "PROTECTED_ANSWER_LEAKAGE"
    REWARD_HACKING_FINDING = "REWARD_HACKING_FINDING"
    EVALUATOR_FAILURE = "EVALUATOR_FAILURE"
    STALE_HARNESS_TRACE = "STALE_HARNESS_TRACE"
    TASK_RUNTIME_MISMATCH = "TASK_RUNTIME_MISMATCH"
    UNKNOWN_EVIDENCE = "UNKNOWN_EVIDENCE"


class RewardHackingFamily(StrEnum):
    PROXY_GAMING = "PROXY_GAMING"
    VERIFIER_GAMING = "VERIFIER_GAMING"
    ENVIRONMENT_TAMPERING = "ENVIRONMENT_TAMPERING"
    DATA_OR_ANSWER_LEAKAGE = "DATA_OR_ANSWER_LEAKAGE"
    REWARD_CHANNEL_MANIPULATION = "REWARD_CHANNEL_MANIPULATION"
    METRIC_CHERRY_PICKING = "METRIC_CHERRY_PICKING"
    PREMATURE_TERMINATION = "PREMATURE_TERMINATION"
    RESOURCE_ACCOUNTING_EVASION = "RESOURCE_ACCOUNTING_EVASION"
    TRACE_INCONSISTENCY = "TRACE_INCONSISTENCY"
    DISTRIBUTION_OR_PARTITION_CONTAMINATION = "DISTRIBUTION_OR_PARTITION_CONTAMINATION"


class RewardHackingFindingStatus(StrEnum):
    INVALIDATING = "INVALIDATING"
    INCONCLUSIVE = "INCONCLUSIVE"
    CLEARED = "CLEARED"


_REASON_ORDER = {item: index for index, item in enumerate(RewardInvalidationReason)}


def _assessment_evidence_ids(
    observation_evidence_id: str | None,
    findings: tuple[RewardHackingFinding, ...],
) -> tuple[str, ...]:
    evidence_ids = {
        item for finding in findings for item in finding.evidence_ids
    }
    if observation_evidence_id is not None:
        evidence_ids.add(observation_evidence_id)
    return tuple(sorted(evidence_ids))


class _RewardHackingFindingPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    finding_id: BoundedTraceIdentifier
    family: RewardHackingFamily
    status: RewardHackingFindingStatus
    trace_id: BoundedTraceIdentifier
    trace_hash: Sha256Hex
    observation_id: BoundedTraceIdentifier
    observation_hash: Sha256Hex
    evidence_ids: tuple[BoundedTraceIdentifier, ...] = Field(
        min_length=1,
        max_length=MAX_REWARD_EVIDENCE,
    )

    @field_validator("evidence_ids")
    @classmethod
    def require_canonical_evidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or values != tuple(sorted(values)):
            raise ValueError("finding observable evidence must be unique and canonically ordered")
        return values


class RewardHackingFinding(_RewardHackingFindingPayload):
    """A closed finding over observable evidence; it makes no claim about hidden motive."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _RewardHackingFindingPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=reward_hacking_finding_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != reward_hacking_finding_hash(self):
            raise ValueError("content_hash must canonically address the reward-hacking finding")
        return self


def reward_hacking_finding_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _RewardValidityAssessmentPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    assessment_id: BoundedAssessmentIdentifier
    observation: RewardObservation
    trace: HarnessExecutionTrace
    trace_id: BoundedTraceIdentifier
    trace_hash: Sha256Hex
    findings: tuple[RewardHackingFinding, ...] = Field(max_length=MAX_REWARD_FINDINGS)
    finding_ids: tuple[BoundedTraceIdentifier, ...] = Field(max_length=MAX_REWARD_FINDINGS)
    evidence_ids: tuple[BoundedTraceIdentifier, ...] = Field(max_length=MAX_REWARD_EVIDENCE)
    verifier_succeeded: bool | None
    freshness: TraceFreshness
    assessor_id: BoundedTraceIdentifier
    assessor_version: BoundedTraceIdentifier
    status: RewardValidityStatus
    reasons: tuple[RewardInvalidationReason, ...] = Field(
        max_length=len(RewardInvalidationReason)
    )
    freshness_hash: Sha256Hex

    @field_validator("reasons")
    @classmethod
    def require_canonical_reasons(
        cls,
        values: tuple[RewardInvalidationReason, ...],
    ) -> tuple[RewardInvalidationReason, ...]:
        if len(values) != len(set(values)) or values != tuple(
            sorted(values, key=_REASON_ORDER.__getitem__)
        ):
            raise ValueError("reward reasons must be unique and canonically ordered")
        return values

    @model_validator(mode="after")
    def require_recomputed_reward_validity(self) -> Self:
        if self.trace_id != self.trace.trace_id or self.trace_hash != self.trace.content_hash:
            raise ValueError("assessment must bind the exact harness trace")
        if (
            self.trace.reward_observation is None
            or self.trace.reward_observation != self.observation
            or self.trace.reward_observation_hash.value != self.observation.content_hash
        ):
            raise ValueError("assessment must bind the trace's exact reward observation")
        expected_findings = tuple(sorted(self.findings, key=lambda item: item.finding_id))
        if self.findings != expected_findings or len(self.findings) != len(
            {item.finding_id for item in self.findings}
        ):
            raise ValueError("assessment findings must be unique and canonically ordered")
        if self.finding_ids != tuple(item.finding_id for item in self.findings):
            raise ValueError("finding_ids must exactly identify the embedded findings")
        expected_evidence = _assessment_evidence_ids(
            self.observation.evidence_id,
            self.findings,
        )
        if self.evidence_ids != expected_evidence:
            raise ValueError("assessment evidence IDs must exactly bind observable evidence")
        for finding in self.findings:
            if (
                finding.trace_id != self.trace_id
                or finding.trace_hash != self.trace_hash
                or finding.observation_id != self.observation.observation_id
                or finding.observation_hash != self.observation.content_hash
            ):
                raise ValueError("reward-hacking finding must bind exact trace and reward identity")
        expected_freshness = trace_freshness(self.trace)
        if self.freshness != expected_freshness or self.freshness_hash != self.trace.content_hash:
            raise ValueError("assessment freshness must be recomputed from exact trace hashes")
        if (
            self.assessor_id != self.observation.evaluator_id
            or self.assessor_version != self.observation.evaluator_version
        ):
            raise ValueError("assessment must bind the exact evaluator identity")
        expected_reasons = ordered_invalidation_reasons(
            self.observation,
            self.trace,
            self.findings,
            self.verifier_succeeded,
        )
        expected_status = reward_status(expected_reasons)
        if self.reasons != expected_reasons or self.status is not expected_status:
            raise ValueError("assessment status and reasons must equal recomputed reward validity")
        if self.assessment_id != reward_assessment_id(
            self.observation,
            self.trace,
            self.findings,
            self.verifier_succeeded,
        ):
            raise ValueError("assessment_id must address the exact validity inputs")
        return self


class RewardValidityAssessment(_RewardValidityAssessmentPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _RewardValidityAssessmentPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=reward_assessment_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != reward_assessment_hash(self):
            raise ValueError("content_hash must canonically address reward validity")
        return self


def reward_assessment_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


def reward_assessment_id(
    observation: RewardObservation,
    trace: HarnessExecutionTrace,
    findings: tuple[RewardHackingFinding, ...],
    verifier_succeeded: bool | None,
) -> str:
    payload_hash = sha256_hex(
        _canonical_record_hash(
            {
                "observation_hash": observation.content_hash,
                "trace_hash": trace.content_hash,
                "finding_hashes": [item.content_hash for item in findings],
                "verifier_succeeded": verifier_succeeded,
            }
        ).encode("ascii")
    )
    return f"reward-assessment-{payload_hash}"


def ordered_invalidation_reasons(
    observation: RewardObservation,
    trace: HarnessExecutionTrace,
    findings: tuple[RewardHackingFinding, ...],
    verifier_succeeded: bool | None,
) -> tuple[RewardInvalidationReason, ...]:
    reasons: set[RewardInvalidationReason] = set()
    event_kinds = {item.kind for item in trace.environment_events}
    if (
        trace.execution_status is ExecutionStatus.CRASHED
        or EnvironmentEventKind.CRASHED in event_kinds
    ):
        reasons.add(RewardInvalidationReason.ENVIRONMENT_CRASH)
    if trace.execution_status is not ExecutionStatus.COMPLETED or any(
        item.status is not ToolObservationStatus.SUCCEEDED for item in trace.tool_observations
    ):
        reasons.add(RewardInvalidationReason.INCOMPLETE_EXECUTION)
    if (
        observation.verifier_id != trace.observed_binding.validator_id
        or observation.verifier_version != trace.observed_binding.validator_version
        or observation.checker_id != trace.observed_binding.checker_id
        or observation.checker_version != trace.observed_binding.checker_version
    ):
        reasons.add(RewardInvalidationReason.VERIFIER_MISMATCH)
    if verifier_succeeded is False:
        reasons.add(RewardInvalidationReason.VERIFIER_FAILURE)
    if trace.artifact_integrity.value is False:
        reasons.add(RewardInvalidationReason.CORRUPTED_ARTIFACT)
    if trace.protected_boundary_crossed.value is True:
        reasons.add(RewardInvalidationReason.PROTECTED_ANSWER_LEAKAGE)
    if any(
        item.status is RewardHackingFindingStatus.INVALIDATING for item in findings
    ):
        reasons.add(RewardInvalidationReason.REWARD_HACKING_FINDING)
    if trace.evaluator_succeeded.value is False:
        reasons.add(RewardInvalidationReason.EVALUATOR_FAILURE)
    freshness = trace_freshness(trace)
    runtime_mismatches = {
        TraceBindingMismatch.TASK,
        TraceBindingMismatch.ENVIRONMENT,
    }
    if any(item not in runtime_mismatches for item in freshness.mismatches):
        reasons.add(RewardInvalidationReason.STALE_HARNESS_TRACE)
    if any(item in runtime_mismatches for item in freshness.mismatches):
        reasons.add(RewardInvalidationReason.TASK_RUNTIME_MISMATCH)
    required_metadata = (
        trace.artifact_integrity,
        trace.protected_boundary_crossed,
        trace.evaluator_succeeded,
    )
    if (
        observation.value is None
        or verifier_succeeded is None
        or any(item.status is not MetadataAvailability.AVAILABLE for item in required_metadata)
        or any(item.status is RewardHackingFindingStatus.INCONCLUSIVE for item in findings)
    ):
        reasons.add(RewardInvalidationReason.UNKNOWN_EVIDENCE)
    return tuple(sorted(reasons, key=_REASON_ORDER.__getitem__))


def reward_status(
    reasons: tuple[RewardInvalidationReason, ...],
) -> RewardValidityStatus:
    if any(item is not RewardInvalidationReason.UNKNOWN_EVIDENCE for item in reasons):
        return RewardValidityStatus.INVALID
    if reasons:
        return RewardValidityStatus.INCONCLUSIVE
    return RewardValidityStatus.VALID


def assess_reward_validity(
    observation: RewardObservation,
    trace: HarnessExecutionTrace,
    findings: tuple[RewardHackingFinding, ...],
    *,
    verifier_succeeded: bool | None,
) -> RewardValidityAssessment:
    validated_observation = RewardObservation.model_validate(observation)
    validated_trace = HarnessExecutionTrace.model_validate(trace)
    if validated_trace.reward_observation != validated_observation:
        raise ValueError("reward observation must be the exact observation embedded in the trace")
    validated_findings = tuple(
        sorted(
            (RewardHackingFinding.model_validate(item) for item in findings),
            key=lambda item: item.finding_id,
        )
    )
    if len(validated_findings) != len({item.finding_id for item in validated_findings}):
        raise ValueError("reward-hacking findings must have unique identifiers")
    for finding in validated_findings:
        if (
            finding.trace_id != validated_trace.trace_id
            or finding.trace_hash != validated_trace.content_hash
            or finding.observation_id != validated_observation.observation_id
            or finding.observation_hash != validated_observation.content_hash
        ):
            raise ValueError("reward-hacking finding must bind exact trace and reward identity")
    reasons = ordered_invalidation_reasons(
        validated_observation,
        validated_trace,
        validated_findings,
        verifier_succeeded,
    )
    freshness = trace_freshness(validated_trace)
    evidence_ids = _assessment_evidence_ids(
        validated_observation.evidence_id,
        validated_findings,
    )
    return RewardValidityAssessment.build(
        assessment_id=reward_assessment_id(
            validated_observation,
            validated_trace,
            validated_findings,
            verifier_succeeded,
        ),
        observation=validated_observation,
        trace=validated_trace,
        trace_id=validated_trace.trace_id,
        trace_hash=validated_trace.content_hash,
        findings=validated_findings,
        finding_ids=tuple(item.finding_id for item in validated_findings),
        evidence_ids=evidence_ids,
        verifier_succeeded=verifier_succeeded,
        freshness=freshness,
        assessor_id=validated_observation.evaluator_id,
        assessor_version=validated_observation.evaluator_version,
        status=reward_status(reasons),
        reasons=reasons,
        freshness_hash=validated_trace.content_hash,
    )


def valid_reward_evidence(
    assessments: tuple[RewardValidityAssessment, ...],
) -> tuple[RewardObservation, ...]:
    if not isinstance(assessments, tuple):
        raise TypeError("reward assessments must be an exact tuple")
    validated = tuple(RewardValidityAssessment.model_validate(item) for item in assessments)
    assessment_ids = tuple(item.assessment_id for item in validated)
    if len(assessment_ids) != len(set(assessment_ids)):
        raise ValueError("reward evidence assessments must be unique")
    evidence = tuple(
        item.observation for item in validated if item.status is RewardValidityStatus.VALID
    )
    observation_ids = tuple(item.observation_id for item in evidence)
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("valid reward evidence observations must be unique")
    return evidence


__all__ = [
    "RewardHackingFamily",
    "RewardHackingFinding",
    "RewardHackingFindingStatus",
    "RewardInvalidationReason",
    "RewardValidityAssessment",
    "RewardValidityStatus",
    "assess_reward_validity",
    "ordered_invalidation_reasons",
    "reward_assessment_hash",
    "reward_hacking_finding_hash",
    "valid_reward_evidence",
]
