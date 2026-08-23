from __future__ import annotations

import json
import math
from collections import Counter
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

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

MAX_COLLABORATION_ITEMS = 10_000
MAX_PEERS = 64
MAX_IDENTIFIER_LENGTH = 200
MAX_PUBLIC_TEXT_LENGTH = 8_000
MAX_CANDIDATE_CONTENT_LENGTH = 64_000
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


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


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


def sum_usage(usages: tuple[ResourceUsage, ...]) -> ResourceUsage:
    values: dict[str, float | int] = {}
    for field_name in RESOURCE_FIELDS:
        values[field_name] = sum(getattr(usage, field_name) for usage in usages)
    return ResourceUsage.model_validate(values)


def usage_matches(
    left: ResourceBudget | ResourceUsage,
    right: ResourceBudget | ResourceUsage,
) -> bool:
    return (
        left.tokens == right.tokens
        and left.tool_calls == right.tool_calls
        and left.human_interventions == right.human_interventions
        and all(
            math.isclose(
                getattr(left, field_name),
                getattr(right, field_name),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for field_name in ("cost_usd", "compute_units", "elapsed_seconds")
        )
    )


def remaining_resources(budget: ResourceBudget, usage: ResourceUsage) -> ResourceBudget:
    if any(getattr(usage, name) > getattr(budget, name) for name in RESOURCE_FIELDS):
        raise ValueError("usage exceeds collaboration resource budget")
    values: dict[str, float | int] = {
        name: getattr(budget, name) - getattr(usage, name) for name in RESOURCE_FIELDS
    }
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
    max_topology_changes: int = Field(strict=True, ge=0, le=MAX_COLLABORATION_ITEMS)
    max_parent_depth: int = Field(strict=True, ge=0, le=MAX_PEERS)
    max_state_repetitions: int = Field(strict=True, ge=1, le=MAX_PEERS)
    max_topology_churn: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
    max_peer_contribution_share: float = Field(strict=True, gt=0.0, le=1.0, allow_inf_nan=False)
    resources: ResourceBudget
    allowed_tool_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)

    @field_validator("allowed_tool_ids")
    @classmethod
    def require_canonical_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_unique(values, "allowed_tool_ids")


class _TopologySnapshotPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    active_peer_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    enabled_edges: tuple[tuple[BoundedIdentifier, BoundedIdentifier], ...] = Field(
        max_length=MAX_PEERS * MAX_PEERS
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
    sequence: int = Field(strict=True, ge=1, le=MAX_COLLABORATION_ITEMS)
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
    peers: tuple[ActorIdentity, ...] = Field(min_length=1, max_length=MAX_PEERS)
    role_assignments: tuple[PeerRoleAssignment, ...] = Field(min_length=1, max_length=MAX_PEERS)
    tools: tuple[ActorIdentity, ...] = Field(max_length=MAX_PEERS)
    allowed_artifacts: tuple[ArtifactRef, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    budget: CollaborationBudget
    allowed_contribution_kinds: tuple[BoundedIdentifier, ...] = Field(
        min_length=1, max_length=MAX_PEERS
    )
    declared_edges: tuple[tuple[BoundedIdentifier, BoundedIdentifier], ...] = Field(
        max_length=MAX_PEERS * MAX_PEERS
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


def _require_canonical_artifacts(artifacts: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
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
    artifact_refs: tuple[ArtifactRef, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    parent_contribution_id: BoundedIdentifier | None
    tool_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    remaining_budget: ResourceBudget

    @field_validator("artifact_refs")
    @classmethod
    def require_artifacts(cls, values: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
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


def _canonical_candidate_json(value: object) -> object:
    if not isinstance(value, str) or len(value) > MAX_CANDIDATE_CONTENT_LENGTH:
        raise ValueError("candidate_content must be a bounded canonical JSON object")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("candidate_content must be a bounded canonical JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError("candidate_content must be a bounded canonical JSON object")
    try:
        canonical = canonical_json_bytes(parsed).decode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate_content must be a bounded canonical JSON object") from exc
    if canonical != value:
        raise ValueError("candidate_content must be a canonical JSON object")
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
    artifact_refs: tuple[ArtifactRef, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    tool_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_PEERS)
    authority: Literal["EVIDENCE_ONLY"] = "EVIDENCE_ONLY"

    @field_validator("parent_contribution_ids", "tool_ids")
    @classmethod
    def require_canonical_ids(cls, values: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _canonical_unique(values, info.field_name)

    @field_validator("artifact_refs")
    @classmethod
    def require_artifacts(cls, values: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
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
        min_length=1, max_length=MAX_COLLABORATION_ITEMS + 1
    )
    topology_events: tuple[TopologyEvent, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    requests: tuple[PeerRequest, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    contributions: tuple[PeerContribution, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    usage_history: tuple[ResourceUsage, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
    usage: ResourceUsage
    hop_count: int = Field(strict=True, ge=0, le=MAX_COLLABORATION_ITEMS)
    scheduling_position: int = Field(strict=True, ge=0, le=MAX_COLLABORATION_ITEMS * 2)
    transitions: tuple[CollaborationTransition, ...] = Field(
        max_length=MAX_COLLABORATION_ITEMS * 2
    )
    observed_state_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_COLLABORATION_ITEMS)
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
        remaining = session.budget.resources
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
            if not usage_matches(request.remaining_budget, remaining):
                raise ValueError("request remaining budget must match state usage")
            known_ids.add(contribution.contribution_id)
            request_ids.add(request.request_id)
            known_depths[contribution.contribution_id] = parent_depth
            remaining = remaining_resources(remaining, transition_usage)
        completed = _completion_satisfied(session, self.contributions)
        if self.completed != completed:
            raise ValueError("completed flag must exactly match the session completion predicate")
        self._require_deterministic_transition_replay()
        return self

    def _require_deterministic_transition_replay(self) -> None:
        topology_index = 0
        peer_index = 0
        last_peer_id: str | None = None
        topology = self.topology_history[0]
        for position, transition in enumerate(self.transitions, start=1):
            if transition.position != position:
                raise ValueError("transition positions must be consecutive")
            reason = collaboration_termination_reason(
                session=self.session,
                topology=topology,
                topology_history=self.topology_history[: topology_index + 1],
                topology_events=self.topology_events[:topology_index],
                contributions=self.contributions[:peer_index],
                observed_state_hashes=self.observed_state_hashes[: position - 1],
                completed=_completion_satisfied(
                    self.session, self.contributions[:peer_index]
                ),
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
                topology = self.topology_history[topology_index]
                continue
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
            eligible = eligible_peer_ids(
                self.session, topology, self.contributions[:peer_index]
            )
            expected_peer = eligible[0] if eligible else None
            if request.recipient_id != expected_peer:
                raise ValueError("request recipient must be the expected peer")
            last_peer_id = contribution.peer_id
            peer_index += 1
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
    active = set(topology.active_peer_ids)
    candidates = {
        peer.actor_id
        for peer in session.peers
        if peer.actor_id in active
        and counts[peer.actor_id] < session.budget.max_contributions_per_peer
    }
    if contributions:
        sender = contributions[-1].peer_id
        targets = {
            target for source, target in topology.enabled_edges if source == sender
        }
        candidates &= targets
    return tuple(sorted(candidates))


def collaboration_termination_reason(
    *,
    session: CollaborationSession,
    topology: TopologySnapshot,
    topology_history: tuple[TopologySnapshot, ...],
    topology_events: tuple[TopologyEvent, ...],
    contributions: tuple[PeerContribution, ...],
    observed_state_hashes: tuple[str, ...],
    completed: bool,
) -> CollaborationTerminationReason | None:
    budget = session.budget
    counts = Counter(item.peer_id for item in contributions)
    if completed:
        return CollaborationTerminationReason.COMPLETED
    if len(contributions) >= budget.max_hops:
        return CollaborationTerminationReason.MAX_HOPS_REACHED
    if len(contributions) >= budget.max_contributions:
        return CollaborationTerminationReason.MAX_CONTRIBUTIONS_REACHED
    if any(count >= budget.max_contributions_per_peer for count in counts.values()):
        return CollaborationTerminationReason.PER_PEER_LIMIT_REACHED
    if topology_events and len(topology_events) >= budget.max_topology_changes:
        return CollaborationTerminationReason.TOPOLOGY_CHANGE_LIMIT_REACHED
    if any(
        count > budget.max_state_repetitions
        for count in Counter(observed_state_hashes).values()
    ):
        return CollaborationTerminationReason.REPEATED_STATE_LOOP
    topology_hashes = tuple(item.content_hash for item in topology_history)
    churn_count = sum(
        topology_hashes[index] == topology_hashes[index - 2]
        for index in range(2, len(topology_hashes))
    )
    if churn_count >= budget.max_topology_churn:
        return CollaborationTerminationReason.TOPOLOGY_CHURN
    if len(contributions) >= 2 and any(
        count / len(contributions) > budget.max_peer_contribution_share
        for count in counts.values()
    ):
        return CollaborationTerminationReason.CONTRIBUTION_MONOPOLY
    if not eligible_peer_ids(session, topology, contributions):
        return CollaborationTerminationReason.NO_ELIGIBLE_PEER
    return None


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
