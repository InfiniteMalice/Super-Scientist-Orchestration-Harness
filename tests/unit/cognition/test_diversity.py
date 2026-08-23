from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.cognition import (
    CapabilityProfile,
    CohortRequest,
    DiversityAssessment,
    DiversityAxisStatus,
    DiversityFingerprint,
    ErrorCorrelationRecord,
    ErrorCorrelationStatus,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY = "f" * 64


def _profile(
    actor_id: str,
    *,
    prompt_strategy: str | None,
    provider: str | None = "provider",
    tools: tuple[str, ...] | None = ("search",),
) -> CapabilityProfile:
    actor = ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL,
        created_at=NOW,
        provider_id="provider",
        model_id="same-model",
        adapter_id="same-adapter",
        configuration_hash="a" * 64,
    )
    fingerprint = DiversityFingerprint(
        fingerprint_id=f"fingerprint-{actor_id}",
        model_family="same-family",
        model_version="same-version",
        scale_class="large",
        provider=provider,
        adapter_hash="b" * 64,
        configuration_hash="a" * 64,
        prompt_strategy=prompt_strategy,
        methodological_prior="deductive",
        tools=tools,
        evidence_partitions=("public",),
        modalities=("text",),
        previous_error_clusters=("cluster-a",),
        prior_task_specializations=("analysis",),
        assigned_role="reviewer",
        procedure_family="deductive",
    )
    return CapabilityProfile.build(
        profile_id=f"profile-{actor_id}",
        actor=actor,
        diversity_fingerprint=fingerprint,
        governing_policy_hash=POLICY,
    )


def _cohort(*profiles: CapabilityProfile):
    request = CohortRequest(
        request_id="request-a",
        task_id="task-a",
        required_capabilities=(),
        preferred_capabilities=(),
        min_members=len(profiles),
        max_members=max(1, len(profiles)),
        candidate_actor_ids=tuple(sorted(profile.actor_id for profile in profiles)),
        prohibited_combinations=(),
        governing_policy_hash=POLICY,
    )
    return build_cohort(request, profiles)


def test_same_model_different_prompts_are_diverse_but_not_independent() -> None:
    left = _profile("peer-a", prompt_strategy="critique-first")
    right = _profile("peer-b", prompt_strategy="direct")

    diversity = assess_diversity(_cohort(left, right), (left, right), ())

    assert diversity.axes["prompt_strategy"] is DiversityAxisStatus.DIFFERENT
    assert diversity.axes["model_family"] is DiversityAxisStatus.SAME
    assert are_independent(left.actor, right.actor) is False
    assert "is_independent" not in DiversityAssessment.model_fields
    assert "authority" not in DiversityAssessment.model_fields


def test_unknown_fingerprint_value_remains_unknown() -> None:
    left = _profile("peer-a", prompt_strategy=None)
    right = _profile("peer-b", prompt_strategy="direct")

    diversity = assess_diversity(_cohort(left, right), (left, right), ())

    assert diversity.axes["prompt_strategy"] is DiversityAxisStatus.UNKNOWN


def test_known_empty_and_nonempty_tool_surfaces_are_different() -> None:
    left = _profile("peer-a", prompt_strategy="direct", tools=())
    right = _profile("peer-b", prompt_strategy="direct", tools=("search",))

    diversity = assess_diversity(_cohort(left, right), (left, right), ())

    assert diversity.axes["tools"] is DiversityAxisStatus.DIFFERENT


def test_unknown_collection_is_not_treated_as_an_empty_collection() -> None:
    left = _profile("peer-a", prompt_strategy="direct", tools=None)
    right = _profile("peer-b", prompt_strategy="direct", tools=())

    diversity = assess_diversity(_cohort(left, right), (left, right), ())

    assert diversity.axes["tools"] is DiversityAxisStatus.UNKNOWN


def test_empty_cohort_has_only_unknown_axes() -> None:
    diversity = assess_diversity(_cohort(), (), ())

    assert set(diversity.axes.as_mapping().values()) == {DiversityAxisStatus.UNKNOWN}


def test_known_error_correlation_requires_a_real_coefficient() -> None:
    with pytest.raises(ValidationError, match="KNOWN"):
        ErrorCorrelationRecord(
            correlation_id="correlation-a",
            left_actor_id="peer-a",
            right_actor_id="peer-b",
            evaluation_set_id="evaluation-a",
            sample_count=10,
            method="pearson",
            status=ErrorCorrelationStatus.KNOWN,
            value=None,
            governing_policy_hash=POLICY,
        )


def test_known_error_correlation_requires_observed_samples() -> None:
    with pytest.raises(ValidationError, match="sample"):
        ErrorCorrelationRecord(
            correlation_id="correlation-a",
            left_actor_id="peer-a",
            right_actor_id="peer-b",
            evaluation_set_id="evaluation-a",
            sample_count=0,
            method="pearson",
            status=ErrorCorrelationStatus.KNOWN,
            value=0.0,
            governing_policy_hash=POLICY,
        )


def test_insufficient_error_data_forbids_an_invented_coefficient() -> None:
    with pytest.raises(ValidationError, match="must not store"):
        ErrorCorrelationRecord(
            correlation_id="correlation-a",
            left_actor_id="peer-a",
            right_actor_id="peer-b",
            evaluation_set_id="evaluation-a",
            sample_count=1,
            method="pearson",
            status=ErrorCorrelationStatus.INSUFFICIENT_DATA,
            value=0.0,
            governing_policy_hash=POLICY,
        )


def test_diversity_rejects_profiles_outside_exact_cohort_membership() -> None:
    left = _profile("peer-a", prompt_strategy="direct")
    outsider = _profile("peer-z", prompt_strategy="other")

    with pytest.raises(ValueError, match="exactly match cohort membership"):
        assess_diversity(_cohort(left), (left, outsider), ())
