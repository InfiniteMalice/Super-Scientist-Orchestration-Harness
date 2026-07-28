from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from super_scientist.domain.primitives import canonical_json_bytes


def _proposal_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "proposal_id": "quality-policy-0.2.0-wheel-install",
        "proposer": {
            "actor_id": "task-18-proposer",
            "kind": "service",
            "created_at": "2026-07-27T20:00:00Z",
            "provider_id": None,
            "model_id": None,
            "adapter_id": None,
            "configuration_hash": None,
        },
        "prior_registry_hash": "1" * 64,
        "proposed_registry_hash": "2" * 64,
        "source_diff_hash": "3" * 64,
        "firewall_policy_sha256": "5" * 64,
        "allowed_attribution_paths": ("docs/a.md", "docs/b.md"),
        "governing_policy_hash": "4" * 64,
        "quality_policy_hash": "36db4f9b9c290029f4d628c1934bbabefab731ac7729a5a9446998df396a132b",
        "measurement_id": "old-eight-check-gate-attempts-1-6",
        "evaluator_audit_id": "task-18-independent-evaluator-audit",
        "rationale": "Add one fixed built-wheel installation and CLI smoke check.",
        "regression_tests": (
            "tests/adversarial/test_imported_pattern_tampering.py",
            "tests/unit/quality/test_imported_pattern_firewall.py",
            "tests/unit/quality/test_runner.py",
            "tests/unit/quality/test_wheel_smoke.py",
        ),
        "rollback_commit": "29342b17de5f9169921cba425ba8765de5828478",
    }


def test_quality_policy_hash_binds_registry_firewall_digest_and_exact_allowlist() -> None:
    from super_scientist.quality.imported_pattern_firewall import quality_policy_hash

    baseline = quality_policy_hash(
        registry_hash="1" * 64,
        firewall_policy_sha256="2" * 64,
        allowed_attribution_paths=("docs/a.md", "docs/b.md"),
    )

    assert baseline == "276d8a110e145840700fd5d900e455acb190b8a870c19a9d988098112193f745"
    assert baseline != quality_policy_hash(
        registry_hash="1" * 64,
        firewall_policy_sha256="3" * 64,
        allowed_attribution_paths=("docs/a.md", "docs/b.md"),
    )
    assert baseline != quality_policy_hash(
        registry_hash="1" * 64,
        firewall_policy_sha256="2" * 64,
        allowed_attribution_paths=("docs/a.md", "docs/c.md"),
    )


def test_registry_hash_is_ordered_and_normalizes_only_the_bound_python() -> None:
    from super_scientist.quality.imported_pattern_firewall import quality_registry_hash

    checks = (
        ("format", ("C:/python/python.exe", "-m", "ruff", "format", "--check", ".")),
        ("lint", ("C:/python/python.exe", "-m", "ruff", "check", ".")),
    )

    digest = quality_registry_hash(checks, python_executable="C:/python/python.exe")

    assert digest == "48c4c817f3a8d0005503f5287a8abb899d7b19a8ae55833ea3866a0ea514c93f"
    assert digest != quality_registry_hash(
        tuple(reversed(checks)),
        python_executable="C:/python/python.exe",
    )


def test_registry_hash_rejects_empty_duplicate_or_unbound_checks() -> None:
    from super_scientist.quality.imported_pattern_firewall import quality_registry_hash

    invalid = (
        (),
        (
            ("format", ("C:/python/python.exe", "-m", "ruff")),
            ("format", ("C:/python/python.exe", "-m", "ruff")),
        ),
        (("format", ("other-python", "-m", "ruff")),),
    )
    for checks in invalid:
        with pytest.raises(ValueError):
            quality_registry_hash(
                checks,
                python_executable="C:/python/python.exe",
            )


def test_workspace_integrity_uses_the_same_versioned_quality_policy_hash() -> None:
    from super_scientist.application.transactions.coordinator import (
        TransactionCoordinator as _TransactionCoordinator,
    )
    from super_scientist.application.workspace_integrity import (
        workspace_quality_policy_hash,
    )
    from super_scientist.quality.imported_pattern_firewall import quality_policy_hash

    assert _TransactionCoordinator is not None
    inputs = {
        "registry_hash": "1" * 64,
        "firewall_policy_sha256": "2" * 64,
        "allowed_attribution_paths": ("docs/a.md", "docs/b.md"),
    }

    assert workspace_quality_policy_hash(**inputs) == quality_policy_hash(**inputs)


@pytest.mark.parametrize(
    "allowed_paths",
    [
        ("docs/b.md", "docs/a.md"),
        ("docs/a.md", "docs/a.md"),
        ("docs",),
        ("../docs/a.md",),
        ("C:/docs/a.md",),
    ],
)
def test_quality_policy_hash_rejects_nonexact_or_noncanonical_allowlists(
    allowed_paths: tuple[str, ...],
) -> None:
    from super_scientist.quality.imported_pattern_firewall import quality_policy_hash

    with pytest.raises((TypeError, ValueError, ValidationError)):
        quality_policy_hash(
            registry_hash="1" * 64,
            firewall_policy_sha256="2" * 64,
            allowed_attribution_paths=allowed_paths,
        )


def test_quality_policy_hash_rejects_malformed_digest_inputs() -> None:
    from super_scientist.quality.imported_pattern_firewall import quality_policy_hash

    for registry_hash, firewall_digest in (
        ("1" * 63, "2" * 64),
        ("1" * 64, "not-a-digest"),
    ):
        with pytest.raises(ValidationError):
            quality_policy_hash(
                registry_hash=registry_hash,
                firewall_policy_sha256=firewall_digest,
                allowed_attribution_paths=("docs/a.md",),
            )


def test_quality_policy_proposal_is_strict_pending_and_content_addressed() -> None:
    from super_scientist.quality.imported_pattern_firewall import (
        QualityPolicyProposal,
        quality_policy_proposal_record,
    )

    proposal = QualityPolicyProposal.model_validate(_proposal_payload())
    record = quality_policy_proposal_record(proposal)

    assert record == canonical_json_bytes(_proposal_payload())
    decoded = json.loads(record)
    assert decoded["regression_tests"] == list(_proposal_payload()["regression_tests"])
    assert "independent_human_approval" not in decoded

    unknown = {**_proposal_payload(), "source_edit": "forbidden"}
    with pytest.raises(ValidationError):
        QualityPolicyProposal.model_validate(unknown)


def test_quality_policy_proposal_rejects_ambiguous_or_ungoverned_changes() -> None:
    from super_scientist.quality.imported_pattern_firewall import QualityPolicyProposal

    for patch in (
        {"prior_registry_hash": "2" * 64},
        {"quality_policy_hash": "4" * 64},
        {"firewall_policy_sha256": "6" * 64},
        {"allowed_attribution_paths": ("docs/a.md", "docs/c.md")},
        {"regression_tests": ()},
        {
            "regression_tests": (
                "tests/unit/quality/test_runner.py",
                "tests/unit/quality/test_runner.py",
            )
        },
        {"regression_tests": ("tests",)},
        {"rollback_commit": "not-a-commit"},
    ):
        with pytest.raises(ValidationError):
            QualityPolicyProposal.model_validate({**_proposal_payload(), **patch})
