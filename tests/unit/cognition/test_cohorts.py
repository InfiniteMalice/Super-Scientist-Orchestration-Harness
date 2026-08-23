from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.cognition import (
    CapabilityAssertion,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityRequirement,
    CohortPlan,
    CohortRequest,
    DiversityFingerprint,
    build_cohort,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex

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
    candidates: tuple[str, ...] = ("peer-a", "peer-b", "peer-c", "peer-d"),
    prohibited: tuple[tuple[str, str], ...] = (),
    required: tuple[CapabilityRequirement, ...] | None = None,
) -> CohortRequest:
    return CohortRequest.build(
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


def test_cohort_plan_parser_rejects_reversed_ranked_tie_sets() -> None:
    profiles = (
        _profile("peer-d", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-b"),
        _profile("peer-c", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-a"),
    )
    payload = build_cohort(_request(max_members=2), profiles).model_dump(mode="python")
    payload["tie_sets"] = tuple(reversed(payload["tie_sets"]))
    payload["tie_group_ranks"] = tuple(reversed(payload["tie_group_ranks"]))

    with pytest.raises(ValidationError, match="rank order"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


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


def test_cohort_request_requires_a_nonempty_fixed_candidate_roster() -> None:
    with pytest.raises(ValidationError, match="candidate_actor_ids"):
        _request(candidates=())


def test_supplied_profiles_cannot_expand_the_fixed_candidate_roster() -> None:
    request = _request(
        min_members=1,
        max_members=2,
        candidates=("peer-a", "peer-c"),
    )

    plan = build_cohort(
        request,
        (_profile("peer-a"), _profile("peer-b")),
    )

    assert tuple(member.actor_id for member in plan.members) == ("peer-a",)
    assert plan.unresolved_candidate_actor_ids == ("peer-c",)
    assert "peer-b" not in plan.excluded_actor_ids


def test_candidate_roster_change_changes_request_and_plan_hashes() -> None:
    left_request = _request(candidates=("peer-a",))
    right_request = _request(candidates=("peer-a", "peer-b"))
    profiles = (_profile("peer-a"), _profile("peer-b"))

    left_plan = build_cohort(left_request, profiles)
    right_plan = build_cohort(right_request, profiles)

    assert left_request.content_hash != right_request.content_hash
    assert left_plan.request_content_hash != right_plan.request_content_hash
    assert left_plan.content_hash != right_plan.content_hash


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


def test_cohort_request_hash_changes_with_material_constraints() -> None:
    left = _request(max_members=1)
    right = _request(max_members=2)

    assert hasattr(left, "content_hash")
    assert left.content_hash != right.content_hash


def test_cohort_plan_binds_exact_request_hash() -> None:
    request = _request()
    plan = build_cohort(request, (_profile("peer-a"),))

    assert plan.request_content_hash == request.content_hash


def test_cohort_request_parser_rejects_constraints_changed_under_same_hash() -> None:
    request = _request(max_members=1)
    assert hasattr(request, "content_hash")
    payload = request.model_dump(mode="python") | {"max_members": 2}

    with pytest.raises(ValidationError, match="content_hash"):
        CohortRequest.model_validate(payload)


def _two_member_plan() -> CohortPlan:
    request = _request(
        min_members=2,
        max_members=2,
        required=(_requirement("requirement-a"), _requirement("requirement-b")),
    )
    return build_cohort(request, (_profile("peer-b"), _profile("peer-a")))


def _rehash_plan_payload(payload: dict[str, object]) -> dict[str, object]:
    unhashed = {key: value for key, value in payload.items() if key != "content_hash"}
    payload["content_hash"] = sha256_hex(canonical_json_bytes(unhashed))
    return payload


@pytest.mark.parametrize("mutation", ("reverse", "duplicate"))
def test_cohort_plan_parser_rejects_noncanonical_or_duplicate_members(mutation: str) -> None:
    plan = _two_member_plan()
    payload = plan.model_dump(mode="python")
    members = list(payload["members"])
    payload["members"] = (
        tuple(reversed(members)) if mutation == "reverse" else (members[0], members[0])
    )

    with pytest.raises(ValidationError, match="members"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


@pytest.mark.parametrize("mutation", ("reverse", "duplicate"))
def test_cohort_plan_parser_rejects_noncanonical_or_duplicate_coverage(mutation: str) -> None:
    plan = _two_member_plan()
    payload = plan.model_dump(mode="python")
    coverage = list(payload["coverage"])
    payload["coverage"] = (
        tuple(reversed(coverage)) if mutation == "reverse" else (coverage[0], coverage[0])
    )

    with pytest.raises(ValidationError, match="coverage"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_selected_actor_as_excluded() -> None:
    payload = _two_member_plan().model_dump(mode="python")
    payload["excluded_actor_ids"] = ("peer-a",)

    with pytest.raises(ValidationError, match="selected and excluded"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_excluded_actor_as_unresolved() -> None:
    request = _request(
        min_members=2,
        max_members=2,
        candidates=("peer-a", "peer-b", "peer-c", "peer-d"),
    )
    plan = build_cohort(
        request,
        (_profile("peer-c"), _profile("peer-b"), _profile("peer-a")),
    )
    assert plan.excluded_actor_ids == ("peer-c",)
    assert plan.unresolved_candidate_actor_ids == ("peer-d",)
    payload = plan.model_dump(mode="python")
    payload["unresolved_candidate_actor_ids"] = ("peer-c",)

    with pytest.raises(ValidationError, match="excluded and unresolved"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_member_assessment_count_drift() -> None:
    payload = _two_member_plan().model_dump(mode="python")
    members = list(payload["members"])
    assert isinstance(members[0], dict)
    assert isinstance(members[1], dict)
    members[0]["required_satisfied"] = 0
    members[1]["required_satisfied"] = 0
    payload["members"] = tuple(members)

    with pytest.raises(ValidationError, match="assessment counts"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


@pytest.mark.parametrize("location", ("coverage", "tie-set"))
def test_cohort_plan_parser_rejects_outsider_actor_references(location: str) -> None:
    payload = _two_member_plan().model_dump(mode="python")
    if location == "coverage":
        coverage = list(payload["coverage"])
        assert isinstance(coverage[0], dict)
        coverage[0]["satisfying_actor_ids"] = ("peer-z",)
        payload["coverage"] = tuple(coverage)
    else:
        payload["tie_sets"] = (("peer-a", "peer-z"),)

    with pytest.raises(ValidationError, match="cohort actors"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


@pytest.mark.parametrize("mutation", ("profile-hash", "assessment-identity"))
def test_cohort_plan_parser_rejects_member_profile_binding_drift(mutation: str) -> None:
    payload = _two_member_plan().model_dump(mode="python")
    members = list(payload["members"])
    assert isinstance(members[0], dict)
    if mutation == "profile-hash":
        members[0]["profile_content_hash"] = "0" * 64
    else:
        assessments = list(members[0]["assessments"])
        assert isinstance(assessments[0], dict)
        assessments[0]["actor_id"] = "peer-z"
        members[0]["assessments"] = tuple(assessments)
    payload["members"] = tuple(members)

    with pytest.raises(ValidationError, match="member profile"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))
