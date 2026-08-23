from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from super_scientist.domain.cognition.models import (
    CapabilityAssessment,
    CapabilityCoverage,
    CapabilityDisposition,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityRequirement,
    CohortMember,
    CohortPlan,
    CohortRankedCandidate,
    CohortRequest,
    CohortTieRank,
)


@dataclass(frozen=True)
class _CohortDerivation:
    resolved_candidate_profiles: tuple[CapabilityProfile, ...]
    members: tuple[CohortMember, ...]
    excluded_actor_ids: tuple[str, ...]
    coverage: tuple[CapabilityCoverage, ...]
    unresolved_requirement_ids: tuple[str, ...]
    unresolved_candidate_actor_ids: tuple[str, ...]
    ranked_candidates: tuple[CohortRankedCandidate, ...]
    tie_sets: tuple[tuple[str, ...], ...]
    tie_group_ranks: tuple[CohortTieRank, ...]
    evidence_snapshot_hashes: tuple[str, ...]
    profile_content_hashes: tuple[str, ...]
    minimum_size_met: bool


def assess_capability(
    profile: CapabilityProfile,
    requirement: CapabilityRequirement,
) -> CapabilityAssessment:
    matching = tuple(
        item
        for item in profile.assertions
        if item.capability_id == requirement.capability_id
        and item.task_family_id == requirement.task_family_id
    )
    verified = tuple(
        item
        for item in matching
        if item.status is CapabilityEvidenceStatus.VERIFIED
        and item.evidence_snapshot_hash == requirement.evidence_snapshot_hash
    )
    return CapabilityAssessment.from_matches(profile, requirement, matching, verified)


def _derive_cohort(
    request: CohortRequest,
    profiles: tuple[CapabilityProfile, ...],
) -> _CohortDerivation:
    candidate_ids = request.candidate_actor_ids
    candidate_id_set = set(candidate_ids)
    declared_profiles = tuple(
        profile for profile in profiles if profile.actor_id in candidate_id_set
    )
    actor_ids = tuple(profile.actor_id for profile in declared_profiles)
    if len(set(actor_ids)) != len(actor_ids):
        raise ValueError("capability profile actor IDs must be unique")
    if any(
        profile.governing_policy_hash != request.governing_policy_hash
        for profile in declared_profiles
    ):
        raise ValueError("capability profiles and cohort request must share governing policy")

    by_actor = {profile.actor_id: profile for profile in declared_profiles}
    candidates = tuple(by_actor[actor_id] for actor_id in candidate_ids if actor_id in by_actor)
    missing_candidates = tuple(actor_id for actor_id in candidate_ids if actor_id not in by_actor)
    requirements = request.required_capabilities + request.preferred_capabilities

    ranked: list[tuple[tuple[int, int], CapabilityProfile, tuple[CapabilityAssessment, ...]]] = []
    for profile in candidates:
        assessments = tuple(assess_capability(profile, requirement) for requirement in requirements)
        required_count = sum(
            assessment.disposition is CapabilityDisposition.SATISFIED
            for assessment in assessments[: len(request.required_capabilities)]
        )
        preferred_count = sum(
            assessment.disposition is CapabilityDisposition.SATISFIED
            for assessment in assessments[len(request.required_capabilities) :]
        )
        ranked.append(((required_count, preferred_count), profile, assessments))
    ranked.sort(key=lambda item: (-item[0][0], -item[0][1], item[1].actor_id))

    score_groups: dict[tuple[int, int], list[str]] = defaultdict(list)
    score_order: list[tuple[int, int]] = []
    for score, profile, _assessments in ranked:
        if score not in score_groups:
            score_order.append(score)
        score_groups[score].append(profile.actor_id)
    tie_sets = tuple(
        tuple(sorted(score_groups[score]))
        for score in score_order
        if len(score_groups[score]) > 1
    )
    tie_group_ranks = tuple(
        CohortTieRank(
            required_satisfied=score[0],
            preferred_satisfied=score[1],
        )
        for score in score_order
        if len(score_groups[score]) > 1
    )
    ranked_candidates = tuple(
        CohortRankedCandidate(
            actor_id=profile.actor_id,
            profile_id=profile.profile_id,
            profile_content_hash=profile.content_hash,
            required_satisfied=score[0],
            preferred_satisfied=score[1],
            assessments=assessments,
        )
        for score, profile, assessments in ranked
    )

    prohibited = {frozenset(pair) for pair in request.prohibited_combinations}
    selected: list[tuple[tuple[int, int], CapabilityProfile, tuple[CapabilityAssessment, ...]]] = []
    for item in ranked:
        if len(selected) >= request.max_members:
            break
        _score, profile, _assessments = item
        if any(
            frozenset((profile.actor_id, selected_profile.actor_id)) in prohibited
            for _selected_score, selected_profile, _selected_assessments in selected
        ):
            continue
        selected.append(item)

    members = tuple(
        CohortMember(
            actor_id=profile.actor_id,
            profile_id=profile.profile_id,
            profile_content_hash=profile.content_hash,
            required_satisfied=score[0],
            preferred_satisfied=score[1],
            assessments=assessments,
        )
        for score, profile, assessments in selected
    )
    selected_actor_ids = {member.actor_id for member in members}
    excluded_actor_ids = tuple(
        sorted(
            profile.actor_id
            for profile in candidates
            if profile.actor_id not in selected_actor_ids
        )
    )

    coverage = tuple(
        CapabilityCoverage(
            requirement=requirement,
            satisfying_actor_ids=tuple(
                sorted(
                    member.actor_id
                    for member in members
                    if any(
                        assessment.requirement.requirement_id == requirement.requirement_id
                        and assessment.disposition is CapabilityDisposition.SATISFIED
                        for assessment in member.assessments
                    )
                )
            ),
        )
        for requirement in request.required_capabilities
    )
    unresolved = tuple(
        item.requirement.requirement_id for item in coverage if not item.satisfying_actor_ids
    )
    return _CohortDerivation(
        resolved_candidate_profiles=candidates,
        members=members,
        excluded_actor_ids=excluded_actor_ids,
        coverage=coverage,
        unresolved_requirement_ids=unresolved,
        unresolved_candidate_actor_ids=missing_candidates,
        ranked_candidates=ranked_candidates,
        tie_sets=tie_sets,
        tie_group_ranks=tie_group_ranks,
        evidence_snapshot_hashes=tuple(
            sorted({item.evidence_snapshot_hash for item in requirements})
        ),
        profile_content_hashes=tuple(sorted(profile.content_hash for profile in candidates)),
        minimum_size_met=len(members) >= request.min_members,
    )


def build_cohort(
    request: CohortRequest,
    profiles: tuple[CapabilityProfile, ...],
) -> CohortPlan:
    derived = _derive_cohort(request, profiles)
    return CohortPlan.build(
        cohort_plan_id=f"{request.request_id}:plan",
        request_id=request.request_id,
        request_content_hash=request.content_hash,
        request_snapshot=request,
        task_id=request.task_id,
        resolved_candidate_profiles=derived.resolved_candidate_profiles,
        members=derived.members,
        excluded_actor_ids=derived.excluded_actor_ids,
        coverage=derived.coverage,
        unresolved_requirement_ids=derived.unresolved_requirement_ids,
        unresolved_candidate_actor_ids=derived.unresolved_candidate_actor_ids,
        ranked_candidates=derived.ranked_candidates,
        tie_sets=derived.tie_sets,
        tie_group_ranks=derived.tie_group_ranks,
        evidence_snapshot_hashes=derived.evidence_snapshot_hashes,
        profile_content_hashes=derived.profile_content_hashes,
        minimum_size_met=derived.minimum_size_met,
        governing_policy_hash=request.governing_policy_hash,
    )


__all__ = ["assess_capability", "build_cohort"]
