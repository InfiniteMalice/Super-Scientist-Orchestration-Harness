from __future__ import annotations

from super_scientist.domain.cognition.models import (
    CapabilityProfile,
    CohortPlan,
    DiversityAssessment,
    DiversityAxes,
    DiversityAxisStatus,
    DiversityFingerprint,
    ErrorCorrelationRecord,
)


def _axis_status(
    fingerprints: tuple[DiversityFingerprint, ...],
    axis: str,
) -> DiversityAxisStatus:
    values = tuple(getattr(fingerprint, axis) for fingerprint in fingerprints)
    if not values or any(value is None for value in values):
        return DiversityAxisStatus.UNKNOWN
    return DiversityAxisStatus.SAME if len(set(values)) == 1 else DiversityAxisStatus.DIFFERENT


def assess_diversity(
    cohort: CohortPlan,
    profiles: tuple[CapabilityProfile, ...],
    error_correlations: tuple[ErrorCorrelationRecord, ...],
) -> DiversityAssessment:
    cohort_actor_ids = tuple(sorted(member.actor_id for member in cohort.members))
    profile_actor_ids = tuple(sorted(profile.actor_id for profile in profiles))
    if profile_actor_ids != cohort_actor_ids:
        raise ValueError("profiles must exactly match cohort membership")
    if any(profile.governing_policy_hash != cohort.governing_policy_hash for profile in profiles):
        raise ValueError("profiles and cohort must share governing policy")
    cohort_actor_set = set(cohort_actor_ids)
    if any(
        record.left_actor_id not in cohort_actor_set
        or record.right_actor_id not in cohort_actor_set
        for record in error_correlations
    ):
        raise ValueError("error correlations must reference only cohort members")
    correlations = tuple(sorted(error_correlations, key=lambda record: record.correlation_id))
    fingerprints = tuple(
        profile.diversity_fingerprint
        for profile in sorted(profiles, key=lambda item: item.actor_id)
    )
    axes = DiversityAxes(
        model_family=_axis_status(fingerprints, "model_family"),
        model_version=_axis_status(fingerprints, "model_version"),
        scale_class=_axis_status(fingerprints, "scale_class"),
        provider=_axis_status(fingerprints, "provider"),
        adapter_hash=_axis_status(fingerprints, "adapter_hash"),
        configuration_hash=_axis_status(fingerprints, "configuration_hash"),
        prompt_strategy=_axis_status(fingerprints, "prompt_strategy"),
        methodological_prior=_axis_status(fingerprints, "methodological_prior"),
        tools=_axis_status(fingerprints, "tools"),
        evidence_partitions=_axis_status(fingerprints, "evidence_partitions"),
        modalities=_axis_status(fingerprints, "modalities"),
        previous_error_clusters=_axis_status(fingerprints, "previous_error_clusters"),
        prior_task_specializations=_axis_status(
            fingerprints,
            "prior_task_specializations",
        ),
        assigned_role=_axis_status(fingerprints, "assigned_role"),
        procedure_family=_axis_status(fingerprints, "procedure_family"),
    )
    return DiversityAssessment.build(
        diversity_assessment_id=f"{cohort.cohort_plan_id}:diversity",
        cohort_plan_id=cohort.cohort_plan_id,
        member_actor_ids=cohort_actor_ids,
        axes=axes,
        error_correlations=correlations,
        governing_policy_hash=cohort.governing_policy_hash,
    )


__all__ = ["assess_diversity"]
