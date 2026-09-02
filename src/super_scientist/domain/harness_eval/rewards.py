from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from super_scientist.domain.harness_eval.receipts import (
    EvidenceReceipt,
    ResolvedEvidenceInventory,
    ResolvedEvidenceKind,
    require_resolved_evidence,
)
from super_scientist.domain.harness_eval.traces import (
    BoundedTraceIdentifier,
    EnvironmentEventKind,
    ExecutionStatus,
    HarnessExecutionTrace,
    MetadataAvailability,
    RewardObservation,
    ToolObservationStatus,
    TraceBindingMismatch,
    TraceExpectation,
    TraceFreshness,
    _canonical_record_hash,
    trace_freshness,
)
from super_scientist.domain.primitives import Sha256Hex, sha256_hex

MAX_REWARD_EVIDENCE = 256


def _reject_nul_identifier(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Phase A identifier must not contain NUL")
    return value


BoundedAssessmentIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=200),
    AfterValidator(_reject_nul_identifier),
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


class VerificationOutcomeStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


# Exact maximum unique receipts admitted by two verification snapshots, ten
# diagnostics, coverage provenance, and one reward observation.
MAX_REWARD_ACCEPTED_EVIDENCE = (
    2 * (3 + MAX_REWARD_EVIDENCE)
    + len(RewardHackingFamily) * (2 + MAX_REWARD_EVIDENCE)
    + MAX_REWARD_EVIDENCE
    + 1
)


_REASON_ORDER = {item: index for index, item in enumerate(RewardInvalidationReason)}


def _accepted_reward_evidence_receipts(
    observation: RewardObservation,
    findings: tuple[RewardHackingFinding, ...],
    verification: VerificationOutcomeEvidence,
    diagnostic_coverage: RewardHackingCoverageAttestation,
    inventory: ResolvedEvidenceInventory,
) -> tuple[EvidenceReceipt, ...]:
    accepted: dict[tuple[str, int, str], EvidenceReceipt] = {}

    def accept(receipt: EvidenceReceipt, kind: ResolvedEvidenceKind) -> None:
        resolved = require_resolved_evidence(inventory, receipt, kind)
        key = (
            resolved.receipt.record_id,
            resolved.receipt.schema_version,
            resolved.receipt.content_hash,
        )
        accepted[key] = resolved.receipt

    for snapshot in (verification.verifier_result, verification.checker_result):
        accept(snapshot.source, ResolvedEvidenceKind.VERIFICATION_RESULT_SOURCE)
        accept(snapshot.result, ResolvedEvidenceKind.VERIFICATION_RESULT)
        accept(snapshot.resolver, ResolvedEvidenceKind.RESOLVER)
        for receipt in snapshot.observable_evidence:
            accept(receipt, ResolvedEvidenceKind.OBSERVABLE_EVIDENCE)
    for diagnostic in diagnostic_coverage.diagnostics:
        accept(diagnostic.source, ResolvedEvidenceKind.DIAGNOSTIC_SOURCE)
        accept(diagnostic.resolver, ResolvedEvidenceKind.RESOLVER)
        for receipt in diagnostic.observable_evidence:
            accept(receipt, ResolvedEvidenceKind.OBSERVABLE_EVIDENCE)
    for receipt in diagnostic_coverage.provenance:
        accept(receipt, ResolvedEvidenceKind.PROVENANCE)
    if observation.evidence_id is None:
        raise ValueError("reward observation requires accepted observable evidence")
    observation_record = next(
        (
            item
            for item in inventory.records
            if item.kind is ResolvedEvidenceKind.OBSERVABLE_EVIDENCE
            and item.receipt.record_id == observation.evidence_id
        ),
        None,
    )
    if observation_record is None:
        raise ValueError("resolved evidence inventory does not accept reward observation evidence")
    accept(observation_record.receipt, ResolvedEvidenceKind.OBSERVABLE_EVIDENCE)
    finding_ids = {item for finding in findings for item in finding.evidence_ids}
    accepted_diagnostic_ids = {
        receipt.record_id
        for diagnostic in diagnostic_coverage.diagnostics
        for receipt in diagnostic.observable_evidence
    }
    if finding_ids != accepted_diagnostic_ids:
        raise ValueError("finding evidence IDs must equal accepted diagnostic evidence receipts")
    return tuple(accepted[key] for key in sorted(accepted))


class _ResolvedVerificationResultSnapshotPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    snapshot_id: BoundedTraceIdentifier
    executor: EvidenceReceipt
    result: EvidenceReceipt
    status: VerificationOutcomeStatus
    observable_evidence: tuple[EvidenceReceipt, ...] = Field(
        min_length=1,
        max_length=MAX_REWARD_EVIDENCE,
    )
    source: EvidenceReceipt
    resolver: EvidenceReceipt

    @field_validator("observable_evidence")
    @classmethod
    def require_canonical_observable_evidence(
        cls,
        values: tuple[EvidenceReceipt, ...],
    ) -> tuple[EvidenceReceipt, ...]:
        keys = tuple((item.record_id, item.schema_version, item.content_hash) for item in values)
        if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
            raise ValueError("resolved result evidence receipts must be unique and canonical")
        return values

    @model_validator(mode="after")
    def require_accepted_source_snapshot(self) -> Self:
        if (
            self.source.record_id != self.snapshot_id
            or self.source.content_hash != verification_result_status_snapshot_hash(self)
        ):
            raise ValueError("verification result source must address the exact status snapshot")
        if self.source.record_id == self.resolver.record_id:
            raise ValueError("verification result source and resolver must be distinct")
        return self


class ResolvedVerificationResultSnapshot(_ResolvedVerificationResultSnapshotPayload):
    """Result content and status resolved outside the pure domain layer."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ResolvedVerificationResultSnapshotPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=resolved_verification_result_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != resolved_verification_result_hash(self):
            raise ValueError("content_hash must canonically address the resolved result snapshot")
        return self


def resolved_verification_result_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


def verification_result_status_snapshot_hash(
    record: BaseModel | Mapping[str, object],
) -> str:
    if isinstance(record, BaseModel):
        return _canonical_record_hash(
            record,
            exclude_fields={"snapshot_id", "source", "resolver"},
        )
    payload = dict(record)
    payload.setdefault("schema_version", 1)
    return _canonical_record_hash(
        payload,
        exclude_fields={"snapshot_id", "source", "resolver"},
    )


class _VerificationOutcomeEvidencePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    outcome_id: BoundedTraceIdentifier
    verifier: EvidenceReceipt
    verifier_result: ResolvedVerificationResultSnapshot
    checker: EvidenceReceipt
    checker_result: ResolvedVerificationResultSnapshot

    @model_validator(mode="after")
    def require_result_executor_bindings(self) -> Self:
        if self.verifier_result.executor != self.verifier:
            raise ValueError("verifier result snapshot must bind the exact verifier")
        if self.checker_result.executor != self.checker:
            raise ValueError("checker result snapshot must bind the exact checker")
        return self


class VerificationOutcomeEvidence(_VerificationOutcomeEvidencePayload):
    """Receipt-bound observable outcomes for both verifier and checker execution."""

    content_hash: Sha256Hex

    @property
    def verifier_status(self) -> VerificationOutcomeStatus:
        return self.verifier_result.status

    @property
    def checker_status(self) -> VerificationOutcomeStatus:
        return self.checker_result.status

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.record_id
                    for snapshot in (self.verifier_result, self.checker_result)
                    for item in snapshot.observable_evidence
                }
            )
        )

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _VerificationOutcomeEvidencePayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=verification_outcome_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != verification_outcome_hash(self):
            raise ValueError("content_hash must canonically address verification outcomes")
        return self


def verification_outcome_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


class _ResolvedRewardHackingDiagnosticPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    family: RewardHackingFamily
    status: RewardHackingFindingStatus
    observable_evidence: tuple[EvidenceReceipt, ...] = Field(
        min_length=1,
        max_length=MAX_REWARD_EVIDENCE,
    )
    source: EvidenceReceipt
    resolver: EvidenceReceipt

    @field_validator("observable_evidence")
    @classmethod
    def require_canonical_observable_evidence(
        cls,
        values: tuple[EvidenceReceipt, ...],
    ) -> tuple[EvidenceReceipt, ...]:
        keys = tuple((item.record_id, item.schema_version, item.content_hash) for item in values)
        if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
            raise ValueError("diagnostic evidence receipts must be unique and canonical")
        return values

    @model_validator(mode="after")
    def require_accepted_source_snapshot(self) -> Self:
        if self.source.content_hash != reward_hacking_diagnostic_status_snapshot_hash(self):
            raise ValueError(
                "diagnostic source must address the exact status and evidence snapshot"
            )
        if self.source.record_id == self.resolver.record_id:
            raise ValueError("diagnostic source and resolver must be distinct")
        return self


class ResolvedRewardHackingDiagnostic(_ResolvedRewardHackingDiagnosticPayload):
    """One diagnostic result resolved from an accepted observable evidence source."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ResolvedRewardHackingDiagnosticPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=resolved_reward_hacking_diagnostic_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != resolved_reward_hacking_diagnostic_hash(self):
            raise ValueError("content_hash must canonically address the resolved diagnostic")
        return self


def resolved_reward_hacking_diagnostic_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


def reward_hacking_diagnostic_status_snapshot_hash(
    record: BaseModel | Mapping[str, object],
) -> str:
    if isinstance(record, BaseModel):
        return _canonical_record_hash(record, exclude_fields={"source", "resolver"})
    payload = dict(record)
    payload.setdefault("schema_version", 1)
    return _canonical_record_hash(payload, exclude_fields={"source", "resolver"})


class _RewardHackingCoverageAttestationPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    attestation_id: BoundedTraceIdentifier
    trace: EvidenceReceipt
    observation: EvidenceReceipt
    diagnostics: tuple[ResolvedRewardHackingDiagnostic, ...] = Field(
        min_length=len(RewardHackingFamily),
        max_length=len(RewardHackingFamily),
    )
    provenance: tuple[EvidenceReceipt, ...] = Field(
        min_length=1,
        max_length=MAX_REWARD_EVIDENCE,
    )

    @field_validator("diagnostics")
    @classmethod
    def require_complete_canonical_diagnostics(
        cls,
        values: tuple[ResolvedRewardHackingDiagnostic, ...],
    ) -> tuple[ResolvedRewardHackingDiagnostic, ...]:
        if tuple(item.family for item in values) != tuple(RewardHackingFamily):
            raise ValueError("coverage must resolve every reward-hacking family in canonical order")
        evidence_ids = tuple(
            receipt.record_id for item in values for receipt in item.observable_evidence
        )
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("coverage diagnostic evidence identifiers must be globally unique")
        return values

    @field_validator("provenance")
    @classmethod
    def require_canonical_provenance(
        cls,
        values: tuple[EvidenceReceipt, ...],
    ) -> tuple[EvidenceReceipt, ...]:
        keys = tuple((item.record_id, item.schema_version, item.content_hash) for item in values)
        if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
            raise ValueError("coverage provenance receipts must be unique and canonical")
        return values


class RewardHackingCoverageAttestation(_RewardHackingCoverageAttestationPayload):
    """Complete independently resolved diagnostic inventory for one trace observation."""

    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _RewardHackingCoverageAttestationPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=reward_hacking_coverage_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != reward_hacking_coverage_hash(self):
            raise ValueError("content_hash must canonically address diagnostic coverage")
        return self


def reward_hacking_coverage_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


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


def _require_complete_diagnostic_coverage(
    findings: tuple[RewardHackingFinding, ...],
) -> tuple[RewardHackingFinding, ...]:
    expected_families = tuple(RewardHackingFamily)
    actual_families = tuple(item.family for item in findings)
    finding_ids = tuple(item.finding_id for item in findings)
    if actual_families != expected_families or len(finding_ids) != len(set(finding_ids)):
        raise ValueError(
            "reward validity requires exactly one diagnostic for every reward-hacking family"
        )
    return findings


def _require_resolved_diagnostic_coverage(
    coverage: RewardHackingCoverageAttestation,
    findings: tuple[RewardHackingFinding, ...],
    trace: HarnessExecutionTrace,
    observation: RewardObservation,
) -> None:
    expected_trace = EvidenceReceipt(
        record_id=trace.trace_id,
        schema_version=trace.schema_version,
        content_hash=trace.content_hash,
    )
    expected_observation = EvidenceReceipt(
        record_id=observation.observation_id,
        schema_version=observation.schema_version,
        content_hash=observation.content_hash,
    )
    if coverage.trace != expected_trace or coverage.observation != expected_observation:
        raise ValueError("diagnostic coverage must bind the exact trace and observation")
    for finding, resolved in zip(findings, coverage.diagnostics, strict=True):
        if (
            finding.family is not resolved.family
            or finding.status is not resolved.status
            or finding.evidence_ids
            != tuple(item.record_id for item in resolved.observable_evidence)
        ):
            raise ValueError("finding status and evidence must match resolved diagnostic coverage")


class _RewardValidityAssessmentPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    assessment_id: BoundedAssessmentIdentifier
    observation: RewardObservation
    trace: HarnessExecutionTrace
    trace_id: BoundedTraceIdentifier
    trace_hash: Sha256Hex
    findings: tuple[RewardHackingFinding, ...] = Field(
        min_length=len(RewardHackingFamily),
        max_length=len(RewardHackingFamily),
    )
    finding_ids: tuple[BoundedTraceIdentifier, ...] = Field(
        min_length=len(RewardHackingFamily),
        max_length=len(RewardHackingFamily),
    )
    evidence_receipts: tuple[EvidenceReceipt, ...] = Field(max_length=MAX_REWARD_ACCEPTED_EVIDENCE)
    expectation: TraceExpectation
    verification: VerificationOutcomeEvidence
    diagnostic_coverage: RewardHackingCoverageAttestation
    evidence_inventory: ResolvedEvidenceInventory
    evidence_inventory_hash: Sha256Hex
    freshness: TraceFreshness
    assessor_id: BoundedTraceIdentifier
    assessor_version: BoundedTraceIdentifier
    status: RewardValidityStatus
    reasons: tuple[RewardInvalidationReason, ...] = Field(max_length=len(RewardInvalidationReason))
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
        _require_complete_diagnostic_coverage(self.findings)
        if self.finding_ids != tuple(item.finding_id for item in self.findings):
            raise ValueError("finding_ids must exactly identify the embedded findings")
        if self.evidence_inventory_hash != self.evidence_inventory.content_hash:
            raise ValueError("assessment must bind the exact resolved evidence inventory")
        expected_evidence = _accepted_reward_evidence_receipts(
            self.observation,
            self.findings,
            self.verification,
            self.diagnostic_coverage,
            self.evidence_inventory,
        )
        if self.evidence_receipts != expected_evidence:
            raise ValueError("assessment must bind exact accepted evidence receipts")
        for finding in self.findings:
            if (
                finding.trace_id != self.trace_id
                or finding.trace_hash != self.trace_hash
                or finding.observation_id != self.observation.observation_id
                or finding.observation_hash != self.observation.content_hash
            ):
                raise ValueError("reward-hacking finding must bind exact trace and reward identity")
        _require_resolved_diagnostic_coverage(
            self.diagnostic_coverage,
            self.findings,
            self.trace,
            self.observation,
        )
        expected_freshness = trace_freshness(
            self.expectation,
            self.trace,
            inventory=self.evidence_inventory,
        )
        if (
            self.freshness != expected_freshness
            or self.freshness_hash != expected_freshness.content_hash
        ):
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
            self.expectation,
            self.verification,
            self.evidence_inventory,
        )
        expected_status = reward_status(expected_reasons)
        if self.reasons != expected_reasons or self.status is not expected_status:
            raise ValueError("assessment status and reasons must equal recomputed reward validity")
        if self.assessment_id != reward_assessment_id(
            self.observation,
            self.trace,
            self.findings,
            self.expectation,
            self.verification,
            self.diagnostic_coverage,
            self.evidence_inventory,
        ):
            raise ValueError("assessment_id must address the exact validity inputs")
        return self


class RewardValidityAssessment(_RewardValidityAssessmentPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _RewardValidityAssessmentPayload(**values)
        return cls.model_construct(
            **payload.__dict__,
            content_hash=reward_assessment_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != reward_assessment_hash(self):
            raise ValueError("content_hash must canonically address reward validity")
        return self


def reward_assessment_hash(record: BaseModel | Mapping[str, object]) -> str:
    return _canonical_record_hash(record)


def reward_validity_receipt(assessment: RewardValidityAssessment) -> EvidenceReceipt:
    validated = RewardValidityAssessment.model_validate(assessment)
    return EvidenceReceipt(
        record_id=validated.assessment_id,
        schema_version=validated.schema_version,
        content_hash=validated.content_hash,
    )


def reward_assessment_id(
    observation: RewardObservation,
    trace: HarnessExecutionTrace,
    findings: tuple[RewardHackingFinding, ...],
    expectation: TraceExpectation,
    verification: VerificationOutcomeEvidence,
    diagnostic_coverage: RewardHackingCoverageAttestation,
    inventory: ResolvedEvidenceInventory,
) -> str:
    payload_hash = sha256_hex(
        _canonical_record_hash(
            {
                "observation_hash": observation.content_hash,
                "trace_hash": trace.content_hash,
                "finding_hashes": [item.content_hash for item in findings],
                "expectation_hash": expectation.content_hash,
                "verification_hash": verification.content_hash,
                "diagnostic_coverage_hash": diagnostic_coverage.content_hash,
                "evidence_inventory_hash": inventory.content_hash,
            }
        ).encode("ascii")
    )
    return f"reward-assessment-{payload_hash}"


def ordered_invalidation_reasons(
    observation: RewardObservation,
    trace: HarnessExecutionTrace,
    findings: tuple[RewardHackingFinding, ...],
    expectation: TraceExpectation,
    verification: VerificationOutcomeEvidence,
    inventory: ResolvedEvidenceInventory,
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
    expected_verifier = EvidenceReceipt(
        record_id=trace.observed_binding.validator_id,
        schema_version=1,
        content_hash=trace.observed_binding.validator_hash,
    )
    expected_checker = EvidenceReceipt(
        record_id=trace.observed_binding.checker_id,
        schema_version=1,
        content_hash=trace.observed_binding.checker_hash,
    )
    expected_verifier_result = EvidenceReceipt(
        record_id=trace.verifier_result_id,
        schema_version=1,
        content_hash=trace.verifier_result_hash,
    )
    expected_checker_result = EvidenceReceipt(
        record_id=trace.checker_result_id,
        schema_version=1,
        content_hash=trace.checker_result_hash,
    )
    if (
        observation.verifier_id != trace.observed_binding.validator_id
        or observation.verifier_version != trace.observed_binding.validator_version
        or observation.checker_id != trace.observed_binding.checker_id
        or observation.checker_version != trace.observed_binding.checker_version
        or verification.verifier != expected_verifier
        or verification.checker != expected_checker
        or verification.verifier_result.result != expected_verifier_result
        or verification.checker_result.result != expected_checker_result
    ):
        reasons.add(RewardInvalidationReason.VERIFIER_MISMATCH)
    if (
        verification.verifier_status is VerificationOutcomeStatus.FAILED
        or verification.checker_status is VerificationOutcomeStatus.FAILED
    ):
        reasons.add(RewardInvalidationReason.VERIFIER_FAILURE)
    if trace.artifact_integrity.value is False:
        reasons.add(RewardInvalidationReason.CORRUPTED_ARTIFACT)
    if trace.protected_boundary_crossed.value is True:
        reasons.add(RewardInvalidationReason.PROTECTED_ANSWER_LEAKAGE)
    if any(item.status is RewardHackingFindingStatus.INVALIDATING for item in findings):
        reasons.add(RewardInvalidationReason.REWARD_HACKING_FINDING)
    if trace.evaluator_succeeded.value is False:
        reasons.add(RewardInvalidationReason.EVALUATOR_FAILURE)
    freshness = trace_freshness(expectation, trace, inventory=inventory)
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
        or verification.verifier_status is VerificationOutcomeStatus.UNKNOWN
        or verification.checker_status is VerificationOutcomeStatus.UNKNOWN
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
    expectation: TraceExpectation,
    verification: VerificationOutcomeEvidence,
    diagnostic_coverage: RewardHackingCoverageAttestation,
    inventory: ResolvedEvidenceInventory,
) -> RewardValidityAssessment:
    """Assess observable reward evidence under a handler-supplied inventory capability.

    Repository resolution remains outside this pure domain function. Constructing or
    reminting verifier and diagnostic records cannot produce a valid assessment unless
    their exact receipts already occur in the separately supplied committed inventory.
    """
    validated_observation = RewardObservation.model_validate(observation)
    validated_trace = HarnessExecutionTrace.model_validate(trace)
    validated_expectation = TraceExpectation.model_validate(expectation)
    validated_verification = VerificationOutcomeEvidence.model_validate(verification)
    validated_coverage = RewardHackingCoverageAttestation.model_validate(diagnostic_coverage)
    validated_inventory = ResolvedEvidenceInventory.model_validate(inventory)
    if validated_trace.reward_observation != validated_observation:
        raise ValueError("reward observation must be the exact observation embedded in the trace")
    validated_findings = tuple(RewardHackingFinding.model_validate(item) for item in findings)
    _require_complete_diagnostic_coverage(validated_findings)
    _require_resolved_diagnostic_coverage(
        validated_coverage,
        validated_findings,
        validated_trace,
        validated_observation,
    )
    evidence_receipts = _accepted_reward_evidence_receipts(
        validated_observation,
        validated_findings,
        validated_verification,
        validated_coverage,
        validated_inventory,
    )
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
        validated_expectation,
        validated_verification,
        validated_inventory,
    )
    freshness = trace_freshness(
        validated_expectation,
        validated_trace,
        inventory=validated_inventory,
    )
    return RewardValidityAssessment.build(
        assessment_id=reward_assessment_id(
            validated_observation,
            validated_trace,
            validated_findings,
            validated_expectation,
            validated_verification,
            validated_coverage,
            validated_inventory,
        ),
        observation=validated_observation,
        trace=validated_trace,
        trace_id=validated_trace.trace_id,
        trace_hash=validated_trace.content_hash,
        findings=validated_findings,
        finding_ids=tuple(item.finding_id for item in validated_findings),
        evidence_receipts=evidence_receipts,
        expectation=validated_expectation,
        verification=validated_verification,
        diagnostic_coverage=validated_coverage,
        evidence_inventory=validated_inventory,
        evidence_inventory_hash=validated_inventory.content_hash,
        freshness=freshness,
        assessor_id=validated_observation.evaluator_id,
        assessor_version=validated_observation.evaluator_version,
        status=reward_status(reasons),
        reasons=reasons,
        freshness_hash=freshness.content_hash,
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
    "ResolvedRewardHackingDiagnostic",
    "ResolvedVerificationResultSnapshot",
    "RewardHackingCoverageAttestation",
    "RewardHackingFamily",
    "RewardHackingFinding",
    "RewardHackingFindingStatus",
    "RewardInvalidationReason",
    "RewardValidityAssessment",
    "RewardValidityStatus",
    "VerificationOutcomeEvidence",
    "VerificationOutcomeStatus",
    "assess_reward_validity",
    "ordered_invalidation_reasons",
    "resolved_reward_hacking_diagnostic_hash",
    "resolved_verification_result_hash",
    "reward_assessment_hash",
    "reward_hacking_coverage_hash",
    "reward_hacking_diagnostic_status_snapshot_hash",
    "reward_hacking_finding_hash",
    "reward_validity_receipt",
    "valid_reward_evidence",
    "verification_outcome_hash",
    "verification_result_status_snapshot_hash",
]
