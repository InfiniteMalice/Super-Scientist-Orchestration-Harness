from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class FoundationModelConfiguration(_StrictFrozenModel):
    foundation_model_configuration_id: StableIdentifier
    provider_id: StableIdentifier
    model_id: StableIdentifier
    adapter_id: StableIdentifier | None = None


class PromptConfiguration(_StrictFrozenModel):
    prompt_configuration_id: StableIdentifier
    template_hash: Sha256Hex
    variable_names: tuple[StableIdentifier, ...]


class MemoryConfiguration(_StrictFrozenModel):
    memory_configuration_id: StableIdentifier
    schema_hash: Sha256Hex
    cross_run_enabled: bool


class ToolConfiguration(_StrictFrozenModel):
    tool_configuration_id: StableIdentifier
    tool_ids: tuple[StableIdentifier, ...]
    routing_hash: Sha256Hex


class ControlConfiguration(_StrictFrozenModel):
    control_configuration_id: StableIdentifier
    policy_hash: Sha256Hex
    max_steps: int = Field(strict=True, gt=0)


class ScaffoldConfiguration(_StrictFrozenModel):
    scaffold_configuration_id: StableIdentifier
    prompt: PromptConfiguration
    memory: MemoryConfiguration
    tools: ToolConfiguration
    control: ControlConfiguration


class AgentConfiguration(_StrictFrozenModel):
    agent_configuration_id: StableIdentifier
    foundation_model: FoundationModelConfiguration
    scaffold: ScaffoldConfiguration


class ConfigurationVersion(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    configuration_version_id: StableIdentifier
    agent_configuration: AgentConfiguration
    predecessor_configuration_version_id: StableIdentifier | None
    rollback_configuration_version_id: StableIdentifier
    created_by: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


type ConfigurationLayer = Literal[
    "FOUNDATION_MODEL",
    "SCAFFOLD",
    "PROMPT",
    "MEMORY",
    "TOOLS",
    "CONTROL",
]


class ConfigurationDiff(_StrictFrozenModel):
    baseline_configuration_version_id: StableIdentifier
    candidate_configuration_version_id: StableIdentifier
    changed_layers: tuple[ConfigurationLayer, ...]

    @classmethod
    def between(
        cls,
        baseline: ConfigurationVersion,
        candidate: ConfigurationVersion,
    ) -> ConfigurationDiff:
        baseline_agent = baseline.agent_configuration
        candidate_agent = candidate.agent_configuration
        baseline_scaffold = baseline_agent.scaffold
        candidate_scaffold = candidate_agent.scaffold
        changed: list[ConfigurationLayer] = []
        if baseline_agent.foundation_model != candidate_agent.foundation_model:
            changed.append("FOUNDATION_MODEL")
        if (
            baseline_scaffold.scaffold_configuration_id
            != candidate_scaffold.scaffold_configuration_id
        ):
            changed.append("SCAFFOLD")
        if baseline_scaffold.prompt != candidate_scaffold.prompt:
            changed.append("PROMPT")
        if baseline_scaffold.memory != candidate_scaffold.memory:
            changed.append("MEMORY")
        if baseline_scaffold.tools != candidate_scaffold.tools:
            changed.append("TOOLS")
        if baseline_scaffold.control != candidate_scaffold.control:
            changed.append("CONTROL")
        return cls(
            baseline_configuration_version_id=baseline.configuration_version_id,
            candidate_configuration_version_id=candidate.configuration_version_id,
            changed_layers=tuple(changed),
        )


class ExecutionState(_StrictFrozenModel):
    """Transient state; no authoritative repository accepts this record."""

    execution_state_id: StableIdentifier
    run_id: StableIdentifier
    step_index: int = Field(strict=True, ge=0)
    state_digest: Sha256Hex
    observed_at: UtcTimestamp


class AdapterTrainingRequest(_StrictFrozenModel):
    candidate_id: StableIdentifier
    base_model_configuration_id: StableIdentifier
    dataset_lineage_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    evaluation_id: StableIdentifier | None
    rollback_configuration_id: StableIdentifier
    requested_at: UtcTimestamp


class AdapterCandidateMetadata(_StrictFrozenModel):
    candidate_id: StableIdentifier
    base_model_configuration_id: StableIdentifier
    dataset_lineage_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    artifact_hash: Sha256Hex
    evaluation_id: StableIdentifier | None
    rollback_configuration_id: StableIdentifier
    promoted: Literal[False] = False


class FakeTrainer(Protocol):
    def train(self, request: AdapterTrainingRequest) -> AdapterCandidateMetadata: ...


class DeterministicFakeTrainer:
    """Produces candidate metadata without loading, training, or writing a model."""

    def train(self, request: AdapterTrainingRequest) -> AdapterCandidateMetadata:
        validated = AdapterTrainingRequest.model_validate(request.model_dump(mode="python"))
        digest = sha256_hex(canonical_json_bytes(validated.model_dump(mode="json")))
        return AdapterCandidateMetadata(
            candidate_id=validated.candidate_id,
            base_model_configuration_id=validated.base_model_configuration_id,
            dataset_lineage_ids=validated.dataset_lineage_ids,
            artifact_hash=digest,
            evaluation_id=validated.evaluation_id,
            rollback_configuration_id=validated.rollback_configuration_id,
        )
