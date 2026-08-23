from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, NoReturn, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from super_scientist.domain.cognition import CohortPlan
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.models import ResourceBudget, ResourceUsage
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    canonical_json_bytes,
    sha256_hex,
)

# Immutable states retain complete transition history, so the public envelope must keep
# both one-shot replay and repeated state evolution operationally bounded.
MAX_COLLABORATION_ITEMS = 256
MAX_PEERS = 16
MAX_TOPOLOGY_EDGES = 64
MAX_TOPOLOGY_CHANGES = 64
MAX_COLLABORATION_TRANSITIONS = MAX_COLLABORATION_ITEMS + MAX_TOPOLOGY_CHANGES
MAX_IDENTIFIER_LENGTH = 200
MAX_PUBLIC_TEXT_LENGTH = 8_000
MAX_CANDIDATE_CONTENT_LENGTH = 16_000
MAX_ARTIFACT_PATH_LENGTH = 1_000
MAX_MEDIA_TYPE_LENGTH = 255
MAX_ARTIFACT_SIZE_BYTES = 1_000_000_000_000
MAX_RESOURCE_SCALAR = 1_000_000_000_000
MAX_CANDIDATE_DEPTH = 12
MAX_CANDIDATE_NODES = 512
MAX_CANDIDATE_MAPPING_KEYS = 64
MAX_CANDIDATE_COLLECTION_ITEMS = 128
MAX_CANDIDATE_KEY_LENGTH = 200
MAX_CANDIDATE_STRING_LENGTH = 4_000
MAX_CANDIDATE_INTEGER_ABS = 10**18
MAX_CANDIDATE_FLOAT_ABS = 10**100
FORBIDDEN_CANDIDATE_KEYS = frozenset(
    {
        "chainofthought",
        "scratchpad",
        "providerpayload",
        "secret",
        "protectedanswer",
        "command",
    }
)
RESOURCE_FIELDS = (
    "cost_usd",
    "compute_units",
    "tokens",
    "elapsed_seconds",
    "tool_calls",
    "human_interventions",
)


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


BoundedIdentifier = Annotated[
    StableIdentifier,
    Field(max_length=MAX_IDENTIFIER_LENGTH),
]
BoundedPublicText = Annotated[
    str,
    BeforeValidator(_strip_text),
    Field(strict=True, min_length=1, max_length=MAX_PUBLIC_TEXT_LENGTH),
]


def _require_bounded_actor_identity(actor: ActorIdentity) -> ActorIdentity:
    values = (actor.actor_id, actor.provider_id, actor.model_id, actor.adapter_id)
    if any(
        value is not None and len(value) > MAX_IDENTIFIER_LENGTH for value in values
    ):
        raise ValueError("Phase A actor identity fields must be bounded identifiers")
    return actor


def _require_strict_actor_identity_input(
    value: object,
    info: ValidationInfo,
) -> object:
    raw = (
        value.model_dump(mode="python", warnings=False)
        if isinstance(value, ActorIdentity)
        else value
    )
    if not isinstance(raw, dict):
        raise ValueError("Phase A actor identity must be a strict object")
    string_fields = (
        "actor_id",
        "provider_id",
        "model_id",
        "adapter_id",
        "configuration_hash",
    )
    if any(
        field_name in raw
        and raw[field_name] is not None
        and not isinstance(raw[field_name], str)
        for field_name in string_fields
    ):
        raise ValueError("Phase A actor identity scalars must be strict")
    created_at = raw.get("created_at")
    kind = raw.get("kind")
    if info.mode == "python" and not isinstance(kind, ActorKind):
        raise ValueError("Phase A actor identity kind must be a strict ActorKind")
    if info.mode == "json" and not isinstance(kind, str):
        raise ValueError("Phase A actor identity JSON kind must be a string")
    if info.mode == "python" and not isinstance(created_at, datetime):
        raise ValueError("Phase A actor identity timestamp must be a strict datetime")
    if info.mode == "json" and not isinstance(created_at, str):
        raise ValueError("Phase A actor identity JSON timestamp must be a string")
    try:
        if info.mode == "json":
            parsed = ActorIdentity.model_validate(raw)
        else:
            parsed = ActorIdentity.model_validate(raw, strict=True)
    except (PydanticValidationError, TypeError, ValueError):
        raise ValueError("Phase A actor identity is invalid") from None
    return parsed


def _require_bounded_artifact(artifact: ArtifactRef) -> ArtifactRef:
    if (
        artifact.size_bytes > MAX_ARTIFACT_SIZE_BYTES
        or len(artifact.media_type) > MAX_MEDIA_TYPE_LENGTH
        or len(artifact.relative_path) > MAX_ARTIFACT_PATH_LENGTH
    ):
        raise ValueError("Phase A artifact reference fields exceed bounded limits")
    return artifact


def _require_strict_artifact_input(value: object) -> object:
    raw = (
        value.model_dump(mode="python", warnings=False)
        if isinstance(value, ArtifactRef)
        else value
    )
    if not isinstance(raw, dict):
        raise ValueError("Phase A artifact reference must be a strict object")
    if type(raw.get("size_bytes")) is not int or any(
        not isinstance(raw.get(field_name), str)
        for field_name in ("sha256", "media_type", "relative_path")
    ):
        raise ValueError("Phase A artifact reference scalars must be strict")
    try:
        return ArtifactRef.model_validate(raw, strict=True)
    except (PydanticValidationError, TypeError, ValueError):
        raise ValueError("Phase A artifact reference is invalid") from None


def _require_bounded_resources(
    resources: ResourceBudget | ResourceUsage,
) -> ResourceBudget | ResourceUsage:
    if any(
        Decimal(str(getattr(resources, field_name))) > MAX_RESOURCE_SCALAR
        for field_name in RESOURCE_FIELDS
    ):
        raise ValueError("Phase A resource fields exceed bounded limits")
    return resources


def _require_strict_resource_input(
    value: object,
    resource_type: type[ResourceBudget] | type[ResourceUsage],
) -> object:
    raw = (
        value.model_dump(mode="python", warnings=False)
        if isinstance(value, (ResourceBudget, ResourceUsage))
        else value
    )
    if not isinstance(raw, dict):
        raise ValueError("Phase A resources must be a strict object")
    for field_name in ("cost_usd", "compute_units", "elapsed_seconds"):
        if type(raw.get(field_name)) is not float:
            raise ValueError("Phase A resources require strict floating scalars")
    for field_name in ("tokens", "tool_calls", "human_interventions"):
        if type(raw.get(field_name)) is not int:
            raise ValueError("Phase A resources require strict integral scalars")
    try:
        return resource_type.model_validate(raw, strict=True)
    except (PydanticValidationError, TypeError, ValueError):
        raise ValueError("Phase A resources are invalid") from None


def _require_strict_resource_budget_input(value: object) -> object:
    return _require_strict_resource_input(value, ResourceBudget)


def _require_strict_resource_usage_input(value: object) -> object:
    return _require_strict_resource_input(value, ResourceUsage)


BoundedActorIdentity = Annotated[
    ActorIdentity,
    BeforeValidator(_require_strict_actor_identity_input),
    AfterValidator(_require_bounded_actor_identity),
]
BoundedArtifactRef = Annotated[
    ArtifactRef,
    BeforeValidator(_require_strict_artifact_input),
    AfterValidator(_require_bounded_artifact),
]
BoundedResourceBudget = Annotated[
    ResourceBudget,
    BeforeValidator(_require_strict_resource_budget_input),
    AfterValidator(_require_bounded_resources),
]
BoundedResourceUsage = Annotated[
    ResourceUsage,
    BeforeValidator(_require_strict_resource_usage_input),
    AfterValidator(_require_bounded_resources),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


def _canonical_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique values")
    if values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must use canonical order")
    return values


def _canonical_edges(
    edges: tuple[tuple[str, str], ...], field_name: str
) -> tuple[tuple[str, str], ...]:
    if any(source == target for source, target in edges):
        raise ValueError(f"{field_name} cannot contain self edges")
    if len(edges) != len(set(edges)) or edges != tuple(sorted(edges)):
        raise ValueError(f"{field_name} must be unique and canonically ordered")
    return edges


def _content_hash(model: BaseModel, hash_field: str = "content_hash") -> str:
    payload = model.model_dump(mode="json", exclude={hash_field})
    return sha256_hex(canonical_json_bytes(payload))


def _zero_usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.0,
        compute_units=0.0,
        tokens=0,
        elapsed_seconds=0.0,
        tool_calls=0,
        human_interventions=0,
    )


class _UsageAccumulator:
    def __init__(self) -> None:
        self.cost_usd = Decimal("0")
        self.compute_units = Decimal("0")
        self.tokens = 0
        self.elapsed_seconds = Decimal("0")
        self.tool_calls = 0
        self.human_interventions = 0

    def add(self, usage: ResourceUsage) -> None:
        self.cost_usd += Decimal(str(usage.cost_usd))
        self.compute_units += Decimal(str(usage.compute_units))
        self.tokens += usage.tokens
        self.elapsed_seconds += Decimal(str(usage.elapsed_seconds))
        self.tool_calls += usage.tool_calls
        self.human_interventions += usage.human_interventions

    def to_usage(self) -> ResourceUsage:
        return ResourceUsage(
            cost_usd=float(self.cost_usd),
            compute_units=float(self.compute_units),
            tokens=self.tokens,
            elapsed_seconds=float(self.elapsed_seconds),
            tool_calls=self.tool_calls,
            human_interventions=self.human_interventions,
        )


def sum_usage(usages: tuple[ResourceUsage, ...]) -> ResourceUsage:
    accumulator = _UsageAccumulator()
    for usage in usages:
        accumulator.add(usage)
    return accumulator.to_usage()


def usage_matches(
    left: ResourceBudget | ResourceUsage,
    right: ResourceBudget | ResourceUsage,
) -> bool:
    return (
        left.tokens == right.tokens
        and left.tool_calls == right.tool_calls
        and left.human_interventions == right.human_interventions
        and all(
            Decimal(str(getattr(left, field_name)))
            == Decimal(str(getattr(right, field_name)))
            for field_name in ("cost_usd", "compute_units", "elapsed_seconds")
        )
    )


def remaining_resources(budget: ResourceBudget, usage: ResourceUsage) -> ResourceBudget:
    if any(getattr(usage, name) > getattr(budget, name) for name in RESOURCE_FIELDS):
        raise ValueError("usage exceeds collaboration resource budget")
    values: dict[str, float | int] = {}
    for name in RESOURCE_FIELDS:
        if name in {"tokens", "tool_calls", "human_interventions"}:
            values[name] = getattr(budget, name) - getattr(usage, name)
        else:
            values[name] = float(
                Decimal(str(getattr(budget, name)))
                - Decimal(str(getattr(usage, name)))
            )
    return ResourceBudget.model_validate(values)


class CollaborationTerminationReason(StrEnum):
    COMPLETED = "COMPLETED"
    MAX_HOPS_REACHED = "MAX_HOPS_REACHED"
    MAX_CONTRIBUTIONS_REACHED = "MAX_CONTRIBUTIONS_REACHED"
    PER_PEER_LIMIT_REACHED = "PER_PEER_LIMIT_REACHED"
    TOPOLOGY_CHANGE_LIMIT_REACHED = "TOPOLOGY_CHANGE_LIMIT_REACHED"
    NO_ELIGIBLE_PEER = "NO_ELIGIBLE_PEER"
    REPEATED_STATE_LOOP = "REPEATED_STATE_LOOP"
    TOPOLOGY_CHURN = "TOPOLOGY_CHURN"
    CONTRIBUTION_MONOPOLY = "CONTRIBUTION_MONOPOLY"


class TopologyOperation(StrEnum):
    ENABLE_EDGE = "ENABLE_EDGE"
    DISABLE_EDGE = "DISABLE_EDGE"
    ACTIVATE_PEER = "ACTIVATE_PEER"
    DEACTIVATE_PEER = "DEACTIVATE_PEER"


class CollaborationTransitionKind(StrEnum):
    PEER_EXCHANGE = "PEER_EXCHANGE"
    TOPOLOGY_EVENT = "TOPOLOGY_EVENT"


class PeerRoleAssignment(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    peer_id: BoundedIdentifier
    role_id: BoundedIdentifier


class CollaborationTransition(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    position: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS * 2)
    kind: CollaborationTransitionKind
    request_id: BoundedIdentifier | None
    contribution_id: BoundedIdentifier | None
    topology_event_id: BoundedIdentifier | None

    @model_validator(mode="after")
    def require_exact_transition_target(self) -> Self:
        peer_exchange = self.kind is CollaborationTransitionKind.PEER_EXCHANGE
        if peer_exchange and (
            self.request_id is None
            or self.contribution_id is None
            or self.topology_event_id is not None
        ):
            raise ValueError("peer transition must bind exactly one request and contribution")
        if not peer_exchange and (
            self.request_id is not None
            or self.contribution_id is not None
            or self.topology_event_id is None
        ):
            raise ValueError("topology transition must bind exactly one topology event")
        return self


class CollaborationCompletionPredicate(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    min_contributions: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
    required_contribution_kind: BoundedIdentifier | None = None


class CollaborationBudget(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    max_peers: int = Field(strict=True, ge=1, le=MAX_PEERS)
    max_hops: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
    max_contributions: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
    max_contributions_per_peer: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
    max_topology_changes: int = Field(strict=True, ge=0, le=MAX_TOPOLOGY_CHANGES)
    max_parent_depth: int = Field(strict=True, ge=0, le=MAX_PEERS)
    max_state_repetitions: int = Field(strict=True, ge=1, le=MAX_PEERS)
    max_topology_churn: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
    max_peer_contribution_share: float = Field(strict=True, gt=0.0, le=1.0, allow_inf_nan=False)
    resources: BoundedResourceBudget
    allowed_tool_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)

    @field_validator("allowed_tool_ids")
    @classmethod
    def require_canonical_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_unique(values, "allowed_tool_ids")


class _TopologySnapshotPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    active_peer_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    enabled_edges: tuple[tuple[BoundedIdentifier, BoundedIdentifier], ...] = Field(
        max_length=MAX_TOPOLOGY_EDGES
    )

    @field_validator("active_peer_ids")
    @classmethod
    def require_canonical_peers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_unique(values, "active_peer_ids")

    @field_validator("enabled_edges")
    @classmethod
    def require_canonical_edges(
        cls, values: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        return _canonical_edges(values, "enabled_edges")

    @model_validator(mode="after")
    def require_active_edge_endpoints(self) -> Self:
        active = set(self.active_peer_ids)
        if any(
            source not in active or target not in active for source, target in self.enabled_edges
        ):
            raise ValueError("enabled edge endpoints must be active peers")
        return self


class TopologySnapshot(_TopologySnapshotPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> TopologySnapshot:
        payload = _TopologySnapshotPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    @model_validator(mode="after")
    def require_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the topology snapshot")
        return self


class _TopologyEventPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: BoundedIdentifier
    session_id: BoundedIdentifier
    sequence: int = Field(strict=True, ge=1, le=MAX_TOPOLOGY_CHANGES)
    before_topology_hash: Sha256Hex
    operation: TopologyOperation
    peer_id: BoundedIdentifier | None
    edge: tuple[BoundedIdentifier, BoundedIdentifier] | None
    reason_code: BoundedIdentifier
    after_topology_hash: Sha256Hex

    @model_validator(mode="after")
    def require_exact_operation_target(self) -> Self:
        peer_operation = self.operation in {
            TopologyOperation.ACTIVATE_PEER,
            TopologyOperation.DEACTIVATE_PEER,
        }
        if peer_operation != (self.peer_id is not None and self.edge is None):
            raise ValueError("topology event must declare exactly one operation target")
        if not peer_operation and (self.edge is None or self.peer_id is not None):
            raise ValueError("topology event must declare exactly one operation target")
        if self.edge is not None and self.edge[0] == self.edge[1]:
            raise ValueError("topology event edge endpoints must be distinct")
        if self.before_topology_hash == self.after_topology_hash:
            raise ValueError("topology event must change the topology hash")
        return self


class TopologyEvent(_TopologyEventPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> TopologyEvent:
        payload = _TopologyEventPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    @model_validator(mode="after")
    def require_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the topology event")
        return self


class _CollaborationSessionPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    session_id: BoundedIdentifier
    task_id: BoundedIdentifier
    cohort_plan: CohortPlan
    peers: tuple[BoundedActorIdentity, ...] = Field(min_length=1, max_length=MAX_PEERS)
    role_assignments: tuple[PeerRoleAssignment, ...] = Field(min_length=1, max_length=MAX_PEERS)
    tools: tuple[BoundedActorIdentity, ...] = Field(max_length=MAX_PEERS)
    allowed_artifacts: tuple[BoundedArtifactRef, ...] = Field(
        max_length=MAX_COLLABORATION_ITEMS
    )
    budget: CollaborationBudget
    allowed_contribution_kinds: tuple[BoundedIdentifier, ...] = Field(
        min_length=1, max_length=MAX_PEERS
    )
    declared_edges: tuple[tuple[BoundedIdentifier, BoundedIdentifier], ...] = Field(
        max_length=MAX_TOPOLOGY_EDGES
    )
    initial_active_peer_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    scheduling_policy_version: BoundedIdentifier
    topology_policy_version: BoundedIdentifier
    completion_predicate: CollaborationCompletionPredicate
    governing_policy_hash: Sha256Hex

    @field_validator("declared_edges")
    @classmethod
    def require_canonical_edges(
        cls, values: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        return _canonical_edges(values, "declared_edges")

    @field_validator("initial_active_peer_ids", "allowed_contribution_kinds")
    @classmethod
    def require_canonical_ids(cls, values: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _canonical_unique(values, info.field_name)

    @model_validator(mode="after")
    def require_fixed_declared_envelope(self) -> Self:
        peer_ids = tuple(peer.actor_id for peer in self.peers)
        _canonical_unique(peer_ids, "peers")
        member_ids = tuple(sorted(member.actor_id for member in self.cohort_plan.members))
        if peer_ids != member_ids:
            raise ValueError("session peers must exactly match the cohort plan members")
        if self.task_id != self.cohort_plan.task_id:
            raise ValueError("session task must match the cohort plan task")
        if self.governing_policy_hash != self.cohort_plan.governing_policy_hash:
            raise ValueError("session and cohort plan must share governing policy")
        if not self.cohort_plan.minimum_size_met:
            raise ValueError("session requires a cohort plan meeting its minimum size")
        if len(peer_ids) > self.budget.max_peers:
            raise ValueError("session peer roster exceeds max_peers")
        role_ids = tuple(item.peer_id for item in self.role_assignments)
        if role_ids != peer_ids:
            raise ValueError("role assignments must exactly cover peers in canonical order")
        tool_ids = tuple(tool.actor_id for tool in self.tools)
        _canonical_unique(tool_ids, "tools")
        if any(tool.kind is not ActorKind.TOOL for tool in self.tools):
            raise ValueError("session tools require fixed TOOL actor identities")
        if tool_ids != self.budget.allowed_tool_ids:
            raise ValueError("fixed tool identities must exactly match allowed tool IDs")
        artifact_hashes = tuple(item.sha256 for item in self.allowed_artifacts)
        if len(artifact_hashes) != len(set(artifact_hashes)) or artifact_hashes != tuple(
            sorted(artifact_hashes)
        ):
            raise ValueError("allowed artifacts must have unique hashes in canonical order")
        peer_set = set(peer_ids)
        if not set(self.initial_active_peer_ids).issubset(peer_set):
            raise ValueError("initial active peers must be declared peers")
        if any(
            source not in peer_set or target not in peer_set
            for source, target in self.declared_edges
        ):
            raise ValueError("declared edge endpoints must be declared peers")
        predicate_kind = self.completion_predicate.required_contribution_kind
        if predicate_kind is not None and predicate_kind not in self.allowed_contribution_kinds:
            raise ValueError("completion predicate kind must be an allowed contribution kind")
        return self


class CollaborationSession(_CollaborationSessionPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> CollaborationSession:
        payload = _CollaborationSessionPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    def remaining_resources(self, usage: ResourceUsage) -> ResourceBudget:
        return remaining_resources(self.budget.resources, usage)

    @model_validator(mode="after")
    def require_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the collaboration session")
        return self


def _require_canonical_artifacts(
    artifacts: tuple[BoundedArtifactRef, ...],
) -> tuple[BoundedArtifactRef, ...]:
    hashes = tuple(item.sha256 for item in artifacts)
    _canonical_unique(hashes, "artifact_refs")
    return artifacts


class _PeerRequestPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    request_id: BoundedIdentifier
    session_id: BoundedIdentifier
    sequence: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
    sender_id: BoundedIdentifier | None
    recipient_id: BoundedIdentifier
    requested_capability_id: BoundedIdentifier
    question: BoundedPublicText
    artifact_refs: tuple[BoundedArtifactRef, ...] = Field(
        max_length=MAX_COLLABORATION_ITEMS
    )
    parent_contribution_id: BoundedIdentifier | None
    tool_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    remaining_budget: BoundedResourceBudget

    @field_validator("artifact_refs")
    @classmethod
    def require_artifacts(
        cls, values: tuple[BoundedArtifactRef, ...]
    ) -> tuple[BoundedArtifactRef, ...]:
        return _require_canonical_artifacts(values)

    @field_validator("tool_ids")
    @classmethod
    def require_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_unique(values, "tool_ids")


class PeerRequest(_PeerRequestPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> PeerRequest:
        payload = _PeerRequestPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    @model_validator(mode="after")
    def require_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the peer request")
        return self


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number is forbidden")


def _reject_candidate_content(message: str) -> NoReturn:
    error = PydanticValidationError.from_exception_data(
        "CanonicalCandidateJson",
        [
            {
                "type": "value_error",
                "loc": (),
                "input": "[REDACTED]",
                "ctx": {"error": ValueError(message)},
            }
        ],
    )
    raise error from None


def _canonical_candidate_json(value: object) -> object:
    if not isinstance(value, str) or len(value) > MAX_CANDIDATE_CONTENT_LENGTH:
        _reject_candidate_content("candidate_content must be a bounded canonical JSON object")
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_json_constant,
        )
    except (
        json.JSONDecodeError,
        MemoryError,
        OverflowError,
        RecursionError,
        ValueError,
    ):
        _reject_candidate_content("candidate_content must be a bounded canonical JSON object")
    if not isinstance(parsed, dict):
        _reject_candidate_content("candidate_content must be a bounded canonical JSON object")

    node_count = 0
    stack: list[tuple[object, int]] = [(parsed, 1)]
    while stack:
        item, depth = stack.pop()
        node_count += 1
        if node_count > MAX_CANDIDATE_NODES or depth > MAX_CANDIDATE_DEPTH:
            _reject_candidate_content("candidate_content exceeds bounded depth or node limits")
        if isinstance(item, dict):
            if len(item) > MAX_CANDIDATE_MAPPING_KEYS:
                _reject_candidate_content("candidate_content exceeds bounded mapping size")
            for key, nested in item.items():
                if (
                    not key
                    or key != key.strip()
                    or len(key) > MAX_CANDIDATE_KEY_LENGTH
                ):
                    _reject_candidate_content("candidate_content contains an invalid bounded key")
                normalized_key = "".join(
                    character.casefold()
                    for character in unicodedata.normalize("NFKC", key)
                    if character.isalnum()
                )
                if normalized_key in FORBIDDEN_CANDIDATE_KEYS:
                    _reject_candidate_content(
                        "candidate_content contains forbidden public candidate key"
                    )
                stack.append((nested, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_CANDIDATE_COLLECTION_ITEMS:
                _reject_candidate_content("candidate_content exceeds bounded collection size")
            stack.extend((nested, depth + 1) for nested in item)
        elif isinstance(item, str):
            if len(item) > MAX_CANDIDATE_STRING_LENGTH:
                _reject_candidate_content("candidate_content contains an oversized string")
        elif isinstance(item, bool) or item is None:
            continue
        elif isinstance(item, int):
            if abs(item) > MAX_CANDIDATE_INTEGER_ABS:
                _reject_candidate_content("candidate_content contains an oversized integer")
        elif isinstance(item, float):
            if not math.isfinite(item) or abs(item) > MAX_CANDIDATE_FLOAT_ABS:
                _reject_candidate_content("candidate_content contains an invalid finite number")
        else:
            _reject_candidate_content("candidate_content contains an unsupported public value")
    try:
        canonical = canonical_json_bytes(parsed).decode("utf-8")
    except (MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        _reject_candidate_content("candidate_content must be a bounded canonical JSON object")
    if canonical != value:
        _reject_candidate_content("candidate_content must be a canonical JSON object")
    return value


CanonicalCandidateJson = Annotated[
    str,
    BeforeValidator(_canonical_candidate_json),
    Field(strict=True, min_length=2, max_length=MAX_CANDIDATE_CONTENT_LENGTH),
]


class _PeerContributionPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    contribution_id: BoundedIdentifier
    session_id: BoundedIdentifier
    request_id: BoundedIdentifier
    peer_id: BoundedIdentifier
    parent_contribution_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    contribution_kind: BoundedIdentifier
    rationale_summary: BoundedPublicText
    candidate_content: CanonicalCandidateJson
    artifact_refs: tuple[BoundedArtifactRef, ...] = Field(
        max_length=MAX_COLLABORATION_ITEMS
    )
    tool_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @field_validator("parent_contribution_ids", "tool_ids")
    @classmethod
    def require_canonical_ids(cls, values: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _canonical_unique(values, info.field_name)

    @field_validator("artifact_refs")
    @classmethod
    def require_artifacts(
        cls, values: tuple[BoundedArtifactRef, ...]
    ) -> tuple[BoundedArtifactRef, ...]:
        return _require_canonical_artifacts(values)


class PeerContribution(_PeerContributionPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> PeerContribution:
        payload = _PeerContributionPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    @model_validator(mode="after")
    def require_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the peer contribution")
        return self


class _CollaborationStatePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    session: CollaborationSession
    topology: TopologySnapshot
    topology_history: tuple[TopologySnapshot, ...] = Field(
        min_length=1, max_length=MAX_TOPOLOGY_CHANGES + 1
    )
    topology_events: tuple[TopologyEvent, ...] = Field(max_length=MAX_TOPOLOGY_CHANGES)
    requests: tuple[PeerRequest, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    contributions: tuple[PeerContribution, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    usage_history: tuple[BoundedResourceUsage, ...] = Field(
        max_length=MAX_COLLABORATION_ITEMS
    )
    usage: BoundedResourceUsage
    hop_count: int = Field(strict=True, ge=0, le=MAX_COLLABORATION_ITEMS)
    scheduling_position: int = Field(strict=True, ge=0, le=MAX_COLLABORATION_TRANSITIONS)
    transitions: tuple[CollaborationTransition, ...] = Field(
        max_length=MAX_COLLABORATION_TRANSITIONS
    )
    observed_state_hashes: tuple[Sha256Hex, ...] = Field(
        min_length=1,
        max_length=MAX_COLLABORATION_TRANSITIONS + 1,
    )
    cycle_projection_hashes: tuple[Sha256Hex, ...] = Field(
        min_length=1,
        max_length=MAX_COLLABORATION_TRANSITIONS + 1,
    )
    completed: bool

    @model_validator(mode="after")
    def require_semantic_integrity(self) -> Self:
        session = self.session
        peer_ids = {peer.actor_id for peer in session.peers}
        allowed_tools = set(session.budget.allowed_tool_ids)
        allowed_artifacts = set(session.allowed_artifacts)
        allowed_capabilities = {
            assessment.requirement.capability_id
            for member in session.cohort_plan.members
            for assessment in member.assessments
        }
        if len(self.topology_history) != len(self.topology_events) + 1:
            raise ValueError("topology history must exactly cover topology events")
        if self.topology != self.topology_history[-1]:
            raise ValueError("current topology must be the final topology history snapshot")
        if self.topology_history[0].active_peer_ids != session.initial_active_peer_ids:
            raise ValueError("initial topology peers must match the session")
        expected_initial_edges = tuple(
            edge
            for edge in session.declared_edges
            if edge[0] in session.initial_active_peer_ids
            and edge[1] in session.initial_active_peer_ids
        )
        if self.topology_history[0].enabled_edges != expected_initial_edges:
            raise ValueError("initial topology edges must match the session")
        for index, event in enumerate(self.topology_events):
            before = self.topology_history[index]
            after = self.topology_history[index + 1]
            if event.sequence != index + 1 or event.session_id != session.session_id:
                raise ValueError("topology event sequence and session must match state")
            if (
                event.before_topology_hash != before.content_hash
                or event.after_topology_hash != after.content_hash
            ):
                raise ValueError("topology event hashes must exactly bind topology history")
            if _apply_topology_operation(session, before, event) != after:
                raise ValueError("topology event operation must exactly produce its after snapshot")
        if not (len(self.requests) == len(self.contributions) == len(self.usage_history)):
            raise ValueError("requests, contributions, and usage history must be one-to-one")
        if self.hop_count != len(self.contributions):
            raise ValueError("hop_count must equal retained contributions")
        if self.scheduling_position != len(self.contributions) + len(self.topology_events):
            raise ValueError("scheduling position must equal checked transitions")
        if len(self.transitions) != self.scheduling_position:
            raise ValueError("transition journal must exactly cover the scheduling position")
        if self.hop_count > session.budget.max_hops:
            raise ValueError("collaboration state exceeds its hop budget")
        if len(self.contributions) > session.budget.max_contributions:
            raise ValueError("collaboration state exceeds its contribution budget")
        if len(self.topology_events) > session.budget.max_topology_changes:
            raise ValueError("collaboration state exceeds its topology-change budget")
        retained_peer_counts = Counter(item.peer_id for item in self.contributions)
        if any(
            count > session.budget.max_contributions_per_peer
            for count in retained_peer_counts.values()
        ):
            raise ValueError("collaboration state exceeds its per-peer contribution budget")
        if not usage_matches(self.usage, sum_usage(self.usage_history)):
            raise ValueError("aggregate usage must equal usage history")
        validation_usage_accumulator = _UsageAccumulator()
        known_depths: dict[str, int] = {}
        known_ids: set[str] = set()
        request_ids: set[str] = set()
        for index, (request, contribution, transition_usage) in enumerate(
            zip(self.requests, self.contributions, self.usage_history, strict=True), start=1
        ):
            if request.sequence != index:
                raise ValueError("request sequence must be consecutive")
            if (
                request.session_id != session.session_id
                or contribution.session_id != session.session_id
            ):
                raise ValueError("request and contribution session must match state")
            if contribution.request_id != request.request_id:
                raise ValueError("contribution must bind its peer request")
            if request.request_id in request_ids:
                raise ValueError("request IDs must be unique")
            if request.recipient_id != contribution.peer_id:
                raise ValueError("contribution peer must match request recipient")
            if request.recipient_id not in peer_ids or (
                request.sender_id is not None and request.sender_id not in peer_ids
            ):
                raise ValueError("requests must use declared peers")
            if request.requested_capability_id not in allowed_capabilities:
                raise ValueError("request capability must be declared by the cohort")
            if not set(request.tool_ids).issubset(allowed_tools) or not set(
                contribution.tool_ids
            ).issubset(allowed_tools):
                raise ValueError("requests and contributions must use declared tools")
            if not set(request.artifact_refs).issubset(allowed_artifacts) or not set(
                contribution.artifact_refs
            ).issubset(allowed_artifacts):
                raise ValueError("requests and contributions must use declared artifacts")
            if (
                request.parent_contribution_id is not None
                and request.parent_contribution_id not in known_ids
            ):
                raise ValueError("request parent must be a declared prior contribution")
            if not set(contribution.parent_contribution_ids).issubset(known_ids):
                raise ValueError("contribution parents must be declared prior contributions")
            if (
                request.parent_contribution_id is not None
                and request.parent_contribution_id not in contribution.parent_contribution_ids
            ):
                raise ValueError("contribution must retain its request parent")
            if contribution.contribution_kind not in session.allowed_contribution_kinds:
                raise ValueError("contribution kind must be declared by the session")
            parent_depth = 0
            if contribution.parent_contribution_ids:
                parent_depth = 1 + max(
                    known_depths[parent] for parent in contribution.parent_contribution_ids
                )
            if parent_depth > session.budget.max_parent_depth:
                raise ValueError("contribution parent depth exceeds collaboration budget")
            if contribution.contribution_id in known_ids:
                raise ValueError("contribution IDs must be unique")
            expected_remaining = remaining_resources(
                session.budget.resources,
                validation_usage_accumulator.to_usage(),
            )
            if not usage_matches(request.remaining_budget, expected_remaining):
                raise ValueError("request remaining budget must match state usage")
            known_ids.add(contribution.contribution_id)
            request_ids.add(request.request_id)
            known_depths[contribution.contribution_id] = parent_depth
            validation_usage_accumulator.add(transition_usage)
        completed = _completion_satisfied(session, self.contributions)
        if self.completed != completed:
            raise ValueError("completed flag must exactly match the session completion predicate")
        self._require_deterministic_transition_replay()
        return self

    def _require_deterministic_transition_replay(self) -> None:
        expected_observation_count = len(self.transitions) + 1
        if (
            len(self.observed_state_hashes) != expected_observation_count
            or len(self.cycle_projection_hashes) != expected_observation_count
        ):
            raise ValueError(
                "state observations must cover the initial state and every transition"
            )
        topology_index = 0
        peer_index = 0
        last_peer_id: str | None = None
        topology = self.topology_history[0]
        peer_counts: Counter[str] = Counter()
        contribution_kind_counts: Counter[str] = Counter()
        cycle_projection_counts: Counter[str] = Counter()
        topology_hashes = [topology.content_hash]
        topology_churn_count = 0
        prior_topology_hash: str | None = None
        usage_accumulator = _UsageAccumulator()
        usage = usage_accumulator.to_usage()
        request_ids: set[str] = set()
        contribution_depths: dict[str, int] = {}
        completed = False
        initial_semantic_hash = collaboration_semantic_state_hash(
            session=self.session,
            topology=topology,
            prior_topology_hash=prior_topology_hash,
            topology_event_count=topology_index,
            topology_churn_count=topology_churn_count,
            peer_contribution_counts=peer_counts,
            contribution_kind_counts=contribution_kind_counts,
            last_peer_id=last_peer_id,
            usage=usage,
            request_ids=tuple(sorted(request_ids)),
            contribution_depths=tuple(sorted(contribution_depths.items())),
            completed=completed,
        )
        initial_cycle_projection = collaboration_cycle_projection_hash(
            session=self.session,
            topology=topology,
            peer_contribution_counts=peer_counts,
            contribution_kind_counts=contribution_kind_counts,
            last_peer_id=last_peer_id,
            usage=usage,
            completed=completed,
        )
        if self.observed_state_hashes[0] != initial_semantic_hash:
            raise ValueError("semantic state observations must be replay-authentic")
        if self.cycle_projection_hashes[0] != initial_cycle_projection:
            raise ValueError("cycle projections must be replay-authentic")
        cycle_projection_counts[initial_cycle_projection] += 1

        for position, transition in enumerate(self.transitions, start=1):
            if transition.position != position:
                raise ValueError("transition positions must be consecutive")
            reason = _termination_reason_from_summary(
                session=self.session,
                topology=topology,
                topology_event_count=topology_index,
                contribution_count=peer_index,
                peer_contribution_counts=peer_counts,
                last_peer_id=last_peer_id,
                observed_cycle_projection_counts=cycle_projection_counts,
                topology_churn_count=topology_churn_count,
                completed=completed,
            )
            if reason is not None:
                raise ValueError(
                    "transition journal continues after collaboration termination: "
                    f"{reason}"
                )
            if transition.kind is CollaborationTransitionKind.TOPOLOGY_EVENT:
                if topology_index >= len(self.topology_events):
                    raise ValueError("transition journal references an absent topology event")
                event = self.topology_events[topology_index]
                if transition.topology_event_id != event.event_id:
                    raise ValueError("transition journal must bind the exact topology event")
                topology_index += 1
                prior_topology_hash = topology.content_hash
                topology = self.topology_history[topology_index]
                if (
                    len(topology_hashes) >= 2
                    and topology.content_hash == topology_hashes[-2]
                ):
                    topology_churn_count += 1
                topology_hashes.append(topology.content_hash)
            else:
                if peer_index >= len(self.requests):
                    raise ValueError("transition journal references an absent peer exchange")
                request = self.requests[peer_index]
                contribution = self.contributions[peer_index]
                if (
                    transition.request_id != request.request_id
                    or transition.contribution_id != contribution.contribution_id
                ):
                    raise ValueError("transition journal must bind the exact peer exchange")
                if request.sender_id != last_peer_id:
                    raise ValueError("request sender must match the prior contributing peer")
                eligible = _eligible_peer_ids_from_summary(
                    self.session,
                    topology,
                    peer_counts,
                    last_peer_id,
                )
                expected_peer = eligible[0] if eligible else None
                if request.recipient_id != expected_peer:
                    raise ValueError("request recipient must be the expected peer")
                usage_accumulator.add(self.usage_history[peer_index])
                usage = usage_accumulator.to_usage()
                peer_counts[contribution.peer_id] += 1
                contribution_kind_counts[contribution.contribution_kind] += 1
                last_peer_id = contribution.peer_id
                request_ids.add(request.request_id)
                parent_depth = (
                    0
                    if not contribution.parent_contribution_ids
                    else 1
                    + max(
                        contribution_depths[parent]
                        for parent in contribution.parent_contribution_ids
                    )
                )
                contribution_depths[contribution.contribution_id] = parent_depth
                peer_index += 1
                completed = _completion_satisfied_from_summary(
                    self.session,
                    peer_index,
                    contribution_kind_counts,
                )

            semantic_hash = collaboration_semantic_state_hash(
                session=self.session,
                topology=topology,
                prior_topology_hash=prior_topology_hash,
                topology_event_count=topology_index,
                topology_churn_count=topology_churn_count,
                peer_contribution_counts=peer_counts,
                contribution_kind_counts=contribution_kind_counts,
                last_peer_id=last_peer_id,
                usage=usage,
                request_ids=tuple(sorted(request_ids)),
                contribution_depths=tuple(sorted(contribution_depths.items())),
                completed=completed,
            )
            if self.observed_state_hashes[position] != semantic_hash:
                raise ValueError("semantic state observations must be replay-authentic")
            cycle_projection = collaboration_cycle_projection_hash(
                session=self.session,
                topology=topology,
                peer_contribution_counts=peer_counts,
                contribution_kind_counts=contribution_kind_counts,
                last_peer_id=last_peer_id,
                usage=usage,
                completed=completed,
            )
            if self.cycle_projection_hashes[position] != cycle_projection:
                raise ValueError("cycle projections must be replay-authentic")
            cycle_projection_counts[cycle_projection] += 1
        if topology_index != len(self.topology_events) or peer_index != len(self.requests):
            raise ValueError("transition journal must retain every checked transition")
        if topology != self.topology:
            raise ValueError("transition journal topology must match current topology")


class CollaborationState(_CollaborationStatePayload):
    state_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> CollaborationState:
        payload = _CollaborationStatePayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), state_hash=digest)

    @model_validator(mode="after")
    def require_hash(self) -> Self:
        if self.state_hash != _content_hash(self, "state_hash"):
            raise ValueError("state_hash must canonically address the collaboration state")
        return self


class CollaborationTermination(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    reason: CollaborationTerminationReason | None

    @property
    def terminated(self) -> bool:
        return self.reason is not None


def _completion_satisfied(
    session: CollaborationSession, contributions: tuple[PeerContribution, ...]
) -> bool:
    predicate = session.completion_predicate
    if len(contributions) < predicate.min_contributions:
        return False
    required = predicate.required_contribution_kind
    return required is None or any(item.contribution_kind == required for item in contributions)


def _completion_satisfied_from_summary(
    session: CollaborationSession,
    contribution_count: int,
    contribution_kind_counts: Counter[str],
) -> bool:
    predicate = session.completion_predicate
    if contribution_count < predicate.min_contributions:
        return False
    required = predicate.required_contribution_kind
    return required is None or contribution_kind_counts[required] > 0


def collaboration_semantic_state_hash(
    *,
    session: CollaborationSession,
    topology: TopologySnapshot,
    prior_topology_hash: str | None,
    topology_event_count: int,
    topology_churn_count: int,
    peer_contribution_counts: Counter[str],
    contribution_kind_counts: Counter[str],
    last_peer_id: str | None,
    usage: ResourceUsage,
    request_ids: tuple[str, ...],
    contribution_depths: tuple[tuple[str, int], ...],
    completed: bool,
) -> str:
    """Authenticate every retained value that can affect a future transition."""

    payload = {
        "semantic_schema_version": 2,
        "session_content_hash": session.content_hash,
        "topology_content_hash": topology.content_hash,
        "prior_topology_hash": prior_topology_hash,
        "topology_event_count": topology_event_count,
        "topology_churn_count": topology_churn_count,
        "peer_contribution_counts": tuple(sorted(peer_contribution_counts.items())),
        "contribution_kind_counts": tuple(sorted(contribution_kind_counts.items())),
        "last_peer_id": last_peer_id,
        "usage": usage.model_dump(mode="json"),
        "request_ids": request_ids,
        "contribution_depths": contribution_depths,
        "completed": completed,
    }
    return sha256_hex(canonical_json_bytes(payload))


def collaboration_cycle_projection_hash(
    *,
    session: CollaborationSession,
    topology: TopologySnapshot,
    peer_contribution_counts: Counter[str],
    contribution_kind_counts: Counter[str],
    last_peer_id: str | None,
    usage: ResourceUsage,
    completed: bool,
) -> str:
    """Project operational state for cycle counting, excluding monotonic audit progress."""

    payload = {
        "cycle_projection_schema_version": 1,
        "session_content_hash": session.content_hash,
        "topology_content_hash": topology.content_hash,
        "peer_contribution_counts": tuple(sorted(peer_contribution_counts.items())),
        "contribution_kind_counts": tuple(sorted(contribution_kind_counts.items())),
        "last_peer_id": last_peer_id,
        "usage": usage.model_dump(mode="json"),
        "completed": completed,
    }
    return sha256_hex(canonical_json_bytes(payload))


def _apply_topology_operation(
    session: CollaborationSession, before: TopologySnapshot, event: TopologyEvent
) -> TopologySnapshot:
    peers = set(before.active_peer_ids)
    edges = set(before.enabled_edges)
    declared_peers = {peer.actor_id for peer in session.peers}
    declared_edges = set(session.declared_edges)
    if event.operation in {TopologyOperation.ACTIVATE_PEER, TopologyOperation.DEACTIVATE_PEER}:
        assert event.peer_id is not None
        if event.peer_id not in declared_peers:
            raise ValueError("topology event peer must be a declared peer")
        if event.operation is TopologyOperation.ACTIVATE_PEER:
            if event.peer_id in peers:
                raise ValueError("topology peer is already active")
            peers.add(event.peer_id)
        else:
            if event.peer_id not in peers:
                raise ValueError("topology peer is already inactive")
            peers.remove(event.peer_id)
            edges = {edge for edge in edges if event.peer_id not in edge}
    else:
        assert event.edge is not None
        if event.edge not in declared_edges:
            raise ValueError("topology event edge must be a declared edge")
        if not set(event.edge).issubset(peers):
            raise ValueError("topology edge endpoints must be active")
        if event.operation is TopologyOperation.ENABLE_EDGE:
            if event.edge in edges:
                raise ValueError("topology edge is already enabled")
            edges.add(event.edge)
        else:
            if event.edge not in edges:
                raise ValueError("topology edge is already disabled")
            edges.remove(event.edge)
    return TopologySnapshot.build(
        active_peer_ids=tuple(sorted(peers)), enabled_edges=tuple(sorted(edges))
    )


def eligible_peer_ids(
    session: CollaborationSession,
    topology: TopologySnapshot,
    contributions: tuple[PeerContribution, ...],
) -> tuple[str, ...]:
    counts = Counter(item.peer_id for item in contributions)
    last_peer_id = contributions[-1].peer_id if contributions else None
    return _eligible_peer_ids_from_summary(session, topology, counts, last_peer_id)


def _eligible_peer_ids_from_summary(
    session: CollaborationSession,
    topology: TopologySnapshot,
    counts: Counter[str],
    last_peer_id: str | None,
) -> tuple[str, ...]:
    active = set(topology.active_peer_ids)
    candidates = {
        peer.actor_id
        for peer in session.peers
        if peer.actor_id in active
        and counts[peer.actor_id] < session.budget.max_contributions_per_peer
    }
    if last_peer_id is not None:
        targets = {
            target
            for source, target in topology.enabled_edges
            if source == last_peer_id
        }
        candidates &= targets
    return tuple(sorted(candidates))


def _termination_reason_from_summary(
    *,
    session: CollaborationSession,
    topology: TopologySnapshot,
    topology_event_count: int,
    contribution_count: int,
    peer_contribution_counts: Counter[str],
    last_peer_id: str | None,
    observed_cycle_projection_counts: Counter[str],
    topology_churn_count: int,
    completed: bool,
) -> CollaborationTerminationReason | None:
    budget = session.budget
    if completed:
        return CollaborationTerminationReason.COMPLETED
    if contribution_count >= budget.max_hops:
        return CollaborationTerminationReason.MAX_HOPS_REACHED
    if contribution_count >= budget.max_contributions:
        return CollaborationTerminationReason.MAX_CONTRIBUTIONS_REACHED
    if any(
        count >= budget.max_contributions_per_peer
        for count in peer_contribution_counts.values()
    ):
        return CollaborationTerminationReason.PER_PEER_LIMIT_REACHED
    if topology_event_count and topology_event_count >= budget.max_topology_changes:
        return CollaborationTerminationReason.TOPOLOGY_CHANGE_LIMIT_REACHED
    if any(
        count - 1 > budget.max_state_repetitions
        for count in observed_cycle_projection_counts.values()
    ):
        return CollaborationTerminationReason.REPEATED_STATE_LOOP
    if topology_churn_count >= budget.max_topology_churn:
        return CollaborationTerminationReason.TOPOLOGY_CHURN
    total = Decimal(contribution_count)
    share_bound = Decimal(str(budget.max_peer_contribution_share))
    if contribution_count and any(
        Decimal(count) / total > share_bound
        for count in peer_contribution_counts.values()
    ):
        return CollaborationTerminationReason.CONTRIBUTION_MONOPOLY
    if not _eligible_peer_ids_from_summary(
        session,
        topology,
        peer_contribution_counts,
        last_peer_id,
    ):
        return CollaborationTerminationReason.NO_ELIGIBLE_PEER
    return None


def collaboration_termination_reason(
    *,
    session: CollaborationSession,
    topology: TopologySnapshot,
    topology_history: tuple[TopologySnapshot, ...],
    topology_events: tuple[TopologyEvent, ...],
    contributions: tuple[PeerContribution, ...],
    cycle_projection_hashes: tuple[str, ...],
    completed: bool,
) -> CollaborationTerminationReason | None:
    counts = Counter(item.peer_id for item in contributions)
    topology_hashes = tuple(item.content_hash for item in topology_history)
    churn_count = sum(
        topology_hashes[index] == topology_hashes[index - 2]
        for index in range(2, len(topology_hashes))
    )
    return _termination_reason_from_summary(
        session=session,
        topology=topology,
        topology_event_count=len(topology_events),
        contribution_count=len(contributions),
        peer_contribution_counts=counts,
        last_peer_id=contributions[-1].peer_id if contributions else None,
        observed_cycle_projection_counts=Counter(cycle_projection_hashes),
        topology_churn_count=churn_count,
        completed=completed,
    )


__all__ = [
    "CollaborationBudget",
    "CollaborationCompletionPredicate",
    "CollaborationSession",
    "CollaborationState",
    "CollaborationTermination",
    "CollaborationTerminationReason",
    "CollaborationTransition",
    "CollaborationTransitionKind",
    "PeerContribution",
    "PeerRequest",
    "PeerRoleAssignment",
    "TopologyEvent",
    "TopologyOperation",
    "TopologySnapshot",
]
