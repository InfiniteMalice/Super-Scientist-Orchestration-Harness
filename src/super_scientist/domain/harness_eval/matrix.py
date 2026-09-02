from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from itertools import combinations, product
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from super_scientist.domain.harness_eval.bounds import (
    bounded_canonical_record_hash,
    require_canonical_byte_limit,
)
from super_scientist.domain.harness_eval.budget_bounds import PhaseAEvaluationBudget
from super_scientist.domain.harness_eval.guidance import (
    MAX_EVALUATION_ITEMS,
    MAX_EVALUATION_RANDOM_SEED,
    MAX_EVALUATION_SCHEMA_VERSION,
    BoundedIdentifier,
    EvaluationMetricDeltaVector,
    EvaluationMetricVector,
    _StrictFrozenModel,
    metric_component_deltas,
)
from super_scientist.domain.harness_eval.models import (
    BUDGET_COMPARISON_FIELDS,
    EvaluationBudget,
    HarnessPartition,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.primitives import Sha256Hex, UtcTimestamp

if TYPE_CHECKING:
    from super_scientist.domain.harness_eval.evidence_chains import (
        HarnessCellEvidenceChain,
        HarnessEvidenceSnapshotIndex,
    )

# The 256-cell grid reaches this maximum at 128 models x 2 harnesses x 1 partition
# when all non-transfer comparison families are declared.
MAX_MODEL_HARNESS_GRID_CELLS = MAX_EVALUATION_ITEMS
MAX_MODEL_HARNESS_COMPARISONS = 24_512
MAX_MODEL_BUDGET_BINDING_CANONICAL_BYTES = 65_536
MAX_MODEL_HARNESS_PROTOCOL_CANONICAL_BYTES = 1_048_576
MAX_MODEL_HARNESS_CELL_CANONICAL_BYTES = 393_216
MAX_MODEL_HARNESS_COMPARISON_CANONICAL_BYTES = 32_768
MAX_MODEL_HARNESS_ANALYSIS_CANONICAL_BYTES = 67_108_864


class ModelHarnessComparisonKind(StrEnum):
    MODEL_HELD_CONSTANT = "MODEL_HELD_CONSTANT"
    HARNESS_HELD_CONSTANT = "HARNESS_HELD_CONSTANT"
    INTERACTION_DESCRIPTIVE = "INTERACTION_DESCRIPTIVE"
    TRAIN_TEST_TRANSFER = "TRAIN_TEST_TRANSFER"


class ModelHarnessConfoundCode(StrEnum):
    INCOMPLETE_GRID = "INCOMPLETE_GRID"
    DUPLICATE_CELL = "DUPLICATE_CELL"
    UNEXPECTED_CELL = "UNEXPECTED_CELL"
    PROTOCOL_ID_MISMATCH = "PROTOCOL_ID_MISMATCH"
    PROTOCOL_VERSION_MISMATCH = "PROTOCOL_VERSION_MISMATCH"
    PROTOCOL_HASH_MISMATCH = "PROTOCOL_HASH_MISMATCH"
    MODEL_IDENTITY_MISMATCH = "MODEL_IDENTITY_MISMATCH"
    HARNESS_IDENTITY_MISMATCH = "HARNESS_IDENTITY_MISMATCH"
    TASK_SET_MISMATCH = "TASK_SET_MISMATCH"
    PARTITION_MISMATCH = "PARTITION_MISMATCH"
    VERIFIER_MISMATCH = "VERIFIER_MISMATCH"
    CHECKER_MISMATCH = "CHECKER_MISMATCH"
    BUDGET_MISMATCH = "BUDGET_MISMATCH"
    ARTIFACTS_MISMATCH = "ARTIFACTS_MISMATCH"
    SEED_MISMATCH = "SEED_MISMATCH"
    OUTPUT_SCHEMA_MISMATCH = "OUTPUT_SCHEMA_MISMATCH"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    TRACE_RECEIPT_MISMATCH = "TRACE_RECEIPT_MISMATCH"
    STALE_TRACE = "STALE_TRACE"
    REWARD_RECEIPT_MISMATCH = "REWARD_RECEIPT_MISMATCH"
    INVALID_REWARD = "INVALID_REWARD"


_PARTITION_ORDER = {item: index for index, item in enumerate(HarnessPartition)}
_COMPARISON_ORDER = {item: index for index, item in enumerate(ModelHarnessComparisonKind)}
_CONFOUND_ORDER = {item: index for index, item in enumerate(ModelHarnessConfoundCode)}


class ModelIdentity(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    model_id: BoundedIdentifier
    model_version: BoundedIdentifier


class HarnessIdentity(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    harness_id: BoundedIdentifier
    harness_version: BoundedIdentifier


class ModelHarnessCoordinate(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    model: ModelIdentity
    harness: HarnessIdentity
    partition: HarnessPartition


RESOURCE_ENVELOPE_FIELDS = BUDGET_COMPARISON_FIELDS[3:]


def evaluation_resource_envelope_hash(budget: EvaluationBudget) -> str:
    validated = PhaseAEvaluationBudget.from_evaluation_budget(budget)
    serialized = validated.model_dump(mode="json")
    return bounded_canonical_record_hash(
        {field_name: serialized[field_name] for field_name in RESOURCE_ENVELOPE_FIELDS},
        maximum=MAX_MODEL_BUDGET_BINDING_CANONICAL_BYTES,
        error="evaluation resource envelope canonical bytes exceed bound",
    )


class _ModelBudgetBindingPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    model: ModelIdentity
    budget: PhaseAEvaluationBudget
    resource_envelope_hash: Sha256Hex

    @field_validator("budget", mode="before")
    @classmethod
    def revalidate_budget(
        cls,
        value: EvaluationBudget | PhaseAEvaluationBudget | Mapping[str, object],
    ) -> PhaseAEvaluationBudget | Mapping[str, object]:
        if isinstance(value, (EvaluationBudget, PhaseAEvaluationBudget)):
            return PhaseAEvaluationBudget.from_evaluation_budget(value)
        return value

    @model_validator(mode="after")
    def require_exact_model_and_envelope(self) -> Self:
        if (
            self.budget.model_id != self.model.model_id
            or self.budget.model_version != self.model.model_version
        ):
            raise ValueError("budget must bind its exact matrix model")
        if self.resource_envelope_hash != evaluation_resource_envelope_hash(self.budget):
            raise ValueError("resource_envelope_hash must address the model-agnostic limits")
        require_canonical_byte_limit(
            self,
            maximum=MAX_MODEL_BUDGET_BINDING_CANONICAL_BYTES,
            error="model budget binding canonical bytes exceed bound",
        )
        return self


class ModelBudgetBinding(_ModelBudgetBindingPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        supplied = dict(values)
        raw_budget = supplied["budget"]
        budget = (
            PhaseAEvaluationBudget.model_validate(raw_budget, strict=True)
            if isinstance(raw_budget, Mapping)
            else PhaseAEvaluationBudget.from_evaluation_budget(raw_budget)
        )
        supplied["budget"] = budget
        supplied.setdefault(
            "resource_envelope_hash",
            evaluation_resource_envelope_hash(budget),
        )
        payload = _ModelBudgetBindingPayload(**supplied)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=model_budget_binding_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != model_budget_binding_hash(self):
            raise ValueError("content_hash must canonically address the model budget binding")
        return self


def _coordinate_key(
    coordinate: ModelHarnessCoordinate,
) -> tuple[str, str, str, str, int]:
    return (
        coordinate.model.model_id,
        coordinate.model.model_version,
        coordinate.harness.harness_id,
        coordinate.harness.harness_version,
        _PARTITION_ORDER[coordinate.partition],
    )


def _coordinate_lookup_key(
    coordinate: ModelHarnessCoordinate,
) -> tuple[str, str, str, str, HarnessPartition]:
    return (
        coordinate.model.model_id,
        coordinate.model.model_version,
        coordinate.harness.harness_id,
        coordinate.harness.harness_version,
        coordinate.partition,
    )


class _ModelHarnessProtocolPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: BoundedIdentifier
    version: int = Field(strict=True, ge=1, le=MAX_EVALUATION_SCHEMA_VERSION)
    models: tuple[ModelIdentity, ...] = Field(min_length=2, max_length=MAX_EVALUATION_ITEMS)
    harnesses: tuple[HarnessIdentity, ...] = Field(
        min_length=2,
        max_length=MAX_EVALUATION_ITEMS,
    )
    partitions: tuple[HarnessPartition, ...] = Field(
        min_length=1,
        max_length=len(HarnessPartition),
    )
    task_set_id: BoundedIdentifier
    task_set_hash: Sha256Hex
    verifier_id: BoundedIdentifier
    verifier_version: BoundedIdentifier
    checker_id: BoundedIdentifier
    checker_version: BoundedIdentifier
    artifact_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_EVALUATION_ITEMS)
    random_seed: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=MAX_EVALUATION_RANDOM_SEED,
    )
    output_schema_hash: Sha256Hex
    model_budgets: tuple[ModelBudgetBinding, ...] = Field(
        min_length=1,
        max_length=MAX_EVALUATION_ITEMS,
    )
    matched_resource_envelope_hash: Sha256Hex
    expected_grid: tuple[ModelHarnessCoordinate, ...] = Field(
        min_length=4,
        max_length=MAX_EVALUATION_ITEMS,
    )
    comparison_kinds: tuple[ModelHarnessComparisonKind, ...] = Field(
        min_length=1,
        max_length=len(ModelHarnessComparisonKind),
    )
    governing_policy_hash: Sha256Hex

    @model_validator(mode="before")
    @classmethod
    def reject_oversized_declared_grid(cls, values: Any) -> Any:
        if not isinstance(values, Mapping):
            return values
        models = values.get("models")
        harnesses = values.get("harnesses")
        partitions = values.get("partitions")
        if not (
            isinstance(models, (tuple, list))
            and isinstance(harnesses, (tuple, list))
            and isinstance(partitions, (tuple, list))
        ):
            return values
        declared_grid_cells = len(models) * len(harnesses) * len(partitions)
        if declared_grid_cells > MAX_MODEL_HARNESS_GRID_CELLS:
            raise ValueError("model-harness Cartesian grid exceeds 256 cells")
        return values

    @field_validator("models")
    @classmethod
    def require_canonical_models(
        cls,
        values: tuple[ModelIdentity, ...],
    ) -> tuple[ModelIdentity, ...]:
        keys = tuple((item.model_id, item.model_version) for item in values)
        if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
            raise ValueError("model identities must be unique and canonically ordered")
        return values

    @field_validator("harnesses")
    @classmethod
    def require_canonical_harnesses(
        cls,
        values: tuple[HarnessIdentity, ...],
    ) -> tuple[HarnessIdentity, ...]:
        keys = tuple((item.harness_id, item.harness_version) for item in values)
        if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
            raise ValueError("harness identities must be unique and canonically ordered")
        return values

    @field_validator("partitions")
    @classmethod
    def require_canonical_partitions(
        cls,
        values: tuple[HarnessPartition, ...],
    ) -> tuple[HarnessPartition, ...]:
        if len(values) != len(set(values)) or values != tuple(
            sorted(values, key=_PARTITION_ORDER.__getitem__)
        ):
            raise ValueError("partitions must be unique and canonically ordered")
        return values

    @field_validator("artifact_ids")
    @classmethod
    def require_canonical_artifacts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or values != tuple(sorted(values)):
            raise ValueError("artifact_ids must be unique and canonically ordered")
        return values

    @field_validator("comparison_kinds")
    @classmethod
    def require_canonical_comparison_kinds(
        cls,
        values: tuple[ModelHarnessComparisonKind, ...],
    ) -> tuple[ModelHarnessComparisonKind, ...]:
        if len(values) != len(set(values)) or values != tuple(
            sorted(values, key=_COMPARISON_ORDER.__getitem__)
        ):
            raise ValueError("comparison kinds must be unique and canonically ordered")
        return values

    @model_validator(mode="after")
    def require_complete_declared_grid(self) -> Self:
        if tuple(binding.model for binding in self.model_budgets) != self.models:
            raise ValueError("protocol requires exactly one budget for every model")
        if any(
            binding.resource_envelope_hash != self.matched_resource_envelope_hash
            for binding in self.model_budgets
        ):
            raise ValueError("all matrix models must use the same resource envelope")
        expected = tuple(
            ModelHarnessCoordinate(model=model, harness=harness, partition=partition)
            for model, harness, partition in product(
                self.models,
                self.harnesses,
                self.partitions,
            )
        )
        keys = tuple(_coordinate_key(item) for item in self.expected_grid)
        if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
            raise ValueError("expected grid cells must be unique and canonically ordered")
        if self.expected_grid != expected:
            raise ValueError("expected_grid must declare the complete Cartesian grid")
        if ModelHarnessComparisonKind.TRAIN_TEST_TRANSFER in self.comparison_kinds and (
            HarnessPartition.HARNESS_DISCOVERY_TASKS not in self.partitions
            or not any(
                item is not HarnessPartition.HARNESS_DISCOVERY_TASKS for item in self.partitions
            )
        ):
            raise ValueError(
                "train-test transfer requires discovery and a distinct held-out partition"
            )
        require_canonical_byte_limit(
            self,
            maximum=MAX_MODEL_HARNESS_PROTOCOL_CANONICAL_BYTES,
            error="model-harness protocol canonical bytes exceed bound",
        )
        return self


class ModelHarnessProtocol(_ModelHarnessProtocolPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ModelHarnessProtocolPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=model_harness_protocol_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != model_harness_protocol_hash(self):
            raise ValueError("content_hash must canonically address the model-harness protocol")
        return self


class _ModelHarnessCellPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    cell_id: BoundedIdentifier
    protocol_receipt: EvidenceReceipt
    protocol_id: BoundedIdentifier
    protocol_version: int = Field(
        strict=True,
        ge=1,
        le=MAX_EVALUATION_SCHEMA_VERSION,
    )
    protocol_hash: Sha256Hex
    coordinate: ModelHarnessCoordinate
    task_set_id: BoundedIdentifier
    task_set_hash: Sha256Hex
    verifier_id: BoundedIdentifier
    verifier_version: BoundedIdentifier
    checker_id: BoundedIdentifier
    checker_version: BoundedIdentifier
    artifact_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_EVALUATION_ITEMS)
    random_seed: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=MAX_EVALUATION_RANDOM_SEED,
    )
    output_schema_hash: Sha256Hex
    evaluation_budget: PhaseAEvaluationBudget
    governing_policy_hash: Sha256Hex
    metrics: EvaluationMetricVector
    evidence_chain_receipt: EvidenceReceipt
    observed_at: UtcTimestamp

    @field_validator("evaluation_budget", mode="before")
    @classmethod
    def revalidate_evaluation_budget(
        cls,
        value: EvaluationBudget | PhaseAEvaluationBudget | Mapping[str, object],
    ) -> PhaseAEvaluationBudget | Mapping[str, object]:
        if isinstance(value, (EvaluationBudget, PhaseAEvaluationBudget)):
            return PhaseAEvaluationBudget.from_evaluation_budget(value)
        return value

    @field_validator("artifact_ids")
    @classmethod
    def require_canonical_artifacts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or values != tuple(sorted(values)):
            raise ValueError("artifact_ids must be unique and canonically ordered")
        return values

    @model_validator(mode="after")
    def require_exact_protocol_binding(self) -> Self:
        if self.protocol_id != self.protocol_receipt.record_id:
            raise ValueError("model-harness cell must bind the exact protocol identifier")
        if self.protocol_receipt.schema_version != self.schema_version:
            raise ValueError("model-harness cell must bind the exact protocol schema version")
        if self.protocol_version < 1:
            raise ValueError("model-harness cell must bind the exact protocol version")
        if self.protocol_hash != self.protocol_receipt.content_hash:
            raise ValueError("model-harness cell must bind the exact protocol hash")
        require_canonical_byte_limit(
            self,
            maximum=MAX_MODEL_HARNESS_CELL_CANONICAL_BYTES,
            error="model-harness cell canonical bytes exceed bound",
        )
        return self


class ModelHarnessCell(_ModelHarnessCellPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ModelHarnessCellPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=model_harness_cell_hash(payload),
        )

    @classmethod
    def from_protocol(
        cls,
        *,
        protocol: ModelHarnessProtocol,
        **values: Any,
    ) -> Self:
        validated = ModelHarnessProtocol.model_validate(protocol)
        coordinate = ModelHarnessCoordinate.model_validate(values["coordinate"])
        budget_by_model = {binding.model: binding.budget for binding in validated.model_budgets}
        return cls.build(
            protocol_receipt=EvidenceReceipt(
                record_id=validated.protocol_id,
                schema_version=validated.schema_version,
                content_hash=validated.content_hash,
            ),
            protocol_id=validated.protocol_id,
            protocol_version=validated.version,
            protocol_hash=validated.content_hash,
            task_set_id=validated.task_set_id,
            task_set_hash=validated.task_set_hash,
            verifier_id=validated.verifier_id,
            verifier_version=validated.verifier_version,
            checker_id=validated.checker_id,
            checker_version=validated.checker_version,
            artifact_ids=validated.artifact_ids,
            random_seed=validated.random_seed,
            output_schema_hash=validated.output_schema_hash,
            evaluation_budget=budget_by_model[coordinate.model],
            governing_policy_hash=validated.governing_policy_hash,
            **values,
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != model_harness_cell_hash(self):
            raise ValueError("content_hash must canonically address the model-harness cell")
        return self


class _ModelHarnessComparisonPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    kind: ModelHarnessComparisonKind
    partitions: tuple[HarnessPartition, ...] = Field(min_length=1, max_length=2)
    cell_ids: tuple[BoundedIdentifier, ...] = Field(min_length=2, max_length=4)
    cell_hashes: tuple[Sha256Hex, ...] = Field(min_length=2, max_length=4)
    component_deltas: tuple[EvaluationMetricDeltaVector, ...] = Field(
        min_length=1,
        max_length=2,
    )

    @model_validator(mode="after")
    def require_kind_shape(self) -> Self:
        if len(self.cell_ids) != len(self.cell_hashes):
            raise ValueError("comparison cell identifiers and hashes must align")
        if len(self.cell_ids) != len(set(self.cell_ids)):
            raise ValueError("comparison cells must be unique")
        expected_shape = {
            ModelHarnessComparisonKind.MODEL_HELD_CONSTANT: (1, 2, 1),
            ModelHarnessComparisonKind.HARNESS_HELD_CONSTANT: (1, 2, 1),
            ModelHarnessComparisonKind.INTERACTION_DESCRIPTIVE: (1, 4, 2),
            ModelHarnessComparisonKind.TRAIN_TEST_TRANSFER: (2, 2, 1),
        }[self.kind]
        if (
            len(self.partitions),
            len(self.cell_ids),
            len(self.component_deltas),
        ) != expected_shape:
            raise ValueError("comparison shape must exactly match its declared kind")
        if self.kind is ModelHarnessComparisonKind.TRAIN_TEST_TRANSFER and (
            self.partitions[0] is not HarnessPartition.HARNESS_DISCOVERY_TASKS
            or self.partitions[1] is HarnessPartition.HARNESS_DISCOVERY_TASKS
        ):
            raise ValueError("transfer comparison must retain discovery and held-out partitions")
        require_canonical_byte_limit(
            self,
            maximum=MAX_MODEL_HARNESS_COMPARISON_CANONICAL_BYTES,
            error="model-harness comparison canonical bytes exceed bound",
        )
        return self


class ModelHarnessComparison(_ModelHarnessComparisonPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ModelHarnessComparisonPayload(**values)
        return cls.model_construct(
            **payload.__dict__,
            content_hash=model_harness_comparison_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != model_harness_comparison_hash(self):
            raise ValueError("content_hash must canonically address the model-harness comparison")
        return self


class _ModelHarnessAnalysisPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    protocol: ModelHarnessProtocol
    protocol_id: BoundedIdentifier
    protocol_version: int = Field(
        strict=True,
        ge=1,
        le=MAX_EVALUATION_SCHEMA_VERSION,
    )
    protocol_hash: Sha256Hex
    cell_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_EVALUATION_ITEMS)
    cell_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_EVALUATION_ITEMS)
    comparisons: tuple[ModelHarnessComparison, ...] = Field(
        max_length=MAX_MODEL_HARNESS_COMPARISONS
    )
    confounds: tuple[ModelHarnessConfoundCode, ...] = Field(
        max_length=len(ModelHarnessConfoundCode)
    )
    causal_claim_permitted: Literal[False] = False

    @model_validator(mode="after")
    def require_canonical_evidence_only_state(self) -> Self:
        if self.protocol_id != self.protocol.protocol_id:
            raise ValueError("analysis must bind the exact protocol identifier")
        if self.protocol_version != self.protocol.version:
            raise ValueError("analysis must bind the exact protocol version")
        if self.protocol_hash != self.protocol.content_hash:
            raise ValueError("analysis must bind the exact protocol hash")
        if len(self.cell_ids) != len(self.cell_hashes):
            raise ValueError("analysis cell identifiers and hashes must align")
        inventory = tuple(zip(self.cell_ids, self.cell_hashes, strict=True))
        if inventory != tuple(sorted(inventory)):
            raise ValueError("analysis cell identifier-hash pairs must be canonically ordered")
        identifiers_are_unique = len(self.cell_ids) == len(set(self.cell_ids))
        duplicate_is_declared = ModelHarnessConfoundCode.DUPLICATE_CELL in self.confounds
        if not identifiers_are_unique and not duplicate_is_declared:
            raise ValueError("repeated analysis cell identifiers require a duplicate-cell confound")
        if self.confounds != tuple(sorted(set(self.confounds), key=_CONFOUND_ORDER.__getitem__)):
            raise ValueError("analysis confounds must be unique and canonical")
        if self.confounds and self.comparisons:
            raise ValueError("confounded analysis cannot emit comparisons")
        inventory_set = set(inventory)
        for comparison in self.comparisons:
            if comparison.kind not in self.protocol.comparison_kinds:
                raise ValueError("analysis comparison kind must be declared by the protocol")
            comparison_inventory = zip(
                comparison.cell_ids,
                comparison.cell_hashes,
                strict=True,
            )
            if any(item not in inventory_set for item in comparison_inventory):
                raise ValueError("comparison cells must belong to the analysis cell inventory")
            if any(item not in self.protocol.partitions for item in comparison.partitions):
                raise ValueError("comparison partitions must belong to the analysis protocol")
        comparison_keys = tuple(_comparison_key(item) for item in self.comparisons)
        if comparison_keys != tuple(sorted(comparison_keys)):
            raise ValueError("analysis comparisons must be canonically ordered")
        require_canonical_byte_limit(
            self,
            maximum=MAX_MODEL_HARNESS_ANALYSIS_CANONICAL_BYTES,
            error="model-harness analysis canonical bytes exceed bound",
        )
        return self


class ModelHarnessAnalysis(_ModelHarnessAnalysisPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _ModelHarnessAnalysisPayload(**values)
        return cls.model_construct(
            **payload.__dict__,
            content_hash=model_harness_analysis_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != model_harness_analysis_hash(self):
            raise ValueError("content_hash must canonically address the model-harness analysis")
        return self


def model_harness_protocol_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return bounded_canonical_record_hash(
        record,
        maximum=MAX_MODEL_HARNESS_PROTOCOL_CANONICAL_BYTES,
        error="model-harness protocol canonical bytes exceed bound",
        exclude_fields=exclude_fields,
    )


def model_budget_binding_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return bounded_canonical_record_hash(
        record,
        maximum=MAX_MODEL_BUDGET_BINDING_CANONICAL_BYTES,
        error="model budget binding canonical bytes exceed bound",
        exclude_fields=exclude_fields,
    )


def model_harness_cell_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return bounded_canonical_record_hash(
        record,
        maximum=MAX_MODEL_HARNESS_CELL_CANONICAL_BYTES,
        error="model-harness cell canonical bytes exceed bound",
        exclude_fields=exclude_fields,
    )


def model_harness_comparison_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return bounded_canonical_record_hash(
        record,
        maximum=MAX_MODEL_HARNESS_COMPARISON_CANONICAL_BYTES,
        error="model-harness comparison canonical bytes exceed bound",
        exclude_fields=exclude_fields,
    )


def model_harness_analysis_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return bounded_canonical_record_hash(
        record,
        maximum=MAX_MODEL_HARNESS_ANALYSIS_CANONICAL_BYTES,
        error="model-harness analysis canonical bytes exceed bound",
        exclude_fields=exclude_fields,
    )


def canonical_cells(cells: tuple[ModelHarnessCell, ...]) -> tuple[ModelHarnessCell, ...]:
    return tuple(sorted(cells, key=lambda item: (item.cell_id, item.content_hash)))


def _require_bounded_raw_cells(cells: object) -> None:
    if isinstance(cells, (tuple, list)) and len(cells) > MAX_MODEL_HARNESS_GRID_CELLS:
        raise ValueError("model-harness cell count exceeds 256 cells")


def _add_protocol_identity_confounds(
    confounds: set[ModelHarnessConfoundCode],
    protocol: ModelHarnessProtocol,
    cell: ModelHarnessCell,
) -> None:
    pairs: tuple[tuple[object, object, ModelHarnessConfoundCode], ...] = (
        (protocol.protocol_id, cell.protocol_id, ModelHarnessConfoundCode.PROTOCOL_ID_MISMATCH),
        (
            protocol.version,
            cell.protocol_version,
            ModelHarnessConfoundCode.PROTOCOL_VERSION_MISMATCH,
        ),
        (protocol.task_set_id, cell.task_set_id, ModelHarnessConfoundCode.TASK_SET_MISMATCH),
        (protocol.task_set_hash, cell.task_set_hash, ModelHarnessConfoundCode.TASK_SET_MISMATCH),
        (protocol.verifier_id, cell.verifier_id, ModelHarnessConfoundCode.VERIFIER_MISMATCH),
        (
            protocol.verifier_version,
            cell.verifier_version,
            ModelHarnessConfoundCode.VERIFIER_MISMATCH,
        ),
        (protocol.checker_id, cell.checker_id, ModelHarnessConfoundCode.CHECKER_MISMATCH),
        (
            protocol.checker_version,
            cell.checker_version,
            ModelHarnessConfoundCode.CHECKER_MISMATCH,
        ),
        (protocol.artifact_ids, cell.artifact_ids, ModelHarnessConfoundCode.ARTIFACTS_MISMATCH),
        (protocol.random_seed, cell.random_seed, ModelHarnessConfoundCode.SEED_MISMATCH),
        (
            protocol.output_schema_hash,
            cell.output_schema_hash,
            ModelHarnessConfoundCode.OUTPUT_SCHEMA_MISMATCH,
        ),
        (
            protocol.governing_policy_hash,
            cell.governing_policy_hash,
            ModelHarnessConfoundCode.POLICY_MISMATCH,
        ),
    )
    confounds.update(code for expected, observed, code in pairs if expected != observed)
    expected_budget = next(
        (
            binding.budget
            for binding in protocol.model_budgets
            if binding.model == cell.coordinate.model
        ),
        None,
    )
    if expected_budget is None or cell.evaluation_budget != expected_budget:
        confounds.add(ModelHarnessConfoundCode.BUDGET_MISMATCH)


def validate_complete_matched_grid(
    protocol: ModelHarnessProtocol,
    cells: tuple[ModelHarnessCell, ...] | list[ModelHarnessCell],
    *,
    evidence_chains: tuple[HarnessCellEvidenceChain, ...],
    evidence_index: HarnessEvidenceSnapshotIndex,
) -> tuple[ModelHarnessConfoundCode, ...]:
    from super_scientist.domain.harness_eval.evidence_chains import (
        HarnessCellEvidenceChain,
        HarnessEvidenceSnapshotIndex,
        harness_cell_evidence_chain_receipt,
    )
    from super_scientist.domain.harness_eval.rewards import (
        RewardValidityStatus,
    )
    from super_scientist.domain.harness_eval.traces import (
        TraceFreshnessStatus,
    )

    _require_bounded_raw_cells(cells)
    validated_protocol = ModelHarnessProtocol.model_validate(protocol)
    expected_cell_count = len(validated_protocol.expected_grid)
    if len(evidence_chains) > expected_cell_count:
        raise ValueError("evidence chain count exceeds expected grid")
    validated_cells = tuple(ModelHarnessCell.model_validate(item) for item in cells)
    validated_chains = tuple(
        HarnessCellEvidenceChain.model_validate(item) for item in evidence_chains
    )
    validated_index = HarnessEvidenceSnapshotIndex.model_validate(evidence_index)
    chain_ids = tuple(item.chain_id for item in validated_chains)
    if len(chain_ids) != len(set(chain_ids)):
        raise ValueError("validated cell evidence chains must have unique identifiers")
    chains_by_id = {item.chain_id: item for item in validated_chains}
    snapshots_by_chain_id = {item.chain_receipt.record_id: item for item in validated_index.records}
    confounds: set[ModelHarnessConfoundCode] = set()
    cell_chain_receipts = set(item.evidence_chain_receipt for item in validated_cells)
    validated_chain_receipts = set(
        harness_cell_evidence_chain_receipt(item) for item in validated_chains
    )
    indexed_chain_receipts = set(item.chain_receipt for item in validated_index.records)
    if not (cell_chain_receipts == validated_chain_receipts == indexed_chain_receipts):
        confounds.add(ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH)
        confounds.add(ModelHarnessConfoundCode.REWARD_RECEIPT_MISMATCH)
    cell_ids = tuple(item.cell_id for item in validated_cells)
    if len(cell_ids) != len(set(cell_ids)):
        confounds.add(ModelHarnessConfoundCode.DUPLICATE_CELL)
    coordinate_keys = tuple(_coordinate_lookup_key(item.coordinate) for item in validated_cells)
    if len(coordinate_keys) != len(set(coordinate_keys)):
        confounds.add(ModelHarnessConfoundCode.DUPLICATE_CELL)
    expected_keys = {_coordinate_lookup_key(item) for item in validated_protocol.expected_grid}
    observed_keys = set(coordinate_keys)
    if expected_keys - observed_keys:
        confounds.add(ModelHarnessConfoundCode.INCOMPLETE_GRID)
    if observed_keys - expected_keys:
        confounds.add(ModelHarnessConfoundCode.UNEXPECTED_CELL)

    expected_models = set(validated_protocol.models)
    expected_harnesses = set(validated_protocol.harnesses)
    expected_partitions = set(validated_protocol.partitions)
    for cell in validated_cells:
        _add_protocol_identity_confounds(confounds, validated_protocol, cell)
        if cell.protocol_hash != validated_protocol.content_hash:
            confounds.add(ModelHarnessConfoundCode.PROTOCOL_HASH_MISMATCH)
        if cell.coordinate.model not in expected_models:
            confounds.add(ModelHarnessConfoundCode.MODEL_IDENTITY_MISMATCH)
        if cell.coordinate.harness not in expected_harnesses:
            confounds.add(ModelHarnessConfoundCode.HARNESS_IDENTITY_MISMATCH)
        if cell.coordinate.partition not in expected_partitions:
            confounds.add(ModelHarnessConfoundCode.PARTITION_MISMATCH)
        chain = chains_by_id.get(cell.evidence_chain_receipt.record_id)
        if (
            chain is None
            or harness_cell_evidence_chain_receipt(chain) != cell.evidence_chain_receipt
            or chain.protocol_receipt
            != EvidenceReceipt(
                record_id=validated_protocol.protocol_id,
                schema_version=validated_protocol.schema_version,
                content_hash=validated_protocol.content_hash,
            )
            or chain.coordinate != cell.coordinate
        ):
            confounds.add(ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH)
            confounds.add(ModelHarnessConfoundCode.REWARD_RECEIPT_MISMATCH)
            continue
        snapshot = snapshots_by_chain_id.get(chain.chain_id)
        if (
            snapshot is None
            or snapshot.chain_receipt != harness_cell_evidence_chain_receipt(chain)
            or snapshot.trace_receipt != chain.trace_receipt
            or snapshot.freshness_receipt != chain.freshness_receipt
            or snapshot.assessment_receipt != chain.assessment_receipt
        ):
            confounds.add(ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH)
            confounds.add(ModelHarnessConfoundCode.REWARD_RECEIPT_MISMATCH)
            continue
        if snapshot.freshness_status is not TraceFreshnessStatus.CURRENT:
            confounds.add(ModelHarnessConfoundCode.STALE_TRACE)
        if snapshot.assessment_status is not RewardValidityStatus.VALID:
            confounds.add(ModelHarnessConfoundCode.INVALID_REWARD)
    return tuple(sorted(confounds, key=_CONFOUND_ORDER.__getitem__))


def _cell_map(
    cells: tuple[ModelHarnessCell, ...],
) -> dict[tuple[str, str, str, str, HarnessPartition], ModelHarnessCell]:
    return {_coordinate_lookup_key(cell.coordinate): cell for cell in cells}


def _lookup(
    cells: dict[tuple[str, str, str, str, HarnessPartition], ModelHarnessCell],
    model: ModelIdentity,
    harness: HarnessIdentity,
    partition: HarnessPartition,
) -> ModelHarnessCell:
    return cells[
        (
            model.model_id,
            model.model_version,
            harness.harness_id,
            harness.harness_version,
            partition,
        )
    ]


def _comparison(
    kind: ModelHarnessComparisonKind,
    partitions: tuple[HarnessPartition, ...],
    cells: tuple[ModelHarnessCell, ...],
    deltas: tuple[EvaluationMetricDeltaVector, ...],
) -> ModelHarnessComparison:
    return ModelHarnessComparison.build(
        kind=kind,
        partitions=partitions,
        cell_ids=tuple(item.cell_id for item in cells),
        cell_hashes=tuple(item.content_hash for item in cells),
        component_deltas=deltas,
    )


def _comparison_key(
    comparison: ModelHarnessComparison,
) -> tuple[int, tuple[int, ...], tuple[str, ...]]:
    return (
        _COMPARISON_ORDER[comparison.kind],
        tuple(_PARTITION_ORDER[item] for item in comparison.partitions),
        comparison.cell_ids,
    )


def _build_declared_comparisons(
    protocol: ModelHarnessProtocol,
    cells: tuple[ModelHarnessCell, ...],
) -> tuple[ModelHarnessComparison, ...]:
    by_coordinate = _cell_map(cells)
    comparisons_out: list[ModelHarnessComparison] = []
    declared = set(protocol.comparison_kinds)
    for partition in protocol.partitions:
        if ModelHarnessComparisonKind.MODEL_HELD_CONSTANT in declared:
            for model in protocol.models:
                for left_harness, right_harness in combinations(protocol.harnesses, 2):
                    left = _lookup(by_coordinate, model, left_harness, partition)
                    right = _lookup(by_coordinate, model, right_harness, partition)
                    comparisons_out.append(
                        _comparison(
                            ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,
                            (partition,),
                            (left, right),
                            (metric_component_deltas(left.metrics, right.metrics),),
                        )
                    )
        if ModelHarnessComparisonKind.HARNESS_HELD_CONSTANT in declared:
            for harness in protocol.harnesses:
                for left_model, right_model in combinations(protocol.models, 2):
                    left = _lookup(by_coordinate, left_model, harness, partition)
                    right = _lookup(by_coordinate, right_model, harness, partition)
                    comparisons_out.append(
                        _comparison(
                            ModelHarnessComparisonKind.HARNESS_HELD_CONSTANT,
                            (partition,),
                            (left, right),
                            (metric_component_deltas(left.metrics, right.metrics),),
                        )
                    )
        if ModelHarnessComparisonKind.INTERACTION_DESCRIPTIVE in declared:
            for left_model, right_model in combinations(protocol.models, 2):
                for left_harness, right_harness in combinations(protocol.harnesses, 2):
                    first = _lookup(by_coordinate, left_model, left_harness, partition)
                    second = _lookup(by_coordinate, left_model, right_harness, partition)
                    third = _lookup(by_coordinate, right_model, left_harness, partition)
                    fourth = _lookup(by_coordinate, right_model, right_harness, partition)
                    comparisons_out.append(
                        _comparison(
                            ModelHarnessComparisonKind.INTERACTION_DESCRIPTIVE,
                            (partition,),
                            (first, second, third, fourth),
                            (
                                metric_component_deltas(first.metrics, second.metrics),
                                metric_component_deltas(third.metrics, fourth.metrics),
                            ),
                        )
                    )
    if ModelHarnessComparisonKind.TRAIN_TEST_TRANSFER in declared:
        discovery = HarnessPartition.HARNESS_DISCOVERY_TASKS
        for held_out in protocol.partitions:
            if held_out is discovery:
                continue
            for model in protocol.models:
                for harness in protocol.harnesses:
                    left = _lookup(by_coordinate, model, harness, discovery)
                    right = _lookup(by_coordinate, model, harness, held_out)
                    comparisons_out.append(
                        _comparison(
                            ModelHarnessComparisonKind.TRAIN_TEST_TRANSFER,
                            (discovery, held_out),
                            (left, right),
                            (metric_component_deltas(left.metrics, right.metrics),),
                        )
                    )
    return tuple(sorted(comparisons_out, key=_comparison_key))


def analyze_model_harness(
    protocol: ModelHarnessProtocol,
    cells: tuple[ModelHarnessCell, ...] | list[ModelHarnessCell],
    *,
    evidence_chains: tuple[HarnessCellEvidenceChain, ...],
    evidence_index: HarnessEvidenceSnapshotIndex,
) -> ModelHarnessAnalysis:
    _require_bounded_raw_cells(cells)
    validated_protocol = ModelHarnessProtocol.model_validate(protocol)
    validated_cells = tuple(ModelHarnessCell.model_validate(item) for item in cells)
    ordered_cells = canonical_cells(validated_cells)
    confounds = validate_complete_matched_grid(
        validated_protocol,
        ordered_cells,
        evidence_chains=evidence_chains,
        evidence_index=evidence_index,
    )
    comparisons_out = (
        () if confounds else _build_declared_comparisons(validated_protocol, validated_cells)
    )
    analysis = ModelHarnessAnalysis.model_construct(
        schema_version=1,
        protocol=validated_protocol,
        protocol_id=validated_protocol.protocol_id,
        protocol_version=validated_protocol.version,
        protocol_hash=validated_protocol.content_hash,
        cell_ids=tuple(cell.cell_id for cell in ordered_cells),
        cell_hashes=tuple(cell.content_hash for cell in ordered_cells),
        comparisons=comparisons_out,
        confounds=confounds,
        causal_claim_permitted=False,
        content_hash="0" * 64,
    )
    return analysis.model_copy(update={"content_hash": model_harness_analysis_hash(analysis)})


__all__ = [
    "MAX_MODEL_HARNESS_COMPARISONS",
    "HarnessIdentity",
    "ModelBudgetBinding",
    "ModelHarnessAnalysis",
    "ModelHarnessCell",
    "ModelHarnessComparison",
    "ModelHarnessComparisonKind",
    "ModelHarnessConfoundCode",
    "ModelHarnessCoordinate",
    "ModelHarnessProtocol",
    "ModelIdentity",
    "analyze_model_harness",
    "canonical_cells",
    "evaluation_resource_envelope_hash",
    "model_budget_binding_hash",
    "model_harness_analysis_hash",
    "model_harness_cell_hash",
    "model_harness_comparison_hash",
    "model_harness_protocol_hash",
    "validate_complete_matched_grid",
]
