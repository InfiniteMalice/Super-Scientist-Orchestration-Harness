from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from super_scientist.domain.cognition import (
    CapabilityAssertion,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityRequirement,
    CohortRequest,
    DiversityFingerprint,
    build_cohort,
)
from super_scientist.domain.collaboration import (
    CollaborationBudget,
    CollaborationCompletionPredicate,
    CollaborationSession,
    PeerRoleAssignment,
)
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.models import ResourceBudget, ResourceUsage

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "f" * 64
SNAPSHOT_HASH = "e" * 64


def zero_usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.0,
        compute_units=0.0,
        tokens=0,
        elapsed_seconds=0.0,
        tool_calls=0,
        human_interventions=0,
    )


def unit_usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=1.0,
        compute_units=1.0,
        tokens=10,
        elapsed_seconds=1.0,
        tool_calls=1,
        human_interventions=0,
    )


def artifact(name: str = "input.txt", digest: str = "a" * 64) -> ArtifactRef:
    return ArtifactRef(sha256=digest, size_bytes=4, media_type="text/plain", relative_path=name)


def actor(actor_id: str, kind: ActorKind = ActorKind.MODEL) -> ActorIdentity:
    if kind is ActorKind.MODEL:
        return ActorIdentity(
            actor_id=actor_id,
            kind=kind,
            created_at=NOW,
            provider_id=f"provider-{actor_id}",
            model_id=f"model-{actor_id}",
            adapter_id=f"adapter-{actor_id}",
            configuration_hash=(actor_id[-1] * 64 if actor_id[-1] in "abcdef" else "b" * 64),
        )
    return ActorIdentity(actor_id=actor_id, kind=kind, created_at=NOW)


def profile(actor_id: str) -> CapabilityProfile:
    identity = actor(actor_id)
    assertion = CapabilityAssertion(
        assertion_id=f"assertion-{actor_id}",
        capability_id="analysis",
        task_family_id="research",
        status=CapabilityEvidenceStatus.VERIFIED,
        evidence_ids=(f"evidence-{actor_id}",),
        validator_id="validator",
        validator_version="v1",
        evidence_snapshot_hash=SNAPSHOT_HASH,
    )
    fingerprint = DiversityFingerprint(
        fingerprint_id=f"fingerprint-{actor_id}",
        model_family=f"family-{actor_id}",
        model_version="v1",
        scale_class="large",
        provider=identity.provider_id,
        adapter_hash="c" * 64,
        configuration_hash=identity.configuration_hash,
        prompt_strategy="direct",
        methodological_prior="deductive",
        tools=("tool-a",),
        evidence_partitions=("public",),
        modalities=("text",),
        previous_error_clusters=(),
        prior_task_specializations=("research",),
    )
    return CapabilityProfile.build(
        profile_id=f"profile-{actor_id}",
        actor=identity,
        diversity_fingerprint=fingerprint,
        allowed_tools=("tool-a",),
        assertions=(assertion,),
        governing_policy_hash=POLICY_HASH,
    )


@pytest.fixture
def session_factory() -> Callable[..., CollaborationSession]:
    def make(
        *peer_ids: str,
        max_hops: int = 8,
        max_contributions: int = 8,
        max_per_peer: int = 4,
        max_topology_changes: int = 4,
        max_topology_churn: int = 3,
        max_parent_depth: int = 3,
        max_state_repetitions: int = 1,
        max_share: float = 1.0,
        completion_count: int = 8,
        resource_cost_usd: float = 100.0,
    ) -> CollaborationSession:
        canonical_ids = tuple(sorted(peer_ids or ("peer-a", "peer-b")))
        requirement = CapabilityRequirement(
            requirement_id="requirement-analysis",
            capability_id="analysis",
            task_family_id="research",
            evidence_snapshot_hash=SNAPSHOT_HASH,
        )
        request = CohortRequest.build(
            request_id="cohort-request",
            task_id="task-a",
            required_capabilities=(requirement,),
            preferred_capabilities=(),
            min_members=len(canonical_ids),
            max_members=len(canonical_ids),
            candidate_actor_ids=canonical_ids,
            prohibited_combinations=(),
            governing_policy_hash=POLICY_HASH,
        )
        profiles = tuple(profile(peer_id) for peer_id in canonical_ids)
        plan = build_cohort(request, profiles)
        edges = tuple(
            (source, target)
            for source in canonical_ids
            for target in canonical_ids
            if source != target
        )
        budget = CollaborationBudget(
            max_peers=len(canonical_ids),
            max_hops=max_hops,
            max_contributions=max_contributions,
            max_contributions_per_peer=max_per_peer,
            max_topology_changes=max_topology_changes,
            max_parent_depth=max_parent_depth,
            max_state_repetitions=max_state_repetitions,
            max_topology_churn=max_topology_churn,
            max_peer_contribution_share=max_share,
            resources=ResourceBudget(
                cost_usd=resource_cost_usd,
                compute_units=100.0,
                tokens=1000,
                elapsed_seconds=100.0,
                tool_calls=100,
                human_interventions=0,
            ),
            allowed_tool_ids=("tool-a",),
        )
        return CollaborationSession.build(
            session_id="session-a",
            task_id="task-a",
            cohort_plan=plan,
            peers=tuple(sorted((item.actor for item in profiles), key=lambda item: item.actor_id)),
            role_assignments=tuple(
                PeerRoleAssignment(peer_id=peer_id, role_id="analyst") for peer_id in canonical_ids
            ),
            tools=(actor("tool-a", ActorKind.TOOL),),
            allowed_artifacts=(artifact(),),
            budget=budget,
            allowed_contribution_kinds=("analysis",),
            declared_edges=edges,
            initial_active_peer_ids=canonical_ids,
            scheduling_policy_version="lexicographic-v1",
            topology_policy_version="declared-edge-v1",
            completion_predicate=CollaborationCompletionPredicate(
                min_contributions=completion_count, required_contribution_kind="analysis"
            ),
            governing_policy_hash=POLICY_HASH,
        )

    return make
