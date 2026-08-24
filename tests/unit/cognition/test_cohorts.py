from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from super_scientist.domain.cognition import (
    CapabilityAssertion,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityRequirement,
    CohortPlan,
    CohortRequest,
    DiversityFingerprint,
    ErrorCorrelationRecord,
    ErrorCorrelationStatus,
    assess_capability,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
SNAPSHOT = "a" * 64
POLICY = "f" * 64
PLAN_LIMIT_BYTES = 8 * 1024 * 1024
PRIVATE_MARKER = "PRIVATE-MARKER-7f4d"


class _UnserializablePrivateMarker:
    def __repr__(self) -> str:
        return PRIVATE_MARKER


def _forge_schema_version(
    model: BaseModel,
    construction: str,
    value: object = PRIVATE_MARKER,
) -> BaseModel:
    if construction == "model-copy":
        return model.model_copy(update={"schema_version": value})
    values = model.model_dump(mode="python", warnings=False)
    values["schema_version"] = value
    return type(model).model_construct(**values)


def _assert_sanitized_derivation_failure(
    action: Callable[[], object],
    expected_message: str,
) -> None:
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(ValueError) as caught:
            action()

    error = caught.value
    assert caught_warnings == []
    assert type(error) is ValueError
    assert str(error) == expected_message
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "errors")


def _requirement(requirement_id: str = "requirement-a") -> CapabilityRequirement:
    return CapabilityRequirement(
        requirement_id=requirement_id,
        capability_id="analysis",
        task_family_id="research",
        evidence_snapshot_hash=SNAPSHOT,
    )


def _assessment_hash(
    profile: CapabilityProfile,
    requirement: CapabilityRequirement,
) -> str:
    assessment = assess_capability(profile, requirement)
    return sha256_hex(canonical_json_bytes(assessment.model_dump(mode="json")))


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
            (f"evidence-{actor_id}",) if status is not CapabilityEvidenceStatus.UNKNOWN else ()
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
    preferred: tuple[CapabilityRequirement, ...] = (),
) -> CohortRequest:
    return CohortRequest.build(
        request_id="cohort-request-a",
        task_id="task-a",
        required_capabilities=(_requirement(),) if required is None else required,
        preferred_capabilities=preferred,
        min_members=min_members,
        max_members=max_members,
        candidate_actor_ids=candidates,
        prohibited_combinations=prohibited,
        governing_policy_hash=POLICY,
    )


@pytest.mark.parametrize("construction", ("model-copy", "model-construct"))
@pytest.mark.parametrize(
    "boundary",
    (
        "assess-requirement",
        "assess-profile",
        "build-request",
        "build-profile",
        "diversity-cohort",
        "diversity-profile",
        "diversity-correlation",
    ),
)
def test_public_cognition_derivations_detach_forged_input_details(
    boundary: str,
    construction: str,
) -> None:
    requirement = _requirement()
    profile = _profile("peer-a")
    request = _request(candidates=("peer-a",), required=(requirement,))

    if boundary == "assess-requirement":
        forged = _forge_schema_version(requirement, construction)
        _assert_sanitized_derivation_failure(
            lambda: assess_capability(profile, forged),
            "capability requirement is invalid",
        )
        return
    if boundary == "assess-profile":
        forged = _forge_schema_version(profile, construction)
        _assert_sanitized_derivation_failure(
            lambda: assess_capability(forged, requirement),
            "capability profile is invalid",
        )
        return
    if boundary == "build-request":
        forged = _forge_schema_version(request, construction)
        _assert_sanitized_derivation_failure(
            lambda: build_cohort(forged, (profile,)),
            "cohort request is invalid",
        )
        return
    if boundary == "build-profile":
        forged = _forge_schema_version(profile, construction)
        _assert_sanitized_derivation_failure(
            lambda: build_cohort(request, (forged,)),
            "capability profile is invalid",
        )
        return

    right = _profile("peer-b")
    diversity_request = _request(
        min_members=2,
        max_members=2,
        candidates=("peer-a", "peer-b"),
        required=(requirement,),
    )
    cohort = build_cohort(diversity_request, (profile, right))
    if boundary == "diversity-cohort":
        forged = _forge_schema_version(cohort, construction)
        _assert_sanitized_derivation_failure(
            lambda: assess_diversity(forged, (profile, right), ()),
            "cohort plan is invalid",
        )
        return
    if boundary == "diversity-profile":
        forged = _forge_schema_version(profile, construction)
        _assert_sanitized_derivation_failure(
            lambda: assess_diversity(cohort, (forged, right), ()),
            "capability profile is invalid",
        )
        return
    correlation = ErrorCorrelationRecord(
        correlation_id="correlation-a",
        left_actor_id="peer-a",
        right_actor_id="peer-b",
        evaluation_set_id="evaluation-a",
        sample_count=1,
        method="pearson",
        status=ErrorCorrelationStatus.INSUFFICIENT_DATA,
        value=None,
        governing_policy_hash=POLICY,
    )
    forged = _forge_schema_version(correlation, construction)
    _assert_sanitized_derivation_failure(
        lambda: assess_diversity(cohort, (profile, right), (forged,)),
        "error correlation is invalid",
    )


@pytest.mark.parametrize("construction", ("model-copy", "model-construct"))
@pytest.mark.parametrize("input_kind", ("request", "profile"))
def test_build_cohort_detaches_supplied_input_serialization_failures(
    construction: str,
    input_kind: str,
) -> None:
    request = _request(candidates=("peer-a",))
    profile = _profile("peer-a")
    marker = _UnserializablePrivateMarker()
    if input_kind == "request":
        request = _forge_schema_version(request, construction, marker)
    else:
        profile = _forge_schema_version(profile, construction, marker)

    _assert_sanitized_derivation_failure(
        lambda: build_cohort(request, (profile,)),
        "cohort supplied profile inputs are invalid",
    )


def test_cohort_plan_byte_gate_detaches_raw_serialization_failure() -> None:
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        with pytest.raises(ValidationError) as caught:
            CohortPlan.model_validate({"schema_version": _UnserializablePrivateMarker()})

    error = caught.value
    assert caught_warnings == []
    assert PRIVATE_MARKER not in str(error)
    assert PRIVATE_MARKER not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.errors(include_url=False, include_context=False) == [
        {
            "type": "value_error",
            "loc": (),
            "msg": "Value error, cohort plan canonical serialization failed",
            "input": "[REDACTED]",
        }
    ]


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


def test_cohort_plan_parser_requires_complete_tie_evidence_for_excluded_candidates() -> None:
    profiles = (
        _profile("peer-d", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-b"),
        _profile("peer-c", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-a"),
    )
    payload = build_cohort(_request(max_members=2), profiles).model_dump(mode="python")
    payload["tie_sets"] = ()
    payload["tie_group_ranks"] = ()

    with pytest.raises(ValidationError, match="grounded ranking evidence"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_fabricated_excluded_tie_rank() -> None:
    profiles = (
        _profile("peer-d", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-b"),
        _profile("peer-c", status=CapabilityEvidenceStatus.UNKNOWN),
        _profile("peer-a"),
    )
    payload = build_cohort(_request(max_members=2), profiles).model_dump(mode="python")
    ranks = list(payload["tie_group_ranks"])
    ranks[1] = {**ranks[1], "preferred_satisfied": 1}
    payload["tie_group_ranks"] = tuple(ranks)

    with pytest.raises(ValidationError, match="grounded ranking evidence"):
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


def test_build_cohort_revalidates_surplus_profiles_before_roster_filtering() -> None:
    request = _request(candidates=("peer-a",))
    malformed_surplus = _profile("peer-z").model_copy(update={"governing_policy_hash": "e" * 64})

    with pytest.raises((ValidationError, ValueError), match=r"capability profile|content_hash"):
        build_cohort(request, (_profile("peer-a"), malformed_surplus))


def test_build_cohort_rejects_duplicate_surplus_actor_profiles() -> None:
    request = _request(candidates=("peer-a",))

    with pytest.raises(ValueError, match="actor IDs must be unique"):
        build_cohort(
            request,
            (_profile("peer-a"), _profile("peer-z"), _profile("peer-z")),
        )


def test_build_cohort_requires_an_exact_profiles_tuple() -> None:
    with pytest.raises(TypeError, match="exact tuple"):
        build_cohort(  # type: ignore[arg-type]
            _request(candidates=("peer-a",)),
            [_profile("peer-a")],
        )


def test_build_cohort_rejects_supplied_profile_count_before_filtering() -> None:
    repeated_surplus = (_profile("peer-z"),) * 10_001

    with pytest.raises(ValueError, match="at most 64 supplied profiles"):
        build_cohort(_request(candidates=("peer-a",)), repeated_surplus)


def test_build_cohort_rejects_supplied_profile_bytes_before_filtering() -> None:
    constraints = tuple(f"{index:02d}-" + "x" * 1997 for index in range(64))
    profiles = []
    for index in range(33):
        actor_id = f"peer-{index:02d}"
        values = _profile(actor_id).model_dump(mode="python", exclude={"content_hash"})
        values["execution_constraints"] = constraints
        profiles.append(CapabilityProfile.build(**values))

    with pytest.raises(ValueError, match="supplied profile inputs exceed"):
        build_cohort(
            _request(candidates=("peer-00",)),
            tuple(profiles),
        )


def test_build_cohort_revalidates_preconstructed_request_hash() -> None:
    request = _request(candidates=("peer-a",))
    stale = request.model_copy(update={"task_id": "task-forged"})

    with pytest.raises((ValidationError, ValueError), match=r"cohort request|content_hash"):
        build_cohort(stale, (_profile("peer-a"),))


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


def test_cohort_plan_retains_exact_grounding_inputs_in_canonical_actor_order() -> None:
    request = _request(candidates=("peer-a", "peer-b", "peer-c"))
    peer_a = _profile("peer-a")
    peer_b = _profile("peer-b")

    plan = build_cohort(request, (peer_b, peer_a))

    assert plan.request_snapshot == request
    assert plan.resolved_candidate_profiles == (peer_a, peer_b)


def test_cohort_plan_parser_recomputes_excluded_unknown_assessment_from_profile() -> None:
    request = _request(
        candidates=("peer-a", "peer-b"),
        required=(_requirement("requirement-a"), _requirement("requirement-b")),
    )
    plan = build_cohort(
        request,
        (
            _profile("peer-a"),
            _profile("peer-b", status=CapabilityEvidenceStatus.UNKNOWN),
        ),
    )
    payload = plan.model_dump(mode="python")
    ranked = list(payload["ranked_candidates"])
    excluded = ranked[1]
    assert isinstance(excluded, dict)
    assessment_hashes = list(excluded["assessment_hashes"])
    assessment_hashes[0] = "0" * 64
    excluded["assessment_hashes"] = tuple(assessment_hashes)
    excluded["required_satisfied"] = 1
    payload["ranked_candidates"] = tuple(ranked)

    with pytest.raises(ValidationError, match="recomputed grounded candidate evidence"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_preferred_requirement_snapshot_zeroing() -> None:
    request = _request(
        candidates=("peer-a",),
        preferred=(_requirement("requirement-preferred"),),
    )
    payload = build_cohort(request, (_profile("peer-a"),)).model_dump(mode="python")
    ranked = list(payload["ranked_candidates"])
    candidate = ranked[0]
    assert isinstance(candidate, dict)
    assessment_hashes = list(candidate["assessment_hashes"])
    zeroed_requirement = request.preferred_capabilities[0].model_copy(
        update={"evidence_snapshot_hash": "0" * 64}
    )
    assessment_hashes[1] = _assessment_hash(_profile("peer-a"), zeroed_requirement)
    candidate["assessment_hashes"] = tuple(assessment_hashes)
    payload["ranked_candidates"] = tuple(ranked)

    with pytest.raises(ValidationError, match="recomputed grounded candidate evidence"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_profile_hash_mutation_mirrored_in_hash_list() -> None:
    plan = build_cohort(
        _request(candidates=("peer-a", "peer-b")),
        (_profile("peer-a"), _profile("peer-b")),
    )
    payload = plan.model_dump(mode="python")
    ranked = list(payload["ranked_candidates"])
    excluded = ranked[1]
    assert isinstance(excluded, dict)
    original_hash = excluded["profile_content_hash"]
    excluded["profile_content_hash"] = "0" * 64
    payload["ranked_candidates"] = tuple(ranked)
    payload["profile_content_hashes"] = tuple(
        sorted(
            "0" * 64 if digest == original_hash else digest
            for digest in payload["profile_content_hashes"]
        )
    )

    with pytest.raises(ValidationError, match="recomputed grounded candidate evidence"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_equal_derived_assessment_and_tie_mutation() -> None:
    plan = build_cohort(
        _request(max_members=1, candidates=("peer-a", "peer-b", "peer-c")),
        (
            _profile("peer-a"),
            _profile("peer-b", status=CapabilityEvidenceStatus.UNKNOWN),
            _profile("peer-c", status=CapabilityEvidenceStatus.UNKNOWN),
        ),
    )
    payload = plan.model_dump(mode="python")
    ranked = list(payload["ranked_candidates"])
    for excluded in ranked[1:]:
        assert isinstance(excluded, dict)
        assessment_hashes = list(excluded["assessment_hashes"])
        assessment_hashes[0] = "0" * 64
        excluded["assessment_hashes"] = tuple(assessment_hashes)
        excluded["required_satisfied"] = 1
    payload["ranked_candidates"] = tuple(ranked)
    payload["tie_sets"] = (("peer-a", "peer-b", "peer-c"),)
    payload["tie_group_ranks"] = (
        {"schema_version": 1, "required_satisfied": 1, "preferred_satisfied": 0},
    )

    with pytest.raises(ValidationError, match="recomputed grounded candidate evidence"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_parser_rejects_resolved_candidate_reclassified_as_unresolved() -> None:
    plan = build_cohort(
        _request(candidates=("peer-a", "peer-b")),
        (_profile("peer-a"), _profile("peer-b")),
    )
    payload = plan.model_dump(mode="python")
    ranked = tuple(payload["ranked_candidates"])
    excluded = ranked[1]
    assert isinstance(excluded, dict)
    excluded_hash = excluded["profile_content_hash"]
    payload["ranked_candidates"] = (ranked[0],)
    payload["excluded_actor_ids"] = ()
    payload["unresolved_candidate_actor_ids"] = ("peer-b",)
    payload["profile_content_hashes"] = tuple(
        digest for digest in payload["profile_content_hashes"] if digest != excluded_hash
    )
    payload["tie_sets"] = ()
    payload["tie_group_ranks"] = ()

    with pytest.raises(ValidationError, match="candidate roster partition"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_rejects_grounding_input_snapshot_above_byte_limit() -> None:
    actor_ids = tuple(f"peer-{index:02d}" for index in range(34))
    constraints = tuple(f"{index:02d}-" + "x" * 1997 for index in range(64))
    profiles = []
    for actor_id in actor_ids:
        values = _profile(actor_id).model_dump(mode="python", exclude={"content_hash"})
        values["execution_constraints"] = constraints
        profiles.append(CapabilityProfile.build(**values))

    with pytest.raises((ValidationError, ValueError), match="profile inputs exceed"):
        build_cohort(
            _request(max_members=1, candidates=actor_ids),
            tuple(profiles),
        )


def test_near_limit_grounding_source_always_builds_within_plan_byte_limit() -> None:
    actor_ids = tuple(f"peer-{index:02d}" for index in range(31))
    constraints = tuple(f"{index:02d}-" + "x" * 1997 for index in range(64))
    profiles = []
    for actor_id in actor_ids:
        values = _profile(actor_id).model_dump(mode="python", exclude={"content_hash"})
        values["execution_constraints"] = constraints
        profiles.append(CapabilityProfile.build(**values))
    request = _request(max_members=1, candidates=actor_ids)
    source_bytes = len(
        canonical_json_bytes(
            {
                "request_snapshot": request.model_dump(mode="json"),
                "resolved_candidate_profiles": tuple(
                    profile.model_dump(mode="json") for profile in profiles
                ),
            }
        )
    )

    plan = build_cohort(request, tuple(profiles))

    assert 3_500_000 < source_bytes <= 4 * 1024 * 1024
    assert len(plan.model_dump_json().encode("utf-8")) <= PLAN_LIMIT_BYTES


def test_cohort_plan_compacts_repeated_assessments_without_source_amplification() -> None:
    actor_ids = tuple(f"peer-{index:02d}" for index in range(64))
    constraints = tuple(f"constraint-{index:02d}-" + "x" * 80 for index in range(64))
    requirements = tuple(
        _requirement(f"requirement-{index:02d}").model_copy(
            update={"required_execution_constraints": (constraints[index],)}
        )
        for index in range(64)
    )
    profiles = []
    for actor_id in actor_ids:
        values = _profile(actor_id).model_dump(mode="python", exclude={"content_hash"})
        values["execution_constraints"] = constraints
        profiles.append(CapabilityProfile.build(**values))
    request = _request(
        min_members=64,
        max_members=64,
        candidates=actor_ids,
        required=requirements,
    )
    source_bytes = len(
        canonical_json_bytes(
            {
                "request_snapshot": request.model_dump(mode="json"),
                "resolved_candidate_profiles": tuple(
                    profile.model_dump(mode="json") for profile in profiles
                ),
            }
        )
    )

    plan = build_cohort(request, tuple(profiles))
    plan_bytes = len(plan.model_dump_json().encode("utf-8"))
    payload = plan.model_dump(mode="json")

    assert 400_000 < source_bytes < 700_000
    assert plan_bytes <= source_bytes * 3
    assert plan_bytes < 2 * 1024 * 1024
    assert all("assessments" not in member for member in payload["members"])
    assert all(
        "assessments" not in candidate and "assessment_hashes" in candidate
        for candidate in payload["ranked_candidates"]
    )
    assert all("requirement" not in item for item in payload["coverage"])


def test_cohort_plan_complete_serialized_byte_bound_is_exact_and_early() -> None:
    plan = build_cohort(
        _request(candidates=("peer-a",)),
        (_profile("peer-a"),),
    )
    payload = plan.model_dump(mode="python")
    payload["padding"] = ""
    base_size = len(canonical_json_bytes(to_jsonable_python(payload)))
    payload["padding"] = "x" * (PLAN_LIMIT_BYTES - base_size)
    assert len(canonical_json_bytes(to_jsonable_python(payload))) == PLAN_LIMIT_BYTES

    with pytest.raises(ValidationError) as at_bound:
        CohortPlan.model_validate(payload)
    assert "serialized plan exceeds" not in str(at_bound.value)
    assert "padding" in str(at_bound.value)

    payload["padding"] += "x"
    with pytest.raises(ValidationError, match="serialized plan exceeds"):
        CohortPlan.model_validate(payload)
    with pytest.raises(ValidationError, match="serialized plan exceeds"):
        CohortPlan.model_validate_json(
            json.dumps(to_jsonable_python(payload), separators=(",", ":"))
        )


def test_cohort_request_rejects_more_than_sixty_four_total_requirements() -> None:
    required = tuple(_requirement(f"required-{index:02d}") for index in range(33))
    preferred = tuple(_requirement(f"preferred-{index:02d}") for index in range(32))

    with pytest.raises(ValidationError, match="at most 64 total requirements"):
        _request(required=required, preferred=preferred)


def test_cohort_request_maximum_total_requirement_count_builds() -> None:
    required = tuple(_requirement(f"required-{index:02d}") for index in range(32))
    preferred = tuple(_requirement(f"preferred-{index:02d}") for index in range(32))
    request = _request(
        candidates=("peer-a",),
        required=required,
        preferred=preferred,
    )

    plan = build_cohort(request, (_profile("peer-a"),))

    assert len(plan.ranked_candidates[0].assessment_hashes) == 64


def test_cohort_plan_revalidates_preconstructed_profile_snapshot() -> None:
    plan = build_cohort(
        _request(candidates=("peer-a",)),
        (_profile("peer-a"),),
    )
    payload = plan.model_dump(mode="python")
    invalid_hash = "0" * 64
    payload["resolved_candidate_profiles"] = (
        plan.resolved_candidate_profiles[0].model_copy(update={"content_hash": invalid_hash}),
    )
    ranked = list(payload["ranked_candidates"])
    candidate = ranked[0]
    assert isinstance(candidate, dict)
    candidate["profile_content_hash"] = invalid_hash
    payload["ranked_candidates"] = tuple(ranked)
    members = list(payload["members"])
    member = members[0]
    assert isinstance(member, dict)
    member["profile_content_hash"] = invalid_hash
    payload["members"] = tuple(members)
    payload["profile_content_hashes"] = (invalid_hash,)

    with pytest.raises(ValidationError, match="capability profile"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


def test_cohort_plan_revalidates_preconstructed_request_snapshot() -> None:
    plan = build_cohort(
        _request(candidates=("peer-a",)),
        (_profile("peer-a"),),
    )
    payload = plan.model_dump(mode="python")
    invalid_hash = "0" * 64
    payload["request_snapshot"] = plan.request_snapshot.model_copy(
        update={"content_hash": invalid_hash}
    )
    payload["request_content_hash"] = invalid_hash

    with pytest.raises(ValidationError, match="cohort request"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


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
    payload["content_hash"] = sha256_hex(canonical_json_bytes(to_jsonable_python(unhashed)))
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

    with pytest.raises(ValidationError, match="grounded ranking evidence"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


@pytest.mark.parametrize("location", ("coverage", "tie-set"))
def test_cohort_plan_parser_rejects_outsider_actor_references(location: str) -> None:
    payload = _two_member_plan().model_dump(mode="python")
    if location == "coverage":
        coverage = list(payload["coverage"])
        assert isinstance(coverage[0], dict)
        coverage[0]["satisfying_actor_indexes"] = (3,)
        payload["coverage"] = tuple(coverage)
    else:
        payload["tie_sets"] = (("peer-a", "peer-z"),)

    with pytest.raises(ValidationError, match="cohort actors"):
        CohortPlan.model_validate(_rehash_plan_payload(payload))


@pytest.mark.parametrize("mutation", ("profile-hash", "assessment-hash"))
def test_cohort_plan_parser_rejects_member_profile_binding_drift(mutation: str) -> None:
    payload = _two_member_plan().model_dump(mode="python")
    members = list(payload["members"])
    assert isinstance(members[0], dict)
    if mutation == "profile-hash":
        members[0]["profile_content_hash"] = "0" * 64
        payload["members"] = tuple(members)
    else:
        ranked = list(payload["ranked_candidates"])
        assert isinstance(ranked[0], dict)
        assessment_hashes = list(ranked[0]["assessment_hashes"])
        assessment_hashes[0] = "0" * 64
        ranked[0]["assessment_hashes"] = tuple(assessment_hashes)
        payload["ranked_candidates"] = tuple(ranked)

    with pytest.raises(
        ValidationError,
        match=r"member profile|grounded ranking evidence|grounded candidate evidence",
    ):
        CohortPlan.model_validate(_rehash_plan_payload(payload))
