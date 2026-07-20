from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import AssessmentOutcome, AssessmentProvenance
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ImportedPatternStatus(StrEnum):
    SOURCE_PATTERN_REVIEW_PENDING = "SOURCE_PATTERN_REVIEW_PENDING"
    GENERIC_PATTERN_EXTRACTED = "GENERIC_PATTERN_EXTRACTED"
    TRANSFER_TESTING = "TRANSFER_TESTING"
    TRANSFER_VALIDATED = "TRANSFER_VALIDATED"
    DOMAIN_SPECIFIC = "DOMAIN_SPECIFIC"
    BENCHMARK_SPECIFIC = "BENCHMARK_SPECIFIC"
    REJECTED = "REJECTED"
    ADMITTED_TO_SSOH = "ADMITTED_TO_SSOH"


class ModelType(StrEnum):
    SOURCE_CONTROLLED_METADATA = "SOURCE_CONTROLLED_METADATA"
    SYMBOLIC_EQUATION = "SYMBOLIC_EQUATION"
    PROBABILISTIC_MODEL = "PROBABILISTIC_MODEL"
    CAUSAL_GRAPH = "CAUSAL_GRAPH"
    STATE_TRANSITION_SYSTEM = "STATE_TRANSITION_SYSTEM"
    DETERMINISTIC_SIMULATOR = "DETERMINISTIC_SIMULATOR"
    FORMAL_SPECIFICATION = "FORMAL_SPECIFICATION"
    APPROVED_DOMAIN_MODEL = "APPROVED_DOMAIN_MODEL"


class ExecutionMode(StrEnum):
    METADATA_ONLY = "METADATA_ONLY"
    BUILTIN_DETERMINISTIC_SIMULATOR = "BUILTIN_DETERMINISTIC_SIMULATOR"


class VerificationOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ABSTAIN = "ABSTAIN"


class AdmissionOutcome(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"


type StrictInteger = Annotated[int, Field(strict=True)]
type StrictFiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
type NumericValue = StrictInteger | StrictFiniteFloat


class NumericField(_StrictFrozenModel):
    name: StableIdentifier
    value: NumericValue


class ModelInput(_StrictFrozenModel):
    model_input_id: StableIdentifier
    schema_id: StableIdentifier
    values: tuple[NumericField, ...] = Field(min_length=1)
    deterministic_seed: int = Field(strict=True)

    @field_validator("values")
    @classmethod
    def require_unique_names(cls, values: tuple[NumericField, ...]) -> tuple[NumericField, ...]:
        names = tuple(item.name for item in values)
        if len(names) != len(set(names)):
            raise ValueError("numeric field names must be unique")
        return values

    def numeric_value(self, name: str) -> NumericValue:
        for item in self.values:
            if item.name == name:
                return item.value
        raise KeyError(name)


class ModelOutput(_StrictFrozenModel):
    model_output_id: StableIdentifier
    schema_id: StableIdentifier
    values: tuple[NumericField, ...] = Field(min_length=1)
    steps: int = Field(strict=True, ge=0, le=100_000)
    state_bytes: int = Field(strict=True, ge=0, le=10_000_000)

    @field_validator("values")
    @classmethod
    def require_unique_names(cls, values: tuple[NumericField, ...]) -> tuple[NumericField, ...]:
        names = tuple(item.name for item in values)
        if len(names) != len(set(names)):
            raise ValueError("numeric field names must be unique")
        return values

    def numeric_value(self, name: str) -> NumericValue:
        for item in self.values:
            if item.name == name:
                return item.value
        raise KeyError(name)


class HypothesisSpec(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    hypothesis_version_id: StableIdentifier
    hypothesis_id: StableIdentifier
    version: int = Field(strict=True, ge=1)
    statement: NonBlankText
    assumptions: tuple[NonBlankText, ...] = Field(min_length=1)
    scope: tuple[NonBlankText, ...] = Field(min_length=1)
    variables: tuple[NonBlankText, ...] = Field(min_length=1)
    predictions: tuple[NonBlankText, ...] = Field(min_length=1)
    falsification_conditions: tuple[NonBlankText, ...] = Field(min_length=1)
    primitive_version_ids: tuple[StableIdentifier, ...]
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    imported_pattern_status: ImportedPatternStatus
    proposer: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("primitive_version_ids", "evidence_ids")
    @classmethod
    def require_unique_ids(
        cls,
        values: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            name = str(info.field_name)  # type: ignore[attr-defined]
            raise ValueError(f"{name} must be unique")
        return values


class ExecutableModelSpec(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    model_spec_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    model_type: ModelType
    execution_mode: ExecutionMode
    artifact_hash: Sha256Hex | None
    artifact_media_type: NonBlankText | None
    artifact_size_bytes: int | None = Field(default=None, strict=True, ge=0)
    artifact_name: NonBlankText
    builtin_simulator_id: StableIdentifier | None
    input_schema_id: StableIdentifier
    output_schema_id: StableIdentifier
    deterministic_seed: int = Field(strict=True)
    max_steps: int = Field(strict=True, ge=1, le=100_000)
    max_state_bytes: int = Field(strict=True, ge=1, le=10_000_000)
    registered_by: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_inert_or_closed_builtin_shape(self) -> Self:
        artifact_values = (self.artifact_hash, self.artifact_media_type, self.artifact_size_bytes)
        if self.execution_mode is ExecutionMode.METADATA_ONLY:
            if any(item is None for item in artifact_values):
                raise ValueError("metadata-only model requires complete artifact metadata")
            if self.builtin_simulator_id is not None:
                raise ValueError("metadata-only model cannot name a builtin simulator")
        else:
            if any(item is not None for item in artifact_values):
                raise ValueError("builtin simulator cannot carry artifact metadata")
            if self.builtin_simulator_id is None:
                raise ValueError("builtin execution requires a simulator identifier")
        return self


class SimulationResult(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    simulation_result_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    model_spec_id: StableIdentifier
    execution_mode: Literal[ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR] = (
        ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR
    )
    model_input: ModelInput
    model_output: ModelOutput
    deterministic_seed: int = Field(strict=True)
    completed_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def bind_seed_and_schemas(self) -> Self:
        if self.deterministic_seed != self.model_input.deterministic_seed:
            raise ValueError("simulation seed must exactly bind the input seed")
        return self


class _VerificationMechanismBase(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    mechanism_spec_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    name: NonBlankText
    description: NonBlankText
    specification_hash: Sha256Hex
    input_schema_id: StableIdentifier
    output_schema_id: StableIdentifier
    created_by: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class FormalVerifierSpec(_VerificationMechanismBase):
    mechanism_type: Literal["FORMAL_VERIFIER"] = "FORMAL_VERIFIER"
    formal_system_id: StableIdentifier


class DeterministicCheckerSpec(_VerificationMechanismBase):
    mechanism_type: Literal["DETERMINISTIC_CHECKER"] = "DETERMINISTIC_CHECKER"
    checked_invariants: tuple[NonBlankText, ...] = Field(min_length=1)


class LearnedJudgeSpec(_VerificationMechanismBase):
    mechanism_type: Literal["LEARNED_JUDGE"] = "LEARNED_JUDGE"
    rubric_id: StableIdentifier


type VerificationMechanismSpec = Annotated[
    FormalVerifierSpec | DeterministicCheckerSpec | LearnedJudgeSpec,
    Field(discriminator="mechanism_type"),
]
VERIFICATION_MECHANISM_ADAPTER: TypeAdapter[VerificationMechanismSpec] = TypeAdapter(
    VerificationMechanismSpec
)


class _VerificationResultBase(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    verification_result_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    mechanism_spec_id: StableIdentifier
    model_spec_id: StableIdentifier | None
    simulation_result_ids: tuple[StableIdentifier, ...]
    outcome: VerificationOutcome
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    provenance: AssessmentProvenance
    counterexample_search_performed: bool
    counterexample_found: bool

    @field_validator("simulation_result_ids")
    @classmethod
    def require_unique_simulations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("simulation_result_ids must be unique")
        return values

    @model_validator(mode="after")
    def require_search_and_outcome_consistency(self) -> Self:
        if self.counterexample_found and not self.counterexample_search_performed:
            raise ValueError("a found counterexample requires retained search evidence")
        if self.counterexample_found and self.outcome is not VerificationOutcome.FAIL:
            raise ValueError("a found counterexample must fail the verification result")
        expected = {
            VerificationOutcome.PASS: AssessmentOutcome.PASSED,
            VerificationOutcome.FAIL: AssessmentOutcome.FAILED,
            VerificationOutcome.ABSTAIN: AssessmentOutcome.ABSTAINED,
        }[self.outcome]
        if self.provenance.result is not expected:
            raise ValueError("verification outcome must match assessment provenance")
        return self


class FormalVerificationResult(_VerificationResultBase):
    mechanism_type: Literal["FORMAL_VERIFIER"] = "FORMAL_VERIFIER"
    proof_artifact_hash: Sha256Hex

    @model_validator(mode="after")
    def require_formal_provenance(self) -> Self:
        if (
            self.provenance.category is not VerificationLevel.FORMAL_VERIFIER
            or self.provenance.deterministic_or_learned != "DETERMINISTIC"
        ):
            raise ValueError("formal results require formal deterministic provenance")
        return self


class DeterministicCheckResult(_VerificationResultBase):
    mechanism_type: Literal["DETERMINISTIC_CHECKER"] = "DETERMINISTIC_CHECKER"
    checked_invariants: tuple[NonBlankText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_deterministic_provenance(self) -> Self:
        if (
            self.provenance.category is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
            or self.provenance.deterministic_or_learned != "DETERMINISTIC"
        ):
            raise ValueError("deterministic checks require independent deterministic provenance")
        return self


class LearnedJudgeResult(_VerificationResultBase):
    mechanism_type: Literal["LEARNED_JUDGE"] = "LEARNED_JUDGE"
    rubric_id: StableIdentifier

    @model_validator(mode="after")
    def require_learned_provenance(self) -> Self:
        if (
            self.provenance.category
            not in {
                VerificationLevel.INDEPENDENT_LEARNED_JUDGE,
                VerificationLevel.RUBRIC_JUDGE,
            }
            or self.provenance.deterministic_or_learned != "LEARNED"
        ):
            raise ValueError("learned results require learned-judge provenance")
        return self


type VerificationResult = Annotated[
    FormalVerificationResult | DeterministicCheckResult | LearnedJudgeResult,
    Field(discriminator="mechanism_type"),
]
VERIFICATION_RESULT_ADAPTER: TypeAdapter[VerificationResult] = TypeAdapter(VerificationResult)


class CounterexampleRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    counterexample_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    model_spec_id: StableIdentifier | None
    simulation_result_ids: tuple[StableIdentifier, ...]
    verification_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    description: NonBlankText
    input_hash: Sha256Hex
    observed_output_hash: Sha256Hex
    expected_output_hash: Sha256Hex
    discovered_by: ActorIdentity
    discovered_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("simulation_result_ids", "verification_result_ids", "evidence_ids")
    @classmethod
    def require_unique_ids(
        cls,
        values: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            name = str(info.field_name)  # type: ignore[attr-defined]
            raise ValueError(f"{name} must be unique")
        return values


class RevisionRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    revision_id: StableIdentifier
    hypothesis_id: StableIdentifier
    prior_hypothesis_version_id: StableIdentifier
    prior_version: int = Field(strict=True, ge=1)
    resulting_hypothesis_version_id: StableIdentifier
    resulting_version: int = Field(strict=True, ge=2)
    triggering_verification_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    considered_counterexample_ids: tuple[StableIdentifier, ...]
    assumptions_added: tuple[NonBlankText, ...]
    assumptions_removed: tuple[NonBlankText, ...]
    assumptions_changed: tuple[NonBlankText, ...]
    variables_added: tuple[NonBlankText, ...]
    variables_removed: tuple[NonBlankText, ...]
    variables_changed: tuple[NonBlankText, ...]
    mechanism_changes: tuple[NonBlankText, ...]
    preserved_elements: tuple[NonBlankText, ...] = Field(min_length=1)
    changed_predictions: tuple[NonBlankText, ...] = Field(min_length=1)
    changed_falsification_conditions: tuple[NonBlankText, ...] = Field(min_length=1)
    author: ActorIdentity
    revised_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_contiguous_distinct_versions(self) -> Self:
        if self.resulting_version != self.prior_version + 1:
            raise ValueError("resulting version must immediately follow prior version")
        if self.resulting_hypothesis_version_id == self.prior_hypothesis_version_id:
            raise ValueError("resulting hypothesis version must be distinct")
        return self


class HypothesisAdmissionDecision(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    admission_decision_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    hypothesis_id: StableIdentifier
    version: int = Field(strict=True, ge=1)
    imported_pattern_status: ImportedPatternStatus
    model_spec_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    verification_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    counterexample_search_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    counterexample_ids: tuple[StableIdentifier, ...]
    revision_ids: tuple[StableIdentifier, ...]
    evaluator_audit_id: StableIdentifier
    measurement_id: StableIdentifier
    rollback_hypothesis_version_id: StableIdentifier | None
    outcome: AdmissionOutcome
    rationale: NonBlankText
    decided_by: ActorIdentity
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_search_results_are_admission_results(self) -> Self:
        if not set(self.counterexample_search_result_ids).issubset(self.verification_result_ids):
            raise ValueError("counterexample search results must be retained verification results")
        return self


class AcceptedHypothesisReceiptRef(_StrictFrozenModel):
    proposal_id: StableIdentifier
    proposal_hash: Sha256Hex
    audit_event_id: StableIdentifier
    audit_event_hash: Sha256Hex


class HypothesisVersionReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["HYPOTHESIS_VERSION"] = "HYPOTHESIS_VERSION"


class HypothesisRevisionReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["HYPOTHESIS_REVISION"] = "HYPOTHESIS_REVISION"


type HypothesisCandidateReceiptRef = Annotated[
    HypothesisVersionReceiptRef | HypothesisRevisionReceiptRef,
    Field(discriminator="receipt_type"),
]


class ModelSpecReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["MODEL_SPEC"] = "MODEL_SPEC"


class VerificationMechanismReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["VERIFICATION_MECHANISM"] = "VERIFICATION_MECHANISM"


class SimulationResultReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["SIMULATION_RESULT"] = "SIMULATION_RESULT"


class VerificationResultReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["VERIFICATION_RESULT"] = "VERIFICATION_RESULT"


class CounterexampleReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["COUNTEREXAMPLE"] = "COUNTEREXAMPLE"


class EvaluatorAuditReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["EVALUATOR_AUDIT"] = "EVALUATOR_AUDIT"


class SelfImprovementMeasurementReceiptRef(AcceptedHypothesisReceiptRef):
    receipt_type: Literal["SELF_IMPROVEMENT_MEASUREMENT"] = "SELF_IMPROVEMENT_MEASUREMENT"


type HypothesisReceiptRef = Annotated[
    HypothesisVersionReceiptRef
    | HypothesisRevisionReceiptRef
    | ModelSpecReceiptRef
    | VerificationMechanismReceiptRef
    | SimulationResultReceiptRef
    | VerificationResultReceiptRef
    | CounterexampleReceiptRef
    | EvaluatorAuditReceiptRef
    | SelfImprovementMeasurementReceiptRef,
    Field(discriminator="receipt_type"),
]


__all__ = [
    "VERIFICATION_MECHANISM_ADAPTER",
    "VERIFICATION_RESULT_ADAPTER",
    "AcceptedHypothesisReceiptRef",
    "AdmissionOutcome",
    "CounterexampleReceiptRef",
    "CounterexampleRecord",
    "DeterministicCheckResult",
    "DeterministicCheckerSpec",
    "EvaluatorAuditReceiptRef",
    "ExecutableModelSpec",
    "ExecutionMode",
    "FormalVerificationResult",
    "FormalVerifierSpec",
    "HypothesisAdmissionDecision",
    "HypothesisCandidateReceiptRef",
    "HypothesisReceiptRef",
    "HypothesisRevisionReceiptRef",
    "HypothesisSpec",
    "HypothesisVersionReceiptRef",
    "ImportedPatternStatus",
    "LearnedJudgeResult",
    "LearnedJudgeSpec",
    "ModelInput",
    "ModelOutput",
    "ModelSpecReceiptRef",
    "ModelType",
    "NumericField",
    "RevisionRecord",
    "SelfImprovementMeasurementReceiptRef",
    "SimulationResult",
    "SimulationResultReceiptRef",
    "VerificationMechanismReceiptRef",
    "VerificationMechanismSpec",
    "VerificationOutcome",
    "VerificationResult",
    "VerificationResultReceiptRef",
]
