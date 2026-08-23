from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.cognition import (
    CapabilityAssertion,
    CapabilityDisposition,
    CapabilityEvidenceStatus,
    CapabilityProfile,
    CapabilityRequirement,
    DiversityFingerprint,
    assess_capability,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
SNAPSHOT = "a" * 64
POLICY = "f" * 64


def _actor(actor_id: str = "peer-a") -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL,
        created_at=NOW,
        provider_id="provider-a",
        model_id="model-a",
        adapter_id="adapter-a",
        configuration_hash="b" * 64,
    )


def _fingerprint(actor_id: str = "peer-a") -> DiversityFingerprint:
    return DiversityFingerprint(
        fingerprint_id=f"fingerprint-{actor_id}",
        model_family="family-a",
        model_version="version-a",
        scale_class="large",
        provider="provider-a",
        adapter_hash="c" * 64,
        configuration_hash="b" * 64,
        prompt_strategy="critique-first",
        methodological_prior="falsification",
        tools=("search",),
        evidence_partitions=("public",),
        modalities=("text",),
        previous_error_clusters=("overgeneralization",),
        prior_task_specializations=("causal-analysis",),
        assigned_role="reviewer",
        procedure_family="deductive",
    )


def _assertion(
    status: CapabilityEvidenceStatus,
    *,
    snapshot: str = SNAPSHOT,
) -> CapabilityAssertion:
    verified = status is CapabilityEvidenceStatus.VERIFIED
    return CapabilityAssertion(
        assertion_id=f"assertion-{status.value.lower()}",
        capability_id="causal-analysis",
        task_family_id="scientific-review",
        status=status,
        evidence_ids=(("evidence-1",) if status is not CapabilityEvidenceStatus.UNKNOWN else ()),
        validator_id=("validator-a" if verified else None),
        validator_version=("v1" if verified else None),
        evidence_snapshot_hash=snapshot,
    )


def _profile(assertion: CapabilityAssertion) -> CapabilityProfile:
    return CapabilityProfile.build(
        profile_id="profile-a",
        actor=_actor(),
        diversity_fingerprint=_fingerprint(),
        allowed_tools=("search",),
        modalities=("text",),
        supported_schemas=("claim-v1",),
        execution_constraints=("no-network",),
        known_failure_categories=("hallucination",),
        assertions=(assertion,),
        governing_policy_hash=POLICY,
    )


def _requirement(**updates: object) -> CapabilityRequirement:
    values: dict[str, object] = {
        "requirement_id": "requirement-causal",
        "capability_id": "causal-analysis",
        "task_family_id": "scientific-review",
        "evidence_snapshot_hash": SNAPSHOT,
        "required_tools": ("search",),
        "required_modalities": ("text",),
        "required_schema_ids": ("claim-v1",),
        "required_execution_constraints": ("no-network",),
        "disqualifying_failure_categories": (),
    }
    values.update(updates)
    return CapabilityRequirement(**values)


def test_verified_current_capability_satisfies_every_declared_dimension() -> None:
    assessment = assess_capability(
        _profile(_assertion(CapabilityEvidenceStatus.VERIFIED)),
        _requirement(),
    )

    assert assessment.disposition is CapabilityDisposition.SATISFIED
    assert assessment.evidence_status is CapabilityEvidenceStatus.VERIFIED
    assert assessment.matched_assertion_ids == ("assertion-verified",)
    assert assessment.missing_dimensions == ()
    assert assessment.failed_dimensions == ()


def test_self_reported_capability_is_not_satisfied() -> None:
    assessment = assess_capability(
        _profile(_assertion(CapabilityEvidenceStatus.SELF_REPORTED)),
        _requirement(),
    )

    assert assessment.disposition is CapabilityDisposition.UNKNOWN
    assert assessment.evidence_status is CapabilityEvidenceStatus.SELF_REPORTED
    assert assessment.missing_dimensions == ("verified_evidence",)


def test_absent_capability_remains_unknown_without_synthesized_evidence() -> None:
    profile = CapabilityProfile.build(
        profile_id="profile-a",
        actor=_actor(),
        diversity_fingerprint=_fingerprint(),
        allowed_tools=("search",),
        modalities=("text",),
        supported_schemas=("claim-v1",),
        execution_constraints=("no-network",),
        known_failure_categories=(),
        assertions=(),
        governing_policy_hash=POLICY,
    )

    assessment = assess_capability(profile, _requirement())

    assert assessment.disposition is CapabilityDisposition.UNKNOWN
    assert assessment.evidence_status is CapabilityEvidenceStatus.UNKNOWN
    assert assessment.matched_assertion_ids == ()
    assert assessment.missing_dimensions == ("capability_evidence",)


def test_explicitly_unsupported_capability_is_unsatisfied() -> None:
    assessment = assess_capability(
        _profile(_assertion(CapabilityEvidenceStatus.UNSUPPORTED)),
        _requirement(),
    )

    assert assessment.disposition is CapabilityDisposition.UNSATISFIED
    assert assessment.evidence_status is CapabilityEvidenceStatus.UNSUPPORTED
    assert assessment.failed_dimensions == ("capability_support",)


def test_stale_verified_evidence_is_not_current_verified_evidence() -> None:
    assessment = assess_capability(
        _profile(_assertion(CapabilityEvidenceStatus.VERIFIED, snapshot="d" * 64)),
        _requirement(),
    )

    assert assessment.disposition is CapabilityDisposition.UNKNOWN
    assert assessment.evidence_status is CapabilityEvidenceStatus.VERIFIED
    assert assessment.failed_dimensions == ("evidence_snapshot_hash",)


def test_missing_profile_metadata_keeps_verified_assertion_unknown() -> None:
    assessment = assess_capability(
        _profile(_assertion(CapabilityEvidenceStatus.VERIFIED)),
        _requirement(required_tools=("calculator", "search")),
    )

    assert assessment.disposition is CapabilityDisposition.UNKNOWN
    assert assessment.missing_dimensions == ("required_tools",)


def test_known_failure_category_explicitly_fails_requirement() -> None:
    assessment = assess_capability(
        _profile(_assertion(CapabilityEvidenceStatus.VERIFIED)),
        _requirement(disqualifying_failure_categories=("hallucination",)),
    )

    assert assessment.disposition is CapabilityDisposition.UNSATISFIED
    assert assessment.failed_dimensions == ("known_failure_categories",)


def test_verified_assertion_requires_validator_identity_version_and_evidence() -> None:
    with pytest.raises(ValidationError, match="verified assertions require"):
        CapabilityAssertion(
            assertion_id="invalid",
            capability_id="causal-analysis",
            task_family_id="scientific-review",
            status=CapabilityEvidenceStatus.VERIFIED,
            evidence_ids=(),
            validator_id=None,
            validator_version=None,
            evidence_snapshot_hash=SNAPSHOT,
        )


def test_profile_rejects_unsorted_assertions_and_tampered_content_hash() -> None:
    later = _assertion(CapabilityEvidenceStatus.UNKNOWN).model_copy(
        update={"assertion_id": "z-assertion"}
    )
    earlier = later.model_copy(update={"assertion_id": "a-assertion"})
    with pytest.raises(ValidationError, match="assertions must be sorted"):
        CapabilityProfile.build(
            profile_id="profile-a",
            actor=_actor(),
            diversity_fingerprint=_fingerprint(),
            assertions=(later, earlier),
            governing_policy_hash=POLICY,
        )

    valid = _profile(_assertion(CapabilityEvidenceStatus.VERIFIED))
    with pytest.raises(ValidationError, match="content_hash"):
        CapabilityProfile.model_validate(
            valid.model_dump(mode="python") | {"content_hash": "0" * 64}
        )
