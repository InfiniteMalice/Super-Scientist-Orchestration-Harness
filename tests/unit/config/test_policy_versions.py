from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from super_scientist.config.loader import load_policy_document, policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicy,
    GovernancePolicyV1,
    GovernancePolicyV2,
    PolicyDocument,
)
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex


def test_governance_policy_is_the_exact_v1_compatibility_alias() -> None:
    assert GovernancePolicy is GovernancePolicyV1


def test_v1_policy_hash_is_unchanged() -> None:
    legacy = load_policy_document(
        {
            "schema_version": 1,
            "required_claim_checks": ["source_exists", "evidence_span_exists"],
        }
    )

    assert isinstance(legacy.policy, GovernancePolicyV1)
    assert legacy.policy_hash == "26269abd13de9d63206eb6fe0465deb5b5ef5f99602a9d4ad89ea710cff3e7d9"


def test_v2_policy_hash_uses_its_exact_payload() -> None:
    policy = _v2_policy()
    expected = sha256_hex(canonical_json_bytes(policy.model_dump(mode="json")))

    assert policy_hash(policy) == expected
    assert policy_hash(policy) != "26269abd13de9d63206eb6fe0465deb5b5ef5f99602a9d4ad89ea710cff3e7d9"


def test_v2_policy_canonical_payload_sorts_set_fields() -> None:
    payload = _v2_policy().model_dump(mode="json")

    assert payload["human_approval_for"] == ["adapter_promotion", "governance_change"]
    assert payload["adaptation_requirements"][0]["permitted_grounding"] == [
        "HUMAN_JUDGMENT",
        "PRIMARY_SOURCE",
    ]


def test_policy_document_decodes_versions_without_upcasting_v1() -> None:
    adapter = TypeAdapter(PolicyDocument)

    v1 = adapter.validate_python(
        {
            "schema_version": 1,
            "required_claim_checks": ["source_exists"],
        }
    )
    v2 = adapter.validate_json(canonical_json_bytes(_v2_policy().model_dump(mode="json")))

    assert type(v1) is GovernancePolicyV1
    assert type(v2) is GovernancePolicyV2


def test_policy_document_routes_legacy_omitted_schema_version_to_v1() -> None:
    policy = TypeAdapter(PolicyDocument).validate_python(
        {"required_claim_checks": ["source_exists"]}
    )

    assert type(policy) is GovernancePolicyV1


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 3, "required_claim_checks": ["source_exists"]},
        {"schema_version": "1", "required_claim_checks": ["source_exists"]},
        {"schema_version": "2", "required_claim_checks": ["source_exists"]},
        {"schema_version": 2, "required_claim_checks": ["source_exists"]},
        {
            "required_claim_checks": ["source_exists"],
            "human_approval_for": [],
            "adaptation_requirements": [],
        },
        {
            "schema_version": 2,
            "required_claim_checks": ["source_exists"],
            "human_approval_for": [],
            "adaptation_requirements": [],
        },
        {
            "schema_version": 2,
            "required_claim_checks": ["source_exists"],
            "human_approval_for": [],
            "adaptation_requirements": [
                {
                    "change_target": ChangeTarget.GOVERNANCE_POLICY,
                    "persistence": PersistenceScope.GOVERNANCE_POLICY,
                    "minimum_verification": VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                    "permitted_grounding": [ExternalGrounding.HUMAN_JUDGMENT],
                    "required_approver_kind": ActorKind.HUMAN,
                    "protected_evaluation_required": True,
                    "rollback_required": True,
                }
            ],
            "unexpected_field": True,
        },
    ],
    ids=[
        "unknown-version",
        "string-v1-version",
        "string-v2-version",
        "missing-v2-fields",
        "omitted-v2-version",
        "empty-requirements",
        "unknown-field",
    ],
)
def test_policy_document_fails_closed_on_unknown_or_invalid_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(PolicyDocument).validate_python(payload)


def test_v2_adaptation_requirement_is_strict_and_uses_typed_classifications() -> None:
    payload = _v2_requirement_payload()
    payload["protected_evaluation_required"] = 1

    with pytest.raises(ValidationError):
        AdaptationRequirement.model_validate(payload)


def _v2_policy() -> GovernancePolicyV2:
    return GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset({"adapter_promotion", "governance_change"}),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.GOVERNANCE_POLICY,
                persistence=PersistenceScope.GOVERNANCE_POLICY,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset(
                    {ExternalGrounding.HUMAN_JUDGMENT, ExternalGrounding.PRIMARY_SOURCE}
                ),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
        ),
    )


def _v2_requirement_payload() -> dict[str, object]:
    return {
        "change_target": ChangeTarget.GOVERNANCE_POLICY,
        "persistence": PersistenceScope.GOVERNANCE_POLICY,
        "minimum_verification": VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        "permitted_grounding": [ExternalGrounding.HUMAN_JUDGMENT],
        "required_approver_kind": ActorKind.HUMAN,
        "protected_evaluation_required": True,
        "rollback_required": True,
    }
