from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.cognition import (
    CapabilityAssertion,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityRequirement,
    CohortRequest,
    DiversityFingerprint,
    build_cohort,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
SNAPSHOT = "a" * 64
POLICY = "f" * 64


def _requirement(requirement_id: str = "requirement-a") -> CapabilityRequirement:
    return CapabilityRequirement(
        requirement_id=requirement_id,
        capability_id="analysis",
        task_family_id="research",
        evidence_snapshot_hash=SNAPSHOT,
    )


def _profile(
    actor_id: str,
    *,
    status: CapabilityEvidenceStatus = CapabilityEvidenceStatus.VERIFIED,
) -> CapabilityProfile:
    actor = ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL,
        created_at=NOW,
        provider_id=f"provider-{actor_id}",
        model_id=f"model-{actor_id}",
        adapter_id=f"adapter-{actor_id}",
        configuration_hash=(actor_id[-1] * 64 if actor_id[-1] in "abcdef" else "b" * 64),
    )
    verified = status is CapabilityEvidenceStatus.VERIFIED
    assertion = CapabilityAssertion(
        assertion_id=f"assertion-{actor_id}",
        capability_id="analysis",
        task_family_id="research",
        status=status,
        evidence_ids=(
            (f"evidence-{actor_id}",)
            if status is not CapabilityEvidenceStatus.UNKNOWN
            else ()
        ),
        validator_id="validator" if verified else None,
        validator_version="v1" if verified else None,
        evidence_snapshot_hash=SNAPSHOT,
    )
    fingerprint = DiversityFingerprint(
        fingerprint_id=f"fingerprint-{actor_id}",
        model_family=f"family-{actor_id}",
        model_version="v1",
        scale_class="large",
        provider=f"provider-{actor_id}",
        adapter_hash="c" * 64,
        configuration_hash=actor.configuration_hash,
        prompt_strategy="direct",
        methodological_prior="deductive",
        tools=(),
        evidence_partitions=("public",),
        modalities=("text",),
        previous_error_clusters=(),
        prior_task_specializations=("research",),
    )
    return CapabilityProfile.build(
        profile_id=f"profile-{actor_id}",
        actor=actor,
        diversity_fingerprint=fingerprint,
        assertions=(assertion,),
        governing_policy_hash=POLICY,
    )


def _request(
    *,
    min_members: int = 1,
    max_members: int = 1,
    candidates: tuple[str, ...] = (),
    prohibited: tuple[tuple[str, str], ...] = (),
    required: tuple[CapabilityRequirement, ...] | None = None,
) -> CohortRequest:
    return CohortRequest(
        request_id="cohort-request-a",
        task_id="task-a",
        required_capabilities=(_requirement(),) if required is None else required,
        preferred_capabilities=(),
        min_members=min_members,
        max_members=max_members,
        candidate_actor_ids=candidates,
        prohibited_combinations=prohibited,
        governing_policy_hash=POLICY,
    )


def test_cohort_tie_is_recorded_then_broken_by_actor_id() -> None:
    plan = build_cohort(_request(max_members=1), (_profile("peer-b"), _profile("peer-a")))

    assert plan.tie_sets == (("peer-a", "peer-b"),)
    assert tuple(member.actor_id for member in plan.members) == ("peer-a",)


def test_all_complete_score_ties_are_retained_in_rank_order() -> None:
    profiles = (
        _profile("peer-d", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-b"),
        _profile("peer-c", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-a"),
    )

    plan = build_cohort(_request(max_members=2), profiles)

    assert plan.tie_sets == (("peer-a", "peer-b"), ("peer-c", "peer-d"))
    assert tuple(member.actor_id for member in plan.members) == ("peer-a", "peer-b")


def test_fixed_candidates_and_prohibited_combinations_are_honored_purely() -> None:
    plan = build_cohort(
        _request(
            min_members=2,
            max_members=2,
            candidates=("peer-a", "peer-b", "peer-c"),
            prohibited=(("peer-a", "peer-b"),),
        ),
        (_profile("peer-c"), _profile("peer-b"), _profile("peer-a"), _profile("peer-d")),
    )

    assert tuple(member.actor_id for member in plan.members) == ("peer-a", "peer-c")
    assert plan.excluded_actor_ids == ("peer-b",)
    assert plan.minimum_size_met is True


def test_empty_profiles_produce_explicit_unresolved_gaps() -> None:
    plan = build_cohort(
        _request(min_members=1, max_members=2, candidates=("peer-a",)),
        (),
    )

    assert plan.members == ()
    assert plan.unresolved_requirement_ids == ("requirement-a",)
    assert plan.unresolved_candidate_actor_ids == ("peer-a",)
    assert plan.minimum_size_met is False


def test_duplicate_actor_profiles_are_rejected_instead_of_implicitly_deduplicated() -> None:
    with pytest.raises(ValueError, match="actor IDs must be unique"):
        build_cohort(_request(), (_profile("peer-a"), _profile("peer-a")))


def test_cohort_request_rejects_malformed_bounds_and_noncanonical_pairs() -> None:
    with pytest.raises(ValidationError, match="min_members"):
        _request(min_members=2, max_members=1)
    with pytest.raises(ValidationError, match="canonical"):
        _request(prohibited=(("peer-b", "peer-a"),))


def test_cohort_plan_content_hash_changes_with_selection() -> None:
    left = build_cohort(_request(), (_profile("peer-a"),))
    right = build_cohort(_request(), (_profile("peer-b"),))

    assert left.content_hash != right.content_hash
